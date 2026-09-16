# Phase 10: Master Synchronization & Death Avoidance

You asked two profound questions. Here is the theoretical answer, followed by how we test it.

### Q1: Can we synchronize the data from all timeframes as one, or will it bring conflict?
**Answer:** We *can* synchronize them, but if we do it poorly, it brings massive conflict. 
The conflict happens because M1 is light and fast, while H1 is heavy and slow. If you just add them together, the M1 noise overpowers the H1 trend. 
**The Solution (Without Conflict):** We do a **Weighted Frequency Sync**. We take the Z-Scores of M1, M5, M15, H1, and H4, and we give the higher timeframes more "Gravity" (weight). We then compress this into a single, unified number: **The BEEP Master Score**. If the Master Score is `+5.0`, it means all timeframes are mathematically humming in absolute bullish unison. You no longer look at M1 or M15 individually; you just look at the Master Frequency.

You explicitly said: *"Answer, don't do, then input this into BEEP see what it does do test before anything."*

So, I will not touch the live bot. Instead, I will write a test script (`test_sync_matrix.py`) that calculates this unified Master Score on past data to see if it predicts massive swings better than our separated gate system. 

### Q2: Avoiding Death (Risk of Ruin)
You said: *"Because we know that death is blowing acc we should be able to calculate how much are we able to risk each time and for how many times before we get a win."*

This is the holy grail of risk management. It is called the **Risk of Ruin Formula** combined with the **Kelly Criterion**. 
Right now, the bot risks a flat `2.0%` on every trade. That is an assumption.
If we know from our Playbook Logger that our win rate on Gold is 45%, and our average win is 3x our average loss, we can mathematically calculate exactly how many losses in a row are possible. 
If the math says we could lose 20 times in a row, then risking 5% per trade equals death (20 x 5% = 100% blown account). Therefore, the bot must dynamically adjust the risk down to `0.5%` so that 20 losses only draw down 10%.

## Proposed Action Plan

1. **The Sync Test (`test_sync_matrix.py`):** I will write a massive backtest script that compresses M1, M5, M15, H1, and H4 into a single **Weighted Master Score**. It will test if entering based solely on a high Master Score generates better profits than our current separated timeframe gates.
2. **The Death Avoidance Calculator:** I will build a math function into the bot called `calculate_survival_risk()`. Instead of a hardcoded `2.0%`, the bot will look at its historical win rate and max consecutive losses, and calculate the exact percentage to risk so the probability of blowing the account mathematically reaches 0%.

## User Review Required
Do you approve me writing the `test_sync_matrix.py` script to backtest the Unified Timeframe theory, and the implementation of the Death Avoidance risk math?
