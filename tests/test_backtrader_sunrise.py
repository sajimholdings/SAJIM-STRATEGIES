"""
SAJIM Quant - Backtrader Sunrise Strategy Runner
Executes SunriseOgle strategy on 2-year Dukascopy XAUUSD Parquet data
and harvests normalized trades into SAJIM schema.
"""

import sys
import logging
from pathlib import Path

# Add project root and strategy directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "strategies" / "backtrader-pullback-window-xauusd" / "src"))

from strategy.sunrise_ogle_xauusd import SunriseOgle
from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
from sajim_quant.analytics import calculate_metrics, generate_text_tear_sheet, generate_markdown_tear_sheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_sunrise")


def run_sunrise_test():
    logger.info("Initializing BacktraderAdapter for SunriseOgle (XAUUSD)...")
    adapter = BacktraderAdapter(
        strategy_class=SunriseOgle,
        strategy_name="SunriseOgle_XAUUSD_M5",
    )

    logger.info("Executing backtest on 2-year XAUUSD M5 data...")
    # Run test on 2024 data (or full 2024-2025 period)
    result = adapter.run(
        symbol="XAUUSD",
        timeframe="M5",
        start_date="2024-09-01",
        end_date="2025-03-01",
        initial_balance=100000.0,
        commission_per_lot=2.0,
        enable_long_trades=True,
        enable_short_trades=True,
        size=1,
        contract_size=1,
        use_forex_position_calc=False,
        enable_risk_sizing=False,
        plot_result=False,
        print_signals=False,
    )

    logger.info(f"Execution complete! Harvested {result.total_trades} normalized trades.")

    if result.total_trades > 0:
        sample = result.trades[0]
        logger.info(f"First Trade: ID={sample.trade_id} | Dir={sample.direction} | PnL=${sample.net_pnl:.2f} | Entry={sample.entry_time} | Exit={sample.exit_time}")

    # Compute standardized analytics
    metrics = calculate_metrics(result)
    logger.info(f"Net Profit: ${metrics.net_profit:,.2f} | Sharpe: {metrics.sharpe_ratio:.2f} | Win Rate: {metrics.win_rate_pct:.1f}% | Max DD: {metrics.max_drawdown_pct:.2f}%")

    # Generate Markdown Tear Sheet
    md_report = generate_markdown_tear_sheet(
        metrics,
        assumptions={
            "engine": "Backtrader (Python)",
            "symbol": "XAUUSD",
            "timeframe": "M5",
            "spread": "Dukascopy Bid/Ask (Fixed commission $2/lot)",
            "period": f"{result.metadata.start_date[:10]} to {result.metadata.end_date[:10]}",
        }
    )

    report_path = Path("tests/backtrader_sunrise_xauusd_tear_sheet.md")
    report_path.write_text(md_report, encoding="utf-8")
    logger.info(f"Saved tear sheet to {report_path}")

    return result, metrics


if __name__ == "__main__":
    run_sunrise_test()
