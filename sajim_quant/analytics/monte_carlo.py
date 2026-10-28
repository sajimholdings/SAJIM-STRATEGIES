"""
SAJIM Quant Platform — Monte Carlo Stress-Testing Engine
=========================================================
Runs path-independent bootstrap resampling and trade reshuffling across historical
trades to calculate probabilistic equity fan charts, worst-case drawdown bounds,
and risk-of-ruin metrics.
"""

from __future__ import annotations

import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class MonteCarloResult:
    num_simulations: int
    num_trades: int
    initial_balance: float
    # Equity fan curves sampled at regular trade steps (e.g. 20-50 points for charts)
    trade_steps: List[int]
    p5_curve: List[float]    # 5th percentile (Stress/Bear case - 95% VaR)
    p25_curve: List[float]   # 25th percentile (Conservative)
    p50_curve: List[float]   # 50th percentile (Median expected path)
    p75_curve: List[float]   # 75th percentile (Optimistic)
    p95_curve: List[float]   # 95th percentile (Bull scenario)
    
    # Stress metrics
    historical_max_dd_pct: float
    expected_max_dd_median_pct: float
    worst_drawdown_95_pct: float   # 95% confidence worst-case drawdown
    worst_drawdown_99_pct: float   # 99% confidence worst-case drawdown
    
    # Ruin probabilities
    risk_of_ruin_10_pct: float     # Probability of suffering >10% drawdown
    risk_of_ruin_20_pct: float     # Probability of suffering >20% drawdown
    risk_of_ruin_30_pct: float     # Probability of suffering >30% drawdown
    risk_of_ruin_50_pct: float     # Probability of suffering >50% drawdown
    
    # Terminal outcomes
    terminal_median_balance: float
    terminal_p5_balance: float
    terminal_p95_balance: float
    
    # Overfitting / Fragility score: Historical Sharpe vs MC Median Sharpe
    fragility_index: float
    is_robust: bool


class MonteCarloSimulator:
    """
    Executes vectorized Monte Carlo bootstrap resamplings on strategy trade return distributions.
    """

    @staticmethod
    def simulate(
        trade_pnls: List[float],
        initial_balance: float = 10000.0,
        historical_max_dd_pct: float = 0.0,
        historical_sharpe: float = 1.0,
        num_simulations: int = 1000,
        num_chart_points: int = 30,
        seed: Optional[int] = 42,
    ) -> MonteCarloResult:
        """
        Runs Monte Carlo simulation on a list of trade dollar PnLs.
        """
        rng = np.random.default_rng(seed)

        pnls = np.array(trade_pnls, dtype=np.float64)
        n_trades = len(pnls)

        if n_trades == 0:
            # Empty fallback
            steps = [0, 1]
            zeros = [initial_balance, initial_balance]
            return MonteCarloResult(
                num_simulations=num_simulations,
                num_trades=0,
                initial_balance=initial_balance,
                trade_steps=steps,
                p5_curve=zeros,
                p25_curve=zeros,
                p50_curve=zeros,
                p75_curve=zeros,
                p95_curve=zeros,
                historical_max_dd_pct=historical_max_dd_pct,
                expected_max_dd_median_pct=0.0,
                worst_drawdown_95_pct=0.0,
                worst_drawdown_99_pct=0.0,
                risk_of_ruin_10_pct=0.0,
                risk_of_ruin_20_pct=0.0,
                risk_of_ruin_30_pct=0.0,
                risk_of_ruin_50_pct=0.0,
                terminal_median_balance=initial_balance,
                terminal_p5_balance=initial_balance,
                terminal_p95_balance=initial_balance,
                fragility_index=1.0,
                is_robust=True,
            )

        # Draw random indices with replacement: shape (num_simulations, n_trades)
        # Bootstrap resampling captures both sequence variance and clustering
        sim_indices = rng.integers(0, n_trades, size=(num_simulations, n_trades))
        sim_pnls = pnls[sim_indices]  # shape: (num_simulations, n_trades)

        # Compute cumulative balance curves: shape (num_simulations, n_trades + 1)
        cum_pnls = np.cumsum(sim_pnls, axis=1)
        equity_paths = np.hstack([np.zeros((num_simulations, 1)), cum_pnls]) + initial_balance

        # Calculate Max Drawdown for each simulation path
        running_max = np.maximum.accumulate(equity_paths, axis=1)
        drawdowns = (running_max - equity_paths) / np.maximum(running_max, 1.0)
        sim_max_dds_pct = np.max(drawdowns, axis=1) * 100.0

        # Drawdown percentiles
        expected_max_dd_median = float(np.median(sim_max_dds_pct))
        worst_dd_95 = float(np.percentile(sim_max_dds_pct, 95))
        worst_dd_99 = float(np.percentile(sim_max_dds_pct, 99))

        # Risk of Ruin probabilities
        ror_10 = float(np.mean(sim_max_dds_pct >= 10.0) * 100.0)
        ror_20 = float(np.mean(sim_max_dds_pct >= 20.0) * 100.0)
        ror_30 = float(np.mean(sim_max_dds_pct >= 30.0) * 100.0)
        ror_50 = float(np.mean(sim_max_dds_pct >= 50.0) * 100.0)

        # Terminal balances
        terminal_balances = equity_paths[:, -1]
        term_median = float(np.median(terminal_balances))
        term_p5 = float(np.percentile(terminal_balances, 5))
        term_p95 = float(np.percentile(terminal_balances, 95))

        # Monte Carlo Sharpe distribution
        # Approximate annualized Sharpe across paths
        returns_pct = sim_pnls / initial_balance
        means = np.mean(returns_pct, axis=1)
        stds = np.std(returns_pct, axis=1) + 1e-9
        mc_sharpes = (means / stds) * np.sqrt(min(252, n_trades))
        mc_median_sharpe = float(np.median(mc_sharpes))

        # Fragility Index: ratio of Historical Sharpe to MC Median Sharpe
        # Ratio around 1.0 means historical outcome was representative and robust
        # Ratio > 2.0 indicates historical path had exceptional luck (curve-fit warning)
        if mc_median_sharpe > 0.1:
            fragility = round(max(0.5, historical_sharpe / mc_median_sharpe), 2)
        else:
            fragility = round(max(1.0, historical_sharpe * 2.0), 2) if historical_sharpe > 0 else 1.0

        is_robust = (fragility <= 1.75 and ror_20 <= 15.0)

        # Downsample curve steps for charts
        total_steps = n_trades + 1
        if total_steps <= num_chart_points:
            sample_indices = np.arange(total_steps)
        else:
            sample_indices = np.linspace(0, total_steps - 1, num_chart_points, dtype=int)

        sampled_paths = equity_paths[:, sample_indices]
        p5_curve = [round(float(x), 2) for x in np.percentile(sampled_paths, 5, axis=0)]
        p25_curve = [round(float(x), 2) for x in np.percentile(sampled_paths, 25, axis=0)]
        p50_curve = [round(float(x), 2) for x in np.percentile(sampled_paths, 50, axis=0)]
        p75_curve = [round(float(x), 2) for x in np.percentile(sampled_paths, 75, axis=0)]
        p95_curve = [round(float(x), 2) for x in np.percentile(sampled_paths, 95, axis=0)]
        steps_list = [int(i) for i in sample_indices]

        return MonteCarloResult(
            num_simulations=num_simulations,
            num_trades=n_trades,
            initial_balance=initial_balance,
            trade_steps=steps_list,
            p5_curve=p5_curve,
            p25_curve=p25_curve,
            p50_curve=p50_curve,
            p75_curve=p75_curve,
            p95_curve=p95_curve,
            historical_max_dd_pct=round(historical_max_dd_pct, 2),
            expected_max_dd_median_pct=round(expected_max_dd_median, 2),
            worst_drawdown_95_pct=round(worst_dd_95, 2),
            worst_drawdown_99_pct=round(worst_dd_99, 2),
            risk_of_ruin_10_pct=round(ror_10, 1),
            risk_of_ruin_20_pct=round(ror_20, 1),
            risk_of_ruin_30_pct=round(ror_30, 1),
            risk_of_ruin_50_pct=round(ror_50, 1),
            terminal_median_balance=round(term_median, 2),
            terminal_p5_balance=round(term_p5, 2),
            terminal_p95_balance=round(term_p95, 2),
            fragility_index=fragility,
            is_robust=is_robust,
        )


def test_mc():
    """Quick sanity check for Monte Carlo simulator."""
    # Synthetic 50 trades: 30 wins (+100), 20 losses (-80)
    trades = [100.0] * 30 + [-80.0] * 20
    res = MonteCarloSimulator.simulate(trades, initial_balance=10000.0, historical_max_dd_pct=2.5, historical_sharpe=1.8)
    return asdict(res)


if __name__ == "__main__":
    import pprint
    pprint.pprint(test_mc())
