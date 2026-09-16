# Phase 5: Margin Protection, Partials, & Volatility Expansion

The pure dynamic engine is mathematically sound, but as we saw in the live logs, it ran into Error `10019` (Not Enough Money). 

**Why did 10019 happen?**
When the Stop Loss is very tight (e.g. 5 pips), the dynamic math calculates that you need a massive lot size (e.g. 85 Lots) to hit your 2% Risk target. However, MT5 brokers require a minimum cash Margin to open large lot sizes. If the calculated lot size requires more Margin than you physically have in the account, the broker rejects the trade.

I will fix this, add the "Partials" logic you requested, and add more pairs.

## Proposed Changes

### [MODIFY] [mt5_vacuum_hunter.py](file:///C:/Users/jimmy/Desktop/BEEP/src/mt5_vacuum_hunter.py)

#### 1. The Margin Safety Net (Fixing 10019)
Before sending the trade, I will use MT5's `order_calc_margin` function.
If the dynamically calculated Lot Size requires more margin than you have available, the bot will automatically scale the Lot Size down until the broker accepts it. You will never see Error 10019 again.

#### 2. The 50% Partial Extraction
You requested to "take partials ukiona one move inafanya tusipate dooh" (take partials so we don't lose money when a single move exhausts).
I will implement this perfectly based on the **Gates of Confirmation**:
- If we enter on the M1 chart, and it spikes into profit...
- The moment the **M5 Baseline** velocity reverses (exhaustion), the bot will immediately close **50% of the position** to secure cash.
- It will leave the remaining 50% running with the Trailing Stop to see if the overarching M15 trend resumes. 
- *This guarantees we extract cash on every micro-swing without killing the macro-trade.*

#### 3. Expanding the Volatility Matrix
I will add highly volatile pairs to the scan list to give the bot more paradigm shifts to hunt:
- Added `GBPJPY` (The Beast)
- Added `USOIL` (Crude Oil)
- Added `NAS100` (Depending on broker naming, matching `NDX100`)

## User Review Required
Are you satisfied with the 50% Partial Close logic (triggering exactly when the 5-Minute baseline exhausts)? If so, give me the green light and I will deploy these changes directly to the live engine and update the Export folder.
