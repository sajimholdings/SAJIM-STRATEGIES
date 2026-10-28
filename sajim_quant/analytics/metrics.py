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

#: Shortest run that may report a CAGR. Annualizing a 2-day sample produced
#: figures like 127,061,215% in shipped results, because a 40% gain over
#: 0.005 years compounds absurdly. Below this, CAGR and Calmar are None.
MIN_CAGR_DAYS = 90


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
    cagr_pct: Optional[float]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    # Ratios are None (not 999.0) when their denominator is zero. A sentinel
    # ranks a 3-trade strategy with no losses above every real one, so the
    # leaderboard sorts on it and shows nonsense; None renders as "n/a".
    profit_factor: Optional[float]
    expectancy: float
    avg_trade_pnl: float
    avg_win: float
    avg_loss: float
    win_loss_ratio: Optional[float]
    max_drawdown_amount: float
    max_drawdown_pct: float
    recovery_factor: Optional[float]
    sharpe_ratio: float
    sortino_ratio: Optional[float]
    calmar_ratio: Optional[float]
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_duration_minutes: float
    total_commission: float
    total_swap: float
    avg_mae: Optional[float] = None
    avg_mfe: Optional[float] = None
    #: Drawdown measured only at trade closes — always a floor on the real figure.
    max_drawdown_pct_closed: float = 0.0
    #: True when trades carried MAE, so max_drawdown_* includes intra-trade excursion.
    drawdown_includes_intratrade: bool = False
    #: Days spanned by the run; CAGR is suppressed below MIN_CAGR_DAYS.
    span_days: int = 0
    equity_series: List[Dict[str, Any]] = field(default_factory=list)
    monthly_returns: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        def r(v: Optional[float], nd: int = 2) -> Optional[float]:
            """Round, preserving None so 'not computable' survives serialization."""
            return None if v is None else round(v, nd)

        return {
            "strategy_name": self.strategy_name,
            "engine": self.engine,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "initial_balance": r(self.initial_balance),
            "final_balance": r(self.final_balance),
            "net_profit": r(self.net_profit),
            "return_pct": r(self.return_pct),
            "cagr_pct": r(self.cagr_pct),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": r(self.win_rate_pct),
            "profit_factor": r(self.profit_factor),
            "expectancy": r(self.expectancy),
            "avg_trade_pnl": r(self.avg_trade_pnl),
            "avg_win": r(self.avg_win),
            "avg_loss": r(self.avg_loss),
            "win_loss_ratio": r(self.win_loss_ratio),
            "max_drawdown_amount": r(self.max_drawdown_amount),
            "max_drawdown_pct": r(self.max_drawdown_pct),
            "recovery_factor": r(self.recovery_factor),
            "sharpe_ratio": r(self.sharpe_ratio),
            "sortino_ratio": r(self.sortino_ratio),
            "calmar_ratio": r(self.calmar_ratio),
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "avg_duration_minutes": r(self.avg_duration_minutes, 1),
            "total_commission": r(self.total_commission),
            "total_swap": r(self.total_swap),
            "avg_mae": r(self.avg_mae),
            "avg_mfe": r(self.avg_mfe),
            "max_drawdown_pct_closed": r(self.max_drawdown_pct_closed),
            "drawdown_includes_intratrade": self.drawdown_includes_intratrade,
            "span_days": self.span_days,
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
            cagr_pct=None,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            profit_factor=None,
            expectancy=0.0,
            avg_trade_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            win_loss_ratio=None,
            max_drawdown_amount=0.0,
            max_drawdown_pct=0.0,
            recovery_factor=None,
            sharpe_ratio=0.0,
            sortino_ratio=None,
            calmar_ratio=None,
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

    # None when there are no losses to divide by: 'no losing trades yet', not 'infinitely good'.
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    avg_trade_pnl = net_profit / total_trades if total_trades > 0 else 0.0
    avg_win = (sum(wins) / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (abs(sum(losses)) / losing_trades) if losing_trades > 0 else 0.0
    win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else None

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
    peak_closed = initial_balance
    max_dd_amount = 0.0
    max_dd_pct = 0.0
    equity_series = [{"time": meta.start_date, "equity": round(equity, 2), "drawdown_pct": 0.0}]

    # Closed-trade drawdown is tracked alongside the intra-trade-aware figure so
    # a consumer can see how much drawdown only shows up inside open trades.
    max_dd_pct_closed = 0.0
    has_mae = any(t.mae is not None for t in sorted_trades)

    def _record(level: float) -> None:
        """Fold one equity level into the running peak/drawdown extremes."""
        nonlocal peak, max_dd_amount, max_dd_pct
        if level > peak:
            peak = level
        amt = peak - level
        if amt > max_dd_amount:
            max_dd_amount = amt
        pct = (amt / peak) * 100 if peak > 0 else 0.0
        if pct > max_dd_pct:
            max_dd_pct = pct

    for t in sorted_trades:
        # Trough reached while the trade was open. The MAE sign convention varies
        # by producer (Backtrader emits it negative, the Pine importer positive),
        # so take the magnitude — an adverse excursion is a loss by definition.
        if t.mae is not None:
            _record(equity - abs(t.mae))

        equity += t.net_pnl
        _record(equity)

        if equity > peak_closed:
            peak_closed = equity
        dd_amt_closed = peak_closed - equity
        dd_pct_closed = (dd_amt_closed / peak_closed) * 100 if peak_closed > 0 else 0.0
        if dd_pct_closed > max_dd_pct_closed:
            max_dd_pct_closed = dd_pct_closed

        equity_series.append({
            "time": t.exit_time,
            "equity": round(equity, 2),
            "drawdown_pct": round(dd_pct_closed, 2),
            "trade_id": t.trade_id,
        })

    recovery_factor = (net_profit / max_dd_amount) if max_dd_amount > 0 else None

    # Time span & CAGR. The metadata dates come from the producer; CSV importers
    # set them to the first and last trade, so a short export can span two days.
    cagr_pct = None
    total_days = 0
    years = 1.0
    try:
        t_start = datetime.fromisoformat(meta.start_date.replace("Z", "+00:00"))
        t_end = datetime.fromisoformat(meta.end_date.replace("Z", "+00:00"))
        total_days = max(0, (t_end - t_start).days)
        years = max(total_days, 1) / 365.25
        if total_days >= MIN_CAGR_DAYS and final_balance > 0:
            cagr_pct = (((final_balance / initial_balance) ** (1 / years)) - 1) * 100
    except Exception:
        total_days = 0
        years = 1.0

    # Calmar inherits CAGR's gate: annualized return over drawdown is meaningless
    # when the return itself could not be annualized.
    calmar_ratio = (cagr_pct / max_dd_pct) if (cagr_pct is not None and max_dd_pct > 0) else None

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
            # No losing trades: downside deviation is zero, so Sortino is undefined.
            sortino_ratio = None
    else:
        sharpe_ratio = 0.0
        sortino_ratio = None

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
        max_drawdown_pct_closed=max_dd_pct_closed,
        drawdown_includes_intratrade=has_mae,
        span_days=total_days,
        equity_series=equity_series,
        monthly_returns=monthly_returns,
    )
