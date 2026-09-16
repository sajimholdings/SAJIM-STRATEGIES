"""
The Account Flipping Playbook Matrix
====================================
Tests multiple Multi-Timeframe combinations (The Comfort Zones)
to find the ultimate balance between Accuracy (Win Rate) and Speed (Trades/Week).
This will define the Playbook for flipping accounts.
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

def run_matrix(pair, period, entry_tf, gate1_tf, gate2_tf, risk_pct=0.05):
    print(f"\n[TESTING ZONE] Entry: {entry_tf} | Gates: {gate1_tf} & {gate2_tf} | Risk: {risk_pct*100}%")
    
    entry_df = fetch_pair(pair, period=period, interval=entry_tf)
    g1_df = fetch_pair(pair, period=period, interval=gate1_tf)
    g2_df = fetch_pair(pair, period=period, interval=gate2_tf)
    
    if len(entry_df) < 200 or len(g1_df) < 50 or len(g2_df) < 20: 
        print("Not enough data for this matrix.")
        return
        
    entry_df["Base_Entry"] = get_asymptote(entry_df)
    g1_df["Base_G1"] = get_asymptote(g1_df)
    g2_df["Base_G2"] = get_asymptote(g2_df)
    
    entry_df["Vel_Entry"] = entry_df["Base_Entry"].diff()
    g1_df["Vel_G1"] = g1_df["Base_G1"].diff()
    g2_df["Vel_G2"] = g2_df["Base_G2"].diff()
    
    # Synchronize Gates
    entry_df["Base_G1"] = np.nan
    entry_df["Vel_G1"] = np.nan
    entry_df["Base_G2"] = np.nan
    entry_df["Vel_G2"] = np.nan
    
    for idx, row in entry_df.iterrows():
        g1_mask = g1_df.index <= idx
        if g1_mask.any():
            entry_df.at[idx, "Base_G1"] = g1_df.loc[g1_mask, "Base_G1"].iloc[-1]
            entry_df.at[idx, "Vel_G1"] = g1_df.loc[g1_mask, "Vel_G1"].iloc[-1]
            
        g2_mask = g2_df.index <= idx
        if g2_mask.any():
            entry_df.at[idx, "Base_G2"] = g2_df.loc[g2_mask, "Base_G2"].iloc[-1]
            entry_df.at[idx, "Vel_G2"] = g2_df.loc[g2_mask, "Vel_G2"].iloc[-1]
            
    entry_df.dropna(subset=["Base_G1", "Base_G2"], inplace=True)
    
    # Physics
    entry_df["Deviation"] = entry_df["Close"] - entry_df["Base_Entry"]
    rolling_std = entry_df["Deviation"].rolling(window=100).std()
    entry_df["Z_Score"] = entry_df["Deviation"] / rolling_std
    
    mass_series = np.zeros(len(entry_df))
    current_mass = 0
    current_dir = 0
    
    for i in range(100, len(entry_df)):
        z = entry_df["Z_Score"].iloc[i]
        if pd.isna(z): continue
        if abs(z) > 1.5:
            if current_dir == 0 or np.sign(z) != current_dir:
                current_dir = np.sign(z)
            current_mass += (abs(z) * 10) 
        else:
            current_mass = 0
            current_dir = 0
        mass_series[i] = current_mass
        
    entry_df["Mass"] = mass_series
    
    bal = 1000.0 # Flip a $1000 account
    pos = 0
    entry_price = 0
    current_lot_size = 0
    trades = []
    
    point_value = 100000 
    
    for i in range(100, len(entry_df)):
        row = entry_df.iloc[i]
        close = row["Close"]
        vel_entry = row["Vel_Entry"]
        vel_g1 = row["Vel_G1"]
        vel_g2 = row["Vel_G2"]
        
        if pos != 0:
            sl_price = row["Base_G2"] # Macro Gate SL
            is_exhausted = (pos == 1 and vel_entry < 0) or (pos == -1 and vel_entry > 0)
            sl_hit = (pos == 1 and close < sl_price) or (pos == -1 and close > sl_price)
            
            if is_exhausted or sl_hit:
                pnl = (close - entry_price) * pos * current_lot_size * point_value
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            if row["Mass"] > 30.0:
                direction = np.sign(row["Z_Score"])
                
                gate_open = False
                if direction == 1 and vel_g1 > 0 and vel_g2 > 0: gate_open = True
                if direction == -1 and vel_g1 < 0 and vel_g2 < 0: gate_open = True
                
                if gate_open:
                    pos = direction
                    entry_price = close
                    
                    risk_amount = bal * risk_pct
                    sl_distance = abs(entry_price - row["Base_G2"])
                    
                    if sl_distance > 0:
                        current_lot_size = risk_amount / (sl_distance * point_value)
                        current_lot_size = min(max(current_lot_size, 0.01), 100.0) 
                    else:
                        pos = 0 
                    
    if pos != 0: 
        pnl = (entry_df.iloc[-1]["Close"] - entry_price) * pos * current_lot_size * point_value
        trades.append(pnl)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"  Total Trades                 : {len(trades)}")
    print(f"  Win Rate                     : {win_rate:.1f}%")
    print(f"  Starting Balance             : $1000.00")
    print(f"  Final Balance                : ${bal:,.2f}")
    if bal > 0:
        print(f"  Flip Multiplier              : {bal/1000:.1f}x")
    
if __name__ == "__main__":
    print("="*60)
    print(" ACCOUNT FLIPPING PLAYBOOK MATRIX (GBPUSD, 7 Days, 5% Risk)")
    print("="*60)
    
    # The Hyper-Scalper (Fast)
    run_matrix("GBPUSD", period="7d", entry_tf="1m", gate1_tf="5m", gate2_tf="15m", risk_pct=0.05)
    
    # The Intraday Flip (Balanced)
    run_matrix("GBPUSD", period="7d", entry_tf="5m", gate1_tf="15m", gate2_tf="60m", risk_pct=0.05)
    
    # The Swing Flip (Slow but deadly accurate)
    run_matrix("GBPUSD", period="7d", entry_tf="60m", gate1_tf="90m", gate2_tf="1d", risk_pct=0.05)
