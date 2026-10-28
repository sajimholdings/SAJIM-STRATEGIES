"""
Test Multi-Strategy Portfolio & Correlation Engine
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sajim_quant.adapters.csv_importer import UniversalLogImporter
from sajim_quant.adapters.pine.tradingview_importer import TradingViewTradeImporter
from sajim_quant.analytics.portfolio import PortfolioEngine


def test_portfolio_analytics():
    print("Evaluating Multi-Strategy Portfolio and Correlation Matrix...")
    # Load 3 diverse benchmarked strategies
    btc_csv = (Path(__file__).resolve().parent.parent / "strategies" / "BTC data collector" / "BTCUSDT_15M_trades.csv").resolve()
    eur_csv = (Path(__file__).resolve().parent.parent / "strategies" / "BTC data collector" / "EURUSD_1H_trades.csv").resolve()
    deriv_log = (Path(__file__).resolve().parent.parent / "strategies" / "Deriv trading" / "trades_v3_fully_tuned.log").resolve()

    res_btc = UniversalLogImporter.import_btc_breakout_csv(btc_csv, strategy_name="BTC_Breakout_15M")
    res_eur = UniversalLogImporter.import_btc_breakout_csv(eur_csv, strategy_name="EUR_Breakout_1H")
    res_deriv = UniversalLogImporter.import_deriv_log(deriv_log, strategy_name="Deriv_Synthetics_V3")

    results = [res_btc, res_eur, res_deriv]

    port_result = PortfolioEngine.evaluate_portfolio(
        results=results,
        initial_balance=100000.0,
    )

    print(f"[PASS] Evaluated {len(port_result.strategies)} strategies.")
    print(f"Total Portfolio Net PnL: ${port_result.total_net_pnl:,.2f}")
    print(f"Portfolio Sharpe Ratio: {port_result.portfolio_sharpe:.2f}")
    print(f"Portfolio Max Drawdown: {port_result.portfolio_max_drawdown_pct:.2f}%")
    print(f"Diversification Ratio: {port_result.diversification_ratio:.2f}")
    print("\nPairwise Correlation Matrix:")
    print(json.dumps(port_result.correlation_matrix, indent=2))

    assert len(port_result.correlation_matrix) == len(port_result.strategies)
    print("\nPortfolio Analytics Test Passed Successfully!")


if __name__ == "__main__":
    test_portfolio_analytics()
