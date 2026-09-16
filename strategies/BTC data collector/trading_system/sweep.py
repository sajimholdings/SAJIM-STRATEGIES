"""
sweep.py -- Parameter Optimizer for the V2 Trading Backtesting System.

Tests both strategies (fade / momentum) across parameter ranges,
ranks results by score = expectancy_r × sqrt(trades_per_month),
and prints the top 25 configurations plus a full report for the best.

Usage:
    python sweep.py --instrument BTCUSDT_1H --strategy fade
    python sweep.py --instrument ETHUSDT_1H --strategy momentum
    python sweep.py --instrument BTCUSDT_1H --strategy both
    python sweep.py --all
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
from dataclasses import dataclass
from math import sqrt

from core import (
    BacktestResult,
    load_candles,
    export_trades_csv, export_equity_csv,
    print_report, score_result,
)
from backtest_fade import FadeParams, run_fade, params_str as fade_params_str
from backtest_momentum import MomentumParams, run_momentum, params_str as momentum_params_str


# -----------------------------------------------------------------------------
# SWEEP PARAMETER GRIDS
# -----------------------------------------------------------------------------

FADE_GRID = {
    "zscore_entry":      [1.5, 2.0, 2.5, 3.0],
    "sl_atr_multiple":   [1.5, 2.0, 3.0, 4.0],
    "tp_mean_fraction":  [0.5, 0.7, 0.9],
    "vol_threshold":     [0.8, 1.0, 1.2],
    "mean_period":       [14, 20],
    "mean_type":         ["ema", "vwma"],
    "max_bars":          [8, 12],
}

MOMENTUM_GRID = {
    "body_atr_entry":    [1.5, 2.0, 2.5, 3.0],
    "trail_atr_multiple":[2.0, 3.0, 4.0, 5.0],
    "vol_threshold":     [0.8, 1.0, 1.2],
    "max_bars":          [24, 36, 48],
    "partial_tp_atr":    [0.0, 2.0, 3.0],
}

# Partial pct only matters when partial_tp_atr > 0
MOMENTUM_PARTIAL_PCTS = [0.3, 0.4, 0.5]


# -----------------------------------------------------------------------------
# SWEEP RESULT CONTAINER
# -----------------------------------------------------------------------------

@dataclass
class SweepRow:
    """A single parameter combination result for ranking."""
    score: float
    result: BacktestResult
    label: str          # short human-readable param string
    fade_params: FadeParams | None = None
    momentum_params: MomentumParams | None = None


# -----------------------------------------------------------------------------
# FADE SWEEP
# -----------------------------------------------------------------------------

def sweep_fade(
    candles: list,
    starting_balance: float = 1000.0,
    risk_pct: float = 0.01,
    fee_pct: float = 0.001,
) -> list[SweepRow]:
    """Run the full fade parameter grid and return ranked results."""

    grid = list(itertools.product(
        FADE_GRID["zscore_entry"],
        FADE_GRID["sl_atr_multiple"],
        FADE_GRID["tp_mean_fraction"],
        FADE_GRID["vol_threshold"],
        FADE_GRID["mean_period"],
        FADE_GRID["mean_type"],
        FADE_GRID["max_bars"],
    ))

    total = len(grid)
    print(f"\n  -- FADE PARAMETER SWEEP --")
    print(f"  Running {total:,} parameter combinations...")

    rows: list[SweepRow] = []

    for idx, (zentry, sl_mult, tp_frac, vol_thr, mp, mtype, mb) in enumerate(grid, 1):
        if idx % 200 == 0:
            print(f"    Progress: {idx}/{total} ({idx*100//total}%)")

        p = FadeParams(
            zscore_entry=zentry,
            sl_atr_multiple=sl_mult,
            tp_mean_fraction=tp_frac,
            vol_threshold=vol_thr,
            mean_period=mp,
            mean_type=mtype,
            max_bars=mb,
            starting_balance=starting_balance,
            risk_pct=risk_pct,
            fee_pct=fee_pct,
        )
        r = run_fade(candles, p)
        if r.total_trades < 20:
            continue

        s = score_result(r)
        label = (
            f"Z={zentry:.1f} SL={sl_mult:.1f} TP={tp_frac:.2f} "
            f"Vol={vol_thr:.1f} Mean={mtype[:1].upper()}{mp} Bars={mb}"
        )
        rows.append(SweepRow(score=s, result=r, label=label, fade_params=p))

    rows.sort(key=lambda x: x.score, reverse=True)
    return rows


# -----------------------------------------------------------------------------
# MOMENTUM SWEEP
# -----------------------------------------------------------------------------

def sweep_momentum(
    candles: list,
    starting_balance: float = 1000.0,
    risk_pct: float = 0.01,
    fee_pct: float = 0.001,
) -> list[SweepRow]:
    """Run the full momentum parameter grid and return ranked results."""

    # Build combinations, handling partial_pct expansion
    base_grid = list(itertools.product(
        MOMENTUM_GRID["body_atr_entry"],
        MOMENTUM_GRID["trail_atr_multiple"],
        MOMENTUM_GRID["vol_threshold"],
        MOMENTUM_GRID["max_bars"],
        MOMENTUM_GRID["partial_tp_atr"],
    ))

    # Expand partial combos
    full_grid: list[tuple] = []
    for (bae, trail, vol, mb, ptp) in base_grid:
        if ptp == 0.0:
            full_grid.append((bae, trail, vol, mb, 0.0, 0.0))
        else:
            for ppct in MOMENTUM_PARTIAL_PCTS:
                full_grid.append((bae, trail, vol, mb, ptp, ppct))

    total = len(full_grid)
    print(f"\n  -- MOMENTUM PARAMETER SWEEP --")
    print(f"  Running {total:,} parameter combinations...")

    rows: list[SweepRow] = []

    for idx, (bae, trail, vol, mb, ptp, ppct) in enumerate(full_grid, 1):
        if idx % 200 == 0:
            print(f"    Progress: {idx}/{total} ({idx*100//total}%)")

        p = MomentumParams(
            body_atr_entry=bae,
            trail_atr_multiple=trail,
            vol_threshold=vol,
            max_bars=mb,
            partial_tp_atr=ptp,
            partial_pct=ppct,
            starting_balance=starting_balance,
            risk_pct=risk_pct,
            fee_pct=fee_pct,
        )
        r = run_momentum(candles, p)
        if r.total_trades < 20:
            continue

        s = score_result(r)
        partial_str = f" P={ppct:.1f}@{ptp:.1f}x" if ptp > 0 else ""
        label = (
            f"Body={bae:.1f} Trail={trail:.1f} "
            f"Vol={vol:.1f} Bars={mb}{partial_str}"
        )
        rows.append(SweepRow(score=s, result=r, label=label, momentum_params=p))

    rows.sort(key=lambda x: x.score, reverse=True)
    return rows


# -----------------------------------------------------------------------------
# RESULTS DISPLAY
# -----------------------------------------------------------------------------

def print_sweep_results(rows: list[SweepRow], strategy: str, top_n: int = 25) -> None:
    """Print the top N sweep results in a formatted table."""
    sep = "=" * 78
    print(f"\n{sep}")
    print(f"  {strategy.upper()} SWEEP RESULTS")
    print(f"{sep}")
    viable = [r for r in rows if r.result.total_trades >= 20]
    print(f"  Viable combinations (20+ trades): {len(viable)}")
    print()

    hdr = f"  {'Label':<44} | {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Exp(R)':>7}  {'Ret%':>6}  {'DD%':>5} | {'Score':>7}"
    print(hdr)
    print("  " + "-" * 76)

    for row in rows[:top_n]:
        r = row.result
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 99 else ">99"
        print(
            f"  {row.label:<44} | "
            f"{r.total_trades:>4}  "
            f"{r.win_rate*100:>5.1f}  "
            f"{pf_str:>5}  "
            f"{r.expectancy_r:>+7.4f}  "
            f"{r.total_return_pct:>+6.1f}  "
            f"{r.max_drawdown_pct:>5.1f} | "
            f"{row.score:>7.3f}"
        )


# -----------------------------------------------------------------------------
# COMPARISON PRINT
# -----------------------------------------------------------------------------

def print_comparison(
    fade_rows: list[SweepRow],
    momentum_rows: list[SweepRow],
    instrument: str,
) -> None:
    """Print a side-by-side comparison of best fade vs best momentum."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  HEAD-TO-HEAD COMPARISON: {instrument}")
    print(f"{sep}")

    def best_summary(rows: list[SweepRow], name: str) -> None:
        if not rows:
            print(f"  {name}: No viable configurations found.")
            return
        top = rows[0]
        r   = top.result
        pf_str = f"{r.profit_factor:.3f}" if r.profit_factor < 999 else ">999"
        print(f"  {name}:")
        print(f"    Best:       {top.label}")
        print(f"    Trades:     {r.total_trades} ({r.trades_per_month:.1f}/mo)")
        print(f"    Win Rate:   {r.win_rate*100:.1f}%")
        print(f"    Profit Factor: {pf_str}")
        print(f"    Expectancy: {r.expectancy_r:+.4f}R")
        print(f"    Return:     {r.total_return_pct:+.2f}%")
        print(f"    Drawdown:   {r.max_drawdown_pct:.2f}%")
        print(f"    Score:      {top.score:.3f}")
        print()

    best_summary(fade_rows, "FADE (Mean-Reversion)")
    best_summary(momentum_rows, "MOMENTUM (Trend-Following)")

    # Verdict
    fade_score = fade_rows[0].score if fade_rows else -999
    mom_score  = momentum_rows[0].score if momentum_rows else -999

    print(f"  -- RECOMMENDATION --")
    if fade_score > 0 and mom_score > 0:
        winner = "FADE" if fade_score >= mom_score else "MOMENTUM"
        loser  = "MOMENTUM" if winner == "FADE" else "FADE"
        print(f"  ✅ BOTH strategies have positive expectancy on {instrument}.")
        print(f"  🏆 {winner} wins by score ({max(fade_score, mom_score):.3f} vs {min(fade_score, mom_score):.3f})")
        print(f"  Consider deploying {winner} as primary, {loser} as secondary.")
    elif fade_score > 0:
        print(f"  🏆 FADE (Mean-Reversion) is the clear winner on {instrument}.")
        print(f"  ❌ Momentum strategy has no positive-expectancy configuration.")
    elif mom_score > 0:
        print(f"  🏆 MOMENTUM (Trend-Following) is the clear winner on {instrument}.")
        print(f"  ❌ Fade strategy has no positive-expectancy configuration.")
    else:
        print(f"  ❌ Neither strategy found a viable configuration on {instrument}.")
        print(f"     This instrument may require a fundamentally different approach.")
    print()


# -----------------------------------------------------------------------------
# MAIN RUNNER
# -----------------------------------------------------------------------------

INSTRUMENT_MAP = {
    "BTCUSDT_1H": "BTCUSDT_1H.csv",
    "BTCUSDT_4H": "BTCUSDT_4H.csv",
    "ETHUSDT_1H": "ETHUSDT_1H.csv",
    "ETHUSDT_4H": "ETHUSDT_4H.csv",
}


def run_sweep_for(
    instrument: str,
    strategy: str,
    data_dir: str,
    balance: float,
    risk: float,
    fee: float,
) -> None:
    """Load candles and run the requested strategy sweeps for one instrument."""
    csv_path = os.path.join(data_dir, INSTRUMENT_MAP.get(instrument, ""))
    if not os.path.exists(csv_path):
        # Try treating instrument directly as a path
        csv_path = instrument
    if not os.path.exists(csv_path):
        print(f"  ERROR: Cannot find data for {instrument} at {csv_path}")
        return

    print(f"\n{'='*70}")
    print(f"  INSTRUMENT: {instrument}")
    print(f"{'='*70}")
    candles = load_candles(csv_path)
    print(f"  Loaded {len(candles):,} candles  [{candles[0].dt} -> {candles[-1].dt}]")

    os.makedirs("results", exist_ok=True)

    fade_rows: list[SweepRow] = []
    momentum_rows: list[SweepRow] = []

    if strategy in ("fade", "both"):
        fade_rows = sweep_fade(candles, balance, risk, fee)
        print_sweep_results(fade_rows, f"{instrument} FADE")

        if fade_rows:
            best = fade_rows[0]
            print(f"\n  -- BEST FADE CONFIGURATION --")
            print(f"  {best.label}")
            print(f"  -> {best.result.total_trades} trades | "
                  f"{best.result.win_rate*100:.1f}% WR | "
                  f"{best.result.profit_factor:.3f} PF | "
                  f"{best.result.expectancy_r:+.4f} Exp(R)")
            print()
            print_report(
                best.result, instrument, "Mean-Reversion Fade",
                fade_params_str(best.fade_params),
                csv_path,
            )
            export_trades_csv(
                best.result.trades,
                f"results/{instrument}_fade_best_trades.csv"
            )
            export_equity_csv(
                best.result.equity_times,
                best.result.equity_curve,
                f"results/{instrument}_fade_best_equity.csv"
            )

    if strategy in ("momentum", "both"):
        momentum_rows = sweep_momentum(candles, balance, risk, fee)
        print_sweep_results(momentum_rows, f"{instrument} MOMENTUM")

        if momentum_rows:
            best = momentum_rows[0]
            print(f"\n  -- BEST MOMENTUM CONFIGURATION --")
            print(f"  {best.label}")
            print(f"  -> {best.result.total_trades} trades | "
                  f"{best.result.win_rate*100:.1f}% WR | "
                  f"{best.result.profit_factor:.3f} PF | "
                  f"{best.result.expectancy_r:+.4f} Exp(R)")
            print()
            print_report(
                best.result, instrument, "Trend-Following Momentum",
                momentum_params_str(best.momentum_params),
                csv_path,
            )
            export_trades_csv(
                best.result.trades,
                f"results/{instrument}_momentum_best_trades.csv"
            )
            export_equity_csv(
                best.result.equity_times,
                best.result.equity_curve,
                f"results/{instrument}_momentum_best_equity.csv"
            )

    if strategy == "both":
        print_comparison(fade_rows, momentum_rows, instrument)


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 Parameter Sweep Optimizer")
    parser.add_argument("--instrument", default=None,
                        help="Instrument key, e.g. BTCUSDT_1H")
    parser.add_argument("--strategy", default="fade",
                        choices=["fade", "momentum", "both"],
                        help="Which strategy to sweep")
    parser.add_argument("--all", action="store_true",
                        help="Run all instruments and both strategies")
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--risk",    type=float, default=0.01)
    parser.add_argument("--fee",     type=float, default=0.001)
    parser.add_argument("--datadir", default="data",
                        help="Directory containing CSV data files")
    args = parser.parse_args()

    banner = "=" * 58 + "\n"
    banner += "  V2 Trading System -- Parameter Sweep Optimizer\n"
    banner += "=" * 58
    print(banner)

    if args.all:
        for instrument in INSTRUMENT_MAP:
            run_sweep_for(instrument, "both", args.datadir, args.balance, args.risk, args.fee)
    elif args.instrument:
        run_sweep_for(args.instrument, args.strategy, args.datadir, args.balance, args.risk, args.fee)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()


