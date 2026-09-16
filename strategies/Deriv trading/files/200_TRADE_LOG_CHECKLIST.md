# 200-Trade Log Analysis Checklist

When you hit 200 trades, extract these metrics from your logs. This is what I'll validate against the research profiling.

---

## Data to Extract (When You Send Logs)

### 1. Per-Asset Win Rate

**Extract from logs:**
```
# Count from terminal: [CLOSED] lines showing PnL

1HZ10V:  
  Total trades: ___
  Wins (PnL > 0): ___
  Losses (PnL < 0): ___
  Win Rate: ___ %

1HZ50V:
  Total trades: ___
  Wins (PnL > 0): ___
  Losses (PnL < 0): ___
  Win Rate: ___ %

1HZ75V:
  Total trades: ___
  Wins (PnL > 0): ___
  Losses (PnL < 0): ___
  Win Rate: ___ %
```

**Expected from research:**
```
1HZ10V: 54-58% WR (lower vol = easier to predict)
1HZ50V: 55-60% WR (balanced)
1HZ75V: 56-62% WR (higher vol but faster mean-reversion helps)
```

**Red flag:** If any asset is <52%, that asset's parameters need tuning.

---

### 2. Regime Distribution

**Extract from logs:**
```
# Count AI_UPDATE messages: [AI] Regime: X | Conf: Y

1HZ10V regimes detected:
  COMPRESSION: ___ % of updates
  CHOP:        ___ % of updates
  TREND:       ___ % of updates
  SPIKE:       ___ % of updates

1HZ50V regimes detected:
  COMPRESSION: ___ % of updates
  CHOP:        ___ % of updates
  TREND:       ___ % of updates
  SPIKE:       ___ % of updates

1HZ75V regimes detected:
  COMPRESSION: ___ % of updates
  CHOP:        ___ % of updates
  TREND:       ___ % of updates
  SPIKE:       ___ % of updates
```

**Expected from research:**
```
1HZ10V: COMPRESSION 40%, CHOP 35%, TREND 20%, SPIKE 5%
1HZ50V: CHOP 35%, TREND 40%, COMPRESSION 15%, SPIKE 10%
1HZ75V: SPIKE 25%, TREND 45%, CHOP 20%, COMPRESSION 10%
```

**If distribution doesn't match:** Lookback window size is wrong for that asset.

---

### 3. Average Confidence Over Time

**Extract:**
```
Trades 1-50 average confidence:   ___ %
Trades 51-100 average confidence: ___ %
Trades 101-150 average confidence: ___ %
Trades 151-200 average confidence: ___ %
```

**Expected:** Trend upward (starts ~67%, ends ~76%+)

**Red flag:** If stuck at 67% after 100 trades, MIN_CONFIDENCE threshold is still too high.

---

### 4. Barrier Distance by Asset

**Extract:**
```
1HZ10V trades:
  Average barrier distance: ___ pips
  Min: ___ pips
  Max: ___ pips

1HZ50V trades:
  Average barrier distance: ___ pips
  Min: ___ pips
  Max: ___ pips

1HZ75V trades:
  Average barrier distance: ___ pips
  Min: ___ pips
  Max: ___ pips
```

**Expected from research:**
```
1HZ10V: 1.5-2.0 pips average
1HZ50V: 3.8-4.5 pips average
1HZ75V: 2.0-2.5 pips average
```

**If not matching:** Barrier distance calculation needs adjustment (multiply by scaling factor).

---

### 5. Trade Frequency by Asset

**Extract:**
```
1HZ10V: ___ trades / hour
1HZ50V: ___ trades / hour
1HZ75V: ___ trades / hour
```

**Expected:** 10-15 trades/hour per asset (30-45 total/hour)

**If lower:** Confidence threshold still too high (lower to 0.60).

---

### 6. Session PnL by Phase

**Extract:**
```
Trades 1-50:
  Wins: ___
  Losses: ___
  Net PnL: $___
  Win Rate: ___%

Trades 51-100:
  Wins: ___
  Losses: ___
  Net PnL: $___
  Win Rate: ___%

Trades 101-150:
  Wins: ___
  Losses: ___
  Net PnL: $___
  Win Rate: ___%

Trades 151-200:
  Wins: ___
  Losses: ___
  Net PnL: $___
  Win Rate: ___%
```

**Expected:** Slight upward trend (not straight up, expect variance)
```
Phase 1: -$0.50 to +$1.00
Phase 2: -$0.25 to +$1.50
Phase 3: +$0.25 to +$2.00
Phase 4: +$0.50 to +$2.50
```

**Red flag:** Consistent losses in any phase = regime detection failing.

---

### 7. Consecutive Loss Streaks

**Extract:**
```
Max consecutive losses seen: ___
How many times cooldown triggered: ___
Average cooldown duration: ___ seconds
```

**Expected:** 
- Max streak: 3-4 (cooldown kicks in at 3)
- Cooldown triggered: 5-10 times in 200 trades
- No streaks > 4 (safeguard working)

**Red flag:** Streaks > 5 = fakeout detection (gamma) is not working.

---

### 8. Velocity & Acceleration Characteristics

**Extract from logs (if logging this):**
```
Average velocity (absolute value):
  1HZ10V: ___ pips/tick
  1HZ50V: ___ pips/tick
  1HZ75V: ___ pips/tick

Average acceleration (absolute value):
  1HZ10V: ___ pips/tick²
  1HZ50V: ___ pips/tick²
  1HZ75V: ___ pips/tick²
```

**Expected (from research):**
```
Velocity should scale 1:5:7.5 ratio
1HZ10V:  ~0.2-0.3 pips/tick
1HZ50V:  ~1.0-1.5 pips/tick
1HZ75V:  ~1.5-2.3 pips/tick
```

**If not matching:** Velocity normalization in GARCH or barrier calc is off.

---

## Format to Send Me

When you hit 200 trades, send:

```
==== 200-TRADE LOG SUMMARY ====

SESSION: 200 trades completed
START TIME: ___
END TIME: ___
TOTAL PnL: $___
OVERALL WIN RATE: ___%

PER-ASSET BREAKDOWN:
1HZ10V: ___ trades, __% WR, Avg barrier: ___ pips
1HZ50V: ___ trades, __% WR, Avg barrier: ___ pips
1HZ75V: ___ trades, __% WR, Avg barrier: ___ pips

REGIME DISTRIBUTION (1HZ75V example):
  COMPRESSION: __%, CHOP: __%, TREND: __%, SPIKE: __%

CONFIDENCE TREND:
  Trades 1-50:     __% avg
  Trades 51-100:   __% avg
  Trades 101-150:  __% avg
  Trades 151-200:  __% avg

CONSECUTIVE LOSS STREAKS:
  Max streak: ___
  Cooldowns triggered: ___

TRADE FREQUENCY:
  Total trades/hour: ___
  Per asset breakdown: 1HZ10V ___, 1HZ50V ___, 1HZ75V ___

COPY OF LAST 50 LINES OF TERMINAL LOG:
[Terminal output here]
```

---

## What I'll Check Against Research

When you send the data, I'll validate:

✅ **Regime Distribution Matches Research Profiling**
- 1HZ10V shows high compression propensity?
- 1HZ75V shows frequent spike detection?
- 1HZ50V balanced between both?

✅ **Win Rate Aligns with Asset Volatility**
- Higher vol assets trending toward higher WR?
- Lower vol assets more stable?
- All assets above 52% (breakeven)?

✅ **Barrier Distances Follow Research**
- 1HZ10V shortest barriers?
- 1HZ75V medium barriers?
- 1HZ50V longest barriers (high nominal price)?

✅ **Confidence Improving Over Time**
- Started ~67%, trending toward 75%+?
- Indicates HMM is converging to correct regimes?

✅ **Mean-Reversion Speed Correct**
- Fakeout filter preventing excessive consecutive losses?
- 1HZ75V faster recovery than 1HZ10V?

✅ **No Systematic Failures**
- One asset consistently profitable?
- One asset consistently losing?
- Or all three roughly balanced?

---

## If Something's Wrong

**Problem: Win rate < 52% on all assets**
→ Barrier distance calculation wrong, or physics model needs work

**Problem: Regime distribution doesn't match research**
→ Lookback window size needs adjustment for that asset

**Problem: Consecutive losses > 5**
→ ACCELERATION_DECAY_GAMMA needs to be tighter (lower value)

**Problem: Barrier distances uniform across assets**
→ stateManager not reading asset-specific config

**Problem: Confidence still stuck at 67%**
→ HMM n_components or GARCH forecast is bad, lower MIN_CONFIDENCE to 0.60

---

## Send Format

Reply with:
1. The 8 data points above (copy-paste into markdown table)
2. Last 100 lines of terminal output (showing recent trades)
3. Any errors or warnings you saw

Then I'll:
1. Validate against research profiling ✓
2. Identify which asset(s) have issues
3. Provide specific tuning adjustments
4. Estimate achievable win rate / session ROI

Let's go. 🚀
