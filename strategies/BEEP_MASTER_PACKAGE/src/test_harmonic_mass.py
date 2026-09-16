"""
The Harmonic Mass Test (Cycle-Adjusted Reality)
===============================================
Hypothesis:
1. Mass is not just Intensity * Raw Time. 
2. Mass = Intensity * (Time relative to the Pair's Harmonic Cycle).
3. We calculate the local cycle dynamically. If a spike sustains for a significant 
   percentage of the local cycle, it bends reality.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_causal_beep_baseline(df, passes=3):
    current_df = df.copy()
    for _ in range(passes):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        
        clean_close = current_df["Close"].copy()
        for idx in filtered[filtered["is_noise"]].index:
            loc = current_df.index.get_loc(idx)
            prev_val = clean_close.iloc[max(0, loc - 1)]
            clean_close.iloc[loc] = prev_val
            
        current_df["Close"] = clean_close
        
    current_df["Baseline"] = current_df["Close"].rolling(window=15, min_periods=1).mean()
    return current_df["Baseline"]

def run_harmonic_vacuum(pair="EURUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  HARMONIC VACUUM HUNTER: CYCLE-ADJUSTED MASS ({pair} @ {interval})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    df["Velocity"] = df["Baseline"].diff()
    
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    # Dynamically calculate the Local Cycle length
    # A cycle is the distance between crosses of the Baseline (Z-Score crosses 0)
    cycle_lengths = []
    last_cross = 0
    
    for i in range(1, 100):
        if (df.iloc[i]["Z_Score"] > 0 and df.iloc[i-1]["Z_Score"] <= 0) or \
           (df.iloc[i]["Z_Score"] < 0 and df.iloc[i-1]["Z_Score"] >= 0):
            if last_cross != 0:
                cycle_lengths.append(i - last_cross)
            last_cross = i
            
    current_local_cycle = np.mean(cycle_lengths) if cycle_lengths else 20
    
    mass_series = np.zeros(len(df))
    direction_series = np.zeros(len(df))
    
    current_mass = 0
    current_dir = 0
    spike_duration = 0
    
    # We will adjust the target "11" threshold based on the Harmonic Cycle logic
    # In harmonic terms, if we multiply by a scaling factor to keep numbers relatable:
    # Harmonic Mass = abs(Z) * (Duration / Local Cycle) * 100
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        z = row["Z_Score"]
        
        # Update local cycle dynamically if crossed baseline
        if (z > 0 and prev["Z_Score"] <= 0) or (z < 0 and prev["Z_Score"] >= 0):
            cycle_len = i - last_cross
            last_cross = i
            # Smooth the local cycle updates
            current_local_cycle = (current_local_cycle * 0.8) + (cycle_len * 0.2)
            
        if pd.isna(z): continue
            
        is_spike = abs(z) > 1.5 
        
        if is_spike:
            if current_dir == 0:
                current_dir = np.sign(z)
                spike_duration = 1
            elif np.sign(z) == current_dir:
                spike_duration += 1
            else:
                current_dir = np.sign(z)
                spike_duration = 1
                
            # HARMONIC MASS EQUATION
            # Instead of raw duration, we measure what percentage of the local cycle this spike has survived
            time_ratio = spike_duration / max(1, current_local_cycle)
            
            # The Intensity * Harmonic Time Ratio. 
            # We multiply by 10 to scale it so the breakout threshold remains around the 11.0 mark.
            current_mass = current_mass + (abs(z) * time_ratio * 10)
        else:
            current_mass = 0
            current_dir = 0
            spike_duration = 0
            
        mass_series[i] = current_mass
        direction_series[i] = current_dir
        
    df["Harmonic_Mass"] = mass_series
    df["Direction"] = direction_series
    
    bal = 10000.0
    pos = 0
    entry_price = 0
    trades = []
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        close = row["Close"]
        mass = row["Harmonic_Mass"]
        prev_mass = prev["Harmonic_Mass"]
        direction = row["Direction"]
        vel = row["Velocity"]
        
        if pos != 0:
            # Exit Logic: Reversal of baseline velocity or hard SL
            is_exhausted = (pos == 1 and vel < 0) or (pos == -1 and vel > 0)
            sl_hit = (pos == 1 and close < entry_price * 0.997) or (pos == -1 and close > entry_price * 1.003)
            
            if is_exhausted or sl_hit:
                pnl = bal * ((close - entry_price) / entry_price * pos) * 20
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            # Entry Logic: Harmonic Mass >= 11.0
            if mass >= 11.0 and prev_mass < 11.0:
                pos = 1 if direction == 1 else -1
                entry_price = close

    if pos != 0: trades.append(bal * ((df.iloc[-1]["Close"] - entry_price) / entry_price * pos) * 20)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"  Total Harmonic Paradigm Shifts : {len(trades)}")
    
    if trades:
        print(f"  Win Rate                : {win_rate:.1f}%")
        print(f"  Starting Balance        : $10,000.00")
        print(f"  Final Balance           : ${bal:,.2f}")
        print(f"  Net Profit              : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
    
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_harmonic_vacuum(pair="EURUSD", interval="5m", period="60d")
    run_harmonic_vacuum(pair="GBPUSD", interval="5m", period="60d")
