"""
backtest_fade.py — Mean-Reversion / Fade Strategy Backtester (V2)

Fades price when it deviates too far from its rolling mean during
high-volatility regimes. Designed for BTC which is statistically
mean-reverting on large intraday moves.

Usage:
    python backtest_fade.py --file data/BTCUSDT_1H.csv
    python backtest_fade.py --file data/BTCUSDT_1H.csv --balance 10000
    python backtest_fade.py --file data/BTCUSDT_1H.csv --risk 0.005
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from core import (
    Candle, Trade, BacktestResult,
    load_candles,
    calc_atr, calc_ema, calc_vwma, calc_sma, calc_rolling_std, calc_zscore,
    compute_stats, print_report, export_trades_csv, export_equity_csv,
)


# ─────────────────────────────────────────────────────────────────────────────
# PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FadeParams:
    """All tunable parameters for the mean-reversion fade strategy."""
    # Mean calculation
    mean_period: int = 20
    mean_type: str = "ema"          # "ema" or "vwma"

    # Z-score entry
    zscore_period: int = 20
    zscore_entry: float = 2.0

    # Volatility regime filter
    atr_period: int = 14
    atr_ma_period: int = 50
    vol_threshold: float = 1.0

    # Exits
    tp_mean_fraction: float = 0.7   # fraction of distance back to mean
    sl_atr_multiple: float = 3.0
    max_bars: int = 12

    # Risk
    risk_pct: float = 0.01
    fee_pct: float = 0.001
    starting_balance: float = 1000.0
    max_position_pct: float = 0.30


# ─────────────────────────────────────────────────────────────────────────────
# CORE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def run_fade(candles: list[Candle], p: FadeParams) -> BacktestResult:
    """
    Execute the mean-reversion fade strategy on a list of candles.

    Returns a BacktestResult with trades and equity curve populated.
    Stats are computed before returning.
    """
    result = BacktestResult()

    # ── Pre-compute all indicator arrays ──────────────────────────────────────
    closes = [c.close for c in candles]
    atr_vals = calc_atr(candles, p.atr_period)
    atr_ma   = calc_sma(atr_vals, p.atr_ma_period)

    if p.mean_type == "vwma":
        mean_vals = calc_vwma(candles, p.mean_period)
    else:
        mean_vals = calc_ema(closes, p.mean_period)

    zscore_vals = calc_zscore(candles, mean_vals, p.zscore_period)

    # ── Warmup: skip until all indicators are valid ───────────────────────────
    warmup = max(p.atr_period, p.mean_period, p.zscore_period, p.atr_ma_period) + 10
    n = len(candles)

    # ── State ─────────────────────────────────────────────────────────────────
    balance     = p.starting_balance
    in_trade    = False
    direction   = ""
    entry_price = 0.0
    sl_price    = 0.0
    tp_price    = 0.0
    size_usd    = 0.0
    entry_time  = ""
    bars_in     = 0
    signal_val  = 0.0
    risk_dist   = 0.0  # absolute price distance risked (for R calculation)

    trades: list[Trade] = []

    for i, candle in enumerate(candles):
        # Always record equity after processing this candle
        # (appended at the end of the loop body)

        if i < warmup:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        atr  = atr_vals[i]
        amov = atr_ma[i]
        mean = mean_vals[i]
        z    = zscore_vals[i]

        # ── Guard: indicators must be valid ───────────────────────────────────
        if atr <= 0 or amov <= 0 or mean <= 0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # ── Manage open position ───────────────────────────────────────────────
        if in_trade:
            bars_in += 1
            closed   = False
            exit_px  = 0.0
            reason   = ""

            if direction == "LONG":
                # SL first (pessimistic — use candle low)
                if candle.low <= sl_price:
                    exit_px = sl_price
                    reason  = "SL"
                    closed  = True
                # TP (use candle high)
                elif candle.high >= tp_price:
                    exit_px = tp_price
                    reason  = "TP"
                    closed  = True
            else:  # SHORT
                if candle.high >= sl_price:
                    exit_px = sl_price
                    reason  = "SL"
                    closed  = True
                elif candle.low <= tp_price:
                    exit_px = tp_price
                    reason  = "TP"
                    closed  = True

            # Timeout
            if not closed and bars_in >= p.max_bars:
                exit_px = candle.close
                reason  = "TIMEOUT"
                closed  = True

            if closed:
                # Calculate P&L
                if direction == "LONG":
                    raw_pnl_pct = (exit_px - entry_price) / entry_price
                else:
                    raw_pnl_pct = (entry_price - exit_px) / entry_price
                net_pnl_pct = raw_pnl_pct - 2.0 * p.fee_pct
                pnl_usd     = net_pnl_pct * size_usd
                risk_pct_actual = risk_dist / entry_price
                pnl_r       = net_pnl_pct / risk_pct_actual if risk_pct_actual > 0 else 0.0

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
                    tp_price=tp_price,
                    size_usd=size_usd,
                    pnl_usd=pnl_usd,
                    pnl_r=pnl_r,
                    exit_reason=reason,
                    bars_held=bars_in,
                    signal_value=signal_val,
                ))
                in_trade = False

            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue  # don't check for new entry on the same bar

        # ── Check for new entry ───────────────────────────────────────────────
        if balance <= 0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Regime filter: only trade in high-volatility environment
        if atr < p.vol_threshold * amov:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        new_direction = ""
        if z > p.zscore_entry:
            new_direction = "SHORT"  # price overextended UP -> fade it
        elif z < -p.zscore_entry:
            new_direction = "LONG"   # price overextended DOWN -> fade it

        if not new_direction:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Validate: price must be on the correct side of the mean
        dist_to_mean = candle.close - mean
        if new_direction == "LONG" and dist_to_mean >= 0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue
        if new_direction == "SHORT" and dist_to_mean <= 0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Compute TP / SL
        ep = candle.close  # entry at close of signal candle
        if new_direction == "SHORT":
            tp_candidate = ep - abs(dist_to_mean) * p.tp_mean_fraction
            sl_candidate = ep + p.sl_atr_multiple * atr
        else:  # LONG
            tp_candidate = ep + abs(dist_to_mean) * p.tp_mean_fraction
            sl_candidate = ep - p.sl_atr_multiple * atr

        # Validate reward/risk >= 1.0
        reward = abs(tp_candidate - ep)
        risk   = abs(sl_candidate - ep)
        if risk <= 0 or reward / risk < 1.0:
            result.equity_curve.append(balance)
            result.equity_times.append(candle.dt)
            continue

        # Position sizing
        risk_frac = risk / ep
        raw_size  = (balance * p.risk_pct) / risk_frac
        capped    = balance * p.max_position_pct
        size      = min(raw_size, capped)

        # Enter trade
        in_trade    = True
        direction   = new_direction
        entry_price = ep
        sl_price    = sl_candidate
        tp_price    = tp_candidate
        size_usd    = size
        entry_time  = candle.dt
        bars_in     = 0
        signal_val  = z
        risk_dist   = risk

        result.equity_curve.append(balance)
        result.equity_times.append(candle.dt)

    # ── Force-close any open trade at end of data ─────────────────────────────
    if in_trade and candles:
        candle  = candles[-1]
        exit_px = candle.close
        if direction == "LONG":
            raw_pnl_pct = (exit_px - entry_price) / entry_price
        else:
            raw_pnl_pct = (entry_price - exit_px) / entry_price
        net_pnl_pct = raw_pnl_pct - 2.0 * p.fee_pct
        pnl_usd     = net_pnl_pct * size_usd
        risk_pct_actual = risk_dist / entry_price
        pnl_r       = net_pnl_pct / risk_pct_actual if risk_pct_actual > 0 else 0.0
        balance    += pnl_usd
        trades.append(Trade(
            entry_time=entry_time,
            exit_time=candle.dt,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_px,
            sl_price=sl_price,
            tp_price=tp_price,
            size_usd=size_usd,
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

def params_str(p: FadeParams) -> str:
    return (
        f"  ATR period:        {p.atr_period}\n"
        f"  Regime threshold:  ATR > {p.vol_threshold}x ATR_MA({p.atr_ma_period})\n"
        f"  Mean:              {p.mean_type.upper()}({p.mean_period})\n"
        f"  Z-score entry:     |Z| > {p.zscore_entry} (period={p.zscore_period})\n"
        f"  TP:                {p.tp_mean_fraction}x distance to mean\n"
        f"  SL:                {p.sl_atr_multiple}x ATR from entry\n"
        f"  Max hold:          {p.max_bars} bars\n"
        f"  Risk/trade:        {p.risk_pct*100:.1f}%\n"
        f"  Fee/side:          {p.fee_pct*100:.2f}%"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="V2 Mean-Reversion Fade Backtester")
    parser.add_argument("--file",    required=True,  help="Path to candle CSV")
    parser.add_argument("--balance", type=float, default=1000.0, help="Starting balance")
    parser.add_argument("--risk",    type=float, default=0.01,   help="Risk per trade, e.g. 0.01 for 1 percent")
    parser.add_argument("--fee",     type=float, default=0.001,  help="Fee per side")
    args = parser.parse_args()

    print(f"\n  Loading candles from {args.file}...")
    candles = load_candles(args.file)
    print(f"  Loaded {len(candles):,} candles")
    if candles:
        print(f"  Range: {candles[0].dt} -> {candles[-1].dt}")

    p = FadeParams(
        starting_balance=args.balance,
        risk_pct=args.risk,
        fee_pct=args.fee,
    )

    result = run_fade(candles, p)

    instrument = os.path.splitext(os.path.basename(args.file))[0]
    print_report(result, instrument, "Mean-Reversion Fade", params_str(p), args.file)

    os.makedirs("results", exist_ok=True)
    export_trades_csv(result.trades, f"results/{instrument}_fade_trades.csv")
    export_equity_csv(result.equity_times, result.equity_curve, f"results/{instrument}_fade_equity.csv")


if __name__ == "__main__":
    main()

