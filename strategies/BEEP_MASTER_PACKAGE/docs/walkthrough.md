# Deployment Walkthrough: The Pure Dynamic MTF Matrix

The final iteration of the Vacuum Hunter has been successfully deployed. It is no longer an "Asset-Class" bot. It is a universal **Multi-Timeframe Physics Engine**.

We successfully ported the dynamic risk equations from your legacy `Stride Forex.txt` code directly into the raw physics of BEEP.

## How it works live

The engine calculates the physics of three timeframes simultaneously for all 10 pairs:
- **The Micro (M1):** 1-Minute True Baseline
- **The Median (M5):** 5-Minute True Baseline
- **The Macro (M15):** 15-Minute True Baseline

### The Gates of Confirmation
The bot scans the M1 chart. When it detects a massive spike in *Mass* (Gravity Broken), it checks the **Gates**.
It will only open the trade if both the M5 and M15 Baselines are flowing in the exact same direction as the M1 breakout. If the macro trend disagrees, the trade is rejected.

> [!CAUTION]
> **The Macro Stop Loss**
> We no longer guess Stop Loss buffers. If a trade is triggered, the engine mathematically locks the Stop Loss exactly onto the **15-Minute True Baseline**. The rationale is pure Cause and Effect: if the price reverses hard enough to break the 15-minute macro trend, the overarching Cause of the move is dead.

> [!TIP]
> **Dynamic Risk Equalization (Account Flipping)**
> The engine evaluates your exact Account Balance live on every tick.
> It calculates a fixed **2% Risk**.
> It measures the distance from the Entry Price to the 15-Minute Stop Loss.
> It then uses your `Stride` math to perfectly calculate the exact Lot Size required.
> 
> - If the M15 baseline is tight (e.g. GBPUSD), the Lot Size automatically **explodes** to maximize leverage.
> - If the M15 baseline is wide (e.g. BTCUSD), the Lot Size automatically **shrinks** to protect the account from liquidation.

## Live Verification
The engine is currently running in the background. If you wish to flip the account faster, simply increase the `RISK_PERCENT = 0.02` variable at the top of the `src/mt5_vacuum_hunter.py` script to `0.05` (5%) or `0.10` (10%).
