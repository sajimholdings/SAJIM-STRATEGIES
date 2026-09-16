# Trading System — Phase 0: Statistical Edge Detection

Two separate systems. Data decides which instruments to trade.

## System Architecture

```
System 1 (BTC)           System 2 (Forex/Gold)
─────────────            ─────────────────────
Binance API              MT5 / Yahoo Finance
BTC/USDT, ETH/USDT       GBPJPY, XAUUSD, GBPUSD
24/7                     Session-based
Python bot               Python bot (separate)
```

## Quick Start

```bash
pip install requests websockets numpy scipy statsmodels pandas yfinance

# Step 1: Collect BTC data (priority — run this first)
python collect_btc_data.py

# Step 2: Collect forex/gold data (for comparison)
python collect_forex_data.py

# Step 3: Run analysis on ALL collected data
python analyze_data.py
```

## What happens

1. `collect_btc_data.py` pulls 2 years of BTC + ETH from Binance (1H, 4H, 15M)
2. `collect_forex_data.py` pulls 2 years of GBPUSD, GBPJPY, EURUSD, Gold from Yahoo Finance (1H, 1D)
3. Both save CSVs to `./data/` in the same format
4. `analyze_data.py` runs all 7 statistical tests on every file

## Decision Framework

After running the analysis, we compare all instruments head-to-head:

| If BTC shows... | Then... |
|---|---|
| H > 0.6, significant ACF, vol clustering | **Build System 1 immediately** — strongest edge |
| H 0.55-0.6, some ACF significance | Worth testing but needs strategy validation |
| H ≈ 0.5, no ACF, no structure | Skip crypto, focus on forex/gold |

| If Forex/Gold shows... | Then... |
|---|---|
| Gold has vol clustering + session structure | Build System 2 around volatility regimes |
| GBPJPY trends better than GBPUSD | GBPJPY becomes the primary forex pair |
| Everything is H ≈ 0.5 | Rethink approach — maybe longer timeframes |

## What to send back

Copy-paste the full terminal output from `analyze_data.py` into Claude.
We'll read the results together and decide:
- Which instrument(s) get a system built
- What TYPE of strategy the data supports
- The exact next step

## File inventory

| File | Purpose |
|---|---|
| `collect_btc_data.py` | Pulls BTC/ETH from Binance public API (no key needed) |
| `collect_forex_data.py` | Pulls forex/gold from Yahoo Finance (no key needed) |
| `analyze_data.py` | Runs 7 statistical tests, produces verdicts |
| `data/` | Created automatically — holds all CSV files |
