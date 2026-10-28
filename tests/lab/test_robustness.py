"""robustness.py: metric definitions behind every gate rule (synthetic data, fake simulate closures)."""

import hashlib
import math

import numpy as np
import pandas as pd
import pytest

from sajim_quant.core.instruments import get_spec
from sajim_quant.core.schema import BacktestResult, Direction, ExitReason, RunMetadata, Trade
from sajim_quant.lab import gates, robustness as R, splits
from sajim_quant.lab.interfaces import EVENTS_DTYPE, CheckReport, SimOverrides, SimResult

XAU = get_spec("XAUUSD")


def trade(net, entry, exit_, *, direction=Direction.BUY, entry_bar=10, exit_bar=12, stop=5.0, tp=10.0, mae=None):
    return Trade(trade_id=f"t{entry}", strategy_name="lab_x", engine=gates.ENGINE, symbol="XAUUSD",
                 timeframe="H1", direction=direction, entry_time=entry, entry_price=2000.0, position_size=0.1,
                 exit_time=exit_, exit_price=2000.0, exit_reason=ExitReason.TAKE_PROFIT, gross_pnl=net,
                 net_pnl=net, mae=mae,
                 custom_fields={"signal_bar": entry_bar - 1, "entry_bar": entry_bar, "exit_bar": exit_bar,
                                "stop_dist": stop, "tp_dist": tp, "quote_rate": 1.0, "cost_mult": 1.0})


def sim_result(trades, **kw):
    return SimResult(trades=list(trades), equity=pd.Series(dtype=float), untradable_stop=kw.get("untradable", 0),
                     n_signals=kw.get("n_signals", len(trades)), n_entry_attempts=kw.get("attempts", len(trades)),
                     window_end_closes=0, halted=False)


def result_of(trades, engine=gates.ENGINE, commission_model=gates.COMMISSION_MODEL):
    meta = RunMetadata(strategy_name="lab_x", engine=engine, symbol="XAUUSD", timeframe="H1",
                       start_date="2024-09-02T00:00:00", end_date="2024-12-31T23:00:00",
                       initial_balance=10_000.0, commission_model=commission_model)
    return BacktestResult(metadata=meta, trades=list(trades))


# ── shared definitions ──────────────────────────────────────────────────────

def test_profit_factor_definitions():
    assert R.profit_factor([]) is None
    assert R.profit_factor([0.0, 0.0]) is None
    assert R.profit_factor([5.0, 1.0]) == math.inf
    assert R.profit_factor([30.0, -10.0, -5.0]) == pytest.approx(2.0)
    assert R.profit_factor([-1.0]) == 0.0


def test_daily_sharpe_zero_filled_business_days_and_weekend_roll():
    # Mon 2024-09-02 .. Fri 2024-09-06: 5 business days; a Saturday exit counts on Monday 09-09
    trades = [trade(100.0, "2024-09-02T10:00:00", "2024-09-03T10:00:00"),
              trade(-50.0, "2024-09-05T10:00:00", "2024-09-07T10:00:00")]      # Saturday -> clamped to Fri 09-06
    got = R.daily_sharpe(trades, first_bar="2024-09-02T00:00:00", last_bar="2024-09-06T23:00:00",
                         initial_balance=10_000.0)
    r = np.array([0.0, 100 / 10_000, 0.0, 0.0, -50 / 10_100])
    assert got == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    # weekend roll inside the range goes to the NEXT business day
    t2 = [trade(100.0, "2024-09-06T10:00:00", "2024-09-07T10:00:00")]
    a = R.daily_sharpe(t2, first_bar="2024-09-02", last_bar="2024-09-10", initial_balance=10_000.0)
    r2 = np.zeros(7)
    r2[5] = 0.01                                     # Mon 09-09 is the 6th business day
    assert a == pytest.approx(r2.mean() / r2.std(ddof=1) * math.sqrt(252))
    assert R.daily_sharpe([], first_bar="2024-09-02", last_bar="2024-09-02", initial_balance=1e4) is None
    assert R.daily_sharpe([], first_bar="2024-09-02", last_bar="2024-09-09", initial_balance=1e4) == 0.0


def test_performance_metrics_hand_computed():
    trades = [
        trade(400.0, "2024-09-02T08:00:00", "2024-09-02T12:00:00"),
        trade(-100.0, "2024-09-10T22:00:00", "2024-09-11T02:00:00"),           # rollover-hour entry
        trade(300.0, "2024-10-01T08:00:00", "2024-10-01T09:00:00", direction=Direction.SELL),
        trade(-200.0, "2024-10-02T08:00:00", "2024-10-03T08:00:00", direction=Direction.SELL),
        trade(100.0, "2024-11-04T08:00:00", "2024-11-04T10:00:00"),
        trade(50.0, "2024-11-05T08:00:00", "2024-11-05T10:00:00"),
    ]
    p = R.performance_metrics(result_of(trades), months=["2024-09", "2024-10", "2024-11", "2024-12"],
                              first_bar="2024-09-02T00:00:00", last_bar="2024-12-31T23:00:00")
    assert p["trades"] == 6 and p["net"] == pytest.approx(550.0)
    assert p["pf"] == pytest.approx(850.0 / 300.0)
    assert p["win_rate"] == pytest.approx(4 / 6)
    assert p["return_pct"] == pytest.approx(5.5)
    # top-5 winners/losers by net: 400+300+100+50-100 = 750
    assert p["top5_profit_share"] == pytest.approx(750.0 / 550.0)
    # exit months: Sep 300, Oct 100, Nov 150
    assert p["max_month_profit_share"] == pytest.approx(300.0 / 550.0)
    assert (p["long_trades"], p["short_trades"]) == (4, 2)
    assert p["long_pf"] == pytest.approx(550.0 / 100.0) and p["short_pf"] == pytest.approx(1.5)
    assert p["min_side_pf"] == pytest.approx(1.5)
    assert p["months_with_trades_frac"] == pytest.approx(3 / 4)
    assert p["rollover_entry_share"] == pytest.approx(1 / 6)
    assert p["max_dd_frac"] == pytest.approx(p["max_dd_pct"] / 100)
    assert p["median_hold_days"] == pytest.approx(np.median([4, 4, 1, 24, 2, 2]) / 24)


def test_performance_metrics_losing_or_empty_run():
    p = R.performance_metrics(result_of([trade(-10.0, "2024-09-02T08:00:00", "2024-09-02T09:00:00")]),
                              months=["2024-09"], first_bar="2024-09-02", last_bar="2024-09-30")
    assert p["top5_profit_share"] is None and p["max_month_profit_share"] is None
    assert p["pf"] == 0.0 and p["min_side_pf"] == 0.0
    e = R.performance_metrics(result_of([]), months=["2024-09"], first_bar="2024-09-02", last_bar="2024-09-30")
    assert e["trades"] == 0 and e["pf"] is None and e["min_side_pf"] is None


# ── G0 / G1 / G2 ────────────────────────────────────────────────────────────

def _check(passed=True):
    return CheckReport(passed=passed, reasons=(), checks={}, n_signals=3, base_events=None)


def test_integrity_metrics_full_and_failed():
    s = sim_result([trade(1.0, "2024-09-02T08:00:00", "2024-09-02T09:00:00")], untradable=1, attempts=4,
                   n_signals=5)
    m = R.integrity_metrics(_check(), s, result=result_of(s.trades), fx_desc="quote currency USD (no conversion)",
                            spec=XAU, n_params=3)
    assert m == {"integrity_checks_passed": 1.0, "n_params": 3, "slippage_points": 5.0,
                 "cost_model_checks_passed": 1.0, "untradable_stop_frac": 0.25, "n_signals": 5}
    bad_fx = R.integrity_metrics(_check(), s, result=result_of(s.trades), fx_desc="UNCONVERTED: no route",
                                 spec=XAU, n_params=3)
    assert bad_fx["cost_model_checks_passed"] == 0.0
    zero = R.integrity_metrics(_check(), s, result=result_of(s.trades, commission_model="zero"),
                               fx_desc="ok", spec=XAU, n_params=3)
    assert zero["cost_model_checks_passed"] == 0.0
    failed = R.integrity_metrics(_check(False), None, result=None, fx_desc="", spec=XAU, n_params=2)
    assert set(failed) == {"integrity_checks_passed", "n_params", "slippage_points"}
    assert failed["integrity_checks_passed"] == 0.0


def test_sample_metrics_dev_and_holdout():
    s = sim_result([trade(1.0, "2024-09-02T08:00:00", "x"), trade(1.0, "2024-09-20T08:00:00", "x"),
                    trade(1.0, "2025-01-02T08:00:00", "x")])
    dev = R.sample_metrics(s, "dev", months=splits.months("dev"))
    assert dev == {"dev_trades": 3, "dev_months_with_trades_frac": pytest.approx(2 / 16)}
    assert R.sample_metrics(s, "holdout", months=[]) == {"holdout_trades": 3}


# ── G6 stress and break-even ────────────────────────────────────────────────

def fake_simulate(net_of_mult, pf_of=lambda o: 1.5):
    """simulate stand-in: one winning and one losing trade whose net depends on the overrides."""
    def simulate(events, o: SimOverrides):
        net = net_of_mult(o)
        pf = pf_of(o)
        loss = -100.0
        win = net - loss
        return sim_result([trade(win, "2024-09-02T08:00:00", "2024-09-02T09:00:00"),
                           trade(loss, "2024-09-03T08:00:00", "2024-09-03T09:00:00")])
    return simulate


def test_breakeven_bisection_and_flags():
    sim = fake_simulate(lambda o: 100.0 - 50.0 * o.cost_mult)          # zero at 2.0
    assert R.breakeven_cost_mult(sim, None) == (2.0, "in_range")
    assert R.breakeven_cost_mult(fake_simulate(lambda o: -1.0), None) == (0.5, "below_range")
    assert R.breakeven_cost_mult(fake_simulate(lambda o: 5.0), None) == (4.0, "above_range")


def test_stress_metrics_uses_frozen_overrides():
    seen = []

    def sim(events, o):
        seen.append(o)
        return sim_result([trade(100.0 - 40.0 * o.cost_mult - 30.0 * o.delay_bars, "2024-09-02T08:00:00", "x"),
                           trade(-10.0, "2024-09-03T08:00:00", "x")])
    m = R.stress_metrics(sim, None)
    assert seen[:3] == [SimOverrides(cost_mult=1.5), SimOverrides(cost_mult=2.0), SimOverrides(delay_bars=1)]
    assert m["net_at_1_5x_cost"] == pytest.approx(100 - 60 - 10)
    assert m["pf_at_2x_cost"] == pytest.approx(20 / 10)
    assert m["pf_delay_1"] == pytest.approx(30 / 10)
    assert m["breakeven_flag"] == "in_range" and 2.0 <= m["breakeven_cost_mult"] <= 2.3
    assert set(m["detail"]) == {"stress"}


# ── G5 folds ────────────────────────────────────────────────────────────────

def test_fold_bounds_equal_bar_counts():
    assert R.fold_bounds((0, 100)) == [(0, 25), (25, 50), (50, 75), (75, 100)]
    assert R.fold_bounds((10, 21)) == [(10, 12), (12, 15), (15, 18), (18, 21)]


def test_fold_metrics_counts_empty_fold_as_pf_zero():
    nets = {(0, 25): 50.0, (25, 50): 20.0, (50, 75): None, (75, 100): 10.0}

    def sim(events, o):
        n = nets[o.window]
        if n is None:
            return sim_result([])
        return sim_result([trade(n + 5, "2024-09-02T08:00:00", "x"), trade(-5.0, "2024-09-02T09:00:00", "x")])
    m = R.fold_metrics(sim, None, (0, 100))
    assert m["positive_folds"] == 3 and m["min_fold_pf"] == 0.0
    assert [f["start_bar"] for f in m["detail"]["folds"]] == [0, 25, 50, 75]


# ── G4a null ────────────────────────────────────────────────────────────────

def test_null_seed_is_frozen():
    sha = "ab" * 32
    want = int.from_bytes(hashlib.sha256(f"sajim.lab.null/1|{sha}|dev|7".encode()).digest()[:8], "big")
    assert R.null_seed(sha, "dev", 7) == want
    assert R.null_seed(sha, "dev", 7) != R.null_seed(sha, "dev", 8)


def _base_with_shapes():
    ts = []
    for k, (d, hold) in enumerate([(Direction.BUY, 3), (Direction.SELL, 5), (Direction.BUY, 1), (Direction.BUY, 8)]):
        ts.append(trade(1.0, "2024-09-02T08:00:00", "x", direction=d, entry_bar=100 + 20 * k,
                        exit_bar=100 + 20 * k + hold - 1, stop=2.0 + k, tp=None if k == 2 else 4.0 + k))
    return sim_result(ts)


def test_null_events_match_trade_shapes_and_stay_in_window():
    base = _base_with_shapes()
    for r in range(20):
        ev = R.null_events(base, (50, 400), np.random.default_rng(R.null_seed("0" * 64, "dev", r)))
        assert ev.dtype == EVENTS_DTYPE
        assert (np.diff(ev["bar"]) > 0).all()
        entries = ev[ev["signal"] != 0]
        assert len(entries) == 4                                  # trade count matches
        assert sorted(entries["signal"].tolist()) == [-1, 1, 1, 1]  # long/short mix matches
        assert sorted(entries["stop_dist"].tolist()) == [2.0, 3.0, 4.0, 5.0]
        assert np.isnan(entries["tp_dist"]).sum() == 1
        fills = entries["bar"] + 1
        assert (fills >= 50).all() and (fills < 400).all()
        exits = ev[ev["exit"]]
        assert len(exits) == 4 and np.isnan(exits["stop_dist"]).all()
        holds = sorted((exits["bar"] - fills + 1).tolist())
        assert holds == [1, 3, 5, 8]                              # hold distribution matches


def test_null_events_deterministic_and_crowded_window():
    base = _base_with_shapes()
    a = R.null_events(base, (0, 300), np.random.default_rng(5))
    b = R.null_events(base, (0, 300), np.random.default_rng(5))
    assert a.tobytes() == b.tobytes()
    assert a["bar"].min() >= 0                                    # a = max(ws, 1): signal bar >= 0
    tight = R.null_events(base, (0, 12), np.random.default_rng(1))  # slots overflow: dropped, never past the window
    assert (tight["bar"] < 12).all()


def test_null_metrics_with_real_simulator():
    pytest.importorskip("sajim_quant.lab.simulator")
    from sajim_quant.lab.simulator import SignalSimulator
    rng = np.random.default_rng(3)
    n = 3000
    close = 2000 + np.cumsum(rng.normal(0, 2, n))
    open_ = np.r_[2000.0, close[:-1]]
    frame = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + 1, "low": np.minimum(open_, close) - 1,
                          "close": close, "volume": 1.0},
                         index=pd.date_range("2024-09-02", periods=n, freq="1h", name="time"))

    def simulate(ev, o):
        return SignalSimulator(XAU, cost_mult=o.cost_mult, delay_bars=o.delay_bars, max_hold_bars=10,
                               allow_short=True, window=o.window or (0, n)).run(frame, ev)
    # "Oracle" strategy: knows the next 5 bars (a planted look-ahead), so it must beat the null.
    picks = np.arange(20, n - 10, 40)
    ev = np.zeros(len(picks), dtype=EVENTS_DTYPE)
    ev["bar"] = picks
    ev["signal"] = np.where(close[picks + 5] > open_[picks + 1], 1, -1)
    ev["stop_dist"] = 30.0
    ev["tp_dist"] = np.nan
    base = simulate(ev, SimOverrides())
    m = R.null_metrics(simulate, base, (0, n), code_sha256="1" * 64, stage="dev", runs=40)
    assert m["net_minus_null_pctl"] > 0 and m["null_rank_pct"] > 95
    assert m["null_mean_trades"] == pytest.approx(len(base.trades), rel=0.2)
    assert m["detail"]["null"]["seed_scheme"] == "sajim.lab.null/1"
    again = R.null_metrics(simulate, base, (0, n), code_sha256="1" * 64, stage="dev", runs=40)
    assert again["null_pctl_95"] == m["null_pctl_95"]             # seeded: reproducible


def test_null_metrics_no_trades_fails():
    m = R.null_metrics(lambda e, o: None, sim_result([]), (0, 100), code_sha256="0" * 64, stage="dev")
    assert "net_minus_null_pctl" in m and m["net_minus_null_pctl"] is None


# ── G7 plateau ──────────────────────────────────────────────────────────────

SPEC = {
    "n": {"default": 3, "min": 2, "max": 4, "type": "int"},
    "h": {"default": 0, "min": 0, "max": 23, "type": "int"},
    "rr": {"default": 2.0, "min": 1.0, "max": 2.2, "type": "float"},
    "k": {"default": 1.0, "min": 1.0, "max": 3.0, "type": "float"},
}


def test_plateau_neighbours_rules():
    defaults = {k: v["default"] for k, v in SPEC.items()}
    nbs = R.plateau_neighbours(SPEC, defaults)
    got = [(nb.run_id, nb.param, nb.factor, nb.params[nb.param]) for nb in nbs]
    assert got == [
        ("nb00", "n", 0.8, 2),        # 2.4 -> 2
        ("nb01", "n", 1.2, 4),        # 3.6 -> 4
        ("nb02", "h", 1.2, 1),        # 0*0.8 -> -1 -> clipped to 0 == default: skipped; 0*1.2 -> +1
        ("nb03", "rr", 0.8, 1.6),
        ("nb04", "rr", 1.2, 2.2),     # 2.4 clipped to max 2.2
        ("nb05", "k", 1.2, 1.2),      # 0.8 clipped to min 1.0 == default: skipped
    ]
    for nb in nbs:                    # one param moved at a time, the rest at default
        assert {k: v for k, v in nb.params.items() if k != nb.param} == {k: v for k, v in defaults.items()
                                                                         if k != nb.param}
    assert isinstance(nbs[0].params["n"], int) and len(nbs) <= 2 * gates.MAX_PARAMS


def test_plateau_metrics_aggregates_and_failed_runs():
    nbs = R.plateau_neighbours({"a": {"default": 10, "min": 5, "max": 20, "type": "int"},
                                "b": {"default": 1.0, "min": 0.5, "max": 2.0, "type": "float"}},
                               {"a": 10, "b": 1.0})
    nets = {0: 30.0, 1: -10.0, 2: 50.0}           # "events" here are just keys into nets

    def sim(ev, o):
        n = nets[int(ev)]
        return sim_result([trade(n + 10, "2024-09-02T08:00:00", "x"), trade(-10.0, "2024-09-02T09:00:00", "x")])
    m = R.plateau_metrics(sim, {"nb00": 0, "nb01": 1, "nb02": 2, "nb03": None}, nbs)
    assert m["plateau_n"] == 4
    assert m["plateau_positive_frac"] == pytest.approx(2 / 4)             # failed run counts as not positive
    assert m["plateau_median_pf"] == pytest.approx(np.median([4.0, 0.0, 6.0, 0.0]))
    assert [r["failed"] for r in m["detail"]["plateau"]["neighbours"]] == [False, False, False, True]
    empty = R.plateau_metrics(sim, {}, [])
    assert empty["plateau_positive_frac"] is None and empty["plateau_median_pf"] is None


# ── G8 holdout ──────────────────────────────────────────────────────────────

def test_holdout_metrics_margins_and_split():
    trades = [trade(200.0, "2026-02-02T08:00:00", "2026-02-02T12:00:00"),
              trade(-100.0, "2026-06-30T23:00:00", "2026-07-01T02:00:00"),      # entry in H-A
              trade(-50.0, "2026-07-06T08:00:00", "2026-07-06T09:00:00")]       # H-B
    res = result_of(trades)
    stressed = sim_result([trade(150.0, "2026-02-02T08:00:00", "x"), trade(-125.0, "2026-06-30T23:00:00", "x")])
    hm = R.holdout_metrics(sim_result(trades), stressed, res, dev_headline={"daily_sharpe": 1.0, "max_dd_pct": 4.0},
                           first_bar="2026-01-05T00:00:00", last_bar="2026-07-31T23:00:00")
    assert hm["holdout_net"] == pytest.approx(50.0)
    assert hm["holdout_pf"] == pytest.approx(200 / 150)
    assert hm["holdout_pf_at_1_5x_cost"] == pytest.approx(150 / 125)
    assert hm["holdout_sharpe_margin"] == pytest.approx(hm["holdout_sharpe"] - 0.5)
    assert hm["holdout_dd_margin"] == pytest.approx(hm["holdout_max_dd_frac"] - 1.5 * 0.04)
    assert hm["h_a"] == {"trades": 2, "net": 100.0, "pf": 2.0}
    assert hm["h_b"] == {"trades": 1, "net": -50.0, "pf": 0.0}
    none = R.holdout_metrics(sim_result(trades), stressed, res, dev_headline={"daily_sharpe": None, "max_dd_pct": None},
                             first_bar="2026-01-05", last_bar="2026-07-31")
    assert none["holdout_sharpe_margin"] is None and none["holdout_dd_margin"] is None
