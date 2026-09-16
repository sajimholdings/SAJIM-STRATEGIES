#!/usr/bin/env python3
"""
BTC Market Data Collector — Binance Public API
================================================
Pulls historical OHLCV candle data for BTC and other crypto pairs
from Binance's public REST API. No API key or account needed.

Usage:
    pip install requests
    python collect_btc_data.py

Output: CSV files in ./data/ ready for analyze_data.py
"""

import os
import csv
import time
import requests
from datetime import datetime, timedelta

# ─── Configuration ───────────────────────────────────────────────────────────

# Pairs to collect (Binance symbol format)
PAIRS = {
    "BTCUSDT":  "Bitcoin / USDT",
    "ETHUSDT":  "Ethereum / USDT",
}

# Timeframes to collect
TIMEFRAMES = {
    "1h":  "1H",   # 1 hour
    "4h":  "4H",   # 4 hours
    "15m": "15M",  # 15 minutes (for higher-frequency analysis)
}

# How far back to collect (Binance has data going back years)
DAYS_OF_HISTORY = 730  # 2 years

# Binance public API (no auth needed)
BASE_URL = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"
MAX_CANDLES_PER_REQUEST = 1000  # Binance limit

OUTPUT_DIR = "data"

# ─── Core Logic ──────────────────────────────────────────────────────────────

def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> list:
    """
    Fetch klines (candlestick data) from Binance.
    
    Returns list of:
    [open_time, open, high, low, close, volume, close_time, 
     quote_volume, trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    
    response = requests.get(f"{BASE_URL}{KLINES_ENDPOINT}", params=params, timeout=30)
    
    if response.status_code == 429:
        # Rate limited — wait and retry
        print("    Rate limited, waiting 60s...")
        time.sleep(60)
        return fetch_klines(symbol, interval, start_ms, end_ms, limit)
    
    response.raise_for_status()
    return response.json()


def collect_pair(symbol: str, display_name: str, interval: str, tf_label: str):
    """Collect all historical data for one pair at one timeframe."""
    
    filename = f"{OUTPUT_DIR}/{symbol}_{tf_label}.csv"
    
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (DAYS_OF_HISTORY * 86400 * 1000)
    
    print(f"\n{'='*60}")
    print(f"  {display_name} ({symbol}) — {tf_label}")
    print(f"  {datetime.utcfromtimestamp(start_ms/1000).strftime('%Y-%m-%d')} → {datetime.utcfromtimestamp(end_ms/1000).strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    
    all_candles = []
    current_start = start_ms
    batch = 0
    
    while current_start < end_ms:
        batch += 1
        try:
            klines = fetch_klines(symbol, interval, current_start, end_ms, MAX_CANDLES_PER_REQUEST)
        except requests.exceptions.RequestException as e:
            print(f"  Batch {batch}: Error — {e}")
            time.sleep(5)
            continue
        
        if not klines:
            break
        
        all_candles.extend(klines)
        
        oldest_dt = datetime.utcfromtimestamp(klines[0][0] / 1000).strftime('%Y-%m-%d %H:%M')
        newest_dt = datetime.utcfromtimestamp(klines[-1][0] / 1000).strftime('%Y-%m-%d %H:%M')
        print(f"  Batch {batch}: {len(klines)} candles | {oldest_dt} → {newest_dt}")
        
        # Move start forward to after the last candle
        current_start = klines[-1][0] + 1
        
        if len(klines) < MAX_CANDLES_PER_REQUEST:
            break  # We've reached the end
        
        time.sleep(0.2)  # Be polite to the API
    
    # Deduplicate by open_time
    seen = set()
    unique = []
    for k in all_candles:
        if k[0] not in seen:
            seen.add(k[0])
            unique.append(k)
    
    unique.sort(key=lambda k: k[0])
    
    # Write CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "epoch", "datetime_utc", "open", "high", "low", "close",
            "volume", "quote_volume", "num_trades", "taker_buy_volume"
        ])
        for k in unique:
            epoch = int(k[0] / 1000)  # Convert ms to seconds
            dt = datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([
                epoch, dt,
                float(k[1]),  # open
                float(k[2]),  # high
                float(k[3]),  # low
                float(k[4]),  # close
                float(k[5]),  # volume
                float(k[7]),  # quote volume
                int(k[8]),    # number of trades
                float(k[9]),  # taker buy base volume
            ])
    
    print(f"\n  ✓ Saved {len(unique)} candles → {filename}")
    return len(unique)


def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║    BTC/Crypto — Historical Data Collector (Binance)      ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"\nPairs: {', '.join(PAIRS.keys())}")
    print(f"Timeframes: {', '.join(TIMEFRAMES.values())}")
    print(f"History: {DAYS_OF_HISTORY} days ({DAYS_OF_HISTORY/365:.1f} years)")
    print(f"Output: ./{OUTPUT_DIR}/\n")
    
    total = 0
    for symbol, display_name in PAIRS.items():
        for interval, tf_label in TIMEFRAMES.items():
            try:
                count = collect_pair(symbol, display_name, interval, tf_label)
                total += count
            except Exception as e:
                print(f"  ✗ Error: {e}")
            
            time.sleep(1)
    
    print(f"\n{'='*60}")
    print(f"  COMPLETE — {total:,} total candles collected")
    print(f"  Files in ./{OUTPUT_DIR}/")
    print(f"{'='*60}")
    print(f"\nNext step: python analyze_data.py")


if __name__ == "__main__":
    main()
