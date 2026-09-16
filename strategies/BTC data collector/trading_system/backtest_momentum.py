"""
backtest_momentum.py — Trend-Following / Momentum Strategy Backtester (V2)

Enters WITH large breakout candles and rides the trend using a dynamic
trailing stop. Designed for ETH which statistically trends on large moves.

Usage:
    python backtest_momentum.py --file data/ETHUSDT_1H.csv
    python backtest_momentum.py --file data/ETHUSDT_1H.csv --balance 10000
    python backtest_momentum.py --file data/BTCUSDT_1H.csv  # cross-test on BTC
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from core import (
    Candle, Trade, BacktestResult,
    load_candles,
    calc_atr, calc_sma,
    compute_stats, print_report, export_trades_csv, export_equity_csv,
)


# ─────────────────────────────────────────────────────────────────────────────
# PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MomentumParams:
    """All tunable parameters for the trend-following momentum strategy."""
    # Entry
    atr_period: int = 14
    body_atr_entry: float = 2.0         # candle body must exceed this × ATR

    # Volatility regime filter
    atr_ma_period: int = 50
    vol_threshold: float = 1.0

    # Exit: trailing stop
    trail_atr_multiple: float = 4.0     # trail at this × ATR from peak/trough
    max_bars: int = 48                  # longer time stop for trends

    # Optional partial exits
    partial_tp_atr: float = 2.0         # take partial at this × ATR profit (0 = off)
    partial_pct: float = 0.4            # fraction of position to close at partial TP

    # Risk
    risk_pct: float = 0.01
    fee_pct: float = 0.001
    starting_balance: float = 1000.0
    max_position_pct: float = 0.30


# ─────────────────────────────────────────────────────────────────────────────
# CORE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def run_momentum(candles: list[Candle], p: MomentumParams) -> BacktestResult:
    """
    Execute the trend-following momentum strategy on a list of candles.

    Uses a dynamic trailing stop as the primary exit. The trailing stop
    uses the CURRENT ATR (updates each bar), tightening when volatility
    contracts to lock in profit and widening when volatility expands.

    Returns a BacktestResult with trades and equity curve populated.
    Stats are computed before returning.
    """
    result = BacktestResult()

    # ── Pre-compute indicator arrays ──────────────────────────────────────────
    atr_vals = calc_atr(candles, p.atr_period)
    atr_ma   = calc_sma(atr_vals, p.atr_ma_period)

    warmup = max(p.atr_period, p.atr_ma_period) + 10
    n = len(candles)

    # ── State ─────────────────────────────────────────────────────────────────
    balance       = p.starting_balance
    in_trade      = False
    direction     = ""
    entry_price   = 0.0
    size_usd      = 0.0       # current (remaining) position size in USD
    size_usd_full = 0.0       # original full position size (for partial exit tracking)
    entry_time    = ""
    bars_in       = 0
    signal_val    = 0.0
    sl_price      = 0.0       # initial SL (stored for Trade record, not used for exit)
    atr_at_entry  = 0.0

    # Trail tracking
    peak           = 0.0      # highest high since entry (LONG)
    trough         = 0.0      # lowest low since entry (SHORT)
    partial_done   = False    # has the partial TP been executed

    trades: list[Trade] = []

    for i, candle in enumerate(candles):
        if i < warmup:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        atr  = atr_vals[i]
        amov = atr_ma[i]

        if atr <= 0 or amov <= 0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # ── Manage open position ───────────────────────────────────────────────
        if in_trade:
            bars_in += 1
            closed  = False
            exit_px = 0.0
            reason  = ""

            # Update trail peak/trough first
            if direction == "LONG":
                if candle.high > peak:
                    peak = candle.high
                trail_level = peak - p.trail_atr_multiple * atr
            else:  # SHORT
                if candle.low < trough:
                    trough = candle.low
                trail_level = trough + p.trail_atr_multiple * atr

            # Check trailing stop (gap protection: use candle.open if gaps through)
            if direction == "LONG":
                if candle.low <= trail_level:
                    exit_px = max(trail_level, candle.open)  # gap protection
                    exit_px = min(exit_px, candle.open) if candle.open < trail_level else trail_level
                    reason  = "TRAIL"
                    closed  = True
            else:  # SHORT
                if candle.high >= trail_level:
                    exit_px = min(trail_level, candle.open)
                    exit_px = max(exit_px, candle.open) if candle.open > trail_level else trail_level
                    reason  = "TRAIL"
                    closed  = True

            # Partial TP (only once per trade, before full close)
            if not closed and not partial_done and p.partial_tp_atr > 0 and p.partial_pct > 0:
                partial_target = p.partial_tp_atr * atr_at_entry
                if direction == "LONG":
                    unrealized_pts = candle.high - entry_price
                else:
                    unrealized_pts = entry_price - candle.low

                if unrealized_pts >= partial_target:
                    # Close partial_pct of the position
                    partial_exit_px = (entry_price + partial_target) if direction == "LONG" \
                                      else (entry_price - partial_target)
                    partial_size = size_usd * p.partial_pct
                    size_usd    -= partial_size

                    if direction == "LONG":
                        raw_pnl = (partial_exit_px - entry_price) / entry_price
                    else:
                        raw_pnl = (entry_price - partial_exit_px) / entry_price
                    net_pnl  = raw_pnl - 2.0 * p.fee_pct
                    pnl_usd  = net_pnl * partial_size
                    balance += pnl_usd
                    partial_done = True

            # Timeout
            if not closed and bars_in >= p.max_bars:
                exit_px = candle.close
                reason  = "TIMEOUT"
                closed  = True

            if closed:
                if direction == "LONG":
                    raw_pnl_pct = (exit_px - entry_price) / entry_price
                else:
                    raw_pnl_pct = (entry_price - exit_px) / entry_price
                net_pnl_pct = raw_pnl_pct - 2.0 * p.fee_pct
                pnl_usd     = net_pnl_pct * size_usd
                risk_dist   = p.trail_atr_multiple * atr_at_entry
                risk_frac   = risk_dist / entry_price if entry_price > 0 else 0.001
                pnl_r       = net_pnl_pct / risk_frac if risk_frac > 0 else 0.0

                balance += pnl_usd
                if balance <= 0:
                    balance = 0.0

                trades.append(Trade(
                    entry_time=entry_time,
                    exit_time=candle.dt,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_px,
                    sl_price=sl_price,
                    tp_price=0.0,        # no fixed TP
                    size_usd=size_usd_full,  # log original size
                    pnl_usd=pnl_usd,
                    pnl_r=pnl_r,
                    exit_reason=reason,
                    bars_held=bars_in,
                    signal_value=signal_val,
                ))
                in_trade = False

            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # ── Check for new entry ───────────────────────────────────────────────
        if balance <= 0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Regime filter
        if atr < p.vol_threshold * amov:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Candle body check
        body = candle.close - candle.open  # positive = bullish
        abs_body = abs(body)
        body_multiple = abs_body / atr if atr > 0 else 0.0

        if abs_body <= p.body_atr_entry * atr:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Direction: enter WITH the breakout
        new_direction = "LONG" if body > 0 else "SHORT"

        # Position sizing (initial SL = trail_atr_multiple × ATR from entry)
        ep         = candle.close
        risk_dist  = p.trail_atr_multiple * atr
        risk_frac  = risk_dist / ep
        raw_size   = (balance * p.risk_pct) / risk_frac
        capped     = balance * p.max_position_pct
        size       = min(raw_size, capped)

        if new_direction == "SHORT":
            init_sl = ep + risk_dist
        else:
            init_sl = ep - risk_dist

        # Enter
        in_trade      = True
        direction     = new_direction
        entry_price   = ep
        size_usd      = size
        size_usd_full = size
        sl_price      = init_sl
        entry_time    = candle.dt
        bars_in       = 0
        signal_val    = body_multiple
        atr_at_entry  = atr
        partial_done  = False

        # Initialize trail tracking
        if direction == "LONG":
            peak   = candle.high
            trough = 0.0
        else:
            trough = candle.low
            peak   = float("inf")

        result.equity_curve.append(balance)
        result.equity_times.append(candle.dt)

    # ── Force-close any open trade at end ─────────────────────────────────────
    if in_trade and candles:
        candle  = candles[-1]
        exit_px = candle.close
        if direction == "LONG":
            raw_pnl_pct = (exit_px - entry_price) / entry_price
        else:
            raw_pnl_pct = (entry_price - exit_px) / entry_price
        net_pnl_pct = raw_pnl_pct - 2.0 * p.fee_pct
        pnl_usd     = net_pnl_pct * size_usd
        risk_dist   = p.trail_atr_multiple * atr_at_entry
        risk_frac   = risk_dist / entry_price if entry_price > 0 else 0.001
        pnl_r       = net_pnl_pct / risk_frac
        balance    += pnl_usd
        trades.append(Trade(
            entry_time=entry_time,
            exit_time=candle.dt,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_px,
            sl_price=sl_price,
            tp_price=0.0,
            size_usd=size_usd_full,
            pnl_usd=pnl_usd,
            pnl_r=pnl_r,
            exit_reason="TIMEOUT",
            bars_held=bars_in,
            signal_value=signal_val,
        ))

    result.trades = trades
    compute_stats(result, p.starting_balance)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PARAMS STRING HELPER
# ─────────────────────────────────────────────────────────────────────────────

def params_str(p: MomentumParams) -> str:
    partial_info = f"{p.partial_pct*100:.0f}% at {p.partial_tp_atr}x ATR profit" \
                   if p.partial_tp_atr > 0 else "disabled"
    return (
        f"  ATR period:        {p.atr_period}\n"
        f"  Regime threshold:  ATR > {p.vol_threshold}x ATR_MA({p.atr_ma_period})\n"
        f"  Entry trigger:     |body| > {p.body_atr_entry}x ATR\n"
        f"  Trailing stop:     {p.trail_atr_multiple}x ATR from peak/trough\n"
        f"  Partial TP:        {partial_info}\n"
        f"  Max hold:          {p.max_bars} bars\n"
        f"  Risk/trade:        {p.risk_pct*100:.1f}%\n"
        f"  Fee/side:          {p.fee_pct*100:.2f}%"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="V2 Trend-Following Momentum Backtester")
    parser.add_argument("--file",    required=True,  help="Path to candle CSV")
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--risk",    type=float, default=0.01)
    parser.add_argument("--fee",     type=float, default=0.001)
    args = parser.parse_args()

    print(f"\n  Loading candles from {args.file}...")
    candles = load_candles(args.file)
    print(f"  Loaded {len(candles):,} candles")
    if candles:
        print(f"  Range: {candles[0].dt} -> {candles[-1].dt}")

    p = MomentumParams(
        starting_balance=args.balance,
        risk_pct=args.risk,
        fee_pct=args.fee,
    )

    result = run_momentum(candles, p)

    instrument = os.path.splitext(os.path.basename(args.file))[0]
    print_report(result, instrument, "Trend-Following Momentum", params_str(p), args.file)

    os.makedirs("results", exist_ok=True)
    export_trades_csv(result.trades, f"results/{instrument}_momentum_trades.csv")
    export_equity_csv(result.equity_times, result.equity_curve, f"results/{instrument}_momentum_equity.csv")


if __name__ == "__main__":
    main()

