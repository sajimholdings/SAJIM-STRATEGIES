"""
The Vacuum Hunter (BEEP Paradigm Shift Engine)
==============================================
This strategy waits patiently for the market to accumulate 11 BEEP Mass Units.
When it hits 11, gravity breaks. The bot enters the void and rides the new baseline.
It ignores all noise, all fakeouts, and all consolidations.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_causal_beep_baseline(df, passes=3):
    current_df = df.copy()
    for _ in range(passes):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        
        clean_close = current_df["Close"].copy()
        for idx in filtered[filtered["is_noise"]].index:
            loc = current_df.index.get_loc(idx)
            prev_val = clean_close.iloc[max(0, loc - 1)]
            clean_close.iloc[loc] = prev_val
            
        current_df["Close"] = clean_close
        
    current_df["Baseline"] = current_df["Close"].rolling(window=15, min_periods=1).mean()
    return current_df["Baseline"]

def run_vacuum_hunter(pair="EURUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  THE VACUUM HUNTER: PARADIGM SHIFT TEST ({pair} @ {interval})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    df["Velocity"] = df["Baseline"].diff()
    
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    mass_series = np.zeros(len(df))
    direction_series = np.zeros(len(df))
    
    current_mass = 0
    current_dir = 0
    
    # Calculate Mass
    for i in range(100, len(df)):
        z = df.iloc[i]["Z_Score"]
        if pd.isna(z): continue
            
        is_spike = abs(z) > 1.5 
        
        if is_spike:
            if current_dir == 0:
                current_dir = np.sign(z)
                current_mass = abs(z)
            elif np.sign(z) == current_dir:
                current_mass += abs(z)
            else:
                current_dir = np.sign(z)
                current_mass = abs(z)
        else:
            current_mass = 0
            current_dir = 0
            
        mass_series[i] = current_mass
        direction_series[i] = current_dir
        
    df["Mass"] = mass_series
    df["Direction"] = direction_series
    
    bal = 10000.0
    pos = 0
    entry_price = 0
    trades = []
    
    # We will track "Time Spent Waiting" vs "Time Spent Trading"
    # to show how it solves the overtrading issue.
    candles_waiting = 0
    candles_in_trade = 0
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        close = row["Close"]
        mass = row["Mass"]
        prev_mass = prev["Mass"]
        direction = row["Direction"]
        vel = row["Velocity"]
        
        if pos != 0:
            candles_in_trade += 1
            
            # Exit Logic: 
            # 1. The baseline velocity starts reversing (exhaustion of the new trend)
            # 2. Hard stop-loss to protect against catastrophic failure
            
            is_exhausted = (pos == 1 and vel < 0) or (pos == -1 and vel > 0)
            sl_hit = (pos == 1 and close < entry_price * 0.997) or (pos == -1 and close > entry_price * 1.003)
            
            if is_exhausted or sl_hit:
                pnl = bal * ((close - entry_price) / entry_price * pos) * 20 # 20x leverage
                bal += pnl
                trades.append(pnl)
                pos = 0
        else:
            candles_waiting += 1
            # Entry Logic: The exact moment Mass crosses 11
            if mass >= 11.0 and prev_mass < 11.0:
                pos = 1 if direction == 1 else -1
                entry_price = close

    if pos != 0: trades.append(bal * ((df.iloc[-1]["Close"] - entry_price) / entry_price * pos) * 20)
        
    wins = len([t for t in trades if t > 0])
    win_rate = (wins / len(trades) * 100) if trades else 0
    
    print(f"  Time Spent Waiting      : {candles_waiting} candles ({(candles_waiting/len(df))*100:.1f}%)")
    print(f"  Time Spent In Trades    : {candles_in_trade} candles ({(candles_in_trade/len(df))*100:.1f}%)")
    print(f"  Total Paradigm Shifts   : {len(trades)}")
    
    if trades:
        print(f"  Win Rate                : {win_rate:.1f}%")
        print(f"  Starting Balance        : $10,000.00")
        print(f"  Final Balance           : ${bal:,.2f}")
        print(f"  Net Profit              : ${bal - 10000:,.2f} ({((bal - 10000)/10000)*100:.1f}%)")
        print(f"  Average Win             : ${np.mean([t for t in trades if t > 0]):,.2f}")
        losses = [t for t in trades if t <= 0]
        if losses:
            print(f"  Average Loss            : ${np.mean(losses):,.2f}")
    
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_vacuum_hunter(pair="EURUSD", interval="5m", period="60d")
    run_vacuum_hunter(pair="GBPUSD", interval="5m", period="60d")
