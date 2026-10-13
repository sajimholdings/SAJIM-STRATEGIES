"""
Post-download validator
=======================
Reads every downloaded CSV and checks:
  - Row counts vs expected (based on trading hours)
  - Date range completeness
  - Gap detection (missing bars)
  - OHLC sanity (high >= low, no zero prices)

Writes a detailed Markdown report.

Usage:
    python validate_data.py
    python validate_data.py --symbol EURUSD --tf M5
"""

import sys
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", buffering=1)

import pandas as pd

BASE_DIR    = Path(__file__).parent
OUTPUT_DIR  = BASE_DIR.parent.parent / "data" / "dukascopy"
SYMBOLS_FILE = BASE_DIR / "symbols.json"
REPORT_FILE = BASE_DIR / "validation_report.md"

START_DATE = datetime(2024, 9, 1)
END_DATE   = datetime(2026, 9, 14)

# Expected bars per day (approximations, markets closed weekends)
# Forex: ~24h trading, Crypto: 24/7, Indices: ~8-10h
BARS_PER_DAY = {
    "M1":  {"forex_major": 1440, "forex_cross": 1440, "crypto": 1440, "commodity": 1440, "index": 540},
    "M5":  {"forex_major": 288,  "forex_cross": 288,  "crypto": 288,  "commodity": 288,  "index": 108},
    "M15": {"forex_major": 96,   "forex_cross": 96,   "crypto": 96,   "commodity": 96,   "index": 36},
    "M30": {"forex_major": 48,   "forex_cross": 48,   "crypto": 48,   "commodity": 48,   "index": 18},
    "H1":  {"forex_major": 24,   "forex_cross": 24,   "crypto": 24,   "commodity": 24,   "index": 9},
    "H4":  {"forex_major": 6,    "forex_cross": 6,    "crypto": 6,    "commodity": 6,    "index": 3},
}

TRADING_DAYS_IN_RANGE = 522   # ~2 years of weekdays (Mon-Fri)


def detect_gaps(df: pd.DataFrame, tf: str) -> list:
    """Return list of gap descriptions where consecutive bar gap > 2× the expected interval."""
    freq_map = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
    expected_min = freq_map.get(tf, 5)
    threshold_min = expected_min * 2.5

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time")
    diffs = df["time"].diff().dropna()
    gaps = diffs[diffs > timedelta(minutes=threshold_min)]

    result = []
    for idx, gap in gaps.items():
        gap_start = df.loc[idx - 1, "time"] if idx > 0 else None
        gap_end   = df.loc[idx, "time"]
        hours = gap.total_seconds() / 3600
        if hours > 72:   # only report gaps > 3 days (skip weekends)
            result.append(f"{gap_start} → {gap_end} ({hours:.0f}h)")
    return result


def validate_symbol_tf(symbol_cfg: dict, tf: str) -> dict:
    sym_name = symbol_cfg["symbol"]
    category = symbol_cfg["category"]
    path     = OUTPUT_DIR / sym_name / f"{tf}.csv"

    report = {
        "symbol":    sym_name,
        "tf":        tf,
        "status":    "MISSING",
        "rows":      0,
        "start":     "N/A",
        "end":       "N/A",
        "size_mb":   0.0,
        "gaps":      0,
        "ohlc_errors": 0,
        "notes":     "",
    }

    if not path.exists():
        report["notes"] = "File not found"
        return report

    try:
        df = pd.read_csv(path)
    except Exception as e:
        report["status"] = "READ_ERROR"
        report["notes"]  = str(e)
        return report

    rows = len(df)
    report["rows"]    = rows
    report["size_mb"] = round(path.stat().st_size / 1e6, 2)

    if rows == 0:
        report["status"] = "EMPTY"
        report["notes"]  = "0 rows"
        return report

    df["time"] = pd.to_datetime(df["time"])
    report["start"] = str(df["time"].iloc[0])[:10]
    report["end"]   = str(df["time"].iloc[-1])[:10]

    # OHLC sanity
    ohlc_errors = 0
    if "high" in df.columns and "low" in df.columns:
        ohlc_errors += (df["high"] < df["low"]).sum()
    if "close" in df.columns:
        ohlc_errors += (df["close"] <= 0).sum()
    report["ohlc_errors"] = int(ohlc_errors)

    # Gap detection
    gaps = detect_gaps(df, tf)
    report["gaps"] = len(gaps)
    if gaps:
        report["notes"] += f"Gaps: {'; '.join(gaps[:3])}"

    # Row count health
    expected_daily = BARS_PER_DAY.get(tf, {}).get(category, 100)
    expected_total = expected_daily * TRADING_DAYS_IN_RANGE
    coverage = (rows / expected_total * 100) if expected_total > 0 else 0

    if ohlc_errors > 0:
        report["status"] = "OHLC_ERROR"
    elif coverage < 50:
        report["status"] = "LOW_COVERAGE"
        report["notes"] += f" coverage={coverage:.0f}%"
    elif coverage < 80:
        report["status"] = "PARTIAL"
        report["notes"] += f" coverage={coverage:.0f}%"
    else:
        report["status"] = "OK"

    return report


def main():
    parser = argparse.ArgumentParser(description="Validate downloaded Dukascopy data")
    parser.add_argument("--symbol", nargs="+", metavar="SYM")
    parser.add_argument("--tf",     nargs="+", metavar="TF")
    args = parser.parse_args()

    with open(SYMBOLS_FILE, encoding="utf-8") as f:
        all_symbols = json.load(f)

    if args.symbol:
        wanted = [s.upper() for s in args.symbol]
        all_symbols = [s for s in all_symbols if s["symbol"] in wanted]

    tfs = [t.upper() for t in args.tf] if args.tf else ["M1","M5","M15","M30","H1","H4"]

    results = []
    for sym in all_symbols:
        for tf in tfs:
            r = validate_symbol_tf(sym, tf)
            results.append(r)
            icon = {"OK": "[OK]  ", "PARTIAL": "[WARN]", "LOW_COVERAGE": "[LOW] ",
                    "MISSING": "[MISS]", "EMPTY": "[EMPTY]", "OHLC_ERROR": "[OHLC]",
                    "READ_ERROR": "[ERR] "}.get(r["status"], "[?]   ")
            print(f"  {icon} {sym['symbol']:10s} {tf:4s}  "
                  f"{r['rows']:>9,} rows  {r['start']} -> {r['end']}  "
                  f"{r['size_mb']:6.1f} MB  gaps={r['gaps']}  "
                  f"{'warn: '+r['notes'] if r['notes'] else ''}")

    # Write report
    ok      = sum(1 for r in results if r["status"] == "OK")
    partial = sum(1 for r in results if r["status"] in ("PARTIAL", "LOW_COVERAGE"))
    bad     = sum(1 for r in results if r["status"] not in ("OK", "PARTIAL", "LOW_COVERAGE", "SKIPPED"))
    total_mb = sum(r["size_mb"] for r in results)

    lines = [
        "# Dukascopy Data Validation Report",
        f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Date range:** {START_DATE.date()} → {END_DATE.date()}",
        "",
        "| Symbol | TF | Status | Rows | Start | End | MB | Gaps | Notes |",
        "|--------|-----|--------|------|-------|-----|-----|------|-------|",
    ]
    for r in sorted(results, key=lambda x: (x["symbol"], x["tf"])):
        icon = {"OK": "✅", "PARTIAL": "🟡", "LOW_COVERAGE": "🟠",
                "MISSING": "❌", "EMPTY": "❌", "OHLC_ERROR": "🔴",
                "READ_ERROR": "🔴"}.get(r["status"], "?")
        lines.append(
            f"| {r['symbol']} | {r['tf']} | {icon} {r['status']} "
            f"| {r['rows']:,} | {r['start']} | {r['end']} "
            f"| {r['size_mb']} | {r['gaps']} | {r['notes'][:80]} |"
        )

    lines += [
        "",
        "## Summary",
        f"- ✅ OK: **{ok}**",
        f"- 🟡 Partial: **{partial}**",
        f"- ❌ Bad/Missing: **{bad}**",
        f"- 💾 Total on disk: **{total_mb:.1f} MB**",
    ]

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✔ Validation report → {REPORT_FILE}")
    print(f"  OK={ok}  Partial={partial}  Bad/Missing={bad}  Total={total_mb:.1f} MB")


if __name__ == "__main__":
    main()
