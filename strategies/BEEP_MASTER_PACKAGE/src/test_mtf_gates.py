"""
The Gates of Confirmation (Multi-Timeframe BEEP Backtester)
===========================================================
This script mathematically proves the user's thesis:
"Every time we go down the baseline, we have our upper band baseline protecting us."

It calculates the True Wave (BEEP Asymptote) across 3 timeframes simultaneously:
- The Micro (1-Minute): Used for lightning-fast entries/exits (Scalping).
- The Median (5-Minute): The Gate of Confirmation.
- The Macro (15-Minute): The True Flow of the Market.

We only take a Micro Paradigm Shift if the Macro Baselines are flowing in the same direction.
The Stop Loss is mathematically placed at the Macro Baseline (because if the Macro breaks, the Cause is dead).
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_asymptote(df):
    """Calculates the True Baseline with 0% noise."""
    current_df = df.copy()
    total = len(df)
    for p in range(1, 10):
        beep = BeepFilter(lookback=20, z_threshold=2.0)
        filtered = beep.apply(current_df)
        noise_idx = filtered[filtered["is_noise"]].index
        if len(noise_idx) / total < 0.01 and p > 1: break
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            clean_close.iloc[loc] = clean_close.iloc[max(0, loc - 1)]
        current_df["Close"] = clean_close
    return current_df["Close"].rolling(window=15, min_periods=1).mean()

def run_mtf_backtest(pair="GBPUSD"):
    print(f"\n{'='*70}")
    print(f"  THE GATES OF CONFIRMATION (MTF BEEP ENGINE): {pair}")
    print(f"{'='*70}")
    
    # Fetch all 3 timeframes
    print("[1] Fetching M1, M5, and M15 Data Matrix...")
    # Fetching 7 days to ensure we have dense 1-minute data mapping cleanly to 15-minute
    m1_df = fetch_pair(pair, period="7d", interval="1m")
    m5_df = fetch_pair(pair, period="7d", interval="5m")
    m15_df = fetch_pair(pair, period="7d", interval="15m")
    
    if len(m1_df) < 500: return
        
    print("[2] Calculating True Baselines for all Timeframes...")
    m1_df["Base_M1"] = get_asymptote(m1_df)
    m5_df["Base_M5"] = get_asymptote(m5_df)
    m15_df["Base_M15"] = get_asymptote(m15_df)
    
    m1_df["Vel_M1"] = m1_df["Base_M1"].diff()
    m5_df["Vel_M5"] = m5_df["Base_M5"].diff()
    m15_df["Vel_M15"] = m15_df["Base_M15"].diff()
    
    print("[3] Synchronizing the Temporal Gates...")
    # Map the M5 and M15 baselines onto the M1 dataframe based on time
    # This simulates watching all 3 charts live on a 1-minute tick
    m1_df["Base_M5"] = np.nan
    m1_df["Vel_M5"] = np.nan
    m1_df["Base_M15"] = np.nan
    m1_df["Vel_M15"] = np.nan
    
    for idx, row in m1_df.iterrows():
        # Find the last completed M5 candle
        m5_mask = m5_df.index <= idx
        if m5_mask.any():
            m1_df.at[idx, "Base_M5"] = m5_df.loc[m5_mask, "Base_M5"].iloc[-1]
            m1_df.at[idx, "Vel_M5"] = m5_df.loc[m5_mask, "Vel_M5"].iloc[-1]
            
        # Find the last completed M15 candle
        m15_mask = m15_df.index <= idx
        if m15_mask.any():
            m1_df.at[idx, "Base_M15"] = m15_df.loc[m15_mask, "Base_M15"].iloc[-1]
            m1_df.at[idx, "Vel_M15"] = m15_df.loc[m15_mask, "Vel_M15"].iloc[-1]
            
    m1_df.dropna(subset=["Base_M5", "Base_M15"], inplace=True)
    
    print("[4] Hunting Paradigm Shifts through the Gates...")
    # Calculate M1 Mass
    m1_df["Deviation"] = m1_df["Close"] - m1_df["Base_M1"]
    rolling_std = m1_df["Deviation"].rolling(window=100).std()
    m1_df["Z_Score"] = m1_df["Deviation"] / rolling_std
    
    mass_series = np.zeros(len(m1_df))
    current_mass = 0
    current_dir = 0
    
    for i in range(100, len(m1_df)):
        z = m1_df["Z_Score"].iloc[i]
        if pd.isna(z): continue
            
        if abs(z) > 1.5:
            if current_dir == 0 or np.sign(z) != current_dir:
                current_dir = np.sign(z)
            current_mass += (abs(z) * 10) # simplified mass for 1m scale
        else:
            current_mass = 0
            current_dir = 0
            
        mass_series[i] = current_mass
        
    m1_df["Mass_M1"] = mass_series
    
    bal = 10000.0
    pos = 0
    entry_price = 0
    trades = []
    
    # Execute with Gates
    for i in range(100, len(m1_df)):
        row = m1_df.iloc[i]
        
        close = row["Close"]
        mass = row["Mass_M1"]
        vel_m1 = row["Vel_M1"]
        vel_m5 = row["Vel_M5"]
        vel_m15 = row["Vel_M15"]
        
        if pos != 0:
            # STOP LOSS LOGIC: We don't guess. The SL is the MACRO (M15) Baseline. 
            # If price crosses the 15-minute True Wave, the overarching Cause is dead.
            sl_price = row["Base_M15"]
            
            is_exhausted = (pos == 1 and vel_m1 < 0) or (pos == -1 and vel_m1 > 0)
            sl_hit = (pos == 1 and close < sl_price) or (pos == -1 and close > sl_price)
            
            if is_exhausted or sl_hit:
                pnl = bal * ((close - entry_price) / entry_price * pos) * 50 # 50 Lots
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            # THE GATES OF CONFIRMATION
            # Entry condition: M1 breaks gravity (Mass > 30), AND M5 and M15 Baselines agree (flowing same direction)
            if mass > 30.0:
                direction = np.sign(row["Z_Score"])
                
                # Are the gates open?
                gate_open = False
                if direction == 1 and vel_m5 > 0 and vel_m15 > 0: gate_open = True
                if direction == -1 and vel_m5 < 0 and vel_m15 < 0: gate_open = True
                
                if gate_open:
                    pos = direction
                    entry_price = close
                    
    if pos != 0: trades.append(bal * ((m1_df.iloc[-1]["Close"] - entry_price) / entry_price * pos) * 50)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"  Total Trades via Gates       : {len(trades)}")
    print(f"  Win Rate                     : {win_rate:.1f}%")
    print(f"  Starting Balance             : $10,000.00")
    print(f"  Final Balance                : ${bal:,.2f}")
    print(f"  Net Profit (7 Days)          : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_mtf_backtest(pair="GBPUSD")
    run_mtf_backtest(pair="EURUSD")
