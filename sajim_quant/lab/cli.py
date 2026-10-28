"""
SAJIM Strategy Lab — the laptop CLI (INTERFACES.md section 10).

The builder Claude session runs exactly these commands (the Bash hook allows nothing else):

    python -X utf8 -m sajim_quant.lab.cli check
    python -X utf8 -m sajim_quant.lab.cli dev
    python -X utf8 -m sajim_quant.lab.cli status
    python -X utf8 -m sajim_quant.lab.cli summary [--sha <sha12>]
    python -X utf8 -m sajim_quant.lab.cli done --final <sha12|sha64>
    python -X utf8 -m sajim_quant.lab.cli review --file review.json      (critic session)

The lab folder is strategies/generated/$SAJIM_LAB_ID/. Calls to the Modal lab API are
short and retried (network errors and 502/503/504 only; 429 is never retried), and jobs
are short-polled, so a flaky link only costs a `status` resume.

Config: SAJIM_API_URL (else SAJIM_MODAL_API_URL) and SAJIM_LAB_TOKEN (else the file
%USERPROFILE%\\.sajim\\lab_token). This module never reads the judge token,
SAJIM_API_TOKEN or sim/.env.local, and never prints a token.

What the builder sees: the compact DEV gate table, always labelled IN-SAMPLE. Never
per-trade, per-fold, per-neighbour or holdout numbers.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sajim_quant.lab import gates, splits
from sajim_quant.lab import interfaces as I

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]
CLI_VERSION = "1"

# Exit codes (INTERFACES.md section 10).
EXIT_OK = 0
EXIT_FAILED = 1          # check failed / DEV gates failed / NOT_PROVEN
EXIT_LOCAL = 2           # local lint, manifest, usage, missing config
EXIT_NETWORK = 3         # network / poll timeout (resume with `status`)
EXIT_REFUSED = 4         # server refusal (4xx: 429 budget, 403, 412, 422, ...)
EXIT_INFRA = 5           # the job itself failed for infrastructure reasons

RETRYABLE_STATUS = frozenset({502, 503, 504})
TERMINAL_STATUSES = ("done", "failed", "expired")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SHA12_RE = re.compile(r"^[0-9a-f]{12}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
_FINAL_RE = re.compile(r"^[0-9a-f]{12}(?:[0-9a-f]{52})?$")
_DEV_SC_RE = re.compile(r"^dev_([0-9a-f]{12})\.json$")
_LAB_ID_RE = re.compile(gates.LAB_ID_PATTERN)
#: http:// is accepted only for these hosts (a local test server); everything else needs https://.
LOCAL_HTTP_HOSTS = ("127.0.0.1", "localhost")
#: A reason that says a sandbox run was stopped for time (runner kinds `timeout` / `budget`).
_RUNTIME_REASON_RE = re.compile(r"\b(?:timeout|budget): |\bstopped for time\b|\btimed out\b")

REVIEW_FILE = "review.json"
REVIEW_KEYS = ("schema", "lab_id", "code_sha256", "decision", "to", "reason", "mismatches",
               "smells", "cost_sensitivity", "next_tests")
REVIEW_DECISIONS = ("KEEP", "DOWNGRADE")
REVIEW_DOWNGRADE_TO = (gates.NOT_PROVEN, gates.REJECT)
REVIEW_LIMITS = {"reason": 1000, "cost_sensitivity": 500}
REVIEW_LIST_LIMITS = {"mismatches": 10, "smells": 10, "next_tests": 3}
REVIEW_ITEM_CHARS = 500

#: Transport(method, url, headers, body, timeout) -> (status, body bytes). Raises OSError-like
#: exceptions for network failures; HTTP error statuses are returned, not raised.
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], Tuple[int, bytes]]


# ── Errors ──────────────────────────────────────────────────────────────────
class LabApiError(Exception):
    """A lab API call failed. code: a server detail.code, or network|timeout|retryable|not_configured|bad_response."""

    def __init__(self, message: str, *, status: int = 0, code: str = "http",
                 detail: Optional[Mapping[str, Any]] = None):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.detail: Dict[str, Any] = dict(detail or {"code": code, "message": message, "reasons": []})

    @property
    def reasons(self) -> List[str]:
        r = self.detail.get("reasons")
        return [str(x) for x in r] if isinstance(r, list) else []


def clean_text(text: Any, limit: int = 2000) -> str:
    """Untrusted text made safe for a terminal: printable ASCII + newline/tab, truncated."""
    s = "".join(ch if (ch in "\n\t" or 32 <= ord(ch) < 127) else "?" for ch in str(text))
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _error_from_response(status: int, raw: bytes) -> LabApiError:
    detail: Dict[str, Any] = {"code": f"http_{status}", "message": "", "reasons": []}
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
    except ValueError:
        parsed = {}
    d = parsed.get("detail") if isinstance(parsed, dict) else None
    if isinstance(d, dict):
        detail["code"] = clean_text(d.get("code") or detail["code"], 64)
        detail["message"] = clean_text(d.get("message") or "", 500)
        reasons = d.get("reasons")
        if isinstance(reasons, list):
            detail["reasons"] = [clean_text(r, 300) for r in reasons[:20]]
    elif d is not None:
        detail["message"] = clean_text(d, 500)
    elif not parsed and raw:
        detail["message"] = clean_text(raw[:500].decode("utf-8", errors="replace"), 500)
    msg = f"HTTP {status} {detail['code']}" + (f": {detail['message']}" if detail["message"] else "")
    return LabApiError(msg, status=status, code=detail["code"], detail=detail)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a 3xx: urllib would re-send the X-Lab-Token / X-Judge-Token header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: Optional[bytes],
                      timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(64 * 1024)
        except Exception:  # noqa: BLE001 — a body we cannot read is just an empty body
            raw = b""
        return int(e.code), raw


# ── HTTP client ─────────────────────────────────────────────────────────────
class LabClient:
    """
    Thin, retried client for the /lab routes. Sends exactly one token header: the judge
    token when given (runner only), else the lab (builder) token. Token values are kept
    private and never appear in messages or repr().
    """

    def __init__(self, base_url: str, *, lab_token: Optional[str] = None, judge_token: Optional[str] = None,
                 timeout: float = 60, retries: int = 4, transport: Optional[Transport] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if not base_url:
            raise LabApiError("lab API URL is not configured", code="not_configured")
        if judge_token:
            self._header = (I.HEADER_JUDGE_TOKEN, judge_token)
            self.scope = "judge"
        elif lab_token:
            self._header = (I.HEADER_LAB_TOKEN, lab_token)
            self.scope = "builder"
        else:
            raise LabApiError("no lab token configured", code="not_configured")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self._transport = transport or _urllib_transport
        self._sleep = sleep
        self._clock = clock

    def __repr__(self) -> str:
        return f"LabClient({self.base_url!r}, scope={self.scope!r})"

    def _request(self, method: str, path: str, body: Any = None, *, timeout: Optional[float] = None) -> Any:
        data = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json", self._header[0]: self._header[1]}
        url = self.base_url + path
        last: Optional[LabApiError] = None
        for attempt in range(self.retries + 1):
            try:
                status, raw = self._transport(method, url, headers, data, timeout or self.timeout)
            except (OSError, http.client.HTTPException, TimeoutError, ValueError) as e:
                last = LabApiError(f"could not reach the lab API ({type(e).__name__})", code="network")
            else:
                if 200 <= status < 300:
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise LabApiError("lab API response too large", status=status, code="bad_response")
                    try:
                        return json.loads(raw.decode("utf-8")) if raw else None
                    except ValueError:
                        raise LabApiError("lab API returned invalid JSON", status=status, code="bad_response") from None
                err = _error_from_response(status, raw)
                if status not in RETRYABLE_STATUS:
                    raise err
                err.code = "retryable"
                last = err
            if attempt < self.retries:
                self._sleep(min(30, 2 ** attempt))
        raise last or LabApiError("lab API request failed", code="network")

    def check(self, body: dict) -> dict:
        return self._request("POST", "/lab/check", body)

    def evaluate(self, body: dict) -> dict:
        return self._request("POST", "/lab/evaluate", body)

    def job(self, job_id: str) -> dict:
        if not _JOB_ID_RE.fullmatch(str(job_id)):
            raise LabApiError(f"invalid job id {clean_text(job_id, 80)!r}", code="bad_request")
        return self._request("GET", f"/lab/job/{job_id}", timeout=max(self.timeout, 120))

    def wait(self, job_id: str, *, timeout: float = 900, interval: float = 3,
             on_change: Optional[Callable[[str], None]] = None) -> dict:
        """Poll until done/failed/expired. Network blips are tolerated until `timeout`."""
        t0 = self._clock()
        last_status = None
        while True:
            try:
                job = self.job(job_id)
            except LabApiError as e:
                if e.code not in ("network", "retryable"):
                    raise
                job = {"status": "unreachable"}
            status = job.get("status") if isinstance(job, dict) else None
            if status != last_status and on_change:
                on_change(str(status))
            last_status = status
            if status in TERMINAL_STATUSES:
                return job
            if self._clock() - t0 > timeout:
                raise LabApiError(f"job {job_id} still {status} after {int(timeout)}s", code="timeout")
            self._sleep(interval)

    def family(self, family: str) -> dict:
        _check_lab_id(family, "family")
        return self._request("GET", f"/lab/{family}")

    def scorecard(self, family: str, sha12: str, stage: str = "dev") -> dict:
        _check_lab_id(family, "family")
        if not _SHA12_RE.fullmatch(sha12 or ""):
            raise LabApiError("sha12 must be 12 lowercase hex characters", code="bad_request")
        if stage not in splits.SCORED_STAGES:
            raise LabApiError(f"stage must be one of {splits.SCORED_STAGES}", code="bad_request")
        return self._request("GET", f"/lab/{family}/scorecard/{sha12}?stage={stage}")

    def finalize(self, body: dict) -> dict:
        return self._request("POST", "/lab/finalize", body)


def _check_lab_id(value: str, what: str = "lab id") -> str:
    if not isinstance(value, str) or not _LAB_ID_RE.fullmatch(value):
        raise LabApiError(f"{what} must match {gates.LAB_ID_PATTERN}", code="bad_request")
    return value


def lab_token_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("USERPROFILE") or Path.home()) / ".sajim" / "lab_token"


def load_config(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, str]:
    """(api_url, lab_token). Never reads the judge token, SAJIM_API_TOKEN or sim/.env.local."""
    env = os.environ if environ is None else environ
    url = (env.get("SAJIM_API_URL") or env.get("SAJIM_MODAL_API_URL") or "").strip()
    token = (env.get("SAJIM_LAB_TOKEN") or "").strip()
    if not token:
        path = lab_token_path(env)
        try:
            token = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            token = ""
    if not url:
        raise LabApiError("SAJIM_API_URL is not set", code="not_configured")
    problem = api_url_problem(url)
    if problem:
        raise LabApiError(problem, code="not_configured")
    if not token:
        raise LabApiError("SAJIM_LAB_TOKEN is not set and ~/.sajim/lab_token is missing", code="not_configured")
    return url.rstrip("/"), token


def api_url_problem(url: str) -> Optional[str]:
    """
    None when `url` may carry a lab token: https://, or http:// to 127.0.0.1 / localhost only
    (tokens would otherwise travel in clear text). Never echoes the URL (it could hold credentials).
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port                                  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return "SAJIM_API_URL is not a valid URL"
    if parts.username is not None or parts.password is not None:
        return "SAJIM_API_URL must not contain credentials"
    if parts.scheme == "https" and host:
        return None
    if parts.scheme == "http" and host in LOCAL_HTTP_HOSTS:
        return None
    if parts.scheme == "http":
        return "SAJIM_API_URL must use https:// (http:// is allowed only for 127.0.0.1 or localhost)"
    return "SAJIM_API_URL must be an https:// URL"


def runtime_hint(reasons: Sequence[Any]) -> Optional[str]:
    """One actionable line when any reason says a sandbox run was stopped for time; else None."""
    if not any(isinstance(r, str) and _RUNTIME_REASON_RE.search(r) for r in reasons or ()):
        return None
    return (f"RUNTIME: a sandbox run was stopped for time. Every generate_signals call gets "
            f"{I.PER_RUN_TIMEOUT_S:g} s on the full frame, including about {I.CHILD_STARTUP_ALLOWANCE_S:g} s of "
            f"process start-up, and a job has at most {I.MAX_RUNS_PER_JOB} runs. Vectorise per-bar Python loops "
            f"with numpy/pandas; a slow strategy fails G0 or G7 for time, not for its results.")


# ── Lab folder files ────────────────────────────────────────────────────────
def lab_folder(lab_id: str, root: Optional[Path] = None) -> Path:
    _check_lab_id(lab_id)
    return Path(root or REPO_DIR) / I.GENERATED_DIR / lab_id


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Temp file in the same folder + os.replace, so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, obj: Any) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    write_bytes_atomic(path, text.encode("utf-8"))


def read_json(path: Path) -> Any:
    """Strict JSON: no NaN/Infinity, no duplicate keys, key order kept."""
    def pairs(items):
        d: Dict[str, Any] = {}
        for k, v in items:
            if k in d:
                raise ValueError(f"duplicate key {k!r}")
            d[k] = v
        return d

    def no_const(name):
        raise ValueError(f"{name} is not valid JSON")

    return json.loads(Path(path).read_bytes().decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=no_const)


def stamp_manifest(folder: Path) -> dict:
    """Recompute spec/code sha256 from the files and rewrite manifest.json in place (key order kept)."""
    folder = Path(folder)
    manifest = read_json(folder / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must be a JSON object")
    manifest["code_sha256"] = sha256_file(folder / "strategy.py")
    manifest["spec_sha256"] = sha256_file(folder / "spec.md")
    write_json_atomic(folder / "manifest.json", manifest)
    return manifest


def build_body(folder: Path, *, stage: Optional[str], kind: str) -> dict:
    """The Submission body for /lab/check (kind check) or /lab/evaluate (kind dev|holdout)."""
    if kind not in ("check", "dev", "holdout"):
        raise ValueError(f"kind must be check|dev|holdout, got {kind!r}")
    folder = Path(folder)
    strategy_raw = (folder / "strategy.py").read_bytes()
    spec_raw = (folder / "spec.md").read_bytes()
    manifest = read_json(folder / "manifest.json")
    lab_id = manifest.get("id")
    family = manifest.get("family") or lab_id
    _check_lab_id(lab_id)
    _check_lab_id(family, "family")
    stage = stage or (kind if kind in splits.SCORED_STAGES else splits.DEV)
    if kind in splits.SCORED_STAGES and stage != kind:
        raise ValueError(f"a {kind} request must use stage {kind}")
    code_sha = hashlib.sha256(strategy_raw).hexdigest()
    request_id = uuid.uuid4().hex if kind == "check" else I.derive_request_id(family, code_sha, kind)
    return {
        "lab_id": lab_id, "family": family, "stage": stage, "request_id": request_id,
        "strategy_py": strategy_raw.decode("utf-8"), "spec_md": spec_raw.decode("utf-8"),
        "manifest": manifest, "client": {"cli_version": CLI_VERSION},
    }


def local_problems(folder: Path, lab_id: Optional[str] = None) -> List[str]:
    """Local lint of strategy.py + manifest.json (contract.lint_path), plus the id/folder match."""
    from sajim_quant.lab import contract      # builder A's module; imported lazily

    folder = Path(folder)
    problems: List[str] = []
    for name in ("strategy.py", "manifest.json", "spec.md"):
        if not (folder / name).is_file():
            problems.append(f"{name}: missing")
    if problems:
        return problems
    for name in ("strategy.py", "manifest.json"):
        problems.extend(contract.lint_path(folder / name))
    try:
        man = read_json(folder / "manifest.json")
    except ValueError:
        man = None
    man = man if isinstance(man, dict) else {}
    if lab_id is not None:
        mid = man.get("id")
        if mid is not None and mid != lab_id:
            problems.append(f"manifest.json: id {clean_text(mid, 60)!r} must equal SAJIM_LAB_ID {lab_id!r}")
    problems.extend(_request_drift(folder, man, lab_id))
    return problems


def _request_drift(folder: Path, man: Mapping[str, Any], lab_id: Optional[str]) -> List[str]:
    """
    manifest.json must keep the runner's family, symbol and timeframe (request.json). A different
    family would claim DEV trials in a fresh budget; a different market was never requested.
    """
    try:
        req = read_json(Path(folder) / "request.json")
    except (OSError, ValueError):
        req = None
    if not isinstance(req, dict):
        want = {"family": lab_id} if lab_id else {}
    else:
        want = {"family": req.get("family") or req.get("lab_id") or lab_id,
                "symbol": req.get("symbol"), "timeframe": req.get("timeframe")}
    out = []
    for key, expected in want.items():
        got = man.get(key, man.get("id")) if key == "family" else man.get(key)
        if expected is not None and got != expected:
            out.append(f"manifest.json: {key} {clean_text(got, 60)!r} must equal {expected!r} from request.json")
    return out


# ── build_log.json ──────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_build_log(lab_id: str, family: str) -> dict:
    return {"schema": I.BUILD_LOG_SCHEMA, "lab_id": lab_id, "family": family, "final_sha": None,
            "pending": None, "events": []}


def load_build_log(folder: Path, lab_id: Optional[str] = None, family: Optional[str] = None) -> dict:
    path = Path(folder) / "build_log.json"
    if path.is_file():
        try:
            log = read_json(path)
            if isinstance(log, dict) and log.get("schema") == I.BUILD_LOG_SCHEMA:
                log.setdefault("events", [])
                log.setdefault("pending", None)
                log.setdefault("final_sha", None)
                return log
        except ValueError:
            logger.warning("build_log.json is unreadable; starting a new one")
    lid = lab_id or Path(folder).name
    return new_build_log(lid, family or lid)


def save_build_log(folder: Path, log: Mapping[str, Any]) -> None:
    write_json_atomic(Path(folder) / "build_log.json", dict(log))


def add_event(log: dict, source: str, event: str, **fields: Any) -> dict:
    entry = {"ts": _now_iso(), "source": source, "event": event}
    entry.update(fields)
    log.setdefault("events", []).append(entry)
    return entry


def record_event(folder: Path, source: str, event: str, **fields: Any) -> None:
    log = load_build_log(folder)
    add_event(log, source, event, **fields)
    save_build_log(folder, log)


# ── Scorecards on disk ──────────────────────────────────────────────────────
def scorecard_path(folder: Path, stage: str, sha12: str) -> Path:
    if stage not in splits.SCORED_STAGES or not _SHA12_RE.fullmatch(sha12 or ""):
        raise ValueError("bad scorecard stage/sha12")
    return Path(folder) / "scorecards" / f"{stage}_{sha12}.json"


def save_scorecard(folder: Path, scorecard: Mapping[str, Any]) -> Path:
    sc = dict(scorecard)
    if sc.get("stage") == splits.DEV:
        sc.pop("detail", None)                 # the builder never holds the server-only block
    path = scorecard_path(folder, str(sc.get("stage")), str(sc.get("sha12") or str(sc.get("code_sha256", ""))[:12]))
    write_json_atomic(path, sc)
    return path


def load_dev_scorecard(folder: Path, sha12: str) -> Optional[dict]:
    path = scorecard_path(folder, splits.DEV, sha12)
    if not path.is_file():
        return None
    try:
        sc = read_json(path)
    except ValueError:
        return None
    return sc if isinstance(sc, dict) else None


def newest_dev_scorecard(folder: Path) -> Optional[dict]:
    """The DEV scorecard with the latest created_at (file name breaks ties)."""
    d = Path(folder) / "scorecards"
    if not d.is_dir():
        return None
    best: Optional[Tuple[str, str, dict]] = None
    for p in d.iterdir():
        m = _DEV_SC_RE.fullmatch(p.name)
        if not m:
            continue
        sc = load_dev_scorecard(folder, m.group(1))
        if sc is None:
            continue
        key = (str(sc.get("created_at") or ""), p.name)
        if best is None or key > best[:2]:
            best = (key[0], key[1], sc)
    return best[2] if best else None


# ── Gate table ──────────────────────────────────────────────────────────────
def fmt_num(v: Any) -> str:
    """`:g`, or n/a for null; inf stays inf."""
    if v is None or isinstance(v, bool):
        return "n/a" if v is None else str(v)
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isinf(v):
            return "inf"
        if isinstance(v, float) and math.isnan(v):
            return "n/a"
        return f"{v:g}"
    return clean_text(v, 40)


def decisive_text(gate: Mapping[str, Any]) -> str:
    """'metric value op threshold' of the first failing evaluated rule, else the first evaluated rule."""
    rules = [r for r in gate.get("rules") or [] if isinstance(r, Mapping) and r.get("passed") is not None]
    pick = next((r for r in rules if r.get("passed") is False), rules[0] if rules else None)
    if pick is not None:
        return f"{pick.get('metric')} {fmt_num(pick.get('value'))} {pick.get('op')} {fmt_num(pick.get('threshold'))}"
    thr = gate.get("threshold")
    if isinstance(thr, str) and thr.count(" ") >= 2:
        metric, op, value = thr.split(" ", 2)
        return f"{metric} {fmt_num(gate.get('value'))} {op} {value}"
    return ""


def gate_rows(scorecard: Mapping[str, Any]) -> List[Tuple[str, str, str, str]]:
    """(id, name, PASS|FAIL|--, decisive) per gate, in gates.GATES order."""
    stage = scorecard.get("stage")
    by_id = {g.get("id"): g for g in scorecard.get("gates") or [] if isinstance(g, Mapping)}
    rows = []
    for spec in gates.GATES:
        g = by_id.get(spec.id)
        if spec.id == "G11" or not spec.hard:
            rows.append((spec.id, spec.name, "--", "report only"))
            continue
        if g is None:
            if spec.phase > I.LAB_PHASE:
                rows.append((spec.id, spec.name, "--", f"phase {spec.phase}"))
            elif spec.id == "G8" and stage == splits.DEV:
                rows.append((spec.id, spec.name, "--", "runner only (judge token)"))
            else:
                rows.append((spec.id, spec.name, "--", "not evaluated"))
            continue
        if not g.get("active", True):
            rows.append((spec.id, spec.name, "--", f"phase {g.get('phase', spec.phase)}"))
            continue
        passed = g.get("passed")
        if spec.id == "G8" and passed is None and stage == splits.DEV:
            rows.append((spec.id, spec.name, "--", "runner only (judge token)"))
            continue
        result = "PASS" if passed is True else "FAIL" if passed is False else "--"
        rows.append((spec.id, spec.name, result, decisive_text(g) or ("not evaluated" if passed is None else "")))
    return rows


def render_gate_table(scorecard: Mapping[str, Any], *, trial_line: bool = True) -> str:
    """The only performance view the builder gets (INTERFACES.md section 10)."""
    sc = scorecard
    label = sc.get("label") or (splits.IN_SAMPLE_LABEL if sc.get("stage") == splits.DEV else "HOLDOUT")
    sha = str(sc.get("sha12") or str(sc.get("code_sha256") or "")[:12])
    stage_word = "DEV" if sc.get("stage") == splits.DEV else str(sc.get("stage", "")).upper()
    head = f"SAJIM LAB  {sc.get('id')}  sha {sha}  {stage_word}"
    if trial_line:
        head += f" trial {fmt_num(sc.get('trial'))}/{fmt_num(sc.get('max_trials', gates.MAX_DEV_TRIALS_PER_FAMILY))}"
    lines = [f"{head}  [{label}]"]
    if trial_line:
        lines.append(f"family holdout touches {fmt_num(sc.get('holdout_touches'))}/"
                     f"{fmt_num(sc.get('max_holdout_touches', gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY))}"
                     f" | global lab trials {fmt_num(sc.get('n_trials_global'))}")
    h = sc.get("headline") or {}
    net = h.get("net")
    net_s = f"{net:+.2f}" if isinstance(net, (int, float)) and not isinstance(net, bool) and math.isfinite(net) else fmt_num(net)
    lines.append(f"trades {fmt_num(h.get('trades'))} | net {net_s} | PF {fmt_num(h.get('pf'))} | "
                 f"max DD {fmt_num(h.get('max_dd_pct'))}% | daily Sharpe {fmt_num(h.get('daily_sharpe'))}   ({label})")
    lines.append(f"{'GATE':<6}{'NAME':<26}{'RESULT':<8}DECISIVE")
    for gid, name, result, decisive in gate_rows(sc):
        lines.append(f"{gid:<6}{name:<26}{result:<8}{decisive}".rstrip())
    verdict = sc.get("verdict")
    if sc.get("final"):
        lines.append(f"VERDICT (final, {label}): {verdict}")
    elif sc.get("dev_hard_gates_passed") is True:
        lines.append(f"VERDICT (provisional, {label}): {verdict} - DEV gates passed; "
                     f"the runner runs the sealed holdout after this session")
    else:
        trial = sc.get("trial") if isinstance(sc.get("trial"), int) else 0
        left = max(0, int(sc.get("max_trials") or gates.MAX_DEV_TRIALS_PER_FAMILY) - trial)
        lines.append(f"VERDICT (provisional, {label}): {verdict} - revisions left: {left} (spec-fix or bug-fix only)")
    for reason in list(sc.get("reasons") or [])[:6]:
        lines.append(f"  - {clean_text(reason, 200)}")
    hint = runtime_hint(list(sc.get("reasons") or []))
    if hint:
        lines.append(hint)
    return "\n".join(lines)


# ── review.json ─────────────────────────────────────────────────────────────
def _strings_in(obj: Any) -> List[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, Mapping):
        return [s for k, v in obj.items() for s in _strings_in(k) + _strings_in(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings_in(v)]
    return []


def validate_review(data: Any, *, lab_id: Optional[str] = None, code_sha256: Optional[str] = None) -> List[str]:
    """Problems with a sajim.lab.review/1 object; [] when valid."""
    if not isinstance(data, Mapping):
        return ["review: must be a JSON object"]
    errs: List[str] = []
    for k in data:
        if k not in REVIEW_KEYS:
            errs.append(f"review: unknown key {clean_text(k, 40)!r}")
    for k in ("schema", "lab_id", "code_sha256", "decision", "reason"):
        if k not in data:
            errs.append(f"review: missing {k!r}")
    if "schema" in data and data["schema"] != I.REVIEW_SCHEMA:
        errs.append(f"review: schema must be {I.REVIEW_SCHEMA!r}")
    if "lab_id" in data:
        if not (isinstance(data["lab_id"], str) and _LAB_ID_RE.fullmatch(data["lab_id"])):
            errs.append("review: lab_id must be a lab id")
        elif lab_id is not None and data["lab_id"] != lab_id:
            errs.append(f"review: lab_id must be {lab_id!r}")
    if "code_sha256" in data:
        sha = data["code_sha256"]
        if not (isinstance(sha, str) and _SHA64_RE.fullmatch(sha)):
            errs.append("review: code_sha256 must be 64 lowercase hex characters")
        elif code_sha256 is not None and sha != code_sha256:
            errs.append("review: code_sha256 does not match the strategy under review")
    decision = data.get("decision")
    if "decision" in data and decision not in REVIEW_DECISIONS:
        errs.append(f"review: decision must be one of {REVIEW_DECISIONS}")
    to = data.get("to")
    if decision == "DOWNGRADE" and to not in REVIEW_DOWNGRADE_TO:
        errs.append(f"review: a DOWNGRADE needs 'to' in {REVIEW_DOWNGRADE_TO}")
    if decision == "KEEP" and to is not None:
        errs.append("review: 'to' must be null when decision is KEEP")
    for k, limit in REVIEW_LIMITS.items():
        if k in data and not (isinstance(data[k], str) and len(data[k]) <= limit):
            errs.append(f"review: {k} must be a string of at most {limit} characters")
    for k, limit in REVIEW_LIST_LIMITS.items():
        if k not in data:
            continue
        v = data[k]
        if not (isinstance(v, list) and len(v) <= limit
                and all(isinstance(x, str) and len(x) <= REVIEW_ITEM_CHARS for x in v)):
            errs.append(f"review: {k} must be a list of at most {limit} strings "
                        f"(each at most {REVIEW_ITEM_CHARS} characters)")
    if any(gates.contains_forbidden_label(s) for s in _strings_in(data)):
        errs.append(f"review: contains a forbidden label ({', '.join(gates.FORBIDDEN_LABELS)})")
    return errs


def load_review(folder: Path, *, lab_id: Optional[str] = None,
                code_sha256: Optional[str] = None) -> Tuple[Optional[dict], List[str]]:
    """(review, problems): review is None unless review.json exists and is valid."""
    path = Path(folder) / REVIEW_FILE
    if not path.is_file():
        return None, [f"{REVIEW_FILE}: missing"]
    try:
        data = read_json(path)
    except (ValueError, UnicodeDecodeError) as exc:
        return None, [f"{REVIEW_FILE}: invalid JSON ({clean_text(exc, 200)})"]
    problems = validate_review(data, lab_id=lab_id, code_sha256=code_sha256)
    return (dict(data) if not problems else None), problems


# ── Commands ────────────────────────────────────────────────────────────────
class _Ctx:
    """Per-invocation state: the lab id/folder, output, and how to build a client."""

    def __init__(self, lab_id: str, folder: Path, out: Callable[[str], None],
                 client_factory: Callable[..., LabClient]):
        self.lab_id = lab_id
        self.folder = folder
        self.out = out
        self._factory = client_factory

    def client(self, **kw: Any) -> LabClient:
        return self._factory(**kw)

    @property
    def family(self) -> str:
        try:
            fam = read_json(self.folder / "manifest.json").get("family")
        except (OSError, ValueError, AttributeError):
            fam = None
        return fam if isinstance(fam, str) and _LAB_ID_RE.fullmatch(fam) else self.lab_id


def api_exit_code(e: LabApiError) -> int:
    if e.code == "not_configured":
        return EXIT_LOCAL
    if e.code in ("network", "timeout", "retryable"):
        return EXIT_NETWORK
    if 400 <= e.status < 500:
        return EXIT_REFUSED
    return EXIT_INFRA


def _report_api_error(ctx: _Ctx, what: str, e: LabApiError) -> int:
    code = api_exit_code(e)
    if code == EXIT_REFUSED:
        ctx.out(f"{what} REFUSED: {clean_text(e, 500)}")
        for r in e.reasons:
            ctx.out(f"- {clean_text(r, 300)}")
    elif code == EXIT_NETWORK:
        ctx.out(f"{what}: {clean_text(e, 300)}. Resume with `python -X utf8 -m sajim_quant.lab.cli status`.")
    else:
        ctx.out(f"{what} ERROR: {clean_text(e, 500)}")
    return code


def _prepare(ctx: _Ctx) -> Optional[dict]:
    """Local lint + sha stamping. None (after printing) when the folder is not clean or a DEV job is pending."""
    pending = load_build_log(ctx.folder, ctx.lab_id, ctx.family).get("pending")
    if isinstance(pending, Mapping) and pending.get("kind") == I.DEV_RESULT_KIND:
        ctx.out(f"a DEV job ({clean_text(pending.get('job_id'), 80)}) is still pending; run "
                f"`python -X utf8 -m sajim_quant.lab.cli status` first (its scorecard would otherwise be lost)")
        return None
    problems = local_problems(ctx.folder, ctx.lab_id)
    if problems:
        ctx.out("LOCAL LINT FAIL")
        for p in problems:
            ctx.out(f"- {clean_text(p, 300)}")
        return None
    return stamp_manifest(ctx.folder)


def _set_pending(ctx: _Ctx, kind: str, started: Mapping[str, Any], request_id: str, sha: str, event: str,
                 **extra: Any) -> dict:
    pending = {"kind": kind, "job_id": str(started.get("job_id")), "request_id": request_id,
               "code_sha256": sha, "started_at": _now_iso()}
    log = load_build_log(ctx.folder, ctx.lab_id, ctx.family)
    log["pending"] = pending
    add_event(log, "cli", event, status="started", job_id=pending["job_id"], code_sha256=sha,
              deduplicated=bool(started.get("deduplicated")), **extra)
    save_build_log(ctx.folder, log)
    return pending


def _clear_pending(ctx: _Ctx, event: str, **fields: Any) -> None:
    log = load_build_log(ctx.folder, ctx.lab_id, ctx.family)
    log["pending"] = None
    add_event(log, "cli", event, **fields)
    save_build_log(ctx.folder, log)


def _wait(ctx: _Ctx, client: LabClient, pending: Mapping[str, Any]) -> dict:
    return client.wait(pending["job_id"], on_change=lambda s: ctx.out(f"  status: {s}"))


def _infra_failure(job: Mapping[str, Any]) -> Optional[str]:
    result = job.get("result")
    if job.get("status") != "done":
        return str(job.get("error") or (result or {}).get("error") or job.get("status"))
    if not isinstance(result, Mapping) or not result.get("success"):
        return str((result or {}).get("error") or "job reported failure")
    return None


def _finish_check(ctx: _Ctx, pending: Mapping[str, Any], job: Mapping[str, Any]) -> int:
    sha12 = str(pending.get("code_sha256", ""))[:12]
    err = _infra_failure(job)
    if err is not None:
        _clear_pending(ctx, "check", status="failed", job_id=pending.get("job_id"), error=clean_text(err, 300))
        ctx.out(f"CHECK ERROR sha {sha12}: the job failed for infrastructure reasons: {clean_text(err, 300)}")
        ctx.out("Run `check` again (checks are free).")
        return EXIT_INFRA
    result = job["result"]
    passed = result.get("passed") is True and result.get("code_sha256") in (None, pending.get("code_sha256"))
    reasons = [clean_text(r, 300) for r in (result.get("reasons") or [])][:20]
    _clear_pending(ctx, "check", status="done", job_id=pending.get("job_id"), passed=passed,
                   code_sha256=pending.get("code_sha256"))
    ctx.out(f"CHECK {'PASS' if passed else 'FAIL'} sha {sha12}")
    for r in reasons:
        ctx.out(f"- {r}")
    hint = runtime_hint(result.get("reasons") or [])
    if hint:
        ctx.out(hint)
    return EXIT_OK if passed else EXIT_FAILED


def _finish_dev(ctx: _Ctx, pending: Mapping[str, Any], job: Mapping[str, Any]) -> int:
    sha = str(pending.get("code_sha256", ""))
    err = _infra_failure(job)
    if err is not None:
        _clear_pending(ctx, "dev", status="failed", job_id=pending.get("job_id"), error=clean_text(err, 300))
        ctx.out(f"DEV ERROR sha {sha[:12]}: the job failed for infrastructure reasons: {clean_text(err, 300)}")
        ctx.out("Run `dev` again: the same request id re-runs under the same trial claim.")
        return EXIT_INFRA
    sc = job["result"].get("scorecard")
    if not isinstance(sc, Mapping) or sc.get("stage") != splits.DEV or sc.get("code_sha256") != sha:
        _clear_pending(ctx, "dev", status="failed", job_id=pending.get("job_id"), error="unexpected scorecard")
        ctx.out("DEV ERROR: the server returned an unexpected scorecard")
        return EXIT_INFRA
    sc = {k: v for k, v in sc.items() if k != "detail"}
    path = save_scorecard(ctx.folder, sc)
    passed = sc.get("dev_hard_gates_passed") is True
    _clear_pending(ctx, "dev", status="done", job_id=pending.get("job_id"), code_sha256=sha,
                   trial=sc.get("trial"), verdict=sc.get("verdict"), final=sc.get("final"),
                   dev_hard_gates_passed=passed, scorecard=path.relative_to(ctx.folder).as_posix())
    ctx.out(render_gate_table(sc))
    return EXIT_OK if passed else EXIT_FAILED


def cmd_check(ctx: _Ctx) -> int:
    manifest = _prepare(ctx)
    if manifest is None:
        return EXIT_LOCAL
    body = build_body(ctx.folder, stage=splits.DEV, kind="check")
    client = ctx.client()
    try:
        started = client.check(body)
    except LabApiError as e:
        return _report_api_error(ctx, "CHECK", e)
    sha = manifest["code_sha256"]
    ctx.out(f"check job {started.get('job_id')} sha {sha[:12]}")
    pending = _set_pending(ctx, I.CHECK_RESULT_KIND, started, body["request_id"], sha, "check")
    try:
        job = _wait(ctx, client, pending)
    except LabApiError as e:
        return _report_api_error(ctx, "CHECK", e)
    return _finish_check(ctx, pending, job)


def cmd_dev(ctx: _Ctx) -> int:
    manifest = _prepare(ctx)
    if manifest is None:
        return EXIT_LOCAL
    body = build_body(ctx.folder, stage=splits.DEV, kind="dev")
    client = ctx.client()
    try:
        started = client.evaluate(body)
    except LabApiError as e:
        return _report_api_error(ctx, "DEV", e)
    sha = manifest["code_sha256"]
    ctx.out(f"DEV trial {fmt_num(started.get('trial'))}/{gates.MAX_DEV_TRIALS_PER_FAMILY} "
            f"job {started.get('job_id')} sha {sha[:12]} (IN-SAMPLE)")
    pending = _set_pending(ctx, I.DEV_RESULT_KIND, started, body["request_id"], sha, "dev",
                           trial=started.get("trial"))
    try:
        job = _wait(ctx, client, pending)
    except LabApiError as e:
        return _report_api_error(ctx, "DEV", e)
    return _finish_dev(ctx, pending, job)


def resume_pending(ctx: _Ctx) -> int:
    """Finish build_log.pending exactly as the original command would."""
    log = load_build_log(ctx.folder, ctx.lab_id, ctx.family)
    pending = log.get("pending")
    if not pending:
        ctx.out("no pending lab job")
        return EXIT_OK
    kind = pending.get("kind")
    if kind not in (I.CHECK_RESULT_KIND, I.DEV_RESULT_KIND):
        ctx.out(f"unknown pending job kind {clean_text(kind, 40)!r}")
        return EXIT_LOCAL
    what = "CHECK" if kind == I.CHECK_RESULT_KIND else "DEV"
    client = ctx.client()
    ctx.out(f"resuming {what} job {pending.get('job_id')}")
    try:
        job = _wait(ctx, client, pending)
    except LabApiError as e:
        if e.status == I.HTTP_NOT_FOUND:
            _clear_pending(ctx, "check" if what == "CHECK" else "dev", status="expired", job_id=pending.get("job_id"))
            ctx.out(f"{what}: the job is unknown or expired. Run `{what.lower()}` again "
                    f"(the same request id does not use a new trial).")
            return EXIT_INFRA
        return _report_api_error(ctx, what, e)
    return _finish_check(ctx, pending, job) if what == "CHECK" else _finish_dev(ctx, pending, job)


def cmd_summary(ctx: _Ctx, sha12: Optional[str]) -> int:
    if sha12 is not None and not _SHA12_RE.fullmatch(sha12):
        ctx.out("--sha must be 12 lowercase hex characters")
        return EXIT_LOCAL
    sc = load_dev_scorecard(ctx.folder, sha12) if sha12 else newest_dev_scorecard(ctx.folder)
    if sc is None:
        ctx.out("no DEV scorecard yet" + (f" for sha {sha12}" if sha12 else "") + " (run `dev` first)")
        return EXIT_LOCAL
    ctx.out(render_gate_table(sc))
    try:
        fam = ctx.client(retries=1).family(ctx.family)
        ctx.out(f"server counts: DEV trials {fmt_num(fam.get('n_trials'))}/"
                f"{fmt_num(fam.get('max_trials', gates.MAX_DEV_TRIALS_PER_FAMILY))} | holdout touches "
                f"{fmt_num(fam.get('holdout_touches'))}/"
                f"{fmt_num(fam.get('max_holdout_touches', gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY))} | "
                f"global lab trials {fmt_num(fam.get('n_trials_global'))}")
    except LabApiError:
        ctx.out("server counts unavailable (offline); the counts above are from the local scorecard")
    return EXIT_OK


def cmd_done(ctx: _Ctx, final: str) -> int:
    final = (final or "").strip().lower()
    if not _FINAL_RE.fullmatch(final):
        ctx.out("--final must be a 12- or 64-character lowercase hex sha")
        return EXIT_LOCAL
    problems = local_problems(ctx.folder, ctx.lab_id)
    if problems:
        ctx.out("DONE REFUSED: the lab folder does not lint clean")
        for p in problems:
            ctx.out(f"- {clean_text(p, 300)}")
        return EXIT_LOCAL
    sha = sha256_file(ctx.folder / "strategy.py")
    if not sha.startswith(final):
        ctx.out(f"DONE REFUSED: strategy.py is sha {sha[:12]}, not {final[:12]}")
        return EXIT_LOCAL
    stamped = read_json(ctx.folder / "manifest.json").get("code_sha256")
    if stamped != sha:
        ctx.out("DONE REFUSED: strategy.py changed after the last `check`/`dev` (manifest code_sha256 is stale)")
        return EXIT_LOCAL
    log = load_build_log(ctx.folder, ctx.lab_id, ctx.family)
    log["final_sha"] = sha
    add_event(log, "cli", "done", final_sha=sha)
    save_build_log(ctx.folder, log)
    ctx.out(f"DONE final sha {sha[:12]}")
    if load_dev_scorecard(ctx.folder, sha[:12]) is None:
        ctx.out("note: this sha has no DEV scorecard; the runner judges the newest DEV-trialled sha")
    return EXIT_OK


def cmd_review(ctx: _Ctx, file: str) -> int:
    if file != REVIEW_FILE:
        ctx.out(f"--file must be {REVIEW_FILE} (in the lab folder)")
        return EXIT_LOCAL
    sha = sha256_file(ctx.folder / "strategy.py") if (ctx.folder / "strategy.py").is_file() else None
    review, problems = load_review(ctx.folder, lab_id=ctx.lab_id, code_sha256=sha)
    if problems:
        ctx.out("REVIEW INVALID")
        for p in problems:
            ctx.out(f"- {clean_text(p, 300)}")
        record_event(ctx.folder, "cli", "critic", valid=False)
        return EXIT_LOCAL
    record_event(ctx.folder, "cli", "critic", valid=True, decision=review["decision"], to=review.get("to"))
    ctx.out("REVIEW OK")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sajim_quant.lab.cli", description="SAJIM Strategy Lab CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="free integrity check on Modal (no performance numbers)")
    sub.add_parser("dev", help="one DEV trial (counts against the family's budget)")
    sub.add_parser("status", help="resume a pending check/dev job")
    s = sub.add_parser("summary", help="the IN-SAMPLE gate table of the newest (or given) DEV scorecard")
    s.add_argument("--sha", default=None)
    d = sub.add_parser("done", help="declare the final sha and end the build")
    d.add_argument("--final", required=True)
    r = sub.add_parser("review", help="validate the critic's review.json")
    r.add_argument("--file", required=True)
    return ap


def _default_factory(environ: Mapping[str, str]) -> Callable[..., LabClient]:
    def make(**kw: Any) -> LabClient:
        url, token = load_config(environ)
        return LabClient(url, lab_token=token, **kw)
    return make


def main(argv: Optional[Sequence[str]] = None, *, environ: Optional[Mapping[str, str]] = None,
         root: Optional[Path] = None, client_factory: Optional[Callable[..., LabClient]] = None,
         out: Optional[Callable[[str], None]] = None) -> int:
    """Entry point; the keyword arguments exist for tests (fakes instead of HTTP)."""
    env = os.environ if environ is None else environ
    say = out or (lambda msg: print(msg, flush=True))
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return EXIT_LOCAL if e.code else EXIT_OK
    lab_id = (env.get("SAJIM_LAB_ID") or "").strip()
    if not _LAB_ID_RE.fullmatch(lab_id):
        say("SAJIM_LAB_ID is not set (or is not a lab id); lab.cli runs inside a lab build")
        return EXIT_LOCAL
    folder = lab_folder(lab_id, root)
    if not folder.is_dir():
        say(f"lab folder {I.GENERATED_DIR}/{lab_id}/ does not exist")
        return EXIT_LOCAL
    ctx = _Ctx(lab_id, folder, say, client_factory or _default_factory(env))
    try:
        if args.cmd == "check":
            return cmd_check(ctx)
        if args.cmd == "dev":
            return cmd_dev(ctx)
        if args.cmd == "status":
            return resume_pending(ctx)
        if args.cmd == "summary":
            return cmd_summary(ctx, args.sha)
        if args.cmd == "done":
            return cmd_done(ctx, args.final)
        if args.cmd == "review":
            return cmd_review(ctx, args.file)
    except LabApiError as e:
        return _report_api_error(ctx, args.cmd.upper(), e)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        say(f"error: {clean_text(e, 300)}")
        return EXIT_LOCAL
    return EXIT_LOCAL


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
