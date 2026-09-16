"""
The BEEP Dynamic Optimization Matrix
====================================
This script tests different Z-Score thresholds and Partial Close percentages
across specific pairs to find the unique "DNA" of each living organism.
No universal variables. Every pair has its own heartbeat.
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

def optimize_pair(pair, period="7d"):
    print(f"\n[OPTIMIZING DNA] {pair}")
    
    m1_df = fetch_pair(pair, period=period, interval="1m")
    m5_df = fetch_pair(pair, period=period, interval="5m")
    m15_df = fetch_pair(pair, period=period, interval="15m")
    
    if len(m1_df) < 200 or len(m5_df) < 50 or len(m15_df) < 20: 
        print(f"Not enough data for {pair}.")
        return None
        
    m1_df["Base_M1"] = get_asymptote(m1_df)
    m5_df["Base_M5"] = get_asymptote(m5_df)
    m15_df["Base_M15"] = get_asymptote(m15_df)
    
    m1_df["Vel_M1"] = m1_df["Base_M1"].diff()
    m5_df["Vel_M5"] = m5_df["Base_M5"].diff()
    m15_df["Vel_M15"] = m15_df["Base_M15"].diff()
    
    # Synchronize Gates
    m1_df["Vel_M5"] = np.nan
    m1_df["Vel_M15"] = np.nan
    m1_df["Base_M5"] = np.nan
    
    for idx, row in m1_df.iterrows():
        m5_mask = m5_df.index <= idx
        if m5_mask.any():
            m1_df.at[idx, "Vel_M5"] = m5_df.loc[m5_mask, "Vel_M5"].iloc[-1]
            m1_df.at[idx, "Base_M5"] = m5_df.loc[m5_mask, "Base_M5"].iloc[-1]
            
        m15_mask = m15_df.index <= idx
        if m15_mask.any():
            m1_df.at[idx, "Vel_M15"] = m15_df.loc[m15_mask, "Vel_M15"].iloc[-1]
            
    m1_df.dropna(subset=["Vel_M5", "Vel_M15"], inplace=True)
    
    # Physics
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
            current_mass += (abs(z) * 10) 
        else:
            current_mass = 0
            current_dir = 0
        mass_series[i] = current_mass
        
    m1_df["Mass"] = mass_series
    
    # Optimization Grid
    # We test combinations of (Threshold 1, Threshold 2) where:
    # Z > T1 -> Close 80%
    # Z > T2 -> Close 50%
    # Z <= T2 -> Close 25%
    
    thresholds = [
        (3.0, 2.0), # The default I suggested
        (4.0, 2.5), # Extremely volatile pair (Crypto)
        (2.0, 1.2), # Very tight pair (Forex)
        (2.5, 1.5), # Medium volatility
    ]
    
    best_pnl = -999999
    best_dna = None
    
    point_value = 100000 
    risk_pct = 0.02
    
    for (t1, t2) in thresholds:
        bal = 1000.0
        pos = 0
        entry_price = 0
        current_lot_size = 0
        partial_taken = False
        
        for i in range(100, len(m1_df)):
            row = m1_df.iloc[i]
            close = row["Close"]
            vel_m1 = row["Vel_M1"]
            vel_m5 = row["Vel_M5"]
            vel_m15 = row["Vel_M15"]
            z = row["Z_Score"]
            
            if pos != 0:
                # M5 Trailing SL Hit
                if (pos == 1 and close < row["Base_M5"]) or (pos == -1 and close > row["Base_M5"]):
                    pnl = (close - entry_price) * pos * current_lot_size * point_value
                    bal += pnl
                    pos = 0
                    continue
                
                # Dynamic Partial Logic on M5 Exhaustion
                m5_exhausted = (pos == 1 and vel_m5 < 0) or (pos == -1 and vel_m5 > 0)
                if m5_exhausted and not partial_taken and (pos == 1 and close > entry_price) or (pos == -1 and close < entry_price):
                    
                    # DNA Check
                    abs_z = abs(z)
                    if abs_z > t1: partial_pct = 0.80
                    elif abs_z > t2: partial_pct = 0.50
                    else: partial_pct = 0.25
                    
                    partial_lot = current_lot_size * partial_pct
                    pnl = (close - entry_price) * pos * partial_lot * point_value
                    bal += pnl
                    current_lot_size -= partial_lot
                    partial_taken = True
                    
            else:
                if row["Mass"] > 30.0:
                    direction = np.sign(row["Z_Score"])
                    gate_open = False
                    if direction == 1 and vel_m5 > 0 and vel_m15 > 0: gate_open = True
                    if direction == -1 and vel_m5 < 0 and vel_m15 < 0: gate_open = True
                    
                    if gate_open:
                        pos = direction
                        entry_price = close
                        partial_taken = False
                        
                        risk_amount = bal * risk_pct
                        sl_distance = abs(entry_price - row["Base_M5"]) # Simplified SL dist
                        
                        if sl_distance > 0:
                            current_lot_size = risk_amount / (sl_distance * point_value)
                            current_lot_size = min(max(current_lot_size, 0.01), 50.0) 
                        else:
                            pos = 0 
        
        if bal > best_pnl:
            best_pnl = bal
            best_dna = (t1, t2)
            
    print(f"  Best DNA (T1/T2) : {best_dna[0]} / {best_dna[1]}")
    print(f"  Final Balance    : ${best_pnl:,.2f}")
    return {"pair": pair, "t1": best_dna[0], "t2": best_dna[1], "bal": best_pnl}
    
if __name__ == "__main__":
    print("="*60)
    print(" BEEP DYNAMIC DNA OPTIMIZATION")
    print(" Finding the exact Z-Score Exit thresholds per pair")
    print("="*60)
    
    # The Majors
    optimize_pair("AUDUSD")
    optimize_pair("USDCAD")
    
    # The Indices & Commodities
    optimize_pair("US30")
    optimize_pair("NDX100")
    optimize_pair("GBPJPY")
    optimize_pair("USOIL")
    
    # The Crypto
    optimize_pair("ETHUSD")
