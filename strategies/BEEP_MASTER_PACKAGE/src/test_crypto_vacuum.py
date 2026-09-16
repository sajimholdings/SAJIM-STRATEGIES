"""
The Crypto Vacuum Hunter (Backtest)
===================================
Tests the Asymptotic Physics Engine and Gap Circuit Breakers 
on real Crypto data (BTCUSD and ETHUSD).
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
        
        if len(noise_idx) / total_candles < 0.01 and p > 1:
            break
            
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            clean_close.iloc[loc] = clean_close.iloc[max(0, loc - 1)]
        current_df["Close"] = clean_close
        
    return current_df["Close"].rolling(window=15, min_periods=1).mean()

def run_crypto_vacuum(pair="BTCUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  CRYPTO VACUUM HUNTER: {pair} ({interval}, {period})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
        
    print("[1] Purifying Data (Variance Asymptote)...")
    df["Baseline"] = get_asymptotic_quality_score(df)
    df["Velocity"] = df["Baseline"].diff()
    
    print("[2] Calculating Harmonics & Physics...")
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    cycle_lengths = []
    last_cross = 0
    current_local_cycle = 20
    
    mass_series = np.zeros(len(df))
    direction_series = np.zeros(len(df))
    threshold_series = np.ones(len(df)) * 8.0
    
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
            if current_mass > 0: historical_masses.append(current_mass)
            
        if pd.isna(z): continue
            
        # THE GAP FILTER (CIRCUIT BREAKER)
        if i > 100:
            open_price = row["Open"]
            prev_close = prev["Close"]
            gap_size = abs(open_price - prev_close)
            if pd.notna(rolling_std.iloc[i-1]) and gap_size > rolling_std.iloc[i-1] * 3:
                gap_cooldown = 20
                
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
            
        if len(historical_masses) >= 5:
            recent_masses = historical_masses[-50:]
            # Using 1.5 Std Dev (aggressive threshold) as requested by user
            dyn_thresh = np.mean(recent_masses) + (1.5 * np.std(recent_masses))
            dynamic_threshold = max(dyn_thresh, 5.0)
        else:
            dynamic_threshold = 8.0
            
        threshold_series[i] = dynamic_threshold
        mass_series[i] = current_mass
        direction_series[i] = current_dir
        
    df["Harmonic_Mass"] = mass_series
    df["Direction"] = direction_series
    df["Dynamic_Threshold"] = threshold_series
    
    print("[3] Executing Trades...")
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
            # Hard stop loss strictly at the Baseline
            sl_hit = (pos == 1 and close < row["Baseline"]) or (pos == -1 and close > row["Baseline"])
            
            # Exit Logic: Velocity Reversal
            is_exhausted = (pos == 1 and vel < 0) or (pos == -1 and vel > 0)
            
            if is_exhausted or sl_hit:
                # 50 Lots is 5,000,000 units. A 1% move on $10k with 50 lots is massive.
                # We will use standard backtest 1 Lot (100k units) to measure normal % return safely.
                pnl = bal * ((close - entry_price) / entry_price * pos) * 20 # 20x Leverage (approx 1 standard lot on 500:1)
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
    
    print(f"  Total Crypto Paradigm Shifts : {len(trades)}")
    print(f"  Win Rate                     : {win_rate:.1f}%")
    print(f"  Starting Balance             : $10,000.00")
    print(f"  Final Balance                : ${bal:,.2f}")
    print(f"  Net Profit                   : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_crypto_vacuum(pair="BTCUSD", interval="5m", period="60d")
    run_crypto_vacuum(pair="ETHUSD", interval="5m", period="60d")
