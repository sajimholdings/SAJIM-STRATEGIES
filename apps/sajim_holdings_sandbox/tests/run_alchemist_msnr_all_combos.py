"""
========================================================================================
ALCHEMIST MSNR & LIT — EXHAUSTIVE MULTI-TIMEFRAME UNIVERSE SWEEP
========================================================================================
Executes across 20 Symbols x (4 Execution Timeframes x 3 HTF Modes)
Generates full JSON database & comparative analytics.
========================================================================================
"""

import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from test_alchemist_msnr import run_alchemist_msnr_backtest

SYMBOLS = [
    "XAUUSD", "XRPUSD", "GBPJPY", "US30", "GER40", "US500", "BTCUSD", "ETHUSD",
    "EURUSD", "GBPUSD", "USDCAD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD",
    "EURJPY", "EURGBP", "XAGUSD", "UK100", "LTCUSD"
]

# Combo configurations: (Execution TF, HTF Bias, Label)
COMBO_CONFIGS = [
    ("H1", "H4", "H1 / H4 Bias"),
    ("M30", "H4", "M30 / H4 Bias"),
    ("M15", "H4", "M15 / H4 Bias"),
    ("M5", "H4", "M5 / H4 Bias"),
    ("M30", "H1", "M30 / H1 Bias"),
    ("M15", "H1", "M15 / H1 Bias (Alchemist Flagship)"),
    ("M5", "H1", "M5 / H1 Bias"),
    ("H1", "NONE", "H1 Standalone"),
    ("M15", "NONE", "M15 Standalone"),
]

OUT_JSON = Path(__file__).resolve().parents[3] / "data" / "alchemist_msnr_multi_timeframe_matrix.json"


def main():
    print("=" * 105)
    print("       ALCHEMIST MSNR / LIT — EXHAUSTIVE MULTI-TIMEFRAME UNIVERSE SWEEP")
    print("=========================================================================================================")
    print(f"Total Symbols: {len(SYMBOLS)} | Total Configs: {len(COMBO_CONFIGS)} | Total Runs: {len(SYMBOLS) * len(COMBO_CONFIGS)}")
    print("-" * 105)
    print(f"{'Symbol':<8} {'Exec TF':<8} {'HTF':<5} {'Trades':<8} {'Win Rate':<10} {'PF':<7} {'Net (R)':<10} {'Net USD ($15/R)':<17} {'Max DD (R)':<11} {'Status'}")
    print("-" * 105)

    all_results: List[Dict[str, Any]] = []
    t_start = time.time()

    for sym in SYMBOLS:
        for tf, htf, label in COMBO_CONFIGS:
            try:
                r = run_alchemist_msnr_backtest(symbol=sym, tf=tf, htf=htf, sl_mode="structural")
                if r and r["total_trades"] > 0:
                    pf = r["profit_factor"]
                    net_r = r["total_pnl_r"]
                    wr = r["win_rate"]
                    dd = r["max_drawdown_r"]
                    trades = r["total_trades"]

                    if pf >= 1.25 and net_r >= 10.0 and dd <= 15.0:
                        status = "ELITE"
                    elif pf >= 1.05 and net_r > 0.0:
                        status = "VIABLE"
                    elif pf >= 0.95 and abs(net_r) < 5.0:
                        status = "FLAT"
                    else:
                        status = "BLEEDER"

                    usd_15 = net_r * 15.0
                    usd_str = f"+${usd_15:.2f}" if usd_15 >= 0 else f"-${abs(usd_15):.2f}"

                    entry = {
                        "symbol": sym,
                        "tf": tf,
                        "htf": htf,
                        "label": label,
                        "bars_scanned": r["bars_scanned"],
                        "total_trades": trades,
                        "win_rate": wr,
                        "profit_factor": pf,
                        "expectancy_r": r["expectancy_r"],
                        "net_pnl_r": net_r,
                        "net_usd_15_risk": usd_15,
                        "net_usd_05_risk": net_r * 5.0,
                        "max_drawdown_r": dd,
                        "status": status,
                        "exit_reasons": r["exit_reasons"],
                    }
                    all_results.append(entry)

                    print(f"{sym:<8} {tf:<8} {htf:<5} {trades:<8} {wr:>5.1f}%     {pf:>5.2f} {net_r:>+7.1f} R   {usd_str:<17} {dd:>6.2f} R    {status}")
                else:
                    print(f"{sym:<8} {tf:<8} {htf:<5} [0 Trades]")
            except Exception as e:
                print(f"{sym:<8} {tf:<8} {htf:<5} [ERROR: {e}]")

    total_time = time.time() - t_start
    print("=" * 105)
    print(f"Sweep Completed in {total_time:.1f} seconds! Total combos evaluated: {len(all_results)}")

    # Save to JSON
    output_payload = {
        "strategy": "Alchemist MSNR / LIT (Liquidity Inducement Theory)",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_combos": len(all_results),
        "combos": all_results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)
    print(f"Master Matrix saved to: {OUT_JSON}")

    # Top Combos Ranking
    df = pd.DataFrame(all_results)
    if not df.empty:
        print("\n" + "=" * 105)
        print("                   TOP 15 BEST PERFORMING ALCHEMIST MSNR COMBINATIONS")
        print("=========================================================================================================")
        top15 = df.sort_values(by=["profit_factor", "net_pnl_r"], ascending=[False, False]).head(15)
        for idx, row in top15.iterrows():
            print(f"  {row['symbol']:<8} TF: {row['tf']:<4} HTF: {row['htf']:<4} | Trades: {row['total_trades']:<5} | WR: {row['win_rate']:>5.1f}% | PF: {row['profit_factor']:>5.2f} | Net: {row['net_pnl_r']:>+7.1f} R (+$ {row['net_usd_15_risk']:.2f}) | DD: {row['max_drawdown_r']:>5.2f} R | {row['status']}")

        print("\n" + "=" * 105)
        print("                     PERFORMANCE BY EXECUTION TIMEFRAME")
        print("=========================================================================================================")
        tf_summary = df.groupby("tf").agg(
            total_combos=("symbol", "count"),
            profitable_combos=("net_pnl_r", lambda x: (x > 0).sum()),
            elite_combos=("status", lambda x: (x == "ELITE").sum()),
            avg_pf=("profit_factor", "mean"),
            avg_wr=("win_rate", "mean"),
            total_net_r=("net_pnl_r", "sum"),
            avg_dd_r=("max_drawdown_r", "mean"),
        )
        print(tf_summary.to_string())

        print("\n" + "=" * 105)
        print("                     HTF BIAS COMPARISON: H4 vs H1 vs NONE")
        print("=========================================================================================================")
        htf_summary = df.groupby("htf").agg(
            total_combos=("symbol", "count"),
            profitable_combos=("net_pnl_r", lambda x: (x > 0).sum()),
            elite_combos=("status", lambda x: (x == "ELITE").sum()),
            avg_pf=("profit_factor", "mean"),
            avg_wr=("win_rate", "mean"),
            total_net_r=("net_pnl_r", "sum"),
        )
        print(htf_summary.to_string())
        print("=" * 105)


if __name__ == "__main__":
    main()
