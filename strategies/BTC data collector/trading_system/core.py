"""
core.py -- Shared infrastructure for the V2 Trading Backtesting System.

Contains:
  - Data structures (Candle, Trade)
  - All indicator calculations (ATR, EMA, VWMA, SMA, StdDev, Z-Score)
  - Statistics computation
  - Terminal report formatting + verdict system
  - CSV export helpers
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from math import sqrt, isfinite
from typing import Optional

import numpy as np


# -----------------------------------------------------------------------------
# DATA STRUCTURES
# -----------------------------------------------------------------------------

@dataclass
class Candle:
    """A single OHLCV candle."""
    epoch: int
    dt: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Trade:
    """A completed (or still-open) trade record."""
    entry_time: str
    exit_time: str
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    size_usd: float
    pnl_usd: float
    pnl_r: float            # P&L in R-multiples
    exit_reason: str        # "TP", "SL", "TIMEOUT", "TRAIL"
    bars_held: int
    signal_value: float     # Z-score at entry (fade) or ATR-body multiple (momentum)


@dataclass
class BacktestResult:
    """Full statistics for a single backtest run."""
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)  # balance per candle
    equity_times: list[str] = field(default_factory=list)

    # Computed stats (populated by compute_stats)
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    max_consec_wins: int = 0
    max_consec_losses: int = 0
    trades_per_month: float = 0.0
    monthly_returns: dict[str, float] = field(default_factory=dict)
    positive_months: int = 0
    total_months: int = 0
    avg_bars_held: float = 0.0
    long_count: int = 0
    short_count: int = 0
    tp_count: int = 0
    sl_count: int = 0
    timeout_count: int = 0
    trail_count: int = 0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    total_return_pct: float = 0.0
    final_balance: float = 0.0
    starting_balance: float = 0.0


# -----------------------------------------------------------------------------
# DATA LOADING
# -----------------------------------------------------------------------------

def load_candles(filepath: str) -> list[Candle]:
    """
    Load candle data from a CSV file.

    Expected columns: epoch, datetime_utc, open, high, low, close, volume, ...
    Returns a list of Candle objects sorted by epoch ascending.
    """
    candles: list[Candle] = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                c = Candle(
                    epoch=int(row["epoch"]),
                    dt=row["datetime_utc"].strip(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0) or 0),
                )
                candles.append(c)
            except (KeyError, ValueError):
                continue  # skip malformed rows
    candles.sort(key=lambda c: c.epoch)
    return candles


# -----------------------------------------------------------------------------
# INDICATOR CALCULATIONS  (numpy-vectorized for sweep performance)
# -----------------------------------------------------------------------------

def calc_atr(candles: list[Candle], period: int) -> list[float]:
    """
    Average True Range using Wilder's smoothing.
    Returns a list aligned to candles (0.0 for warmup bars).
    """
    n = len(candles)
    if n < 2:
        return [0.0] * n

    highs  = np.array([c.high  for c in candles], dtype=np.float64)
    lows   = np.array([c.low   for c in candles], dtype=np.float64)
    closes = np.array([c.close for c in candles], dtype=np.float64)

    prev_close = np.empty(n, dtype=np.float64)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]

    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close))
    )

    atr = np.zeros(n, dtype=np.float64)
    if n < period:
        return atr.tolist()

    atr[period - 1] = tr[:period].mean()
    k = 1.0 / period
    for i in range(period, n):
        atr[i] = atr[i - 1] * (1.0 - k) + tr[i] * k

    return atr.tolist()


def calc_ema(values: list[float], period: int) -> list[float]:
    """
    Exponential Moving Average. Returns 0.0 for warmup bars.
    """
    arr = np.array(values, dtype=np.float64)
    n   = len(arr)
    ema = np.zeros(n, dtype=np.float64)
    if n < period:
        return ema.tolist()

    ema[period - 1] = arr[:period].mean()
    k = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = arr[i] * k + ema[i - 1] * (1.0 - k)
    return ema.tolist()


def calc_vwma(candles: list[Candle], period: int) -> list[float]:
    """
    Volume Weighted Moving Average using cumulative-sum trick. O(n).
    Returns 0.0 for warmup bars.
    """
    n      = len(candles)
    closes = np.array([c.close  for c in candles], dtype=np.float64)
    vols   = np.array([c.volume for c in candles], dtype=np.float64)

    pv     = closes * vols
    cum_pv = np.cumsum(pv)
    cum_v  = np.cumsum(vols)

    # Prepend 0 for easy windowed subtraction
    cum_pv = np.concatenate([[0.0], cum_pv])
    cum_v  = np.concatenate([[0.0], cum_v])

    vwma = np.zeros(n, dtype=np.float64)
    for i in range(period - 1, n):
        w_pv = cum_pv[i + 1] - cum_pv[i + 1 - period]
        w_v  = cum_v[i + 1]  - cum_v[i + 1 - period]
        vwma[i] = w_pv / w_v if w_v > 0 else 0.0
    return vwma.tolist()


def calc_sma(values: list[float], period: int) -> list[float]:
    """
    Simple Moving Average using cumulative-sum trick. O(n).
    Returns 0.0 for warmup bars.
    """
    arr    = np.array(values, dtype=np.float64)
    n      = len(arr)
    cum    = np.concatenate([[0.0], np.cumsum(arr)])
    sma    = np.zeros(n, dtype=np.float64)
    sma[period - 1:] = (cum[period:] - cum[:n - period + 1]) / period
    return sma.tolist()


def calc_rolling_std(values: list[float], period: int) -> list[float]:
    """
    Rolling standard deviation (ddof=1) using the two-pass cumulative-sum
    method. O(n), no nested loops.
    Returns 0.0 for warmup bars.
    """
    arr   = np.array(values, dtype=np.float64)
    n     = len(arr)
    cum   = np.concatenate([[0.0], np.cumsum(arr)])
    cum2  = np.concatenate([[0.0], np.cumsum(arr ** 2)])
    std   = np.zeros(n, dtype=np.float64)

    if n < period:
        return std.tolist()

    for i in range(period - 1, n):
        s   = cum[i + 1]  - cum[i + 1 - period]
        s2  = cum2[i + 1] - cum2[i + 1 - period]
        mean    = s / period
        var     = (s2 - period * mean ** 2) / (period - 1)
        std[i]  = float(np.sqrt(var)) if var > 1e-14 else 0.0

    return std.tolist()


def calc_zscore(
    candles: list[Candle],
    mean_values: list[float],
    zscore_period: int,
) -> list[float]:
    """
    Z-score of (close - mean) using a rolling standard deviation.
    z = (close - mean) / rolling_std(close - mean, zscore_period)
    Returns 0.0 for warmup bars.
    """
    n          = len(candles)
    closes     = np.array([c.close for c in candles], dtype=np.float64)
    means      = np.array(mean_values, dtype=np.float64)
    deviations = (closes - means).tolist()
    rolling_std = calc_rolling_std(deviations, zscore_period)
    zscore = [0.0] * n
    for i in range(n):
        if rolling_std[i] > 0:
            zscore[i] = deviations[i] / rolling_std[i]
    return zscore


# -----------------------------------------------------------------------------
# STATISTICS
# -----------------------------------------------------------------------------

def compute_stats(result: BacktestResult, starting_balance: float) -> None:
    """
    Populate all statistical fields of a BacktestResult in place.
    Requires result.trades and result.equity_curve to be already filled.
    """
    trades = result.trades
    result.starting_balance = starting_balance
    result.final_balance = result.equity_curve[-1] if result.equity_curve else starting_balance
    result.total_return_pct = (result.final_balance / starting_balance - 1.0) * 100.0
    result.total_trades = len(trades)

    if not trades:
        return

    # Win / Loss
    winners = [t for t in trades if t.pnl_r > 0]
    losers  = [t for t in trades if t.pnl_r <= 0]
    result.win_rate = len(winners) / len(trades) if trades else 0.0
    result.avg_win_r  = sum(t.pnl_r for t in winners) / len(winners) if winners else 0.0
    result.avg_loss_r = abs(sum(t.pnl_r for t in losers) / len(losers)) if losers else 0.0

    # Profit factor
    gross_profit = sum(t.pnl_usd for t in winners) if winners else 0.0
    gross_loss   = abs(sum(t.pnl_usd for t in losers)) if losers else 0.0
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Expectancy
    wr = result.win_rate
    lr = 1.0 - wr
    result.expectancy_r = (wr * result.avg_win_r) - (lr * result.avg_loss_r)

    # Drawdown from equity curve
    peak = starting_balance
    max_dd_usd = 0.0
    for bal in result.equity_curve:
        if bal > peak:
            peak = bal
        dd = peak - bal
        if dd > max_dd_usd:
            max_dd_usd = dd
    result.max_drawdown_usd = max_dd_usd
    result.max_drawdown_pct = (max_dd_usd / starting_balance) * 100.0 if starting_balance > 0 else 0.0

    # Consecutive wins/losses
    max_w = max_l = cur_w = cur_l = 0
    for t in trades:
        if t.pnl_r > 0:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    result.max_consec_wins   = max_w
    result.max_consec_losses = max_l

    # Average bars held
    result.avg_bars_held = sum(t.bars_held for t in trades) / len(trades)

    # Directional split
    result.long_count  = sum(1 for t in trades if t.direction == "LONG")
    result.short_count = sum(1 for t in trades if t.direction == "SHORT")

    # Exit reason split
    result.tp_count      = sum(1 for t in trades if t.exit_reason == "TP")
    result.sl_count      = sum(1 for t in trades if t.exit_reason == "SL")
    result.timeout_count = sum(1 for t in trades if t.exit_reason == "TIMEOUT")
    result.trail_count   = sum(1 for t in trades if t.exit_reason == "TRAIL")

    # Monthly returns
    monthly_start: dict[str, float] = {}
    monthly_end: dict[str, float]   = {}
    for t in trades:
        month_key = t.entry_time[:7]  # "YYYY-MM"
        if month_key not in monthly_start:
            # Find balance just before this trade opened -- approximate via equity curve
            monthly_start[month_key] = starting_balance  # safe fallback
        monthly_end[month_key] = monthly_end.get(month_key, starting_balance) + t.pnl_usd

    # Better approach: derive monthly returns from the equity curve directly
    result.monthly_returns = _monthly_returns_from_curve(
        result.equity_times, result.equity_curve, starting_balance
    )
    result.total_months    = len(result.monthly_returns)
    result.positive_months = sum(1 for v in result.monthly_returns.values() if v > 0)

    # Trades per month
    if result.total_months > 0:
        result.trades_per_month = result.total_trades / result.total_months
    else:
        result.trades_per_month = result.total_trades


def _monthly_returns_from_curve(
    times: list[str], curve: list[float], starting_balance: float
) -> dict[str, float]:
    """Derive month-by-month % return from the equity curve."""
    if not times or not curve:
        return {}
    monthly: dict[str, tuple[float, float]] = {}  # month -> (open_bal, close_bal)
    for i, (t, bal) in enumerate(zip(times, curve)):
        month = t[:7]
        if month not in monthly:
            # Use balance from the candle just before this month if possible
            prev_bal = curve[i - 1] if i > 0 else starting_balance
            monthly[month] = (prev_bal, bal)
        else:
            monthly[month] = (monthly[month][0], bal)

    result: dict[str, float] = {}
    for month, (open_bal, close_bal) in sorted(monthly.items()):
        if open_bal > 0:
            result[month] = (close_bal / open_bal - 1.0) * 100.0
        else:
            result[month] = 0.0
    return result


def score_result(result: BacktestResult) -> float:
    """
    Composite score for parameter sweep ranking.
    score = expectancy_r × sqrt(trades_per_month)
    Balances edge quality with trade frequency.
    """
    if result.total_trades < 20:
        return -999.0
    tpm = max(result.trades_per_month, 0.01)
    return result.expectancy_r * sqrt(tpm)


def verdict(result: BacktestResult) -> str:
    """Return a VIABLE / MARGINAL / NOT VIABLE verdict string."""
    e = result.expectancy_r
    pf = result.profit_factor
    dd = result.max_drawdown_pct
    n  = result.total_trades
    if e > 0.05 and pf > 1.1 and dd < 30.0 and n >= 50:
        return "[GREEN] VIABLE"
    if e > 0.0 and pf > 1.0:
        return "[YELLOW] MARGINAL -- optimize parameters before forward testing"
    return "[RED] NOT VIABLE -- strategy needs redesign"


# -----------------------------------------------------------------------------
# REPORTING
# -----------------------------------------------------------------------------

def print_report(
    result: BacktestResult,
    instrument: str,
    strategy: str,
    params_str: str,
    data_path: str,
) -> None:
    """Print a formatted terminal report for a single backtest run."""
    r = result
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  BACKTEST REPORT -- {strategy}")
    print(f"{sep}")
    print(f"  Data: {data_path}")
    print(f"  Trades: {r.total_trades}  |  Period: {r.total_months} months")
    print()
    print(f"  -- Parameters --")
    print(params_str)
    print()
    print(f"  -- Core Metrics --")
    print(f"  Starting balance:  ${r.starting_balance:,.2f}")
    print(f"  Final balance:     ${r.final_balance:,.2f}")
    print(f"  Total return:      {r.total_return_pct:+.2f}%")
    tpm = r.trades_per_month
    avg_monthly = r.total_return_pct / r.total_months if r.total_months else 0
    print(f"  Avg monthly:       {avg_monthly:+.2f}%")
    print(f"  Profit factor:     {r.profit_factor:.3f}")
    print(f"  Expectancy (R):    {r.expectancy_r:+.4f}")
    print()
    print(f"  -- Win/Loss --")
    print(f"  Win rate:          {r.win_rate*100:.1f}%")
    print(f"  Avg winner:        +{r.avg_win_r:.3f}R")
    print(f"  Avg loser:         {r.avg_loss_r:.3f}R")
    print(f"  Max consec wins:   {r.max_consec_wins}")
    print(f"  Max consec losses: {r.max_consec_losses}")
    print()
    print(f"  -- Risk --")
    print(f"  Max drawdown:      {r.max_drawdown_pct:.2f}%  (${r.max_drawdown_usd:.2f})")
    print(f"  Positive months:   {r.positive_months}/{r.total_months}")
    print()
    print(f"  -- Activity --")
    print(f"  Trades/month:      {tpm:.1f}")
    print(f"  Avg bars held:     {r.avg_bars_held:.1f}")
    print(f"  Longs/Shorts:      {r.long_count} / {r.short_count}")
    tp_t = r.tp_count; sl_t = r.sl_count; to_t = r.timeout_count; tr_t = r.trail_count
    print(f"  TP/SL/Timeout/Trail: {tp_t} / {sl_t} / {to_t} / {tr_t}")
    print()
    print(f"  -- Monthly Returns --")
    for month, ret in sorted(r.monthly_returns.items()):
        bar_len = int(abs(ret) / 0.5)
        bar = "|" * min(bar_len, 20)
        tag = " UP" if ret > 0 else ("DWN" if ret < 0 else "   ")
        sign = "+" if ret >= 0 else " "
        print(f"  {month}  {tag} {sign}{ret:5.2f}%  {bar}")
    print()
    print(f"  -- VERDICT --")
    vd = verdict(result)
    for line in _verdict_detail(result):
        print(f"    {line}")
    print(f"  {vd}")
    print()


def _verdict_detail(r: BacktestResult) -> list[str]:
    """Return bullet points highlighting key strengths/weaknesses."""
    lines = []
    e = r.expectancy_r
    pf = r.profit_factor
    dd = r.max_drawdown_pct
    n  = r.total_trades
    lines.append(f"[{'OK' if e > 0.05 else '!!'}] {'Strong' if e > 0.1 else 'Positive'} expectancy ({e:+.3f}R)")
    lines.append(f"[{'OK' if pf > 1.1 else '!!'}] {'Healthy' if pf > 1.5 else 'Positive'} profit factor ({pf:.2f})")
    lines.append(f"[{'OK' if dd < 20 else '!!'}] Controlled drawdown ({dd:.1f}%)")
    lines.append(f"[{'OK' if n >= 50 else '!!'}] {'Adequate' if n >= 50 else 'Low'} sample size ({n} trades)")
    return lines


# -----------------------------------------------------------------------------
# CSV EXPORT
# -----------------------------------------------------------------------------

def export_trades_csv(trades: list[Trade], filepath: str) -> None:
    """Export full trade log to CSV."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if not trades:
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        fields = [
            "entry_time", "exit_time", "direction", "entry_price", "exit_price",
            "sl_price", "tp_price", "size_usd", "pnl_usd", "pnl_r",
            "exit_reason", "bars_held", "signal_value",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "direction": t.direction,
                "entry_price": f"{t.entry_price:.4f}",
                "exit_price": f"{t.exit_price:.4f}",
                "sl_price": f"{t.sl_price:.4f}",
                "tp_price": f"{t.tp_price:.4f}",
                "size_usd": f"{t.size_usd:.4f}",
                "pnl_usd": f"{t.pnl_usd:.4f}",
                "pnl_r": f"{t.pnl_r:.4f}",
                "exit_reason": t.exit_reason,
                "bars_held": t.bars_held,
                "signal_value": f"{t.signal_value:.4f}",
            })
    print(f"  Saved: {filepath}")


def export_equity_csv(times: list[str], curve: list[float], filepath: str) -> None:
    """Export equity curve to CSV."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["datetime_utc", "balance"])
        for t, b in zip(times, curve):
            writer.writerow([t, f"{b:.4f}"])
    print(f"  Saved: {filepath}")


