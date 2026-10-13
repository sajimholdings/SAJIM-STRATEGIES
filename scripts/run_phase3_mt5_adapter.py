"""
Phase 3 Verification: MT5 Headless Adapter End-to-End Execution
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
REPO_ROOT = Path(__file__).resolve().parent.parent

from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter
from sajim_quant.registry import record_run
from sajim_quant.analytics import calculate_metrics, generate_text_tear_sheet, generate_markdown_tear_sheet


def test_mt5_headless_adapter_end_to_end():
    print("Testing MT5 Headless Adapter on GoldOrb (XAUUSD)...")
    gold_orb_path = (REPO_ROOT / "strategies" / "GoldOrb" / "GOLD_ORB" / "GOLD_ORB.mq5").resolve()
    assert gold_orb_path.exists(), f"GOLD_ORB.mq5 not found at {gold_orb_path}"

    adapter = MT5HeadlessAdapter(
        mq5_source_path=gold_orb_path,
        strategy_name="GoldOrb_ORB",
    )

    # Run headless test on MT5 for 2024
    result = adapter.run(
        symbol="XAUUSD",
        timeframe="H1",
        start_date="2024-09-01",
        end_date="2024-11-30",
        initial_balance=50000.0,
        timeout_seconds=120,
    )

    print(f"[PASS] MT5 Headless execution completed! Harvested {result.total_trades} normalized trades.")

    if result.total_trades > 0:
        sample_trade = result.trades[0]
        print(f"Sample MT5 Trade: ID={sample_trade.trade_id}, Dir={sample_trade.direction}, PnL=${sample_trade.net_pnl:.2f}, Dur={sample_trade.duration_seconds/3600:.1f}h")

    # Run the exact same Analytics Suite!
    metrics = calculate_metrics(result)
    print(f"[PASS] Analytics calculated successfully for MT5 strategy!")
    print(f"Net Profit: ${metrics.net_profit:,.2f} | Win Rate: {metrics.win_rate_pct:.1f}% | Profit Factor: {metrics.profit_factor:.2f}")

    # Generate ASCII Tear Sheet
    tear_sheet = generate_text_tear_sheet(metrics)
    print("\n" + tear_sheet)

    # Save Markdown Tear Sheet
    md_report = generate_markdown_tear_sheet(metrics, assumptions={"engine": "MT5 Strategy Tester", "spread": "broker/floating"})
    record_run(result, strategy_id="goldorb_orb", folder="strategies/GoldOrb", tear_sheet_md=md_report)
    print(f"[registry] recorded goldorb_orb -> results/goldorb_orb/")
    print(f"[PASS] MT5 Markdown tear sheet exported to {report_path}")


if __name__ == "__main__":
    test_mt5_headless_adapter_end_to_end()
