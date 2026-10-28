"""
Worked example: trend pullback to the fast EMA (long and short).

Idea: "In an uptrend (fast EMA above slow EMA) buy when price dips to the fast EMA
and closes back above it; mirror it for shorts. ATR stop, fixed reward/risk."
Rules (bar closes, UTC):
  1. Uptrend: ema(fast) > ema(slow) and close > ema(slow). Downtrend: the mirror.
  2. Long: uptrend, this bar's low touched ema(fast), and it closed above ema(fast),
     while the previous close was also above ema(fast) (a dip, not a cross).
  3. Short: the mirror image.
  4. Stop distance = atr_mult x ATR(14); target distance = rr x stop distance.
  5. No discretionary exit: stop, target or max_hold_bars end the trade.
manifest: allow_short true, max_hold_bars 48 (two days of H1 bars).
"""

import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind

ATR_N = 14

PARAMS = {
    "fast": {"default": 20, "min": 10, "max": 40, "type": "int"},
    "slow": {"default": 100, "min": 50, "max": 200, "type": "int"},
    "atr_mult": {"default": 1.5, "min": 0.5, "max": 3.0, "type": "float"},
    "rr": {"default": 2.0, "min": 1.0, "max": 4.0, "type": "float"},
}


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """All inputs are causal indicators of bars <= i; shift(1) reads the previous bar."""
    fast = ind.ema(df["close"], int(params["fast"]))
    slow = ind.ema(df["close"], int(params["slow"]))
    atr = ind.atr(df, ATR_N)
    close, low, high = df["close"], df["low"], df["high"]
    prev_close, prev_fast = close.shift(1), fast.shift(1)

    up = (fast > slow) & (close > slow)
    down = (fast < slow) & (close < slow)
    long_entry = up & (low <= fast) & (close > fast) & (prev_close > prev_fast)
    short_entry = down & (high >= fast) & (close < fast) & (prev_close < prev_fast)

    stop = atr * float(params["atr_mult"])
    ok = stop.notna() & (stop > 0)
    signal = np.where(long_entry & ok, 1, np.where(short_entry & ok, -1, 0))

    out = pd.DataFrame(index=df.index)
    out["signal"] = signal.astype("int8")
    has = out["signal"] != 0
    out["stop_dist"] = stop.where(has).astype("float64")
    out["tp_dist"] = (stop * float(params["rr"])).where(has).astype("float64")
    out["exit"] = False
    return out
