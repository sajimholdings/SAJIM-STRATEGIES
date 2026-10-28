"""
Worked example: Asian-range breakout into London (long only).

Idea: "Buy gold when London breaks the Asian range high; stop below the range low;
2R target; flat by NY close."  Rules, all UTC:
  1. The Asian range is the high/low of bars opening in [0, range_end_h).
  2. From range_end_h until flat_h, the FIRST close above the range high of the day
     is a long signal (fill at the next bar's open, done by the simulator).
  3. Stop distance = signal close - range low (the stop sits at the range low,
     measured from the close; the simulator re-anchors it at the fill).
  4. Target distance = rr x stop distance.
  5. The first bar opening at or after flat_h raises `exit` (flat at the next open).
manifest: allow_short false, max_hold_bars 96 (one day of M15 bars).
"""

import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind

PARAMS = {
    "range_end_h": {"default": 6, "min": 4, "max": 8, "type": "int"},
    "flat_h": {"default": 20, "min": 16, "max": 22, "type": "int"},
    "rr": {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"},
}


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Row i uses rows <= i only: session_range exposes a session once it has closed."""
    range_end_h = int(params["range_end_h"])
    flat_h = int(params["flat_h"])
    rr = float(params["rr"])

    close = df["close"]
    rng = ind.session_range(df, 0, range_end_h)
    hours = pd.Series(df.index.hour, index=df.index)
    day = pd.Series(df.index.normalize(), index=df.index)

    in_window = (hours >= range_end_h) & (hours < flat_h)
    above = close > rng["high"]
    crossed = above & ~above.shift(1, fill_value=False) & in_window & rng["low"].notna()
    first_of_day = crossed & (crossed.astype(int).groupby(day).cumsum() == 1)

    stop = close - rng["low"]
    long_entry = first_of_day & (stop > 0)

    late = hours >= flat_h
    exit_flag = late & (late.astype(int).groupby(day).cumsum() == 1)

    out = pd.DataFrame(index=df.index)
    out["signal"] = np.where(long_entry, 1, 0).astype("int8")
    out["stop_dist"] = stop.where(long_entry).astype("float64")
    out["tp_dist"] = (stop * rr).where(long_entry).astype("float64")
    out["exit"] = exit_flag.astype(bool)
    return out
