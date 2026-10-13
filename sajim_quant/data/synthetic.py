"""
Deterministic synthetic OHLCV generator for tests and demos.

Never use this for a strategy claim. It exists so the pipeline (adapter -> registry -> dashboard)
can be exercised end-to-end on a machine without the Dukascopy download.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def random_walk_ohlcv(
    bars: int = 3000,
    timeframe: str = "H1",
    start: str = "2024-01-01",
    price: float = 1.10,
    daily_vol: float = 0.006,
    drift: float = 0.0,  # expected log-return per DAY (0.01 = +1%/day); keep tiny or zero
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    minutes = _TF_MINUTES.get(timeframe.upper(), 60)
    bar_vol = daily_vol * np.sqrt(minutes / 1440.0)
    rets = rng.normal(drift * minutes / 1440.0, bar_vol, bars)
    close = price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[price], close[:-1]])
    wick = np.abs(rng.normal(0, bar_vol * 0.6, bars)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    vol = rng.integers(50, 500, bars).astype(float)
    time = pd.date_range(start, periods=bars, freq=f"{minutes}min")
    return pd.DataFrame({"time": time, "open": open_, "high": high, "low": low, "close": close, "volume": vol})
