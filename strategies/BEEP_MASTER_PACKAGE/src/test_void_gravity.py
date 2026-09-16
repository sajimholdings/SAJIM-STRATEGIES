"""
The Void Gravity Test (Baseline Attraction)
===========================================
Hypothesis:
1. "The Void" is an emptiness in the market (e.g., Fair Value Gaps / Liquidity Imbalances).
2. The Baseline (Truth) is gravitationally pulled toward these voids to fill them.
3. By mapping the Voids, we can see where the Baseline is going before it bends.
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

def run_void_test(pair="EURUSD", interval="1h", period="6mo"):
    print(f"\n{'='*70}")
    print(f"  TESTING THE VOID: DOES EMPTINESS PULL THE BASELINE? ({pair})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100:
        return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    
    # 1. Identify "The Voids" (Fair Value Gaps / Liquidity Imbalances)
    # A Bullish Void: Price shoots up, leaving a gap between High(i-1) and Low(i+1)
    # A Bearish Void: Price shoots down, leaving a gap between Low(i-1) and High(i+1)
    
    df["Void_Upper"] = np.nan
    df["Void_Lower"] = np.nan
    df["Void_Type"] = 0 # 1 for Bullish Void (below price), -1 for Bearish Void (above price)
    
    for i in range(1, len(df)-1):
        # Bullish FVG (Void left below)
        if df.iloc[i+1]["Low"] > df.iloc[i-1]["High"]:
            df.at[df.index[i], "Void_Lower"] = df.iloc[i-1]["High"]
            df.at[df.index[i], "Void_Upper"] = df.iloc[i+1]["Low"]
            df.at[df.index[i], "Void_Type"] = 1
            
        # Bearish FVG (Void left above)
        elif df.iloc[i+1]["High"] < df.iloc[i-1]["Low"]:
            df.at[df.index[i], "Void_Lower"] = df.iloc[i+1]["High"]
            df.at[df.index[i], "Void_Upper"] = df.iloc[i-1]["Low"]
            df.at[df.index[i], "Void_Type"] = -1

    # 2. Test if the Baseline eventually bends to fill these Voids
    voids_created = 0
    voids_filled = 0
    time_to_fill = []
    
    # Keep track of active voids
    active_voids = []
    
    for i in range(2, len(df)):
        row = df.iloc[i]
        
        # Did we create a void previously?
        prev_row = df.iloc[i-1]
        if prev_row["Void_Type"] != 0:
            active_voids.append({
                "index": i-1,
                "upper": prev_row["Void_Upper"],
                "lower": prev_row["Void_Lower"],
                "type": prev_row["Void_Type"]
            })
            voids_created += 1
            
        # Check if the current baseline has entered any active voids
        still_active = []
        for void in active_voids:
            baseline_val = row["Baseline"]
            # A void is "filled" if the baseline reaches it
            if void["lower"] <= baseline_val <= void["upper"]:
                voids_filled += 1
                time_to_fill.append(i - void["index"])
            elif (void["type"] == 1 and baseline_val < void["lower"]) or (void["type"] == -1 and baseline_val > void["upper"]):
                # Baseline went completely past it, filling it heavily
                voids_filled += 1
                time_to_fill.append(i - void["index"])
            else:
                still_active.append(void)
                
        active_voids = still_active

    fill_rate = (voids_filled / voids_created * 100) if voids_created > 0 else 0
    avg_fill_time = np.mean(time_to_fill) if time_to_fill else 0
    
    print(f"  Total Voids (Emptiness) Created : {voids_created}")
    print(f"  Voids Filled by the Baseline    : {voids_filled}")
    print(f"  Gravity Fill Rate               : {fill_rate:.1f}%")
    if avg_fill_time > 0:
        print(f"  Avg Time for Baseline to Fill   : {avg_fill_time:.1f} candles")
        
    print(f"\n  CONCLUSION:")
    if fill_rate > 80:
        print(f"  YES. The Void creates Gravity.")
        print(f"  {fill_rate:.1f}% of the time, an emptiness in the market pulls the Baseline")
        print(f"  toward it until it is filled. The Baseline seeks equilibrium.")
    else:
        print(f"  Gravity is weak. Fill rate is {fill_rate:.1f}%")
        
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_void_test(pair="EURUSD", interval="1h", period="6mo")
    run_void_test(pair="GBPUSD", interval="1h", period="6mo")
    run_void_test(pair="GBPJPY", interval="1h", period="6mo")
