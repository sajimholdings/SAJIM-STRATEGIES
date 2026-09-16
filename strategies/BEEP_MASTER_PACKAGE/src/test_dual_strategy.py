"""
BEEP Dual-Module Strategy Test (The 10 vs The 11)
=================================================
Simulates the two mathematical approaches derived from the Rule of 11.

Module 1: The Snapback (Farm the Consolidations)
- Enter counter-trend when Mass is [8, 10]. Gravity is intact.
- Exit when price touches the Baseline.

Module 2: The Vacuum (Hunt the Paradigm Shifts)
- Enter with the trend the moment Mass >= 11. Gravity breaks.
- Exit when Baseline velocity flatlines (exhaustion).
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

def run_dual_strategy_test(pair="EURUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  DUAL MODULE BEEP STRATEGY TEST ({pair} @ {interval})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
        
    print("[1] Calculating True Wave Baseline & Velocity...")
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    df["Velocity"] = df["Baseline"].diff()
    df["Velocity_Std"] = df["Velocity"].rolling(window=100).std()
    
    print("[2] Calculating Mass (Intensity x Consistency)...")
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    # State tracking
    mass_series = np.zeros(len(df))
    direction_series = np.zeros(len(df))
    
    current_mass = 0
    current_dir = 0
    
    for i in range(100, len(df)):
        z = df.iloc[i]["Z_Score"]
        if pd.isna(z): continue
            
        is_spike = abs(z) > 1.5 # Lower threshold to start accumulating mass early
        
        if is_spike:
            if current_dir == 0:
                current_dir = np.sign(z)
                current_mass = abs(z)
            elif np.sign(z) == current_dir:
                current_mass += abs(z)
            else:
                current_dir = np.sign(z)
                current_mass = abs(z)
        else:
            current_mass = 0
            current_dir = 0
            
        mass_series[i] = current_mass
        direction_series[i] = current_dir
        
    df["Mass"] = mass_series
    df["Direction"] = direction_series
    
    print("[3] Simulating Trades...")
    
    # --- MODULE 1: THE SNAPBACK (MASS 8 to 10.9) ---
    bal_m1 = 10000.0
    pos_m1 = 0
    entry_m1 = 0
    trades_m1 = []
    
    # --- MODULE 2: THE VACUUM (MASS >= 11) ---
    bal_m2 = 10000.0
    pos_m2 = 0
    entry_m2 = 0
    trades_m2 = []
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        close = row["Close"]
        mass = row["Mass"]
        direction = row["Direction"]
        dev = row["Deviation"]
        prev_dev = prev["Deviation"]
        vel = row["Velocity"]
        vel_std = row["Velocity_Std"]
        
        # --- MODULE 1 LOGIC ---
        if pos_m1 != 0:
            # Exit: Price touches/crosses baseline
            crossed = (dev > 0 and prev_dev <= 0) or (dev < 0 and prev_dev >= 0)
            # Stop loss: Mass reaches 11 (Gravity Broken)
            sl_hit = mass >= 11.0
            
            if crossed or sl_hit:
                pnl = bal_m1 * ((close - entry_m1) / entry_m1 * pos_m1) * 20 # 20x leverage (micro scalping)
                bal_m1 += pnl
                trades_m1.append(pnl)
                pos_m1 = 0
        else:
            # Entry: Mass is between 8 and 10.9
            if 8.0 <= mass < 11.0:
                pos_m1 = -1 if direction == 1 else 1 # Trade opposite the spike
                entry_m1 = close
                
                
        # --- MODULE 2 LOGIC ---
        if pos_m2 != 0:
            # Exit: Velocity flatlines (exhaustion)
            is_flat = abs(vel) < (0.2 * vel_std) if pd.notna(vel_std) else False
            # Hard stop if it reverses wildly
            sl_hit = (pos_m2 == 1 and close < entry_m2 * 0.998) or (pos_m2 == -1 and close > entry_m2 * 1.002)
            
            if is_flat or sl_hit:
                pnl = bal_m2 * ((close - entry_m2) / entry_m2 * pos_m2) * 20
                bal_m2 += pnl
                trades_m2.append(pnl)
                pos_m2 = 0
        else:
            # Entry: Mass hits 11 (Breakout)
            # We only enter on the EXACT candle it breaches 11
            if mass >= 11.0 and prev["Mass"] < 11.0:
                pos_m2 = 1 if direction == 1 else -1 # Trade WITH the spike
                entry_m2 = close

    # Close open trades at end
    if pos_m1 != 0: trades_m1.append(bal_m1 * ((df.iloc[-1]["Close"] - entry_m1) / entry_m1 * pos_m1) * 20)
    if pos_m2 != 0: trades_m2.append(bal_m2 * ((df.iloc[-1]["Close"] - entry_m2) / entry_m2 * pos_m2) * 20)
        
    wins_m1 = len([t for t in trades_m1 if t > 0])
    wins_m2 = len([t for t in trades_m2 if t > 0])
    
    print(f"\n==================================================")
    print(f" MODULE 1: THE SNAPBACK (Trading Gravity)")
    print(f"==================================================")
    print(f" Total Trades : {len(trades_m1)}")
    if trades_m1:
        print(f" Win Rate     : {(wins_m1/len(trades_m1))*100:.1f}%")
        print(f" Net Profit   : ${sum(trades_m1):.2f}")
    
    print(f"\n==================================================")
    print(f" MODULE 2: THE VACUUM (Trading the Paradigm Shift)")
    print(f"==================================================")
    print(f" Total Trades : {len(trades_m2)}")
    if trades_m2:
        print(f" Win Rate     : {(wins_m2/len(trades_m2))*100:.1f}%")
        print(f" Net Profit   : ${sum(trades_m2):.2f}")
    
    print(f"\nCOMBINED NET PROFIT: ${sum(trades_m1) + sum(trades_m2):.2f}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_dual_strategy_test(pair="EURUSD", interval="5m", period="60d")
    run_dual_strategy_test(pair="GBPUSD", interval="5m", period="60d")
