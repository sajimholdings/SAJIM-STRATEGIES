"""
The Physics of the Breakout (Post-11 Mass)
==========================================
Hypothesis:
1. When Mass hits 11, the Baseline permanently shifts (The Rule of 11).
2. What happens immediately after 11? How far does it travel?
3. Is it profitable to join the spike at exactly 11, or does it exhaust and snap back?
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

def run_post_11_test(pair="EURUSD", interval="1h", period="6mo"):
    print(f"\n{'='*70}")
    print(f"  PHYSICS OF THE BREAKOUT: WHAT HAPPENS AFTER 11? ({pair})")
    print(f"{'='*70}")
    
    df = fetch_pair(pair, period=period, interval=interval)
    if len(df) < 100: return
        
    df["Baseline"] = get_causal_beep_baseline(df, passes=3)
    
    df["Deviation"] = df["Close"] - df["Baseline"]
    rolling_std = df["Deviation"].rolling(window=100).std()
    df["Z_Score"] = df["Deviation"] / rolling_std
    
    # We are looking for the exact moment Mass hits >= 11
    current_spike = {"active": False, "mass": 0, "direction": 0, "start_price": 0}
    
    breakouts = []
    
    for i in range(100, len(df)-50): # Stop 50 candles early to observe the aftermath
        row = df.iloc[i]
        z = row["Z_Score"]
        
        if pd.isna(z): continue
            
        is_spike = abs(z) > 2.0
        
        if is_spike:
            if not current_spike["active"]:
                current_spike = {"active": True, "mass": abs(z), "direction": np.sign(z), "start_price": row["Close"]}
            else:
                if np.sign(z) == current_spike["direction"]:
                    current_spike["mass"] += abs(z)
                    
                    # EXACT MOMENT OF MASS = 11
                    if current_spike["mass"] >= 11.0 and current_spike["mass"] - abs(z) < 11.0:
                        # We hit 11! Record the event and track the next 20 candles
                        entry_price = row["Close"]
                        direction = current_spike["direction"]
                        
                        # Look at the next 20 candles to see how far it travels
                        future_window = df.iloc[i+1 : i+21]
                        
                        if direction == 1:
                            max_travel = future_window["High"].max() - entry_price
                            max_drawdown = entry_price - future_window["Low"].min()
                            final_price = future_window.iloc[-1]["Close"]
                            net_move = final_price - entry_price
                        else:
                            max_travel = entry_price - future_window["Low"].min()
                            max_drawdown = future_window["High"].max() - entry_price
                            final_price = future_window.iloc[-1]["Close"]
                            net_move = entry_price - final_price
                            
                        breakouts.append({
                            "entry": entry_price,
                            "direction": direction,
                            "max_travel_pips": max_travel * 10000,
                            "max_drawdown_pips": max_drawdown * 10000,
                            "net_move_pips": net_move * 10000
                        })
                else:
                    current_spike = {"active": True, "mass": abs(z), "direction": np.sign(z), "start_price": row["Close"]}
        else:
            current_spike["active"] = False
            current_spike["mass"] = 0

    if not breakouts:
        print("  No Mass=11 events found.")
        return
        
    avg_travel = np.mean([b["max_travel_pips"] for b in breakouts])
    avg_drawdown = np.mean([b["max_drawdown_pips"] for b in breakouts])
    profitable_holds = sum(1 for b in breakouts if b["net_move_pips"] > 0)
    win_rate = (profitable_holds / len(breakouts)) * 100
    
    print(f"  Total Paradigm Shifts (Mass >= 11) : {len(breakouts)}")
    print(f"  Avg Max Expansion (Reward)         : {avg_travel:.1f} pips")
    print(f"  Avg Max Retracement (Risk)         : {avg_drawdown:.1f} pips")
    print(f"  Win Rate (Holding 20 candles)      : {win_rate:.1f}%")
    print(f"\n  THE PHYSICS OF JOINING AT 11:")
    if win_rate < 50:
        print("  DO NOT JOIN AT 11. By the time it hits 11, the energy is exhausted.")
        print("  It snaps back to form the new baseline. Joining here is buying the top.")
    else:
        print("  JOIN AT 11. Hitting 11 unlocks a vacuum. It accelerates forward.")
        print("  The baseline completely breaks and free-falls/rockets.")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    run_post_11_test(pair="EURUSD", interval="1h", period="6mo")
    run_post_11_test(pair="GBPUSD", interval="1h", period="6mo")
