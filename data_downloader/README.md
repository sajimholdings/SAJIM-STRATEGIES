# Dukascopy Parallel Data Downloader

Downloads **2 years** of OHLCV bar data (M1, M5, M15, M30, H1, H4) for
**20 symbols** (forex majors, crosses, crypto, indices/commodities) from
Dukascopy's public CDN — **no API key required**.

## Quick Start

```powershell
cd "f:\Tete\SAJIM STRATEGIES\data_downloader"
.\setup.ps1               # setup venv + full download (10 workers)
```

## Options

```powershell
# Adjust worker count (higher = faster, but watch CDN throttling)
.\setup.ps1 -Workers 12

# Download specific symbols only
.\setup.ps1 -Symbols EURUSD,GBPUSD,BTCUSD

# Download specific timeframes only
.\setup.ps1 -Timeframes M5,H1

# Preview without downloading
.\setup.ps1 -DryRun

# Just set up the venv, don't download yet
.\setup.ps1 -SetupOnly
```

## Manual usage (after venv is set up)

```powershell
.\venv\Scripts\python download_all.py --workers 10
.\venv\Scripts\python download_all.py --workers 10 --tf M5 H1
.\venv\Scripts\python download_all.py --symbol EURUSD BTCUSD --tf H1
.\venv\Scripts\python validate_data.py
```

## Output Structure

```
f:\Tete\SAJIM STRATEGIES\data\dukascopy\
  EURUSD\
    M1.csv   H1.csv
    M5.csv   H4.csv
    M15.csv
    M30.csv
  GBPUSD\
    ...
  BTCUSD\
    ...
```

CSV columns: `time, open, high, low, close, volume`

## Symbols (20)

| Category | Symbols |
|---|---|
| Forex Majors | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD, EURGBP |
| Forex Crosses | GBPJPY, EURJPY |
| Crypto | BTCUSD, ETHUSD, LTCUSD, XRPUSD |
| Commodities | XAUUSD, XAGUSD |
| Indices | US500, US30, GER40, UK100 |

## Reports

- `download_report.md`   — status after download (rows, size, errors)
- `validation_report.md` — data quality (gaps, OHLC sanity, coverage)

## Re-running

The downloader is **idempotent** — it skips files that already exist.
To force re-download a symbol, delete its folder under `data\dukascopy\`.
