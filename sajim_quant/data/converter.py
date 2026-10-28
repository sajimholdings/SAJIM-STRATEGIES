"""
SAJIM Quant - Dukascopy to Parquet Batch Converter

Converts raw Dukascopy CSV files into compressed, indexed Apache Parquet files
for 50x-100x faster reading and memory-efficient multi-pair backtesting sweeps.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sajim_quant.data.converter")


class DukascopyConverter:
    """
    Scans `data/dukascopy/` and converts all {SYMBOL}/{TIMEFRAME}.csv files
    into `data/parquet/{SYMBOL}/{TIMEFRAME}.parquet` with typed schemas.
    """

    def __init__(
        self,
        csv_root: Optional[Path] = None,
        parquet_root: Optional[Path] = None,
    ):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.csv_root = Path(csv_root) if csv_root else base_dir / "data" / "dukascopy"
        self.parquet_root = Path(parquet_root) if parquet_root else base_dir / "data" / "parquet"

    def list_available_symbols(self) -> List[str]:
        if not self.csv_root.exists():
            return []
        return sorted([d.name for d in self.csv_root.iterdir() if d.is_dir()])

    def convert_file(self, csv_file: Path, output_file: Path, force: bool = False) -> Dict[str, Any]:
        """
        Converts a single CSV to Parquet.
        """
        if output_file.exists() and not force:
            # Check if parquet is newer than CSV
            if output_file.stat().st_mtime > csv_file.stat().st_mtime:
                return {
                    "status": "skipped",
                    "reason": "up_to_date",
                    "file": str(output_file.name),
                }

        t0 = time.perf_counter()
        
        # Read CSV with explicit types
        df = pd.read_csv(
            csv_file,
            parse_dates=["time"],
            dtype={
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "float64",
            },
        )

        # Sort and deduplicate on timestamp
        df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)

        # Ensure directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Save as snappy-compressed parquet
        df.to_parquet(output_file, engine="pyarrow", compression="snappy", index=False)

        elapsed = time.perf_counter() - t0
        csv_size = csv_file.stat().st_size / (1024 * 1024)
        pq_size = output_file.stat().st_size / (1024 * 1024)

        return {
            "status": "converted",
            "symbol": csv_file.parent.name,
            "timeframe": csv_file.stem,
            "rows": len(df),
            "start": str(df["time"].min()),
            "end": str(df["time"].max()),
            "csv_mb": round(csv_size, 2),
            "parquet_mb": round(pq_size, 2),
            "compression_ratio": f"{(1 - pq_size / max(csv_size, 0.001)) * 100:.1f}%",
            "duration_sec": round(elapsed, 3),
        }

    def convert_symbol(self, symbol: str, force: bool = False) -> List[Dict[str, Any]]:
        symbol_dir = self.csv_root / symbol
        if not symbol_dir.exists():
            raise FileNotFoundError(f"Symbol folder not found: {symbol_dir}")

        out_dir = self.parquet_root / symbol
        results = []
        csv_files = sorted(list(symbol_dir.glob("*.csv")))
        
        for csv_f in csv_files:
            tf = csv_f.stem
            out_f = out_dir / f"{tf}.parquet"
            res = self.convert_file(csv_f, out_f, force=force)
            results.append(res)
            if res.get("status") == "converted":
                logger.info(
                    f"Converted {symbol} {tf}: {res['rows']:,} rows | "
                    f"{res['csv_mb']}MB -> {res['parquet_mb']}MB ({res['compression_ratio']} saved) in {res['duration_sec']}s"
                )

        return results

    def convert_all(self, force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        symbols = self.list_available_symbols()
        logger.info(f"Found {len(symbols)} symbols in {self.csv_root} to convert.")
        all_results = {}
        for sym in symbols:
            all_results[sym] = self.convert_symbol(sym, force=force)
        return all_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Convert Dukascopy CSVs to Parquet")
    parser.add_argument("--symbol", type=str, default=None, help="Convert a specific symbol (e.g. XAUUSD)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing parquet files")
    args = parser.parse_args()

    converter = DukascopyConverter()
    if args.symbol:
        converter.convert_symbol(args.symbol, force=args.force)
    else:
        converter.convert_all(force=args.force)
