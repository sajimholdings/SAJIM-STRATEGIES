#!/usr/bin/env python3
"""
Forex & Gold — Historical Data Collector (Yahoo Finance)
=========================================================
Pulls historical OHLCV data for forex pairs and gold using yfinance.
Free, no API key needed. Data quality is decent for 1H and above.

For higher-quality data (especially 5M/15M), export from MT5 instead.

Usage:
    pip install yfinance
    python collect_forex_data.py

Output: CSV files in ./data/ ready for analyze_data.py
"""

import os
import csv
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print("Install yfinance: pip install yfinance")
    exit(1)

# ─── Configuration ───────────────────────────────────────────────────────────

# Yahoo Finance tickers for forex/commodities
PAIRS = {
    "GBPUSD=X":  "GBP/USD",
    "GBPJPY=X":  "GBP/JPY",
    "EURUSD=X":  "EUR/USD",
    "GC=F":      "Gold Futures (XAU proxy)",
}

# yfinance interval options: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo
# Note: intraday data limited to 730 days for 1h, 60 days for <1h
TIMEFRAMES = {
    "1h": "1H",
    "1d": "1D",
}

DAYS_OF_HISTORY = 720  # ~2 years (max for 1h on Yahoo Finance)

OUTPUT_DIR = "data"

# ─── Core Logic ──────────────────────────────────────────────────────────────

def collect_pair(ticker: str, display_name: str, interval: str, tf_label: str):
    """Collect historical data for one pair at one timeframe."""
    
    # Clean up ticker for filename
    clean_name = ticker.replace("=X", "").replace("=F", "_FUT")
    filename = f"{OUTPUT_DIR}/{clean_name}_{tf_label}.csv"
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=DAYS_OF_HISTORY)
    
    print(f"\n{'='*60}")
    print(f"  {display_name} ({ticker}) — {tf_label}")
    print(f"  {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    
    try:
        data = yf.download(
            ticker,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"  ✗ Download failed: {e}")
        return 0
    
    if data.empty:
        print(f"  ✗ No data returned")
        return 0
    
    # Flatten multi-level columns if present
    if hasattr(data.columns, 'levels'):
        data.columns = [col[0] if isinstance(col, tuple) else col for col in data.columns]
    
    # Write CSV in the same format as our other collectors
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    count = 0
    
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["epoch", "datetime_utc", "open", "high", "low", "close"])
        
        for idx, row in data.iterrows():
            try:
                if hasattr(idx, 'timestamp'):
                    epoch = int(idx.timestamp())
                else:
                    epoch = int(idx.value / 1e9)
                
                dt = datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
                
                o = float(row.get("Open", row.iloc[0]))
                h = float(row.get("High", row.iloc[1]))
                l = float(row.get("Low", row.iloc[2]))
                c = float(row.get("Close", row.iloc[3]))
                
                # Skip rows with NaN
                if any(x != x for x in [o, h, l, c]):
                    continue
                
                writer.writerow([epoch, dt, round(o, 6), round(h, 6), round(l, 6), round(c, 6)])
                count += 1
            except (ValueError, TypeError, IndexError) as e:
                continue
    
    print(f"\n  ✓ Saved {count} candles → {filename}")
    return count


def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║    Forex & Gold — Historical Data Collector              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"\nPairs: {', '.join(PAIRS.values())}")
    print(f"Timeframes: {', '.join(TIMEFRAMES.values())}")
    print(f"History: {DAYS_OF_HISTORY} days")
    print(f"Output: ./{OUTPUT_DIR}/\n")
    
    total = 0
    for ticker, display_name in PAIRS.items():
        for interval, tf_label in TIMEFRAMES.items():
            try:
                count = collect_pair(ticker, display_name, interval, tf_label)
                total += count
            except Exception as e:
                print(f"  ✗ Error: {e}")
    
    print(f"\n{'='*60}")
    print(f"  COMPLETE — {total:,} total candles collected")
    print(f"  Files in ./{OUTPUT_DIR}/")
    print(f"{'='*60}")
    print(f"\nNext step: python analyze_data.py")


if __name__ == "__main__":
    main()
