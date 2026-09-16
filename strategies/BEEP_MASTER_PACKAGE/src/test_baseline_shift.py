"""
The Mathematics of Bending Reality
==================================
Calculating the Mass required to permanently alter a Baseline.

Hypothesis:
1. A spike (BEEP score) only alters the baseline if it has enough MASS.
2. Mass = Magnitude (How high the Z-score is) × Duration (How long the spike stays elevated).
3. If Mass > Baseline Gravity, the baseline shifts.
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
        
    current_df["Baseline"] = current_df["Close"].rolling(window=20, min_periods=1).mean()
    return current_df["Baseline"]

def calculate_shift_threshold(pair="EURUSD", interval="1h", period="6mo"):
    print(f"\n{'='*70}")
    print(f"  MATHEMATICS OF BENDING THE BASELINE ({pair})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100:
        return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    
    # Calculate Z-Score (Magnitude of the Spike)
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    # Calculate Baseline Shift (Did the baseline permanently move?)
    # We define a permanent shift if the baseline 20 candles later is significantly different
    df["Baseline_Future"] = df["Baseline"].shift(-20)
    df["Baseline_Shift"] = df["Baseline_Future"] - df["Baseline"]
    
    # Analyze all spikes (Z-score > 2.0 or < -2.0)
    spikes = []
    current_spike = {"active": False, "start": 0, "max_z": 0, "duration": 0, "direction": 0}
    
    for i in range(100, len(df)-20):
        row = df.iloc[i]
        z = row["Z_Score"]
        
        if pd.isna(z): continue
            
        is_spike = abs(z) > 2.0
        
        if is_spike:
            if not current_spike["active"]:
                # Start new spike
                current_spike = {"active": True, "start": i, "max_z": abs(z), "duration": 1, "direction": np.sign(z)}
            else:
                # Continue spike
                if np.sign(z) == current_spike["direction"]:
                    current_spike["duration"] += 1
                    current_spike["max_z"] = max(current_spike["max_z"], abs(z))
                else:
                    # Direction flipped, end old spike
                    spikes.append(current_spike)
                    current_spike = {"active": True, "start": i, "max_z": abs(z), "duration": 1, "direction": np.sign(z)}
        else:
            if current_spike["active"]:
                # End spike
                spikes.append(current_spike)
                current_spike = {"active": False, "start": 0, "max_z": 0, "duration": 0, "direction": 0}

    # Evaluate the spikes
    reverted_spikes = 0
    shifted_spikes = 0
    
    shift_thresholds = [] # The mass required to cause a shift
    
    for spike in spikes:
        idx = spike["start"]
        shift = df.iloc[idx]["Baseline_Shift"]
        direction = spike["direction"]
        
        # Did the baseline actually move in the direction of the spike?
        # A significant shift is > 1 standard deviation of normal baseline movement
        baseline_std = df["Baseline"].diff().std()
        
        is_shift = (direction == 1 and shift > baseline_std * 2) or (direction == -1 and shift < -baseline_std * 2)
        
        mass = spike["max_z"] * spike["duration"]
        
        if is_shift:
            shifted_spikes += 1
            shift_thresholds.append(mass)
        else:
            reverted_spikes += 1

    total_spikes = reverted_spikes + shifted_spikes
    avg_shift_mass = np.mean(shift_thresholds) if shift_thresholds else 0
    
    print(f"  Total Anomalies (Spikes) Found : {total_spikes}")
    print(f"  Spikes that REVERTED (Noise)   : {reverted_spikes} ({(reverted_spikes/total_spikes)*100:.1f}%)")
    print(f"  Spikes that SHIFTED Baseline   : {shifted_spikes} ({(shifted_spikes/total_spikes)*100:.1f}%)")
    print(f"\n  THE MATHEMATICS OF CHANGE:")
    print(f"  To permanently bend the baseline, a spike required an average MASS of:")
    print(f"  --> {avg_shift_mass:.2f} BEEP Mass Units (Z-Score × Duration)")
    print(f"\n  Breakdown of the formula:")
    print(f"  Mass = Magnitude (Intensity) × Time (Consistency)")
    print(f"  A massive Z-score for 1 candle = Noise (Reverts)")
    print(f"  A moderate Z-score for 10 candles = Mass (Bends Reality)")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    calculate_shift_threshold(pair="EURUSD", interval="1h", period="6mo")
