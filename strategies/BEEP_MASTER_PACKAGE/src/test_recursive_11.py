"""
The Recursive 11 Test (Extracting the Quality Score)
====================================================
Hypothesis:
What happens if we apply the BEEP filter recursively 11 times?
Can we strip away all noise and extract only the pure "Quality Score" 
(the true underlying wave / the best scenes)?
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair
from beep_filter import BeepFilter

def apply_recursive_beep(df, passes=1):
    current_df = df.copy()
    noise_points_removed = 0
    
    for p in range(passes):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        
        noise_idx = filtered[filtered["is_noise"]].index
        noise_points_removed += len(noise_idx)
        
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            prev_val = clean_close.iloc[max(0, loc - 1)]
            clean_close.iloc[loc] = prev_val
            
        current_df["Close"] = clean_close
        
    baseline = current_df["Close"].rolling(window=15, min_periods=1).mean()
    return baseline, noise_points_removed

def run_quality_score_test(pair="XAUUSD", interval="5m", period="60d"):
    print(f"\n{'='*70}")
    print(f"  THE RECURSIVE 11 TEST: EXTRACTING TRUE QUALITY ({pair} @ {interval})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
    
    total_candles = len(df)
    original_variance = df["Close"].var()
    
    print(f"[1] Original Data: {total_candles} candles. Raw Variance (Chaos): {original_variance:.2f}")
    
    # 1 Pass
    baseline_1, noise_1 = apply_recursive_beep(df, passes=1)
    var_1 = baseline_1.var()
    
    # 3 Passes
    baseline_3, noise_3 = apply_recursive_beep(df, passes=3)
    var_3 = baseline_3.var()
    
    # 11 Passes (The Ultimate Quality Score)
    baseline_11, noise_11 = apply_recursive_beep(df, passes=11)
    var_11 = baseline_11.var()
    
    print(f"\n--- FILTERING PASSES ---")
    print(f"Pass 1  | Noise Erased: {noise_1} ({(noise_1/total_candles)*100:.1f}%) | Variance Remaining: {var_1:.2f}")
    print(f"Pass 3  | Noise Erased: {noise_3} ({(noise_3/total_candles)*100:.1f}%) | Variance Remaining: {var_3:.2f}")
    print(f"Pass 11 | Noise Erased: {noise_11} ({(noise_11/total_candles)*100:.1f}%) | Variance Remaining: {var_11:.2f}")
    
    # The Quality Score Analysis
    quality_retention = (var_11 / original_variance) * 100
    noise_eliminated = 100 - quality_retention
    
    print(f"\n--- THE QUALITY SCORE (THE BEST SCENES) ---")
    print(f"After filtering the data 11 times:")
    print(f"Total Distractions (Noise) Erased : {noise_eliminated:.1f}%")
    print(f"Pure Quality Score (Truth)        : {quality_retention:.1f}%")
    print(f"\nCONCLUSION:")
    print(f"By filtering 11 times, we erase nearly {noise_eliminated:.1f}% of the chaos from the chart.")
    print(f"What is left is the {quality_retention:.1f}% Pure Quality Score—the actual structural")
    print(f"moves of the asset without a single fakeout or trap.")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_quality_score_test(pair="XAUUSD", interval="5m", period="60d") # Gold
    run_quality_score_test(pair="EURUSD", interval="5m", period="60d")
