"""
Dukascopy Multi-Symbol Parallel Data Downloader — Fast & Robust
===============================================================
Downloads 2 years of OHLCV data across Forex, Crypto, Commodities, and Indices.
Uses dukascopy_python.fetch() with verified INSTRUMENT_* constants.
Parallelized with ThreadPoolExecutor (10 workers).

Usage:
    python download_all.py              # full 120-task download
    python download_all.py --dry-run    # inspect tasks without downloading
    python download_all.py --workers 10 # customize worker count
    python download_all.py --symbol EURUSD BTCUSD
    python download_all.py --tf M5 H1
"""

import sys
import os
import json
import time
import logging
import argparse
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

# Force UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", buffering=1)

import pandas as pd
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init

import dukascopy_python as duka
import dukascopy_python.instruments as instruments

# ─────────────────────────── CONFIG ───────────────────────────

BASE_DIR     = Path(__file__).parent
OUTPUT_DIR   = BASE_DIR.parent.parent / "data" / "dukascopy"
SYMBOLS_FILE = BASE_DIR / "symbols.json"
LOG_DIR      = BASE_DIR / "logs"
REPORT_FILE  = BASE_DIR / "download_report.md"

START_DATE = datetime(2024, 9, 1)
END_DATE   = datetime(2026, 9, 14)

DEFAULT_WORKERS = 10
MAX_RETRIES     = 3
RETRY_BACKOFF   = [5, 15, 30]

TIMEFRAMES = {
    "M1":  duka.INTERVAL_MIN_1,
    "M5":  duka.INTERVAL_MIN_5,
    "M15": duka.INTERVAL_MIN_15,
    "M30": duka.INTERVAL_MIN_30,
    "H1":  duka.INTERVAL_HOUR_1,
    "H4":  duka.INTERVAL_HOUR_4,
}

# ─────────────────────────── LOGGING ──────────────────────────

colorama_init(autoreset=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

# Route root logs to file, keep console clean
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"download_{run_ts}.log", encoding="utf-8"),
    ],
)

# Completely silence noisy DUKASCRIPT logger from stdout/stderr
duka_logger = logging.getLogger("DUKASCRIPT")
duka_logger.setLevel(logging.CRITICAL)
duka_logger.propagate = False
duka_logger.handlers = [logging.NullHandler()]

_print_lock = Lock()

def cprint(msg: str, color=Fore.WHITE):
    with _print_lock:
        print(color + msg + Style.RESET_ALL, flush=True)

# ─────────────────────────── HELPERS ──────────────────────────

def get_instrument(const_name: str):
    """Resolve INSTRUMENT_* constant string to library object."""
    return getattr(instruments, const_name)


def output_path(symbol: str, tf: str) -> Path:
    p = OUTPUT_DIR / symbol
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{tf}.csv"


def already_complete(path: Path, min_rows: int = 200) -> bool:
    if not path.exists():
        return False
    try:
        count = sum(1 for _ in open(path, encoding="utf-8")) - 1
        return count > min_rows
    except Exception:
        return False

# ─────────────────────────── WORKER TASK ──────────────────────

def download_symbol_tf(symbol_cfg: dict, tf_name: str, dry_run: bool = False) -> dict:
    sym_name   = symbol_cfg["symbol"]
    const_name = symbol_cfg["instrument_const"]
    out_path   = output_path(sym_name, tf_name)
    tag        = f"{sym_name}/{tf_name}"

    result = {
        "symbol":  sym_name, "tf": tf_name, "status": "PENDING",
        "rows":    0,        "start": None, "end": None,
        "size_mb": 0.0,      "file": str(out_path), "error": "",
    }

    if dry_run:
        result["status"] = "DRY_RUN"
        return result

    if already_complete(out_path):
        rows = sum(1 for _ in open(out_path, encoding="utf-8")) - 1
        result.update({
            "status": "SKIPPED",
            "rows": rows,
            "size_mb": round(out_path.stat().st_size / 1e6, 2)
        })
        cprint(f"  [SKIP] {tag:18s} already exists ({rows:,} rows)", Fore.CYAN)
        return result

    try:
        instrument = get_instrument(const_name)
    except AttributeError:
        result["status"] = "FAILED"
        result["error"]  = f"Missing constant: {const_name}"
        cprint(f"  [FAIL] {tag:18s} invalid constant: {const_name}", Fore.RED)
        return result

    cprint(f"  [DOWN] {tag:18s} starting fetch...", Fore.YELLOW)
    start_time = time.time()

    df = None
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            df = duka.fetch(
                instrument=instrument,
                interval=TIMEFRAMES[tf_name],
                offer_side=duka.OFFER_SIDE_BID,
                start=START_DATE,
                end=END_DATE,
                max_retries=5,
            )
            if df is not None and len(df) > 0:
                break
        except Exception as exc:
            last_err = str(exc)[:120]
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            if attempt < MAX_RETRIES - 1:
                cprint(f"  [WARN] {tag:18s} attempt {attempt+1} failed: {last_err}. Retrying in {wait}s...", Fore.YELLOW)
                time.sleep(wait)

    if df is None or len(df) == 0:
        result["status"] = "FAILED"
        result["error"]  = last_err or "Empty dataset returned"
        cprint(f"  [FAIL] {tag:18s} {result['error']}", Fore.RED)
        return result

    # Format dataframe cleanly: time, open, high, low, close, volume
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df.reset_index(inplace=True)

    # First column is timestamp
    col0 = df.columns[0]
    df.rename(columns={col0: "time"}, inplace=True)
    df["time"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    # Standardize column names
    col_map = {}
    for col in df.columns:
        low = col.lower()
        if "open" in low:   col_map[col] = "open"
        elif "high" in low: col_map[col] = "high"
        elif "low" in low:  col_map[col] = "low"
        elif "close" in low:col_map[col] = "close"
        elif "vol" in low:  col_map[col] = "volume"
    df.rename(columns=col_map, inplace=True)

    target_cols = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[target_cols]

    # Save CSV
    df.to_csv(out_path, index=False)

    size_mb  = round(out_path.stat().st_size / 1e6, 2)
    rows     = len(df)
    t_start  = df["time"].iloc[0][:10] if rows > 0 else "N/A"
    t_end    = df["time"].iloc[-1][:10] if rows > 0 else "N/A"
    duration = time.time() - start_time

    result.update({
        "status":  "OK",
        "rows":    rows,
        "start":   t_start,
        "end":     t_end,
        "size_mb": size_mb,
    })

    cprint(
        f"  [ OK ] {tag:18s} {rows:>8,} rows  {t_start} -> {t_end}  ({size_mb:.1f} MB in {duration:.1f}s)",
        Fore.GREEN,
    )
    return result

# ─────────────────────────── REPORT ───────────────────────────

def write_report(results: list, elapsed: float):
    lines = [
        "# Dukascopy Download Report",
        f"\n- **Run Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Elapsed:** {elapsed/60:.1f} minutes",
        f"- **Date Range:** {START_DATE.date()} to {END_DATE.date()}",
        "",
        "| Symbol | TF | Status | Rows | Start Date | End Date | Size (MB) | Error |",
        "|:-------|:---|:-------|-----:|:-----------|:---------|----------:|:------|",
    ]
    ok = skipped = failed = 0
    for r in sorted(results, key=lambda x: (x["symbol"], x["tf"])):
        lines.append(
            f"| {r['symbol']} | {r['tf']} | {r['status']} "
            f"| {r['rows']:,} | {r['start'] or '-'} | {r['end'] or '-'} "
            f"| {r['size_mb']} | {r['error']} |"
        )
        if r["status"] == "OK":        ok += 1
        elif r["status"] == "SKIPPED": skipped += 1
        elif r["status"] == "FAILED":  failed += 1

    total_mb = sum(r["size_mb"] for r in results)
    lines += [
        "",
        "## Summary",
        f"- **Completed:** {ok}",
        f"- **Skipped (Pre-existing):** {skipped}",
        f"- **Failed:** {failed}",
        f"- **Total Stored Size:** {total_mb:.1f} MB",
    ]
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")

# ─────────────────────────── MAIN ─────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Dukascopy parallel downloader")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--workers",  type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--symbol",   nargs="+", metavar="SYM")
    parser.add_argument("--tf",       nargs="+", metavar="TF")
    args = parser.parse_args()

    with open(SYMBOLS_FILE, encoding="utf-8") as f:
        all_symbols = json.load(f)

    if args.symbol:
        wanted = [s.upper() for s in args.symbol]
        all_symbols = [s for s in all_symbols if s["symbol"] in wanted]

    tf_names = [t.upper() for t in args.tf] if args.tf else list(TIMEFRAMES.keys())
    tf_names = [t for t in tf_names if t in TIMEFRAMES]

    tasks = [(sym, tf) for sym in all_symbols for tf in tf_names]
    total = len(tasks)

    cprint("=" * 74, Fore.CYAN)
    cprint("       DUKASCOPY HIGH-SPEED PARALLEL DOWNLOADER", Fore.CYAN)
    cprint("=" * 74, Fore.CYAN)
    cprint(f"  Symbols     : {len(all_symbols)} ({', '.join(s['symbol'] for s in all_symbols)})", Fore.WHITE)
    cprint(f"  Timeframes  : {', '.join(tf_names)}", Fore.WHITE)
    cprint(f"  Total Tasks : {total} ({len(all_symbols)} symbols x {len(tf_names)} timeframes)", Fore.WHITE)
    cprint(f"  Workers     : {args.workers} concurrent threads", Fore.WHITE)
    cprint(f"  Date Range  : {START_DATE.date()} -> {END_DATE.date()} (2 Years)", Fore.WHITE)
    cprint(f"  Output Dir  : {OUTPUT_DIR}", Fore.WHITE)
    cprint(f"  Dry Run     : {'YES' if args.dry_run else 'NO'}", Fore.WHITE)
    cprint("=" * 74, Fore.CYAN)

    if args.dry_run:
        for sym, tf in tasks:
            cprint(f"  [DRY-RUN] {sym['symbol']}/{tf} -> {sym['instrument_const']}", Fore.MAGENTA)
        cprint(f"\n{total} tasks previewed.", Fore.CYAN)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results  = []
    start_ts = time.time()
    pbar     = tqdm(total=total, desc="Progress", unit="task",
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_symbol_tf, sym, tf, args.dry_run): (sym["symbol"], tf)
            for sym, tf in tasks
        }
        for future in as_completed(futures):
            sym_label, tf_label = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "symbol": sym_label, "tf": tf_label, "status": "FAILED",
                    "rows": 0, "start": None, "end": None,
                    "size_mb": 0.0, "file": "", "error": str(exc)[:200],
                }
                cprint(f"  [EXCEPTION] {sym_label}/{tf_label}: {exc}", Fore.RED)
            results.append(result)
            pbar.update(1)

    pbar.close()
    elapsed = time.time() - start_ts

    ok       = sum(1 for r in results if r["status"] == "OK")
    skipped  = sum(1 for r in results if r["status"] == "SKIPPED")
    failed   = sum(1 for r in results if r["status"] == "FAILED")
    total_mb = sum(r["size_mb"] for r in results)

    cprint("=" * 74, Fore.CYAN)
    cprint(f"  COMPLETED in {elapsed/60:.1f} minutes", Fore.GREEN)
    cprint(f"  Downloaded: {ok} | Skipped: {skipped} | Failed: {failed}", Fore.WHITE)
    cprint(f"  Total Data Size: {total_mb:.1f} MB", Fore.WHITE)
    cprint("=" * 74, Fore.CYAN)

    write_report(results, elapsed)
    if failed:
        cprint(f"\n  [ATTENTION] {failed} tasks encountered errors. See report at: {REPORT_FILE}", Fore.YELLOW)


if __name__ == "__main__":
    main()
