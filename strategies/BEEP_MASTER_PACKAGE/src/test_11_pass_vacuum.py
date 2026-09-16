"""
The 11-Pass Harmonic Vacuum Hunter (Backtest)
=============================================
This script tests the final MT5 logic on historical data.
1. Recursively filters the data 11 times to get the Pure Quality Score.
2. Calculates Harmonic Cycle on the purified baseline.
3. Trades at Mass >= 11 (The Paradigm Shift).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_asymptotic_quality_score(df, max_passes=20):
    """Dynamically calculates the exact number of passes by finding the variance asymptote."""
    current_df = df.copy()
    total_candles = len(df)
    
    for p in range(1, max_passes + 1):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        
        noise_idx = filtered[filtered["is_noise"]].index
        noise_ratio = len(noise_idx) / total_candles
        
        if noise_ratio < 0.01 and p > 1:
            # Asymptote reached: less than 1% noise detected. Stop filtering to preserve structure.
            # print(f"    [Asymptote Reached at Pass {p}]")
            break
            
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            prev_val = clean_close.iloc[max(0, loc - 1)]
            clean_close.iloc[loc] = prev_val
            
        current_df["Close"] = clean_close
        
    baseline = current_df["Close"].rolling(window=15, min_periods=1).mean()
    return baseline

def run_asymptotic_vacuum(pair="XAUUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  ASYMPTOTIC VACUUM HUNTER: THE HOLY GRAIL ({pair} @ {interval})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
        
    print(f"[1] Finding Variance Asymptote & Purifying Data...")
    df["Baseline"] = get_asymptotic_quality_score(df)
    df["Velocity"] = df["Baseline"].diff()
    
    print("[2] Calculating Harmonics & Physics...")
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    cycle_lengths = []
    last_cross = 0
    
    # Init local cycle
    for i in range(1, 100):
        if (df.iloc[i]["Z_Score"] > 0 and df.iloc[i-1]["Z_Score"] <= 0) or \
           (df.iloc[i]["Z_Score"] < 0 and df.iloc[i-1]["Z_Score"] >= 0):
            if last_cross != 0:
                cycle_lengths.append(i - last_cross)
            last_cross = i
            
    current_local_cycle = np.mean(cycle_lengths) if cycle_lengths else 20
    
    mass_series = np.zeros(len(df))
    direction_series = np.zeros(len(df))
    threshold_series = np.ones(len(df)) * 11.0
    
    current_mass = 0
    current_dir = 0
    spike_duration = 0
    gap_cooldown = 0
    
    historical_masses = []
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        z = row["Z_Score"]
        
        if (z > 0 and prev["Z_Score"] <= 0) or (z < 0 and prev["Z_Score"] >= 0):
            cycle_len = i - last_cross
            last_cross = i
            current_local_cycle = (current_local_cycle * 0.8) + (cycle_len * 0.2)
            
            # When a cycle ends, if we had a mass, record it for the dynamic threshold
            if current_mass > 0:
                historical_masses.append(current_mass)
            
        if pd.isna(z): continue
            
        # THE GAP FILTER (CIRCUIT BREAKER)
        if i > 100:
            open_price = row["Open"]
            prev_close = prev["Close"]
            gap_size = abs(open_price - prev_close)
            if pd.notna(rolling_std.iloc[i-1]) and gap_size > rolling_std.iloc[i-1] * 3:
                gap_cooldown = 20 # Freeze engine for 20 candles to let baseline catch up
                
        if gap_cooldown > 0:
            gap_cooldown -= 1
            current_mass = 0
            current_dir = 0
            spike_duration = 0
            mass_series[i] = 0
            direction_series[i] = 0
            continue
                
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
                
            time_ratio = spike_duration / max(1, current_local_cycle)
            current_mass = current_mass + (abs(z) * time_ratio * 10)
        else:
            current_mass = 0
            current_dir = 0
            spike_duration = 0
            
        # DYNAMIC THRESHOLD MATH
        if len(historical_masses) >= 5:
            recent_masses = historical_masses[-50:]
            dynamic_threshold = np.mean(recent_masses) + (3 * np.std(recent_masses))
            # Put a safety floor so it doesn't overtrade on dead days
            dynamic_threshold = max(dynamic_threshold, 8.0)
        else:
            dynamic_threshold = 11.0
            
        threshold_series[i] = dynamic_threshold
        mass_series[i] = current_mass
        direction_series[i] = current_dir
        
    df["Harmonic_Mass"] = mass_series
    df["Direction"] = direction_series
    df["Dynamic_Threshold"] = threshold_series
    
    print("[3] Executing Trades at Dynamic Threshold...")
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
        threshold = row["Dynamic_Threshold"]
        direction = row["Direction"]
        vel = row["Velocity"]
        
        if pos != 0:
            is_exhausted = (pos == 1 and vel < 0) or (pos == -1 and vel > 0)
            sl_hit = (pos == 1 and close < entry_price * 0.997) or (pos == -1 and close > entry_price * 1.003)
            
            if is_exhausted or sl_hit:
                pnl = bal * ((close - entry_price) / entry_price * pos) * 20
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            if mass >= threshold and prev_mass < threshold:
                pos = 1 if direction == 1 else -1
                entry_price = close

    if pos != 0: trades.append(bal * ((df.iloc[-1]["Close"] - entry_price) / entry_price * pos) * 20)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"\n  Total Holy Grail Paradigm Shifts : {len(trades)}")
    
    if trades:
        print(f"  Win Rate                         : {win_rate:.1f}%")
        print(f"  Starting Balance                 : $10,000.00")
        print(f"  Final Balance                    : ${bal:,.2f}")
        print(f"  Net Profit                       : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
    
    print(f"{'='*70}\n")

if __name__ == "__main__":
    # Test on Gold, EURUSD, and GBPUSD using the Variance Asymptote
    run_asymptotic_vacuum(pair="XAUUSD", interval="5m", period="60d")
    run_asymptotic_vacuum(pair="GBPUSD", interval="5m", period="60d")
    run_asymptotic_vacuum(pair="EURUSD", interval="5m", period="60d")
