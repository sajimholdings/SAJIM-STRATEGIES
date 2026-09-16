"""
SAJIM Quant - Sovereign Sniper GoldPullbackAI Replay Benchmark
Simulates the GoldPullbackAI strategy across historical Parquet H1 data for XAUUSD,
executes dynamic ATR entries/exits, and normalizes trades into the SAJIM schema.
"""

import sys
import logging
from pathlib import Path
from typing import List

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "XAUUSD_TRADING_ASISTENT_AI" / "sovereign_sniper_bot"))

from strategies.gold_pullback_ai import GoldPullbackAI
from sajim_quant.core.schema import (
    BacktestResult,
    Direction,
    EngineType,
    ExitReason,
    RunMetadata,
    Trade,
)
from sajim_quant.data.loader import DataLoader
from sajim_quant.analytics import calculate_metrics, generate_markdown_tear_sheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_gold_ai")


def run_gold_pullback_ai_backtest():
    symbol = "XAUUSD"
    logger.info(f"Loading Parquet H1 data for {symbol}...")
    loader = DataLoader()
    df = loader.load(symbol=symbol, timeframe="H1", start_date="2024-09-01", end_date="2025-03-01", set_index=False)

    strat = GoldPullbackAI()
    strat.initialize({"timeframe": "H1"})
    strat.market_structure = "NEUTRAL"

    trades: List[Trade] = []
    active_trade = None

    logger.info(f"Simulating GoldPullbackAI across {len(df)} H1 bars...")

    # Rolling window simulation
    lookback = 50
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i+1].copy().reset_index(drop=True)
        current_bar = window.iloc[-1]
        c_time = str(current_bar['time'])

        # Manage active trade
        if active_trade is not None:
            high = current_bar['high']
            low = current_bar['low']
            dir_buy = (active_trade['direction'] == Direction.BUY)
            closed = False
            exit_price = current_bar['close']
            exit_reason = ExitReason.MANUAL

            if dir_buy:
                if high >= active_trade['tp']:
                    closed = True
                    exit_price = active_trade['tp']
                    exit_reason = ExitReason.TAKE_PROFIT
                elif low <= active_trade['sl']:
                    closed = True
                    exit_price = active_trade['sl']
                    exit_reason = ExitReason.STOP_LOSS
            else:
                if low <= active_trade['tp']:
                    closed = True
                    exit_price = active_trade['tp']
                    exit_reason = ExitReason.TAKE_PROFIT
                elif high >= active_trade['sl']:
                    closed = True
                    exit_price = active_trade['sl']
                    exit_reason = ExitReason.STOP_LOSS

            if closed:
                mult = 1.0 if dir_buy else -1.0
                pnl = (exit_price - active_trade['entry_price']) * active_trade['size'] * 100.0 * mult
                trade = Trade(
                    trade_id=f"GOLD_AI_{len(trades)+1}",
                    strategy_name="Sovereign_Sniper_GoldPullbackAI",
                    engine="python:sovereign_sniper",
                    symbol=symbol,
                    timeframe="H1",
                    direction=active_trade['direction'],
                    entry_time=active_trade['entry_time'],
                    entry_price=active_trade['entry_price'],
                    position_size=active_trade['size'],
                    exit_time=c_time,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    gross_pnl=pnl,
                    net_pnl=pnl - 2.0,  # $2 comm
                )
                trades.append(trade)
                active_trade = None

        # Check entry if no active position
        if active_trade is None:
            strat.current_phase = "ARMED"
            facts = strat.analyze(symbol, window)
            pullback_count = facts.get("pullback_count", 0)
            structure = facts.get("market_structure", "NEUTRAL")
            slope = facts.get("ema_slope_degrees", 0.0)
            atr_val = facts.get("atr_value", 5.0)
            if isinstance(atr_val, pd.Series):
                atr_val = float(atr_val.iloc[-1]) if not atr_val.empty else 5.0
            if pd.isna(atr_val) or atr_val <= 0:
                atr_val = 5.0

            # Trigger condition: armed with pullback >= 1 and alignment with structure
            if pullback_count >= 1:
                entry_p = current_bar['close']
                sl_dist = max(3.0, min(15.0, 1.5 * atr_val))
                tp_dist = 1.5 * sl_dist

                if structure == "BULLISH" and slope >= -10:
                    active_trade = {
                        "direction": Direction.BUY,
                        "entry_price": entry_p,
                        "sl": entry_p - sl_dist,
                        "tp": entry_p + tp_dist,
                        "size": 1.0,
                        "entry_time": c_time,
                    }
                elif structure == "BEARISH" and slope <= 10:
                    active_trade = {
                        "direction": Direction.SELL,
                        "entry_price": entry_p,
                        "sl": entry_p + sl_dist,
                        "tp": entry_p - tp_dist,
                        "size": 1.0,
                        "entry_time": c_time,
                    }

    logger.info(f"Simulation finished! Harvested {len(trades)} normalized trades.")

    meta = RunMetadata(
        strategy_name="Sovereign_Sniper_GoldPullbackAI",
        engine="python:sovereign_sniper",
        symbol=symbol,
        timeframe="H1",
        start_date=str(df['time'].iloc[lookback]),
        end_date=str(df['time'].iloc[-1]),
        initial_balance=50000.0,
        commission_model="per_lot",
        slippage_pips=0.0,
        notes="Sovereign Sniper Gold Pullback AI Strategy",
    )
    result = BacktestResult(metadata=meta, trades=trades)

    metrics = calculate_metrics(result)
    logger.info(f"GoldPullbackAI: Net Profit=${metrics.net_profit:,.2f} | Win Rate={metrics.win_rate_pct:.1f}% | Profit Factor={metrics.profit_factor:.2f}")

    md_report = generate_markdown_tear_sheet(
        metrics,
        assumptions={
            "engine": "Sovereign Sniper AI Engine (Python)",
            "symbol": symbol,
            "timeframe": "H1",
            "period": "2024-09-01 to 2025-03-01",
        }
    )
    Path("tests/gold_pullback_ai_tear_sheet.md").write_text(md_report, encoding="utf-8")
    logger.info("Saved tear sheet to tests/gold_pullback_ai_tear_sheet.md")

    return result, metrics


if __name__ == "__main__":
    run_gold_pullback_ai_backtest()
