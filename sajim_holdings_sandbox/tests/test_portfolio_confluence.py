"""
Portfolio Confluence Analysis: BEEP (M15) + EMA Pullback (H1) on Gold (XAUUSD)
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from test_beep_v3_canonical import run_canonical_backtest
from test_ema_pullback_system import run_ema_pullback_backtest

print("=" * 80)
print("     PORTFOLIO CONFLUENCE: BEEP (M15) + EMA PULLBACK (H1) ON GOLD (XAUUSD)")
print("================================================================================")

beep_res = run_canonical_backtest(symbol="XAUUSD", tf="M15", min_layer_m=55.0)
ema_res = run_ema_pullback_backtest(symbol="XAUUSD", tf="H1")

print(f"BEEP Alone (M15 Breakout):")
print(f"  Trades: {beep_res['total_trades']:,} | Win Rate: {beep_res['win_rate']:.1f}% | PF: {beep_res['profit_factor']:.2f}")
print(f"  Net P&L: {beep_res['total_pnl_r']:+.1f} R | Max Drawdown: {beep_res['max_drawdown_r']:.2f} R")

print("-" * 80)
print(f"EMA Pullback Alone (H1 Retracement):")
print(f"  Trades: {ema_res['total_trades']:,} | Win Rate: {ema_res['win_rate']:.1f}% | PF: {ema_res['profit_factor']:.2f}")
print(f"  Net P&L: {ema_res['total_pnl_r']:+.1f} R | Max Drawdown: {ema_res['max_drawdown_r']:.2f} R")

# Build daily return series
beep_daily = pd.DataFrame([{"date": t.entry_time.date(), "beep_r": t.pnl_r} for t in beep_res["trades"]]).groupby("date").sum()
ema_daily = pd.DataFrame([{"date": t.entry_time.date(), "ema_r": t.pnl_r} for t in ema_res["trades"]]).groupby("date").sum()

merged = pd.concat([beep_daily, ema_daily], axis=1).fillna(0.0)
corr = float(merged["beep_r"].corr(merged["ema_r"]))

print("-" * 80)
print(f"Daily P&L Correlation (r): {corr:+.3f}")
if abs(corr) < 0.15:
    print("  ==> EXTREMELY ORTHOGONAL (Virtually zero correlation!) Perfect for multi-strategy fleet.")

# Combined portfolio
merged["portfolio_r"] = merged["beep_r"] + merged["ema_r"]
cum_pnl = merged["portfolio_r"].cumsum()
peak = np.maximum.accumulate(cum_pnl)
drawdowns = peak - cum_pnl
max_dd = float(np.max(drawdowns))

# On $1,000 account at 1.5% risk ($15/R)
combined_net_r = float(cum_pnl.iloc[-1])
combined_usd_15 = combined_net_r * 15.0

print("-" * 80)
print("COMBINED DUAL-STRATEGY PORTFOLIO OUTCOME:")
print(f"  Total Combined Trades: {len(beep_res['trades']) + len(ema_res['trades']):,}")
print(f"  Combined Net P&L:      {combined_net_r:+.1f} R (+${combined_usd_15:,.2f} USD on $1,000)")
print(f"  Combined Max Drawdown: {max_dd:.2f} R (${max_dd * 15.0:.2f} USD)")
print(f"  Return-to-Drawdown (Calmar Proxy): {combined_net_r / max_dd:.2f}")
print("=" * 80)
