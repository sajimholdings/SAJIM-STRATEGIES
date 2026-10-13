"""
========================================================================================
MULTI-STRATEGY TRIFECTA COMPARATIVE SYNTHESIS
========================================================================================
Compares:
  1. BEEP Canonical (Momentum & Volatility Expansion)
  2. EMA Pullback System (Trend Retracement)
  3. Alchemist MSNR / LIT (Liquidity Sweep & Market Structure Rejection)

Identifies top symbols, optimal timeframes, and optimal multi-strategy portfolio balance.
========================================================================================
"""

import json
from pathlib import Path
import pandas as pd
import numpy as np

DATA_DIR = Path(str(__import__("pathlib").Path(__file__).resolve().parents[3] / r"data"))

BEEP_JSON = DATA_DIR / "strategy_universe_matrix.json"
EMA_JSON = DATA_DIR / "ema_pullback_multi_timeframe_matrix.json"
MSNR_JSON = DATA_DIR / "alchemist_msnr_multi_timeframe_matrix.json"


def load_matrix(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "combos" in data:
        return pd.DataFrame(data["combos"])
    elif "matrix" in data:
        return pd.DataFrame(data["matrix"])
    return pd.DataFrame()


def main():
    print("=" * 100)
    print("               MULTI-STRATEGY COMPARATIVE ANALYSIS: BEEP vs EMA PULLBACK vs MSNR")
    print("====================================================================================================")

    df_ema = load_matrix(EMA_JSON)
    df_msnr = load_matrix(MSNR_JSON)

    if not df_ema.empty:
        print("\n--- 1. EMA PULLBACK SYSTEM (Top 5 Setups) ---")
        top_ema = df_ema.sort_values(by=["profit_factor", "net_pnl_r"], ascending=[False, False]).head(5)
        for _, r in top_ema.iterrows():
            print(f"  {r['symbol']:<8} TF: {r['tf']:<4} HTF: {r['htf']:<3} | Trades: {r['total_trades']:<5} | WR: {r['win_rate']:>5.1f}% | PF: {r['profit_factor']:>5.2f} | Net: {r['net_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")

    if not df_msnr.empty:
        print("\n--- 2. ALCHEMIST MSNR / LIT SYSTEM (Top 5 Setups) ---")
        top_msnr = df_msnr.sort_values(by=["profit_factor", "net_pnl_r"], ascending=[False, False]).head(5)
        for _, r in top_msnr.iterrows():
            print(f"  {r['symbol']:<8} TF: {r['tf']:<4} HTF: {r['htf']:<4} | Trades: {r['total_trades']:<5} | WR: {r['win_rate']:>5.1f}% | PF: {r['profit_factor']:>5.2f} | Net: {r['net_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")

        print("\n--- 3. TIMEFRAME EFFICIENCY BENCHMARK ---")
        print("MSNR by Execution Timeframe:")
        msnr_tf = df_msnr.groupby("tf").agg(
            total=("symbol", "count"),
            profitable=("net_pnl_r", lambda x: (x > 0).sum()),
            avg_pf=("profit_factor", "mean"),
            net_r=("net_pnl_r", "sum")
        )
        print(msnr_tf)

        if not df_ema.empty:
            print("\nEMA Pullback by Execution Timeframe:")
            ema_tf = df_ema.groupby("tf").agg(
                total=("symbol", "count"),
                profitable=("net_pnl_r", lambda x: (x > 0).sum()),
                avg_pf=("profit_factor", "mean"),
                net_r=("net_pnl_r", "sum")
            )
            print(ema_tf)


if __name__ == "__main__":
    main()
