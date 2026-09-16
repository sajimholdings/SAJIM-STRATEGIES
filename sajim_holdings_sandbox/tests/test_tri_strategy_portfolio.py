"""
========================================================================================
TRI-STRATEGY DIVERSIFIED PORTFOLIO: BEEP + EMA PULLBACK + ALCHEMIST MSNR
========================================================================================
Simulates the simultaneous execution of all 3 distinct engines across elite symbols:
  1. BEEP Canonical (M15 Breakout Expansion)
  2. EMA Pullback System (H1 Trend Retracement)
  3. Alchemist MSNR / LIT (M30/H1 Liquidity Sweep & Stop Hunt Rejection)
Evaluates combined equity curve, correlation matrix, and overnight signal readiness.
========================================================================================
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from test_beep_v3_canonical import run_canonical_backtest
from test_ema_pullback_system import run_ema_pullback_backtest
from test_alchemist_msnr import run_alchemist_msnr_backtest

# Curate optimal rosters based on empirical universe sweeps
BEEP_ROSTER = [
    ("XAUUSD", "M15"),
    ("US500",  "M15"),
    ("GER40",  "M15"),
    ("BTCUSD", "M15"),
]

EMA_ROSTER = [
    ("XAUUSD", "H1", "H4"),
    ("XRPUSD", "H1", "H4"),
    ("US30",   "H1", "H4"),
]

MSNR_ROSTER = [
    ("XRPUSD", "M30", "H4"),
    ("EURJPY", "H1",  "H4"),
    ("ETHUSD", "H1",  "H4"),
]

print("=" * 95)
print("     TRI-STRATEGY DIVERSIFIED FLEET BACKTEST (BEEP M15 + EMA H1 + MSNR M30/H1)")
print("===========================================================================================")

all_trades = []

print("\n--- 1. BEEP ENGINE (M15 Momentum Expansion) ---")
for sym, tf in BEEP_ROSTER:
    r = run_canonical_backtest(symbol=sym, tf=tf, min_layer_m=55.0)
    print(f"  [BEEP] {sym:<8} {tf}: {r['total_trades']:,} trades | WR: {r['win_rate']:>4.1f}% | PF: {r['profit_factor']:>4.2f} | Net: {r['total_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")
    for t in r["trades"]:
        all_trades.append({
            "strategy": "BEEP_M15",
            "symbol": sym,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "pnl_r": t.pnl_r,
        })

print("\n--- 2. EMA PULLBACK ENGINE (H1 Trend Retracement to EMA 20) ---")
for sym, tf, htf in EMA_ROSTER:
    r = run_ema_pullback_backtest(symbol=sym, tf=tf, htf=htf)
    print(f"  [EMA]  {sym:<8} {tf} ({htf}): {r['total_trades']:,} trades | WR: {r['win_rate']:>4.1f}% | PF: {r['profit_factor']:>4.2f} | Net: {r['total_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")
    for t in r["trades"]:
        all_trades.append({
            "strategy": "EMA_H1",
            "symbol": sym,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "pnl_r": t.pnl_r,
        })

print("\n--- 3. ALCHEMIST MSNR ENGINE (M30/H1 Liquidity Sweep Rejection) ---")
for sym, tf, htf in MSNR_ROSTER:
    r = run_alchemist_msnr_backtest(symbol=sym, tf=tf, htf=htf, sl_mode="structural")
    print(f"  [MSNR] {sym:<8} {tf} ({htf}): {r['total_trades']:,} trades | WR: {r['win_rate']:>4.1f}% | PF: {r['profit_factor']:>4.2f} | Net: {r['total_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")
    for t in r["trades"]:
        all_trades.append({
            "strategy": "MSNR_LIT",
            "symbol": sym,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "pnl_r": t.pnl_r,
        })

df_trades = pd.DataFrame(all_trades).sort_values("entry_time").reset_index(drop=True)

# Build daily portfolio P&L
df_trades["date"] = pd.to_datetime(df_trades["entry_time"]).dt.date
daily_pnl = df_trades.groupby(["date", "strategy"])["pnl_r"].sum().unstack(fill_value=0.0)

# Cross-Strategy Correlation Matrix
corr_matrix = daily_pnl.corr()
print("\n" + "=" * 95)
print("                   CROSS-STRATEGY DAILY P&L CORRELATION MATRIX")
print("===========================================================================================")
print(corr_matrix.to_string())

# Portfolio totals
daily_total = df_trades.groupby("date")["pnl_r"].sum()
cum_r = daily_total.cumsum()
peak_r = np.maximum.accumulate(cum_r)
dd_r = peak_r - cum_r
max_dd_r = float(np.max(dd_r))
total_net_r = float(cum_r.iloc[-1])

win_trades = (df_trades["pnl_r"] > 0).sum()
total_trades = len(df_trades)
win_rate = (win_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

gross_profit_r = df_trades.loc[df_trades["pnl_r"] > 0, "pnl_r"].sum()
gross_loss_r = abs(df_trades.loc[df_trades["pnl_r"] < 0, "pnl_r"].sum())
pf = gross_profit_r / gross_loss_r if gross_loss_r > 0 else np.nan

daily_mean = daily_total.mean()
daily_std = daily_total.std()
sharpe = (daily_mean / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0

# USD sizing on $1,000 base
usd_net_5 = total_net_r * 5.0
usd_dd_5 = max_dd_r * 5.0
usd_net_15 = total_net_r * 15.0
usd_dd_15 = max_dd_r * 15.0

print("\n" + "=" * 95)
print("         COMBINED TRI-STRATEGY PORTFOLIO SUMMARY (OVERNIGHT FLEET CANDIDATE)")
print("===========================================================================================")
print(f"Total Combined Trades:        {total_trades:,}")
print(f"Overall Portfolio Win Rate:   {win_rate:.2f}%")
print(f"Portfolio Profit Factor:      {pf:.2f}")
print(f"Annualized Sharpe Ratio:      {sharpe:.2f}")
print(f"Calmar Ratio (Net R / Max DD):{total_net_r / max_dd_r:.2f}")
print("-" * 95)
print("ACCOUNT PROJECTION ON A $1,000 USD BASE CAPITAL:")
print(f"  At 0.5% Risk per Trade ($5.00 / R):")
print(f"    Net Profit:               +${usd_net_5:,.2f} USD (+{total_net_r:+.1f} R)")
print(f"    Max Portfolio Drawdown:   -${usd_dd_5:,.2f} USD ({max_dd_r:.2f} R / {(usd_dd_5 / 1000.0) * 100:.1f}% DD)")
print(f"  At 1.5% Risk per Trade ($15.00 / R):")
print(f"    Net Profit:               +${usd_net_15:,.2f} USD (+{total_net_r:+.1f} R)")
print(f"    Max Portfolio Drawdown:   -${usd_dd_15:,.2f} USD ({max_dd_r:.2f} R / {(usd_dd_15 / 1000.0) * 100:.1f}% DD)")
print("=" * 95)
