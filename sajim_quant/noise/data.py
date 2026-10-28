"""Load M5 bars from the SAJIM Dukascopy parquet store and turn them into returns."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# NoisePrediction/ lives inside SAJIM STRATEGIES/, next to data/
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "parquet"

BAR_SECONDS = 300
MAX_GAP_SECONDS = 30 * 60   # returns spanning a longer gap (weekend, outage) are dropped
DAY_SHIFT = pd.Timedelta(hours=3)  # FX week opens 21:00/22:00 UTC -> that session belongs to the next day


def load_m5(symbol: str, data_root: Path | str | None = None) -> pd.DataFrame:
    """Load M5 bars for `symbol`. Times are naive UTC bar-open times."""
    root = Path(data_root) if data_root else DEFAULT_DATA_ROOT
    path = root / symbol.upper() / "M5.parquet"
    if not path.exists():
        available = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
        raise FileNotFoundError(f"No M5 data at {path}. Available symbols: {', '.join(available)}")
    df = pd.read_parquet(path, columns=["time", "close"])
    df["time"] = pd.to_datetime(df["time"])
    if df["time"].dt.tz is not None:
        df["time"] = df["time"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return df[df["close"] > 0].reset_index(drop=True)


def session_day(times: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """Trading day each timestamp belongs to (Sunday-evening open counts as Monday)."""
    return (pd.Series(times) + DAY_SHIFT).dt.floor("D")


def m5_returns(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Close-to-close log returns between consecutive bars, labelled with the
    bar's open time. Returns that span a gap > MAX_GAP_SECONDS are dropped so
    weekend gaps and data holes don't show up as volatility.
    """
    t = bars["time"]
    gap = t.diff().dt.total_seconds()
    r = np.log(bars["close"]).diff()
    out = pd.DataFrame({"time": t, "ret": r, "gap": gap})
    out = out[(out["gap"] > 0) & (out["gap"] <= MAX_GAP_SECONDS)].drop(columns="gap")
    out["day"] = session_day(out["time"]).values
    out["hour"] = out["time"].dt.hour
    return out.reset_index(drop=True)
