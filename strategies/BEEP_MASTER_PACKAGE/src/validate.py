"""
BEEP Validation Test
=====================
Does the BEEP filter actually work?

This script runs a rigorous test:
- Multiple forex pairs
- Multiple filter threshold settings
- A proper trend-following strategy (EMA crossover, not "follow last candle")
- Multiple metrics (win rate, profit factor, max drawdown, Sharpe)
- Compares RAW vs FILTERED for each combination

The output is a clear YES or NO with data to back it up.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair, FOREX_PAIRS
from beep_filter import BeepFilter


def ema(series, period):
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def run_ema_strategy(df, use_filter=False, fast=9, slow=21):
    """
    EMA crossover strategy — a real, commonly used approach.

    Rules:
    - BUY when fast EMA crosses above slow EMA
    - SELL when fast EMA crosses below slow EMA
    - If use_filter=True, skip entries on noise candles
    - Hold until opposite signal

    Returns dict with performance metrics.
    """
    data = df.copy()
    data["ema_fast"] = ema(data["Close"], fast)
    data["ema_slow"] = ema(data["Close"], slow)

    balance = 10000.0
    position = 0  # 1=long, -1=short, 0=flat
    entry_price = 0
    trades = []
    equity_curve = [balance]

    for i in range(1, len(data)):
        row = data.iloc[i]
        prev = data.iloc[i - 1]

        # Skip noise candles if filter is on
        if use_filter and row.get("is_noise", False):
            equity_curve.append(balance)
            continue

        # EMA crossover signals
        prev_signal = 1 if prev["ema_fast"] > prev["ema_slow"] else -1
        curr_signal = 1 if row["ema_fast"] > row["ema_slow"] else -1

        # Signal change = trade
        if curr_signal != prev_signal:
            # Close existing position
            if position != 0:
                pnl_pct = (row["Close"] - entry_price) / entry_price * position
                trade_pnl = pnl_pct * balance * 0.02  # 2% risk per trade
                balance += trade_pnl
                trades.append({
                    "pnl": trade_pnl,
                    "pnl_pct": pnl_pct,
                    "direction": "LONG" if position == 1 else "SHORT",
                })

            # Open new position
            position = curr_signal
            entry_price = row["Close"]

        equity_curve.append(balance)

    # Calculate metrics
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0, "total_pnl": 0,
            "return_pct": 0, "profit_factor": 0, "max_drawdown": 0,
            "sharpe": 0, "final_balance": balance,
        }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]

    gross_profit = sum(t["pnl"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1

    # Max drawdown
    eq = pd.Series(equity_curve)
    rolling_max = eq.cummax()
    drawdown = (eq - rolling_max) / rolling_max
    max_dd = drawdown.min() * 100

    # Sharpe ratio (simplified)
    returns = pd.Series([t["pnl_pct"] for t in trades])
    sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

    return {
        "total_trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(balance - 10000, 2),
        "return_pct": round((balance - 10000) / 10000 * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "final_balance": round(balance, 2),
    }


def test_pair(pair, period="6mo", interval="1h"):
    """Test BEEP filter on a single pair with multiple threshold settings."""
    print(f"\n{'='*60}")
    print(f"  Testing: {pair}")
    print(f"{'='*60}")

    try:
        df = fetch_pair(pair, period=period, interval=interval)
    except Exception as e:
        print(f"  [ERROR] Could not fetch {pair}: {e}")
        return None

    results = []

    # Test multiple filter configurations
    configs = [
        {"z": 1.5, "wick": 0.6, "label": "Aggressive (z=1.5, wick=0.6)"},
        {"z": 2.0, "wick": 0.7, "label": "Default (z=2.0, wick=0.7)"},
        {"z": 2.5, "wick": 0.8, "label": "Conservative (z=2.5, wick=0.8)"},
        {"z": 3.0, "wick": 0.85, "label": "Minimal (z=3.0, wick=0.85)"},
    ]

    # RAW baseline (no filter)
    raw_result = run_ema_strategy(df, use_filter=False)
    raw_result["mode"] = "RAW (No Filter)"
    raw_result["pair"] = pair
    raw_result["noise_pct"] = 0
    results.append(raw_result)

    # Filtered with each config
    for cfg in configs:
        beep = BeepFilter(
            lookback=20,
            z_threshold=cfg["z"],
            wick_ratio_threshold=cfg["wick"],
        )
        filtered_df = beep.apply(df)
        summary = beep.get_summary(filtered_df)

        filtered_result = run_ema_strategy(filtered_df, use_filter=True)
        filtered_result["mode"] = f"BEEP {cfg['label']}"
        filtered_result["pair"] = pair
        filtered_result["noise_pct"] = summary["noise_percentage"]
        results.append(filtered_result)

    return results


def run_full_validation():
    """Run the complete BEEP validation across multiple pairs."""
    print("\n" + "#" * 60)
    print("  BEEP VALIDATION TEST")
    print("  Question: Does the BEEP filter improve trading outcomes?")
    print("#" * 60)

    pairs_to_test = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]
    all_results = []

    for pair in pairs_to_test:
        pair_results = test_pair(pair)
        if pair_results:
            all_results.extend(pair_results)

    if not all_results:
        print("\n  [ERROR] No results. Check internet connection.")
        return

    # Build results table
    results_df = pd.DataFrame(all_results)

    # Print results per pair
    for pair in pairs_to_test:
        pair_data = results_df[results_df["pair"] == pair]
        if pair_data.empty:
            continue

        print(f"\n{'='*80}")
        print(f"  {pair} RESULTS")
        print(f"{'='*80}")
        print(f"\n  {'Mode':<35s} {'Trades':>7s} {'WinRate':>8s} {'P&L':>10s} {'Return':>8s} {'PF':>6s} {'MaxDD':>8s} {'Sharpe':>7s}")
        print(f"  {'-'*35} {'-'*7} {'-'*8} {'-'*10} {'-'*8} {'-'*6} {'-'*8} {'-'*7}")

        for _, row in pair_data.iterrows():
            print(f"  {row['mode']:<35s} {row['total_trades']:>7d} {str(row['win_rate'])+'%':>8s} {'$'+str(row['total_pnl']):>10s} {str(row['return_pct'])+'%':>8s} {row['profit_factor']:>6.2f} {str(row['max_drawdown'])+'%':>8s} {row['sharpe']:>7.2f}")

    # FINAL VERDICT
    print(f"\n\n{'#'*60}")
    print(f"  FINAL VERDICT")
    print(f"{'#'*60}")

    # Compare: how many pair+config combos beat raw?
    raw_results = results_df[results_df["mode"] == "RAW (No Filter)"]
    filtered_results = results_df[results_df["mode"] != "RAW (No Filter)"]

    wins = 0
    losses = 0
    total_comparisons = 0

    for pair in pairs_to_test:
        raw_pnl = raw_results[raw_results["pair"] == pair]["total_pnl"].values
        if len(raw_pnl) == 0:
            continue
        raw_pnl = raw_pnl[0]

        filtered_pair = filtered_results[filtered_results["pair"] == pair]
        for _, row in filtered_pair.iterrows():
            total_comparisons += 1
            if row["total_pnl"] > raw_pnl:
                wins += 1
            else:
                losses += 1

    if total_comparisons > 0:
        win_pct = wins / total_comparisons * 100

        print(f"\n  Comparisons where BEEP beat RAW: {wins}/{total_comparisons} ({win_pct:.0f}%)")
        print(f"  Comparisons where RAW beat BEEP: {losses}/{total_comparisons} ({100-win_pct:.0f}%)")

        # Averages
        avg_raw_return = raw_results["return_pct"].mean()
        avg_filtered_return = filtered_results["return_pct"].mean()
        avg_raw_winrate = raw_results["win_rate"].mean()
        avg_filtered_winrate = filtered_results["win_rate"].mean()
        avg_raw_sharpe = raw_results["sharpe"].mean()
        avg_filtered_sharpe = filtered_results["sharpe"].mean()

        print(f"\n  Average Return  - RAW: {avg_raw_return:.2f}%  |  BEEP: {avg_filtered_return:.2f}%")
        print(f"  Average WinRate - RAW: {avg_raw_winrate:.1f}%   |  BEEP: {avg_filtered_winrate:.1f}%")
        print(f"  Average Sharpe  - RAW: {avg_raw_sharpe:.2f}    |  BEEP: {avg_filtered_sharpe:.2f}")

        if win_pct >= 60:
            print(f"\n  >>> VERDICT: YES - BEEP filter shows improvement in {win_pct:.0f}% of tests <<<")
        elif win_pct >= 40:
            print(f"\n  >>> VERDICT: INCONCLUSIVE - BEEP filter shows mixed results ({win_pct:.0f}%) <<<")
            print(f"  >>> The filter detects noise but the edge is not consistent yet. <<<")
            print(f"  >>> Next step: improve the filter algorithm, not the strategy. <<<")
        else:
            print(f"\n  >>> VERDICT: NO - BEEP filter did not improve results ({win_pct:.0f}%) <<<")
            print(f"  >>> The current filter approach needs fundamental rethinking. <<<")
    else:
        print("\n  No valid comparisons could be made.")

    print(f"\n{'#'*60}\n")

    # Save results
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "validation_results.csv")
    results_df.to_csv(output_path, index=False)
    print(f"  Full results saved to: {output_path}")

    return results_df


if __name__ == "__main__":
    run_full_validation()
