"""
Phase 10: The Master Synchronization Test
=========================================
Tests the theory of combining M1, M5, and M15 into a single Master Score.
- NO ASSUMED WEIGHTS: Weights are derived directly from the physical Mass of each timeframe.
- NO FIXED RISK: Risk % dynamically scales based on the absolute strength of the baseline.
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_asymptote(df):
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

def calculate_mass(df):
    df["Deviation"] = df["Close"] - df["Base"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    mass_series = np.zeros(len(df))
    current_mass = 0
    current_dir = 0
    
    for i in range(100, len(df)):
        z = df["Z_Score"].iloc[i]
        if pd.isna(z): continue
        if abs(z) > 1.5:
            if current_dir == 0 or np.sign(z) != current_dir:
                current_dir = np.sign(z)
            current_mass += (abs(z) * 10) 
        else:
            current_mass = 0
            current_dir = 0
        mass_series[i] = current_mass
        
    df["Mass"] = mass_series
    return df

def test_sync_theory(pair):
    print(f"\n[SYNC THEORY TEST] {pair}")
    
    m1_df = fetch_pair(pair, period="7d", interval="1m")
    m5_df = fetch_pair(pair, period="7d", interval="5m")
    m15_df = fetch_pair(pair, period="7d", interval="15m")
    
    if len(m1_df) < 100 or len(m5_df) < 30 or len(m15_df) < 10:
        print(f"  Not enough data.")
        return
        
    m1_df["Base"] = get_asymptote(m1_df)
    m5_df["Base"] = get_asymptote(m5_df)
    m15_df["Base"] = get_asymptote(m15_df)
    
    m1_df["Vel"] = m1_df["Base"].diff()
    m5_df["Vel"] = m5_df["Base"].diff()
    m15_df["Vel"] = m15_df["Base"].diff()
    
    m1_df = calculate_mass(m1_df)
    m5_df = calculate_mass(m5_df)
    m15_df = calculate_mass(m15_df)
    
    # Synchronize all data into M1 dataframe frame-by-frame
    m1_df["M5_Vel"] = np.nan
    m1_df["M5_Mass"] = np.nan
    m1_df["M15_Vel"] = np.nan
    m1_df["M15_Mass"] = np.nan
    
    for idx, row in m1_df.iterrows():
        m5_mask = m5_df.index <= idx
        if m5_mask.any():
            m1_df.at[idx, "M5_Vel"] = m5_df.loc[m5_mask, "Vel"].iloc[-1]
            m1_df.at[idx, "M5_Mass"] = m5_df.loc[m5_mask, "Mass"].iloc[-1]
            
        m15_mask = m15_df.index <= idx
        if m15_mask.any():
            m1_df.at[idx, "M15_Vel"] = m15_df.loc[m15_mask, "Vel"].iloc[-1]
            m1_df.at[idx, "M15_Mass"] = m15_df.loc[m15_mask, "Mass"].iloc[-1]
            
    m1_df.dropna(subset=["M5_Vel", "M15_Vel"], inplace=True)
    
    # Run the Simulation
    bal = 1000.0
    pos = 0
    entry_price = 0
    current_lot_size = 0
    point_value = 100000 
    
    # Variables to track to compare against Old System
    trades_taken = 0
    wins = 0
    
    for i in range(100, len(m1_df)):
        row = m1_df.iloc[i]
        close = row["Close"]
        
        # DYNAMIC WEIGHTS (Derived strictly from physical Mass)
        mass_m1 = row["Mass"]
        mass_m5 = row["M5_Mass"]
        mass_m15 = row["M15_Mass"]
        
        total_mass = mass_m1 + mass_m5 + mass_m15
        if total_mass == 0:
            weight_m1, weight_m5, weight_m15 = 0, 0, 0
        else:
            weight_m1 = mass_m1 / total_mass
            weight_m5 = mass_m5 / total_mass
            weight_m15 = mass_m15 / total_mass
            
        # THE MASTER SCORE
        # Sign represents direction, magnitude represents pure synchronized momentum
        vel_m1 = row["Vel"]
        vel_m5 = row["M5_Vel"]
        vel_m15 = row["M15_Vel"]
        
        master_score = (vel_m1 * weight_m1) + (vel_m5 * weight_m5) + (vel_m15 * weight_m15)
        
        if pos != 0:
            # Dynamic Exit: If the Master Score flips violently against us, close entirely.
            # We don't use arbitrary trailing stops here, we use the pure synchronized physics.
            if (pos == 1 and master_score < -0.001) or (pos == -1 and master_score > 0.001):
                pnl = (close - entry_price) * pos * current_lot_size * point_value
                bal += pnl
                if pnl > 0: wins += 1
                pos = 0
                continue
        else:
            # Entry: Only enter when there is massive synchronized pressure
            if total_mass > 50.0 and abs(master_score) > 0.001:
                # Dynamic Risk: If total mass is huge, risk up to 10%. If low, risk 1%.
                # Assumes "Max Expected Mass" of around 300 for extreme trends.
                risk_pct = min((total_mass / 300.0) * 0.10, 0.10) # Max 10% risk
                risk_pct = max(risk_pct, 0.01) # Min 1% risk
                
                direction = np.sign(master_score)
                
                # Check alignment with M15 macro reality before diving in
                if (direction == 1 and vel_m15 > 0) or (direction == -1 and vel_m15 < 0):
                    pos = direction
                    entry_price = close
                    trades_taken += 1
                    
                    sl_distance = abs(entry_price - row["Base"]) 
                    if sl_distance > 0:
                        current_lot_size = (bal * risk_pct) / (sl_distance * point_value)
                        current_lot_size = min(max(current_lot_size, 0.01), 50.0)
                    else:
                        pos = 0
                        
    win_rate = (wins / trades_taken * 100) if trades_taken > 0 else 0
    print(f"  Final Balance : ${bal:,.2f}")
    print(f"  Trades Taken  : {trades_taken}")
    print(f"  Win Rate      : {win_rate:.1f}%")
    return bal

if __name__ == "__main__":
    print("="*60)
    print(" PHASE 10: MASTER SYNCHRONIZATION TEST")
    print(" Testing purely dynamic weights and dynamic risk of ruin scaling")
    print("="*60)
    
    test_sync_theory("GBPUSD")
    test_sync_theory("BTCUSD")
    test_sync_theory("XAUUSD")
