# Project: Phase 2 - Temporal Resonance (Cause & Effect)

The Vacuum Hunter currently operates in the "Now". It reacts perfectly when gravity breaks in the present moment. 
But you have just pointed toward the future: **Cause and Effect via Historical Layering.**

If we layer the BEEP Baseline of an asset on the exact same date and time across multiple years, we can measure the **Temporal Resonance**. We can teach the engine to remember when structural shifts happen every year, allowing it to anticipate gravity breaking before the Mass even hits 11.

## Goal
Design a new research module called the **Temporal Layering Engine**. 
It will pull multiple years of historical data, extract the Pure Quality Score (Asymptotic Baseline), and overlay the years on top of each other to find recurring "Cause and Effect" cycles.

## User Review Required
> [!IMPORTANT]
> To do this properly, we will need to pull massive amounts of historical data (e.g., 5-10 years of daily or hourly data) rather than just the 60-day 5-minute charts we have been using. This requires heavier data processing. Are you comfortable with the engine running deeper, slower backtests to map these multi-year cycles?

## Proposed Architecture

### 1. The Temporal Layering Engine (`temporal_resonance.py`)
I will build a completely new script that:
- Fetches 5 years of historical data for a specific asset (e.g., Gold).
- Slices the data into 1-year blocks (Jan 1 to Dec 31).
- Runs the **Asymptotic Filter** on each year to extract the True Wave.
- **Layers the waves on top of each other** along a single 365-day axis.

### 2. Identifying Cause and Effect
The script will mathematically measure where the Baselines intersect and resonate:
- **The Nodes of Chaos (Cause):** Times of year where the historical baselines completely diverge (unpredictable).
- **The Nodes of Gravity (Effect):** Specific weeks of the year where the Baselines across all 5 years align perfectly and trigger a Mass 11 shift in the exact same direction. 

### 3. The Memory Matrix
Once the engine identifies a "Node of Gravity" (e.g., Gold always breaks gravity on the 3rd week of September), it writes that memory into a configuration file. 
The live `mt5_vacuum_hunter.py` can then read this memory. When that specific week approaches, the live bot can lower its Mass Threshold and aggressively scale its lot size because it *knows* the seasonal Cause & Effect cycle is arriving.

## Next Steps
If you approve this creative direction, I will write the `temporal_resonance.py` engine and we will run a 5-year multi-layer analysis on Gold to see if it has a hidden heartbeat.
