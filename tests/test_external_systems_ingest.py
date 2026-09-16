"""
Test ingestion and benchmarking of BTC data collector and Deriv trading bots.
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sajim_quant.adapters.csv_importer import UniversalLogImporter
from sajim_quant.analytics import calculate_metrics, generate_markdown_tear_sheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_ingest")


def test_btc_and_deriv_ingestion():
    # 1. BTC Breakout 15M
    btc_csv = Path("BTC data collector/BTCUSDT_15M_trades.csv").resolve()
    assert btc_csv.exists(), f"Missing {btc_csv}"

    logger.info("Ingesting BTCUSDT 15M Breakout trades...")
    btc_res = UniversalLogImporter.import_btc_breakout_csv(
        csv_path=btc_csv,
        strategy_name="BTC_Volatility_Breakout_15M",
    )
    logger.info(f"Imported {btc_res.total_trades} trades for BTCUSDT 15M.")
    btc_metrics = calculate_metrics(btc_res)
    logger.info(f"BTC 15M: Net Profit=${btc_metrics.net_profit:,.2f} | Win Rate={btc_metrics.win_rate_pct:.1f}% | Profit Factor={btc_metrics.profit_factor:.2f}")

    btc_report = generate_markdown_tear_sheet(
        btc_metrics,
        assumptions={
            "engine": "BTC Volatility Breakout (Python)",
            "symbol": "BTCUSDT",
            "timeframe": "15M",
            "source": "BTC data collector",
        }
    )
    Path("tests/btc_breakout_15m_tear_sheet.md").write_text(btc_report, encoding="utf-8")

    # 2. EURUSD 1H Breakout from BTC Collector
    eur_csv = Path("BTC data collector/EURUSD_1H_trades.csv").resolve()
    assert eur_csv.exists(), f"Missing {eur_csv}"

    logger.info("Ingesting EURUSD 1H Breakout trades...")
    eur_res = UniversalLogImporter.import_btc_breakout_csv(
        csv_path=eur_csv,
        strategy_name="EURUSD_Volatility_Breakout_1H",
    )
    logger.info(f"Imported {eur_res.total_trades} trades for EURUSD 1H.")
    eur_metrics = calculate_metrics(eur_res)
    logger.info(f"EURUSD 1H: Net Profit=${eur_metrics.net_profit:,.2f} | Win Rate={eur_metrics.win_rate_pct:.1f}% | Profit Factor={eur_metrics.profit_factor:.2f}")

    eur_report = generate_markdown_tear_sheet(
        eur_metrics,
        assumptions={
            "engine": "Forex Volatility Breakout (Python)",
            "symbol": "EURUSD",
            "timeframe": "1H",
            "source": "BTC data collector",
        }
    )
    Path("tests/eurusd_breakout_1h_tear_sheet.md").write_text(eur_report, encoding="utf-8")

    # 3. Deriv Trading Bot (trades_v3_fully_tuned.log)
    deriv_log = Path("Deriv trading/trades_v3_fully_tuned.log").resolve()
    assert deriv_log.exists(), f"Missing {deriv_log}"

    logger.info("Ingesting Deriv WebSocket Bot trades...")
    deriv_res = UniversalLogImporter.import_deriv_log(
        log_path=deriv_log,
        strategy_name="Deriv_V3_FullyTuned",
    )
    logger.info(f"Imported {deriv_res.total_trades} trades for Deriv V3.")
    deriv_metrics = calculate_metrics(deriv_res)
    logger.info(f"Deriv V3: Net Profit=${deriv_metrics.net_profit:,.2f} | Win Rate={deriv_metrics.win_rate_pct:.1f}% | Profit Factor={deriv_metrics.profit_factor:.2f}")

    deriv_report = generate_markdown_tear_sheet(
        deriv_metrics,
        assumptions={
            "engine": "Deriv WebSocket Bot (JavaScript/Node)",
            "symbol": "Synthetics Basket (1HZ10V, 1HZ50V, 1HZ75V)",
            "timeframe": "Tick / Real-time Execution",
            "source": "Deriv trading",
        }
    )
    Path("tests/deriv_v3_fully_tuned_tear_sheet.md").write_text(deriv_report, encoding="utf-8")

    return btc_res, eur_res, deriv_res


if __name__ == "__main__":
    test_btc_and_deriv_ingestion()
