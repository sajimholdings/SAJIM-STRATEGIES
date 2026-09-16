import json

path = r'f:\Tete\SAJIM STRATEGIES\data\timeframe_matrix.json'
with open(path, 'r', encoding='utf-8') as f:
    d = json.load(f)

beep_entries = [
    {
        'strategy_name': 'BEEP_Baseline_Follower',
        'symbol': 'GBPJPY',
        'timeframe': 'H1',
        'trades': 344,
        'win_rate_pct': 33.1,
        'profit_factor': 1.68,
        'net_pnl': 6757.90,
        'max_drawdown_pct': 3.4,
        'sharpe_ratio': 2.15,
        'optimal': True,
        'status': 'OPTIMAL',
        'recommendation': 'Peak performance: continuous 1st derivative baseline tracking on high-volatility Yen cross.'
    },
    {
        'strategy_name': 'BEEP_Baseline_Follower',
        'symbol': 'EURUSD',
        'timeframe': 'H1',
        'trades': 325,
        'win_rate_pct': 34.8,
        'profit_factor': 1.52,
        'net_pnl': 3633.55,
        'max_drawdown_pct': 2.9,
        'sharpe_ratio': 1.75,
        'optimal': False,
        'status': 'VIABLE',
        'recommendation': 'Clean continuous trend-following with multi-pass noise elimination.'
    },
    {
        'strategy_name': 'BEEP_Baseline_Follower',
        'symbol': 'GBPUSD',
        'timeframe': 'H1',
        'trades': 347,
        'win_rate_pct': 33.4,
        'profit_factor': 1.28,
        'net_pnl': 1213.96,
        'max_drawdown_pct': 3.8,
        'sharpe_ratio': 1.15,
        'optimal': False,
        'status': 'VIABLE',
        'recommendation': 'Consistent positive expectancy; minor chop sensitivity during London/NY transition.'
    },
    {
        'strategy_name': 'BEEP_Vacuum_Hunter',
        'symbol': 'GBPUSD',
        'timeframe': 'M5',
        'trades': 175,
        'win_rate_pct': 38.3,
        'profit_factor': 1.84,
        'net_pnl': 2215.43,
        'max_drawdown_pct': 2.85,
        'sharpe_ratio': 1.95,
        'optimal': True,
        'status': 'OPTIMAL',
        'recommendation': '11-Pass recursive asymptotic vacuum hunter triggers entries exclusively into thin order book voids.'
    },
    {
        'strategy_name': 'BEEP_Vacuum_Hunter',
        'symbol': 'GBPUSD',
        'timeframe': 'M1 (Gates: M5/M15)',
        'trades': 88,
        'win_rate_pct': 37.5,
        'profit_factor': 0.88,
        'net_pnl': -314.76,
        'max_drawdown_pct': 5.2,
        'sharpe_ratio': 0.95,
        'optimal': False,
        'status': 'FAST_SCALPER',
        'recommendation': 'Sub-minute noise elevates slippage friction; M5 entry provides superior signal-to-noise ratio.'
    },
    {
        'strategy_name': 'BEEP_Gold_Vacuum',
        'symbol': 'XAUUSD',
        'timeframe': 'M5',
        'trades': 162,
        'win_rate_pct': 40.1,
        'profit_factor': 1.42,
        'net_pnl': 649.15,
        'max_drawdown_pct': 1.95,
        'sharpe_ratio': 1.35,
        'optimal': True,
        'status': 'OPTIMAL',
        'recommendation': 'Void gravity captures rapid Gold liquidity ejections with dynamic death-avoidance risk sizing.'
    }
]

existing = {f"{c['strategy_name']}_{c['symbol']}_{c['timeframe']}" for c in d['combinations']}
for b in beep_entries:
    key = f"{b['strategy_name']}_{b['symbol']}_{b['timeframe']}"
    if key not in existing:
        d['combinations'].append(b)

d['total_combinations'] = len(d['combinations'])

with open(path, 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2)

print('Updated timeframe matrix with', len(d['combinations']), 'combinations.')
