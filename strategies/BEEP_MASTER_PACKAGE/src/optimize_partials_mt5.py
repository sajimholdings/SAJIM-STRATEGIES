"""
The BEEP Dynamic Optimization Matrix (MT5 DIRECT FEED)
======================================================
This script bypasses Yahoo Finance entirely. It pulls continuous, flawless 
historical data directly from your live MetaTrader 5 broker to sequence the DNA.
"""

import sys
import os
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

sys.path.insert(0, os.path.dirname(__file__))
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

def get_mt5_history(symbol, timeframe, count):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "tick_volume": "Volume"}, inplace=True)
    return df

def optimize_pair_mt5(pair):
    print(f"\n[OPTIMIZING DNA VIA MT5] {pair}")
    
    # 7 Days of M1 is roughly 10,000 candles.
    m1_df = get_mt5_history(pair, mt5.TIMEFRAME_M1, 10000)
    m5_df = get_mt5_history(pair, mt5.TIMEFRAME_M5, 2000)
    m15_df = get_mt5_history(pair, mt5.TIMEFRAME_M15, 670)
    
    if m1_df is None or len(m1_df) < 200 or m5_df is None or m15_df is None: 
        print(f"  Not enough MT5 data for {pair}.")
        return None
        
    m1_df["Base_M1"] = get_asymptote(m1_df)
    m5_df["Base_M5"] = get_asymptote(m5_df)
    m15_df["Base_M15"] = get_asymptote(m15_df)
    
    m1_df["Vel_M1"] = m1_df["Base_M1"].diff()
    m5_df["Vel_M5"] = m5_df["Base_M5"].diff()
    m15_df["Vel_M15"] = m15_df["Base_M15"].diff()
    
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
    
    thresholds = [
        (4.0, 2.5),
        (3.0, 2.0),
        (2.5, 1.5),
        (2.0, 1.2),
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
                if (pos == 1 and close < row["Base_M5"]) or (pos == -1 and close > row["Base_M5"]):
                    pnl = (close - entry_price) * pos * current_lot_size * point_value
                    bal += pnl
                    pos = 0
                    continue
                
                m5_exhausted = (pos == 1 and vel_m5 < 0) or (pos == -1 and vel_m5 > 0)
                if m5_exhausted and not partial_taken and ((pos == 1 and close > entry_price) or (pos == -1 and close < entry_price)):
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
                        
                        sl_distance = abs(entry_price - row["Base_M5"])
                        if sl_distance > 0:
                            current_lot_size = (bal * risk_pct) / (sl_distance * point_value)
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
    if not mt5.initialize():
        print("initialize() failed, error code =", mt5.last_error())
        sys.exit()
        
    print("="*60)
    print(" BEEP MT5 DIRECT FEED OPTIMIZATION")
    print(" Bypassing Yahoo Finance. Using flawless broker data.")
    print("="*60)
    
    # Test the Danger Zones
    optimize_pair_mt5("US30")
    optimize_pair_mt5("NDX100")
    
    # Test a few others to verify
    optimize_pair_mt5("BTCUSD")
    optimize_pair_mt5("GBPUSD")
    
    mt5.shutdown()
