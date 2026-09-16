"""
SAJIM Quant - Base Strategy Adapter Interface

Defines the contract that all execution adapters (Python, MT5 Headless, Pine Importer) must satisfy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union
import pandas as pd

from sajim_quant.core.schema import BacktestResult


class BaseStrategyAdapter(ABC):
    """
    Abstract execution harness. Every engine adapter must return a schema-compliant BacktestResult.
    """

    def __init__(self, strategy_name: str, engine_name: str):
        self.strategy_name = strategy_name
        self.engine_name = engine_name

    @abstractmethod
    def run(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        initial_balance: float = 10000.0,
        **strategy_params: Any,
    ) -> BacktestResult:
        """
        Executes a backtest for the configured strategy and emits normalized trades.
        """
        pass
