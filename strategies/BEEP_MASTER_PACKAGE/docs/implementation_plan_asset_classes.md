# The Asset-Class Matrix Engine (Phase 3)

The backtest mathematically proved that using a universal set of parameters (like 50 Lots or a 1.5 Threshold) across different asset classes will lead to catastrophic drawdowns on volatile pairs like Crypto, while completely starving safer pairs like Forex. 

To solve this, we will restructure `mt5_vacuum_hunter.py` into a **Matrix Engine** that handles every asset class according to its specific physics.

## Open Questions

> [!CAUTION]
> Below are the proposed Base Lot Sizes for the live MT5 account. Please confirm if these lot sizes fit your current account equity before I write the code. 

## Proposed Changes

### [MODIFY] [mt5_vacuum_hunter.py](file:///C:/Users/jimmy/Desktop/BEEP/src/mt5_vacuum_hunter.py)
I will replace the global variables (`LOT_SIZE` and `dynamic_threshold`) with an `ASSET_CONFIG` dictionary. 

When the bot loops through a pair, it will identify its class and apply the correct physics:

#### 1. FOREX (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD)
Forex trends are smooth but small. They require high leverage and aggressive entries.
- **Base Lot Size:** 50.0 
- **Dynamic Threshold:** `Mean + 1.5 Std Dev` (Aggressive, captures early breakouts).
- **Stop Loss:** Hard SL directly at the True Baseline (Zero buffer).

#### 2. CRYPTO (BTCUSD, ETHUSD)
Crypto is violently noisy. 50 Lots will liquidate the account instantly. 
- **Base Lot Size:** 1.0 (Low leverage).
- **Dynamic Threshold:** `Mean + 3.0 Std Dev` (Extremely conservative, ignores the noise).
- **Stop Loss:** `Baseline ± (Rolling Std Dev * 0.5)` (Gives the trade a "Volatility Buffer" so it doesn't get wick-stopped before the true trend begins).

#### 3. METALS & INDICES (XAUUSD, US30, NDX100)
These pairs are highly volatile but respect structural trends better than crypto.
- **Base Lot Size:** 5.0 (Moderate leverage).
- **Dynamic Threshold:** `Mean + 2.0 Std Dev`.
- **Stop Loss:** `Baseline ± (Rolling Std Dev * 0.2)` (Small Volatility Buffer).

## Verification Plan
Once updated, I will reboot the MT5 bot. We will verify in the live logs that BTCUSD uses a Threshold of `Mean + 3 Std` and a Lot Size of `1.0`, while GBPUSD simultaneously uses a Threshold of `Mean + 1.5 Std` and a Lot Size of `50.0`.
