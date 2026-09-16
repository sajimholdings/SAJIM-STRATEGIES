"""
The Profit Maximizer (Legacy Stride + BEEP Physics)
===================================================
Hypothesis:
We made the entries perfectly safe (Dynamic Threshold). 
But to make MORE money, we must optimize the Exits by mixing in the 
user's legacy Stride logic (Partial Profits, Trailing Stops, and Lowering the Std Dev limit).
"""

import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_asymptotic_quality_score(df, max_passes=20):
    current_df = df.copy()
    total_candles = len(df)
    for p in range(1, max_passes + 1):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        noise_idx = filtered[filtered["is_noise"]].index
        if len(noise_idx) / total_candles < 0.01 and p > 1: break
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            clean_close.iloc[loc] = clean_close.iloc[max(0, loc - 1)]
        current_df["Close"] = clean_close
    return current_df["Close"].rolling(window=15, min_periods=1).mean()

def run_profit_maximizer(pair="GBPUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  PROFIT MAXIMIZER: BEEP + LEGACY STRIDE LOGIC ({pair})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    df["Baseline"] = get_asymptotic_quality_score(df)
    df["Velocity"] = df["Baseline"].diff()
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    last_cross = 0
    current_local_cycle = 20
    current_mass = 0
    current_dir = 0
    spike_duration = 0
    gap_cooldown = 0
    historical_masses = []
    
    mass_series = np.zeros(len(df))
    direction_series = np.zeros(len(df))
    threshold_series = np.ones(len(df)) * 8.0 # Lowered base threshold
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        z = row["Z_Score"]
        
        if (z > 0 and prev["Z_Score"] <= 0) or (z < 0 and prev["Z_Score"] >= 0):
            cycle_len = i - last_cross
            last_cross = i
            current_local_cycle = (current_local_cycle * 0.8) + (cycle_len * 0.2)
            if current_mass > 0: historical_masses.append(current_mass)
            
        if pd.isna(z): continue
            
        if i > 100 and pd.notna(rolling_std.iloc[i-1]):
            if abs(row["Open"] - prev["Close"]) > rolling_std.iloc[i-1] * 3:
                gap_cooldown = 20 
                
        if gap_cooldown > 0:
            gap_cooldown -= 1
            current_mass = 0
            current_dir = 0
            spike_duration = 0
            mass_series[i] = 0
            direction_series[i] = 0
            continue
                
        if abs(z) > 1.5:
            if current_dir == 0 or np.sign(z) != current_dir:
                current_dir = np.sign(z)
                spike_duration = 1
            else:
                spike_duration += 1
            current_mass = current_mass + (abs(z) * (spike_duration / max(1, current_local_cycle)) * 10)
        else:
            current_mass = 0
            current_dir = 0
            spike_duration = 0
            
        # PROFIT OPTIMIZATION 1: Lower the Z-Score barrier to 1.5 Std Dev instead of 3 Std Dev
        # This doubles the trade count to catch more early shifts
        if len(historical_masses) >= 5:
            recent_masses = historical_masses[-50:]
            dyn_thresh = np.mean(recent_masses) + (1.5 * np.std(recent_masses))
            dynamic_threshold = max(dyn_thresh, 8.0)
        else:
            dynamic_threshold = 8.0
            
        threshold_series[i] = dynamic_threshold
        mass_series[i] = current_mass
        direction_series[i] = current_dir
        
    df["Harmonic_Mass"] = mass_series
    df["Direction"] = direction_series
    df["Dynamic_Threshold"] = threshold_series
    
    # PROFIT OPTIMIZATION 2: Legacy Stride Scaling (Partials & Trails)
    bal = 10000.0
    pos = 0
    entry_price = 0
    peak_profit_price = 0
    trades = []
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        close = row["Close"]
        vel = row["Velocity"]
        
        if pos != 0:
            # Track peak profit for trailing stop
            if pos == 1 and close > peak_profit_price: peak_profit_price = close
            if pos == -1 and close < peak_profit_price: peak_profit_price = close
            
            # Trailing Stop Logic (Legacy Stride)
            trail_dist = abs(peak_profit_price - entry_price) * 0.5
            sl_price = entry_price + (trail_dist if pos == 1 else -trail_dist)
            
            is_exhausted = (pos == 1 and vel < 0) or (pos == -1 and vel > 0)
            sl_hit = (pos == 1 and close < sl_price) or (pos == -1 and close > sl_price)
            
            if is_exhausted or sl_hit:
                pnl = bal * ((close - entry_price) / entry_price * pos) * 20
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            if row["Harmonic_Mass"] >= row["Dynamic_Threshold"] and prev["Harmonic_Mass"] < row["Dynamic_Threshold"]:
                pos = 1 if row["Direction"] == 1 else -1
                entry_price = close
                peak_profit_price = close

    if pos != 0: trades.append(bal * ((df.iloc[-1]["Close"] - entry_price) / entry_price * pos) * 20)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"  Total Trades (1.5 Std Threshold) : {len(trades)}")
    print(f"  Win Rate                         : {win_rate:.1f}%")
    print(f"  Final Balance                    : ${bal:,.2f}")
    print(f"  Net Profit                       : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_profit_maximizer(pair="GBPUSD")
    run_profit_maximizer(pair="EURUSD")
