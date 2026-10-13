"""
Sweep runner for EMA Pullback System across all 20 symbols
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from test_ema_pullback_system import run_ema_pullback_backtest

symbols = [
    'EURUSD', 'GBPUSD', 'USDCAD', 'USDJPY', 'USDCHF', 'AUDUSD', 'NZDUSD',
    'EURJPY', 'GBPJPY', 'EURGBP', 'XAUUSD', 'XAGUSD', 'US30', 'US500',
    'GER40', 'UK100', 'BTCUSD', 'ETHUSD', 'LTCUSD', 'XRPUSD'
]

print("=" * 82)
print("             EMA PULLBACK SYSTEM — FULL 20-SYMBOL H1 UNIVERSE SWEEP")
print("==================================================================================")
print(f"{'Symbol':<8} {'Trades':<8} {'Win Rate':<10} {'PF':<7} {'Net (R)':<10} {'Net USD ($15/R)':<16} {'Max DD (R)':<12} {'Status'}")
print("-" * 82)

results = []
for s in symbols:
    try:
        r = run_ema_pullback_backtest(symbol=s, tf='H1')
        if r and r['total_trades'] > 0:
            status = "ELITE" if r['profit_factor'] >= 1.30 and r['total_pnl_r'] > 10 else ("VIABLE" if r['profit_factor'] >= 1.05 and r['total_pnl_r'] > 0 else "BLEEDER")
            results.append((s, r, status))
            usd_str = f"+${r['net_usd_15_risk']:.2f}" if r['net_usd_15_risk'] >= 0 else f"-${abs(r['net_usd_15_risk']):.2f}"
            print(f"{s:<8} {r['total_trades']:<8} {r['win_rate']:>5.1f}%     {r['profit_factor']:>5.2f} {r['total_pnl_r']:>+7.1f} R   {usd_str:<16} {r['max_drawdown_r']:>6.2f} R     {status}")
        else:
            print(f"{s:<8} [No trades or insufficient data]")
    except Exception as e:
        print(f"{s:<8} [ERROR: {e}]")

print("=" * 82)
