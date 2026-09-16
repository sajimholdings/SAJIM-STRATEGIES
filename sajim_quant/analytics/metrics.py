"""
SAJIM Quant - Institutional Analytics Engine

Calculates comprehensive risk-adjusted performance metrics, drawdown curves,
and trade profiles operating strictly on the normalized Trade schema contract.
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
class PerformanceMetrics:
    """
    Standardized quantitative metrics container.
    """
    strategy_name: str
    engine: str
    symbol: str
    timeframe: str
    initial_balance: float
    final_balance: float
    net_profit: float
    return_pct: float
    cagr_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    avg_trade_pnl: float
    avg_win: float
    avg_loss: float
    win_loss_ratio: float
    max_drawdown_amount: float
    max_drawdown_pct: float
    recovery_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_duration_minutes: float
    total_commission: float
    total_swap: float
    avg_mae: Optional[float] = None
    avg_mfe: Optional[float] = None
    equity_series: List[Dict[str, Any]] = field(default_factory=list)
    monthly_returns: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "engine": self.engine,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "initial_balance": round(self.initial_balance, 2),
            "final_balance": round(self.final_balance, 2),
            "net_profit": round(self.net_profit, 2),
            "return_pct": round(self.return_pct, 2),
            "cagr_pct": round(self.cagr_pct, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy": round(self.expectancy, 2),
            "avg_trade_pnl": round(self.avg_trade_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "win_loss_ratio": round(self.win_loss_ratio, 2),
            "max_drawdown_amount": round(self.max_drawdown_amount, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "recovery_factor": round(self.recovery_factor, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "calmar_ratio": round(self.calmar_ratio, 2),
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "avg_duration_minutes": round(self.avg_duration_minutes, 1),
            "total_commission": round(self.total_commission, 2),
            "total_swap": round(self.total_swap, 2),
            "avg_mae": round(self.avg_mae, 2) if self.avg_mae is not None else None,
            "avg_mfe": round(self.avg_mfe, 2) if self.avg_mfe is not None else None,
        }


def calculate_metrics(result: BacktestResult) -> PerformanceMetrics:
    """
    Computes institutional performance metrics from a BacktestResult.
    """
    meta = result.metadata
    trades = result.trades
    initial_balance = meta.initial_balance if meta.initial_balance > 0 else 10000.0

    if not trades:
        return PerformanceMetrics(
            strategy_name=meta.strategy_name,
            engine=meta.engine,
            symbol=meta.symbol,
            timeframe=meta.timeframe,
            initial_balance=initial_balance,
            final_balance=initial_balance,
            net_profit=0.0,
            return_pct=0.0,
            cagr_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            expectancy=0.0,
            avg_trade_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            win_loss_ratio=0.0,
            max_drawdown_amount=0.0,
            max_drawdown_pct=0.0,
            recovery_factor=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            avg_duration_minutes=0.0,
            total_commission=0.0,
            total_swap=0.0,
        )

    # Sort trades by exit time
    sorted_trades = sorted(trades, key=lambda t: t.exit_time if t.exit_time else t.entry_time)

    pnl_list = [t.net_pnl for t in sorted_trades]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]

    total_trades = len(sorted_trades)
    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate_pct = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_profit = sum(pnl_list)
    final_balance = initial_balance + net_profit
    return_pct = (net_profit / initial_balance) * 100

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    avg_trade_pnl = net_profit / total_trades if total_trades > 0 else 0.0
    avg_win = (sum(wins) / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (abs(sum(losses)) / losing_trades) if losing_trades > 0 else 0.0
    win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)

    loss_rate = losing_trades / total_trades if total_trades > 0 else 0.0
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    # Consecutive wins / losses
    max_cons_wins = 0
    max_cons_losses = 0
    curr_wins = 0
    curr_losses = 0
    for p in pnl_list:
        if p > 0:
            curr_wins += 1
            curr_losses = 0
            if curr_wins > max_cons_wins:
                max_cons_wins = curr_wins
        elif p < 0:
            curr_losses += 1
            curr_wins = 0
            if curr_losses > max_cons_losses:
                max_cons_losses = curr_losses
        else:
            curr_wins = 0
            curr_losses = 0

    # Equity Curve & Drawdown calculation
    equity = initial_balance
    peak = initial_balance
    max_dd_amount = 0.0
    max_dd_pct = 0.0
    equity_series = [{"time": meta.start_date, "equity": round(equity, 2), "drawdown_pct": 0.0}]

    for t in sorted_trades:
        equity += t.net_pnl
        if equity > peak:
            peak = equity
        dd_amt = peak - equity
        dd_pct = (dd_amt / peak) * 100 if peak > 0 else 0.0

        if dd_amt > max_dd_amount:
            max_dd_amount = dd_amt
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        equity_series.append({
            "time": t.exit_time,
            "equity": round(equity, 2),
            "drawdown_pct": round(dd_pct, 2),
            "trade_id": t.trade_id,
        })

    recovery_factor = (net_profit / max_dd_amount) if max_dd_amount > 0 else (999.0 if net_profit > 0 else 0.0)

    # Time span & CAGR
    cagr_pct = 0.0
    try:
        t_start = datetime.fromisoformat(meta.start_date.replace("Z", "+00:00"))
        t_end = datetime.fromisoformat(meta.end_date.replace("Z", "+00:00"))
        total_days = max(1, (t_end - t_start).days)
        years = total_days / 365.25
        if years > 0 and final_balance > 0:
            cagr_pct = (((final_balance / initial_balance) ** (1 / years)) - 1) * 100
    except Exception:
        years = 1.0

    calmar_ratio = (cagr_pct / max_dd_pct) if max_dd_pct > 0 else (999.0 if cagr_pct > 0 else 0.0)

    # Sharpe & Sortino (trade-level annualized assuming ~252 trading days / avg trade freq)
    if len(pnl_list) > 1:
        pnl_arr = np.array(pnl_list)
        std_pnl = np.std(pnl_arr, ddof=1)
        # Annualization factor estimate based on trades per year
        trades_per_year = max(1.0, total_trades / max(0.1, years))
        ann_factor = math.sqrt(trades_per_year)

        sharpe_ratio = (np.mean(pnl_arr) / std_pnl * ann_factor) if std_pnl > 0 else 0.0

        # Sortino downside deviation
        downside = pnl_arr[pnl_arr < 0]
        if len(downside) > 0:
            downside_std = math.sqrt(np.mean(downside ** 2))
            sortino_ratio = (np.mean(pnl_arr) / downside_std * ann_factor) if downside_std > 0 else 0.0
        else:
            sortino_ratio = 999.0 if np.mean(pnl_arr) > 0 else 0.0
    else:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0

    # Trade Profile
    total_comm = sum(t.commission for t in sorted_trades)
    total_swap = sum(t.swap_cost for t in sorted_trades)
    durations = [t.duration_seconds for t in sorted_trades if t.duration_seconds is not None]
    avg_duration_min = (sum(durations) / len(durations) / 60.0) if durations else 0.0

    maes = [t.mae for t in sorted_trades if t.mae is not None]
    mfes = [t.mfe for t in sorted_trades if t.mfe is not None]
    avg_mae = (sum(maes) / len(maes)) if maes else None
    avg_mfe = (sum(mfes) / len(mfes)) if mfes else None

    # Monthly breakdown
    monthly_returns: Dict[str, Dict[str, float]] = {}
    for t in sorted_trades:
        try:
            dt = datetime.fromisoformat(t.exit_time.replace("Z", "+00:00"))
            yr = str(dt.year)
            mo = dt.strftime("%b")
            if yr not in monthly_returns:
                monthly_returns[yr] = {}
            monthly_returns[yr][mo] = monthly_returns[yr].get(mo, 0.0) + t.net_pnl
        except Exception:
            pass

    return PerformanceMetrics(
        strategy_name=meta.strategy_name,
        engine=meta.engine,
        symbol=meta.symbol,
        timeframe=meta.timeframe,
        initial_balance=initial_balance,
        final_balance=final_balance,
        net_profit=net_profit,
        return_pct=return_pct,
        cagr_pct=cagr_pct,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        expectancy=expectancy,
        avg_trade_pnl=avg_trade_pnl,
        avg_win=avg_win,
        avg_loss=avg_loss,
        win_loss_ratio=win_loss_ratio,
        max_drawdown_amount=max_dd_amount,
        max_drawdown_pct=max_dd_pct,
        recovery_factor=recovery_factor,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        max_consecutive_wins=max_cons_wins,
        max_consecutive_losses=max_cons_losses,
        avg_duration_minutes=avg_duration_min,
        total_commission=total_comm,
        total_swap=total_swap,
        avg_mae=avg_mae,
        avg_mfe=avg_mfe,
        equity_series=equity_series,
        monthly_returns=monthly_returns,
    )
