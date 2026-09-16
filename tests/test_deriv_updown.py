"""
Test ingestion and analytics for Deriv UpDown Momentum strategy.
"""
import sys
from pathlib import Path
from decimal import Decimal

# Add workspace to path
workspace_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(workspace_dir))

from sajim_quant.adapters.csv_importer import UniversalLogImporter
from sajim_quant.analytics.metrics import calculate_metrics
from sajim_quant.analytics.report import generate_markdown_tear_sheet

csv_file = workspace_dir / "strategies" / "Deriv UpDown" / "backtest_results.csv"
res = UniversalLogImporter.import_deriv_updown_csv(
    csv_path=csv_file,
    strategy_name="Deriv_UpDown_Momentum",
    symbol="1HZ100V",
    timeframe="5M",
    ahead_filter=1,
    stake=10.0,
    payout_rate=0.95
)

metrics = calculate_metrics(res)
print(f"Ingested {len(res.trades)} trades.")
print(f"Total Net PnL: ${metrics.net_profit:.2f}")
print(f"Win Rate: {metrics.win_rate_pct:.2f}%")
print(f"Profit Factor: {metrics.profit_factor:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}%")
print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")

report_md = generate_markdown_tear_sheet(metrics)
tear_sheet_path = workspace_dir / "tests" / "deriv_updown_momentum_tear_sheet.md"
with open(tear_sheet_path, "w", encoding="utf-8") as f:
    f.write(report_md)

print(f"Saved tear sheet to {tear_sheet_path}")
