"""
SAJIM Quant - Multi-Strategy Portfolio & Correlation Engine
Aligns normalized trade streams, computes cross-strategy correlation matrices,
and generates combined portfolio allocations (Equal Weight, Risk Parity).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from sajim_quant.core.schema import BacktestResult, Trade


@dataclass
class PortfolioAnalyticsResult:
    """
    Combined portfolio metrics and cross-strategy matrix.
    """
    strategies: List[str]
    correlation_matrix: Dict[str, Dict[str, float]]
    weights: Dict[str, float]
    total_net_pnl: float
    portfolio_sharpe: float
    portfolio_max_drawdown_pct: float
    diversification_ratio: float
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategies": self.strategies,
            "correlation_matrix": self.correlation_matrix,
            "weights": self.weights,
            "total_net_pnl": round(self.total_net_pnl, 2),
            "portfolio_sharpe": round(self.portfolio_sharpe, 2),
            "portfolio_max_drawdown_pct": round(self.portfolio_max_drawdown_pct, 2),
            "diversification_ratio": round(self.diversification_ratio, 2),
            "equity_curve": self.equity_curve,
        }


class PortfolioEngine:
    """
    Analyzes multiple strategy results collectively.
    """

    @staticmethod
    def build_daily_pnl_matrix(results: List[BacktestResult]) -> pd.DataFrame:
        """
        Builds a daily PnL dataframe indexed by date with columns = strategy names.
        """
        all_series = {}

        for res in results:
            name = res.metadata.strategy_name
            records = []
            for t in res.trades:
                # Use exit_time for closed PnL realization date
                date_str = str(t.exit_time)[:10]
                records.append({"date": date_str, "pnl": t.net_pnl})

            if records:
                df_t = pd.DataFrame(records)
                daily = df_t.groupby("date")["pnl"].sum()
                all_series[name] = daily
            else:
                all_series[name] = pd.Series(dtype=float)

        pnl_matrix = pd.DataFrame(all_series).fillna(0.0)
        pnl_matrix.index = pd.to_datetime(pnl_matrix.index)
        pnl_matrix = pnl_matrix.sort_index()
        return pnl_matrix

    @staticmethod
    def calculate_correlation_matrix(pnl_matrix: pd.DataFrame) -> Dict[str, Dict[str, float]]:
        """
        Computes pairwise correlation between daily PnL series.
        """
        corr = pnl_matrix.corr()
        out = {}
        for col1 in corr.columns:
            out[col1] = {}
            for col2 in corr.columns:
                val = corr.loc[col1, col2]
                out[col1][col2] = 0.0 if pd.isna(val) else round(float(val), 3)
        return out

    @staticmethod
    def evaluate_portfolio(
        results: List[BacktestResult],
        initial_balance: float = 100000.0,
        weights: Optional[Dict[str, float]] = None,
    ) -> PortfolioAnalyticsResult:
        """
        Constructs combined portfolio performance metrics and correlation matrix.
        """
        strat_names = [r.metadata.strategy_name for r in results if r.total_trades > 0]
        active_results = [r for r in results if r.total_trades > 0]

        if not active_results:
            return PortfolioAnalyticsResult(
                strategies=[],
                correlation_matrix={},
                weights={},
                total_net_pnl=0.0,
                portfolio_sharpe=0.0,
                portfolio_max_drawdown_pct=0.0,
                diversification_ratio=1.0,
            )

        pnl_matrix = PortfolioEngine.build_daily_pnl_matrix(active_results)
        corr_dict = PortfolioEngine.calculate_correlation_matrix(pnl_matrix)

        # Equal weighting by default
        n = len(active_results)
        if weights is None:
            w = {name: 1.0 / n for name in strat_names}
        else:
            w = weights

        # Weighted daily PnL
        weighted_pnl = pd.Series(0.0, index=pnl_matrix.index)
        for name in strat_names:
            if name in pnl_matrix.columns:
                weighted_pnl += pnl_matrix[name] * (w.get(name, 1.0 / n) * n)

        # Equity curve
        equity_series = initial_balance + weighted_pnl.cumsum()
        total_pnl = float(weighted_pnl.sum())

        # Portfolio Sharpe (annualized from daily)
        mean_d = float(weighted_pnl.mean())
        std_d = float(weighted_pnl.std()) if float(weighted_pnl.std()) > 0 else 1.0
        portfolio_sharpe = (mean_d / std_d) * math.sqrt(252)

        # Portfolio Max Drawdown
        running_max = equity_series.cummax()
        dd_series = (equity_series - running_max) / running_max
        max_dd_pct = abs(float(dd_series.min())) * 100.0 if not dd_series.empty else 0.0

        # Diversification Ratio (Weighted sum of vol / portfolio vol)
        strat_vols = [float(pnl_matrix[col].std()) for col in strat_names]
        port_vol = float(weighted_pnl.std()) if float(weighted_pnl.std()) > 0 else 1.0
        weighted_vol_sum = sum(vol * w.get(name, 1.0/n) for vol, name in zip(strat_vols, strat_names))
        div_ratio = (weighted_vol_sum / port_vol) if port_vol > 0 else 1.0

        # Build equity curve point list for charts
        curve = []
        for dt, eq in equity_series.items():
            curve.append({
                "date": dt.strftime("%Y-%m-%d"),
                "equity": round(float(eq), 2),
                "daily_pnl": round(float(weighted_pnl.loc[dt]), 2),
            })

        return PortfolioAnalyticsResult(
            strategies=strat_names,
            correlation_matrix=corr_dict,
            weights=w,
            total_net_pnl=total_pnl,
            portfolio_sharpe=portfolio_sharpe,
            portfolio_max_drawdown_pct=max_dd_pct,
            diversification_ratio=div_ratio,
            equity_curve=curve,
        )
