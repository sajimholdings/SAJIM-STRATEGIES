"""
Phase 2 Verification: Backtrader Strategy Adapter & Institutional Analytics Suite
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtrader as bt
from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
from sajim_quant.registry import record_run
from sajim_quant.analytics import calculate_metrics, generate_text_tear_sheet, generate_markdown_tear_sheet


class SmaCrossPullbackStrategy(bt.Strategy):
    """
    Reference Trend-Following / Pullback Strategy:
    - Fast SMA (20) & Slow SMA (50)
    - Buy when Fast crosses above Slow
    - Sell / Close when Fast crosses below Slow
    """
    params = (
        ("fast_period", 20),
        ("slow_period", 50),
    )

    def __init__(self):
        self.fast_sma = bt.indicators.SMA(self.data.close, period=self.params.fast_period)
        self.slow_sma = bt.indicators.SMA(self.data.close, period=self.params.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_sma, self.slow_sma)

    def notify_order(self, order):
        if order.status in [order.Completed]:
            pass
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            print(f"Order failed with status: {order.getstatusname()}")

    def next(self):
        if not self.position:
            if self.crossover > 0:
                self.buy(size=1.0)
            elif self.crossover < 0:
                self.sell(size=1.0)
        else:
            if self.position.size > 0 and self.crossover < 0:
                self.close()
            elif self.position.size < 0 and self.crossover > 0:
                self.close()


def test_python_adapter_and_analytics():
    print("Testing Backtrader Adapter on real XAUUSD Dukascopy data...")
    adapter = BacktraderAdapter(
        strategy_class=SmaCrossPullbackStrategy,
        strategy_name="XAUUSD_SMA_Pullback",
    )

    # Run backtest on 2024-2026 data
    result = adapter.run(
        symbol="XAUUSD",
        timeframe="H1",
        start_date="2024-09-01",
        end_date="2026-09-01",
        initial_balance=50000.0,
        commission_per_lot=2.0,
    )

    print(f"[PASS] Backtest execution completed! Harvested {result.total_trades} normalized trades.")
    assert result.total_trades > 0, "Should have executed at least 1 trade"

    sample_trade = result.trades[0]
    print(f"Sample Trade 0: ID={sample_trade.trade_id}, Dir={sample_trade.direction}, PnL=${sample_trade.net_pnl:.2f}, Dur={sample_trade.duration_seconds/3600:.1f}h")

    # Run Analytics Suite
    metrics = calculate_metrics(result)
    assert metrics.total_trades == result.total_trades
    print(f"[PASS] Analytics calculated successfully!")
    print(f"Net Profit: ${metrics.net_profit:,.2f} | Win Rate: {metrics.win_rate_pct:.1f}% | Profit Factor: {metrics.profit_factor:.2f}")
    print(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}% | Sharpe: {metrics.sharpe_ratio:.2f}")

    # Print ASCII Tear Sheet
    tear_sheet = generate_text_tear_sheet(metrics)
    print("\n" + tear_sheet)

    # Save Markdown Tear Sheet
    md_report = generate_markdown_tear_sheet(metrics, assumptions={"commission": "$2/lot", "spread": "historical/raw"})
    record_run(result, strategy_id="xauusd_sma_pullback_reference", folder="scripts/run_phase2_python_adapter.py", tear_sheet_md=md_report, description="Reference 20/50 SMA crossover used to validate the Backtrader adapter", tags=["reference"])
    print(f"[registry] recorded xauusd_sma_pullback_reference -> results/xauusd_sma_pullback_reference/")
    print(f"[PASS] Markdown tear sheet exported to {report_path}")


if __name__ == "__main__":
    test_python_adapter_and_analytics()
