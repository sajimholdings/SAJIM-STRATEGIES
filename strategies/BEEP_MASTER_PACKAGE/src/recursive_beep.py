"""
Recursive BEEP Test
====================
What happens when you feed BEEP output back as BEEP input?

We run the filter N times and measure:
- How many new noise candles are found on each pass
- When does it converge (no more noise found)?
- Does the cleaned signal improve with each pass?
- What's the backtest performance at each depth level?
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from data_loader import fetch_pair, load_data
from beep_filter import BeepFilter


def recursive_beep(df, max_passes=10, z_threshold=2.0, wick_threshold=0.7):
    """
    Run BEEP filter recursively — output becomes input.

    At each pass, we recalculate the noise scores on the cleaned data.
    We track how the noise distribution changes with each pass.

    Returns:
        list of dicts, one per pass, with metrics
    """
    results = []
    current_df = df.copy()
    cumulative_noise_mask = pd.Series(False, index=df.index)

    for pass_num in range(1, max_passes + 1):
        beep = BeepFilter(
            lookback=20,
            z_threshold=z_threshold,
            wick_ratio_threshold=wick_threshold,
        )

        # Apply filter to current data
        filtered = beep.apply(current_df)
        summary = beep.get_summary(filtered)

        # How many NEW noise candles were found this pass?
        new_noise = filtered["is_noise"] & ~cumulative_noise_mask
        new_noise_count = new_noise.sum()

        # Update cumulative noise mask
        cumulative_noise_mask = cumulative_noise_mask | filtered["is_noise"]
        total_noise = cumulative_noise_mask.sum()

        # Compute the "cleaned" version for next pass
        # Replace noise candle closes with interpolated values
        clean_close = current_df["Close"].copy()
        noise_indices = filtered[filtered["is_noise"]].index
        for idx in noise_indices:
            # Interpolate: average of previous and next clean candle
            loc = current_df.index.get_loc(idx)
            prev_val = clean_close.iloc[max(0, loc - 1)]
            next_val = clean_close.iloc[min(len(clean_close) - 1, loc + 1)]
            clean_close.iloc[loc] = (prev_val + next_val) / 2

        # Create cleaned dataframe for next pass
        next_df = current_df.copy()
        next_df["Close"] = clean_close
        # Adjust Open to match previous close for consistency
        next_df["Open"] = next_df["Close"].shift(1).fillna(next_df["Open"])
        # Clamp High/Low
        next_df["High"] = next_df[["Open", "Close", "High"]].max(axis=1)
        next_df["Low"] = next_df[["Open", "Close", "Low"]].min(axis=1)

        # Measure signal quality: autocorrelation of returns (higher = more trending = cleaner)
        returns = clean_close.pct_change().dropna()
        autocorr = returns.autocorrelation(lag=1) if hasattr(returns, 'autocorrelation') else returns.corr(returns.shift(1))
        
        # Measure noise level: standard deviation of return differences (lower = smoother)
        return_diffs = returns.diff().dropna()
        roughness = return_diffs.std()

        pass_result = {
            "pass": pass_num,
            "new_noise_found": int(new_noise_count),
            "total_noise_cumulative": int(total_noise),
            "noise_pct_cumulative": round(total_noise / len(df) * 100, 1),
            "by_layer": summary["by_layer"],
            "avg_noise_score": summary["avg_noise_score"],
            "signal_autocorr": round(autocorr, 4) if not np.isnan(autocorr) else 0,
            "roughness": round(roughness, 8),
        }
        results.append(pass_result)

        # Check convergence
        if new_noise_count == 0:
            print(f"  Pass {pass_num}: CONVERGED - no new noise found")
            break

        print(f"  Pass {pass_num}: {new_noise_count} new noise candles | Total: {total_noise} ({pass_result['noise_pct_cumulative']}%) | Roughness: {roughness:.8f}")

        current_df = next_df

    return results


def run_test():
    """Run the recursive BEEP test."""
    print("\n" + "=" * 60)
    print("  RECURSIVE BEEP TEST")
    print("  What happens when BEEP output feeds back as input?")
    print("=" * 60)

    # Fetch data
    print("\n[1] Fetching data...")
    try:
        df = load_data("EURUSD", "1h")
    except FileNotFoundError:
        df = fetch_pair("EURUSD", period="6mo", interval="1h")

    print(f"  {len(df)} candles loaded\n")

    # Run recursive BEEP
    print("[2] Running recursive BEEP (max 10 passes)...\n")
    results = recursive_beep(df, max_passes=10)

    # Print summary
    print(f"\n{'='*60}")
    print(f"  RECURSIVE BEEP RESULTS")
    print(f"{'='*60}")
    print(f"\n  {'Pass':>4s} {'New Noise':>10s} {'Total Noise':>12s} {'Noise %':>8s} {'Roughness':>12s} {'Autocorr':>10s}")
    print(f"  {'-'*4} {'-'*10} {'-'*12} {'-'*8} {'-'*12} {'-'*10}")

    for r in results:
        print(f"  {r['pass']:>4d} {r['new_noise_found']:>10d} {r['total_noise_cumulative']:>12d} {str(r['noise_pct_cumulative'])+'%':>8s} {r['roughness']:>12.8f} {r['signal_autocorr']:>10.4f}")

    # Analysis
    total_passes = len(results)
    converged = results[-1]["new_noise_found"] == 0
    total_noise_found = results[-1]["total_noise_cumulative"]
    pass_1_noise = results[0]["new_noise_found"]

    print(f"\n  ANALYSIS:")
    print(f"  - Converged after {total_passes} passes: {'YES' if converged else 'NO (hit max)'}")
    print(f"  - Total noise across all passes: {total_noise_found} candles ({results[-1]['noise_pct_cumulative']}%)")
    print(f"  - Pass 1 found {pass_1_noise} noise candles")
    if total_passes > 1:
        deeper_noise = total_noise_found - pass_1_noise
        print(f"  - Deeper passes found {deeper_noise} ADDITIONAL noise candles")
        print(f"  - That's {round(deeper_noise/pass_1_noise*100, 1) if pass_1_noise > 0 else 0}% more noise hidden under the first layer")
    
    roughness_improvement = (results[0]["roughness"] - results[-1]["roughness"]) / results[0]["roughness"] * 100 if results[0]["roughness"] > 0 else 0
    print(f"  - Signal roughness reduced by {roughness_improvement:.1f}%")
    print(f"\n{'='*60}\n")

    # Now test different BEEP "races" (threshold configurations)
    print("\n" + "=" * 60)
    print("  BEEP DIMENSIONS / VARIETIES")
    print("  Testing different 'species' of noise filters")
    print("=" * 60)

    dimensions = [
        {
            "name": "Price BEEP",
            "desc": "Filters noise in raw price movement",
            "z": 2.0, "wick": 0.7,
            "focus": "statistical + structural",
        },
        {
            "name": "Momentum BEEP",
            "desc": "Filters noise in trend strength (tight z, loose wick)",
            "z": 1.5, "wick": 0.9,
            "focus": "statistical (dominant)",
        },
        {
            "name": "Structure BEEP",
            "desc": "Filters noise in market structure (loose z, tight wick)",
            "z": 3.0, "wick": 0.5,
            "focus": "structural (dominant)",
        },
        {
            "name": "Scalper BEEP",
            "desc": "Aggressive noise removal for short-term trades",
            "z": 1.2, "wick": 0.5,
            "focus": "everything - maximum filtering",
        },
        {
            "name": "Swing BEEP",
            "desc": "Conservative - only removes extreme noise for swing trades",
            "z": 3.0, "wick": 0.85,
            "focus": "minimal - only obvious outliers",
        },
    ]

    print(f"\n  {'Dimension':<18s} {'Noise Found':>11s} {'Noise %':>8s} {'Stat':>5s} {'Struct':>6s} {'Temp':>5s} {'Focus'}")
    print(f"  {'-'*18} {'-'*11} {'-'*8} {'-'*5} {'-'*6} {'-'*5} {'-'*30}")

    for dim in dimensions:
        beep = BeepFilter(
            lookback=20,
            z_threshold=dim["z"],
            wick_ratio_threshold=dim["wick"],
        )
        filtered = beep.apply(df)
        summary = beep.get_summary(filtered)
        bl = summary["by_layer"]

        print(f"  {dim['name']:<18s} {summary['noise_candles']:>11d} {str(summary['noise_percentage'])+'%':>8s} {bl['statistical']:>5d} {bl['structural']:>6d} {bl['temporal']:>5d} {dim['focus']}")

    print(f"\n  Each 'dimension' of BEEP sees different types of noise.")
    print(f"  Combining them creates a multi-dimensional filter.")
    print(f"  Stacking them recursively adds DEPTH.")
    print(f"\n  Full BEEP space = Dimensions x Depth")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    run_test()
