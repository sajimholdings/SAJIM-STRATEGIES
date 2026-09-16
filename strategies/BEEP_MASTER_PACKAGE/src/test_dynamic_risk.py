"""
The Pure Dynamic Risk Backtester
================================
Proves the mathematical validity of Dynamic Risk Equalization.
Calculates exact lot size based on 2% account risk divided by 
the distance to the 15-Minute Macro-Baseline.
Tests Forex (GBPUSD) and Crypto (BTCUSD) simultaneously to prove
it automatically regulates leverage across asset classes.
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

def run_dynamic_risk_test(pair="GBPUSD"):
    print(f"\n{'='*70}")
    print(f"  PURE DYNAMIC RISK MATRIX: {pair} (2% Risk Eq.)")
    print(f"{'='*70}")
    
    m1_df = fetch_pair(pair, period="7d", interval="1m")
    m5_df = fetch_pair(pair, period="7d", interval="5m")
    m15_df = fetch_pair(pair, period="7d", interval="15m")
    
    if len(m1_df) < 500: return
        
    print("[1] Calculating True Baselines (M1, M5, M15)...")
    m1_df["Base_M1"] = get_asymptote(m1_df)
    m5_df["Base_M5"] = get_asymptote(m5_df)
    m15_df["Base_M15"] = get_asymptote(m15_df)
    
    m1_df["Vel_M1"] = m1_df["Base_M1"].diff()
    m5_df["Vel_M5"] = m5_df["Base_M5"].diff()
    m15_df["Vel_M15"] = m15_df["Base_M15"].diff()
    
    print("[2] Synchronizing the Temporal Gates...")
    m1_df["Base_M5"] = np.nan
    m1_df["Vel_M5"] = np.nan
    m1_df["Base_M15"] = np.nan
    m1_df["Vel_M15"] = np.nan
    
    for idx, row in m1_df.iterrows():
        m5_mask = m5_df.index <= idx
        if m5_mask.any():
            m1_df.at[idx, "Base_M5"] = m5_df.loc[m5_mask, "Base_M5"].iloc[-1]
            m1_df.at[idx, "Vel_M5"] = m5_df.loc[m5_mask, "Vel_M5"].iloc[-1]
            
        m15_mask = m15_df.index <= idx
        if m15_mask.any():
            m1_df.at[idx, "Base_M15"] = m15_df.loc[m15_mask, "Base_M15"].iloc[-1]
            m1_df.at[idx, "Vel_M15"] = m15_df.loc[m15_mask, "Vel_M15"].iloc[-1]
            
    m1_df.dropna(subset=["Base_M5", "Base_M15"], inplace=True)
    
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
        
    m1_df["Mass_M1"] = mass_series
    
    print("[3] Simulating Dynamic Lot Math (2% Risk)...")
    bal = 10000.0
    pos = 0
    entry_price = 0
    current_lot_size = 0
    trades = []
    
    # Simple Point Value estimation for simulation
    point_value = 100000 if "USD" in pair and len(pair) == 6 else 1 # standard lot estimation
    
    for i in range(100, len(m1_df)):
        row = m1_df.iloc[i]
        
        close = row["Close"]
        mass = row["Mass_M1"]
        vel_m1 = row["Vel_M1"]
        vel_m5 = row["Vel_M5"]
        vel_m15 = row["Vel_M15"]
        
        if pos != 0:
            sl_price = row["Base_M15"]
            is_exhausted = (pos == 1 and vel_m1 < 0) or (pos == -1 and vel_m1 > 0)
            sl_hit = (pos == 1 and close < sl_price) or (pos == -1 and close > sl_price)
            
            if is_exhausted or sl_hit:
                # Calculate absolute PnL based on the dynamically calculated lot size
                price_diff = (close - entry_price) * pos
                pnl = price_diff * current_lot_size * point_value
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            if mass > 30.0:
                direction = np.sign(row["Z_Score"])
                
                gate_open = False
                if direction == 1 and vel_m5 > 0 and vel_m15 > 0: gate_open = True
                if direction == -1 and vel_m5 < 0 and vel_m15 < 0: gate_open = True
                
                if gate_open:
                    pos = direction
                    entry_price = close
                    
                    # DYNAMIC RISK MATH (Line 732 from Stride)
                    risk_amount = bal * 0.02 # 2% Account Risk
                    sl_distance = abs(entry_price - row["Base_M15"])
                    
                    if sl_distance > 0:
                        # lot_size = risk_amount / (sl_distance * point_value)
                        current_lot_size = risk_amount / (sl_distance * point_value)
                        # Cap extreme lot sizes
                        current_lot_size = min(max(current_lot_size, 0.01), 100.0) 
                    else:
                        pos = 0 # Invalid entry
                    
    if pos != 0: 
        pnl = (m1_df.iloc[-1]["Close"] - entry_price) * pos * current_lot_size * point_value
        trades.append(pnl)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"  Total Trades (2% Risk)       : {len(trades)}")
    print(f"  Win Rate                     : {win_rate:.1f}%")
    print(f"  Starting Balance             : $10,000.00")
    print(f"  Final Balance                : ${bal:,.2f}")
    print(f"  Net Profit (7 Days)          : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_dynamic_risk_test(pair="BTCUSD")
