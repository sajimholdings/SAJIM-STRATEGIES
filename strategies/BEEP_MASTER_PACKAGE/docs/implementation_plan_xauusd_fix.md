# Project: The Universal Engine (XAUUSD/Crypto Fix & Dynamic Stop Loss)

You have correctly identified the final mathematical pieces. We must solve the market gap issue that caused Gold to fail, and we must mathematically calculate the exact price point where a Stop Loss belongs (the boundary where Mass hits 11).

## Goal
Update the live `mt5_vacuum_hunter.py` and the theoretical backtest models to:
1. Include a **Gap Filter** so volatile pairs like XAUUSD and Crypto don't trigger false Paradigm Shifts over weekends/overnight.
2. Introduce the **Dynamic Stop-Loss Boundary Equation**, which calculates the exact price level where gravity breaks, allowing us to place hard MT5 stop-losses perfectly outside the baseline bound.

## User Review Required
> [!IMPORTANT]
> The Dynamic Stop Loss equation calculates the exact price where Mass reaches 11. This means the MT5 Stop Loss will visually move up and down on your chart as the Baseline and Volatility (Standard Deviation) breathe. Are you comfortable with dynamic (trailing/shifting) stop-losses, or do you prefer the stop-loss to remain static once placed?

## Proposed Changes

### 1. The Gap Filter (Solving XAUUSD & Crypto)
When markets close and reopen (like Gold overnight, or Bitcoin over the weekend), the price jumps violently. The engine currently reads this gap as a massive spike in Intent (triggering a false Mass 11).
**The Fix:** We will add a mathematical check comparing `Open[current]` to `Close[previous]`. If the gap is larger than 3x the normal candle volatility, the engine classifies it as a **Structural Gap, not Human Intent**. It will freeze Mass calculation until the baseline catches up, preventing the 60% drawdown we saw in the backtest.

### 2. The Dynamic Stop Loss Equation
You realized that changing the filter depth changes the baseline, which changes where the Stop Loss goes. You are 100% correct.
We can reverse-engineer the Mass equation to find the exact price where gravity breaks:
1. We know: `Mass = Z-Score × TimeRatio`
2. If we want to find the price where Mass hits exactly 11, we set `Mass = 11`.
3. `Target Z-Score = 11 ÷ TimeRatio`
4. `Stop Loss Price = Baseline ± (Target Z-Score × Rolling Standard Deviation)`

By running this math on every tick, the bot knows the exact price boundary of the Vacuum. It will place the MT5 stop-loss exactly at that price offset.

#### [MODIFY] `C:\Users\jimmy\Desktop\BEEP\src\mt5_vacuum_hunter.py`
- Add the Gap Filter to ignore overnight/weekend volatility jumps.
- Add the Dynamic Stop Loss Equation to calculate the exact `Stop Loss Price`.
- Update the MT5 `send_market_order` function to use this exact mathematical price for the hard SL, rather than the arbitrary 0.3% multiplier we used before.

## Verification Plan
1. We will update a backtest script with the Gap Filter to verify that the XAUUSD -60% loss is eliminated.
2. We will update the live MT5 bot with the exact code.
