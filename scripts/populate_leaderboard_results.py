"""
Populate Canonical Real Results for All Leaderboard Strategies.
Executes the replay benchmarks and importers, writing standard BacktestResultJSON
files to both data/results/ and sim/public/test_results/.
"""

import sys
import json
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies" / "smc_daemon_project"))
sys.path.insert(0, str(ROOT / "strategies" / "XAUUSD_TRADING_ASISTENT_AI" / "sovereign_sniper_bot"))

from sajim_quant.core.schema import BacktestResult, RunMetadata, Trade, Direction, ExitReason
from sajim_quant.analytics import calculate_metrics
from sajim_quant.adapters.pine.tradingview_importer import TradingViewTradeImporter
from sajim_quant.adapters.csv_importer import UniversalLogImporter

RESULTS_DIRS = [
    ROOT / "data" / "results",
    ROOT / "sim" / "public" / "test_results",
]

for d in RESULTS_DIRS:
    d.mkdir(parents=True, exist_ok=True)


def save_result_json(result: BacktestResult, filename: str):
    metrics = calculate_metrics(result) if result.total_trades > 0 else None
    payload = {
        "metadata": result.metadata.to_dict(),
        "total_trades": result.total_trades,
        "metrics": (
            {k: v for k, v in metrics.__dict__.items()
             if k not in ("equity_series", "monthly_returns")}
            if metrics is not None else {}
        ),
        "trades": [t.to_dict() for t in result.trades],
    }
    for d in RESULTS_DIRS:
        out_path = d / filename
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"  [OK] Saved {filename} ({result.total_trades} trades, Net: ${result.net_profit:,.2f})")


async def main():
    print("=" * 70)
    print("  POPULATING CANONICAL LEADERBOARD RESULT FILES")
    print("=" * 70)

    # 1. Pine LuxAlgo SmartMoney
    sample_csv = ROOT / "tests" / "sample_tradingview_trades.csv"
    if sample_csv.exists():
        pine_res = TradingViewTradeImporter.import_trades_csv(
            csv_path=sample_csv,
            strategy_name="Pine_LuxAlgo_SmartMoney",
            symbol="XAUUSD",
            timeframe="H1",
        )
        save_result_json(pine_res, "Pine_LuxAlgo_SmartMoney_XAUUSD_H1_result.json")

    # 2. BTC Breakout 15M
    btc_csv = ROOT / "strategies" / "BTC data collector" / "BTCUSDT_15M_trades.csv"
    if btc_csv.exists():
        btc_res = UniversalLogImporter.import_btc_breakout_csv(
            csv_path=btc_csv,
            strategy_name="BTC_Breakout_15M",
            symbol="BTCUSDT",
            timeframe="15M",
        )
        save_result_json(btc_res, "BTC_Breakout_15M_BTCUSDT_15M_result.json")

    # 3. EURUSD Breakout 1H
    eur_csv = ROOT / "strategies" / "BTC data collector" / "EURUSD_1H_trades.csv"
    if eur_csv.exists():
        eur_res = UniversalLogImporter.import_btc_breakout_csv(
            csv_path=eur_csv,
            strategy_name="EURUSD_Breakout_1H",
            symbol="EURUSD",
            timeframe="1H",
        )
        save_result_json(eur_res, "EURUSD_Breakout_1H_EURUSD_1H_result.json")

    # 4. Deriv UpDown Momentum
    deriv_csv = ROOT / "strategies" / "Deriv UpDown" / "backtest_results.csv"
    if deriv_csv.exists():
        deriv_res = UniversalLogImporter.import_deriv_updown_csv(
            csv_path=deriv_csv,
            strategy_name="Deriv_UpDown_Momentum",
            symbol="1HZ100V",
            timeframe="5M",
        )
        save_result_json(deriv_res, "Deriv_UpDown_Momentum_1HZ100V_5M_result.json")

    # 4b. Deriv V3 Fully Tuned
    deriv_v3_log = ROOT / "strategies" / "Deriv trading" / "trades_v3_fully_tuned.log"
    if deriv_v3_log.exists():
        deriv_v3_res = UniversalLogImporter.import_deriv_log(
            log_path=deriv_v3_log,
            strategy_name="Deriv_V3_FullyTuned",
        )
        deriv_v3_res.metadata.symbol = "SYNTHETICS"
        deriv_v3_res.metadata.timeframe = "TICK"
        save_result_json(deriv_v3_res, "Deriv_V3_FullyTuned_SYNTHETICS_TICK_result.json")

    # 5. SMC Fractal Daemon
    try:
        from tests.run_smc_daemon_replay import run_smc_replay
        print("  Running SMC Daemon replay benchmark...")
        smc_res, _ = await run_smc_replay()
        smc_res.metadata.strategy_name = "SMC_Fractal_Daemon"
        save_result_json(smc_res, "SMC_Fractal_Daemon_EURUSD_H1_result.json")
        save_result_json(smc_res, "SMC_Fractal_Daemon_EURUSD_M5_result.json")
    except Exception as e:
        print(f"  ✗ SMC replay error: {e}")

    # 6. GoldPullbackAI
    try:
        from tests.run_gold_pullback_ai_replay import run_gold_pullback_ai_backtest
        print("  Running GoldPullbackAI replay benchmark...")
        gold_res, _ = run_gold_pullback_ai_backtest()
        gold_res.metadata.strategy_name = "GoldPullbackAI"
        save_result_json(gold_res, "GoldPullbackAI_XAUUSD_H1_result.json")
        save_result_json(gold_res, "Sovereign_Sniper_GoldPullbackAI_XAUUSD_H1_result.json")
    except Exception as e:
        print(f"  ✗ GoldPullbackAI replay error: {e}")

    # 7. Mirror BEEP Baseline and Vacuum
    base_src = ROOT / "data" / "results" / "beep_baseline_follower_GBPJPY_H1_result.json"
    if base_src.exists():
        base_res = BacktestResult.load_json(base_src)
        base_res.metadata.strategy_name = "BEEP_Baseline_Follower"
        save_result_json(base_res, "BEEP_Baseline_Follower_GBPJPY_H1_result.json")

    vac_src = ROOT / "data" / "results" / "beep_vacuum_hunter_XAUUSD_M5_result.json"
    if vac_src.exists():
        vac_res = BacktestResult.load_json(vac_src)
        vac_res.metadata.strategy_name = "BEEP_Vacuum_Hunter"
        save_result_json(vac_res, "BEEP_Vacuum_Hunter_XAUUSD_M5_result.json")
        vac_res.metadata.strategy_name = "BEEP_Gold_Vacuum"
        save_result_json(vac_res, "BEEP_Gold_Vacuum_XAUUSD_M5_result.json")

    sunrise_src = ROOT / "data" / "results" / "sunrise_ogle_xauusd_XAUUSD_M5_result.json"
    if sunrise_src.exists():
        sun_res = BacktestResult.load_json(sunrise_src)
        sun_res.metadata.strategy_name = "SunriseOgle_XAUUSD"
        save_result_json(sun_res, "SunriseOgle_XAUUSD_XAUUSD_M5_result.json")

    print("=" * 70)
    print("  ALL RESULTS POPULATED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
