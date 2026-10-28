# MCP_Scalper_Sajim (XAUUSD M5)

> **EMA Pullback Micro-Scalper** for MetaTrader 5, engineered and verified through the SAJIM Quant automated MT5 pipeline.

---

## Strategy Overview

`MCP_Scalper_Sajim` is a clean, self-contained trend-following pullback scalper designed specifically for high-volatility spot commodities like Gold (**XAUUSD**) on the **5-Minute (M5)** timeframe.

### Core Logic

1. **Trend Filter**:
   - Long when price > $EMA(50)$
   - Short when price < $EMA(50)$

2. **Pullback Signal**:
   - Price touches or crosses $EMA(21)$ and closes back in the direction of the dominant trend.
   - Longs require candle low $\le EMA(21)$ and candle close $> EMA(21)$ with a bullish close.
   - Shorts require candle high $\ge EMA(21)$ and candle close $< EMA(21)$ with a bearish close.

3. **Momentum Confirmation**:
   - $RSI(14)$ must be in the mean-reversion pullback band ($40.0 - 60.0$). This ensures entries occur during consolidation/exhaustion rather than overbought (>70) or oversold (<30) extremes.

4. **Dynamic Volatility Risk Management**:
   - **Stop Loss**: $1.0 \times ATR(14)$ (Base 1R Risk)
   - **Take Profit**: $3.0 \times ATR(14)$ (True 1:3 R:R Target)
   - **Breakeven Trigger**: At $+1.5R$ in unrealized profit, Stop Loss is moved to Entry Price $+ 10$ points buffer to eliminate risk without premature choking.
   - **Trailing Stop**: Disabled (`InpTrailStart_ATR = 0.0`) to let winners reach the full 3R target without premature clipping.

5. **Execution Filters**:
   - **Spread Guard**: Max spread $\le 50$ points (5.0 pips for Gold).
   - **Session Window**: Configurable (0–24 for 24/7 scalping, or 10–18 for London/NY liquidity).
   - **Daily Trade Throttle**: Configurable (0 = Unlimited, or 3–5 to prevent overtrading).

---

## Directory Structure

```
strategies/MCP_Scalper_Sajim/
├── MCP_Scalper_Sajim.mq5      # Single-file MQL5 EA source code
├── sajim_strategy.yaml        # Universal SAJIM strategy manifest
├── README.md                  # Strategy documentation and architecture
└── results/                   # Harvested backtest benchmarks
    ├── backtest_2026.json     # SAJIM universal schema trade log & metrics
    ├── tear_sheet.md          # Quantitative performance tear sheet
    └── trades.csv             # Raw harvested trade-by-trade log
```

---

## Technical Specifications

| Parameter | Default | Description |
|:---|:---|:---|
| `InpEMA_Trend` | 50 | Higher timeframe trend direction EMA |
| `InpEMA_Signal` | 21 | Pullback entry trigger EMA |
| `InpRSI_Period` | 14 | Relative Strength Index calculation period |
| `InpRSI_Lower` | 40.0 | Pullback zone lower bound |
| `InpRSI_Upper` | 60.0 | Pullback zone upper bound |
| `InpLotSize` | 0.10 | Fixed execution lot size |
| `InpSL_ATR_Mult` | 1.0 | Stop loss distance in ATR multiples (1R risk) |
| `InpTP_ATR_Mult` | 3.0 | Take profit distance in ATR multiples (3R reward) |
| `InpBreakeven_R` | 1.5 | Profit in R multiples to trigger breakeven SL |
| `InpBE_Buffer_Points`| 10 | Profit buffer in points locked on breakeven |
| `InpATR_Period` | 14 | Volatility measurement period |
| `InpTrailStart_ATR`| 0.0 | Trailing stop trigger (0 = Disabled for full 3R targets) |
| `InpUseHTF_Filter` | true | Enable Higher Timeframe trend direction filter |
| `InpHTF_Timeframe` | PERIOD_H1 | Higher Timeframe chart period |
| `InpHTF_EMA_Period`| 100 | HTF Trend EMA period (e.g. 100) |
| `InpMaxTradesPerDay`| 0 | Daily execution throttle (0 = Unlimited) |
| `InpMaxSpreadPoints`| 50 | Maximum allowable spread in points |
| `InpSessionStart` | 0 | Earliest trade hour (0 = 24/7) |
| `InpSessionEnd` | 24 | Latest trade hour (24 = 24/7) |
| `InpMagicNumber` | 20261006 | Unique expert advisor magic identifier |

---

## Benchmarks & Performance Comparison

### Benchmark 1: Full 1-Year Dataset with H1 EMA 100 Filter (2025.10.01 – 2026.10.02)
- **Settings**: H1 EMA 100 Filter, SL 1.0 ATR, TP 3.0 ATR, Breakeven at +1.5R, 24/7 trading
- **Total Trades**: **2,806 trades** across 257 trading days (~10.9 trades/day)
- **Initial Balance**: **$10,000.00** | **Final Balance**: **$18,149.80**
- **Total Net Profit**: **+$8,149.80** (**+81.50% Return on $10,000**)
- **CAGR**: **81.28%**
- **Win Rate**: **41.9%** (1,175 wins / 1,631 losses)
- **Profit Factor**: **1.09**
- **Realized Win/Loss Ratio**: **1.51 : 1** (Avg Win: $85.89 vs Avg Loss: $56.88)
- **Sharpe Ratio (Annualized)**: **1.38**
- **Sortino Ratio**: **2.30**
- **Calmar Ratio**: **3.72**
- **Max Drawdown**: **21.87%** ($4,180.60)
- **Recovery Factor**: **1.95**
- **Expectancy per Trade**: **+$2.90**
- **Full TP (3R) Target Hits**: 874 trades
- **Profitable Months**: 8 out of 12 full months profitable

### Benchmark 2: 9-Month Test with H1 EMA 100 Filter (2026.01.05 – 2026.10.02)
- **Total Trades**: 2,060 trades
- **Win Rate**: 41.8%
- **Net Profit**: +$5,522.20 (+55.22%)
- **Sharpe Ratio**: 1.17
- **Max Drawdown**: 25.36%

### Benchmark 3: Baseline 1:3 R:R without HTF Filter (2026.01.05 – 2026.10.02)
- **Total Trades**: 3,310 trades
- **Win Rate**: 41.3%
- **Net Profit**: +$1,416.98 (+14.17%)
- **Sharpe Ratio**: 0.25
- **Max Drawdown**: 33.69%

### Benchmark 4: Original Asymmetric Setup (SL 1.5 ATR / TP 2.0 ATR / Trailing @ 1.0 ATR)
- **Total Trades**: 3,088 trades
- **Win Rate**: 60.8%
- **Net Profit**: -$1,727.20 (full dataset)
- **Root Cause of Deficit**: Trailing stop choked winners early at +1.0 ATR, while losers took the full $-1.5 ATR$ hit, inverting the Win/Loss payout ratio to 0.63 : 1.
