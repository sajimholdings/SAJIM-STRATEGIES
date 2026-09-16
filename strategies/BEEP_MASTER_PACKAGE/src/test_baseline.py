"""
BEEP Continuous Baseline Follower
=================================
Hypothesis:
1. Don't trade the reversions. BE THE BASELINE.
2. Always be in the market. If baseline goes up, you are long. If it goes down, you are short.
3. Spikes are just opportunities to take profit ("exit the market"), but you are protected by the trend.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair
from beep_filter import BeepFilter

def get_causal_beep_baseline(df, passes=3):
    """Generate the 'True Wave' baseline causally (no future data)."""
    current_df = df.copy()
    for _ in range(passes):
        beep = BeepFilter(lookback=20, z_threshold=2.0, wick_ratio_threshold=0.7)
        filtered = beep.apply(current_df)
        
        # Clean the noise (using only past data for interpolation to prevent lookahead)
        clean_close = current_df["Close"].copy()
        for idx in filtered[filtered["is_noise"]].index:
            loc = current_df.index.get_loc(idx)
            # Just replace noise with the previous valid close to be 100% causal
            prev_val = clean_close.iloc[max(0, loc - 1)]
            clean_close.iloc[loc] = prev_val
            
        current_df["Close"] = clean_close
        
    # Apply a moving average to smooth the final wave
    current_df["Baseline"] = current_df["Close"].rolling(window=15, min_periods=1).mean()
    return current_df["Baseline"]

def run_baseline_test(pair="EURUSD", interval="1h", period="6mo"):
    print(f"\n{'='*60}")
    print(f"  TESTING 'BEING THE BASELINE' ({pair} @ {interval})")
    print(f"{'='*60}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100:
        return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    
    # Calculate Baseline Slope (Derivative)
    # If slope is positive, Baseline is moving UP.
    df["Baseline_Slope"] = df["Baseline"].diff()
    
    balance = 10000.0
    position = 0 # 1 for Long, -1 for Short
    entry_price = 0
    trades = []
    equity_curve = [balance]
    
    # We are ALWAYS in the market.
    # We only change direction when the baseline slope changes direction.
    
    for i in range(20, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        current_slope = row["Baseline_Slope"]
        
        # Determine desired position based on baseline
        desired_position = 1 if current_slope > 0 else -1
        
        # If we need to flip direction
        if position != desired_position:
            # Close old position if we had one
            if position != 0:
                pnl_pct = (row["Close"] - entry_price) / entry_price * position
                # Using 10x leverage for standard swing trading
                trade_pnl = balance * pnl_pct * 10
                balance += trade_pnl
                trades.append(trade_pnl)
                
            # Open new position
            position = desired_position
            entry_price = row["Close"]
            
        equity_curve.append(balance)
        
    # Results
    wins = [t for t in trades if t > 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0
    total_profit = balance - 10000.0
    
    print(f"  Total Flips (Trades): {len(trades)}")
    print(f"  Win Rate            : {win_rate:.1f}%")
    print(f"  Starting Balance    : $10000.00")
    print(f"  Final Balance       : ${balance:.2f}")
    print(f"  Net Profit          : ${total_profit:.2f} ({total_profit/10000*100:.1f}%)")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    run_baseline_test(pair="EURUSD", interval="1h", period="6mo")
    run_baseline_test(pair="GBPUSD", interval="1h", period="6mo")
    run_baseline_test(pair="GBPJPY", interval="1h", period="6mo")
