"""
Phase 2: The Temporal Layering Engine (Cause & Effect)
======================================================
This script fetches 5 years of daily data for an asset.
It slices the data into 1-year blocks, extracts the Pure Quality Score 
(Variance Asymptote), and layers the 5 baselines on top of each other 
to find "Nodes of Gravity" (seasonal resonance).
"""

import sys
import os
import yfinance as yf
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from beep_filter import BeepFilter

def get_asymptotic_quality_score(df, max_passes=20):
    current_df = df.copy()
    total_candles = len(df)
    for p in range(1, max_passes + 1):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        noise_idx = filtered[filtered["is_noise"]].index
        if len(noise_idx) / total_candles < 0.01 and p > 1: break
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            clean_close.iloc[loc] = clean_close.iloc[max(0, loc - 1)]
        current_df["Close"] = clean_close
    return current_df["Close"].rolling(window=15, min_periods=1).mean()

def run_temporal_layering(symbol="GC=F", years=5):
    print(f"\n{'='*70}")
    print(f"  TEMPORAL RESONANCE ENGINE: {symbol} ({years} Years Layered)")
    print(f"{'='*70}")
    
    # Fetch 5 years of daily data
    print(f"[1] Fetching {years} years of daily data...")
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{years}y", interval="1d")
    
    if len(df) < 200:
        print("Not enough data.")
        return
        
    df.reset_index(inplace=True)
    
    # Ensure correct column names
    if 'Date' in df.columns:
        df.rename(columns={'Date': 'time'}, inplace=True)
    elif 'Datetime' in df.columns:
        df.rename(columns={'Datetime': 'time'}, inplace=True)
        
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    
    df['Year'] = df['time'].dt.year
    df['DayOfYear'] = df['time'].dt.dayofyear
    
    unique_years = df['Year'].unique()
    if len(unique_years) > years:
        unique_years = unique_years[-years:]
        
    year_baselines = {}
    
    print("[2] Purifying each year into its True Wave (Asymptote)...")
    for y in unique_years:
        year_data = df[df['Year'] == y].copy()
        if len(year_data) < 200: continue # Skip incomplete years
            
        year_data.reset_index(drop=True, inplace=True)
        baseline = get_asymptotic_quality_score(year_data)
        
        # Normalize the baseline to percentage return from Jan 1st
        # so we can compare structural shapes regardless of actual price
        start_price = baseline.iloc[15] if len(baseline) > 15 else baseline.iloc[0]
        if pd.isna(start_price): start_price = year_data["Close"].iloc[0]
        
        normalized_baseline = ((baseline - start_price) / start_price) * 100
        
        # Map to Day of Year
        year_dict = {}
        for idx, row in year_data.iterrows():
            doy = row['DayOfYear']
            year_dict[doy] = normalized_baseline.iloc[idx]
            
        year_baselines[y] = year_dict
        print(f"    - Processed {y} (Normalized)")
        
    # Layer the years
    print("\n[3] Calculating Temporal Resonance (Nodes of Gravity)...")
    resonance_df = pd.DataFrame(index=range(1, 366))
    
    for y, b_dict in year_baselines.items():
        series = pd.Series(b_dict)
        # Forward fill missing weekend days to maintain the wave shape
        series = series.reindex(range(1, 366)).ffill()
        resonance_df[y] = series
        
    # Calculate daily agreement (correlation)
    resonance_df['Mean_Wave'] = resonance_df.mean(axis=1)
    resonance_df['Std_Wave'] = resonance_df.std(axis=1)
    
    # Calculate Velocity of the Mean Wave
    resonance_df['Mean_Velocity'] = resonance_df['Mean_Wave'].diff(5) # 5-day velocity
    
    # A Node of Gravity is where the Standard Deviation between years is LOW (they agree)
    # AND the Velocity is HIGH (they are moving violently together).
    nodes = []
    
    for i in range(15, 360):
        std_val = resonance_df['Std_Wave'].iloc[i]
        vel_val = resonance_df['Mean_Velocity'].iloc[i]
        
        if pd.isna(std_val) or pd.isna(vel_val): continue
            
        # Defining a strict resonance node: High agreement (low variance between years) 
        # and massive structural movement (high mean velocity)
        if std_val < 3.0 and abs(vel_val) > 1.0: 
            direction = "UP" if vel_val > 0 else "DOWN"
            # Map DayOfYear to month/day for readability
            date = pd.to_datetime(f"2023-{i}", format="%Y-%j")
            nodes.append({
                "Date": date.strftime("%B %d"),
                "DayOfYear": i,
                "Direction": direction,
                "Power": abs(vel_val) / max(0.1, std_val)
            })
            
    # Filter nodes to show only the strongest structural shifts (Top 5)
    nodes = sorted(nodes, key=lambda x: x["Power"], reverse=True)
    unique_nodes = []
    seen_weeks = set()
    
    for n in nodes:
        week = n["DayOfYear"] // 7
        if week not in seen_weeks:
            unique_nodes.append(n)
            seen_weeks.add(week)
        if len(unique_nodes) == 5: break
            
    print("\n======================================================================")
    print("  THE NODES OF GRAVITY (Multi-Year Seasonal Paradigm Shifts)")
    print("======================================================================")
    if not unique_nodes:
        print("No absolute Temporal Resonance found. The asset is highly chaotic.")
    else:
        for idx, n in enumerate(unique_nodes):
            print(f"  #{idx+1} | {n['Date']:<15} | Direction: {n['Direction']:<4} | Resonance Power: {n['Power']:.2f}")
    
    print("======================================================================\n")

if __name__ == "__main__":
    # Test on Gold and GBPUSD
    run_temporal_layering(symbol="GC=F", years=5)
    run_temporal_layering(symbol="GBPUSD=X", years=5)
