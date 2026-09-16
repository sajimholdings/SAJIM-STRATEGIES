"""
BEEP — Main Entry Point
========================
Runs the full Phase 0 pipeline:

1. Fetch forex data
2. Apply the BEEP filter
3. Visualize raw vs cleaned price action
4. Backtest to measure the filter's edge

Usage:
    python src/main.py
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from data_loader import fetch_and_save, load_data, FOREX_PAIRS
from beep_filter import BeepFilter
from visualizer import plot_beep_analysis, plot_noise_distribution
from backtest import SimpleBacktest, print_comparison


def run_pipeline(
    pair: str = "EURUSD",
    period: str = "6mo",
    interval: str = "1h",
    force_fetch: bool = False,
):
    """
    Run the full BEEP analysis pipeline for a forex pair.

    Args:
        pair: Forex pair to analyze (e.g., "EURUSD", "GBPUSD", "XAUUSD")
        period: How far back to look ("3mo", "6mo", "1y")
        interval: Candle size ("1h", "4h", "1d")
        force_fetch: If True, re-fetches data even if cached locally
    """
    print("\n" + "=" * 60)
    print(f"  [BEEP] Behavioral Encoding & Existence Prediction")
    print(f"  Phase 0: Forex Filter Prototype")
    print(f"  Analyzing: {pair} | Period: {period} | Interval: {interval}")
    print("=" * 60)

    # ── Step 1: Get Data ──
    print("\n[Step 1] Fetching data...")
    try:
        if not force_fetch:
            df = load_data(pair, interval)
        else:
            raise FileNotFoundError("Force fetch")
    except FileNotFoundError:
        df = fetch_and_save(pair, period, interval)

    print(f"  Data range: {df.index.min()} → {df.index.max()}")
    print(f"  Total candles: {len(df)}")

    # ── Step 2: Apply BEEP Filter ──
    print("\n[Step 2] Applying BEEP filter...")
    beep = BeepFilter(
        lookback=20,
        z_threshold=2.0,
        volume_z_threshold=2.0,
        fakeout_window=3,
        wick_ratio_threshold=0.7,
    )
    filtered_df = beep.apply(df)
    summary = beep.get_summary(filtered_df)

    print(f"  Noise detected:  {summary['noise_candles']} candles ({summary['noise_percentage']}%)")
    print(f"  Clean candles:   {summary['clean_candles']}")
    print(f"  Avg noise score: {summary['avg_noise_score']}")
    print(f"  By layer:")
    for layer, count in summary["by_layer"].items():
        pct = round(count / summary["total_candles"] * 100, 1)
        print(f"    {layer:15s}: {count:5d} ({pct}%)")

    # ── Step 3: Visualize ──
    print("\n[Step 3] Generating charts...")
    plot_beep_analysis(filtered_df, pair=pair, show_last_n=200, save=True)
    plot_noise_distribution(filtered_df, pair=pair, save=True)

    # ── Step 4: Backtest ──
    print("\n[Step 4] Running backtest comparison...")
    bt = SimpleBacktest(initial_balance=10000, risk_per_trade=0.01)
    results = bt.compare(filtered_df)
    print_comparison(results)

    # ── Done ──
    print(f"\n{'=' * 60}")
    print(f"  [OK] BEEP analysis complete for {pair}")
    print(f"  Charts saved to: output/")
    print(f"{'=' * 60}\n")

    return filtered_df, results


if __name__ == "__main__":
    # Run for the main pairs
    pairs_to_analyze = ["EURUSD"]  # Start with one, expand later

    for pair in pairs_to_analyze:
        run_pipeline(
            pair=pair,
            period="6mo",
            interval="1h",
            force_fetch=True,
        )
