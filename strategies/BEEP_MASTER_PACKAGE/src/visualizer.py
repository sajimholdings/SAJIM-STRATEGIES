"""
BEEP Visualizer
================
Plots raw price action vs BEEP-filtered (cleaned) price action.
Noise candles are highlighted so you can visually see what the filter catches.
"""

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")


def ensure_output_dir():
    """Create output directory if it doesn't exist."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def plot_beep_analysis(
    df: pd.DataFrame,
    pair: str = "EURUSD",
    show_last_n: int = 200,
    save: bool = True,
):
    """
    Create the main BEEP analysis chart.

    Shows:
    - Top panel: Price action with noise candles highlighted in red
    - Middle panel: Noise score over time
    - Bottom panel: Clean signal direction

    Args:
        df: DataFrame processed by BeepFilter.apply()
        pair: Name of the pair for the title
        show_last_n: Number of candles to display (most recent)
        save: Whether to save the chart to output/
    """
    # Take last N candles for readability
    plot_df = df.tail(show_last_n).copy()

    fig, axes = plt.subplots(
        3, 1,
        figsize=(18, 12),
        gridspec_kw={"height_ratios": [3, 1, 1]},
        sharex=True,
    )

    fig.patch.set_facecolor("#0a0a0a")
    for ax in axes:
        ax.set_facecolor("#0a0a0a")
        ax.tick_params(colors="#888888")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#333333")
        ax.spines["left"].set_color("#333333")

    # ── Panel 1: Price Action with Noise Highlighting ──
    ax1 = axes[0]
    x = range(len(plot_df))

    for i, (idx, row) in enumerate(plot_df.iterrows()):
        color = "#ff3333" if row["is_noise"] else (
            "#00cc66" if row["Close"] >= row["Open"] else "#cc3333"
        )
        alpha = 0.3 if row["is_noise"] else 0.9

        # Candle body
        body_bottom = min(row["Open"], row["Close"])
        body_height = abs(row["Close"] - row["Open"])
        ax1.add_patch(Rectangle(
            (i - 0.3, body_bottom), 0.6, body_height,
            facecolor=color, edgecolor=color, alpha=alpha,
        ))

        # Wicks
        ax1.plot(
            [i, i], [row["Low"], row["High"]],
            color=color, linewidth=0.5, alpha=alpha,
        )

        # Highlight noise candles with a background
        if row["is_noise"]:
            ax1.axvspan(i - 0.5, i + 0.5, alpha=0.08, color="#ff3333")

    ax1.set_ylabel("Price", color="#cccccc", fontsize=11)
    ax1.set_title(
        f"BEEP Filter Analysis — {pair}",
        color="#ffffff", fontsize=16, fontweight="bold", pad=15,
    )

    # Legend
    ax1.plot([], [], "s", color="#00cc66", markersize=8, label="Clean (Bullish)")
    ax1.plot([], [], "s", color="#cc3333", markersize=8, label="Clean (Bearish)")
    ax1.plot([], [], "s", color="#ff3333", markersize=8, alpha=0.3, label="NOISE (Filtered)")
    ax1.legend(
        loc="upper left", facecolor="#1a1a1a", edgecolor="#333333",
        labelcolor="#cccccc", fontsize=9,
    )

    # ── Panel 2: Noise Score ──
    ax2 = axes[1]
    colors = ["#00cc66" if s < 2 else "#ffaa00" if s < 3 else "#ff3333"
              for s in plot_df["noise_score"]]
    ax2.bar(x, plot_df["noise_score"], color=colors, alpha=0.7, width=0.8)
    ax2.set_ylabel("Noise Score", color="#cccccc", fontsize=11)
    ax2.axhline(y=2, color="#ff3333", linestyle="--", alpha=0.5, linewidth=0.8)
    ax2.set_ylim(0, 4.5)

    # ── Panel 3: Signal Direction ──
    ax3 = axes[2]
    signal = plot_df["signal_direction"].values
    pos_signal = np.where(signal > 0, signal, 0)
    neg_signal = np.where(signal < 0, signal, 0)
    ax3.fill_between(x, pos_signal, 0, alpha=0.5, color="#00cc66", label="Bullish")
    ax3.fill_between(x, neg_signal, 0, alpha=0.5, color="#cc3333", label="Bearish")
    ax3.axhline(y=0, color="#555555", linewidth=0.5)
    ax3.set_ylabel("Signal Direction", color="#cccccc", fontsize=11)
    ax3.set_xlabel("Candle Index", color="#cccccc", fontsize=11)
    ax3.legend(
        loc="upper left", facecolor="#1a1a1a", edgecolor="#333333",
        labelcolor="#cccccc", fontsize=9,
    )

    plt.tight_layout()

    if save:
        ensure_output_dir()
        filepath = os.path.join(OUTPUT_DIR, f"beep_analysis_{pair}.png")
        plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
        print(f"[BEEP] Chart saved to {filepath}")

    plt.show()
    return fig


def plot_noise_distribution(df: pd.DataFrame, pair: str = "EURUSD", save: bool = True):
    """
    Show the distribution of noise scores and breakdown by layer.
    """
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    fig.patch.set_facecolor("#0a0a0a")
    for ax in axes:
        ax.set_facecolor("#0a0a0a")
        ax.tick_params(colors="#888888")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#333333")
        ax.spines["left"].set_color("#333333")

    # Noise score distribution
    scores = df["noise_score"].value_counts().sort_index()
    colors = ["#00cc66", "#66cc66", "#ffaa00", "#ff6633", "#ff3333"]
    axes[0].bar(scores.index, scores.values, color=[colors[int(i)] for i in scores.index], alpha=0.8)
    axes[0].set_xlabel("Noise Score", color="#cccccc")
    axes[0].set_ylabel("Candle Count", color="#cccccc")
    axes[0].set_title("Noise Score Distribution", color="#ffffff", fontweight="bold")

    # By layer breakdown
    layers = ["statistical", "volume", "structural", "temporal"]
    layer_cols = [f"noise_{l}" for l in layers]
    layer_counts = [df[col].sum() for col in layer_cols]
    layer_colors = ["#3399ff", "#ff9933", "#cc33ff", "#33cccc"]

    axes[1].barh(layers, layer_counts, color=layer_colors, alpha=0.8)
    axes[1].set_xlabel("Anomalies Detected", color="#cccccc")
    axes[1].set_title("Anomalies by Filter Layer", color="#ffffff", fontweight="bold")

    # Add count labels
    for i, (count, layer) in enumerate(zip(layer_counts, layers)):
        axes[1].text(count + 1, i, str(count), va="center", color="#cccccc", fontsize=10)

    plt.tight_layout()

    if save:
        ensure_output_dir()
        filepath = os.path.join(OUTPUT_DIR, f"beep_distribution_{pair}.png")
        plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#0a0a0a")
        print(f"[BEEP] Distribution chart saved to {filepath}")

    plt.show()
    return fig
