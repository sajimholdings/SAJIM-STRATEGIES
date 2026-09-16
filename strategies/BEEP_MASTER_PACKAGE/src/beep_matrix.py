"""
BEEP Matrix — 2D Grid Analysis
================================
Y-axis: Filter dimensions (layers of reality)
X-axis: Recursive passes at each layer

At each (layer, pass) intersection, we measure accuracy/roughness.
At full convergence across all layers = the single true wave.

Then we extract the fundamental frequency of the pair.
If you can replicate that frequency — you ARE the market.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from data_loader import fetch_pair, load_data
from beep_filter import BeepFilter

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")


def apply_beep_pass(df, z_threshold=2.0, wick_threshold=0.7):
    """Apply one BEEP pass and return filtered df + noise count."""
    beep = BeepFilter(lookback=20, z_threshold=z_threshold, wick_ratio_threshold=wick_threshold)
    filtered = beep.apply(df)
    noise_count = filtered["is_noise"].sum()
    
    # Create cleaned version for next pass
    clean_df = df.copy()
    clean_close = clean_df["Close"].copy()
    noise_mask = filtered["is_noise"]
    
    for idx in filtered[noise_mask].index:
        loc = clean_df.index.get_loc(idx)
        prev_val = clean_close.iloc[max(0, loc - 1)]
        next_val = clean_close.iloc[min(len(clean_close) - 1, loc + 1)]
        clean_close.iloc[loc] = (prev_val + next_val) / 2
    
    clean_df["Close"] = clean_close
    clean_df["Open"] = clean_df["Close"].shift(1).fillna(clean_df["Open"])
    clean_df["High"] = clean_df[["Open", "Close", "High"]].max(axis=1)
    clean_df["Low"] = clean_df[["Open", "Close", "Low"]].min(axis=1)
    
    return clean_df, filtered, noise_count


def measure_signal_quality(close_series):
    """Measure how 'clean' the signal is."""
    returns = close_series.pct_change().dropna()
    if len(returns) < 10:
        return {"roughness": 0, "autocorr": 0, "trend_strength": 0}
    
    # Roughness: std of return differences (lower = smoother)
    roughness = returns.diff().dropna().std()
    
    # Autocorrelation: how predictable is the next move (higher = more trending)
    autocorr = returns.corr(returns.shift(1))
    if np.isnan(autocorr):
        autocorr = 0
        
    # Trend strength: ratio of net movement to total movement
    net_move = abs(close_series.iloc[-1] - close_series.iloc[0])
    total_move = returns.abs().sum() * close_series.mean()
    trend_strength = net_move / total_move if total_move > 0 else 0
    
    return {
        "roughness": roughness,
        "autocorr": autocorr,
        "trend_strength": trend_strength,
    }


def extract_fundamental_frequency(close_series, top_n=5):
    """
    Use FFT to extract the fundamental frequencies of the price signal.
    This is the 'true wave' — the frequencies that survive all filtering.
    """
    # Detrend the data
    prices = close_series.values
    x = np.arange(len(prices))
    
    # Remove linear trend
    coeffs = np.polyfit(x, prices, 1)
    detrended = prices - np.polyval(coeffs, x)
    
    # Apply FFT
    fft_vals = np.fft.fft(detrended)
    freqs = np.fft.fftfreq(len(detrended))
    
    # Get magnitudes (only positive frequencies)
    n = len(freqs) // 2
    magnitudes = np.abs(fft_vals[:n])
    pos_freqs = freqs[:n]
    
    # Skip DC component (index 0)
    magnitudes[0] = 0
    
    # Find top N frequencies
    top_indices = np.argsort(magnitudes)[-top_n:][::-1]
    
    dominant_frequencies = []
    for idx in top_indices:
        freq = pos_freqs[idx]
        period = 1.0 / freq if freq > 0 else float('inf')
        dominant_frequencies.append({
            "frequency": freq,
            "period_candles": round(period, 1),
            "magnitude": magnitudes[idx],
            "power_pct": round(magnitudes[idx] / magnitudes.sum() * 100, 1),
        })
    
    return dominant_frequencies, magnitudes, pos_freqs


def run_beep_matrix(df, pair="EURUSD"):
    """
    Run the full 2D BEEP matrix.
    Y-axis: Different filter dimensions
    X-axis: Recursive passes at each dimension
    """
    
    # Define the BEEP dimensions (Y-axis layers)
    dimensions = [
        {"name": "Price", "z": 2.0, "wick": 0.7, "color": "#00cc66"},
        {"name": "Momentum", "z": 1.5, "wick": 0.9, "color": "#3399ff"},
        {"name": "Structure", "z": 3.0, "wick": 0.5, "color": "#cc33ff"},
        {"name": "Scalper", "z": 1.2, "wick": 0.5, "color": "#ff6633"},
        {"name": "Swing", "z": 3.0, "wick": 0.85, "color": "#33cccc"},
    ]
    
    max_passes = 8
    
    # Build the matrix: roughness at each (dimension, pass)
    roughness_matrix = np.zeros((len(dimensions), max_passes))
    autocorr_matrix = np.zeros((len(dimensions), max_passes))
    noise_matrix = np.zeros((len(dimensions), max_passes))
    convergence_pass = {}
    
    print("\n[BEEP MATRIX] Building 2D grid...\n")
    
    for dim_idx, dim in enumerate(dimensions):
        current_df = df.copy()
        cumulative_noise = 0
        
        for pass_num in range(max_passes):
            cleaned, filtered, noise_count = apply_beep_pass(
                current_df, z_threshold=dim["z"], wick_threshold=dim["wick"]
            )
            
            cumulative_noise += noise_count
            quality = measure_signal_quality(cleaned["Close"])
            
            roughness_matrix[dim_idx, pass_num] = quality["roughness"]
            autocorr_matrix[dim_idx, pass_num] = quality["autocorr"]
            noise_matrix[dim_idx, pass_num] = noise_count
            
            if noise_count == 0 and dim["name"] not in convergence_pass:
                convergence_pass[dim["name"]] = pass_num + 1
            
            current_df = cleaned
        
        conv = convergence_pass.get(dim["name"], ">8")
        print(f"  {dim['name']:<12s}: Converged at pass {conv} | Total noise removed: {cumulative_noise}")
    
    # === VISUALIZATION ===
    
    fig, axes = plt.subplots(2, 2, figsize=(18, 14))
    fig.patch.set_facecolor("#0a0a0a")
    fig.suptitle(f"BEEP Matrix - {pair}\nDimensions (Y) x Recursive Passes (X)", 
                 color="white", fontsize=16, fontweight="bold", y=0.98)
    
    for ax in axes.flat:
        ax.set_facecolor("#0a0a0a")
        ax.tick_params(colors="#888888")
        for spine in ax.spines.values():
            spine.set_color("#333333")
    
    # Panel 1: Roughness heatmap (lower = cleaner signal)
    ax1 = axes[0, 0]
    cmap1 = LinearSegmentedColormap.from_list("beep", ["#00cc66", "#ffaa00", "#ff3333"])
    im1 = ax1.imshow(roughness_matrix, cmap=cmap1, aspect="auto")
    ax1.set_yticks(range(len(dimensions)))
    ax1.set_yticklabels([d["name"] for d in dimensions], color="#cccccc")
    ax1.set_xticks(range(max_passes))
    ax1.set_xticklabels([f"P{i+1}" for i in range(max_passes)], color="#cccccc")
    ax1.set_xlabel("Recursive Pass", color="#cccccc")
    ax1.set_ylabel("BEEP Dimension", color="#cccccc")
    ax1.set_title("Signal Roughness (lower = cleaner)", color="white", fontweight="bold")
    plt.colorbar(im1, ax=ax1, shrink=0.8)
    
    # Mark convergence points
    for dim_name, conv_pass in convergence_pass.items():
        dim_idx = next(i for i, d in enumerate(dimensions) if d["name"] == dim_name)
        if isinstance(conv_pass, int) and conv_pass <= max_passes:
            ax1.plot(conv_pass - 1, dim_idx, 'w*', markersize=15, markeredgecolor='black', markeredgewidth=0.5)
    
    # Panel 2: Noise found per pass (shows convergence)
    ax2 = axes[0, 1]
    for dim_idx, dim in enumerate(dimensions):
        ax2.plot(range(1, max_passes + 1), noise_matrix[dim_idx], 
                 color=dim["color"], linewidth=2, marker="o", markersize=5, label=dim["name"])
    ax2.set_xlabel("Recursive Pass", color="#cccccc")
    ax2.set_ylabel("New Noise Found", color="#cccccc")
    ax2.set_title("Noise Discovery per Pass (convergence = 0)", color="white", fontweight="bold")
    ax2.legend(facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    ax2.axhline(y=0, color="#555555", linestyle="--", linewidth=0.5)
    
    # Panel 3: Autocorrelation improvement (higher = more predictable)
    ax3 = axes[1, 0]
    for dim_idx, dim in enumerate(dimensions):
        ax3.plot(range(1, max_passes + 1), autocorr_matrix[dim_idx],
                 color=dim["color"], linewidth=2, marker="s", markersize=5, label=dim["name"])
    ax3.set_xlabel("Recursive Pass", color="#cccccc")
    ax3.set_ylabel("Autocorrelation", color="#cccccc")
    ax3.set_title("Signal Predictability (higher = more predictable)", color="white", fontweight="bold")
    ax3.legend(facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    
    # Panel 4: Fundamental frequency extraction
    ax4 = axes[1, 1]
    
    # Extract frequencies from RAW vs FULLY CLEANED
    raw_freqs, raw_mags, raw_f = extract_fundamental_frequency(df["Close"])
    
    # Get the cleanest version (Scalper BEEP, max passes)
    cleanest = df.copy()
    for _ in range(6):
        cleanest, _, _ = apply_beep_pass(cleanest, z_threshold=1.5, wick_threshold=0.5)
    
    clean_freqs, clean_mags, clean_f = extract_fundamental_frequency(cleanest["Close"])
    
    # Plot frequency spectrum
    ax4.plot(raw_f[1:200], raw_mags[1:200], color="#ff333380", linewidth=1, label="RAW (noisy)")
    ax4.plot(clean_f[1:200], clean_mags[1:200], color="#00cc66", linewidth=2, label="BEEP CLEANED (true wave)")
    ax4.fill_between(clean_f[1:200], clean_mags[1:200], alpha=0.2, color="#00cc66")
    ax4.set_xlabel("Frequency", color="#cccccc")
    ax4.set_ylabel("Magnitude", color="#cccccc")
    ax4.set_title("Frequency Spectrum: RAW vs TRUE WAVE", color="white", fontweight="bold")
    ax4.legend(facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    
    filepath = os.path.join(OUTPUT_DIR, f"beep_matrix_{pair}.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
    print(f"\n[BEEP] Matrix chart saved to {filepath}")
    
    # === TRUE WAVE ANALYSIS ===
    print(f"\n{'='*60}")
    print(f"  THE TRUE WAVE OF {pair}")
    print(f"{'='*60}")
    
    print(f"\n  RAW dominant frequencies (with noise):")
    for i, f in enumerate(raw_freqs[:3]):
        print(f"    #{i+1}: Period = {f['period_candles']} candles | Power = {f['power_pct']}%")
    
    print(f"\n  BEEP CLEANED dominant frequencies (true wave):")
    for i, f in enumerate(clean_freqs[:3]):
        print(f"    #{i+1}: Period = {f['period_candles']} candles | Power = {f['power_pct']}%")
    
    # The fundamental frequency
    fundamental = clean_freqs[0]
    period_hours = fundamental["period_candles"]
    period_days = period_hours / 24
    
    print(f"\n  FUNDAMENTAL FREQUENCY OF {pair}:")
    print(f"    Period: {period_hours} candles ({period_days:.1f} days)")
    print(f"    Power:  {fundamental['power_pct']}% of total signal energy")
    print(f"\n    This is the heartbeat of {pair}.")
    print(f"    The market breathes in cycles of ~{period_days:.0f} days.")
    print(f"    Replicate this wave. You ARE the market.")
    print(f"\n{'='*60}\n")
    
    return roughness_matrix, noise_matrix, autocorr_matrix


if __name__ == "__main__":
    print("\n" + "#" * 60)
    print("  BEEP MATRIX — 2D GRID ANALYSIS")
    print("  Y-axis: Dimensions | X-axis: Recursive Passes")
    print("  Goal: Find the single true wave")
    print("#" * 60)
    
    try:
        df = load_data("EURUSD", "1h")
    except FileNotFoundError:
        df = fetch_pair("EURUSD", period="6mo", interval="1h")
    
    run_beep_matrix(df, pair="EURUSD")
