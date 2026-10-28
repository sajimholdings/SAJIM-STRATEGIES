"""
SAJIM Strategy Lab — the trusted metrics behind every gate rule (INTERFACES.md section 6).

Every function returns a flat ``dict[str, float | None]`` keyed by the metric names that
``gates.GATES`` rules reference. An ABSENT key means "rule not evaluated"; a key present
with ``None`` means "evaluated and not computable", which fails the rule. A nested
``detail`` dict may ride along (server-only: per-fold, per-neighbour and null numbers
never reach the builder).

Robustness work runs entirely in the trusted simulator: cost stress, the +1 bar delay,
the four fixed-parameter folds, the break-even cost search and the 300-run random-entry
null re-simulate the SAME events. Only plateau neighbours need fresh strategy output,
which ``evaluate.py`` gets from one sandbox job.

The simulator is reached only through a ``SimulateFn`` closure built by evaluate.py, so
this module never loads data and never imports another builder's module.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from sajim_quant.analytics.metrics import calculate_metrics
from sajim_quant.core.instruments import InstrumentSpec
from sajim_quant.core.schema import BacktestResult, Direction, Trade
from sajim_quant.lab import gates, splits
from sajim_quant.lab.interfaces import (
    EVENTS_DTYPE, CheckReport, Number, SimOverrides, SimResult, SimulateFn,
)

logger = logging.getLogger(__name__)

NULL_SEED_SCHEME = "sajim.lab.null/1"
_SQRT_252 = math.sqrt(252.0)


# ── Shared definitions ──────────────────────────────────────────────────────
def profit_factor(pnls: Sequence[float]) -> Optional[float]:
    """
    gross profit / gross loss. None with no trades, or with neither wins nor losses;
    inf with wins and no losses (serialised as JSON null; the rule result is kept).
    """
    vals = [float(p) for p in pnls]
    if not vals:
        return None
    gp = sum(p for p in vals if p > 0)
    gl = -sum(p for p in vals if p < 0)
    if gl > 0:
        return gp / gl
    return float("inf") if gp > 0 else None


def _pf_num(pf: Optional[float]) -> float:
    """PF where an aggregate needs a number: None counts as 0.0 (INTERFACES 6.2)."""
    return 0.0 if pf is None else float(pf)


def _ts(iso: str) -> pd.Timestamp:
    t = pd.Timestamp(iso)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def _naive(t: Any) -> datetime:
    return splits.to_utc_naive(t)


def daily_sharpe(trades: Sequence[Trade], *, first_bar: Any, last_bar: Any,
                 initial_balance: float) -> Optional[float]:
    """
    Annualised Sharpe of a zero-filled business-day return series (x sqrt(252)).

    A trade counts on its exit day; a weekend exit counts on the next business day,
    clamped to the window's business-day range. r_d = that day's net / equity at the
    start of the day. None with fewer than 2 business days; 0.0 when std is 0.
    """
    first, last = _naive(first_bar), _naive(last_bar)
    days = pd.bdate_range(first.date(), last.date())
    if len(days) < 2:
        return None
    pnl = np.zeros(len(days))
    if trades:
        exits = pd.DatetimeIndex([_ts(t.exit_time) for t in trades]).normalize()
        pos = days.searchsorted(exits, side="left")          # weekend -> next business day
        pos = np.clip(pos, 0, len(days) - 1)
        np.add.at(pnl, pos, np.array([float(t.net_pnl) for t in trades]))
    equity_start = float(initial_balance) + np.concatenate([[0.0], np.cumsum(pnl)[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(equity_start > 0, pnl / equity_start, 0.0)
    sd = float(np.std(r, ddof=1))
    if not math.isfinite(sd):
        return None
    if sd == 0.0:
        return 0.0
    return float(np.mean(r) / sd * _SQRT_252)


def _side_stats(trades: Sequence[Trade], direction: Direction) -> Tuple[int, float, Optional[float]]:
    pnls = [float(t.net_pnl) for t in trades if t.direction == direction]
    return len(pnls), float(sum(pnls)), profit_factor(pnls)


def performance_metrics(result: BacktestResult, *, months: Sequence[str], first_bar: Any,
                        last_bar: Any) -> Dict[str, Any]:
    """G3 metrics plus the scorecard headline, sides and G11 context inputs."""
    trades = list(result.trades)
    pm = calculate_metrics(result)                 # drawdown incl. intra-trade MAE; never reimplemented
    pnls = [float(t.net_pnl) for t in trades]
    net = float(sum(pnls))
    n = len(trades)

    top5 = max_month = None
    if n and net > 0:
        top5 = float(sum(sorted(pnls, reverse=True)[:5]) / net)
        by_month: Dict[str, float] = {}
        for t in trades:
            m = _ts(t.exit_time).strftime("%Y-%m")
            by_month[m] = by_month.get(m, 0.0) + float(t.net_pnl)
        max_month = float(max(by_month.values()) / net)

    long_n, long_net, long_pf = _side_stats(trades, Direction.BUY)
    short_n, short_net, short_pf = _side_stats(trades, Direction.SELL)
    side_pfs = [_pf_num(pf) for k, pf in ((long_n, long_pf), (short_n, short_pf)) if k > 0]
    min_side_pf = min(side_pfs) if side_pfs else None

    entries = [_ts(t.entry_time) for t in trades]
    month_set = set(months)
    entry_months = {e.strftime("%Y-%m") for e in entries}
    months_frac = (len(entry_months & month_set) / len(month_set)) if month_set else None

    holds = [(_ts(t.exit_time) - e).total_seconds() / 86400.0 for t, e in zip(trades, entries)]
    lo_h, hi_h = gates.ROLLOVER_FLAG_UTC_HOURS
    rollover = (sum(1 for e in entries if lo_h <= e.hour < hi_h) / n) if n else 0.0

    return {
        "trades": n,
        "net": net,
        "return_pct": float(pm.return_pct),
        "pf": profit_factor(pnls),
        "expectancy": float(pm.expectancy),
        "win_rate": float(pm.win_rate_pct) / 100.0,
        "max_dd_pct": float(pm.max_drawdown_pct),
        "max_dd_frac": float(pm.max_drawdown_pct) / 100.0,
        "daily_sharpe": daily_sharpe(trades, first_bar=first_bar, last_bar=last_bar,
                                     initial_balance=result.metadata.initial_balance),
        "top5_profit_share": top5,
        "max_month_profit_share": max_month,
        "long_trades": long_n, "long_net": long_net, "long_pf": long_pf,
        "short_trades": short_n, "short_net": short_net, "short_pf": short_pf,
        "min_side_pf": min_side_pf,
        "months_with_trades_frac": months_frac,
        "median_hold_days": float(np.median(holds)) if holds else 0.0,
        "rollover_entry_share": float(rollover),
    }


# ── G0 / G1 / G2 ────────────────────────────────────────────────────────────
def integrity_metrics(check: CheckReport, sim: Optional[SimResult], *, result: Optional[BacktestResult],
                      fx_desc: str, spec: InstrumentSpec, n_params: Optional[int]) -> Dict[str, Any]:
    """G0 + G1. With sim None (integrity failed) only the always-known metrics are present."""
    out: Dict[str, Any] = {
        "integrity_checks_passed": 1.0 if check.passed else 0.0,
        "n_params": None if n_params is None else int(n_params),
        "slippage_points": float(spec.slippage_points),
    }
    if sim is None:
        return out
    if result is None:
        raise ValueError("integrity_metrics needs the BacktestResult when a simulation ran")
    meta = result.metadata
    cost_ok = (meta.commission_model != "zero"
               and not (fx_desc or "").startswith(gates.UNCONVERTED_FX_PREFIX)
               and meta.engine == gates.ENGINE)
    out["cost_model_checks_passed"] = 1.0 if cost_ok else 0.0
    out["untradable_stop_frac"] = sim.untradable_stop / max(1, sim.n_entry_attempts)
    out["n_signals"] = int(sim.n_signals)
    return out


def sample_metrics(sim: SimResult, stage: str, *, months: Sequence[str]) -> Dict[str, Any]:
    """G2: dev -> trade count and the share of DEV months with an entry; holdout -> trade count."""
    stage = splits.normalize_stage(stage)
    if stage == splits.DEV:
        month_set = set(months)
        entry_months = {_ts(t.entry_time).strftime("%Y-%m") for t in sim.trades}
        frac = (len(entry_months & month_set) / len(month_set)) if month_set else None
        return {"dev_trades": len(sim.trades), "dev_months_with_trades_frac": frac}
    if stage == splits.HOLDOUT:
        return {"holdout_trades": len(sim.trades)}
    raise ValueError(f"sample_metrics: stage must be dev or holdout, got {stage!r}")


# ── G6 cost / latency stress ────────────────────────────────────────────────
def _pf_of(sim: SimResult) -> Optional[float]:
    return profit_factor([t.net_pnl for t in sim.trades])


def breakeven_cost_mult(simulate: SimulateFn, events: np.ndarray, lo: float = gates.BREAKEVEN_MULT_RANGE[0],
                        hi: float = gates.BREAKEVEN_MULT_RANGE[1], tol: float = 0.01,
                        max_iter: int = 12) -> Tuple[float, str]:
    """Cost multiplier where net crosses zero, by bisection on net(m) > 0 (reported, not gated)."""
    def net(m: float) -> float:
        return simulate(events, SimOverrides(cost_mult=m)).net

    if net(lo) <= 0:
        return lo, "below_range"
    if net(hi) > 0:
        return hi, "above_range"
    a, b = float(lo), float(hi)
    for _ in range(max_iter):
        if b - a <= tol:
            break
        mid = (a + b) / 2.0
        if net(mid) > 0:
            a = mid
        else:
            b = mid
    return round((a + b) / 2.0, 2), "in_range"


def stress_metrics(simulate: SimulateFn, events: np.ndarray) -> Dict[str, Any]:
    """G6: net at 1.5x cost, PF at 2x cost, PF with a +1 bar entry delay; break-even reported."""
    s15 = simulate(events, SimOverrides(cost_mult=gates.COST_STRESS_NET_MULT))
    s20 = simulate(events, SimOverrides(cost_mult=gates.COST_STRESS_PF_MULT))
    sd = simulate(events, SimOverrides(delay_bars=gates.DELAY_BARS))
    be, flag = breakeven_cost_mult(simulate, events)
    return {
        "net_at_1_5x_cost": s15.net,
        "pf_at_2x_cost": _pf_of(s20),
        "pf_delay_1": _pf_of(sd),
        "breakeven_cost_mult": be,
        "breakeven_flag": flag,
        "detail": {"stress": {
            "cost_1_5x": {"trades": len(s15.trades), "net": s15.net, "pf": _pf_of(s15)},
            "cost_2x": {"trades": len(s20.trades), "net": s20.net, "pf": _pf_of(s20)},
            "delay_1": {"trades": len(sd.trades), "net": sd.net, "pf": _pf_of(sd)},
            "breakeven_cost_mult": be, "breakeven_flag": flag,
        }},
    }


# ── G5 folds ────────────────────────────────────────────────────────────────
def fold_bounds(window: Tuple[int, int], n_folds: int = gates.N_FOLDS) -> List[Tuple[int, int]]:
    """Equal bar-count contiguous folds of a half-open bar window."""
    ws, we = int(window[0]), int(window[1])
    if n_folds < 1 or we < ws:
        raise ValueError("fold_bounds needs n_folds >= 1 and a non-negative window")
    edges = [ws + (k * (we - ws)) // n_folds for k in range(n_folds + 1)]
    return [(edges[k], edges[k + 1]) for k in range(n_folds)]


def fold_metrics(simulate: SimulateFn, events: np.ndarray, window: Tuple[int, int]) -> Dict[str, Any]:
    """G5: each fold simulated alone (fresh balance, fold-end TIMEOUT), params fixed."""
    folds = []
    for a, b in fold_bounds(window):
        s = simulate(events, SimOverrides(window=(a, b)))
        folds.append({"start_bar": a, "stop_bar": b, "trades": len(s.trades), "net": s.net, "pf": _pf_of(s)})
    return {
        "positive_folds": sum(1 for f in folds if f["net"] > 0),
        "min_fold_pf": min(_pf_num(f["pf"]) for f in folds),
        "detail": {"folds": folds},
    }


# ── G4a random-entry null ───────────────────────────────────────────────────
def null_seed(code_sha256: str, stage: str, r: int) -> int:
    """Frozen per-(sha, stage, run) seed of the random-entry null."""
    blob = f"{NULL_SEED_SCHEME}|{code_sha256}|{stage}|{r}".encode("ascii")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def _trade_shapes(base: SimResult) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dirs, stops, tps, holds = [], [], [], []
    for t in base.trades:
        cf = t.custom_fields
        dirs.append(1 if t.direction == Direction.BUY else -1)
        stops.append(float(cf["stop_dist"]))
        tp = cf.get("tp_dist")
        tps.append(np.nan if tp is None else float(tp))
        holds.append(int(cf["exit_bar"]) - int(cf["entry_bar"]) + 1)
    return (np.array(dirs, dtype=np.int8), np.array(stops, dtype=np.float64),
            np.array(tps, dtype=np.float64), np.array(holds, dtype=np.int64))


def null_events(base: SimResult, window: Tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    """
    One random-entry null layout (INTERFACES 6.3): the strategy's own trades (direction,
    stop, target, hold) in permuted order, separated by random gaps across the window.
    Exit events whose bar lies at or past the window end are dropped: the window-end
    close happens first, so they could never act (and may lie outside the frame).
    """
    ws, we = int(window[0]), int(window[1])
    dirs, stops, tps, holds = _trade_shapes(base)
    k = len(dirs)
    if k == 0:
        return np.zeros(0, dtype=EVENTS_DTYPE)
    a = max(ws, 1)
    span = we - a
    slot_len = holds + 1
    free = max(0, span - int(slot_len.sum()))
    cuts = np.sort(rng.integers(0, free + 1, size=k))
    gaps = np.diff(np.concatenate([[0], cuts, [free]]))
    perm = rng.permutation(k)

    recs: List[Tuple[int, int, float, float, bool]] = []
    pos = a
    for m in range(k):
        pos += int(gaps[m])
        fill = pos
        t = int(perm[m])
        if fill >= we:
            break
        recs.append((fill - 1, int(dirs[t]), float(stops[t]), float(tps[t]), False))
        exit_bar = fill + int(holds[t]) - 1
        if exit_bar < we:
            recs.append((exit_bar, 0, np.nan, np.nan, True))
        pos = fill + int(slot_len[t])
    ev = np.zeros(len(recs), dtype=EVENTS_DTYPE)
    for i, rec in enumerate(recs):
        ev[i] = rec
    return ev


def null_metrics(simulate: SimulateFn, base: SimResult, window: Tuple[int, int], *, code_sha256: str,
                 stage: str, runs: int = gates.NULL_RUNS) -> Dict[str, Any]:
    """G4a: strategy net minus the 95th percentile of `runs` seeded random-entry nulls."""
    if not base.trades:
        return {"net_minus_null_pctl": None, "null_pctl_95": None, "null_rank_pct": None,
                "null_mean_trades": None,
                "detail": {"null": {"runs": 0, "pctl_95": None, "rank_pct": None, "mean_trades": None,
                                    "seed_scheme": NULL_SEED_SCHEME}}}
    win = (int(window[0]), int(window[1]))
    nets = np.empty(runs)
    n_trades = np.empty(runs)
    for r in range(runs):
        rng = np.random.default_rng(null_seed(code_sha256, stage, r))
        s = simulate(null_events(base, win, rng), SimOverrides(window=win))
        nets[r] = s.net
        n_trades[r] = len(s.trades)
    pctl = float(np.percentile(nets, gates.NULL_PERCENTILE))
    base_net = base.net
    rank = float(100.0 * np.mean(nets < base_net))
    mean_trades = float(n_trades.mean())
    return {
        "net_minus_null_pctl": base_net - pctl,
        "null_pctl_95": pctl,
        "null_rank_pct": rank,
        "null_mean_trades": mean_trades,
        "detail": {"null": {"runs": runs, "pctl_95": pctl, "rank_pct": rank, "mean_trades": mean_trades,
                            "seed_scheme": NULL_SEED_SCHEME}},
    }


# ── G7 parameter plateau ────────────────────────────────────────────────────
@dataclass(frozen=True)
class Neighbour:
    """One-at-a-time parameter move; params holds the FULL param set for that run."""

    run_id: str
    param: str
    factor: float
    params: Mapping[str, Number]


def _round_half_away(v: float) -> int:
    return int(math.copysign(math.floor(abs(v) + 0.5), v))


def plateau_neighbours(params_spec: Mapping[str, Mapping], defaults: Mapping[str, Number]) -> List[Neighbour]:
    """
    PARAMS order x PLATEAU_FACTORS order; ints rounded and moved by at least
    PLATEAU_MIN_INT_STEP; clipped to [min, max]; no-ops and duplicates skipped
    (a skipped neighbour does not consume an index).
    """
    out: List[Neighbour] = []
    seen = set()
    for name, entry in params_spec.items():
        is_int = entry.get("type") == "int"
        d = defaults[name]
        for factor in gates.PLATEAU_FACTORS:
            v: Number
            if is_int:
                v = _round_half_away(float(d) * factor)
                if v == int(d):
                    step = gates.PLATEAU_MIN_INT_STEP
                    v = int(d) - step if factor < 1 else int(d) + step
                v = int(min(max(v, int(entry["min"])), int(entry["max"])))
                if v == int(d):
                    continue
            else:
                v = float(min(max(float(d) * factor, float(entry["min"])), float(entry["max"])))
                if v == float(d):
                    continue
            params = dict(defaults)
            params[name] = v
            key = tuple(sorted(params.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(Neighbour(run_id=f"nb{len(out):02d}", param=name, factor=float(factor), params=params))
    return out


def plateau_metrics(simulate: SimulateFn, neighbour_events: Mapping[str, Optional[np.ndarray]],
                    neighbours: Sequence[Neighbour]) -> Dict[str, Any]:
    """G7 aggregates; per-neighbour numbers only in detail.plateau (server-only)."""
    rows = []
    for nb in neighbours:
        ev = neighbour_events.get(nb.run_id)
        if ev is None:
            rows.append({"run_id": nb.run_id, "param": nb.param, "factor": nb.factor,
                         "value": nb.params[nb.param], "failed": True, "trades": 0, "net": None, "pf": 0.0})
            continue
        s = simulate(ev, SimOverrides())
        rows.append({"run_id": nb.run_id, "param": nb.param, "factor": nb.factor,
                     "value": nb.params[nb.param], "failed": False, "trades": len(s.trades),
                     "net": s.net, "pf": _pf_of(s)})
    n = len(rows)
    if n == 0:
        pos_frac = median_pf = None
    else:
        pos_frac = sum(1 for r in rows if r["net"] is not None and r["net"] > 0) / n
        median_pf = float(np.median([_pf_num(r["pf"]) for r in rows]))
    return {
        "plateau_positive_frac": pos_frac,
        "plateau_median_pf": median_pf,
        "plateau_n": n,
        "detail": {"plateau": {"n": n, "neighbours": rows}},
    }


# ── G8 holdout ──────────────────────────────────────────────────────────────
def _part_stats(trades: Sequence[Trade], part: str) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0, "net": 0.0, "pf": None}
    mask = splits.window_mask(pd.DatetimeIndex([_ts(t.entry_time) for t in trades]), part)
    sel = [float(t.net_pnl) for t, m in zip(trades, mask) if m]
    return {"trades": len(sel), "net": float(sum(sel)), "pf": profit_factor(sel)}


def holdout_metrics(base: SimResult, stressed: SimResult, result: BacktestResult, *,
                    dev_headline: Mapping, first_bar: Any, last_bar: Any) -> Dict[str, Any]:
    """G8 margins against the DEV headline (never ratios), plus the H-A / H-B split by entry time."""
    trades = list(result.trades)
    sharpe = daily_sharpe(trades, first_bar=first_bar, last_bar=last_bar,
                          initial_balance=result.metadata.initial_balance)
    dd_frac = float(calculate_metrics(result).max_drawdown_pct) / 100.0
    dev_sharpe = dev_headline.get("daily_sharpe")
    dev_dd_pct = dev_headline.get("max_dd_pct")
    sharpe_margin = (None if sharpe is None or dev_sharpe is None
                     else sharpe - gates.MIN_HOLDOUT_SHARPE_RATIO * float(dev_sharpe))
    dd_margin = (None if dev_dd_pct is None
                 else dd_frac - gates.MAX_HOLDOUT_DD_RATIO * float(dev_dd_pct) / 100.0)
    return {
        "holdout_net": base.net,
        "holdout_pf": _pf_of(base),
        "holdout_pf_at_1_5x_cost": _pf_of(stressed),
        "holdout_sharpe": sharpe,
        "holdout_max_dd_frac": dd_frac,
        "holdout_sharpe_margin": sharpe_margin,
        "holdout_dd_margin": dd_margin,
        "h_a": _part_stats(trades, splits.H_A),
        "h_b": _part_stats(trades, splits.H_B),
    }
