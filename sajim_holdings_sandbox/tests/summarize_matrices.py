import json

data = json.load(open(r'F:\Tete\SAJIM STRATEGIES\data\canonical_beep_edge_matrix.json'))
print(f"{'Symbol':<8} {'BEEP Status':<12} {'BEEP PF':<9} {'BEEP Net R':<12} {'BEEP DD (R)':<12}")
print("-" * 55)
for c in data['combinations']:
    print(f"{c['symbol']:<8} {c['status']:<12} {c['profit_factor']:<9.2f} {c['net_pnl_r']:>+10.1f} R {c['max_drawdown_r']:>10.2f} R")
