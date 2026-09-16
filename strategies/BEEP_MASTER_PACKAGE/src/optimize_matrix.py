"""
Phase 9: The Universal Mastery Matrix
=====================================
Tests different Timeframe matrices against specific pairs to find
their perfect physical environment. It tests combinations of:
Trigger Frame / Exhaustion Gate / Macro Gate
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

def run_environment_test(pair, env_name, t_trigger, t_exhaust, t_macro, dna_t1, dna_t2):
    print(f"\n[{pair}] Testing Environment: {env_name} ({t_trigger}/{t_exhaust}/{t_macro})")
    
    # Using 7d period to satisfy Yahoo Finance 1m limits
    trig_df = fetch_pair(pair, period="7d", interval=t_trigger)
    exh_df = fetch_pair(pair, period="7d", interval=t_exhaust)
    mac_df = fetch_pair(pair, period="7d", interval=t_macro)
    
    if len(trig_df) < 100 or len(exh_df) < 30 or len(mac_df) < 10:
        print(f"  Not enough data.")
        return -999999
        
    trig_df["Base_Trig"] = get_asymptote(trig_df)
    exh_df["Base_Exh"] = get_asymptote(exh_df)
    mac_df["Base_Mac"] = get_asymptote(mac_df)
    
    trig_df["Vel_Trig"] = trig_df["Base_Trig"].diff()
    exh_df["Vel_Exh"] = exh_df["Base_Exh"].diff()
    mac_df["Vel_Mac"] = mac_df["Base_Mac"].diff()
    
    trig_df["Vel_Exh"] = np.nan
    trig_df["Vel_Mac"] = np.nan
    trig_df["Base_Exh"] = np.nan
    
    for idx, row in trig_df.iterrows():
        exh_mask = exh_df.index <= idx
        if exh_mask.any():
            trig_df.at[idx, "Vel_Exh"] = exh_df.loc[exh_mask, "Vel_Exh"].iloc[-1]
            trig_df.at[idx, "Base_Exh"] = exh_df.loc[exh_mask, "Base_Exh"].iloc[-1]
            
        mac_mask = mac_df.index <= idx
        if mac_mask.any():
            trig_df.at[idx, "Vel_Mac"] = mac_df.loc[mac_mask, "Vel_Mac"].iloc[-1]
            
    trig_df.dropna(subset=["Vel_Exh", "Vel_Mac"], inplace=True)
    
    trig_df["Deviation"] = trig_df["Close"] - trig_df["Base_Trig"]
    rolling_std = trig_df["Deviation"].rolling(window=100).std()
    trig_df["Z_Score"] = trig_df["Deviation"] / rolling_std
    
    mass_series = np.zeros(len(trig_df))
    current_mass = 0
    current_dir = 0
    
    for i in range(100, len(trig_df)):
        z = trig_df["Z_Score"].iloc[i]
        if pd.isna(z): continue
        if abs(z) > 1.5:
            if current_dir == 0 or np.sign(z) != current_dir:
                current_dir = np.sign(z)
            current_mass += (abs(z) * 10) 
        else:
            current_mass = 0
            current_dir = 0
        mass_series[i] = current_mass
        
    trig_df["Mass"] = mass_series
    
    bal = 1000.0
    pos = 0
    entry_price = 0
    current_lot_size = 0
    partial_taken = False
    point_value = 100000 
    risk_pct = 0.02
    
    for i in range(100, len(trig_df)):
        row = trig_df.iloc[i]
        close = row["Close"]
        vel_trig = row["Vel_Trig"]
        vel_exh = row["Vel_Exh"]
        vel_mac = row["Vel_Mac"]
        z = row["Z_Score"]
        
        if pos != 0:
            # Trailing SL Hit (on Exhaustion timeframe)
            if (pos == 1 and close < row["Base_Exh"]) or (pos == -1 and close > row["Base_Exh"]):
                pnl = (close - entry_price) * pos * current_lot_size * point_value
                bal += pnl
                pos = 0
                continue
            
            # Exhaustion Partial
            exh_exhausted = (pos == 1 and vel_exh < 0) or (pos == -1 and vel_exh > 0)
            if exh_exhausted and not partial_taken and ((pos == 1 and close > entry_price) or (pos == -1 and close < entry_price)):
                abs_z = abs(z)
                if abs_z > dna_t1: partial_pct = 0.80
                elif abs_z > dna_t2: partial_pct = 0.50
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
                if direction == 1 and vel_exh > 0 and vel_mac > 0: gate_open = True
                if direction == -1 and vel_exh < 0 and vel_mac < 0: gate_open = True
                
                if gate_open:
                    pos = direction
                    entry_price = close
                    partial_taken = False
                    
                    sl_distance = abs(entry_price - row["Base_Exh"])
                    if sl_distance > 0:
                        current_lot_size = (bal * risk_pct) / (sl_distance * point_value)
                        current_lot_size = min(max(current_lot_size, 0.01), 50.0)
                    else:
                        pos = 0 
    
    print(f"  Final Balance: ${bal:,.2f}")
    return bal

def master_pair(pair, dna_t1, dna_t2):
    print("="*50)
    print(f" MASTERING: {pair}")
    print("="*50)
    
    envs = [
        ("The Scalper", "1m", "5m", "15m"),
        ("The Intraday", "5m", "15m", "60m"),
        ("The Swinger", "15m", "60m", "1d")
    ]
    
    best_bal = -999999
    best_env = None
    
    for name, t_trig, t_exh, t_mac in envs:
        bal = run_environment_test(pair, name, t_trig, t_exh, t_mac, dna_t1, dna_t2)
        if bal > best_bal:
            best_bal = bal
            best_env = name
            
    print(f"\n[WINNER] {pair} requires Environment: {best_env}")
    print(f"         Maximized Balance: ${best_bal:,.2f}\n")

if __name__ == "__main__":
    # We test the 3 pairs that failed the Scalper environment
    master_pair("US30", 4.0, 2.5)
    master_pair("NDX100", 3.0, 2.0)
    master_pair("ETHUSD", 2.0, 1.2)
