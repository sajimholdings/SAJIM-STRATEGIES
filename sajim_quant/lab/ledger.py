"""
SAJIM Strategy Lab — the trial ledger and budget slots (INTERFACES.md section 7).

P0 findings this module encodes:
  * the ledger is IMMUTABLE PER-RECORD FILES, never appends to a shared file
    (concurrent jsonl appends lost 4 of 5 lines): lab/<family>/ledger/<utc_ts>_<request_id>_<record_type>.json;
  * budgets are SLOT CLAIMS, first-writer-wins (LocalDirStore: O_CREAT|O_EXCL; ModalStore:
    Dict.put(skip_if_exists=True)), mirrored by a budget_claim record;
  * a slot counts as taken when EITHER the claim OR a budget_claim record exists, so a
    Modal Dict entry expiring after 7 idle days cannot reset a family's budget;
  * the trial is claimed and recorded BEFORE the job is spawned (G1 note).

The store is a dumb byte store (interfaces.LedgerStore); LocalDirStore is the tests/dev
implementation. The integrator's ModalStore lives in store_modal.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sajim_quant.lab import gates, splits
from sajim_quant.lab.interfaces import (
    ACTORS, LEDGER_SCHEMA, RECORD_TYPES, REQUEST_ID_RE, BudgetExhausted, BudgetInfo, HoldoutAlreadyRun,
    LedgerStore, holdout_sha_key, holdout_touch_key, ledger_filename, sha12, slot_filename, trial_key,
    utc_stamp,
)

logger = logging.getLogger(__name__)

LAB_ROOT = "lab"
SLOTS_DIR = f"{LAB_ROOT}/_slots"
_FAMILY_RE = re.compile(gates.LAB_ID_PATTERN)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CLAIM_KINDS = ("trial", "holdout_sha", "holdout_touch")


# ── Store ───────────────────────────────────────────────────────────────────
def _check_relpath(relpath: str) -> PurePosixPath:
    p = PurePosixPath(relpath)
    if (not relpath or "\\" in relpath or p.is_absolute() or ":" in relpath
            or any(part in ("", ".", "..") for part in relpath.split("/"))):
        raise ValueError(f"bad store path {relpath!r}")
    return p


def _is_protected(relpath: str) -> bool:
    # Case-folded and stripped of trailing dots/spaces: NTFS treats "LEDGER", "ledger." and
    # "ledger " as the same directory, so a plain comparison could be bypassed on Windows.
    parts = [p.rstrip(". ").casefold() for p in relpath.split("/")]
    return len(parts) >= 3 and parts[0] == LAB_ROOT and (parts[1] == "_slots" or parts[2] == "ledger")


class LocalDirStore:
    """LedgerStore on a local directory (tests and local dev). Write-once via O_CREAT|O_EXCL."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, relpath: str) -> Path:
        return self.root.joinpath(*_check_relpath(relpath).parts)

    def write_once(self, relpath: str, data: bytes) -> bool:
        path = self._path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as fh:
            fh.write(bytes(data))
            fh.flush()
            os.fsync(fh.fileno())
        return True

    def write(self, relpath: str, data: bytes) -> None:
        if _is_protected(relpath):
            raise ValueError(f"{relpath!r} is write-once (ledger records / slot claims)")
        path = self._path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(bytes(data))
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def read(self, relpath: str) -> Optional[bytes]:
        try:
            return self._path(relpath).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None

    def listdir(self, reldir: str) -> List[str]:
        path = self.root if reldir in ("", ".") else self._path(reldir)
        try:
            return sorted(p.name for p in path.iterdir() if not p.name.startswith(".tmp_"))
        except (FileNotFoundError, NotADirectoryError):
            return []

    def claim_slot(self, key: str, value: bytes) -> bool:
        return self.write_once(f"{SLOTS_DIR}/{slot_filename(key)}", value)

    def read_slot(self, key: str) -> Optional[bytes]:
        return self.read(f"{SLOTS_DIR}/{slot_filename(key)}")


# ── Records ─────────────────────────────────────────────────────────────────
def _now(now: Optional[datetime]) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def make_record(record_type: str, *, family: str, lab_id: str, request_id: str, actor: str, payload: Mapping,
                code_sha256: Optional[str] = None, spec_sha256: Optional[str] = None,
                stage: Optional[str] = None, deploy_id: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """A validated sajim.lab.ledger/1 record (not yet written)."""
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown record_type {record_type!r}")
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    for name, v in (("family", family), ("lab_id", lab_id)):
        if not _FAMILY_RE.fullmatch(v or ""):
            raise ValueError(f"{name} {v!r} must match {gates.LAB_ID_PATTERN}")
    if not REQUEST_ID_RE.fullmatch(request_id or ""):
        raise ValueError(f"bad request_id {request_id!r}")
    for name, v in (("code_sha256", code_sha256), ("spec_sha256", spec_sha256)):
        if v is not None and not _SHA_RE.fullmatch(v):
            raise ValueError(f"{name} must be 64 lowercase hex chars or None")
    if stage is not None:
        stage = splits.normalize_stage(stage)
        if stage not in splits.SCORED_STAGES:
            raise ValueError(f"stage must be one of {splits.SCORED_STAGES} or None")
    return {
        "schema": LEDGER_SCHEMA, "record_type": record_type, "family": family, "id": lab_id,
        "request_id": request_id, "actor": actor, "code_sha256": code_sha256, "spec_sha256": spec_sha256,
        "stage": stage, "created_at": _now(now).isoformat(timespec="microseconds"), "deploy_id": deploy_id,
        "gates_version": gates.GATES_VERSION, "protocol_version": splits.PROTOCOL_VERSION,
        "payload": json.loads(json.dumps(dict(payload), allow_nan=False)),
    }


def record_relpath(record: Mapping[str, Any]) -> str:
    stamp = utc_stamp(datetime.fromisoformat(record["created_at"]))
    name = ledger_filename(stamp, record["request_id"], record["record_type"])
    return f"{LAB_ROOT}/{record['family']}/ledger/{name}"


def put_record(store: LedgerStore, record: Mapping[str, Any]) -> str:
    """Write one immutable record; an existing file at that path is kept and its path returned."""
    if record.get("schema") != LEDGER_SCHEMA:
        raise ValueError("not a sajim.lab.ledger/1 record")
    rel = record_relpath(record)
    data = json.dumps(record, indent=2, sort_keys=False, allow_nan=False).encode("utf-8")
    if not store.write_once(rel, data):
        existing = store.read(rel)
        if existing is not None and existing != data:
            # Records are immutable: the first file wins. Different bytes at the same path mean two
            # records collided on <utc_ts>_<request_id>_<record_type> (that once hid a lost claim).
            logger.warning("ledger record %s already exists with DIFFERENT content; the existing file "
                           "was kept and this record was NOT written", rel)
        else:
            logger.info("ledger record %s already exists; kept", rel)
    return rel


def _load(store: LedgerStore, rel: str) -> Optional[Dict[str, Any]]:
    raw = store.read(rel)
    if raw is None:
        return None
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        logger.warning("unreadable ledger record %s skipped", rel)
        return None
    if not isinstance(rec, dict) or rec.get("schema") != LEDGER_SCHEMA:
        logger.warning("ledger record %s has the wrong schema; skipped", rel)
        return None
    return rec


def list_records(store: LedgerStore, family: str, *, record_type: Optional[str] = None,
                 code_sha256: Optional[str] = None, stage: Optional[str] = None,
                 request_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """A family's records, sorted by file name (= time), optionally filtered."""
    if not _FAMILY_RE.fullmatch(family or ""):
        raise ValueError(f"family {family!r} must match {gates.LAB_ID_PATTERN}")
    out = []
    for name in store.listdir(f"{LAB_ROOT}/{family}/ledger"):
        if not name.endswith(".json"):
            continue
        rec = _load(store, f"{LAB_ROOT}/{family}/ledger/{name}")
        if rec is None or rec.get("family") != family:
            continue
        if record_type is not None and rec.get("record_type") != record_type:
            continue
        if code_sha256 is not None and rec.get("code_sha256") != code_sha256:
            continue
        if stage is not None and rec.get("stage") != stage:
            continue
        if request_id is not None and rec.get("request_id") != request_id:
            continue
        out.append(rec)
    return out


def list_families(store: LedgerStore) -> List[str]:
    return [n for n in store.listdir(LAB_ROOT) if _FAMILY_RE.fullmatch(n)]


# ── Budget slots ────────────────────────────────────────────────────────────
def _claims_with_key(store: LedgerStore, key: str) -> List[Dict[str, Any]]:
    parts = key.split(":")
    families = [parts[1]] if parts[0] in ("trial", "holdout_touch") else list_families(store)
    out = []
    for fam in families:
        out += [r for r in list_records(store, fam, record_type="budget_claim")
                if (r.get("payload") or {}).get("key") == key]
    return out


def is_slot_taken(store: LedgerStore, key: str) -> bool:
    """Taken if the slot claim exists OR a budget_claim record names it (the ledger is the truth)."""
    if store.read_slot(key) is not None:
        return True
    return bool(_claims_with_key(store, key))


def _slot_owner(store: LedgerStore, key: str) -> Optional[str]:
    """request_id that claimed `key`, from the claim value or its budget_claim record."""
    raw = store.read_slot(key)
    if raw is not None:
        try:
            rid = json.loads(raw.decode("utf-8")).get("request_id")
            if isinstance(rid, str):
                return rid
        except (UnicodeDecodeError, ValueError, AttributeError):
            pass
    recs = _claims_with_key(store, key)
    return recs[0].get("request_id") if recs else None


def count_dev_trials(store: LedgerStore, family: str) -> int:
    return sum(1 for n in range(1, gates.MAX_DEV_TRIALS_PER_FAMILY + 1)
               if is_slot_taken(store, trial_key(family, n)))


def count_holdout_touches(store: LedgerStore, family: str) -> int:
    return sum(1 for n in range(1, gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY + 1)
               if is_slot_taken(store, holdout_touch_key(family, n)))


def count_global_trials(store: LedgerStore) -> int:
    return sum(count_dev_trials(store, f) for f in list_families(store))


def find_claim(store: LedgerStore, family: str, request_id: str, *,
               kinds: Sequence[str] = ("trial", "holdout_touch")) -> Optional[Dict[str, Any]]:
    """The budget_claim record this request already made (dedup), or None."""
    for rec in list_records(store, family, record_type="budget_claim", request_id=request_id):
        if (rec.get("payload") or {}).get("kind") in kinds:
            return rec
    return None


def _slot_value(key: str, family: str, lab_id: str, request_id: str, code_sha256: str,
                now: datetime) -> bytes:
    return json.dumps({"key": key, "family": family, "id": lab_id, "request_id": request_id,
                       "code_sha256": code_sha256, "claimed_at": now.isoformat()}).encode("utf-8")


def _claim_record(store: LedgerStore, *, key: str, kind: str, slot: Optional[int], family: str, lab_id: str,
                  request_id: str, code_sha256: str, stage: str, actor: str, now: datetime,
                  ignored_client_fields: Sequence[str] = ()) -> str:
    rec = make_record("budget_claim", family=family, lab_id=lab_id, request_id=request_id, actor=actor,
                      code_sha256=code_sha256, stage=stage, now=now,
                      payload={"key": key, "kind": kind, "slot": slot, "job_id": None,
                               "ignored_client_fields": list(ignored_client_fields)})
    return put_record(store, rec)


def claim_dev_trial(store: LedgerStore, family: str, *, lab_id: str, request_id: str, code_sha256: str,
                    actor: str = "api", now: Optional[datetime] = None,
                    ignored_client_fields: Sequence[str] = ()) -> Optional[int]:
    """
    First-writer-wins DEV trial slot. Returns the trial number (1..MAX_DEV_TRIALS_PER_FAMILY),
    the same number again for a retried request_id (no new trial), or None when the budget
    is used up (the API answers 429). Claimed and recorded before any job is spawned.
    """
    prior = find_claim(store, family, request_id, kinds=("trial",))
    if prior is not None:
        return int(prior["payload"]["slot"])
    t = _now(now)
    for n in range(1, gates.MAX_DEV_TRIALS_PER_FAMILY + 1):
        key = trial_key(family, n)
        if is_slot_taken(store, key):
            if _slot_owner(store, key) != request_id:
                continue
            got = True                                  # this request claimed it, then crashed
        else:
            got = store.claim_slot(key, _slot_value(key, family, lab_id, request_id, code_sha256, t))
        if got:
            _claim_record(store, key=key, kind="trial", slot=n, family=family, lab_id=lab_id,
                          request_id=request_id, code_sha256=code_sha256, stage=splits.DEV, actor=actor,
                          now=t, ignored_client_fields=ignored_client_fields)
            logger.info("DEV trial %d/%d claimed for %s (%s)", n, gates.MAX_DEV_TRIALS_PER_FAMILY,
                        family, sha12(code_sha256))
            return n
    logger.info("DEV trial budget exhausted for %s", family)
    return None


def require_dev_trial(store: LedgerStore, family: str, *, lab_id: str, request_id: str, code_sha256: str,
                      actor: str = "api", now: Optional[datetime] = None,
                      ignored_client_fields: Sequence[str] = ()) -> int:
    """claim_dev_trial, raising BudgetExhausted (HTTP 429, never retried) instead of returning None."""
    n = claim_dev_trial(store, family, lab_id=lab_id, request_id=request_id, code_sha256=code_sha256,
                        actor=actor, now=now, ignored_client_fields=ignored_client_fields)
    if n is None:
        raise BudgetExhausted(f"family {family} has used all {gates.MAX_DEV_TRIALS_PER_FAMILY} DEV trials")
    return n


def claim_holdout(store: LedgerStore, family: str, code_sha256: str, *, lab_id: str, request_id: str,
                  actor: str = "api", now: Optional[datetime] = None,
                  ignored_client_fields: Sequence[str] = ()) -> int:
    """
    Claim the once-per-sha holdout and a family touch. Returns the touch number.
    Raises BudgetExhausted(code="holdout_burned") (429) or HoldoutAlreadyRun (409).
    A retried request_id gets its existing touch back (dedup; an infra failure re-runs
    under the same claim).
    """
    prior = find_claim(store, family, request_id, kinds=("holdout_touch",))
    if prior is not None:
        return int(prior["payload"]["slot"])
    # A touch slot this request claimed before crashing is recovered below, even when it was the last one.
    owns_touch = any(_slot_owner(store, holdout_touch_key(family, n)) == request_id
                     for n in range(1, gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY + 1))
    if not owns_touch and count_holdout_touches(store, family) >= gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY:
        raise BudgetExhausted(f"family {family} has used all {gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY} "
                              f"holdout touches", code="holdout_burned")
    t = _now(now)
    # The sha and touch budget_claim records share record_type and request_id; a distinct stamp keeps
    # their ledger file names apart (same name = the touch record was silently dropped).
    t_touch = t + timedelta(microseconds=1)
    sha_key = holdout_sha_key(code_sha256)
    mine = _slot_owner(store, sha_key) == request_id
    if not mine:
        if is_slot_taken(store, sha_key) or not store.claim_slot(
                sha_key, _slot_value(sha_key, family, lab_id, request_id, code_sha256, t)):
            raise HoldoutAlreadyRun(f"the holdout already ran for sha {sha12(code_sha256)}")
    if not mine or not _claims_with_key(store, sha_key):     # recovered claim: its record may be missing
        _claim_record(store, key=sha_key, kind="holdout_sha", slot=None, family=family, lab_id=lab_id,
                      request_id=request_id, code_sha256=code_sha256, stage=splits.HOLDOUT, actor=actor,
                      now=t, ignored_client_fields=ignored_client_fields)
    for n in range(1, gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY + 1):
        key = holdout_touch_key(family, n)
        if is_slot_taken(store, key):
            if _slot_owner(store, key) != request_id:
                continue
            got = True                                  # this request claimed it, then crashed
        else:
            got = store.claim_slot(key, _slot_value(key, family, lab_id, request_id, code_sha256, t))
        if got:
            _claim_record(store, key=key, kind="holdout_touch", slot=n, family=family, lab_id=lab_id,
                          request_id=request_id, code_sha256=code_sha256, stage=splits.HOLDOUT, actor=actor,
                          now=t_touch, ignored_client_fields=ignored_client_fields)
            logger.info("holdout touch %d/%d claimed for %s (%s)", n, gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY,
                        family, sha12(code_sha256))
            return n
    raise BudgetExhausted(f"family {family} has used all {gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY} "
                          f"holdout touches", code="holdout_burned")


def budget_info(store: LedgerStore, family: str, *, trial: Optional[int] = None) -> BudgetInfo:
    """Counts stamped into a scorecard. budget_exhausted = this trial is the last allowed one."""
    n_family = count_dev_trials(store, family)
    touches = count_holdout_touches(store, family)
    exhausted = (trial if trial is not None else n_family) >= gates.MAX_DEV_TRIALS_PER_FAMILY
    return BudgetInfo(trial=trial, n_trials_family=n_family, n_trials_global=count_global_trials(store),
                      holdout_touches=touches, budget_exhausted=bool(exhausted),
                      holdout_burned=touches >= gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY)


# ── DEV-pass lookups (the holdout precondition) ─────────────────────────────
def dev_scorecard_relpath(family: str, code_sha256: str) -> str:
    return f"{LAB_ROOT}/{family}/{sha12(code_sha256)}/dev_scorecard.json"


def dev_pass_record(store: LedgerStore, family: str, code_sha256: str) -> Optional[Dict[str, Any]]:
    """The newest dev_trial record for this exact sha with every hard DEV gate passed, or None."""
    recs = [r for r in list_records(store, family, record_type="dev_trial", code_sha256=code_sha256)
            if (r.get("payload") or {}).get("status") == "done"
            and (r.get("payload") or {}).get("dev_hard_gates_passed") is True]
    return recs[-1] if recs else None


def load_dev_scorecard(store: LedgerStore, family: str, code_sha256: str) -> Optional[Dict[str, Any]]:
    raw = store.read(dev_scorecard_relpath(family, code_sha256))
    if raw is None:
        return None
    try:
        sc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return sc if isinstance(sc, dict) and sc.get("code_sha256") == code_sha256 else None
