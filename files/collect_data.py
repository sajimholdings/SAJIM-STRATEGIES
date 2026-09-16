#!/usr/bin/env python3
"""
Deriv Synthetic Indices — Historical Data Collector
====================================================
Connects to the Deriv WebSocket API and pulls historical OHLC candle data
for synthetic indices. Saves to CSV for statistical analysis.

Usage:
    pip install websockets
    python collect_data.py

No authentication required — ticks_history is a public endpoint.
"""

import asyncio
import json
import csv
import os
import time
from datetime import datetime, timedelta

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets")
    exit(1)

# ─── Configuration ───────────────────────────────────────────────────────────

APP_ID = 1089  # Test app_id. Register your own at https://developers.deriv.com/docs/app-registration/
WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

# Symbols to collect
SYMBOLS = {
    "R_75":     "Volatility 75 Index (2s ticks)",
    "R_100":    "Volatility 100 Index (2s ticks)",
    "1HZ100V":  "Volatility 100 (1s) Index",
    "1HZ75V":   "Volatility 75 (1s) Index",
    "BOOM1000":  "Boom 1000 Index",
    "CRASH1000": "Crash 1000 Index",
    "BOOM500":   "Boom 500 Index",
    "CRASH500":  "Crash 500 Index",
}

# Granularity options (seconds):
# 60=1M, 300=5M, 900=15M, 3600=1H, 14400=4H, 86400=1D
GRANULARITIES = {
    "1M":  60,
    "5M":  300,
    "15M": 900,
    "1H":  3600,
    "4H":  14400,
    "1D":  86400,
}

# What to collect — edit these to control scope
COLLECT_GRANULARITIES = ["1H", "5M"]  # Start with 1H and 5M
DAYS_OF_HISTORY = 180  # 6 months. Deriv may limit how far back you can go.
MAX_CANDLES_PER_REQUEST = 5000  # API limit per request
OUTPUT_DIR = "data"

# ─── Core Logic ──────────────────────────────────────────────────────────────

async def fetch_candles(ws, symbol: str, granularity: int, start: int, end: int, count: int = 5000) -> list:
    """Fetch a batch of candles from the Deriv API."""
    request = {
        "ticks_history": symbol,
        "style": "candles",
        "granularity": granularity,
        "start": start,
        "end": end,
        "count": count,
        "adjust_start_time": 1,
    }
    
    await ws.send(json.dumps(request))
    response = json.loads(await ws.recv())
    
    if "error" in response:
        print(f"  API Error: {response['error']['message']}")
        return []
    
    candles = response.get("candles", [])
    return candles


async def collect_symbol(symbol: str, display_name: str, granularity_label: str, granularity_secs: int):
    """Collect all historical data for one symbol at one granularity."""
    
    filename = f"{OUTPUT_DIR}/{symbol}_{granularity_label}.csv"
    
    # Calculate time range
    end_epoch = int(time.time())
    start_epoch = end_epoch - (DAYS_OF_HISTORY * 86400)
    
    print(f"\n{'='*60}")
    print(f"  {display_name} ({symbol}) — {granularity_label} candles")
    print(f"  Range: {datetime.utcfromtimestamp(start_epoch).strftime('%Y-%m-%d')} → {datetime.utcfromtimestamp(end_epoch).strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    
    all_candles = []
    current_end = end_epoch
    batch = 0
    
    async with websockets.connect(WS_URL) as ws:
        while current_end > start_epoch:
            batch += 1
            candles = await fetch_candles(ws, symbol, granularity_secs, start_epoch, current_end, MAX_CANDLES_PER_REQUEST)
            
            if not candles:
                print(f"  Batch {batch}: No data returned. Stopping.")
                break
            
            all_candles = candles + all_candles  # Prepend (older data first)
            oldest = candles[0]["epoch"]
            newest = candles[-1]["epoch"]
            
            print(f"  Batch {batch}: {len(candles)} candles | {datetime.utcfromtimestamp(oldest).strftime('%Y-%m-%d %H:%M')} → {datetime.utcfromtimestamp(newest).strftime('%Y-%m-%d %H:%M')}")
            
            if oldest <= start_epoch or len(candles) < 100:
                break
            
            # Move window back — next batch ends just before this batch's oldest
            current_end = oldest - 1
            await asyncio.sleep(0.5)  # Rate limiting
    
    # Deduplicate by epoch
    seen = set()
    unique_candles = []
    for c in all_candles:
        if c["epoch"] not in seen:
            seen.add(c["epoch"])
            unique_candles.append(c)
    
    unique_candles.sort(key=lambda c: c["epoch"])
    
    # Write CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["epoch", "datetime_utc", "open", "high", "low", "close"])
        for c in unique_candles:
            dt = datetime.utcfromtimestamp(c["epoch"]).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([c["epoch"], dt, c["open"], c["high"], c["low"], c["close"]])
    
    print(f"\n  ✓ Saved {len(unique_candles)} candles → {filename}")
    return len(unique_candles)


async def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║    Deriv Synthetic Indices — Data Collector              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"\nSymbols: {len(SYMBOLS)}")
    print(f"Granularities: {', '.join(COLLECT_GRANULARITIES)}")
    print(f"History: {DAYS_OF_HISTORY} days")
    print(f"Output: ./{OUTPUT_DIR}/\n")
    
    total = 0
    for symbol, display_name in SYMBOLS.items():
        for gran_label in COLLECT_GRANULARITIES:
            gran_secs = GRANULARITIES[gran_label]
            try:
                count = await collect_symbol(symbol, display_name, gran_label, gran_secs)
                total += count
            except Exception as e:
                print(f"  ✗ Error collecting {symbol} {gran_label}: {e}")
            
            await asyncio.sleep(1)  # Be polite to the API
    
    print(f"\n{'='*60}")
    print(f"  COMPLETE — {total:,} total candles collected")
    print(f"  Files in ./{OUTPUT_DIR}/")
    print(f"{'='*60}")
    print(f"\nNext step: python analyze_data.py")


if __name__ == "__main__":
    asyncio.run(main())
