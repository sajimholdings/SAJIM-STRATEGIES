"""
SAJIM Quant - Backtrader Adapter

Runs any Backtrader strategy on high-speed Parquet data and normalizes output trades
into the frozen SAJIM Universal Trade Schema contract.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type, Union

import backtrader as bt
import pandas as pd

from sajim_quant.adapters.base import BaseStrategyAdapter
from sajim_quant.core.schema import (
    BacktestResult,
    Direction,
    EngineType,
    ExitReason,
    RunMetadata,
    Trade,
)
from sajim_quant.data.loader import DataLoader

logger = logging.getLogger("sajim_quant.adapters.python.backtrader")


class SajimTradeHarvester(bt.Analyzer):
    """
    Backtrader Analyzer that intercepts closed trades and converts them
    into SAJIM Universal Trade records.
    """

    def __init__(self):
        super().__init__()
        self.trades: List[Trade] = []

    def notify_trade(self, trade):
        if not trade.isclosed:
            return

        try:
            # Parse entry and exit datetimes
            entry_dt = bt.num2date(trade.dtopen)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)

            exit_dt = bt.num2date(trade.dtclose)
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)

            # Determine direction from initial order
            direction = Direction.BUY if trade.history and trade.history[0].event.size > 0 else Direction.SELL

            # Determine exit price
            exit_price = trade.history[-1].event.price if trade.history else trade.price

            # Excursion metrics
            mae = getattr(trade, "pnl_min", None)
            mfe = getattr(trade, "pnl_max", None)

            t = Trade(
                trade_id=f"BT_{trade.ref}",
                strategy_name=getattr(self.strategy, "_sajim_name", "BacktraderStrategy"),
                engine=EngineType.PYTHON_BACKTRADER.value,
                symbol=getattr(self.strategy, "_sajim_symbol", "UNKNOWN"),
                timeframe=getattr(self.strategy, "_sajim_timeframe", "H1"),
                direction=direction,
                entry_time=entry_dt.isoformat(),
                entry_price=round(float(trade.price), 5),
                position_size=abs(float(trade.history[0].event.size)) if trade.history else 1.0,
                exit_time=exit_dt.isoformat(),
                exit_price=round(float(exit_price), 5),
                exit_reason=ExitReason.SIGNAL,
                gross_pnl=round(float(trade.pnl), 2),
                net_pnl=round(float(trade.pnlcomm), 2),
                commission=round(float(trade.commission), 2),
                mae=round(float(mae), 2) if mae is not None else None,
                mfe=round(float(mfe), 2) if mfe is not None else None,
            )
            self.trades.append(t)
        except Exception as e:
            logger.warning(f"Failed to harvest trade ref {trade.ref}: {e}")

    def get_analysis(self):
        return {"trades": self.trades}


class BacktraderAdapter(BaseStrategyAdapter):
    """
    Standardized execution harness for Backtrader strategies.
    """

    def __init__(
        self,
        strategy_class: Type[bt.Strategy],
        strategy_name: Optional[str] = None,
        data_loader: Optional[DataLoader] = None,
    ):
        name = strategy_name or strategy_class.__name__
        super().__init__(strategy_name=name, engine_name=EngineType.PYTHON_BACKTRADER.value)
        self.strategy_class = strategy_class
        self.loader = data_loader or DataLoader()

    def run(
        self,
        symbol: str,
        timeframe: str = "H1",
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        initial_balance: float = 10000.0,
        commission_per_lot: float = 0.0,
        slippage_pips: float = 0.0,
        **strategy_params: Any,
    ) -> BacktestResult:
        """
        Executes a full backtest against cached historical data.
        """
        # 1. Load Parquet data
        df = self.loader.load(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            set_index=True,
        )

        if df.empty:
            raise ValueError(f"No data returned for {symbol} on {timeframe}")

        # 2. Setup Cerebro
        cerebro = bt.Cerebro()
        cerebro.broker.setcash(initial_balance)

        # Configure commission and margin properly for CFD / FX
        cerebro.broker.setcommission(
            commission=commission_per_lot,
            commtype=bt.CommInfoBase.COMM_FIXED,
            leverage=30.0,
            mult=1.0,
        )

        # Ensure DataFrame has name attribute for strategies checking it
        df.name = f"{symbol}_{timeframe}.csv"

        # 3. Create Backtrader feed from Pandas
        feed = bt.feeds.PandasData(
            dataname=df,
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
            openinterest=-1,
        )
        cerebro.adddata(feed)

        # 4. Inject metadata attributes into strategy class
        strat_name = self.strategy_name
        strat_sym = symbol
        strat_tf = timeframe

        class InstrumentedStrategy(self.strategy_class):
            _sajim_name = strat_name
            _sajim_symbol = strat_sym
            _sajim_timeframe = strat_tf
            _data_filename = f"{strat_sym}_{strat_tf}.csv"

            def __init__(self, *args, **kwargs):
                self._data_filename = f"{strat_sym}_{strat_tf}.csv"
                super().__init__(*args, **kwargs)
                self._data_filename = f"{strat_sym}_{strat_tf}.csv"

        cerebro.addstrategy(
            InstrumentedStrategy,
            **strategy_params,
        )

        # 5. Add Harvester Analyzer
        cerebro.addanalyzer(SajimTradeHarvester, _name="sajim_harvester")

        # 6. Execute Run
        actual_start = str(df.index[0])
        actual_end = str(df.index[-1])

        results = cerebro.run()
        strat = results[0]
        harvester = strat.analyzers.sajim_harvester
        trades = harvester.get_analysis().get("trades", [])

        # 7. Package into RunMetadata and BacktestResult
        metadata = RunMetadata(
            strategy_name=self.strategy_name,
            engine=self.engine_name,
            symbol=symbol,
            timeframe=timeframe,
            start_date=actual_start,
            end_date=actual_end,
            initial_balance=initial_balance,
            commission_model="per_lot" if commission_per_lot > 0 else "zero",
            commission_rate=commission_per_lot,
            slippage_pips=slippage_pips,
            notes="Backtrader execution via SAJIM Python Adapter",
        )

        return BacktestResult(metadata=metadata, trades=trades)
