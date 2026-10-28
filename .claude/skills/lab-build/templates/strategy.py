"""
<One-line name of the idea.>

Rules (copied from spec.md, all times UTC, decided on bar closes):
  1. ...
  2. ...
"""

import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind

# Fixed technical constants that are NOT tuned may live here (small integers only).
ATR_N = 14

# 1..5 entries, exactly as in spec.md's PARAMS table.
PARAMS = {
    "lookback": {"default": 20, "min": 10, "max": 40, "type": "int"},
    "atr_mult": {"default": 1.5, "min": 0.5, "max": 3.0, "type": "float"},
}


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Row i may use only rows <= i. Returns signal/stop_dist/tp_dist/exit on df.index."""
    lookback = int(params["lookback"])
    stop = ind.atr(df, ATR_N) * float(params["atr_mult"])
    ok = stop.notna() & (stop > 0)

    upper_prior = ind.donchian(df, lookback)["upper"].shift(1)
    long_entry = (df["close"] > upper_prior) & ok          # replace with the spec's rules

    out = pd.DataFrame(index=df.index)
    out["signal"] = np.where(long_entry, 1, 0).astype("int8")
    has = out["signal"] != 0
    out["stop_dist"] = stop.where(has).astype("float64")
    out["tp_dist"] = (stop * 2.0).where(has).astype("float64")   # or np.nan for no target
    out["exit"] = False
    return out
