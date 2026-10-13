"""
Test runner for EA_AGGRESSIVE_SCALPER on MT5 Strategy Tester
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
REPO_ROOT = Path(__file__).resolve().parent.parent

from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter
from sajim_quant.registry import record_run
from sajim_quant.analytics import calculate_metrics, generate_markdown_tear_sheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_aggressive")


def run_aggressive_test():
    ea_path = (REPO_ROOT / "strategies" / "Scalper1" / "MQL5" / "Experts" / "EA_AGGRESSIVE_SCALPER.mq5").resolve()
    assert ea_path.exists(), f"File not found: {ea_path}"

    adapter = MT5HeadlessAdapter(
        mq5_source_path=ea_path,
        strategy_name="Scalper1_EA_AGGRESSIVE_SCALPER",
    )

    logger.info("Executing headless backtest on XAUUSD M5 for EA_AGGRESSIVE_SCALPER...")
    result = adapter.run(
        symbol="XAUUSD",
        timeframe="M5",
        start_date="2024-09-01",
        end_date="2024-10-15",
        initial_balance=50000.0,
        timeout_seconds=180,
    )

    logger.info(f"Test finished! Harvested {result.total_trades} normalized trades.")

    if result.total_trades > 0:
        sample = result.trades[0]
        logger.info(f"Sample Trade: ID={sample.trade_id} | Dir={sample.direction} | PnL=${sample.net_pnl:.2f}")

    metrics = calculate_metrics(result)
    logger.info(f"Net Profit: ${metrics.net_profit:,.2f} | Win Rate: {metrics.win_rate_pct:.1f}% | Profit Factor: {metrics.profit_factor:.2f}")

    md_report = generate_markdown_tear_sheet(
        metrics,
        assumptions={
            "engine": "MT5 Strategy Tester (Headless)",
            "symbol": "XAUUSD",
            "timeframe": "M5",
            "period": "2024-09-01 to 2024-10-15",
        }
    )
    record_run(result, strategy_id="ea_aggressive_scalper", folder="strategies/Scalper1", tear_sheet_md=md_report)
    print(f"[registry] recorded ea_aggressive_scalper -> results/ea_aggressive_scalper/")
    logger.info(f"Saved tear sheet to {report_path}")

    return result, metrics


if __name__ == "__main__":
    run_aggressive_test()
