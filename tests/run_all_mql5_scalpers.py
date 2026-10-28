"""
Scalper1 MQL5 Expert Advisors Automated Headless Test
Runs EA_SCALPER_XAUUSD headlessly in MT5 and harvests trades into SAJIM schema.
"""

import sys
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter
from sajim_quant.analytics import calculate_metrics, generate_text_tear_sheet, generate_markdown_tear_sheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_scalper1")


def run_scalper1_test():
    ea_path = (Path(__file__).resolve().parent.parent / "strategies" / "Scalper1" / "MQL5" / "Experts" / "EA_SCALPER_XAUUSD.mq5").resolve()
    assert ea_path.exists(), f"File not found: {ea_path}"

    logger.info(f"Initializing MT5HeadlessAdapter for {ea_path.name}...")
    adapter = MT5HeadlessAdapter(
        mq5_source_path=ea_path,
        strategy_name="Scalper1_EA_SCALPER_XAUUSD",
    )

    logger.info("Executing headless backtest on XAUUSD M5...")
    result = adapter.run(
        symbol="XAUUSD",
        timeframe="M5",
        start_date="2024-09-01",
        end_date="2024-11-30",
        initial_balance=50000.0,
        timeout_seconds=180,
    )

    logger.info(f"Test finished! Total trades harvested: {result.total_trades}")

    metrics = calculate_metrics(result)
    logger.info(f"Net Profit: ${metrics.net_profit:,.2f} | Win Rate: {metrics.win_rate_pct:.1f}% | Profit Factor: {metrics.profit_factor:.2f}")

    # Generate Markdown Tear Sheet
    md_report = generate_markdown_tear_sheet(
        metrics,
        assumptions={
            "engine": "MT5 Strategy Tester (Headless)",
            "symbol": "XAUUSD",
            "timeframe": "M5",
            "spread": "broker/floating",
            "period": "2024-09-01 to 2024-11-30",
        }
    )
    report_path = Path("tests/scalper1_ea_scalper_xauusd_tear_sheet.md")
    report_path.write_text(md_report, encoding="utf-8")
    logger.info(f"Saved tear sheet to {report_path}")

    return result, metrics


if __name__ == "__main__":
    run_scalper1_test()
