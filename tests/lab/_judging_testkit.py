"""Shared helpers for builder C's tests (robustness / scorecard / ledger / evaluate).

Synthetic OHLC only; never loads data/**. The fake executor runs strategy code IN-PROCESS:
it is test scaffolding for trusted, hand-written strategies, never a security boundary.
"""

from __future__ import annotations

import hashlib
import io
import json
import textwrap
import sys
from typing import Dict, Iterable, Mapping, Optional, Union

import numpy as np
import pandas as pd

from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I

sys.dont_write_bytecode = True        # no __pycache__ next to hooks, skills or examples the tests load

# ── Strategies (lint-clean under contract.lint_source) ──────────────────────
#: Causal planted edge: a volume spike on bar i (visible on bar i) precedes an upward drift.
PLANTED_SRC = textwrap.dedent('''\
    import numpy as np
    import pandas as pd

    PARAMS = {
        "vol_mult": {"default": 3.0, "min": 1.5, "max": 6.0, "type": "float"},
        "stop_rng": {"default": 2.0, "min": 1.0, "max": 4.0, "type": "float"},
        "rr": {"default": 2.0, "min": 1.0, "max": 4.0, "type": "float"},
    }


    def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
        vol_base = df["volume"].rolling(48, min_periods=48).mean().shift(1)
        bar_rng = (df["high"] - df["low"]).rolling(14, min_periods=14).mean()
        spike = df["volume"] > params["vol_mult"] * vol_base
        stop = params["stop_rng"] * bar_rng
        out = pd.DataFrame(index=df.index)
        out["signal"] = (spike & stop.notna()).astype("int8")
        out["stop_dist"] = stop
        out["tp_dist"] = stop * params["rr"]
        return out
''').encode("utf-8")

#: A coin flip on a random walk: every `every` bars, follow the last bar's colour.
COIN_SRC = textwrap.dedent('''\
    import numpy as np
    import pandas as pd

    PARAMS = {
        "every": {"default": 72, "min": 36, "max": 144, "type": "int"},
        "stop_rng": {"default": 2.0, "min": 1.0, "max": 4.0, "type": "float"},
    }


    def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
        k = np.arange(len(df))
        bar_rng = (df["high"] - df["low"]).rolling(14, min_periods=14).mean()
        pick = (k % params["every"] == 0) & bar_rng.notna().to_numpy()
        side = np.where(df["close"].to_numpy() > df["open"].to_numpy(), 1, -1)
        out = pd.DataFrame(index=df.index)
        out["signal"] = pd.Series(np.where(pick, side, 0), index=df.index).astype("int8")
        out["stop_dist"] = params["stop_rng"] * bar_rng
        out["tp_dist"] = params["stop_rng"] * bar_rng
        return out
''').encode("utf-8")

#: PLANTED_SRC that also requires the close 3 bars ahead to be higher: a short look-ahead written as
#: an array slice (lint-clean), caught only by the adaptive cuts placed just after its own signals.
PEEK_SRC = PLANTED_SRC.replace(
    b'    out["signal"] = (spike & stop.notna()).astype("int8")\n',
    b'    ahead = pd.Series(np.r_[df["close"].to_numpy()[3:], [np.nan] * 3], index=df.index)\n'
    b'    out["signal"] = (spike & stop.notna() & (ahead > df["close"])).astype("int8")\n')
assert PEEK_SRC != PLANTED_SRC

SPEC_MD = b"# spec\n\nSynthetic test strategy.\n"


# ── Synthetic data ──────────────────────────────────────────────────────────
def synthetic_ohlc(start: str, end: str, *, seed: int, freq: str = "1h", markers: bool = True,
                   marker_every: int = 30, drift_bars: int = 6, drift: float = 2.0, sigma: float = 2.0,
                   px: float = 2000.0, n_markers: Optional[int] = None) -> pd.DataFrame:
    """
    XAUUSD-like hourly bars on [start, end). Marker bars carry volume 1000 (base 100..200);
    the `drift_bars` bars after a marker drift by `drift` per bar (negative = adverse).
    The added drift is removed again uniformly, so the series has no net trend.
    """
    idx = pd.date_range(start, end, freq=freq, inclusive="left", name="time")
    n = len(idx)
    rng = np.random.default_rng(seed)
    inc = rng.normal(0.0, sigma, n)
    vol = rng.uniform(100.0, 200.0, n)
    if markers:
        gaps = rng.integers(marker_every // 2, marker_every * 3 // 2 + 1, size=n)
        pos = 60 + np.cumsum(gaps)
        pos = pos[pos < n - drift_bars - 2]
        if n_markers is not None:
            pos = pos[:n_markers]
        vol[pos] = 1000.0
        for p in pos:
            inc[p + 1:p + 1 + drift_bars] += drift
        inc -= drift * drift_bars * len(pos) / n
    close = px + np.cumsum(inc)
    open_ = np.r_[px, close[:-1]]
    wick = np.abs(rng.normal(0.0, sigma * 0.5, (2, n)))
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def dev_raw(seed: int = 11, **kw) -> pd.DataFrame:
    """The whole DEV window, hourly, 24/7."""
    return synthetic_ohlc("2024-09-01", "2026-01-01", seed=seed, **kw)


def holdout_raw(seed: int = 23, end: str = "2026-10-01", **kw) -> pd.DataFrame:
    """Late DEV + PURGE (warmup only) + HOLDOUT to `end`."""
    return synthetic_ohlc("2025-12-01", end, seed=seed, **kw)


# ── Submissions ─────────────────────────────────────────────────────────────
def manifest(src: bytes, *, lab_id: str = "lab_test_strategy", family: Optional[str] = None,
             symbol: str = "XAUUSD", timeframe: str = "H1", max_hold_bars: int = 12,
             allow_short: bool = False, spec_md: bytes = SPEC_MD, **overrides) -> Dict:
    m = {
        "schema": gates.MANIFEST_SCHEMA, "id": lab_id, "family": family or lab_id, "name": "Test strategy",
        "description": "synthetic test strategy", "origin": gates.ORIGIN, "run_adapter": gates.RUN_ADAPTER,
        "engine": gates.ENGINE, "symbol": symbol, "timeframe": timeframe, "supported_symbols": [symbol],
        "breadth_symbols": ["XAGUSD"] if symbol == "XAUUSD" else [], "max_hold_bars": max_hold_bars,
        "allow_short": allow_short, "warmup_bars": 100,
        "spec_sha256": hashlib.sha256(spec_md).hexdigest(), "code_sha256": hashlib.sha256(src).hexdigest(),
    }
    m.update(overrides)
    return m


def submission(src: bytes, **kw) -> I.Submission:
    m = manifest(src, **kw)
    return I.Submission(lab_id=m["id"], family=m["family"], strategy_py=src, spec_md=SPEC_MD, manifest=m)


def budget(trial: Optional[int] = 1, *, touches: int = 0, n_global: Optional[int] = None) -> I.BudgetInfo:
    n_family = trial or 0
    return I.BudgetInfo(trial=trial, n_trials_family=n_family,
                        n_trials_global=n_family if n_global is None else n_global, holdout_touches=touches,
                        budget_exhausted=(trial or 0) >= gates.MAX_DEV_TRIALS_PER_FAMILY,
                        holdout_burned=touches >= gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY)


# ── Executors ───────────────────────────────────────────────────────────────
#: Per-run timeout for REAL local sandbox runs in tests. On a shared laptop (other test suites
#: running) one child's start-up alone can exceed the production limit, interfaces.PER_RUN_TIMEOUT_S
#: (14 s, sized for a dedicated 2-CPU Modal sandbox). Timeout behaviour itself is tested in
#: test_sandbox_runner.py and with injected kinds; the evaluation code never raises the limit.
LOCAL_PER_RUN_TIMEOUT_S = float(gates.MAX_SIGNAL_RUNTIME_S)


class LocalTimeoutExecutor:
    """Wraps a real (local) SandboxExecutor and gives every job the laptop per-run timeout."""

    def __init__(self, inner):
        self.inner = inner

    def run(self, job: I.SandboxJob) -> I.SandboxResult:
        import dataclasses
        return self.inner.run(dataclasses.replace(job, per_run_timeout_s=LOCAL_PER_RUN_TIMEOUT_S))


# ── Fake executor ───────────────────────────────────────────────────────────
def dense_to_events(out: pd.DataFrame) -> np.ndarray:
    sig = out["signal"].to_numpy().astype(np.int8)
    ex = out["exit"].to_numpy().astype(bool) if "exit" in out.columns else np.zeros(len(out), bool)
    keep = (sig != 0) | ex
    ev = np.zeros(int(keep.sum()), dtype=I.EVENTS_DTYPE)
    ev["bar"] = np.flatnonzero(keep)
    ev["signal"] = sig[keep]
    on = sig[keep] != 0
    ev["stop_dist"] = np.where(on, out["stop_dist"].to_numpy(np.float64)[keep], np.nan)
    ev["tp_dist"] = np.where(on, out["tp_dist"].to_numpy(np.float64)[keep], np.nan)
    ev["exit"] = ex[keep]
    return ev


def npy_bytes(ev: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, ev, allow_pickle=False)
    return buf.getvalue()


class InProcessExecutor:
    """SandboxExecutor fake: runs the (trusted, test-only) strategy in-process with the real run plan."""

    def __init__(self, fail_runs: Union[Iterable[str], Mapping[str, str]] = ()):
        self.jobs = []
        # run_id -> error kind the fake reports for that run (a plain iterable means "exception")
        self.fail_runs = (dict(fail_runs) if isinstance(fail_runs, Mapping)
                          else {r: "exception" for r in fail_runs})

    def run(self, job: I.SandboxJob) -> I.SandboxResult:
        self.jobs.append(job)
        table = pd.read_parquet(io.BytesIO(job.frame_parquet))
        frame = table.set_index("time")
        frame.index = pd.DatetimeIndex(frame.index).astype("datetime64[ns]")
        assert len(frame) == job.n_bars
        files: Dict[str, bytes] = {}
        errors, runs = [], {}
        for run in job.runs:
            if run.run_id in self.fail_runs:
                kind = self.fail_runs[run.run_id]
                msg = ("RuntimeError: injected" if kind == "exception"
                       else f"injected {kind}: generate_signals did not finish in time")
                errors.append({"run_id": run.run_id, "kind": kind, "message": msg})
                continue
            df = frame
            if run.cut is not None:
                df = frame.iloc[:run.cut]
            elif run.scramble is not None:
                from sajim_quant.lab.sandbox_runner import scramble_frame
                df = scramble_frame(frame, run.scramble.start, run.scramble.seed)
            ns: Dict = {"__name__": "lab_strategy"}
            exec(compile(job.strategy_py, "strategy.py", "exec"), ns)  # noqa: S102 - trusted test strategies only
            ev = dense_to_events(ns["generate_signals"](df.copy(), dict(run.params)))
            files[I.events_file(run.run_id)] = npy_bytes(ev)
            runs[run.run_id] = {"ok": True, "n_events": int(len(ev)), "seconds": 0.0}
        status = {"schema": I.STATUS_SCHEMA, "ok": not errors, "runs": runs, "errors": errors,
                  "timings": {}, "versions": {}, "sensitive_env_names": []}
        files[I.OUT_STATUS] = json.dumps(status).encode("utf-8")
        return I.SandboxResult(files=files, exit_code=0, timed_out=False, wall_s=0.0)
