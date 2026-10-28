"""
SAJIM Strategy Lab — trusted sandbox I/O (INTERFACES.md §2.2, §2.5).

Encodes one SandboxJob into the five input files the sandbox receives, and decodes
what comes back. Everything read from a sandbox is UNTRUSTED bytes. The decoder
never unpickles, never evals and never trusts a header it has not checked:

  * events: ``.npy`` only, header parsed with numpy's literal-only header reader,
    dtype must equal interfaces.EVENTS_DTYPE exactly, no trailing bytes, loaded
    with ``allow_pickle=False``, then every value validated (bar range and order,
    exit bytes, signal values, stop/target distances, short permission);
  * status: ``json.loads`` with NaN/Infinity rejected, schema-checked, messages
    truncated and reduced to printable ASCII before they reach the builder.

``decode_result`` turns any bad untrusted output into RunError entries; it never
raises on sandbox data.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I

logger = logging.getLogger(__name__)

FRAME_COLUMNS: Tuple[str, ...] = ("open", "high", "low", "close", "volume")
MAX_HEADER_SIZE = 10_000
MAX_TIMINGS = 64
MAX_VERSION_CHARS = 64
#: Most events one run may emit: what fits in one MAX_EVENTS_FILE_BYTES .npy (4 KiB kept for
#: the header). Below MAX_FRAME_BARS, so a dense run fails as the runner's `too_many_events`.
MAX_EVENTS_PER_RUN = (I.MAX_EVENTS_FILE_BYTES - 4096) // I.EVENTS_DTYPE.itemsize
_ENV_NAME_RE = re.compile(r"^[A-Z0-9_]{1,64}$")
_RUN_ID_RE = re.compile(I.RUN_ID_PATTERN)
_HERE = Path(__file__).resolve().parent


class SandboxDecodeError(ValueError):
    """Untrusted sandbox output failed validation. kind is "decode" or "status_invalid"."""

    def __init__(self, message: str, kind: str = "decode"):
        super().__init__(message)
        self.kind = kind


# ── Encoding (trusted inputs) ───────────────────────────────────────────────
def frame_to_parquet(frame: pd.DataFrame) -> bytes:
    """
    The stage frame as parquet bytes: columns exactly time, open, high, low, close,
    volume; time is timestamp[us] without tz; OHLCV float64; rows ascending, unique.
    """
    idx = frame.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError("frame must have a DatetimeIndex")
    if idx.tz is not None:
        raise ValueError("frame index must be timezone-naive UTC")
    if not idx.is_monotonic_increasing or not idx.is_unique:
        raise ValueError("frame index must be strictly ascending")
    missing = [c for c in FRAME_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"frame is missing column(s) {missing}")
    if len(frame) < 1 or len(frame) > I.MAX_FRAME_BARS:
        raise ValueError(f"frame must have 1..{I.MAX_FRAME_BARS} rows")
    arrays = [pa.array(np.asarray(idx.values).astype("datetime64[us]"), type=pa.timestamp("us"))]
    arrays += [pa.array(frame[c].to_numpy(dtype=np.float64), type=pa.float64()) for c in FRAME_COLUMNS]
    table = pa.Table.from_arrays(arrays, names=["time", *FRAME_COLUMNS])
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _json_param(name: str, value: Any) -> Any:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"param {name!r} must be int or float, not bool")
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        if not math.isfinite(v):
            raise ValueError(f"param {name!r} must be finite")
        return v
    raise ValueError(f"param {name!r} must be int or float, got {type(value).__name__}")


def job_budget_s(job: I.SandboxJob) -> float:
    """Seconds after which the runner starts no more runs (job_timeout_s - margin)."""
    return max(0.0, float(job.job_timeout_s) - I.RUNNER_BUDGET_MARGIN_S)


def plan_json(job: I.SandboxJob) -> bytes:
    """plan.json bytes (interfaces.PLAN_SCHEMA); ints stay JSON ints, floats JSON floats."""
    runs = []
    for r in job.runs:
        if r.cut is not None and r.cut > job.n_bars:
            raise ValueError(f"run {r.run_id}: cut {r.cut} > n_bars {job.n_bars}")
        runs.append({
            "run_id": r.run_id,
            "params": {str(k): _json_param(str(k), v) for k, v in r.params.items()},
            "cut": None if r.cut is None else int(r.cut),
            "scramble": None if r.scramble is None else {"start": int(r.scramble.start),
                                                         "seed": int(r.scramble.seed)},
        })
    plan = {
        "schema": I.PLAN_SCHEMA, "n_bars": int(job.n_bars), "allow_short": bool(job.allow_short),
        "per_run_timeout_s": float(job.per_run_timeout_s), "job_budget_s": job_budget_s(job),
        "max_events_per_run": min(int(job.n_bars), MAX_EVENTS_PER_RUN), "runs": runs,
    }
    return json.dumps(plan, allow_nan=False, indent=1).encode("utf-8")


def runner_source() -> bytes:
    """Exact bytes of the trusted sandbox_runner.py next to this module."""
    return (_HERE / I.IN_RUNNER).read_bytes()


def indicators_source() -> bytes:
    """Exact bytes of the trusted sajim_quant/lab/indicators.py (read lazily)."""
    return (_HERE / I.IN_INDICATORS).read_bytes()


def encode_job(job: I.SandboxJob) -> Dict[str, bytes]:
    """The five input files, keyed by their path relative to the sandbox root."""
    return {
        f"{I.IN_DIR}/{I.IN_FRAME}": job.frame_parquet,
        f"{I.IN_DIR}/{I.IN_STRATEGY}": job.strategy_py,
        f"{I.IN_DIR}/{I.IN_INDICATORS}": job.indicators_py,
        f"{I.IN_DIR}/{I.IN_PLAN}": plan_json(job),
        f"{I.IN_DIR}/{I.IN_RUNNER}": runner_source(),
    }


def expected_output_names(job: I.SandboxJob) -> List[str]:
    """
    The only files an executor reads back, as names relative to ``<root>/out``.
    SandboxResult.files uses these names as keys.
    """
    return [I.OUT_STATUS] + [I.events_file(r.run_id) for r in job.runs]


def output_limit(name: str) -> int:
    """Byte limit for one expected output file (executors read at most limit + 1 bytes)."""
    return I.MAX_STATUS_BYTES if name == I.OUT_STATUS else I.MAX_EVENTS_FILE_BYTES


# ── Decoding (untrusted outputs) ────────────────────────────────────────────
def _dtype_matches(dt: np.dtype) -> bool:
    exp = I.EVENTS_DTYPE
    return (dt == exp and dt.names == exp.names and dt.fields == exp.fields
            and dt.itemsize == exp.itemsize and not dt.hasobject)


def decode_events(raw: bytes, n_bars: int, *, allow_short: bool) -> np.ndarray:
    """Strictly decode one ``<run_id>.events.npy``; raises SandboxDecodeError on any violation."""
    if not isinstance(raw, (bytes, bytearray)):
        raise SandboxDecodeError("events file is not bytes")
    if len(raw) > I.MAX_EVENTS_FILE_BYTES:
        raise SandboxDecodeError(f"events file larger than {I.MAX_EVENTS_FILE_BYTES} bytes")
    fh = io.BytesIO(bytes(raw))
    fmt = np.lib.format
    try:
        version = fmt.read_magic(fh)
        if version == (1, 0):
            shape, fortran, dtype = fmt.read_array_header_1_0(fh, max_header_size=MAX_HEADER_SIZE)
        elif version == (2, 0):
            shape, fortran, dtype = fmt.read_array_header_2_0(fh, max_header_size=MAX_HEADER_SIZE)
        else:
            raise SandboxDecodeError(f"unsupported .npy version {version}")
    except SandboxDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parse failure is a decode failure
        raise SandboxDecodeError(f"bad .npy header: {type(exc).__name__}") from None
    if not isinstance(dtype, np.dtype) or not _dtype_matches(dtype):
        raise SandboxDecodeError(f"events dtype must be exactly {I.EVENTS_DTYPE.descr}")
    if fortran is not False:
        raise SandboxDecodeError("events array must not be fortran-ordered")
    if not (isinstance(shape, tuple) and len(shape) == 1 and isinstance(shape[0], int)):
        raise SandboxDecodeError(f"events shape must be (k,), got {shape!r}")
    k = shape[0]
    if k < 0 or k > n_bars:
        raise SandboxDecodeError(f"{k} events for a {n_bars}-bar frame")
    header_len = fh.tell()
    if header_len + k * I.EVENTS_DTYPE.itemsize != len(raw):
        raise SandboxDecodeError("events file size does not match its header (trailing or missing bytes)")
    try:
        arr = np.load(io.BytesIO(bytes(raw)), allow_pickle=False)
    except Exception as exc:  # noqa: BLE001
        raise SandboxDecodeError(f"events file failed to load: {type(exc).__name__}") from None
    arr = np.array(arr, dtype=I.EVENTS_DTYPE, copy=True)
    if arr.shape != (k,):
        raise SandboxDecodeError("events shape changed on load")
    _validate_events(arr, n_bars, allow_short=allow_short)
    sig0 = arr["signal"] == 0
    arr["stop_dist"][sig0] = np.nan
    arr["tp_dist"][sig0] = np.nan
    return arr


def _validate_events(arr: np.ndarray, n_bars: int, *, allow_short: bool) -> None:
    if len(arr) == 0:
        return
    exit_bytes = np.ascontiguousarray(arr["exit"]).view(np.uint8)
    if np.any(exit_bytes > 1):
        raise SandboxDecodeError("exit field holds a byte other than 0 or 1")
    bar = arr["bar"]
    if bar.min() < 0 or bar.max() >= n_bars:
        raise SandboxDecodeError(f"event bar outside [0, {n_bars})")
    if np.any(np.diff(bar) <= 0):
        raise SandboxDecodeError("event bars must be strictly increasing")
    sig = arr["signal"]
    if not np.all(np.isin(sig, (-1, 0, 1))):
        raise SandboxDecodeError("signal must be -1, 0 or 1")
    nz = sig != 0
    if np.any(~nz & ~arr["exit"]):
        raise SandboxDecodeError("an event has neither a signal nor an exit")
    stop, tp = arr["stop_dist"], arr["tp_dist"]
    with np.errstate(invalid="ignore"):
        if np.any(nz & ~(np.isfinite(stop) & (stop > 0))):
            raise SandboxDecodeError("stop_dist must be finite and > 0 where signal != 0")
        if np.any(nz & ~(np.isnan(tp) | (np.isfinite(tp) & (tp > 0)))):
            raise SandboxDecodeError("tp_dist must be NaN or finite and > 0 where signal != 0")
    if not allow_short and np.any(sig == -1):
        raise SandboxDecodeError("short signal but allow_short is false")


def sanitize_message(text: Any, limit: int = I.MAX_ERROR_MESSAGE_CHARS) -> str:
    """Untrusted text -> at most `limit` chars of printable ASCII plus newline and tab."""
    s = str(text)[:limit]
    return "".join(ch if (" " <= ch <= "~" or ch in "\n\t") else "?" for ch in s)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


def _finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def decode_status(raw: bytes) -> Dict[str, Any]:
    """Parse and schema-check status.json; returns a normalised copy. Raises SandboxDecodeError."""
    def bad(msg: str) -> SandboxDecodeError:
        return SandboxDecodeError(f"status.json invalid: {msg}", kind="status_invalid")

    if not isinstance(raw, (bytes, bytearray)):
        raise bad("not bytes")
    if len(raw) > I.MAX_STATUS_BYTES:
        raise bad(f"larger than {I.MAX_STATUS_BYTES} bytes")
    try:
        doc = json.loads(bytes(raw).decode("utf-8"), parse_constant=_reject_constant)
    except Exception as exc:  # noqa: BLE001
        raise bad(f"not strict JSON ({type(exc).__name__})") from None
    if not isinstance(doc, dict) or doc.get("schema") != I.STATUS_SCHEMA:
        raise bad("wrong schema")

    errors_in = doc.get("errors", [])
    if not isinstance(errors_in, list) or len(errors_in) > I.MAX_ERRORS:
        raise bad(f"errors must be a list of at most {I.MAX_ERRORS} entries")
    errors = []
    for e in errors_in:
        if not isinstance(e, dict):
            raise bad("error entry is not an object")
        rid = e.get("run_id")
        if rid is not None and not (isinstance(rid, str) and _RUN_ID_RE.fullmatch(rid)):
            raise bad("error entry has a bad run_id")
        kind = e.get("kind")
        if kind not in I.ERROR_KINDS:
            kind = "internal"
        errors.append({"run_id": rid, "kind": kind, "message": sanitize_message(e.get("message", ""))})

    runs: Dict[str, Dict[str, Any]] = {}
    runs_in = doc.get("runs", {})
    if isinstance(runs_in, dict):
        for rid, info in list(runs_in.items())[:I.MAX_RUNS_PER_JOB]:
            if isinstance(rid, str) and _RUN_ID_RE.fullmatch(rid) and isinstance(info, dict):
                runs[rid] = {
                    "ok": info.get("ok") is True,
                    "n_events": int(info["n_events"]) if _finite_number(info.get("n_events")) else 0,
                    "seconds": float(info["seconds"]) if _finite_number(info.get("seconds")) else 0.0,
                }

    timings: Dict[str, float] = {}
    t_in = doc.get("timings", {})
    if isinstance(t_in, dict):
        for k, v in list(t_in.items())[:MAX_TIMINGS]:
            if isinstance(k, str) and (k == "_total" or _RUN_ID_RE.fullmatch(k)) and _finite_number(v) and v >= 0:
                timings[k] = float(v)

    versions: Dict[str, str] = {}
    v_in = doc.get("versions", {})
    if isinstance(v_in, dict):
        for k in ("python", "numpy", "pandas", "pyarrow"):
            if isinstance(v_in.get(k), str):
                versions[k] = sanitize_message(v_in[k], MAX_VERSION_CHARS)

    names_in = doc.get("sensitive_env_names", [])
    if not isinstance(names_in, list) or len(names_in) > 64:
        raise bad("sensitive_env_names must be a list of at most 64 names")
    for n in names_in:
        if not (isinstance(n, str) and _ENV_NAME_RE.fullmatch(n)):
            raise bad("sensitive_env_names holds a malformed name")

    return {"schema": I.STATUS_SCHEMA, "ok": doc.get("ok") is True, "runs": runs, "errors": errors,
            "timings": timings, "versions": versions, "sensitive_env_names": list(names_in)}


def decode_result(job: I.SandboxJob, result: I.SandboxResult) -> I.DecodedJob:
    """
    Trusted view of one sandbox job. Never raises on bad untrusted data: every problem
    becomes a RunError (run_id None = job-level). status.ok is advisory and ignored.
    """
    errors: List[I.RunError] = []
    files = result.files or {}
    if result.stderr_tail:
        logger.debug("sandbox %s stderr tail (untrusted): %r", job.label, result.stderr_tail[-500:])
    if result.timed_out:
        errors.append(I.RunError(None, "timeout", f"sandbox job exceeded {job.job_timeout_s:g} s and was killed"))

    status: Optional[Dict[str, Any]] = None
    raw_status = files.get(I.OUT_STATUS)
    if raw_status is None:
        if not result.timed_out and result.exit_code not in (0, None):
            errors.append(I.RunError(None, "sandbox", f"sandbox runner exited with code {result.exit_code} "
                                                      f"without writing status.json"))
        else:
            errors.append(I.RunError(None, "status_invalid", "status.json missing"))
    else:
        try:
            status = decode_status(raw_status)
        except SandboxDecodeError as exc:
            errors.append(I.RunError(None, "status_invalid", sanitize_message(exc)))
        if not result.timed_out and result.exit_code not in (0, None):
            errors.append(I.RunError(None, "sandbox", f"sandbox runner exited with code {result.exit_code}"))

    plan_ids = {r.run_id for r in job.runs}
    failed_runs = set()
    if status is not None:
        for e in status["errors"]:
            rid = e["run_id"]
            if rid is not None and rid not in plan_ids:
                errors.append(I.RunError(None, "internal", f"error reported for unknown run {rid}: {e['message']}"))
                continue
            errors.append(I.RunError(rid, e["kind"], e["message"]))
            if rid is not None:
                failed_runs.add(rid)

    events: Dict[str, np.ndarray] = {}
    total = len(raw_status) if isinstance(raw_status, (bytes, bytearray)) else 0
    for run in job.runs:
        if run.run_id in failed_runs:
            continue
        raw = files.get(I.events_file(run.run_id))
        if raw is None:
            errors.append(I.RunError(run.run_id, "missing_output", "the run produced no events file"))
            continue
        total += len(raw)
        if total > I.MAX_TOTAL_OUT_BYTES:
            errors.append(I.RunError(run.run_id, "decode", f"total sandbox output exceeds "
                                                           f"{I.MAX_TOTAL_OUT_BYTES} bytes"))
            continue
        try:
            events[run.run_id] = decode_events(raw, run.n_bars(job.n_bars), allow_short=job.allow_short)
        except SandboxDecodeError as exc:
            errors.append(I.RunError(run.run_id, "decode", sanitize_message(exc)))

    return I.DecodedJob(
        events=events,
        errors=tuple(errors[: I.MAX_ERRORS + 8]),
        timings=dict(status["timings"]) if status else {},
        versions=dict(status["versions"]) if status else {},
        sensitive_env_names=tuple(status["sensitive_env_names"]) if status else (),
    )


# ── Comparison ──────────────────────────────────────────────────────────────
def _float_eq(a: np.ndarray, b: np.ndarray, atol: float) -> np.ndarray:
    an, bn = np.isnan(a), np.isnan(b)
    with np.errstate(invalid="ignore"):
        close = (a == b) | (np.abs(a - b) <= atol)
    return (an & bn) | (~an & ~bn & close)


def events_equal(a: np.ndarray, b: np.ndarray, *, atol: float = gates.CAUSALITY_ATOL) -> Tuple[bool, Optional[int]]:
    """
    Compare two validated event arrays: bar/signal/exit exactly, stop/tp NaN-aware within
    atol. Returns (equal, first differing bar or None). The first differing bar is the
    smallest bar that is present in only one array or whose fields differ.
    """
    ab, bb = a["bar"], b["bar"]
    only = np.setxor1d(ab, bb, assume_unique=True)
    common, ia, ib = np.intersect1d(ab, bb, assume_unique=True, return_indices=True)
    x, y = a[ia], b[ib]
    same = ((x["signal"] == y["signal"]) & (x["exit"] == y["exit"])
            & _float_eq(x["stop_dist"], y["stop_dist"], atol) & _float_eq(x["tp_dist"], y["tp_dist"], atol))
    diffs = []
    if len(only):
        diffs.append(int(only.min()))
    if not same.all():
        diffs.append(int(common[~same].min()))
    if not diffs:
        return True, None
    return False, min(diffs)
