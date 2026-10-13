"""
SAJIM Quant - SMC Daemon Historical Replay Benchmark
Runs the SMC 5-Phase State Machine (AssetTrader) across historical Parquet data (H1 + M5),
intercepts completed trades, and generates a normalized SAJIM Tear Sheet.
"""

import sys
import asyncio
import logging
from pathlib import Path
from typing import List

import pandas as pd

# Add project root and smc_daemon_project to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "strategies" / "smc_daemon_project"))

from smc_daemon import AssetTrader
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
from sajim_quant.registry import record_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_smc_replay")


class SajimReplayTrader(AssetTrader):
    """
    Subclass of AssetTrader that records all filled trades and exits into SAJIM schema.
    """
    def __init__(self, symbol: str):
        super().__init__(symbol=symbol, config={}, is_mock=True)
        self.harvested_trades: List[Trade] = []
        self._current_trade_data = None

    async def execute_limit_order(self):
        await super().execute_limit_order()
        # Track entry order
        self._current_trade_data = {
            "symbol": self.symbol,
            "direction": Direction.BUY if self.directional_bias == "BUY" else Direction.SELL,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "position_size": 1.0,
            "entry_time": None,
        }

    async def _run_phase5_order_monitoring(self, df_5M):
        # Deterministic fill condition: check if latest candle touches entry_price
        c = df_5M.iloc[-1]
        filled = False
        if self.directional_bias == "BUY" and c['low'] <= self.entry_price:
            filled = True
        elif self.directional_bias == "SELL" and c['high'] >= self.entry_price:
            filled = True

        if filled:
            self.active_position_ticket = self.limit_order_ticket + 100
            self.state = self.STATE_POSITION_ACTIVE
            self.breakeven_triggered = False
            if self._current_trade_data:
                self._current_trade_data["entry_time"] = str(c['time'])
            logger.info(f"[{self.symbol}] Limit Order filled at {self.entry_price:.5f} on {c['time']}")
            return

        # Check timeout / invalidation
        await super()._run_phase5_order_monitoring(df_5M)

    async def _run_phase5_position_management(self, df_5M):
        c = df_5M.iloc[-1]
        close_trade = False
        exit_reason = ExitReason.MANUAL
        exit_price = c['close']

        if self.directional_bias == "BUY":
            if c['high'] >= self.tp_price:
                close_trade = True
                exit_price = self.tp_price
                exit_reason = ExitReason.TAKE_PROFIT
            elif c['low'] <= self.sl_price:
                close_trade = True
                exit_price = self.sl_price
                exit_reason = ExitReason.STOP_LOSS
            # 50% TP distance move to break-even
            elif not self.breakeven_triggered and c['high'] >= (self.entry_price + 0.5 * (self.tp_price - self.entry_price)):
                self.sl_price = self.entry_price
                self.breakeven_triggered = True
        else:
            if c['low'] <= self.tp_price:
                close_trade = True
                exit_price = self.tp_price
                exit_reason = ExitReason.TAKE_PROFIT
            elif c['high'] >= self.sl_price:
                close_trade = True
                exit_price = self.sl_price
                exit_reason = ExitReason.STOP_LOSS
            elif not self.breakeven_triggered and c['low'] <= (self.entry_price - 0.5 * (self.entry_price - self.tp_price)):
                self.sl_price = self.entry_price
                self.breakeven_triggered = True

        if close_trade and self._current_trade_data:
            entry_p = self._current_trade_data["entry_price"]
            dir_mult = 1.0 if self.directional_bias == "BUY" else -1.0
            # Calculate standard 1 lot PnL ($100k contract)
            pnl = (exit_price - entry_p) * 100000.0 * dir_mult
            
            entry_t = self._current_trade_data.get("entry_time") or str(c['time'])
            exit_t = str(c['time'])

            trade = Trade(
                trade_id=f"SMC_{len(self.harvested_trades)+1}",
                strategy_name="SMC_Fractal_Daemon",
                engine="python:smc_daemon",
                symbol=self.symbol,
                timeframe="H1_M5",
                direction=self._current_trade_data["direction"],
                entry_time=entry_t,
                entry_price=entry_p,
                position_size=1.0,
                exit_time=exit_t,
                exit_price=exit_price,
                exit_reason=exit_reason,
                gross_pnl=pnl,
                net_pnl=pnl - 4.0,  # $4 broker roundturn commission
            )
            self.harvested_trades.append(trade)
            logger.info(f"[{self.symbol}] Closed SMC Trade #{len(self.harvested_trades)}: {exit_reason.value} | PnL=${pnl:.2f} | Exit={exit_t}")
            self.reset_to_scanning()
            self._current_trade_data = None


async def run_smc_replay():
    symbol = "EURUSD"
    logger.info(f"Loading 2-year Parquet data for SMC Daemon Replay ({symbol})...")
    loader = DataLoader()
    df_h1 = loader.load(symbol=symbol, timeframe="H1", start_date="2024-09-01", end_date="2025-03-01", set_index=False)
    df_m5 = loader.load(symbol=symbol, timeframe="M5", start_date="2024-09-01", end_date="2025-03-01", set_index=False)

    # Add ATR to H1
    df_h1['atr'] = (df_h1['high'] - df_h1['low']).rolling(14).mean().fillna(0.0015)

    trader = SajimReplayTrader(symbol=symbol)
    logger.info(f"Simulating SMC state machine across {len(df_h1)} H1 candles and {len(df_m5)} M5 candles...")

    # Step through every 5M candle with available lookback
    step_interval = 24  # Step every 2 hours
    for i in range(100, len(df_m5), step_interval):
        current_time = df_m5['time'].iloc[i]
        
        # Slices
        h1_slice = df_h1[df_h1['time'] <= current_time].tail(50).copy().reset_index(drop=True)
        m5_slice = df_m5.iloc[max(0, i-200):i+1].copy().reset_index(drop=True)

        if len(h1_slice) < 30 or len(m5_slice) < 50:
            continue

        mock_dfs = {
            'df_1H': h1_slice,
            'df_5M': m5_slice,
        }
        await trader.process_tick(mock_dfs=mock_dfs)

    logger.info(f"Replay complete! Total trades harvested: {len(trader.harvested_trades)}")

    # Build BacktestResult
    meta = RunMetadata(
        strategy_name="SMC_Fractal_Daemon",
        engine="python:smc_daemon",
        symbol=symbol,
        timeframe="H1_M5",
        start_date=str(df_m5['time'].iloc[100]),
        end_date=str(df_m5['time'].iloc[-1]),
        initial_balance=50000.0,
        commission_model="per_lot",
        slippage_pips=0.0,
        notes="SMC 5-Phase State Machine Historical Replay",
    )
    result = BacktestResult(metadata=meta, trades=trader.harvested_trades)

    metrics = calculate_metrics(result)
    logger.info(f"SMC Daemon {symbol}: Net Profit=${metrics.net_profit:,.2f} | Win Rate={metrics.win_rate_pct:.1f}% | Profit Factor={metrics.profit_factor:.2f}")

    # Generate Markdown Tear Sheet
    md_report = generate_markdown_tear_sheet(
        metrics,
        assumptions={
            "engine": "SMC Forex Trading Daemon (Python Async State Machine)",
            "symbol": symbol,
            "timeframe": "HTF: 1H, LTF: 5M",
            "period": "2024-09-01 to 2025-03-01",
        }
    )
    record_run(result, strategy_id="smc_fractal_daemon", folder="strategies/smc_daemon_project", tear_sheet_md=md_report)
    print(f"[registry] recorded smc_fractal_daemon -> results/smc_fractal_daemon/")

    return result, metrics


if __name__ == "__main__":
    asyncio.run(run_smc_replay())
