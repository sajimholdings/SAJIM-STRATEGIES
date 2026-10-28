"""
SAJIM Quant - High-Performance Historical Data Loader

Provides ultra-fast, read-optimized data loading for backtests and research.
Prioritizes compressed Parquet files with PyArrow predicate pushdown,
with seamless fallback to CSV if Parquet has not yet been generated.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

logger = logging.getLogger("sajim_quant.data.loader")


class DataLoader:
    """
    Sub-millisecond data access layer for SAJIM historical datasets.
    """

    SUPPORTED_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"]

    def __init__(
        self,
        parquet_root: Optional[Path] = None,
        csv_root: Optional[Path] = None,
    ):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.parquet_root = Path(parquet_root) if parquet_root else base_dir / "data" / "parquet"
        self.csv_root = Path(csv_root) if csv_root else base_dir / "data" / "dukascopy"

    def list_symbols(self) -> List[str]:
        """Returns list of symbols available in either parquet or CSV storage."""
        symbols = set()
        if self.parquet_root.exists():
            symbols.update([d.name for d in self.parquet_root.iterdir() if d.is_dir()])
        if self.csv_root.exists():
            symbols.update([d.name for d in self.csv_root.iterdir() if d.is_dir()])
        return sorted(list(symbols))

    def list_timeframes(self, symbol: str) -> List[str]:
        """Returns available timeframes for a given symbol."""
        tfs = set()
        pq_dir = self.parquet_root / symbol
        if pq_dir.exists():
            tfs.update([f.stem for f in pq_dir.glob("*.parquet")])
        csv_dir = self.csv_root / symbol
        if csv_dir.exists():
            tfs.update([f.stem for f in csv_dir.glob("*.csv")])
        return sorted(list(tfs), key=lambda x: self.SUPPORTED_TIMEFRAMES.index(x) if x in self.SUPPORTED_TIMEFRAMES else 99)

    def load(
        self,
        symbol: str,
        timeframe: str = "H1",
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        set_index: bool = True,
    ) -> pd.DataFrame:
        """
        Loads OHLCV data for a given symbol and timeframe.

        Args:
            symbol: Instrument ticker, e.g. "XAUUSD", "EURUSD", "BTCUSD"
            timeframe: "M1", "M5", "M15", "M30", "H1", "H4"
            start_date: Optional start filter (inclusive), e.g. "2024-01-01"
            end_date: Optional end filter (inclusive), e.g. "2024-12-31"
            set_index: If True, sets 'time' as the DataFrame DatetimeIndex.

        Returns:
            pd.DataFrame with columns: [open, high, low, close, volume]
        """
        symbol = symbol.upper()
        timeframe = timeframe.upper()

        pq_path = self.parquet_root / symbol / f"{timeframe}.parquet"
        csv_path = self.csv_root / symbol / f"{timeframe}.csv"

        if pq_path.exists():
            # Fast Parquet path with pushdown filters
            filters = []
            if start_date:
                filters.append(("time", ">=", pd.to_datetime(start_date)))
            if end_date:
                filters.append(("time", "<=", pd.to_datetime(end_date)))

            df = pd.read_parquet(
                pq_path,
                engine="pyarrow",
                filters=filters if filters else None,
            )
        elif csv_path.exists():
            # Fallback CSV path
            df = pd.read_csv(
                csv_path,
                parse_dates=["time"],
                dtype={
                    "open": "float64",
                    "high": "float64",
                    "low": "float64",
                    "close": "float64",
                    "volume": "float64",
                },
            )
            if start_date:
                df = df[df["time"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["time"] <= pd.to_datetime(end_date)]
        else:
            raise FileNotFoundError(
                f"No data found for symbol '{symbol}' on timeframe '{timeframe}'. Checked: {pq_path} and {csv_path}"
            )

        if set_index and not df.empty:
            df = df.set_index("time")

        self._warn_if_short(df, symbol, timeframe, start_date, end_date, set_index)
        return df

    #: A returned span shorter than this fraction of the requested one is flagged.
    SHORT_SPAN_RATIO = 0.9

    @classmethod
    def _warn_if_short(
        cls,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        start_date,
        end_date,
        indexed: bool,
    ) -> None:
        """
        Warn when the data returned covers materially less than was requested.

        Coverage is uneven in the cache — BTCUSD M1 ends 2025-04-19 while its H1
        runs to 2026-09-13, and ETHUSD/LTCUSD M5 are short too. The matrix
        builder asks every node for 2024-09-01..2026-09-01, so those nodes
        silently tested a third of the period the forex nodes did and were then
        ranked side by side. A short frame is still returned (the caller may well
        want it) but it no longer passes unremarked.
        """
        if df.empty or not (start_date and end_date):
            return
        try:
            req_start = pd.to_datetime(start_date)
            req_end = pd.to_datetime(end_date)
            have = df.index if indexed else df["time"]
            got_start = pd.to_datetime(have[0] if indexed else have.iloc[0])
            got_end = pd.to_datetime(have[-1] if indexed else have.iloc[-1])
        except (KeyError, IndexError, ValueError, TypeError):
            return

        requested_days = (req_end - req_start).days
        covered_days = (got_end - got_start).days
        if requested_days <= 0:
            return

        if covered_days < requested_days * cls.SHORT_SPAN_RATIO:
            logger.warning(
                "%s %s covers %s to %s (%d of %d requested days, %.0f%%). "
                "Metrics from this run are not comparable with a full-period run.",
                symbol, timeframe,
                got_start.date(), got_end.date(),
                covered_days, requested_days,
                100.0 * covered_days / requested_days,
            )


# Lazy singleton — only created on first actual data access
_default_loader: Optional[DataLoader] = None


def _get_default_loader() -> DataLoader:
    """Returns the default DataLoader, creating it on first call."""
    global _default_loader
    if _default_loader is None:
        _default_loader = DataLoader()
    return _default_loader


def load_data(
    symbol: str,
    timeframe: str = "H1",
    start_date: Optional[Union[str, pd.Timestamp]] = None,
    end_date: Optional[Union[str, pd.Timestamp]] = None,
    set_index: bool = True,
) -> pd.DataFrame:
    """Convenience function: load_data('XAUUSD', 'H1', start_date='2024-01-01')"""
    return _get_default_loader().load(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        set_index=set_index,
    )
