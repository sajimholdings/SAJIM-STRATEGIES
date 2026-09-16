# Deriv Synthetic Indices — Edge Detector

Determines whether Deriv synthetic indices have exploitable statistical structure or are pure random walks.

## Why this matters

If the price generator has memory/structure → we can build a profitable bot around that structure.  
If it's a pure random walk → no strategy will work long-term, and we go back to GBPUSD.

## Setup

```bash
# Install dependencies
pip install websockets numpy scipy statsmodels pandas

# 1. Collect historical data (takes 5-10 minutes)
python collect_data.py

# 2. Run statistical analysis
python analyze_data.py
```

## What `collect_data.py` does

- Connects to Deriv's public WebSocket API (no account needed)
- Pulls 6 months of 1H and 5M candles for 8 synthetic indices
- Saves to `./data/` as CSV files
- Uses test `app_id=1089`. For production, register at https://developers.deriv.com/

## What `analyze_data.py` tests

| Test | What it answers | Exploitable if... |
|------|----------------|-------------------|
| **Hurst Exponent** | Does the series trend or mean-revert? | H > 0.6 (trending) or H < 0.4 (mean-reverting) |
| **Autocorrelation** | Do past returns predict future returns? | Multiple significant lags |
| **Runs Test** | Is the up/down sequence random? | p-value < 0.05 |
| **Distribution** | Fat tails? Skewness? | High kurtosis, significant skew |
| **ARCH Effect** | Does volatility cluster? | p-value < 0.05 (vol is predictable) |
| **Spike Analysis** | Is Crash/Boom spike timing random? | Non-Poisson intervals |
| **Stationarity** | Is behavior stable over time? | Stationary = safe to automate |

## Reading the output

Each instrument gets a verdict:

- 🟢 **STRONG SIGNAL** (3+ indicators) — Build a strategy around this
- 🟡 **MODERATE SIGNAL** (2 indicators) — Worth deeper testing
- 🟠 **WEAK SIGNAL** (1 indicator) — Probably not enough
- 🔴 **NO SIGNAL** — Random walk, skip it

## What to do with results

**If Vol 75/100 shows H ≈ 0.5 and no significant ACF:**  
→ Pure random walk. Don't waste time. Go to GBPUSD.

**If Vol 75/100 shows H > 0.6 with significant ACF:**  
→ Trend-following strategy. Simple MA crossover may actually work.

**If Crash/Boom shows non-Poisson spike timing:**  
→ Spike prediction strategy. Entry before predicted spike window.

**If any index shows strong volatility clustering:**  
→ Regime-based sizing. Trade normally but size up in low-vol → high-vol transitions.

## Customization

In `collect_data.py`, edit:
- `SYMBOLS` — add/remove indices
- `COLLECT_GRANULARITIES` — try "1D", "4H", "15M", "1M"
- `DAYS_OF_HISTORY` — increase for more data (max depends on Deriv's retention)

## Next steps

After running this analysis, share the terminal output with Claude.  
We'll decide together which instruments to pursue and which strategy to design.
