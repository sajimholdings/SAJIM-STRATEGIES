# Strategy: Short-Term Momentum Continuation

> **Instrument:** V100 (Volatility 100 Index) — Deriv Rise/Fall contracts  
> **Chart Timeframe:** 1-minute or 5-minute (not 1s tick — too noisy)

---

## Indicators

| Indicator | Setting |
|-----------|---------|
| EMA (fast) | 10-period |
| EMA (slow) | 30-period |
| RSI | 14-period |

---

## Entry Rules

### 📈 Higher (Call / Rise)

All three conditions must be true simultaneously:

- [ ] **10 EMA > 30 EMA** — short-term trend is up
- [ ] **RSI between 50 and 70** — momentum is present, not yet overbought
- [ ] **Price made a new local high in the last 5 candles**

### 📉 Lower (Put / Fall)

Mirror of the above — all three must be true:

- [ ] **10 EMA < 30 EMA** — short-term trend is down
- [ ] **RSI between 30 and 50** — momentum is present, not yet oversold
- [ ] **Price made a new local low in the last 5 candles**

---

## Why This Strategy

> *"Only trade in the direction of recent momentum, and only when that momentum isn't already exhausted."*

- **Mechanical yes/no** — no discretion, no "this looks like it's about to turn"
- **No indicator conflicts** — EMA gives direction, RSI filters exhaustion, local high/low confirms follow-through
- **Simple to backtest** — every rule is binary and unambiguous

---

## Contract Duration Selection

**Match your contract duration to the timeframe you're reading the signal on.**

| Signal Timeframe | Contract Duration |
|-----------------|------------------|
| 1-minute candles | ~1–2 minutes |
| 5-minute candles | ~5–10 minutes |

> ⚠️ **Do NOT** run a 5-minute Rise/Fall contract off a signal computed from 1-second noise. That's a timeframe mismatch — it kills most retail attempts at this approach.

---

## Backtesting — Step by Step

### 1. Pull Historical Data
Use Deriv's API — `ticks_history` call returns the raw tick series for V100 (1s).

```json
{
  "ticks_history": "R_100",
  "adjust_start_time": 1,
  "count": 5000,
  "end": "latest",
  "granularity": 60,
  "style": "candles"
}
```

> Set `granularity` to `60` for 1-min candles or `300` for 5-min candles.

### 2. Resample to Candles
If fetching raw ticks, resample to 1m or 5m OHLC yourself before computing indicators.

### 3. Compute Signals
Apply the EMA/RSI rules exactly as defined above to the historical OHLC data.

### 4. Evaluate Each Signal
For every signal bar, check:
- Did price **finish above** entry price after your chosen duration? → **Win (Higher)**
- Did price **finish below** entry price after your chosen duration? → **Win (Lower)**

### 5. Calculate Win Rate
```
Win Rate = (Winning Trades / Total Trades) × 100
```

### 6. Compare to Breakeven Threshold
From the payout structure, your breakeven win rate is approximately:

```
Breakeven Win Rate ≈ 52%
(based on ~95% payout — e.g. stake $10, win $9.50 profit)
```

> **Formula:** `Breakeven % = 1 / (1 + Payout Rate)`  
> Your strategy must win **more than 52%** of the time to be profitable.

---

## Quick Checklist Before Every Trade

```
[ ] Am I on the 1m or 5m chart? (not the 1s tick view)
[ ] 10 EMA and 30 EMA are on the chart and clearly crossed?
[ ] RSI is visible and the value is in the valid zone (50–70 or 30–50)?
[ ] New local high/low confirmed in the last 5 candles?
[ ] Contract duration matches my signal timeframe?
[ ] Win rate from backtest exceeds 52%?
```

---

## Notes

- **Do not override the rules.** If RSI is at 72 (overbought), skip the trade — even if it "looks good."
- **Backtest before live trading.** Intuition is not a substitute for historical verification on V100 specifically, which has its own volatility profile.
- This strategy does **not** include money management rules — apply a fixed stake or fixed % risk per trade separately.
