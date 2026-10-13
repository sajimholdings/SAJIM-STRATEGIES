"""
SAJIM Quant - Historical Market Data Loader
===========================================
Single entry point for every adapter and runner that needs OHLCV bars.

Layout on disk (both are optional; Parquet wins when present):

    data/parquet/{SYMBOL}/{TF}.parquet        fast cache written by `DataLoader.build_cache()`
    data/parquet/{SYMBOL} {TF}.parquet        legacy flat layout, still readable
    data/dukascopy/{SYMBOL}/{TF}.csv          raw download from tools/data_downloader

Every frame returned has the columns  time, open, high, low, close, volume  with `time`
as timezone-naive UTC `datetime64[ns]`. With `set_index=True` the frame is indexed by `time`
(what Backtrader's PandasData feed expects).

The repository root is located by walking up from this file until `pyproject.toml` is found,
so the loader works no matter where the caller lives or what the current directory is.
`SAJIM_DATA_DIR` overrides the data root entirely.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

TIMEFRAMES: List[str] = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

DateLike = Union[str, pd.Timestamp, None]


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk upwards until a directory containing pyproject.toml (or .git) is found."""
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    # Fallback: three levels up from sajim_quant/data/loader.py
    return Path(__file__).resolve().parent.parent.parent


def default_data_dir() -> Path:
    env = os.environ.get("SAJIM_DATA_DIR")
    return Path(env).expanduser().resolve() if env else find_repo_root() / "data"


class DataLoader:
    """Loads cached OHLCV bars for a symbol/timeframe pair."""

    def __init__(
        self,
        parquet_root: Optional[Union[str, Path]] = None,
        csv_root: Optional[Union[str, Path]] = None,
    ) -> None:
        data_dir = default_data_dir()
        self.parquet_root = Path(parquet_root) if parquet_root else data_dir / "parquet"
        self.csv_root = Path(csv_root) if csv_root else data_dir / "dukascopy"

    # ------------------------------------------------------------------ paths
    def parquet_path(self, symbol: str, timeframe: str) -> Path:
        return self.parquet_root / symbol.upper() / f"{timeframe.upper()}.parquet"

    def legacy_parquet_path(self, symbol: str, timeframe: str) -> Path:
        return self.parquet_root / f"{symbol.upper()} {timeframe.upper()}.parquet"

    def csv_path(self, symbol: str, timeframe: str) -> Path:
        return self.csv_root / symbol.upper() / f"{timeframe.upper()}.csv"

    # -------------------------------------------------------------- discovery
    def list_symbols(self) -> List[str]:
        symbols = set()
        if self.parquet_root.exists():
            for p in self.parquet_root.iterdir():
                if p.is_dir():
                    symbols.add(p.name.upper())
                elif p.suffix == ".parquet" and " " in p.stem:
                    symbols.add(p.stem.split(" ")[0].upper())
        if self.csv_root.exists():
            for p in self.csv_root.iterdir():
                if p.is_dir():
                    symbols.add(p.name.upper())
        return sorted(symbols)

    def list_timeframes(self, symbol: str) -> List[str]:
        tfs = set()
        sym_dir = self.parquet_root / symbol.upper()
        if sym_dir.exists():
            tfs.update(p.stem.upper() for p in sym_dir.glob("*.parquet"))
        csv_dir = self.csv_root / symbol.upper()
        if csv_dir.exists():
            tfs.update(p.stem.upper() for p in csv_dir.glob("*.csv"))
        return [tf for tf in TIMEFRAMES if tf in tfs] + sorted(tfs - set(TIMEFRAMES))

    def has_data(self, symbol: str, timeframe: str) -> bool:
        return (
            self.parquet_path(symbol, timeframe).exists()
            or self.legacy_parquet_path(symbol, timeframe).exists()
            or self.csv_path(symbol, timeframe).exists()
        )

    # ---------------------------------------------------------------- loading
    def load(
        self,
        symbol: str,
        timeframe: str,
        start_date: DateLike = None,
        end_date: DateLike = None,
        set_index: bool = False,
    ) -> pd.DataFrame:
        """
        Returns bars for `symbol`/`timeframe` filtered to [start_date, end_date].
        Raises FileNotFoundError when neither a Parquet file nor a CSV exists.
        """
        df = self._read_raw(symbol, timeframe)
        df = self._normalize(df)

        if start_date is not None:
            df = df[df["time"] >= pd.Timestamp(start_date)]
        if end_date is not None:
            df = df[df["time"] <= pd.Timestamp(end_date)]
        df = df.reset_index(drop=True)

        if set_index:
            df = df.set_index("time")
        return df

    def _read_raw(self, symbol: str, timeframe: str) -> pd.DataFrame:
        for p in (self.parquet_path(symbol, timeframe), self.legacy_parquet_path(symbol, timeframe)):
            if p.exists():
                return pd.read_parquet(p)
        csv = self.csv_path(symbol, timeframe)
        if csv.exists():
            return pd.read_csv(csv)
        raise FileNotFoundError(
            f"No cached data for {symbol.upper()} {timeframe.upper()}. Looked in "
            f"{self.parquet_path(symbol, timeframe)} and {csv}. "
            "Run tools/data_downloader/download_all.py, then DataLoader.build_cache()."
        )

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "time" not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "time"})
            else:
                first = df.columns[0]
                df = df.rename(columns={first: "time"})
        rename = {c: c.lower() for c in df.columns}
        for c in list(df.columns):
            lc = c.lower()
            if lc in ("timestamp", "datetime", "date", "gmt time"):
                rename[c] = "time"
            elif lc in ("vol", "tick_volume", "tickvol"):
                rename[c] = "volume"
        df = df.rename(columns=rename)
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
        df = df[_COLUMNS].sort_values("time")
        df = df.drop_duplicates(subset="time", keep="last")
        for c in _COLUMNS[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["open", "high", "low", "close"])

    # ------------------------------------------------------------------ cache
    def build_cache(self, symbols: Optional[List[str]] = None, timeframes: Optional[List[str]] = None) -> List[Path]:
        """Converts every available CSV into the Parquet layout. Returns the files written."""
        written: List[Path] = []
        for sym in symbols or self.list_symbols():
            for tf in timeframes or self.list_timeframes(sym):
                csv = self.csv_path(sym, tf)
                if not csv.exists():
                    continue
                out = self.parquet_path(sym, tf)
                out.parent.mkdir(parents=True, exist_ok=True)
                self._normalize(pd.read_csv(csv)).to_parquet(out, index=False)
                written.append(out)
        return written

    def save_frame(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        """Writes an in-memory frame (e.g. a synthetic fixture) into the Parquet cache."""
        out = self.parquet_path(symbol, timeframe)
        out.parent.mkdir(parents=True, exist_ok=True)
        self._normalize(df).to_parquet(out, index=False)
        return out
