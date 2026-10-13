"""
Test TradingView / Pine Script Trade Importer
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sajim_quant.adapters.pine.tradingview_importer import TradingViewTradeImporter
from sajim_quant.registry import record_run
from sajim_quant.analytics import calculate_metrics, generate_markdown_tear_sheet


def test_tradingview_importer():
    # Generate a representative sample TradingView trade list CSV
    import tempfile
    sample_csv_path = Path(tempfile.mkdtemp(prefix="sajim_pine_")) / "sample_tradingview_trades.csv"
    sample_content = """Trade #,Type,Signal,Date/Time,Price USD,Contracts,Profit USD,Profit %,Cum. Profit USD,Run-up USD,Run-up %,Drawdown USD,Drawdown %
1,Entry long,Buy Signal,2024-05-01 10:00,2300.50,1.0,,,0.00,,,
1,Exit long,Take Profit,2024-05-01 14:00,2320.50,1.0,2000.00,0.87%,2000.00,2200.00,0.96%,-150.00,-0.07%
2,Entry short,Sell Signal,2024-05-02 09:30,2315.00,1.0,,,2000.00,,,
2,Exit short,Stop Loss,2024-05-02 11:15,2325.00,1.0,-1000.00,-0.43%,1000.00,100.00,0.04%,-1050.00,-0.45%
3,Entry long,Buy Signal,2024-05-03 12:00,2310.00,1.0,,,1000.00,,,
3,Exit long,Take Profit,2024-05-03 16:30,2340.00,1.0,3000.00,1.30%,4000.00,3100.00,1.34%,-200.00,-0.09%
"""
    sample_csv_path.write_text(sample_content, encoding="utf-8")

    result = TradingViewTradeImporter.import_trades_csv(
        csv_path=sample_csv_path,
        strategy_name="Pine_LuxAlgo_SmartMoney",
        symbol="XAUUSD",
        timeframe="H1",
    )

    print(f"TradingView Importer: Harvested {result.total_trades} trades.")
    assert result.total_trades == 3, f"Expected 3 trades, got {result.total_trades}"

    trade1 = result.trades[0]
    assert trade1.trade_id == "PINE_1"
    assert trade1.net_pnl == 2000.00
    assert trade1.mae == 150.00
    assert trade1.mfe == 2200.00

    metrics = calculate_metrics(result)
    print(f"Pine Strategy: Net PnL=${metrics.net_profit:,.2f} | Win Rate={metrics.win_rate_pct:.1f}% | Profit Factor={metrics.profit_factor:.2f}")

    md_report = generate_markdown_tear_sheet(
        metrics,
        assumptions={"engine": "TradingView (Pine Script Export)", "symbol": "XAUUSD", "timeframe": "H1"}
    )
    record_run(result, strategy_id="pine_luxalgo_smartmoney", folder="scripts/run_pine_importer.py", tear_sheet_md=md_report, description="Sample TradingView List-of-Trades export (3 trades) proving the Pine importer", tags=["sample"])
    print(f"[registry] recorded pine_luxalgo_smartmoney -> results/pine_luxalgo_smartmoney/")


if __name__ == "__main__":
    test_tradingview_importer()
