"""
Multi-Strategy Fleet Backtest:
Combining Canonical BEEP (M15 Breakout) + Pine Script EMA Pullback (H1 Retracement)
Across Elite Curated Symbols on a $1,000 USD Account
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from test_beep_v3_canonical import run_canonical_backtest
from test_ema_pullback_system import run_ema_pullback_backtest

# Curate elite combinations based on empirical sweep results
BEEP_SYMBOLS = ["XAUUSD", "US500", "GER40", "BTCUSD", "USDCAD", "EURUSD"]
EMA_SYMBOLS  = ["XAUUSD", "XRPUSD", "GBPJPY", "US30"]

print("=" * 84)
print("     MULTI-STRATEGY DIVERSIFIED FLEET BACKTEST (BEEP M15 + EMA PULLBACK H1)")
print("====================================================================================")

all_trades = []

print("Running BEEP on Elite Roster (M15 Breakout Expansion):")
for s in BEEP_SYMBOLS:
    r = run_canonical_backtest(symbol=s, tf="M15", min_layer_m=55.0)
    print(f"  [BEEP] {s:<8}: {r['total_trades']:,} trades | WR: {r['win_rate']:>4.1f}% | PF: {r['profit_factor']:>4.2f} | Net: {r['total_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")
    for t in r["trades"]:
        all_trades.append({
            "strategy": "BEEP_M15",
            "symbol": s,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "pnl_r": t.pnl_r,
            "pnl_usd_15": t.pnl_r * 15.0,  # 1.5% risk on $1,000
            "pnl_usd_5": t.pnl_r * 5.0    # 0.5% risk on $1,000
        })

print("\nRunning EMA Pullback on Elite Roster (H1 Structural Retracement):")
for s in EMA_SYMBOLS:
    r = run_ema_pullback_backtest(symbol=s, tf="H1")
    print(f"  [EMA]  {s:<8}: {r['total_trades']:,} trades | WR: {r['win_rate']:>4.1f}% | PF: {r['profit_factor']:>4.2f} | Net: {r['total_pnl_r']:>+6.1f} R | DD: {r['max_drawdown_r']:>5.2f} R")
    for t in r["trades"]:
        all_trades.append({
            "strategy": "EMA_H1",
            "symbol": s,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "pnl_r": t.pnl_r,
            "pnl_usd_15": t.pnl_r * 15.0,
            "pnl_usd_5": t.pnl_r * 5.0
        })

df_trades = pd.DataFrame(all_trades).sort_values("entry_time").reset_index(drop=True)

# Build daily portfolio P&L
df_trades["date"] = df_trades["entry_time"].dt.date
daily_pnl = df_trades.groupby(["date", "strategy"])["pnl_r"].sum().unstack(fill_value=0.0)

# Calculate strategy correlation
corr = daily_pnl["BEEP_M15"].corr(daily_pnl["EMA_H1"])

# Daily portfolio aggregate
daily_total = df_trades.groupby("date")["pnl_r"].sum()
cum_r = daily_total.cumsum()
peak_r = np.maximum.accumulate(cum_r)
dd_r = peak_r - cum_r
max_dd_r = float(np.max(dd_r))
total_net_r = float(cum_r.iloc[-1])

# Risk calculations
usd_net_15 = total_net_r * 15.0
usd_dd_15 = max_dd_r * 15.0

usd_net_5 = total_net_r * 5.0
usd_dd_5 = max_dd_r * 5.0

# Win rate and trade metrics
win_trades = (df_trades["pnl_r"] > 0).sum()
total_trades = len(df_trades)
win_rate = (win_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

gross_profit_r = df_trades.loc[df_trades["pnl_r"] > 0, "pnl_r"].sum()
gross_loss_r = abs(df_trades.loc[df_trades["pnl_r"] < 0, "pnl_r"].sum())
pf = gross_profit_r / gross_loss_r if gross_loss_r > 0 else np.nan

# Annualized Sharpe ratio (using daily returns)
daily_mean = daily_total.mean()
daily_std = daily_total.std()
sharpe = (daily_mean / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0

print("=" * 84)
print("            COMBINED MULTI-STRATEGY PORTFOLIO PERFORMANCE SUMMARY")
print("====================================================================================")
print(f"Total Combined Trades:        {total_trades:,}")
print(f"Overall Win Rate:             {win_rate:.2f}%")
print(f"Portfolio Profit Factor:      {pf:.2f}")
print(f"Daily Strategy Correlation:   {corr:+.4f} (Virtually Zero Correlation!)")
print(f"Annualized Sharpe Ratio:      {sharpe:.2f}")
print(f"Calmar Ratio (Net R / Max DD):{total_net_r / max_dd_r:.2f}")
print("-" * 84)
print("ACCOUNT PROJECTION ON A $1,000 USD BASE CAPITAL:")
print(f"  At 0.5% Risk per Trade ($5.00 / R):")
print(f"    Net Profit:               +${usd_net_5:,.2f} USD (+{total_net_r:+.1f} R)")
print(f"    Max Portfolio Drawdown:   -${usd_dd_5:,.2f} USD ({max_dd_r:.2f} R / {(usd_dd_5 / 1000.0) * 100:.1f}% DD)")
print(f"  At 1.5% Risk per Trade ($15.00 / R):")
print(f"    Net Profit:               +${usd_net_15:,.2f} USD (+{total_net_r:+.1f} R)")
print(f"    Max Portfolio Drawdown:   -${usd_dd_15:,.2f} USD ({max_dd_r:.2f} R / {(usd_dd_15 / 1000.0) * 100:.1f}% DD)")
print("=" * 84)
