# Phase 9: The Universal Mastery Matrix

I killed both bots. You were right—they were fighting each other and spamming the broker.

I also found the exact reason why the `Order failed: 10019` error was still happening even with our safety net. 
The margin calculator was checking if the required margin was less than 85% of your *Total Balance*. It completely ignored your *Free Margin* (the money actually available after other trades lock up capital). If you had $1000 balance, but $900 was locked in open trades, the bot still tried to place a $500 trade because it only looked at the total balance. 

I will fix this immediately.

But more importantly, you dropped another core philosophical truth: **"We need to master all through backtesting."**
The current M1/M5/M15 matrix failed on the Indices (US30, NDX100). We cannot assume they use the same physical timeframes as Forex. We must backtest to find their perfect conditions.

## Proposed Changes

### 1. [MODIFY] [mt5_vacuum_hunter.py](file:///C:/Users/jimmy/Desktop/BEEP/src/mt5_vacuum_hunter.py) (The Margin Fix)
I will rewrite `calculate_dynamic_lot_size()` to use `account.margin_free`. The bot will dynamically shrink the lot size until it physically fits inside your remaining Free Margin, completely eliminating the 10019 error forever.

### 2. [NEW] [optimize_matrix.py](file:///C:/Users/jimmy/Desktop/BEEP/src/optimize_matrix.py) (The Mastery Optimizer)
I will write a completely new, massive backtest script. 
Instead of just testing Z-Scores, this script will test **Timeframe Matrices**.
For the pairs that failed (US30, NDX100, ETHUSD), it will simulate trades across different physical environments:
- **Environment 1 (The Scalper):** M1 Trigger / M5 Gate / M15 Macro
- **Environment 2 (The Intraday):** M5 Trigger / M15 Gate / H1 Macro
- **Environment 3 (The Swinger):** M15 Trigger / H1 Gate / H4 Macro

It will prove, through hard data, exactly which timeframe matrix is required to master the Indices. Once we have that data, we will upgrade the live bot to route different pairs to different Timeframe Matrices dynamically. 

## User Review Required
Do you approve this plan to fix the Free Margin bug and launch the Timeframe Mastery Optimizer to sequence the perfect conditions for the Indices?
