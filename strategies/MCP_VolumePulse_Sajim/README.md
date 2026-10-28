# MCP_VolumePulse_Sajim (XAUUSD)

> **High-Frequency Micro-Volume Pulse Scalper** capturing M1 tick velocity spikes that construct M5 expansion candles, with diminishing pyramid scaling and dynamic volume-exhaustion exit.

---

## Strategy Overview

`MCP_VolumePulse_Sajim` was engineered to capture the beginning of aggressive institutional volume sweeps. Rather than waiting for a candle to close (when the meat of the move is already finished), the EA monitors real-time tick velocity and micro-aggression in a rolling 3-second window.

### 1. The Ignition Pulse (Entry 1: 0.10 Lots)
* **Velocity Trigger**: $\ge 20$ ticks within a rolling 3,000 ms window.
* **Micro-Aggression Ratio**: $\ge 70\%$ upticks hitting the Ask (for Buy) or $\ge 70\%$ downticks hitting the Bid (for Sell).
* **M5 Candle Clock**: Only ignites within the first $0 - 180$ seconds of the M5 candle to ensure we are participating in the candle expansion, not buying a late climax wick.
* **Spread Guard**: Max spread $\le 30$ points (3.0 pips on Gold).

### 2. Diminishing Pyramid Scale-Ins
* **Tier 2 (0.07 Lots)**:
  * Tier 1 is in profit by at least $+10$ points (+1.0 pip).
  * Tick velocity remains active ($\ge 14$ ticks / 3s).
  * Minimum time gap $\ge 3$ seconds from Tier 1.
* **Tier 3 (0.03 Lots)**:
  * Tier 2 is in profit by at least $+10$ points.
  * Tick velocity remains active ($\ge 14$ ticks / 3s).
  * Minimum time gap $\ge 3$ seconds from Tier 2.
* **Maximum Open Exposure**: $0.20$ lots across 3 positions. Front-weighted so the average entry price remains close to the base.

### 3. Dynamic Volume-Drop Exit (No Fixed TP)
The entire basket of open positions is closed simultaneously when any of the following occur:
1. **Velocity Deceleration**: Current velocity drops below $35\%$ of the pulse peak (`InpDecelExitRatio = 0.35`).
2. **Price Stagnation**: Price fails to make a new high/low within $10$ seconds (`InpStagnationSeconds = 10`).
3. **Micro-Retracement**: Price retraces $\ge 15$ points from the highest point achieved during the burst.
4. **Emergency Disaster SL**: Hard stop placed with broker at $35$ points from each entry price.

---

## Technical Specifications

| Parameter | Default | Description |
|:---|:---|:---|
| `InpLot_Tier1` | `0.10` | Base ignition trigger lot size |
| `InpLot_Tier2` | `0.07` | Continuation add-on 1 lot size |
| `InpLot_Tier3` | `0.03` | Continuation add-on 2 lot size |
| `InpMinAddonPoints` | `10` | Min price advance required between add-ons |
| `InpVelocityWindow_ms`| `3000` | Rolling tick buffer window (ms) |
| `InpVelocityTrigger` | `20` | Ignition threshold (ticks within window) |
| `InpVelocityContinuation`| `14` | Continuation threshold for add-ons |
| `InpDirectionThreshold` | `0.70` | Aggression ratio (70% upticks/downticks) |
| `InpDecelExitRatio` | `0.35` | Exit when velocity drops below 35% of peak |
| `InpStagnationSeconds`| `10` | Exit if no new high/low in N seconds |
| `InpRetracementExitPoints`| `15` | Exit if price gives back N points from peak |
| `InpEmergencySL_Points` | `35` | Emergency disaster SL |
| `InpMaxM5ElapsedSec` | `180` | Max elapsed seconds in M5 bar to ignite |
