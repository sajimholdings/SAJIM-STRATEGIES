"""
Worked example: z-score mean reversion with a signal exit and no fixed target.

Idea: "When price stretches far below its recent average, buy and hold until it is
back at the average; mirror it for shorts. Protective ATR stop."  Rules (bar closes):
  1. z = zscore(close, n) (rolling mean / rolling std of the last n closes).
  2. Long when z crosses below -z_entry; short when z crosses above +z_entry.
  3. Stop distance = atr_mult x ATR(14). No target (tp_dist NaN).
  4. Exit flag when z crosses zero (back at the mean): flat at the next open.
manifest: allow_short true, max_hold_bars 96.
"""

import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind

ATR_N = 14

PARAMS = {
    "n": {"default": 48, "min": 20, "max": 120, "type": "int"},
    "z_entry": {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"},
    "atr_mult": {"default": 2.0, "min": 1.0, "max": 4.0, "type": "float"},
}


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Crossings compare bar i with bar i-1 only; no whole-series statistics."""
    z = ind.zscore(df["close"], int(params["n"]))
    z_prev = z.shift(1)
    z_entry = float(params["z_entry"])
    stop = ind.atr(df, ATR_N) * float(params["atr_mult"])
    ok = stop.notna() & (stop > 0)

    long_entry = (z < -z_entry) & (z_prev >= -z_entry) & ok
    short_entry = (z > z_entry) & (z_prev <= z_entry) & ok
    crossed_mean = ((z_prev < 0) & (z >= 0)) | ((z_prev > 0) & (z <= 0))

    out = pd.DataFrame(index=df.index)
    out["signal"] = np.where(long_entry, 1, np.where(short_entry, -1, 0)).astype("int8")
    has = out["signal"] != 0
    out["stop_dist"] = stop.where(has).astype("float64")
    out["tp_dist"] = np.nan
    out["exit"] = crossed_mean.fillna(False).astype(bool) & ~has
    return out
