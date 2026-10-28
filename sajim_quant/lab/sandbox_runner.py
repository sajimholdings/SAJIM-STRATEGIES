"""
SAJIM Strategy Lab — the script that runs INSIDE the sandbox (INTERFACES.md §2.3).

Untrusted side. This file is copied byte-for-byte into ``<root>/in/sandbox_runner.py``
and started as ``python -I -X utf8 <root>/in/sandbox_runner.py <root>/in <root>/out``.
It may import only the standard library, numpy, pandas and pyarrow: there is no
``sajim_quant`` package in the sandbox image, so nothing here imports it.

Parent mode (``<in_dir> <out_dir>``):
  1. read ``plan.json``, load ``frame.parquet`` into memory, check the row count,
     then DELETE ``frame.parquet`` before any strategy code runs;
  2. run every plan entry in list order, each in a FRESH child process that sees only
     its own frame (``work/<run_id>/frame.parquet``: the cut prefix, the scrambled
     frame or the full frame), with a per-run timeout and an overall job budget;
  3. always write ``out/status.json`` last.

Child mode (``--child <run_id> <in_dir> <out_dir> <frame_path>``):
  installs the vendored indicators as ``sajim_quant.lab.indicators``, execs
  ``strategy.py`` into a fresh module whose builtins carry an import guard, calls
  ``generate_signals``, validates the dense output and writes sparse events
  (``out/<run_id>.events.npy``) or ``out/<run_id>.error.json``.

Nothing written here is trusted: the trusted side re-validates every byte with
``sajim_quant.lab.sandbox_io``. The import guard and the removed builtins are
hygiene against ACCIDENTAL misuse by a model; the security boundary is the sandbox
itself (no network, no secrets, no volumes).
"""

from __future__ import annotations

import builtins
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
import traceback
import types
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ── Frozen layout and limits (mirrors interfaces.py; this file cannot import it) ─
PLAN_SCHEMA = "sajim.lab.sandbox_plan/1"
STATUS_SCHEMA = "sajim.lab.sandbox_status/1"
IN_FRAME = "frame.parquet"
IN_STRATEGY = "strategy.py"
IN_INDICATORS = "indicators.py"
IN_PLAN = "plan.json"
OUT_STATUS = "status.json"
WORK_DIR = "work"
FRAME_COLUMNS: Tuple[str, ...] = ("open", "high", "low", "close", "volume")
EVENTS_DTYPE = np.dtype([
    ("bar", "<i8"), ("signal", "i1"), ("stop_dist", "<f8"), ("tp_dist", "<f8"), ("exit", "?"),
])
RUN_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
MAX_ERROR_MESSAGE_CHARS = 2000
MAX_RUNS_PER_JOB = 16
MAX_EVENTS_FILE_BYTES = 8 * 1024 * 1024
#: interfaces.RUNNER_BUDGET_MARGIN_S: the executor kills the job at job_budget_s + this.
RUNNER_BUDGET_MARGIN_S = 45
#: Seconds of that margin kept free so status.json is always written before the kill.
STATUS_RESERVE_S = 15
SENSITIVE_ENV_RE = re.compile(r"^(SAJIM|MODAL|ANTHROPIC|CLAUDE)_")
RUNNER_ERROR_KINDS: Tuple[str, ...] = (
    "import", "params", "exception", "timeout", "crash", "schema", "index", "dtype",
    "value", "too_many_events", "budget", "internal",
)

STRATEGY_MODULE = "lab_strategy"
#: Top-level modules strategy.py may import (CONTRACT.md §6; __future__ is inert).
ALLOWED_IMPORT_TOPS = frozenset({"numpy", "pandas", "math", "typing", "__future__"})
#: The vendored indicators and its two empty parent packages.
ALLOWED_LAB_IMPORTS = {"sajim_quant": {"lab"}, "sajim_quant.lab": {"indicators"},
                       "sajim_quant.lab.indicators": set()}
#: numpy/pandas sub-packages that reach compilers, ctypes or test runners.
BLOCKED_SUBMODULES: Tuple[str, ...] = ("numpy.f2py", "numpy.distutils", "numpy.ctypeslib",
                                       "numpy.testing", "pandas.testing", "pandas.util._tester")
#: Builtins removed from strategy.py's namespace (lint bans them too).
REMOVED_BUILTINS: Tuple[str, ...] = ("open", "exec", "eval", "compile", "breakpoint", "input")

_REAL_IMPORT = builtins.__import__


class RunFailure(Exception):
    """A per-run failure with an ERROR_KINDS kind (reported, never fatal to the job)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# ── Frozen future-scramble algorithm (INTERFACES.md §5.2) ───────────────────
def scramble_frame(df: pd.DataFrame, start: int, seed: int) -> pd.DataFrame:
    """
    Rows [start, n) replaced by a seeded random walk built from the frame's own bars
    in shuffled order; rows before `start` and the index are unchanged. Frozen:

        rng = default_rng(seed); src = rng.permutation(arange(start, n))
        prev = close[src-1]; r_x = x[src]/prev for x in o/h/l/c
        new_close = close[start-1]*cumprod(r_c); new_prev = [close[start-1], new_close[:-1]]
        o/h/l/c[start:] = new_prev*r_x; volume[start:] = volume[src]
    """
    if start < 1:
        raise ValueError("scramble start must be >= 1")
    n = len(df)
    out = df.copy()
    if start >= n:
        return out
    rng = np.random.default_rng(seed)
    src = rng.permutation(np.arange(start, n))
    o, h, l, c = (df[k].to_numpy(dtype=np.float64) for k in ("open", "high", "low", "close"))
    v = df["volume"].to_numpy(dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = c[src - 1]
        r_o, r_h, r_l, r_c = o[src] / prev, h[src] / prev, l[src] / prev, c[src] / prev
        new_close = c[start - 1] * np.cumprod(r_c)
        new_prev = np.concatenate([[c[start - 1]], new_close[:-1]])
        for name, orig, ratio in (("open", o, r_o), ("high", h, r_h), ("low", l, r_l), ("close", c, r_c)):
            col = orig.copy()
            col[start:] = new_prev * ratio
            out[name] = col
    vol = v.copy()
    vol[start:] = v[src]
    out["volume"] = vol
    return out


# ── Frame I/O (same format as sandbox_io.frame_to_parquet) ──────────────────
def read_frame(path: Path) -> pd.DataFrame:
    """frame.parquet -> DataFrame with a naive datetime64[ns] index named `time`, float64 OHLCV."""
    table = pq.read_table(str(path))
    names = list(table.column_names)
    if names != ["time", *FRAME_COLUMNS]:
        raise ValueError(f"frame columns must be time,{','.join(FRAME_COLUMNS)}; got {names}")
    ttype = table.schema.field("time").type
    if not pa.types.is_timestamp(ttype) or ttype.tz is not None:
        raise ValueError(f"frame time column must be a naive timestamp, got {ttype}")
    times = table.column("time").to_numpy().astype("datetime64[ns]")
    index = pd.DatetimeIndex(times, name="time")
    data = {c: np.asarray(table.column(c).to_numpy(), dtype=np.float64) for c in FRAME_COLUMNS}
    return pd.DataFrame(data, index=index)


def write_frame(df: pd.DataFrame, path: Path) -> None:
    """DataFrame (time index) -> parquet with `time` as timestamp[us] and float64 OHLCV."""
    arrays = [pa.array(np.asarray(df.index.values).astype("datetime64[us]"), type=pa.timestamp("us"))]
    arrays += [pa.array(df[c].to_numpy(dtype=np.float64), type=pa.float64()) for c in FRAME_COLUMNS]
    table = pa.Table.from_arrays(arrays, names=["time", *FRAME_COLUMNS])
    pq.write_table(table, str(path))


# ── Import guard and the strategy module ────────────────────────────────────
def _import_allowed(name: str, fromlist: Any) -> Optional[str]:
    """None if `import name` (with fromlist) is allowed for strategy.py, else the reason."""
    if name in ALLOWED_LAB_IMPORTS:
        allowed_from = ALLOWED_LAB_IMPORTS[name]
        bad = [f for f in (fromlist or ()) if f not in allowed_from]
        return f"'from {name} import {bad[0]}'" if bad else None
    if name.split(".", 1)[0] not in ALLOWED_IMPORT_TOPS:
        return f"'{name}'"
    if any(name == b or name.startswith(b + ".") for b in BLOCKED_SUBMODULES):
        return f"'{name}'"
    if name.split(".", 1)[0] in ("numpy", "pandas"):
        for f in fromlist or ():
            full = f"{name}.{f}"
            if any(full == b or full.startswith(b + ".") for b in BLOCKED_SUBMODULES):
                return f"'{full}'"
    return None


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    """The `__import__` seen by strategy.py: numpy, pandas, math, typing and the indicators only."""
    if level != 0:
        raise ImportError("relative imports are not allowed in strategy.py")
    why = _import_allowed(str(name), fromlist)
    if why is not None:
        raise ImportError(f"import of {why} is blocked in the lab sandbox (allowed: numpy, pandas, "
                          f"math, typing, sajim_quant.lab.indicators)")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


def strategy_builtins() -> Dict[str, Any]:
    """A copy of builtins with the import guard installed and I/O / dynamic-code builtins removed."""
    b = dict(builtins.__dict__)
    for name in REMOVED_BUILTINS:
        b.pop(name, None)
    b["__import__"] = guarded_import
    return b


def install_indicators(source: bytes) -> types.ModuleType:
    """Register the vendored, trusted indicators as `sajim_quant.lab.indicators`."""
    pkg = types.ModuleType("sajim_quant")
    lab = types.ModuleType("sajim_quant.lab")
    pkg.__path__ = []  # type: ignore[attr-defined]
    lab.__path__ = []  # type: ignore[attr-defined]
    ind = types.ModuleType("sajim_quant.lab.indicators")
    ind.__file__ = IN_INDICATORS
    sys.modules["sajim_quant"] = pkg
    sys.modules["sajim_quant.lab"] = lab
    sys.modules["sajim_quant.lab.indicators"] = ind
    pkg.lab = lab  # type: ignore[attr-defined]
    lab.indicators = ind  # type: ignore[attr-defined]
    exec(compile(source, IN_INDICATORS, "exec", dont_inherit=True), ind.__dict__)
    return ind


def load_strategy(source: bytes) -> types.ModuleType:
    """Exec strategy.py into a fresh module `lab_strategy` with guarded builtins."""
    mod = types.ModuleType(STRATEGY_MODULE)
    mod.__file__ = IN_STRATEGY
    mod.__dict__["__builtins__"] = strategy_builtins()
    sys.modules[STRATEGY_MODULE] = mod
    try:
        code = compile(source, IN_STRATEGY, "exec", dont_inherit=True)
        exec(code, mod.__dict__)
    except BaseException as exc:  # noqa: BLE001 - every failure is reported, never raised
        raise RunFailure("import", format_exception(exc)) from None
    params = mod.__dict__.get("PARAMS")
    if not isinstance(params, dict):
        raise RunFailure("params", "strategy.py must define PARAMS as a dict literal")
    if not callable(mod.__dict__.get("generate_signals")):
        raise RunFailure("import", "strategy.py defines no callable generate_signals(df, params)")
    return mod


def format_exception(exc: BaseException) -> str:
    """'<Type>: <msg> (strategy.py line N)' + the traceback tail, <= MAX_ERROR_MESSAGE_CHARS."""
    tb = traceback.extract_tb(exc.__traceback__)
    line = None
    for fr in tb:
        if fr.filename == IN_STRATEGY:
            line = fr.lineno
    if isinstance(exc, SyntaxError) and exc.filename == IN_STRATEGY:
        line = exc.lineno
    summary = f"{type(exc).__name__}: {exc}"
    if line is not None:
        summary += f" ({IN_STRATEGY} line {line})"
    summary = summary.replace("\r", " ")[:500]
    full = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    room = MAX_ERROR_MESSAGE_CHARS - len(summary) - 1
    return summary + ("\n" + full[-room:] if room > 0 else "")


# ── Dense output -> sparse events ───────────────────────────────────────────
def _real_numeric(s: pd.Series) -> bool:
    t = pd.api.types
    return (t.is_numeric_dtype(s.dtype) and not t.is_bool_dtype(s.dtype)
            and not t.is_complex_dtype(s.dtype))


def _first_bar(mask: np.ndarray) -> int:
    return int(np.flatnonzero(mask)[0])


def dense_to_events(out: Any, index: pd.Index, *, allow_short: bool, max_events: int) -> np.ndarray:
    """Validate generate_signals' DataFrame (INTERFACES.md §2.3 step 4) and return sparse events."""
    t = pd.api.types
    if not isinstance(out, pd.DataFrame):
        raise RunFailure("schema", f"generate_signals must return a pandas DataFrame, got {type(out).__name__}")
    if not out.index.equals(index):
        raise RunFailure("index", "returned index differs from the input index "
                                  "(same length, order and timestamps required)")
    cols = list(out.columns)
    for c in ("signal", "stop_dist", "tp_dist", "exit"):
        if cols.count(c) > 1:
            raise RunFailure("schema", f"duplicate column {c!r}")
    missing = [c for c in ("signal", "stop_dist", "tp_dist") if c not in cols]
    if missing:
        raise RunFailure("schema", f"missing required column(s): {', '.join(missing)}")

    sig_s = out["signal"]
    if not t.is_integer_dtype(sig_s.dtype) or t.is_bool_dtype(sig_s.dtype):
        raise RunFailure("dtype", f"signal must be an integer dtype, got {sig_s.dtype}; use .astype('int8')")
    if bool(sig_s.isna().any()):
        raise RunFailure("value", f"signal is NA on bar {_first_bar(sig_s.isna().to_numpy())}")
    raw_sig = sig_s.to_numpy()
    bad = ~np.isin(raw_sig, (-1, 0, 1))
    if bad.any():
        raise RunFailure("value", f"signal must be -1, 0 or 1; bar {_first_bar(bad)} has {raw_sig[bad][0]}")
    sig = raw_sig.astype(np.int8)

    floats = {}
    for c in ("stop_dist", "tp_dist"):
        s = out[c]
        if not _real_numeric(s):
            raise RunFailure("dtype", f"{c} must be a real numeric dtype, got {s.dtype}")
        floats[c] = s.to_numpy(dtype=np.float64, na_value=np.nan)
    stop, tp = floats["stop_dist"], floats["tp_dist"]

    if "exit" in cols:
        ex_s = out["exit"]
        if not t.is_bool_dtype(ex_s.dtype) or bool(ex_s.isna().any()):
            raise RunFailure("dtype", f"exit must be a bool dtype without NA, got {ex_s.dtype}")
        ex = ex_s.to_numpy(dtype=bool)
    else:
        ex = np.zeros(len(sig), dtype=bool)

    nz = sig != 0
    if not allow_short and (sig == -1).any():
        raise RunFailure("value", f"short signal (-1) on bar {_first_bar(sig == -1)} "
                                  f"but the manifest has allow_short=false")
    bad = nz & ~(np.isfinite(stop) & (stop > 0))
    if bad.any():
        i = _first_bar(bad)
        raise RunFailure("value", f"stop_dist must be finite and > 0 where signal != 0; bar {i} has {stop[i]!r}")
    bad = nz & ~(np.isnan(tp) | (np.isfinite(tp) & (tp > 0)))
    if bad.any():
        i = _first_bar(bad)
        raise RunFailure("value", f"tp_dist must be NaN or finite and > 0 where signal != 0; bar {i} has {tp[i]!r}")

    keep = np.flatnonzero(nz | ex)
    if len(keep) > max_events:
        raise RunFailure("too_many_events", f"{len(keep)} events exceed the limit of {max_events}")
    ev = np.zeros(len(keep), dtype=EVENTS_DTYPE)
    ev["bar"] = keep
    ev["signal"] = sig[keep]
    ev["stop_dist"] = np.where(nz[keep], stop[keep], np.nan)
    ev["tp_dist"] = np.where(nz[keep], tp[keep], np.nan)
    ev["exit"] = ex[keep]
    return ev


# ── Child: one generate_signals call ────────────────────────────────────────
def _write_json(path: Path, obj: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def child_main(run_id: str, in_dir: Path, out_dir: Path, frame_path: Path) -> int:
    """Run one plan entry; always leaves either <run_id>.events.npy or <run_id>.error.json."""
    if not RUN_ID_RE.fullmatch(run_id):
        return 2
    err_path = out_dir / f"{run_id}.error.json"
    try:
        plan = json.loads((in_dir / IN_PLAN).read_text(encoding="utf-8"))
        run = next(r for r in plan["runs"] if r["run_id"] == run_id)
        df = read_frame(frame_path)
        install_indicators((in_dir / IN_INDICATORS).read_bytes())
        strategy_src = (in_dir / IN_STRATEGY).read_bytes()
    except BaseException as exc:  # noqa: BLE001
        _write_json(err_path, {"kind": "internal", "message": f"runner setup failed: {format_exception(exc)}"})
        return 0
    try:
        mod = load_strategy(strategy_src)
        try:
            out = mod.generate_signals(df.copy(), dict(run["params"]))
        except BaseException as exc:  # noqa: BLE001 - SystemExit/KeyboardInterrupt included
            raise RunFailure("exception", format_exception(exc)) from None
        events = dense_to_events(out, df.index, allow_short=bool(plan["allow_short"]),
                                 max_events=int(plan["max_events_per_run"]))
        tmp = out_dir / f"{run_id}.events.tmp.npy"
        np.save(tmp, events, allow_pickle=False)
        os.replace(tmp, out_dir / f"{run_id}.events.npy")
    except RunFailure as f:
        _write_json(err_path, {"kind": f.kind, "message": f.message[-MAX_ERROR_MESSAGE_CHARS:]})
    except BaseException as exc:  # noqa: BLE001
        _write_json(err_path, {"kind": "internal", "message": format_exception(exc)})
    return 0


# ── Parent: the whole job ───────────────────────────────────────────────────
class _JobFailure(Exception):
    pass


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _JobFailure(f"plan.json unreadable: {exc}") from None
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise _JobFailure("plan.json has the wrong schema")
    runs = plan.get("runs")
    if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_RUNS_PER_JOB:
        raise _JobFailure("plan.json runs must be a list of 1..16 entries")
    seen = set()
    for r in runs:
        rid = r.get("run_id") if isinstance(r, dict) else None
        if not isinstance(rid, str) or not RUN_ID_RE.fullmatch(rid) or rid in seen:
            raise _JobFailure(f"plan.json has a bad or duplicate run_id {rid!r}")
        seen.add(rid)
        if not isinstance(r.get("params"), dict):
            raise _JobFailure(f"plan.json run {rid} has no params object")
    for key in ("n_bars", "per_run_timeout_s", "job_budget_s", "max_events_per_run"):
        if not isinstance(plan.get(key), (int, float)) or isinstance(plan.get(key), bool):
            raise _JobFailure(f"plan.json {key} must be a number")
    return plan


def _run_frame(df: pd.DataFrame, run: Mapping[str, Any]) -> pd.DataFrame:
    cut, scr = run.get("cut"), run.get("scramble")
    if cut is not None:
        return df.iloc[: int(cut)]
    if scr is not None:
        return scramble_frame(df, int(scr["start"]), int(scr["seed"]))
    return df


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name != "nt":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _remove(path: Path) -> None:
    for _ in range(20):
        try:
            if path.is_dir():
                for child in path.iterdir():
                    _remove(child)
                path.rmdir()
            elif path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.05)


PROC_ROOT = Path("/proc")
STRAY_KILL_SWEEPS = 3


def _proc_pids() -> set:
    """PIDs visible in /proc (empty off Linux)."""
    try:
        return {int(n) for n in os.listdir(PROC_ROOT) if n.isdigit()}
    except OSError:
        return set()


def _ancestry(pid: int) -> set:
    """pid and its parent chain up to init."""
    chain = set()
    while pid > 0 and pid not in chain:
        chain.add(pid)
        try:
            stat = (PROC_ROOT / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return chain


def _kill_strays(keep: set) -> int:
    """
    SIGKILL every process that is not in `keep` (the PIDs alive before any strategy code ran)
    or this runner's own ancestry. A strategy child can detach a process (setsid/double fork)
    that outlives it; left alive, it could read a later run's frame or rewrite other runs'
    outputs and defeat the causality check. Linux only (the Modal sandbox); a no-op elsewhere.
    """
    if os.name == "nt" or not PROC_ROOT.is_dir():
        return 0
    import signal
    sent = 0
    for _ in range(STRAY_KILL_SWEEPS):
        spare = keep | _ancestry(os.getpid())
        strays = _proc_pids() - spare
        if not strays:
            break
        for pid in strays:
            try:
                os.kill(pid, getattr(signal, "SIGKILL", 9))
                sent += 1
            except OSError:
                pass
        time.sleep(0.05)
    return sent


def _ascii_message(text: Any) -> str:
    """
    First MAX_ERROR_MESSAGE_CHARS chars as printable ASCII (+ newline/tab), others -> '?'.
    The trusted side applies the same reduction; doing it here keeps status.json well under
    MAX_STATUS_BYTES (json.dumps writes an astral char as a 12-byte escape).
    """
    s = str(text)[:MAX_ERROR_MESSAGE_CHARS]
    return "".join(ch if (" " <= ch <= "~" or ch in "\n\t") else "?" for ch in s)


def _tail(path: Path, n: int = 1500) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _read_bounded(path: Path, limit: int) -> Optional[bytes]:
    """File bytes, or None if missing or over `limit` bytes (never reads more than limit + 1)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    return None if len(data) > limit else data


def _run_child(run: Mapping[str, Any], frame: pd.DataFrame, in_dir: Path, out_dir: Path,
               timeout_s: float, *, budget_clipped: bool = False,
               collected: Optional[Dict[str, bytes]] = None,
               keep: Optional[set] = None) -> Tuple[Optional[Tuple[str, str]], int]:
    """
    Spawn one child; returns ((kind, message) | None, n_events). budget_clipped: timeout_s was
    cut below the per-run limit by the job budget, so a kill is kind `budget`, not `timeout`.

    The child writes into its own work dir, never into out_dir. After it exits, every stray
    process is killed (keep = PIDs to spare) BEFORE its outputs are read and before the next
    run's frame is written; the events bytes go into `collected` (parent memory), and
    parent_main writes out_dir only once all runs are done. A detached process therefore can
    neither see a later frame nor rewrite another run's events.
    """
    run_id = run["run_id"]
    rdir = in_dir.parent / WORK_DIR / run_id
    _remove(rdir)
    rdir.mkdir(parents=True)
    child_out = rdir / "out"
    child_out.mkdir()
    frame_path = rdir / IN_FRAME
    events_path = child_out / f"{run_id}.events.npy"
    err_path = child_out / f"{run_id}.error.json"
    write_frame(frame, frame_path)
    argv = [sys.executable, "-I", "-X", "utf8", os.path.abspath(__file__), "--child", run_id,
            str(in_dir), str(child_out), str(frame_path)]
    stderr_path = rdir / "stderr.txt"
    timed_out = False
    events_present = False
    events_bytes: Optional[bytes] = None
    err_bytes: Optional[bytes] = None
    try:
        with open(stderr_path, "wb") as err_fh:
            proc = subprocess.Popen(argv, cwd=str(rdir), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=err_fh,
                                    start_new_session=(os.name != "nt"))
            try:
                code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill(proc)
                code = proc.returncode
        if keep is not None:
            _kill_strays(keep)
        stderr_tail = _tail(stderr_path)
        events_present = events_path.exists()
        if events_present:
            events_bytes = _read_bounded(events_path, MAX_EVENTS_FILE_BYTES)
        if err_path.exists():
            err_bytes = _read_bounded(err_path, 65536) or b"{}"
    finally:
        _remove(frame_path)
        _remove(rdir)

    failure: Optional[Tuple[str, str]] = None
    if timed_out and budget_clipped:
        failure = ("budget", f"stopped after {timeout_s:g} s because the job time budget ran out "
                             f"(earlier runs used it); the run was killed")
    elif timed_out:
        failure = ("timeout", f"generate_signals did not finish within the per-run limit of {timeout_s:g} s "
                              f"(process start-up included); the run was killed")
    elif err_bytes is not None:
        try:
            info = json.loads(err_bytes.decode("utf-8"))
            kind = info.get("kind") if info.get("kind") in RUNNER_ERROR_KINDS else "internal"
            failure = (kind, str(info.get("message", ""))[-MAX_ERROR_MESSAGE_CHARS:])
        except Exception:  # noqa: BLE001
            failure = ("crash", "the run wrote an unreadable error file")
    elif code != 0:
        failure = ("crash", f"the run process exited with code {code}: {stderr_tail.strip()[-1500:]}")
    elif not events_present:
        failure = ("crash", "the run process exited without writing output")
    elif events_bytes is None:
        failure = ("too_many_events", f"the events file is larger than {MAX_EVENTS_FILE_BYTES} bytes")
    if failure is not None:
        return failure, 0
    try:
        n_events = int(np.load(io.BytesIO(events_bytes), allow_pickle=False).shape[0])
    except Exception:  # noqa: BLE001 - the trusted decoder will reject it
        n_events = 0
    if collected is not None:
        collected[run_id] = events_bytes
    else:                                   # direct callers (tests) get the file in out_dir
        (out_dir / f"{run_id}.events.npy").write_bytes(events_bytes)
    return None, n_events


def parent_main(in_dir: Path, out_dir: Path) -> int:
    """Run the plan; status.json is written in all cases."""
    t0 = time.monotonic()
    status: Dict[str, Any] = {
        "schema": STATUS_SCHEMA, "ok": False, "runs": {}, "errors": [], "timings": {},
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "pandas": pd.__version__, "pyarrow": pa.__version__},
        "sensitive_env_names": sorted(k for k in os.environ if SENSITIVE_ENV_RE.match(k))[:64],
    }
    errors: List[Dict[str, Any]] = status["errors"]
    keep = _proc_pids() | _ancestry(os.getpid())    # alive before any strategy code runs
    collected: Dict[str, bytes] = {}
    try:
        frame_file = in_dir / IN_FRAME
        try:
            plan = _load_plan(in_dir / IN_PLAN)
            df = read_frame(frame_file)
            if len(df) != int(plan["n_bars"]):
                raise _JobFailure(f"frame has {len(df)} rows but the plan says n_bars={plan['n_bars']}")
        except _JobFailure as exc:
            errors.append({"run_id": None, "kind": "internal", "message": str(exc)})
            return 0
        except Exception as exc:  # noqa: BLE001
            errors.append({"run_id": None, "kind": "internal",
                           "message": f"frame unreadable: {type(exc).__name__}: {exc}"[:MAX_ERROR_MESSAGE_CHARS]})
            return 0
        finally:
            _remove(frame_file)          # the full frame never sits on disk while strategy code runs
        if frame_file.exists():          # fail closed: never run strategy code next to the full frame
            errors.append({"run_id": None, "kind": "internal",
                           "message": f"could not delete in/{IN_FRAME}; no strategy code was run"})
            return 0

        budget = float(plan["job_budget_s"])
        per_run = float(plan["per_run_timeout_s"])
        hard_stop = budget + RUNNER_BUDGET_MARGIN_S - STATUS_RESERVE_S
        for run in plan["runs"]:
            run_id = run["run_id"]
            elapsed = time.monotonic() - t0
            if elapsed > budget:
                errors.append({"run_id": run_id, "kind": "budget",
                               "message": f"not started: the job time budget of {budget:g} s was used up "
                                          f"by earlier runs"})
                status["runs"][run_id] = {"ok": False, "n_events": 0, "seconds": 0.0}
                continue
            r0 = time.monotonic()
            try:
                timeout_s = max(1.0, min(per_run, hard_stop - elapsed))
                failure, n_events = _run_child(run, _run_frame(df, run), in_dir, out_dir, timeout_s,
                                               budget_clipped=timeout_s < per_run,
                                               collected=collected, keep=keep)
            except Exception as exc:  # noqa: BLE001
                failure, n_events = ("internal", f"runner failed to start the run: {type(exc).__name__}: {exc}"), 0
            secs = round(time.monotonic() - r0, 3)
            status["timings"][run_id] = secs
            status["runs"][run_id] = {"ok": failure is None, "n_events": n_events, "seconds": secs}
            if failure is not None:
                errors.append({"run_id": run_id, "kind": failure[0], "message": failure[1]})
        return 0
    finally:
        _kill_strays(keep)
        # out_dir holds only what this parent writes: anything a strategy process dropped
        # there is removed, then the collected events (parent memory) and status.json.
        try:
            for stale in list(out_dir.iterdir()):
                _remove(stale)
        except OSError:
            pass
        for rid, data in collected.items():
            (out_dir / f"{rid}.events.npy").write_bytes(data)
        status["timings"]["_total"] = round(time.monotonic() - t0, 3)
        status["ok"] = not errors
        for e in errors:
            e["message"] = _ascii_message(e["message"])
        _write_json(out_dir / OUT_STATUS, status)


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "--child":
        if len(args) != 5:
            return 2
        return child_main(args[1], Path(args[2]), Path(args[3]), Path(args[4]))
    if len(args) != 2:
        sys.stderr.write("usage: sandbox_runner.py <in_dir> <out_dir>\n")
        return 2
    return parent_main(Path(args[0]), Path(args[1]))


if __name__ == "__main__":
    sys.exit(main())
