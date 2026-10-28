"""
SAJIM Strategy Lab — the /lab HTTP routes and the lab_job body (INTERFACES.md sections 8 and 9).

modal_runner.py only wires real objects into this module (ModalStore, ModalSandboxExecutor,
lab_job.spawn, the JOBS Dict, the results volume). Everything here takes those as injected
dependencies, so the routes run under FastAPI's TestClient with a LocalDirStore and a
synchronous fake spawner, without Modal.

  POST /lab/check                       builder, judge     free integrity check (check job + adaptive cuts)
  POST /lab/evaluate                    dev: builder, judge · holdout: judge only
  GET  /lab/job/{job_id}                read, builder, judge (builder: 403 on lab_holdout jobs)
  GET  /lab/{family}                    read, builder, judge (builder: holdout entries redacted)
  GET  /lab/{family}/scorecard/{sha12}  read, builder (dev only, builder_view), judge
  POST /lab/finalize                    judge (synchronous; review + verdict ledger records)

Auth (section 9.1): /lab/* skips the dashboard's X-Sajim-Token gate. The middleware answers
503 when SAJIM_LAB_TOKEN / SAJIM_JUDGE_TOKEN are missing, too short, equal to each other or
equal to SAJIM_API_TOKEN, and 413 when Content-Length is over LAB_MAX_BODY_BYTES; each route
then resolves the caller's scope from X-Judge-Token / X-Lab-Token / X-Sajim-Token (401 when
none is valid) and reads the body with the same bound (413 for chunked bodies).

POST validation order is section 9.2's, and every refusal (400/403/412/413/422) happens before
any budget claim. Defence in depth beyond section 9.2:
  * a lab_id is bound to one family on its first DEV/holdout claim (lab/_bindings/<lab_id>.json,
    first writer wins); a later body naming another family gets 422 family_mismatch;
  * a holdout must match the DEV-time snapshot lab/<family>/<sha12>/manifest.json (id, family,
    symbol, timeframe, max_hold_bars, allow_short, warmup_bars, breadth_symbols) and the DEV
    spec_sha256, else 412 dev_snapshot_mismatch (no touch spent);
  * one claim starts at most one job at a time: each attempt is a first-writer-wins spawn lock in
    the JOBS Dict (JOBS["labspawn-..."], put(skip_if_exists=True)), so a concurrent POST of the same
    request gets 503 and its retry is deduplicated; an attempt that wrote its scorecard is never
    respawned (409 already_evaluated once its lock is gone), so one trial is one look;
  * every attempt of one DEV/holdout request evaluates the manifest its first attempt started with
    (409 request_conflict otherwise), so a respawn can never try a second configuration on one claim;
    after MAX_SPAWN_ATTEMPTS infrastructure failures a request is refused (409 attempts_exhausted);
  * a check never takes over a request id that already names another request's job (400).

No `from __future__ import annotations`: the FastAPI handlers below are closures, and FastAPI
must see real annotation objects for them (see modal_runner.py).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sajim_quant.lab import gates, splits
from sajim_quant.lab import interfaces as I

logger = logging.getLogger("sajim_quant.cloud.lab_api")

LAB_PREFIX = "/lab"
#: Lab tokens shorter than this are treated as "lab not configured" (503).
MIN_TOKEN_CHARS = 16
#: Keys a Submission body may carry; anything else is ignored and recorded (section 9.2 step 5).
SUBMISSION_KEYS = ("lab_id", "family", "stage", "request_id", "strategy_py", "spec_md", "manifest", "client")
MAX_IGNORED_FIELDS = 32
MAX_REASONS = 50
#: Newest ledger records returned by GET /lab/{family} (checks are free, so a ledger can grow).
FAMILY_MAX_RECORDS = 500
BINDINGS_DIR = "lab/_bindings"
#: Manifest keys a holdout submission must share with the DEV-time snapshot.
SNAPSHOT_KEYS = ("id", "family", "code_sha256", "symbol", "timeframe", "max_hold_bars", "allow_short",
                 "warmup_bars", "breadth_symbols")
FINALIZE_SOURCES = ("scorecard", "critic", "budget")
FINALIZE_KEYS = ("lab_id", "family", "code_sha256", "request_id", "verdict", "source", "review")
#: JOBS key holding a lab job's info (a JSON *string*, so GET /jobs, which lists dict values, never shows it).
JOB_INFO_PREFIX = "labjob-"
REQUEST_PREFIX = "req-"                     # the existing _dedup/_remember key scheme
#: Spawn locks: JOBS["labspawn-<kind>-<family>-<sha>-<rid>-<n>"], claimed first-writer-wins before attempt n
#: is spawned, so two concurrent POSTs of one request never start two jobs under one claim.
SPAWN_LOCK_PREFIX = "labspawn-"
#: JOBS["labbind-<lab_id>"] = family: the atomic half of the lab_id -> family binding (the file is the durable half).
BINDING_KEY_PREFIX = "labbind-"
MAX_SPAWN_ATTEMPTS = 8
#: A "pending" lock older than this was abandoned (api() times out at 120 s), so the next attempt may start.
SPAWN_LOCK_STALE_S = 180
SAFE_JOB = re.compile(r"^fc-[A-Za-z0-9]{1,64}$")   # == modal_runner.SAFE_JOB
SHA12_RE = re.compile(r"^[0-9a-f]{12}$")
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
LAB_ID_RE = re.compile(gates.LAB_ID_PATTERN)
RESULTS_PREFIX = "results"                  # store-relative; = modal_runner.RESULTS_DIR under /results
MAX_ERROR_CHARS = 500
RUNNING = ("queued", "running")


# ── Errors (interfaces.LabError subclasses this API adds) ───────────────────
class Unauthorized(I.LabError):
    status, code = I.HTTP_UNAUTHORIZED, "unauthorized"


class NotFound(I.LabError):
    status, code = I.HTTP_NOT_FOUND, "not_found"


class AlreadyFinalized(I.LabError):
    status, code = 409, "already_finalized"


class AlreadyEvaluated(I.LabError):
    status, code = 409, "already_evaluated"


class RequestConflict(I.LabError):
    """A retry of a DEV/holdout request whose earlier attempt started with a different manifest."""
    status, code = 409, "request_conflict"


class AttemptsExhausted(I.LabError):
    status, code = 409, "attempts_exhausted"


class NotConfigured(I.LabError):
    status, code = 503, "lab_not_configured"


class Unavailable(I.LabError):
    status, code = 503, "unavailable"


def _fullmatch(rx: "re.Pattern[str]", value: Any) -> bool:
    return isinstance(value, str) and rx.fullmatch(value) is not None


def _is_lab_id(value: Any) -> bool:
    # fullmatch: gates.LAB_ID_PATTERN ends in '$', which re.match lets through with a trailing newline.
    return _fullmatch(LAB_ID_RE, value)


def _clean(text: Any, limit: int = MAX_ERROR_CHARS) -> str:
    s = "".join(ch if (ch in "\n\t" or 32 <= ord(ch) < 127) else "?" for ch in str(text))
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """sha256 of the manifest's canonical JSON: the configuration one DEV/holdout claim evaluates."""
    canon = json.dumps(dict(manifest), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ── Dependencies ────────────────────────────────────────────────────────────
@dataclass
class LabDeps:
    """What the routes need from the outside world (real Modal objects in modal_runner, fakes in tests)."""

    store: Callable[[], I.LedgerStore]                       # factory; called lazily, once
    spawn: Callable[[Dict[str, Any]], str]                   # job dict -> job id ("fc-...")
    poll: Callable[[str], Tuple[str, Any, Optional[str]]]    # job id -> (status, result, error)
    kv_get: Callable[[str], Any]                             # the JOBS Dict
    kv_put: Callable[[str, Any], None]
    read_token_ok: Callable[[Any], bool]                     # request -> valid X-Sajim-Token?
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    refresh: Callable[[], None] = lambda: None               # before reads, when the store has no refresh()
    deploy_id: str = ""
    now: Callable[[], datetime] = _utcnow
    #: Atomic "put unless the key exists" on the JOBS Dict -> created? (modal: put(..., skip_if_exists=True)).
    #: None = emulated with kv_get + kv_put, which is NOT atomic (single-threaded tests only).
    kv_put_new: Optional[Callable[[str, Any], bool]] = None


def is_lab_path(path: str) -> bool:
    return path == LAB_PREFIX or path.startswith(LAB_PREFIX + "/")


def lab_tokens(environ: Mapping[str, str]) -> Tuple[str, str]:
    """(lab token, judge token) from the api() secret; NotConfigured (503) unless both are usable."""
    lab = (environ.get("SAJIM_LAB_TOKEN") or "").strip()
    judge = (environ.get("SAJIM_JUDGE_TOKEN") or "").strip()
    api_token = (environ.get("SAJIM_API_TOKEN") or "").strip()
    if not lab or not judge:
        raise NotConfigured("lab not configured: SAJIM_LAB_TOKEN and SAJIM_JUDGE_TOKEN must both be set")
    if len(lab) < MIN_TOKEN_CHARS or len(judge) < MIN_TOKEN_CHARS:
        raise NotConfigured(f"lab not configured: lab tokens must be at least {MIN_TOKEN_CHARS} characters")
    if hmac.compare_digest(lab.encode("utf-8"), judge.encode("utf-8")):
        raise NotConfigured("lab not configured: SAJIM_LAB_TOKEN and SAJIM_JUDGE_TOKEN must differ")
    if api_token and (hmac.compare_digest(lab.encode("utf-8"), api_token.encode("utf-8"))
                      or hmac.compare_digest(judge.encode("utf-8"), api_token.encode("utf-8"))):
        raise NotConfigured("lab not configured: lab tokens must differ from SAJIM_API_TOKEN")
    return lab, judge


def _token_matches(got: Optional[str], expected: str) -> bool:
    # Bytes: compare_digest raises TypeError on non-ASCII str (headers decode as latin-1).
    return bool(got and expected) and hmac.compare_digest(got.encode("latin-1", "replace"),
                                                          expected.encode("utf-8"))


def lab_scope(request: Any, deps: LabDeps) -> str:
    """'judge' | 'builder' | 'read'. A presented-but-wrong lab token is 401, never a fallback."""
    lab, judge = lab_tokens(deps.environ)
    headers = request.headers
    got = headers.get(I.HEADER_JUDGE_TOKEN)
    if got is not None:
        if _token_matches(got, judge):
            return "judge"
        raise Unauthorized("invalid X-Judge-Token header")
    got = headers.get(I.HEADER_LAB_TOKEN)
    if got is not None:
        if _token_matches(got, lab):
            return "builder"
        raise Unauthorized("invalid X-Lab-Token header")
    if deps.read_token_ok(request):
        return "read"
    raise Unauthorized("missing or invalid X-Lab-Token / X-Judge-Token header")


def error_response(e: I.LabError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": e.detail()}, status_code=e.status)


def guard_response(request: Any, deps: LabDeps):
    """
    The /lab branch of the api() middleware: None to continue, else the refusal.
    Runs before routing, so FastAPI never buffers or parses an oversized body.
    """
    try:
        lab_tokens(deps.environ)
    except I.LabError as e:
        return error_response(e)
    length = request.headers.get("content-length", "")
    if length and (not length.isdigit() or int(length) > I.LAB_MAX_BODY_BYTES):
        return error_response(I.PayloadTooLarge(f"request body over {I.LAB_MAX_BODY_BYTES} bytes"))
    return None


# ── Body parsing ────────────────────────────────────────────────────────────
def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        d: Dict[str, Any] = {}
        for k, v in items:
            if k in d:
                raise ValueError(f"duplicate key {k!r}")
            d[k] = v
        return d

    def no_const(name):
        raise ValueError(f"{name} is not valid JSON")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=no_const)


def parse_body(raw: bytes) -> Dict[str, Any]:
    if len(raw) > I.LAB_MAX_BODY_BYTES:
        raise I.PayloadTooLarge(f"request body over {I.LAB_MAX_BODY_BYTES} bytes")
    try:
        body = _strict_json(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise I.LabError("body must be strict UTF-8 JSON", code="malformed") from None
    if not isinstance(body, dict):
        raise I.LabError("body must be a JSON object", code="malformed")
    return body


def _utf8(value: Any, what: str) -> bytes:
    if not isinstance(value, str):
        raise I.LabError(f"{what} must be a string", code="malformed")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise I.LabError(f"{what} is not valid UTF-8 text", code="malformed") from None


def _ignored_fields(body: Mapping[str, Any]) -> List[str]:
    out = [_clean(k, 64) for k in body if k not in SUBMISSION_KEYS]
    return out[:MAX_IGNORED_FIELDS]


# ── The service (sync; the FastAPI glue runs it in a threadpool) ────────────
class LabService:
    """The /lab routes as plain methods: (scope, raw body | path args) -> JSON-able dict, or LabError."""

    def __init__(self, deps: LabDeps):
        self.deps = deps
        self._store: Optional[I.LedgerStore] = None

    # ── plumbing ──
    def store(self) -> I.LedgerStore:
        """The store, refreshed: its own refresh() when it has one (ModalStore: throttled volume reload)."""
        if self._store is None:
            self._store = self.deps.store()
        own = getattr(self._store, "refresh", None)
        try:
            (own if callable(own) else self.deps.refresh)()
        except Exception as e:  # noqa: BLE001 — serve the current view
            logger.warning("lab store refresh skipped: %s", e)
        return self._store

    def _kv_get(self, key: str) -> Any:
        try:
            return self.deps.kv_get(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("lab job memory read failed for %s: %s", key, e)
            return None

    def _kv_put(self, key: str, value: Any) -> None:
        try:
            self.deps.kv_put(key, value)
        except Exception as e:  # noqa: BLE001
            logger.warning("lab job memory write failed for %s: %s", key, e)

    def _kv_get_strict(self, key: str) -> Any:
        """A read a spawn decision depends on: an unreadable job memory is 503, never "nothing started"."""
        try:
            return self.deps.kv_get(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("lab job memory read failed for %s: %s", key, e)
            raise Unavailable("the lab job memory is unavailable; retry the same request") from None

    def _kv_put_new(self, key: str, value: str) -> bool:
        """First writer wins. True only for the caller whose value is now stored under `key`."""
        try:
            if self.deps.kv_put_new is not None:
                if self.deps.kv_put_new(key, value) is True:
                    return True
                # modal retries a put whose response was lost: our exact value (with its nonce) means it landed.
                return self.deps.kv_get(key) == value
            if self.deps.kv_get(key) is not None:
                return False
            self.deps.kv_put(key, value)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("lab spawn lock %s failed: %s", key, e)
            raise Unavailable("the lab job memory is unavailable; retry the same request") from None

    def job_info(self, job_id: str) -> Optional[Dict[str, Any]]:
        raw = self._kv_get(JOB_INFO_PREFIX + job_id)
        if not isinstance(raw, str):
            return None
        try:
            info = json.loads(raw)
        except ValueError:
            return None
        return info if isinstance(info, dict) else None

    def _remember(self, job_id: str, info: Mapping[str, Any]) -> None:
        self._kv_put(JOB_INFO_PREFIX + job_id, json.dumps(dict(info), allow_nan=False))
        self._kv_put(REQUEST_PREFIX + str(info["request_id"]), job_id)

    # ── POST bodies: section 9.2 steps 1-5 ──
    def submission(self, raw: bytes, *, kind: str) -> Tuple[I.Submission, Dict[str, Any], List[str]]:
        from sajim_quant.lab import contract

        b = parse_body(raw)                                                       # 1. body size, JSON
        src = _utf8(b.get("strategy_py"), "strategy_py")
        if len(src) > gates.MAX_SOURCE_BYTES:
            raise I.PayloadTooLarge(f"strategy.py is over {gates.MAX_SOURCE_BYTES} bytes")
        spec = _utf8(b.get("spec_md"), "spec_md")
        stage = b.get("stage", splits.DEV if kind == "check" else None)          # 2. ids, stage, request id
        if not (_is_lab_id(b.get("lab_id")) and _is_lab_id(b.get("family"))):
            raise I.LabError(f"lab_id and family must match {gates.LAB_ID_PATTERN}", code="malformed")
        if stage not in splits.SCORED_STAGES:
            raise I.LabError(f"stage must be one of {splits.SCORED_STAGES}", code="malformed")
        if kind == "check" and stage != splits.DEV:
            raise I.LabError("integrity checks run on DEV bars only (stage must be dev)", code="malformed")
        if not _fullmatch(I.REQUEST_ID_RE, b.get("request_id")):
            raise I.LabError("request_id must be 8-64 chars of [A-Za-z0-9_-]", code="malformed")
        manifest = b.get("manifest")
        if not isinstance(manifest, dict):
            raise I.LabError("manifest must be a JSON object", code="malformed")
        if b.get("client") is not None and not isinstance(b.get("client"), dict):
            raise I.LabError("client must be a JSON object", code="malformed")
        sub = I.Submission(lab_id=b["lab_id"], family=b["family"], strategy_py=src, spec_md=spec,
                           manifest=manifest)
        if manifest.get("code_sha256") != sub.code_sha256 or manifest.get("spec_sha256") != sub.spec_sha256:
            raise I.ContractRefused("manifest code_sha256/spec_sha256 do not match the submitted bytes",  # 3.
                                    code="sha_mismatch")
        problems = [f"lint: {i.format('strategy.py')}" for i in contract.lint_source(src)]     # 4.
        problems += contract.validate_manifest(manifest, strategy_py=src, spec_md=spec, require_shas=True)
        if manifest.get("id") != sub.lab_id:
            problems.append("manifest: id does not match the submission's lab_id")
        if manifest.get("family", sub.lab_id) != sub.family:
            problems.append("manifest: family does not match the submission's family")
        if problems:
            raise I.ContractRefused("contract refused", reasons=[_clean(p, 300) for p in problems[:MAX_REASONS]])
        ignored = _ignored_fields(b)                                              # 5. ignored client keys
        if ignored:
            logger.warning("lab %s: ignored client fields %s (windows and risk come from the server)",
                           sub.lab_id, ignored)
        return sub, b, ignored

    # ── family binding (lab_id -> family, first writer wins) ──
    @staticmethod
    def _binding_path(lab_id: str) -> str:
        return f"{BINDINGS_DIR}/{lab_id}.json"

    def _bound_family(self, store: I.LedgerStore, lab_id: str) -> Optional[str]:
        raw = store.read(self._binding_path(lab_id))
        if raw is None:
            return None
        try:
            fam = json.loads(raw.decode("utf-8")).get("family")
        except (UnicodeDecodeError, ValueError, AttributeError):
            fam = None
        if not _is_lab_id(fam):
            raise Unavailable(f"the family binding of {lab_id} is unreadable")
        return fam

    def _require_binding(self, store: I.LedgerStore, sub: I.Submission) -> None:
        fam = self._bound_family(store, sub.lab_id)
        if fam is not None and fam != sub.family:
            raise I.ContractRefused(f"{sub.lab_id} is bound to family {fam}, not {sub.family}",
                                    code="family_mismatch")

    def _bind(self, store: I.LedgerStore, sub: I.Submission, request_id: str) -> None:
        self._require_binding(store, sub)
        # The file is the durable record, but write_once on a volume is not exclusive across API containers;
        # the atomic Dict claim decides a first-claim race (its entry may expire later; the file stays).
        key = BINDING_KEY_PREFIX + sub.lab_id
        if not self._kv_put_new(key, sub.family):
            fam = self._kv_get_strict(key)
            if fam is not None and fam != sub.family:
                raise I.ContractRefused(f"{sub.lab_id} is bound to family {_clean(fam, 64)}, not {sub.family}",
                                        code="family_mismatch")
        data = json.dumps({"lab_id": sub.lab_id, "family": sub.family, "request_id": request_id,
                           "created_at": self.deps.now().isoformat()}).encode("utf-8")
        if not store.write_once(self._binding_path(sub.lab_id), data):
            self._require_binding(store, sub)                     # lost a race: the winner decides

    # ── holdout precondition (DEV pass + DEV-time snapshot) ──
    def _require_dev_pass(self, store: I.LedgerStore, sub: I.Submission) -> None:
        from sajim_quant.lab import ledger

        dev_sc = ledger.load_dev_scorecard(store, sub.family, sub.code_sha256)
        if (dev_sc is None or dev_sc.get("stage") != splits.DEV
                or dev_sc.get("dev_hard_gates_passed") is not True
                or ledger.dev_pass_record(store, sub.family, sub.code_sha256) is None):
            raise I.DevNotPassed(f"no passing DEV scorecard for sha {sub.sha12} in {sub.family}")
        raw = store.read(f"{ledger.LAB_ROOT}/{sub.family}/{sub.sha12}/manifest.json")
        try:
            snap = json.loads(raw.decode("utf-8")) if raw is not None else None
        except (UnicodeDecodeError, ValueError):
            snap = None
        if not isinstance(snap, dict):
            raise I.DevNotPassed(f"no DEV-time manifest snapshot for sha {sub.sha12}")
        m = sub.manifest
        diffs = [k for k in SNAPSHOT_KEYS
                 if snap.get(k, snap.get("id") if k == "family" else None)
                 != m.get(k, m.get("id") if k == "family" else None)]
        if snap.get("spec_sha256") != sub.spec_sha256 or dev_sc.get("spec_sha256") != sub.spec_sha256:
            diffs.append("spec_sha256")
        for key, want in (("id", sub.lab_id), ("family", sub.family), ("symbol", m.get("symbol")),
                          ("timeframe", m.get("timeframe"))):
            if dev_sc.get(key) != want and key not in diffs:
                diffs.append(key)
        if diffs:
            raise I.DevNotPassed("the holdout submission differs from what DEV judged",
                                 code="dev_snapshot_mismatch",
                                 reasons=[f"{k} differs from the DEV-time snapshot" for k in diffs])

    def _done_record(self, store: I.LedgerStore, family: str, job_kind: str,
                     request_id: str) -> Optional[Dict[str, Any]]:
        from sajim_quant.lab import ledger

        record_type = "dev_trial" if job_kind == I.DEV_RESULT_KIND else "holdout"
        recs = [r for r in ledger.list_records(store, family, record_type=record_type, request_id=request_id)
                if (r.get("payload") or {}).get("status") == "done"]
        return recs[-1] if recs else None

    # ── dedup ──
    def _remembered(self, rid: str, job_kind: str, sub: I.Submission) -> Optional[Tuple[str, Dict[str, Any]]]:
        """(job id, info) this request already started, or None (also when the id belongs to another request)."""
        job_id = self._kv_get(REQUEST_PREFIX + rid)
        if not isinstance(job_id, str) or not SAFE_JOB.fullmatch(job_id):
            return None
        info = self.job_info(job_id)
        if not info or (info.get("kind"), info.get("request_id"), info.get("family"), info.get("code_sha256")) \
                != (job_kind, rid, sub.family, sub.code_sha256):
            if info is None:
                logger.warning("request id %s maps to %s, which is not a lab job; ignored", rid, job_id)
            return None
        return job_id, info

    @staticmethod
    def _started(job_id: str, info: Mapping[str, Any], *, deduplicated: bool) -> Dict[str, Any]:
        out = {"job_id": job_id, "status": "queued", "kind": info["kind"], "request_id": info["request_id"],
               "code_sha256": info["code_sha256"]}
        if info["kind"] != I.CHECK_RESULT_KIND:
            out.update(trial=info.get("trial"), holdout_touch=info.get("holdout_touch"))
        out["deduplicated"] = deduplicated
        return out

    def _info(self, sub: I.Submission, job_kind: str, rid: str, *, trial: Optional[int], touch: Optional[int],
              created_at: Optional[str] = None, deploy_id: Optional[str] = None) -> Dict[str, Any]:
        return {"kind": job_kind, "lab_id": sub.lab_id, "family": sub.family, "code_sha256": sub.code_sha256,
                "request_id": rid, "trial": trial, "holdout_touch": touch,
                "created_at": created_at or self.deps.now().isoformat(timespec="seconds"),
                "deploy_id": self.deps.deploy_id if deploy_id is None else deploy_id}

    @staticmethod
    def _lock_key(job_kind: str, sub: I.Submission, rid: str, n: int) -> str:
        return f"{SPAWN_LOCK_PREFIX}{job_kind}-{sub.family}-{sub.code_sha256}-{rid}-{n}"

    @staticmethod
    def _parse_lock(raw: Any) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        try:
            lock = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            lock = None
        return lock if isinstance(lock, dict) else {"state": "unreadable"}

    def _lock_age_s(self, lock: Mapping[str, Any]) -> float:
        try:
            return (self.deps.now() - datetime.fromisoformat(str(lock.get("at")))).total_seconds()
        except (TypeError, ValueError):
            return float("inf")

    def _result_exists(self, store: I.LedgerStore, sub: I.Submission, job_kind: str) -> bool:
        """Did an earlier attempt already write this request's scorecard? (Then it must never run again.)"""
        from sajim_quant.lab import ledger

        if job_kind == I.DEV_RESULT_KIND:
            return store.read(ledger.dev_scorecard_relpath(sub.family, sub.code_sha256)) is not None
        if job_kind == I.HOLDOUT_RESULT_KIND:
            return store.read(f"{ledger.LAB_ROOT}/{sub.family}/{sub.sha12}/holdout_scorecard.json") is not None
        return False

    def _spawn_once(self, store: I.LedgerStore, sub: I.Submission, job_kind: str, rid: str, *,
                    trial: Optional[int], touch: Optional[int]) -> Dict[str, Any]:
        """
        Start this request's job at most once per attempt, across threads and API containers.

        Attempt n is claimed with an atomic first-writer-wins put of a "pending" lock before the spawn;
        a concurrent POST of the same request sees the lock and gets 503 (the client retries and is
        then deduplicated). The next attempt opens only when attempt n's job failed or expired with no
        done record and no scorecard written (an infrastructure failure), or its lock was abandoned.

        Every lock records the manifest digest its attempt started with, and a DEV/holdout attempt n > 0
        must carry the same digest (else 409 request_conflict). "failed" can be a transient poll error
        and a "spawned" write can be lost, so an earlier attempt may still be running: a respawn may
        repeat that evaluation, never evaluate another configuration under the same claim.
        """
        digest = manifest_digest(sub.manifest)
        one_config = job_kind in (I.DEV_RESULT_KIND, I.HOLDOUT_RESULT_KIND)
        earlier: set = set()
        for n in range(MAX_SPAWN_ATTEMPTS):
            key = self._lock_key(job_kind, sub, rid, n)
            lock = self._parse_lock(self._kv_get_strict(key))
            if lock is None and self._result_exists(store, sub, job_kind):
                # A scorecard with no done record and no spawn lock left (the Dict entry expired): an earlier
                # attempt wrote its result and then failed. Running it again would be a second look.
                raise AlreadyEvaluated(f"{sub.sha12} already has a {job_kind} scorecard; it is not run again")
            if lock is None:
                if one_config and any(d != digest for d in earlier):
                    raise RequestConflict(f"request {rid} first started with a different manifest; resubmit the "
                                          f"same manifest (a changed configuration needs changed code: a new sha)")
                mine = json.dumps({"state": "pending", "at": self.deps.now().isoformat(),
                                   "nonce": uuid.uuid4().hex, "manifest_sha256": digest})
                if self._kv_put_new(key, mine):
                    return self._spawn(sub, job_kind, rid, trial=trial, touch=touch, lock_key=key, digest=digest)
                lock = self._parse_lock(self._kv_get_strict(key)) or {"state": "unreadable"}
            if isinstance(lock.get("manifest_sha256"), str):
                earlier.add(lock["manifest_sha256"])
            state = lock.get("state")
            if state == "spawned" and _fullmatch(SAFE_JOB, lock.get("job_id")):
                job_id = lock["job_id"]
                status, _, _ = self.deps.poll(job_id)
                if (status not in ("failed", "expired") or self._done_record(store, sub.family, job_kind, rid)
                        or self._result_exists(store, sub, job_kind)):
                    info = self.job_info(job_id)
                    if not info or info.get("request_id") != rid or info.get("kind") != job_kind:
                        info = self._info(sub, job_kind, rid, trial=trial, touch=touch)
                    self._remember(job_id, info)
                    return self._started(job_id, info, deduplicated=True)
                logger.info("lab job %s for %s is %s with no result; attempt %d under the same claim",
                            job_id, rid, status, n + 1)
                continue
            if state == "spawn_failed":
                continue
            if state == "pending" and self._lock_age_s(lock) < SPAWN_LOCK_STALE_S:
                raise Unavailable("this request is being started by another call; retry the same request")
            logger.warning("lab spawn lock %s is %s; moving to the next attempt", key, state)
        raise AttemptsExhausted(f"request {rid} failed {MAX_SPAWN_ATTEMPTS} times; the server will not start it "
                                f"again")

    def _spawn(self, sub: I.Submission, job_kind: str, rid: str, *, trial: Optional[int],
               touch: Optional[int], lock_key: Optional[str] = None, digest: Optional[str] = None) -> Dict[str, Any]:
        job = {"kind": job_kind, "lab_id": sub.lab_id, "family": sub.family, "request_id": rid,
               "strategy_py": sub.strategy_py.decode("utf-8"), "spec_md": sub.spec_md.decode("utf-8"),
               "manifest": dict(sub.manifest), "trial": trial, "holdout_touch": touch}
        try:
            job_id = self.deps.spawn(job)
        except Exception as e:  # noqa: BLE001 — the claim stays; a retry dedups onto it
            logger.error("lab job spawn failed for %s %s: %s", job_kind, sub.sha12, e)
            if lock_key is not None:
                self._kv_put(lock_key, json.dumps({"state": "spawn_failed", "at": self.deps.now().isoformat(),
                                                   "manifest_sha256": digest}))
            raise Unavailable("could not start the lab job; retry the same request") from None
        if lock_key is not None:
            self._kv_put(lock_key, json.dumps({"state": "spawned", "job_id": job_id,
                                               "at": self.deps.now().isoformat(), "manifest_sha256": digest}))
        info = self._info(sub, job_kind, rid, trial=trial, touch=touch)
        self._remember(job_id, info)
        logger.info("lab job %s started: %s %s %s", job_id, job_kind, sub.family, sub.sha12)
        return self._started(job_id, info, deduplicated=False)

    # ── POST /lab/check ──
    def check(self, scope: str, raw: bytes) -> Dict[str, Any]:
        sub, b, _ = self.submission(raw, kind="check")
        if scope not in ("builder", "judge"):                                     # 6.
            raise I.WrongScope("checks need the lab or judge token")
        rid = b["request_id"]
        if rid in {I.derive_request_id(sub.family, sub.code_sha256, k) for k in ("dev", "holdout", "finalize")}:
            raise I.LabError("a check needs its own request_id (uuid4 hex)", code="malformed")
        store = self.store()
        self._require_binding(store, sub)
        prior = self._remembered(rid, I.CHECK_RESULT_KIND, sub)                   # 9. dedup only
        if prior is None and self._kv_get_strict(REQUEST_PREFIX + rid) is not None:
            # The id already names another request's job (another sha's check, a judge's holdout, a dashboard
            # backtest): taking it over would make that request's retry start a second job.
            raise I.LabError("request_id is already used by another request; use a fresh uuid4 hex",
                             code="malformed")
        if prior is not None:
            status, _, _ = self.deps.poll(prior[0])
            if status not in ("failed", "expired"):
                return self._started(prior[0], prior[1], deduplicated=True)
        return self._spawn_once(store, sub, I.CHECK_RESULT_KIND, rid, trial=None, touch=None)

    # ── POST /lab/evaluate ──
    def evaluate(self, scope: str, raw: bytes) -> Dict[str, Any]:
        from sajim_quant.lab import ledger

        sub, b, ignored = self.submission(raw, kind="evaluate")
        stage = b["stage"]
        if scope not in ("builder", "judge"):                                     # 6.
            raise I.WrongScope("evaluations need the lab or judge token")
        if stage == splits.HOLDOUT and scope != "judge":
            raise I.WrongScope("the holdout needs the judge token")
        job_kind = I.DEV_RESULT_KIND if stage == splits.DEV else I.HOLDOUT_RESULT_KIND
        rid = b["request_id"]
        if rid != I.derive_request_id(sub.family, sub.code_sha256, stage):       # 7./8.
            raise I.LabError("request_id is not the derived id for this family/sha/stage", code="malformed")
        store = self.store()
        self._require_binding(store, sub)

        prior = self._remembered(rid, job_kind, sub)                              # fast path; _spawn_once decides
        if prior is not None:
            status, _, _ = self.deps.poll(prior[0])
            if status not in ("failed", "expired") or self._done_record(store, sub.family, job_kind, rid):
                return self._started(prior[0], prior[1], deduplicated=True)

        if job_kind == I.DEV_RESULT_KIND:
            self._bind(store, sub, rid)
            trial = ledger.claim_dev_trial(store, sub.family, lab_id=sub.lab_id, request_id=rid,
                                           code_sha256=sub.code_sha256, ignored_client_fields=ignored)
            if trial is None:
                raise I.BudgetExhausted(f"family {sub.family} has used all {gates.MAX_DEV_TRIALS_PER_FAMILY} "
                                        f"DEV trials")
            touch = None
        else:
            self._require_dev_pass(store, sub)
            self._bind(store, sub, rid)
            touch = ledger.claim_holdout(store, sub.family, sub.code_sha256, lab_id=sub.lab_id, request_id=rid,
                                         ignored_client_fields=ignored)     # raises 409 / 429
            trial = None

        done = self._done_record(store, sub.family, job_kind, rid)
        if done is not None and _fullmatch(SAFE_JOB, (done.get("payload") or {}).get("job_id")):
            # The job memory expired but this request already finished: point at its job (GET
            # /lab/job rebuilds the result from the stored scorecard). Never a second run.
            job_id = done["payload"]["job_id"]
            info = self._info(sub, job_kind, rid, trial=trial, touch=touch, created_at=done.get("created_at"),
                              deploy_id=done.get("deploy_id", ""))
            self._remember(job_id, info)
            return self._started(job_id, info, deduplicated=True)
        return self._spawn_once(store, sub, job_kind, rid, trial=trial, touch=touch)

    # ── GET /lab/job/{job_id} ──
    def job(self, scope: str, job_id: str) -> Dict[str, Any]:
        if not _fullmatch(SAFE_JOB, job_id):
            raise I.LabError("invalid job id", code="malformed")
        info = self.job_info(job_id)
        if info is None:
            raise NotFound(f"unknown lab job {job_id}")
        kind = info.get("kind")
        if kind == I.HOLDOUT_RESULT_KIND and scope != "judge":
            raise I.WrongScope("holdout jobs are judge-only")
        status, result, error = self.deps.poll(job_id)
        if status in ("failed", "expired") and kind in (I.DEV_RESULT_KIND, I.HOLDOUT_RESULT_KIND):
            rebuilt = self._rebuild_result(info, job_id)
            if rebuilt is not None:
                status, result, error = "done", rebuilt, None
        out: Dict[str, Any] = {"job_id": job_id, "status": status, "kind": kind}
        if error:
            out["error"] = _clean(error)
        if status == "done" and isinstance(result, dict):
            result = dict(result)
            if scope == "builder" and isinstance(result.get("scorecard"), Mapping):
                result["scorecard"] = self._builder_view(result["scorecard"])
            out["result"] = result
        return out

    def _rebuild_result(self, info: Mapping[str, Any], job_id: str) -> Optional[Dict[str, Any]]:
        """A finished job whose Modal result expired, rebuilt from its done record + stored scorecard."""
        family, rid = info.get("family"), info.get("request_id")
        if not (_is_lab_id(family) and _fullmatch(I.REQUEST_ID_RE, rid)):
            return None
        store = self.store()
        rec = self._done_record(store, family, str(info.get("kind")), rid)
        if rec is None or (rec.get("payload") or {}).get("job_id") != job_id:
            return None
        path = rec["payload"].get("scorecard_path")
        raw = store.read(path) if isinstance(path, str) else None
        if raw is None:
            return None
        sc = json.loads(raw.decode("utf-8"))
        return {"success": True, "kind": info.get("kind"), "code_sha256": sc.get("code_sha256"),
                "stage": sc.get("stage"), "scorecard": sc, "result_file": sc.get("result_file"),
                "deploy_id": rec.get("deploy_id", "")}

    @staticmethod
    def _builder_view(sc: Mapping[str, Any]) -> Dict[str, Any]:
        from sajim_quant.lab import scorecard
        try:
            return scorecard.builder_view(sc)
        except ValueError:
            raise I.WrongScope("holdout scorecards are judge-only") from None

    # ── GET /lab/{family} ──
    def family(self, scope: str, family: str) -> Dict[str, Any]:
        from sajim_quant.lab import ledger

        if not _is_lab_id(family):
            raise I.LabError(f"family must match {gates.LAB_ID_PATTERN}", code="malformed")
        store = self.store()
        records = ledger.list_records(store, family)
        sha_dirs = [n for n in store.listdir(f"{ledger.LAB_ROOT}/{family}") if _fullmatch(SHA12_RE, n)]
        if not records and not sha_dirs:
            raise NotFound(f"unknown family {family}")
        scorecards = []
        for s in sha_dirs:
            for stage in splits.SCORED_STAGES:
                raw = store.read(f"{ledger.LAB_ROOT}/{family}/{s}/{stage}_scorecard.json")
                if raw is None:
                    continue
                try:
                    sc = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if scope == "builder" and stage == splits.HOLDOUT:
                    scorecards.append({"sha12": s, "stage": stage, "created_at": sc.get("created_at")})
                else:
                    scorecards.append({"sha12": s, "stage": stage, "verdict": sc.get("verdict"),
                                       "final": sc.get("final"), "trial": sc.get("trial"),
                                       "created_at": sc.get("created_at")})
        scorecards.sort(key=lambda e: (str(e.get("created_at") or ""), e["sha12"], e["stage"]))
        touches = ledger.count_holdout_touches(store, family)
        out_records = [self._redact(r) if scope == "builder" else r for r in records[-FAMILY_MAX_RECORDS:]]
        return {"family": family, "ids": sorted({str(r.get("id")) for r in records if _is_lab_id(r.get("id"))}),
                "n_trials": ledger.count_dev_trials(store, family), "max_trials": gates.MAX_DEV_TRIALS_PER_FAMILY,
                "holdout_touches": touches, "max_holdout_touches": gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY,
                "holdout_burned": touches >= gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY,
                "n_trials_global": ledger.count_global_trials(store), "scorecards": scorecards,
                "records": out_records, "records_truncated": len(records) > FAMILY_MAX_RECORDS}

    @staticmethod
    def _redact(rec: Mapping[str, Any]) -> Dict[str, Any]:
        if rec.get("record_type") not in ("holdout", "review", "verdict"):
            return dict(rec)
        p = rec.get("payload") or {}
        return {"record_type": rec.get("record_type"), "created_at": rec.get("created_at"),
                "payload": {"touch": p.get("touch", p.get("holdout_touch"))}}

    # ── GET /lab/{family}/scorecard/{sha12} ──
    def scorecard(self, scope: str, family: str, sha12: str, stage: str) -> Dict[str, Any]:
        from sajim_quant.lab import ledger

        if not _is_lab_id(family) or not _fullmatch(SHA12_RE, sha12):
            raise I.LabError("family or sha12 is malformed", code="malformed")
        if stage not in splits.SCORED_STAGES:
            raise I.LabError(f"stage must be one of {splits.SCORED_STAGES}", code="malformed")
        if stage == splits.HOLDOUT and scope not in ("judge", "read"):
            raise I.WrongScope("holdout scorecards are judge-only")
        raw = self.store().read(f"{ledger.LAB_ROOT}/{family}/{sha12}/{stage}_scorecard.json")
        if raw is None:
            raise NotFound(f"no {stage} scorecard for {family}/{sha12}")
        sc = json.loads(raw.decode("utf-8"))
        return self._builder_view(sc) if scope == "builder" else sc

    # ── POST /lab/finalize ──
    def finalize(self, scope: str, raw: bytes) -> Dict[str, Any]:
        from sajim_quant.lab import cli, ledger, scorecard

        b = parse_body(raw)
        if scope != "judge":
            raise I.WrongScope("finalize needs the judge token")
        lab_id, family, sha, rid = b.get("lab_id"), b.get("family"), b.get("code_sha256"), b.get("request_id")
        verdict, source, review = b.get("verdict"), b.get("source"), b.get("review")
        if not (_is_lab_id(lab_id) and _is_lab_id(family) and _fullmatch(SHA64_RE, sha)):
            raise I.LabError("lab_id, family or code_sha256 is malformed", code="malformed")
        if not _fullmatch(I.REQUEST_ID_RE, rid) or rid != I.derive_request_id(family, sha, "finalize"):
            raise I.LabError("request_id is not the derived finalize id for this family/sha", code="malformed")
        if verdict not in gates.VERDICTS or source not in FINALIZE_SOURCES:
            raise I.LabError(f"verdict must be one of {gates.VERDICTS} and source one of {FINALIZE_SOURCES}",
                             code="malformed")
        if review is not None:
            problems = cli.validate_review(review, lab_id=lab_id, code_sha256=sha)
            if problems:
                raise I.ContractRefused("invalid review", reasons=[_clean(p, 300) for p in problems[:MAX_REASONS]])
        unknown = [k for k in b if k not in FINALIZE_KEYS]
        if unknown:
            logger.warning("finalize %s: ignored client fields %s", lab_id, _ignored_fields({k: 1 for k in unknown}))

        store = self.store()
        fam = self._bound_family(store, lab_id)
        if fam is not None and fam != family:
            raise I.ContractRefused(f"{lab_id} is bound to family {fam}, not {family}", code="family_mismatch")
        dev_sc = ledger.load_dev_scorecard(store, family, sha)
        if dev_sc is None or dev_sc.get("id") != lab_id:
            raise NotFound(f"no DEV scorecard for {lab_id} sha {sha[:12]} in {family}")
        ho_raw = store.read(f"{ledger.LAB_ROOT}/{family}/{sha[:12]}/holdout_scorecard.json")
        ho_sc = json.loads(ho_raw.decode("utf-8")) if ho_raw is not None else None
        if not (isinstance(ho_sc, dict) and ho_sc.get("code_sha256") == sha):
            ho_sc = None
        if ho_sc is not None:
            before = str(ho_sc.get("verdict"))
        else:
            burned = ledger.count_holdout_touches(store, family) >= gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY
            before = scorecard.verdict(scorecard.gate_results_from_json(dev_sc.get("gates") or []), splits.DEV,
                                       budget_exhausted=True, holdout_burned=burned).verdict
        if before not in gates.VERDICTS or gates.worse(verdict, before) != verdict:
            raise I.ContractRefused(f"verdict {verdict} is better than the scorecards support ({before})",
                                    code="verdict_refused")

        existing = ledger.list_records(store, family, record_type="verdict", request_id=rid)
        if existing:
            if (existing[0].get("payload") or {}).get("verdict") != verdict:
                raise AlreadyFinalized(f"{lab_id} sha {sha[:12]} was already finalized with another verdict")
            paths = [ledger.record_relpath(r) for r in ledger.list_records(store, family, request_id=rid)
                     if r.get("record_type") in ("review", "verdict")]
            return {"ok": True, "records": paths, "verdict": verdict}

        t = self.deps.now()
        paths = []
        if review is not None:
            paths.append(ledger.put_record(store, ledger.make_record(
                "review", family=family, lab_id=lab_id, request_id=rid, actor="critic", code_sha256=sha,
                deploy_id=self.deps.deploy_id, now=t,
                payload={"decision": review.get("decision"), "to": review.get("to"), "reason": review.get("reason"),
                         "verdict_before": before, "verdict_after": verdict})))
        paths.append(ledger.put_record(store, ledger.make_record(
            "verdict", family=family, lab_id=lab_id, request_id=rid, actor="judge", code_sha256=sha,
            deploy_id=self.deps.deploy_id, now=t + timedelta(microseconds=1),
            payload={"verdict": verdict, "final": True, "source": source, "trial": dev_sc.get("trial"),
                     "holdout_touch": ho_sc.get("holdout_touches") if ho_sc else None})))
        logger.info("finalized %s %s: %s (%s)", family, sha[:12], verdict, source)
        return {"ok": True, "records": paths, "verdict": verdict}


# ── FastAPI glue ────────────────────────────────────────────────────────────
def install(web: Any, deps: LabDeps) -> LabService:
    """Register the /lab routes on `web`. /lab/job/{job_id} is declared before /lab/{family}."""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    svc = LabService(deps)

    def _fail(e: Exception):
        if isinstance(e, I.LabError):
            return error_response(e)
        logger.exception("lab route failed")
        return error_response(Unavailable(f"lab API error ({type(e).__name__}); retry"))

    async def _read_body(request: Request) -> bytes:
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > I.LAB_MAX_BODY_BYTES:
                raise I.PayloadTooLarge(f"request body over {I.LAB_MAX_BODY_BYTES} bytes")
        return bytes(buf)

    async def _post(request: Request, fn: Callable[[str, bytes], Dict[str, Any]]):
        try:
            scope = lab_scope(request, deps)
            raw = await _read_body(request)
            return JSONResponse(await run_in_threadpool(fn, scope, raw))
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    def _get(request: Request, fn: Callable[..., Dict[str, Any]], *args: Any):
        try:
            return JSONResponse(fn(lab_scope(request, deps), *args))
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    @web.post("/lab/check")
    async def lab_check(request: Request):
        return await _post(request, svc.check)

    @web.post("/lab/evaluate")
    async def lab_evaluate(request: Request):
        return await _post(request, svc.evaluate)

    @web.post("/lab/finalize")
    async def lab_finalize(request: Request):
        return await _post(request, svc.finalize)

    @web.get("/lab/job/{job_id}")
    def lab_job_status(request: Request, job_id: str):
        return _get(request, svc.job, job_id)

    @web.get("/lab/{family}")
    def lab_family(request: Request, family: str):
        return _get(request, svc.family, family)

    @web.get("/lab/{family}/scorecard/{sha12}")
    def lab_scorecard(request: Request, family: str, sha12: str, stage: str = splits.DEV):
        return _get(request, svc.scorecard, family, sha12, stage)

    return svc


def build_app(deps: LabDeps) -> Any:
    """A standalone app with the same /lab middleware branch as modal_runner.api() (tests, local dev)."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    web = FastAPI(title="SAJIM lab API (local)", docs_url=None, redoc_url=None, openapi_url=None)

    @web.middleware("http")
    async def _guard(request: Request, call_next):
        if is_lab_path(request.url.path):
            refusal = guard_response(request, deps)
            if refusal is not None:
                return refusal
        elif request.url.path != "/health" and not deps.read_token_ok(request):
            return JSONResponse({"detail": "missing or invalid X-Sajim-Token header"}, status_code=401)
        return await call_next(request)

    install(web, deps)
    return web


# ── The lab_job body (runs in the trusted Modal function; never in the API) ─
def _job_submission(job: Mapping[str, Any]) -> I.Submission:
    return I.Submission(lab_id=job["lab_id"], family=job["family"], strategy_py=job["strategy_py"].encode("utf-8"),
                        spec_md=job["spec_md"].encode("utf-8"), manifest=dict(job["manifest"]))


def load_stage_frame(loader: Any, sub: I.Submission, stage: str):
    """(StageFrame, quote rates ndarray, fx description). Loads only up to the stage's end."""
    from sajim_quant.core.fx import quote_rate_series
    from sajim_quant.core.instruments import get_spec
    from sajim_quant.lab import evaluate

    symbol, tf = sub.manifest["symbol"], sub.manifest["timeframe"]
    raw = loader.load(symbol, tf, start_date=None, end_date=splits.loader_bounds(stage)[1])
    sframe = evaluate.prepare_frame(raw, stage, int(sub.manifest["warmup_bars"]))
    rates, fx_desc = quote_rate_series(get_spec(symbol).quote_currency, tf, sframe.frame.index, loader=loader)
    return sframe, rates.to_numpy(), fx_desc


def run_lab_job(job: Mapping[str, Any], *, store: I.LedgerStore, executor: I.SandboxExecutor, loader: Any,
                deploy_id: str = "", job_id: Optional[str] = None) -> Dict[str, Any]:
    """
    One lab job (INTERFACES.md section 8, "The Modal lab_job"): load the stage frame, run
    evaluate.check / evaluate_dev / evaluate_holdout through `executor`, write the code
    snapshot (DEV only), the scorecard, results/<result_file> and the section 7.2 ledger
    record. Returns the section 9.2 job result; success False only on an infrastructure
    failure (a failed gate is still success True).
    """
    from sajim_quant.lab import evaluate, ledger, scorecard

    kind = job["kind"]
    sub = _job_submission(job)
    rid = job["request_id"]
    job_id = job_id if _fullmatch(SAFE_JOB, job_id) else None
    common = dict(family=sub.family, lab_id=sub.lab_id, request_id=rid, actor="api", code_sha256=sub.code_sha256,
                  spec_sha256=sub.spec_sha256, deploy_id=deploy_id)
    base = f"{ledger.LAB_ROOT}/{sub.family}/{sub.sha12}"
    try:
        if kind == I.CHECK_RESULT_KIND:
            sframe, _, _ = load_stage_frame(loader, sub, splits.DEV)
            rep = evaluate.check(sub, sframe, executor)
            reasons = [scorecard.scrub(r) for r in rep.reasons]
            ledger.put_record(store, ledger.make_record(
                "check", stage=splits.DEV, payload={"passed": rep.passed, "reasons": reasons,
                                                    "checks": dict(rep.checks)}, **common))
            return {"success": True, "kind": kind, "code_sha256": sub.code_sha256, "passed": rep.passed,
                    "reasons": reasons, "checks": dict(rep.checks), "deploy_id": deploy_id}

        if kind == I.DEV_RESULT_KIND:
            sframe, rates, fx_desc = load_stage_frame(loader, sub, splits.DEV)
            store.write(f"{base}/strategy.py", sub.strategy_py)       # the DEV-time snapshot a holdout must match
            store.write(f"{base}/spec.md", sub.spec_md)
            store.write(f"{base}/manifest.json", json.dumps(dict(sub.manifest), indent=2).encode("utf-8"))
            ev = evaluate.evaluate_dev(sub, sframe, executor, quote_rate=rates, fx_desc=fx_desc,
                                       budget=ledger.budget_info(store, sub.family, trial=job.get("trial")),
                                       deploy_id=deploy_id)
            sc_path = f"{base}/dev_scorecard.json"
        elif kind == I.HOLDOUT_RESULT_KIND:
            dev_sc = ledger.load_dev_scorecard(store, sub.family, sub.code_sha256)
            if dev_sc is None:
                raise I.DevNotPassed(f"no DEV scorecard for sha {sub.sha12}")
            sframe, rates, fx_desc = load_stage_frame(loader, sub, splits.HOLDOUT)
            ev = evaluate.evaluate_holdout(sub, sframe, executor, quote_rate=rates, fx_desc=fx_desc,
                                           budget=ledger.budget_info(store, sub.family), dev_scorecard=dev_sc,
                                           deploy_id=deploy_id, store=store)
            sc_path = f"{base}/holdout_scorecard.json"
        else:
            raise ValueError(f"unknown lab job kind {kind!r}")

        # Every byte string is built before anything is written, and the scorecard is written last before
        # the ledger record: a scorecard the builder can read always comes with its done record (an attempt
        # that wrote its scorecard is never respawned; see LabService._spawn_once).
        sc_bytes = scorecard.to_json_bytes(ev.scorecard)
        sc = json.loads(sc_bytes)
        result_bytes = (None if ev.result is None else
                        json.dumps(evaluate.result_payload(ev.result), allow_nan=False).encode("utf-8"))
        if result_bytes is not None:
            store.write(f"{RESULTS_PREFIX}/{sc['result_file']}", result_bytes)
        store.write(sc_path, sc_bytes)
        if kind == I.DEV_RESULT_KIND:
            payload = {"trial": job.get("trial"), "job_id": job_id, "status": "done", "verdict": sc["verdict"],
                       "final": sc["final"], "dev_hard_gates_passed": sc["dev_hard_gates_passed"],
                       "scorecard_path": sc_path, "result_file": sc["result_file"], "error": None}
            ledger.put_record(store, ledger.make_record("dev_trial", stage=splits.DEV, payload=payload, **common))
        else:
            touch = job.get("holdout_touch") if job.get("holdout_touch") is not None else sc["holdout_touches"]
            payload = {"touch": touch, "job_id": job_id, "status": "done", "verdict": sc["verdict"],
                       "holdout_trades": sc["headline"]["trades"], "scorecard_path": sc_path,
                       "result_file": sc["result_file"], "error": None}
            ledger.put_record(store, ledger.make_record("holdout", stage=splits.HOLDOUT, payload=payload, **common))
        return {"success": True, "kind": kind, "code_sha256": sub.code_sha256, "stage": sc["stage"],
                "scorecard": sc, "result_file": sc["result_file"], "deploy_id": deploy_id}
    except Exception as e:  # noqa: BLE001 — an infrastructure failure: report it, record it, never raise
        msg = _clean(e.detail()["message"] if isinstance(e, I.LabError) else f"{type(e).__name__}: {e}")
        logger.exception("lab job %s (%s %s) failed", job_id, kind, sub.sha12)
        if kind in (I.DEV_RESULT_KIND, I.HOLDOUT_RESULT_KIND):
            try:
                if kind == I.DEV_RESULT_KIND:
                    payload = {"trial": job.get("trial"), "job_id": job_id, "status": "failed", "verdict": None,
                               "final": None, "dev_hard_gates_passed": False, "scorecard_path": None,
                               "result_file": None, "error": msg}
                    rec = ledger.make_record("dev_trial", stage=splits.DEV, payload=payload, **common)
                else:
                    payload = {"touch": job.get("holdout_touch"), "job_id": job_id, "status": "failed",
                               "verdict": None, "holdout_trades": None, "scorecard_path": None, "result_file": None,
                               "error": msg}
                    rec = ledger.make_record("holdout", stage=splits.HOLDOUT, payload=payload, **common)
                ledger.put_record(store, rec)
            except Exception as e2:  # noqa: BLE001
                logger.error("could not record the failed lab job %s: %s", job_id, e2)
        return {"success": False, "kind": kind, "code_sha256": sub.code_sha256, "error": msg,
                "deploy_id": deploy_id}


def new_job_id() -> str:
    """A SAFE_JOB-shaped id for local/test spawners (Modal ids come from FunctionCall.object_id)."""
    return "fc-" + uuid.uuid4().hex[:24]
