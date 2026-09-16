"""
Phase 13: Temporal Optimization (The Harmonic Cycles)
=====================================================
Extracts the exact trading hours where each specific pair generates 
profit, and identifies the chop hours that bleed the account.
Uses pure physical MT5 broker data.
"""

import sys
import os
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from beep_filter import BeepFilter

# The exact DNA we sequenced for each pair
DNA_THRESHOLDS = {
    "BTCUSD": (2.0, 1.2),
    "XAUUSD": (2.5, 1.5),
    "US30":   (2.5, 1.5),
    "NDX100": (2.0, 1.2),
    "GBPUSD": (4.0, 2.5),
    "EURUSD": (3.0, 2.0)
}

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
    if rates is None or len(rates) == 0: return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "tick_volume": "Volume"}, inplace=True)
    return df

def analyze_time_cycles(pair):
    print(f"\n[ANALYZING HARMONIC CYCLES] {pair}")
    
    m1_df = get_mt5_history(pair, mt5.TIMEFRAME_M1, 15000) # ~10 days
    m5_df = get_mt5_history(pair, mt5.TIMEFRAME_M5, 3000)
    m15_df = get_mt5_history(pair, mt5.TIMEFRAME_M15, 1000)
    
    if m1_df is None or len(m1_df) < 200 or m5_df is None or m15_df is None: 
        print(f"  Not enough MT5 data for {pair}.")
        return
        
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
    
    # Get physical DNA
    t1, t2 = DNA_THRESHOLDS.get(pair, (3.0, 2.0))
    required_mass = 15.0 if pair in ["GBPUSD", "EURUSD"] else 30.0
    
    pos = 0
    entry_price = 0
    entry_hour = -1
    
    # Dictionary to track pure Pip Gained/Lost per hour
    # Format: hour -> {"pips": 0.0, "trades": 0, "wins": 0}
    hourly_stats = defaultdict(lambda: {"pips": 0.0, "trades": 0, "wins": 0})
    
    for i in range(100, len(m1_df)):
        row = m1_df.iloc[i]
        close = row["Close"]
        vel_m5 = row["Vel_M5"]
        vel_m15 = row["Vel_M15"]
        z = row["Z_Score"]
        hour = row["time"].hour
        
        if pos != 0:
            # Macro Dead Exit
            macro_dead = (pos == 1 and vel_m15 < 0) or (pos == -1 and vel_m15 > 0)
            # Trail Stop Exit
            trail_hit = (pos == 1 and close < row["Base_M5"]) or (pos == -1 and close > row["Base_M5"])
            
            if macro_dead or trail_hit:
                pip_diff = (close - entry_price) * pos
                hourly_stats[entry_hour]["pips"] += pip_diff
                if pip_diff > 0:
                    hourly_stats[entry_hour]["wins"] += 1
                pos = 0
                continue
                
            # Partial DNA Exit (Simplification: just add to pips if partial hits)
            m5_exhausted = (pos == 1 and vel_m5 < 0) or (pos == -1 and vel_m5 > 0)
            if m5_exhausted and ((pos == 1 and close > entry_price) or (pos == -1 and close < entry_price)):
                if abs(z) > t2:
                    pip_diff = (close - entry_price) * pos
                    hourly_stats[entry_hour]["pips"] += (pip_diff * 0.5) # Simulating partial taking 50% risk off
                    
        else:
            if row["Mass"] >= required_mass:
                direction = np.sign(row["Z_Score"])
                gate_open = False
                
                if pair == "BTCUSD":
                    if direction == 1 and vel_m5 > 0: gate_open = True
                    if direction == -1 and vel_m5 < 0: gate_open = True
                else:
                    if direction == 1 and vel_m5 > 0 and vel_m15 > 0: gate_open = True
                    if direction == -1 and vel_m5 < 0 and vel_m15 < 0: gate_open = True
                
                if gate_open:
                    pos = direction
                    entry_price = close
                    entry_hour = hour
                    hourly_stats[entry_hour]["trades"] += 1

    # Print the Heatmap
    print(f"{'Hour':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Pure Pip Yield':<15}")
    print("-" * 50)
    
    total_pips = 0
    for h in range(24):
        stats = hourly_stats[h]
        trades = stats["trades"]
        if trades > 0:
            win_rate = (stats["wins"] / trades) * 100
            pips = stats["pips"]
            total_pips += pips
            print(f"{h:02d}:00  | {trades:<8} | {win_rate:>5.1f}%     | {pips:>10.2f}")
    
    print("-" * 50)
    print(f"Total MT5 Yield: {total_pips:.2f} points\n")

if __name__ == "__main__":
    if not mt5.initialize():
        print("initialize() failed, error code =", mt5.last_error())
        sys.exit()
        
    print("="*60)
    print(" THE TEMPORAL MATRIX (HARMONIC CYCLES)")
    print(" Extracting optimal physical trading windows per pair.")
    print("="*60)
    
    analyze_time_cycles("US30")
    analyze_time_cycles("BTCUSD")
    analyze_time_cycles("GBPUSD")
    analyze_time_cycles("XAUUSD")
    
    mt5.shutdown()
