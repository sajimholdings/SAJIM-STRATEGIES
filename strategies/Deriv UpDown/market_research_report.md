# Deriv Volatility Index — Market Research Report

> **Generated:** 2026-06-24 05:16:51 UTC  
> **Strategy:** EMA(10/30) + RSI(14) Momentum Continuation  
> **Breakeven Win Rate:** 52.0% (based on ~95% payout)  
> **Data:** ~17 days per symbol at 5m equivalent  
> **Combinations tested:** 10 symbols × 4 timeframes = 40

---

## 1. Executive Summary

| Category | Count |
|----------|-------|
| ✅ Profitable combinations (WR ≥ 52%) | **15** |
| 〰️ Near breakeven (50–52%) | 17 |
| ❌ Below 50% | 8 |
| ⚠️ Skipped (error / insufficient data) | 0 |

> **Top performer:** `1HZ50V` on `1m` candles — **56.2%** win rate at +10 candle look-ahead (1263 signals over 3.5 days)

---

## 2. Full Rankings — All Combinations

Sorted by best win rate across all look-ahead durations.

| Rank | Symbol | Name | TF | Signals | Sig/Day | Best WR | @Ahead | CALL WR | PUT WR | Max Loss Streak | Status |
|------|--------|------|----|---------|---------|---------|--------|---------|--------|-----------------|--------|
| 1 | `1HZ50V` | Volatility 50 (1s) Index | 1m | 1263 | 363.8 | **56.2%** | +10 | 59.0% | 52.5% | 14 | ✅ Profitable |
| 2 | `R_25` | Volatility 25 Index | 5m | 1395 | 80.4 | **54.7%** | +10 | 50.4% | 59.1% | 14 | ✅ Profitable |
| 3 | `R_25` | Volatility 25 Index | 15m | 1353 | 26 | **54.3%** | +10 | 51.3% | 56.9% | 18 | ✅ Profitable |
| 4 | `1HZ75V` | Volatility 75 (1s) Index | 1h | 1353 | 6.5 | **54.3%** | +10 | 55.5% | 53.2% | 12 | ✅ Profitable |
| 5 | `1HZ25V` | Volatility 25 (1s) Index | 5m | 1347 | 77.6 | **54.3%** | +3 | 56.2% | 51.6% | 11 | ✅ Profitable |
| 6 | `1HZ25V` | Volatility 25 (1s) Index | 1m | 1327 | 382.3 | **53.7%** | +5 | 52.9% | 54.5% | 19 | ✅ Profitable |
| 7 | `R_75` | Volatility 75 Index | 1h | 1336 | 6.4 | **53.1%** | +5 | 53.9% | 52.2% | 13 | ✅ Profitable |
| 8 | `R_10` | Volatility 10 Index | 1h | 1404 | 6.7 | **52.9%** | +10 | 50.7% | 54.9% | 18 | ✅ Profitable |
| 9 | `R_10` | Volatility 10 Index | 15m | 1299 | 24.9 | **52.7%** | +10 | 51.4% | 53.8% | 16 | ✅ Profitable |
| 10 | `1HZ10V` | Volatility 10 (1s) Index | 1m | 1321 | 380.5 | **52.6%** | +10 | 51.5% | 53.5% | 11 | ✅ Profitable |
| 11 | `R_10` | Volatility 10 Index | 1m | 1292 | 372.2 | **52.4%** | +3 | 53.4% | 51.2% | 11 | ✅ Profitable |
| 12 | `1HZ10V` | Volatility 10 (1s) Index | 1h | 1313 | 6.3 | **52.3%** | +1 | 53.5% | 51.2% | 13 | ✅ Profitable |
| 13 | `1HZ75V` | Volatility 75 (1s) Index | 5m | 1279 | 73.7 | **52.3%** | +10 | 53.2% | 51.4% | 16 | ✅ Profitable |
| 14 | `1HZ100V` | Volatility 100 (1s) Index | 1m | 1314 | 378.5 | **52.3%** | +10 | 52.1% | 52.5% | 17 | ✅ Profitable |
| 15 | `R_75` | Volatility 75 Index | 1m | 1357 | 390.9 | **52.2%** | +1 | 51.1% | 53.2% | 9 | ✅ Profitable |
| 16 | `1HZ50V` | Volatility 50 (1s) Index | 15m | 1405 | 27 | **52.0%** | +1 | 52.3% | 51.6% | 8 | 〰️ Near |
| 17 | `R_100` | Volatility 100 Index | 5m | 1352 | 77.9 | **51.8%** | +5 | 50.8% | 52.9% | 15 | 〰️ Near |
| 18 | `1HZ50V` | Volatility 50 (1s) Index | 1h | 1370 | 6.6 | **51.8%** | +1 | 51.1% | 52.6% | 9 | 〰️ Near |
| 19 | `R_25` | Volatility 25 Index | 1m | 1334 | 384.3 | **51.5%** | +3 | 50.7% | 52.3% | 11 | 〰️ Near |
| 20 | `1HZ50V` | Volatility 50 (1s) Index | 5m | 1297 | 74.7 | **51.4%** | +3 | 53.5% | 49.2% | 9 | 〰️ Near |
| 21 | `R_25` | Volatility 25 Index | 1h | 1312 | 6.3 | **51.4%** | +5 | 48.7% | 53.9% | 11 | 〰️ Near |
| 22 | `1HZ75V` | Volatility 75 (1s) Index | 1m | 1367 | 393.8 | **51.2%** | +1 | 50.4% | 51.8% | 9 | 〰️ Near |
| 23 | `R_100` | Volatility 100 Index | 15m | 1330 | 25.5 | **50.9%** | +10 | 47.6% | 53.4% | 24 | 〰️ Near |
| 24 | `1HZ75V` | Volatility 75 (1s) Index | 15m | 1244 | 23.9 | **50.9%** | +1 | 51.7% | 50.1% | 7 | 〰️ Near |
| 25 | `R_75` | Volatility 75 Index | 15m | 1327 | 25.5 | **50.8%** | +10 | 54.1% | 47.2% | 13 | 〰️ Near |
| 26 | `1HZ100V` | Volatility 100 (1s) Index | 15m | 1295 | 24.9 | **50.4%** | +1 | 50.0% | 50.8% | 11 | 〰️ Near |
| 27 | `1HZ10V` | Volatility 10 (1s) Index | 15m | 1329 | 25.5 | **50.4%** | +1 | 49.5% | 51.1% | 10 | 〰️ Near |
| 28 | `1HZ25V` | Volatility 25 (1s) Index | 15m | 1312 | 25.2 | **50.4%** | +10 | 51.7% | 48.9% | 17 | 〰️ Near |
| 29 | `1HZ100V` | Volatility 100 (1s) Index | 5m | 1309 | 75.4 | **50.3%** | +5 | 49.1% | 51.6% | 14 | 〰️ Near |
| 30 | `R_100` | Volatility 100 Index | 1h | 1357 | 6.5 | **50.3%** | +1 | 48.3% | 51.9% | 10 | 〰️ Near |
| 31 | `R_50` | Volatility 50 Index | 1m | 1383 | 398.4 | **50.0%** | +10 | 52.9% | 47.4% | 23 | 〰️ Near |
| 32 | `R_50` | Volatility 50 Index | 15m | 1354 | 26 | **50.0%** | +1 | 51.0% | 49.2% | 10 | 〰️ Near |
| 33 | `1HZ10V` | Volatility 10 (1s) Index | 5m | 1363 | 78.5 | **49.9%** | +5 | 47.5% | 51.6% | 12 | ❌ Below |
| 34 | `R_10` | Volatility 10 Index | 5m | 1264 | 72.8 | **49.8%** | +5 | 50.0% | 49.7% | 12 | ❌ Below |
| 35 | `1HZ100V` | Volatility 100 (1s) Index | 1h | 1367 | 6.6 | **49.5%** | +1 | 50.6% | 48.3% | 10 | ❌ Below |
| 36 | `R_75` | Volatility 75 Index | 5m | 1250 | 72 | **49.3%** | +3 | 51.2% | 47.1% | 11 | ❌ Below |
| 37 | `R_100` | Volatility 100 Index | 1m | 1306 | 376.2 | **49.2%** | +3 | 49.1% | 49.4% | 13 | ❌ Below |
| 38 | `R_50` | Volatility 50 Index | 5m | 1361 | 78.4 | **49.0%** | +3 | 45.5% | 52.1% | 13 | ❌ Below |
| 39 | `1HZ25V` | Volatility 25 (1s) Index | 1h | 1312 | 6.3 | **48.3%** | +3 | 47.7% | 49.0% | 20 | ❌ Below |
| 40 | `R_50` | Volatility 50 Index | 1h | 1337 | 6.4 | **47.5%** | +1 | 46.3% | 48.6% | 10 | ❌ Below |

---

## 3. Results by Volatility Index

### Volatility 10 Index (`R_10`)

**Best timeframe:** `1h` — 52.9% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1292 | 372.2 | 51.2% | 52.4% | 51.5% | 50.9% | **52.4%** (+3) |
| 5m | 1264 | 72.8 | 48.7% | 49.0% | 49.8% | 48.8% | **49.8%** (+5) |
| 15m | 1299 | 24.9 | 49.0% | 52.1% | 50.7% | 52.7% | **52.7%** (+10) |
| 1h | 1404 | 6.7 | 50.3% | 51.1% | 52.6% | 52.9% | **52.9%** (+10) |

**Best look-ahead detail (+10 candles on 1h):**
- CALL win rate: 50.7% (661 CALL signals)
- PUT win rate: 54.9% (743 PUT signals)
- Max consecutive losses: 18
- Data: 2025-11-27 22:00 → 2026-06-24 05:00 (208.3 days)

### Volatility 25 Index (`R_25`)

**Best timeframe:** `5m` — 54.7% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1334 | 384.3 | 51.1% | 51.5% | 51.0% | 51.5% | **51.5%** (+3) |
| 5m | 1395 | 80.4 | 49.1% | 49.1% | 52.1% | 54.7% | **54.7%** (+10) |
| 15m | 1353 | 26 | 49.6% | 50.7% | 52.2% | 54.3% | **54.3%** (+10) |
| 1h | 1312 | 6.3 | 50.3% | 51.2% | 51.4% | 48.7% | **51.4%** (+5) |

**Best look-ahead detail (+10 candles on 5m):**
- CALL win rate: 50.4% (699 CALL signals)
- PUT win rate: 59.1% (696 PUT signals)
- Max consecutive losses: 14
- Data: 2026-06-06 20:35 → 2026-06-24 05:10 (17.4 days)

### Volatility 50 Index (`R_50`)

**Best timeframe:** `1m` — 50.0% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1383 | 398.4 | 49.3% | 48.4% | 48.8% | 50.0% | **50.0%** (+10) |
| 5m | 1361 | 78.4 | 47.8% | 49.0% | 48.7% | 46.7% | **49.0%** (+3) |
| 15m | 1354 | 26 | 50.0% | 48.2% | 48.6% | 47.0% | **50.0%** (+1) |
| 1h | 1337 | 6.4 | 47.5% | 46.2% | 44.7% | 46.9% | **47.5%** (+1) |

**Best look-ahead detail (+10 candles on 1m):**
- CALL win rate: 52.9% (662 CALL signals)
- PUT win rate: 47.4% (721 PUT signals)
- Max consecutive losses: 23
- Data: 2026-06-20 17:55 → 2026-06-24 05:14 (3.5 days)

### Volatility 75 Index (`R_75`)

**Best timeframe:** `1h` — 53.1% win rate at +5 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1357 | 390.9 | 52.2% | 50.3% | 47.7% | 47.2% | **52.2%** (+1) |
| 5m | 1250 | 72 | 48.7% | 49.3% | 48.6% | 47.4% | **49.3%** (+3) |
| 15m | 1327 | 25.5 | 48.3% | 48.3% | 50.3% | 50.8% | **50.8%** (+10) |
| 1h | 1336 | 6.4 | 52.0% | 52.1% | 53.1% | 51.9% | **53.1%** (+5) |

**Best look-ahead detail (+5 candles on 1h):**
- CALL win rate: 53.9% (713 CALL signals)
- PUT win rate: 52.2% (623 PUT signals)
- Max consecutive losses: 13
- Data: 2025-11-27 22:00 → 2026-06-24 05:00 (208.3 days)

### Volatility 100 Index (`R_100`)

**Best timeframe:** `5m` — 51.8% win rate at +5 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1306 | 376.2 | 49.0% | 49.2% | 48.6% | 48.2% | **49.2%** (+3) |
| 5m | 1352 | 77.9 | 49.9% | 51.5% | 51.8% | 49.4% | **51.8%** (+5) |
| 15m | 1330 | 25.5 | 49.2% | 47.8% | 47.7% | 50.9% | **50.9%** (+10) |
| 1h | 1357 | 6.5 | 50.3% | 48.9% | 47.2% | 48.6% | **50.3%** (+1) |

**Best look-ahead detail (+5 candles on 5m):**
- CALL win rate: 50.8% (681 CALL signals)
- PUT win rate: 52.9% (671 PUT signals)
- Max consecutive losses: 15
- Data: 2026-06-06 20:40 → 2026-06-24 05:15 (17.4 days)

### Volatility 10 (1s) Index (`1HZ10V`)

**Best timeframe:** `1m` — 52.6% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1321 | 380.5 | 49.6% | 49.0% | 52.0% | 52.6% | **52.6%** (+10) |
| 5m | 1363 | 78.5 | 49.8% | 48.4% | 49.9% | 49.7% | **49.9%** (+5) |
| 15m | 1329 | 25.5 | 50.4% | 50.1% | 50.0% | 49.4% | **50.4%** (+1) |
| 1h | 1313 | 6.3 | 52.3% | 51.0% | 50.0% | 51.9% | **52.3%** (+1) |

**Best look-ahead detail (+10 candles on 1m):**
- CALL win rate: 51.5% (583 CALL signals)
- PUT win rate: 53.5% (738 PUT signals)
- Max consecutive losses: 11
- Data: 2026-06-20 17:56 → 2026-06-24 05:15 (3.5 days)

### Volatility 25 (1s) Index (`1HZ25V`)

**Best timeframe:** `5m` — 54.3% win rate at +3 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1327 | 382.3 | 50.0% | 50.8% | 53.7% | 53.2% | **53.7%** (+5) |
| 5m | 1347 | 77.6 | 51.5% | 54.3% | 53.0% | 50.3% | **54.3%** (+3) |
| 15m | 1312 | 25.2 | 50.2% | 49.0% | 50.2% | 50.4% | **50.4%** (+10) |
| 1h | 1312 | 6.3 | 47.6% | 48.3% | 46.8% | 45.7% | **48.3%** (+3) |

**Best look-ahead detail (+3 candles on 5m):**
- CALL win rate: 56.2% (783 CALL signals)
- PUT win rate: 51.6% (564 PUT signals)
- Max consecutive losses: 11
- Data: 2026-06-06 20:40 → 2026-06-24 05:15 (17.4 days)

### Volatility 50 (1s) Index (`1HZ50V`)

**Best timeframe:** `1m` — 56.2% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1263 | 363.8 | 51.4% | 53.0% | 52.8% | 56.2% | **56.2%** (+10) |
| 5m | 1297 | 74.7 | 51.3% | 51.4% | 50.3% | 49.6% | **51.4%** (+3) |
| 15m | 1405 | 27 | 52.0% | 51.6% | 49.1% | 48.0% | **52.0%** (+1) |
| 1h | 1370 | 6.6 | 51.8% | 49.5% | 50.7% | 49.9% | **51.8%** (+1) |

**Best look-ahead detail (+10 candles on 1m):**
- CALL win rate: 59.0% (724 CALL signals)
- PUT win rate: 52.5% (539 PUT signals)
- Max consecutive losses: 14
- Data: 2026-06-20 17:56 → 2026-06-24 05:15 (3.5 days)

### Volatility 75 (1s) Index (`1HZ75V`)

**Best timeframe:** `1h` — 54.3% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1367 | 393.8 | 51.2% | 48.4% | 49.9% | 46.5% | **51.2%** (+1) |
| 5m | 1279 | 73.7 | 48.1% | 51.1% | 50.3% | 52.3% | **52.3%** (+10) |
| 15m | 1244 | 23.9 | 50.9% | 50.8% | 49.3% | 49.4% | **50.9%** (+1) |
| 1h | 1353 | 6.5 | 51.6% | 52.8% | 53.1% | 54.3% | **54.3%** (+10) |

**Best look-ahead detail (+10 candles on 1h):**
- CALL win rate: 55.5% (658 CALL signals)
- PUT win rate: 53.2% (695 PUT signals)
- Max consecutive losses: 12
- Data: 2025-11-27 22:00 → 2026-06-24 05:00 (208.3 days)

### Volatility 100 (1s) Index (`1HZ100V`)

**Best timeframe:** `1m` — 52.3% win rate at +10 candles

| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |
|-----------|---------|---------|-------|-------|-------|--------|------|
| 1m | 1314 | 378.5 | 49.2% | 50.2% | 51.1% | 52.3% | **52.3%** (+10) |
| 5m | 1309 | 75.4 | 49.4% | 48.7% | 50.3% | 49.0% | **50.3%** (+5) |
| 15m | 1295 | 24.9 | 50.4% | 50.3% | 50.0% | 48.0% | **50.4%** (+1) |
| 1h | 1367 | 6.6 | 49.5% | 46.9% | 49.3% | 48.1% | **49.5%** (+1) |

**Best look-ahead detail (+10 candles on 1m):**
- CALL win rate: 52.1% (685 CALL signals)
- PUT win rate: 52.5% (629 PUT signals)
- Max consecutive losses: 17
- Data: 2026-06-20 17:57 → 2026-06-24 05:16 (3.5 days)

---

## 4. Results by Timeframe

Average win rate across all symbols for each timeframe:

| Timeframe | Avg Win Rate | Profitable Symbols | Best Symbol | Best WR |
|-----------|-------------|-------------------|-------------|--------|
| **1m** | 52.1% | 6/10 | `1HZ50V` | 56.2% |
| **5m** | 51.3% | 3/10 | `R_25` | 54.7% |
| **15m** | 51.3% | 2/10 | `R_25` | 54.3% |
| **1h** | 51.2% | 4/10 | `1HZ75V` | 54.3% |

---

## 5. CALL vs PUT Signal Analysis

Which direction wins more consistently?

| Symbol | TF | Best CALL WR | Best PUT WR | Dominant |
|--------|----|-------------|------------|----------|
| `1HZ50V` | 1m | 59.0% | 52.5% | 📈 CALL |
| `R_25` | 5m | 50.6% | 59.1% | 📉 PUT |
| `R_25` | 15m | 53.0% | 56.9% | 📉 PUT |
| `1HZ75V` | 1h | 55.5% | 53.2% | 📈 CALL |
| `1HZ25V` | 5m | 56.2% | 51.6% | 📈 CALL |
| `1HZ25V` | 1m | 52.9% | 54.5% | 📉 PUT |
| `R_75` | 1h | 55.3% | 52.5% | 📈 CALL |
| `R_10` | 1h | 50.7% | 55.3% | 📉 PUT |
| `R_10` | 15m | 51.4% | 53.8% | 📉 PUT |
| `1HZ10V` | 1m | 51.5% | 53.5% | 📉 PUT |
| `R_10` | 1m | 54.2% | 51.2% | 📈 CALL |
| `1HZ10V` | 1h | 53.5% | 53.2% | 📈 CALL |
| `1HZ75V` | 5m | 53.2% | 51.4% | 📈 CALL |
| `1HZ100V` | 1m | 54.0% | 52.5% | 📈 CALL |
| `R_75` | 1m | 51.1% | 53.2% | 📉 PUT |
| `1HZ50V` | 15m | 52.3% | 52.6% | 📉 PUT |
| `R_100` | 5m | 52.1% | 52.9% | 📉 PUT |
| `1HZ50V` | 1h | 54.1% | 52.6% | 📈 CALL |
| `R_25` | 1m | 52.8% | 52.3% | 📈 CALL |
| `1HZ50V` | 5m | 53.5% | 50.3% | 📈 CALL |
| `R_25` | 1h | 48.7% | 53.9% | 📉 PUT |
| `1HZ75V` | 1m | 50.4% | 51.8% | 📉 PUT |
| `R_100` | 15m | 47.6% | 53.4% | 📉 PUT |
| `1HZ75V` | 15m | 51.7% | 50.4% | 📈 CALL |
| `R_75` | 15m | 54.1% | 48.4% | 📈 CALL |
| `1HZ100V` | 15m | 50.0% | 51.4% | 📉 PUT |
| `1HZ10V` | 15m | 49.5% | 53.9% | 📉 PUT |
| `1HZ25V` | 15m | 51.7% | 51.5% | 📈 CALL |
| `1HZ100V` | 5m | 49.2% | 51.6% | 📉 PUT |
| `R_100` | 1h | 48.3% | 51.9% | 📉 PUT |
| `R_50` | 1m | 52.9% | 48.5% | 📈 CALL |
| `R_50` | 15m | 51.0% | 49.2% | 📈 CALL |
| `1HZ10V` | 5m | 48.6% | 51.6% | 📉 PUT |
| `R_10` | 5m | 50.9% | 49.7% | 📈 CALL |
| `1HZ100V` | 1h | 50.6% | 50.5% | 📈 CALL |
| `R_75` | 5m | 51.2% | 48.8% | 📈 CALL |
| `R_100` | 1m | 49.1% | 49.5% | 📉 PUT |
| `R_50` | 5m | 47.4% | 52.1% | 📉 PUT |
| `1HZ25V` | 1h | 48.3% | 49.0% | 📉 PUT |
| `R_50` | 1h | 46.3% | 50.3% | 📉 PUT |

---

## 6. Top 5 Combinations — Detailed Analysis

### 1. Volatility 50 (1s) Index — 1m candles

| Metric | Value |
|--------|-------|
| Symbol | `1HZ50V` |
| Timeframe | 1m candles (60s granularity) |
| Data range | 2026-06-20 17:56 → 2026-06-24 05:15 |
| Span | 3.5 days |
| Total signals | 1263 |
| Signal frequency | 363.8/day |
| CALL signals | 724 |
| PUT signals | 539 |

**Win rates by look-ahead duration:**

| Look-Ahead | Total | Win Rate | CALL WR | PUT WR | Max Consec. Loss |
|-----------|-------|----------|---------|--------|------------------|
| +1 candle | 1263 | 〰️ **51.4%** | 52.3% | 50.1% | 9 |
| +3 candles | 1263 | ✅ **53.0%** | 54.1% | 51.4% | 17 |
| +5 candles | 1263 | ✅ **52.8%** | 54.1% | 51.0% | 11 |
| +10 candles | 1263 | ✅ **56.2%** | 59.0% | 52.5% | 14 |

### 2. Volatility 25 Index — 5m candles

| Metric | Value |
|--------|-------|
| Symbol | `R_25` |
| Timeframe | 5m candles (300s granularity) |
| Data range | 2026-06-06 20:35 → 2026-06-24 05:10 |
| Span | 17.4 days |
| Total signals | 1395 |
| Signal frequency | 80.4/day |
| CALL signals | 699 |
| PUT signals | 696 |

**Win rates by look-ahead duration:**

| Look-Ahead | Total | Win Rate | CALL WR | PUT WR | Max Consec. Loss |
|-----------|-------|----------|---------|--------|------------------|
| +1 candle | 1395 | ❌ **49.1%** | 47.2% | 51.0% | 12 |
| +3 candles | 1395 | ❌ **49.1%** | 47.5% | 50.7% | 16 |
| +5 candles | 1395 | ✅ **52.1%** | 50.6% | 53.6% | 16 |
| +10 candles | 1395 | ✅ **54.7%** | 50.4% | 59.1% | 14 |

### 3. Volatility 25 Index — 15m candles

| Metric | Value |
|--------|-------|
| Symbol | `R_25` |
| Timeframe | 15m candles (900s granularity) |
| Data range | 2026-05-03 03:15 → 2026-06-24 05:00 |
| Span | 52.1 days |
| Total signals | 1353 |
| Signal frequency | 26/day |
| CALL signals | 626 |
| PUT signals | 727 |

**Win rates by look-ahead duration:**

| Look-Ahead | Total | Win Rate | CALL WR | PUT WR | Max Consec. Loss |
|-----------|-------|----------|---------|--------|------------------|
| +1 candle | 1353 | ❌ **49.6%** | 51.4% | 48.0% | 13 |
| +3 candles | 1353 | 〰️ **50.7%** | 50.5% | 50.9% | 17 |
| +5 candles | 1353 | ✅ **52.2%** | 53.0% | 51.4% | 15 |
| +10 candles | 1353 | ✅ **54.3%** | 51.3% | 56.9% | 18 |

### 4. Volatility 75 (1s) Index — 1h candles

| Metric | Value |
|--------|-------|
| Symbol | `1HZ75V` |
| Timeframe | 1h candles (3600s granularity) |
| Data range | 2025-11-27 22:00 → 2026-06-24 05:00 |
| Span | 208.3 days |
| Total signals | 1353 |
| Signal frequency | 6.5/day |
| CALL signals | 658 |
| PUT signals | 695 |

**Win rates by look-ahead duration:**

| Look-Ahead | Total | Win Rate | CALL WR | PUT WR | Max Consec. Loss |
|-----------|-------|----------|---------|--------|------------------|
| +1 candle | 1353 | 〰️ **51.6%** | 52.6% | 50.6% | 12 |
| +3 candles | 1353 | ✅ **52.8%** | 53.3% | 52.4% | 13 |
| +5 candles | 1353 | ✅ **53.1%** | 53.6% | 52.7% | 18 |
| +10 candles | 1353 | ✅ **54.3%** | 55.5% | 53.2% | 12 |

### 5. Volatility 25 (1s) Index — 5m candles

| Metric | Value |
|--------|-------|
| Symbol | `1HZ25V` |
| Timeframe | 5m candles (300s granularity) |
| Data range | 2026-06-06 20:40 → 2026-06-24 05:15 |
| Span | 17.4 days |
| Total signals | 1347 |
| Signal frequency | 77.6/day |
| CALL signals | 783 |
| PUT signals | 564 |

**Win rates by look-ahead duration:**

| Look-Ahead | Total | Win Rate | CALL WR | PUT WR | Max Consec. Loss |
|-----------|-------|----------|---------|--------|------------------|
| +1 candle | 1347 | 〰️ **51.5%** | 52.6% | 50.0% | 10 |
| +3 candles | 1347 | ✅ **54.3%** | 56.2% | 51.6% | 11 |
| +5 candles | 1347 | ✅ **53.0%** | 56.1% | 48.8% | 15 |
| +10 candles | 1347 | 〰️ **50.3%** | 52.9% | 46.8% | 15 |

---

## 7. Worst Performers — What to Avoid

| Rank | Symbol | TF | Best WR | Signals | Issue |
|------|--------|----|---------|---------|-------|
| 40 | `R_50` | 1h | 47.5% | 1337 | Win rate too low |
| 39 | `1HZ25V` | 1h | 48.3% | 1312 | Below breakeven |
| 38 | `R_50` | 5m | 49.0% | 1361 | Below breakeven |
| 37 | `R_100` | 1m | 49.2% | 1306 | Below breakeven |
| 36 | `R_75` | 5m | 49.3% | 1250 | Below breakeven |

---

## 8. Recommendation — Target Market

### ✅ Primary Recommendation

**Volatility 50 (1s) Index (`1HZ50V`) on 1m candles**

- **Win rate:** 56.2% at +10 candle duration
- **CALL win rate:** 59.0%
- **PUT win rate:** 52.5%
- **Signal frequency:** 363.8 signals/day
- **Max consecutive losses:** 14 — budget at least 17 units per session

**Configuration for .env:**
```
SYMBOL=1HZ50V
CANDLE_GRANULARITY=60
```

### 🥈 Alternative Recommendation

**Volatility 25 Index (`R_25`) on 5m candles** — 54.7% win rate  
CALL: 50.4% | PUT: 59.1% | 80.4 signals/day

---

## 9. Risk Metrics Summary

| Symbol | TF | Max Consec. Loss (best) | Signals/Day | Min Bankroll (×stake) |
|--------|----|------------------------|-------------|----------------------|
| `1HZ75V` | 15m | 7 | 23.9 | 12× |
| `1HZ50V` | 15m | 8 | 27 | 13× |
| `R_75` | 1m | 9 | 390.9 | 14× |
| `1HZ50V` | 1h | 9 | 6.6 | 14× |
| `1HZ50V` | 5m | 9 | 74.7 | 14× |
| `1HZ75V` | 1m | 9 | 393.8 | 14× |
| `1HZ10V` | 15m | 10 | 25.5 | 15× |
| `R_100` | 1h | 10 | 6.5 | 15× |
| `R_50` | 15m | 10 | 26 | 15× |
| `1HZ100V` | 1h | 10 | 6.6 | 15× |

> **Bankroll rule:** Always have at least (max consecutive losses + 5) × stake available per session.

---

*Report generated by V100 Momentum Continuation Backtester — 2026-06-24 05:16:51 UTC*
