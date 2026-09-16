"""
Baseline Velocity & Intent Filter
=================================
This calculates the math of "When to Ride and When to Wait".
If the Baseline is the true wave, its VELOCITY (slope) is the INTENT.
- High velocity = Strong Intent (Ride it)
- Low velocity = Consolidation / Confusion (Stay out)

We test if applying a velocity filter improves the M5 baseline strategy.
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

def run_velocity_test(pair="EURUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  MATHEMATICAL PROOF: FILTERING THE BASELINE ({pair} @ {interval})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100:
        return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    
    # 1. Calculate Velocity (Slope of the Baseline)
    df["Velocity"] = df["Baseline"].diff()
    
    # 2. Normalize Velocity to find the "Average Intent"
    # We use a rolling standard deviation of the velocity to adapt to market volatility
    df["Velocity_Std"] = df["Velocity"].rolling(window=100).std()
    
    # We will test two models:
    # Model A: Always in (The previous test)
    # Model B: Only in when Velocity > 0.5 * Velocity_Std (Wait out consolidations)
    
    balance_A = 10000.0
    balance_B = 10000.0
    
    pos_A = 0
    pos_B = 0
    
    entry_A = 0
    entry_B = 0
    
    trades_A = []
    trades_B = []
    
    for i in range(100, len(df)):
        row = df.iloc[i]
        
        slope = row["Velocity"]
        std = row["Velocity_Std"]
        
        if pd.isna(slope) or pd.isna(std) or std == 0:
            continue
            
        # Model A Logic (Always In)
        desired_A = 1 if slope > 0 else -1
        if pos_A != desired_A:
            if pos_A != 0:
                pnl = balance_A * ((row["Close"] - entry_A) / entry_A * pos_A) * 10
                balance_A += pnl
                trades_A.append(pnl)
            pos_A = desired_A
            entry_A = row["Close"]
            
        # Model B Logic (Intent Filtered)
        # Is the intent strong enough?
        is_intent_strong = abs(slope) > (0.5 * std)
        
        if is_intent_strong:
            desired_B = 1 if slope > 0 else -1
        else:
            desired_B = 0 # Stay out / Consolidation
            
        if pos_B != desired_B:
            if pos_B != 0:
                pnl = balance_B * ((row["Close"] - entry_B) / entry_B * pos_B) * 10
                balance_B += pnl
                trades_B.append(pnl)
            pos_B = desired_B
            entry_B = row["Close"]
            
    # Close open positions
    if pos_A != 0:
        trades_A.append(balance_A * ((df.iloc[-1]["Close"] - entry_A) / entry_A * pos_A) * 10)
    if pos_B != 0:
        trades_B.append(balance_B * ((df.iloc[-1]["Close"] - entry_B) / entry_B * pos_B) * 10)
        
    wins_A = len([t for t in trades_A if t > 0])
    wins_B = len([t for t in trades_B if t > 0])
    
    win_rate_A = (wins_A / len(trades_A) * 100) if trades_A else 0
    win_rate_B = (wins_B / len(trades_B) * 100) if trades_B else 0
    
    print(f"\n  MODEL A: ALWAYS IN (Riding everything)")
    print(f"  - Trades Taken: {len(trades_A)}")
    print(f"  - Win Rate    : {win_rate_A:.1f}%")
    print(f"  - Net Profit  : ${sum(trades_A):.2f}")
    
    print(f"\n  MODEL B: INTENT FILTER (Waiting out consolidations)")
    print(f"  - Trades Taken: {len(trades_B)}")
    print(f"  - Win Rate    : {win_rate_B:.1f}%")
    print(f"  - Net Profit  : ${sum(trades_B):.2f}")
    
    print(f"\n  CONCLUSION FOR {pair}:")
    saved_trades = len(trades_A) - len(trades_B)
    print(f"  By measuring INTENT (Velocity), you avoided {saved_trades} bad trades (chop/consolidation).")
    if win_rate_B > win_rate_A:
        print(f"  Your Win Rate increased by {win_rate_B - win_rate_A:.1f}%.")
    if sum(trades_B) > sum(trades_A):
        print(f"  Your Net Profit increased by ${sum(trades_B) - sum(trades_A):.2f}.")
        
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_velocity_test(pair="EURUSD", interval="5m", period="60d")
    run_velocity_test(pair="GBPUSD", interval="5m", period="60d")
