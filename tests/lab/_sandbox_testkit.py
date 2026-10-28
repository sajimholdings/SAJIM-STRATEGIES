"""Shared helpers for the sandbox tests (builder B). Synthetic OHLC only; never loads data/**."""

from __future__ import annotations

import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_io
from sajim_quant.lab.sandbox_local import LocalSubprocessExecutor

sys.dont_write_bytecode = True        # no __pycache__ next to hooks, skills or examples the tests load

#: Per-run timeout for these LOCAL tests. run_jobs() starts up to 6 jobs at once on a laptop, where
#: a child's start-up alone can exceed the production limit (interfaces.PER_RUN_TIMEOUT_S, 14 s), so
#: tests that are not about timeouts use the CONTRACT's outer cap instead.
LOCAL_PER_RUN_TIMEOUT_S = float(gates.MAX_SIGNAL_RUNTIME_S)

#: Stand-in for A's indicators.py (the canary tests must not depend on it).
STUB_INDICATORS = textwrap.dedent('''
    import pandas as pd

    def sma(x, n):
        return x.rolling(int(n), min_periods=int(n)).mean()
''').encode()


def synthetic_frame(n: int = 1200, seed: int = 7, start: str = "2025-01-06 00:00", freq: str = "15min") -> pd.DataFrame:
    """Seeded geometric random walk with consistent OHLC, DataLoader-shaped (naive UTC 'time' index)."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.002, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0.0, 0.001, n)) + 0.0002
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    vol = rng.integers(1, 1000, n).astype(np.float64)
    idx = pd.date_range(start, periods=n, freq=freq, name="time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def strategy(body: str) -> bytes:
    """Dedent a strategy source and return its bytes."""
    return textwrap.dedent(body).lstrip().encode("utf-8")


def make_job(src: bytes, runs: Sequence[I.SandboxRun], frame: Optional[pd.DataFrame] = None, *,
             allow_short: bool = False, per_run_timeout_s: float = LOCAL_PER_RUN_TIMEOUT_S,
             job_timeout_s: float = I.JOB_TIMEOUT_S, n_bars: Optional[int] = None,
             label: str = "test") -> I.SandboxJob:
    frame = synthetic_frame() if frame is None else frame
    return I.SandboxJob(frame_parquet=sandbox_io.frame_to_parquet(frame),
                        n_bars=len(frame) if n_bars is None else n_bars, strategy_py=src,
                        indicators_py=STUB_INDICATORS, runs=tuple(runs), allow_short=allow_short,
                        per_run_timeout_s=per_run_timeout_s, job_timeout_s=job_timeout_s, label=label)


def run_jobs(jobs: Mapping[str, I.SandboxJob], max_workers: int = 6) -> Dict[str, Tuple[I.SandboxResult, I.DecodedJob]]:
    """Run several jobs concurrently (each is its own process tree) and decode them."""
    def one(job: I.SandboxJob):
        res = LocalSubprocessExecutor().run(job)
        return res, sandbox_io.decode_result(job, res)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {name: pool.submit(one, job) for name, job in jobs.items()}
        return {name: f.result() for name, f in futures.items()}


def errors_of(decoded: I.DecodedJob, run_id: Optional[str]) -> list:
    return [e for e in decoded.errors if e.run_id == run_id]
