"""Frozen Strategy Lab protocol (splits.py) and gate thresholds (gates.py) stay coherent."""

import dataclasses
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from sajim_quant.core.instruments import _SPECS
from sajim_quant.data.loader import DataLoader
from sajim_quant.lab import gates as G
from sajim_quant.lab import splits as S


# ── splits ──────────────────────────────────────────────────────────────────

def test_approved_calendar():
    assert S.DEV_START == datetime(2024, 9, 1)
    assert S.DEV_END == datetime(2025, 12, 31)
    assert (S.PURGE_START, S.PURGE_END) == (datetime(2026, 1, 1), datetime(2026, 1, 4))
    assert S.HOLDOUT_START == datetime(2026, 1, 5) and S.HOLDOUT_END is None
    assert S.MODEL_CUTOFF == datetime(2026, 6, 30)
    assert S.MIN_COVERAGE == 0.90
    for t in (S.DEV_START, S.DEV_END, S.PURGE_START, S.HOLDOUT_START, S.MODEL_CUTOFF, S.H_B_START):
        assert t.tzinfo is None


def test_dev_purge_holdout_contiguous_and_disjoint():
    dev, purge, hold = S.window("dev"), S.window("purge"), S.window("holdout")
    assert dev[1] == purge[0] and purge[1] == hold[0]
    assert dev[0] < dev[1] < purge[1] and hold[1] is None
    edges = {
        datetime(2024, 8, 31, 23, 55): None,
        S.DEV_START: "dev",
        datetime(2025, 12, 31, 23, 55): "dev",
        datetime(2026, 1, 1): "purge",
        datetime(2026, 1, 4, 23, 55): "purge",
        datetime(2026, 1, 5): "holdout",
        datetime(2026, 9, 13, 21): "holdout",
    }
    for t, want in edges.items():
        assert S.stage_of(t) == want, t
        assert sum(S.in_window(t, s) for s in ("dev", "purge", "holdout")) == (0 if want is None else 1)


def test_holdout_partitioned_at_model_cutoff():
    ha, hb, hold = S.window("h_a"), S.window("h_b"), S.window("holdout")
    assert ha[0] == hold[0] and ha[1] == hb[0] and hb[1] == hold[1] is None
    assert ha[1] == S.MODEL_CUTOFF + timedelta(days=1)
    times = pd.date_range("2025-12-30", "2026-07-03", freq="h")
    labels = S.split_holdout(times)
    in_hold = S.window_mask(times, "holdout")
    assert ((labels != None) == in_hold).all()           # noqa: E711 (object array)
    assert set(labels[in_hold]) == {"h_a", "h_b"}
    assert S.split_holdout([datetime(2026, 6, 30, 23, 59)]) == ["h_a"]
    assert S.split_holdout([datetime(2026, 7, 1)]) == ["h_b"]
    assert S.split_holdout([datetime(2026, 1, 4, 23)]) == [None]


def test_vectorised_matches_scalar_and_handles_tz():
    times = pd.date_range("2025-12-31 20:00", "2026-01-05 04:00", freq="15min")
    vec = S.window_mask(times, "purge")
    scalar = [S.in_window(t, "purge") for t in times]
    assert list(vec) == scalar
    aware = times.tz_localize("UTC").tz_convert("Asia/Tokyo")
    assert list(S.window_mask(aware, "purge")) == scalar
    assert list(S.window_mask(pd.Series(times), "purge")) == scalar
    assert S.to_utc_naive(datetime(2026, 1, 1, 2, tzinfo=timezone(timedelta(hours=3)))) == datetime(2025, 12, 31, 23)
    assert S.to_utc_naive(np.datetime64("2026-01-05T00:00")) == datetime(2026, 1, 5)
    assert S.to_utc_naive("2026-01-05T00:00:00Z") == datetime(2026, 1, 5)


def test_loader_bounds_keep_the_last_day():
    # DataLoader filters time <= end_date; DEV_END itself would drop 2025-12-31 after 00:00.
    start, end = S.loader_bounds("dev")
    assert start == S.DEV_START
    assert datetime(2025, 12, 31, 23, 55) <= end < datetime(2026, 1, 1)
    assert S.loader_bounds("holdout")[1] is None


def test_months_and_coverage():
    assert len(S.months("dev")) == 16
    assert S.months("dev")[0] == "2024-09" and S.months("dev")[-1] == "2025-12"
    assert S.months("h_a") == [f"2026-{m:02d}" for m in range(1, 7)]
    with pytest.raises(ValueError):
        S.months("holdout")
    assert S.months("h_b", last_bar=datetime(2026, 9, 11, 20, 55)) == ["2026-07", "2026-08", "2026-09"]
    # real cache shape: XAUUSD M5 runs 2024-09-01 22:00 .. 2026-09-11 20:55
    assert S.has_min_coverage("dev", "2024-09-01 22:00", "2026-09-11 20:55")
    # a symbol whose data ends 2025-04-19 (BTCUSD M1-like) is refused for DEV
    assert not S.has_min_coverage("dev", "2024-09-01", "2025-04-19")
    assert not S.has_min_coverage("holdout", "2026-01-05", "2026-03-01", reference_end="2026-09-13")
    assert DataLoader.SHORT_SPAN_RATIO == S.MIN_COVERAGE


def test_timeframes_and_symbols():
    assert S.ALLOWED_TIMEFRAMES == ("M5", "M15", "M30", "H1", "H4")
    for bad in ("M1", "D1", "W1", "", None, "H2"):
        assert not S.is_allowed_tf(bad)
    assert S.is_allowed_tf(" m15 ") and S.is_allowed_tf("H4")
    assert set(S.ALLOWED_TIMEFRAMES) <= set(DataLoader.SUPPORTED_TIMEFRAMES)
    assert len(S.ALLOWED_SYMBOLS) == 20 and set(S.ALLOWED_SYMBOLS) == set(_SPECS)
    assert S.is_allowed_symbol("xauusd") and not S.is_allowed_symbol("R_100")


def test_unknown_stage_rejected():
    with pytest.raises(ValueError):
        S.window("live")
    assert S.normalize_stage("H-A") == "h_a"
    assert set(S.SCORED_STAGES) == {"dev", "holdout"}


# ── gates ───────────────────────────────────────────────────────────────────

def test_versions_and_budgets():
    assert G.GATES_VERSION == "v1" and S.PROTOCOL_VERSION
    assert G.MAX_DEV_TRIALS_PER_FAMILY == 3
    assert G.MAX_HOLDOUT_TOUCHES_PER_FAMILY == 3
    assert G.MAX_HOLDOUT_RUNS_PER_SHA == 1
    assert G.INTEGRITY_CHECKS_ARE_TRIALS is False


def test_system_defaults():
    assert G.RISK_PCT == 0.01 and 0 < G.RISK_PCT <= G.MAX_RISK_PCT == 0.02
    assert G.INITIAL_BALANCE == 10_000.0 and G.ONE_POSITION is True
    assert G.RUIN_FLOOR == 0.10
    assert (G.MAX_PARAMS, G.MAX_PARAM_RANGE_RATIO, G.MAX_SOURCE_BYTES) == (5, 10, 64 * 1024)
    assert G.MAX_HOLD_BARS == G.MAX_WARMUP_BARS == 2000
    assert re.fullmatch(G.LAB_ID_PATTERN, "lab_asian_range_breakout_xauusd")
    assert not re.fullmatch(G.LAB_ID_PATTERN, "beep_vacuum_hunter")


def test_threshold_values_match_plan():
    assert G.MAX_UNTRADABLE_STOP_FRAC == 0.30
    assert (G.MIN_DEV_TRADES, G.MIN_DEV_MONTHS_WITH_TRADES_FRAC, G.MIN_HOLDOUT_TRADES) == (100, 0.70, 30)
    assert (G.MIN_DEV_PF, G.MAX_DEV_DD, G.MAX_TOP5_PROFIT_SHARE,
            G.MAX_SINGLE_MONTH_PROFIT_SHARE, G.MIN_SIDE_PF) == (1.20, 0.20, 0.30, 0.25, 0.9)
    assert (G.NULL_RUNS, G.NULL_PERCENTILE, G.MIN_PSR, G.MIN_DSR) == (300, 95.0, 0.95, 0.95)
    assert (G.N_FOLDS, G.MIN_POSITIVE_FOLDS, G.MIN_FOLD_PF) == (4, 3, 0.8)
    assert (G.COST_STRESS_NET_MULT, G.COST_STRESS_PF_MULT, G.MIN_PF_AT_2X_COST,
            G.DELAY_BARS, G.MIN_PF_DELAYED, G.BREAKEVEN_MULT_RANGE) == (1.5, 2.0, 1.0, 1, 1.0, (0.5, 4.0))
    assert (G.PLATEAU_FACTORS, G.MIN_PLATEAU_POSITIVE_FRAC, G.MIN_PLATEAU_MEDIAN_PF) == ((0.8, 1.2), 0.70, 1.10)
    assert (G.MIN_HOLDOUT_PF, G.HOLDOUT_COST_STRESS_MULT, G.MIN_HOLDOUT_PF_STRESSED,
            G.MIN_HOLDOUT_SHARPE_RATIO, G.MAX_HOLDOUT_DD_RATIO) == (1.10, 1.5, 1.0, 0.5, 1.5)
    assert (G.MC_BLOCK_TRADES, G.MC_PATHS, G.MC_DD_LEVEL, G.MAX_MC_P_DD, G.MAX_MC_P95_WORST_DD) == \
        (10, 2000, 0.20, 0.15, 0.30)


def test_thresholds_are_sane():
    # stressed / out-of-sample floors never exceed the base DEV floor
    assert 1.0 <= G.MIN_PF_AT_2X_COST <= G.MIN_DEV_PF
    assert 1.0 <= G.MIN_PF_DELAYED <= G.MIN_DEV_PF
    assert 1.0 <= G.MIN_HOLDOUT_PF_STRESSED <= G.MIN_HOLDOUT_PF <= G.MIN_DEV_PF
    assert G.MIN_PLATEAU_MEDIAN_PF <= G.MIN_DEV_PF
    assert G.MIN_FOLD_PF < 1.0 <= G.MIN_DEV_PF and G.MIN_SIDE_PF < 1.0
    assert 1.0 < G.COST_STRESS_NET_MULT < G.COST_STRESS_PF_MULT <= G.BREAKEVEN_MULT_RANGE[1]
    assert G.BREAKEVEN_MULT_RANGE[0] < 1.0
    assert G.MIN_POSITIVE_FOLDS <= G.N_FOLDS
    assert G.MAX_SINGLE_MONTH_PROFIT_SHARE < G.MAX_TOP5_PROFIT_SHARE < 1.0
    assert G.MC_DD_LEVEL == G.MAX_DEV_DD < G.MAX_MC_P95_WORST_DD
    assert G.MAX_DEV_DD * G.MAX_HOLDOUT_DD_RATIO <= G.MAX_MC_P95_WORST_DD + 1e-12   # 0.2*1.5 is 0.30000000000000004
    assert G.MIN_HOLDOUT_TRADES < G.MIN_DEV_TRADES
    assert all(0 < c < 1 for c in G.CAUSALITY_CUTS) and list(G.CAUSALITY_CUTS) == sorted(G.CAUSALITY_CUTS)
    assert min(G.PLATEAU_FACTORS) < 1 < max(G.PLATEAU_FACTORS)
    assert G.RUIN_FLOOR < 1 - G.MAX_DEV_DD           # the floor halts long after G3 would fail
    assert G.MIN_PARAMS <= G.MAX_PARAMS
    # >= 70% of the 16 DEV months means at least 12 months with trades (11/16 = 0.6875 fails)
    n = len(S.months("dev"))
    need = next(k for k in range(n + 1) if k / n >= G.MIN_DEV_MONTHS_WITH_TRADES_FRAC)
    assert (n, need) == (16, 12)
    assert G.CAUSALITY_CUTS == (0.20, 0.40, 0.60, 0.80, 0.95) and G.CAUSALITY_ATOL == 1e-9
    assert G.DETERMINISM_RUNS == 2 and G.MIN_SIGNALS == 1


def test_gate_specs_iterable_and_frozen():
    ids = [g.id for g in G.GATES]
    assert ids == ["G0", "G1", "G2", "G3", "G4a", "G4b", "G5", "G6", "G7", "G8", "G9", "G10", "G11"]
    assert len(set(ids)) == len(ids) and set(G.GATES_BY_ID) == set(ids)
    for g in G.GATES:
        assert g.on_fail in (None,) + G.VERDICTS
        assert (g.on_fail is None) == (not g.hard)
        assert g.phase in (1, 2)
        assert g.hard == bool(g.rules), g.id
    assert G.GATES_BY_ID["G2"].on_fail == G.NOT_PROVEN
    assert {g.id for g in G.GATES if g.phase == 2} == {"G4b", "G9", "G10"}
    assert len(G.active_gates(1)) == len(G.GATES) - 3 and len(G.active_gates(2)) == len(G.GATES)
    with pytest.raises(dataclasses.FrozenInstanceError):
        G.GATES[0].hard = False
    with pytest.raises(TypeError):
        G.GATES_BY_ID["G0"].params["engine"] = "x"


def test_rules_compare_with_frozen_direction():
    top5 = next(r for r in G.GATES_BY_ID["G3"].rules if r.metric == "top5_profit_share")
    assert not top5.passes(0.30) and top5.passes(0.2999)          # strictly less
    pf = next(r for r in G.GATES_BY_ID["G3"].rules if r.metric == "pf")
    assert pf.passes(1.20) and not pf.passes(1.19)
    assert not pf.passes(None) and not pf.passes(float("nan"))
    with pytest.raises(ValueError):
        G.Rule("x", "=>", 1.0)


def test_every_rule_matches_plan_text():
    """Every (metric, op, value) per gate, transcribed from docs/STRATEGY_LAB_PLAN.md evaluation_gates."""
    want = {
        "G0": [("integrity_checks_passed", "==", 1), ("cost_model_checks_passed", "==", 1),
               ("slippage_points", ">", 0.0), ("untradable_stop_frac", "<=", 0.30), ("n_signals", ">=", 1)],
        "G1": [("n_params", "<=", 5), ("n_params", ">=", 1)],
        "G2": [("dev_trades", ">=", 100), ("dev_months_with_trades_frac", ">=", 0.70),
               ("holdout_trades", ">=", 30)],
        "G3": [("pf", ">=", 1.20), ("expectancy", ">", 0.0), ("max_dd_frac", "<=", 0.20),
               ("top5_profit_share", "<", 0.30), ("max_month_profit_share", "<=", 0.25),
               ("min_side_pf", ">=", 0.9)],
        "G4a": [("net_minus_null_pctl", ">", 0.0)],
        "G4b": [("psr", ">=", 0.95), ("dsr", ">=", 0.95)],
        "G5": [("positive_folds", ">=", 3), ("min_fold_pf", ">=", 0.8)],
        "G6": [("net_at_1_5x_cost", ">", 0.0), ("pf_at_2x_cost", ">=", 1.0), ("pf_delay_1", ">=", 1.0)],
        "G7": [("plateau_positive_frac", ">=", 0.70), ("plateau_median_pf", ">=", 1.10)],
        "G8": [("holdout_net", ">", 0.0), ("holdout_pf", ">=", 1.10), ("holdout_pf_at_1_5x_cost", ">=", 1.0),
               ("holdout_sharpe_margin", ">=", 0.0), ("holdout_dd_margin", "<=", 0.0)],
        "G9": [("mc_p5_terminal_over_initial", ">", 1.0), ("mc_p_dd_ge_level", "<=", 0.15),
               ("mc_p95_worst_dd", "<=", 0.30)],
        "G10": [("profitable_nodes_beyond_primary", ">=", 1)],
        "G11": [],
    }
    got = {g.id: [(r.metric, r.op, r.value) for r in g.rules] for g in G.GATES}
    assert got == want
    assert G.GATES_BY_ID["G4a"].params["runs"] == 300 and G.GATES_BY_ID["G4a"].params["percentile"] == 95.0
    assert G.GATES_BY_ID["G5"].params["n_folds"] == 4
    assert (G.GATES_BY_ID["G9"].params["block_trades"], G.GATES_BY_ID["G9"].params["paths"]) == (10, 2000)
    # boundary behaviour of every strict rule
    for g in G.GATES:
        for r in g.rules:
            if r.op in (">", "<"):
                assert not r.passes(r.value), (g.id, r.metric)


def test_loader_bounds_against_real_dataloader(tmp_path):
    # Exercise DataLoader's own `time <= end_date` filter on a synthetic naive-UTC M5 parquet.
    t = pd.date_range("2025-12-30 22:00", "2026-01-05 01:00", freq="5min")
    df = pd.DataFrame({"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    (tmp_path / "TEST").mkdir()
    df.to_parquet(tmp_path / "TEST" / "M5.parquet", index=False)
    L = DataLoader(parquet_root=tmp_path, csv_root=tmp_path)
    s, e = S.loader_bounds("dev")
    dev = L.load("TEST", "M5", start_date=s, end_date=e)
    assert dev.index.tz is None
    assert dev.index[-1] == pd.Timestamp("2025-12-31 23:55")
    assert S.window_mask(dev.index, "dev").all()
    naive = L.load("TEST", "M5", start_date=S.DEV_START, end_date=S.DEV_END)
    assert naive.index[-1] == pd.Timestamp("2025-12-31 00:00")        # the gotcha loader_bounds avoids
    hold = L.load("TEST", "M5", start_date=S.loader_bounds("holdout")[0])
    assert hold.index[0] == pd.Timestamp("2026-01-05 00:00")
    assert S.window_mask(hold.index, "holdout").all()


def test_verdict_words():
    assert G.VERDICTS == ("REJECT", "NOT_PROVEN", "CANDIDATE")
    for v in G.VERDICTS:
        assert v not in G.FORBIDDEN_LABELS
        assert not G.contains_forbidden_label(v)
    assert G.FORBIDDEN_LABELS == ("ELITE", "OPTIMAL", "Tier 1", "edge found")
    assert G.contains_forbidden_label("an elite strategy") and G.contains_forbidden_label("TIER 1")
    assert not G.contains_forbidden_label(G.CANDIDATE_MEANING)


def test_critic_can_only_downgrade():
    assert G.apply_downgrade(G.CANDIDATE, G.NOT_PROVEN) == G.NOT_PROVEN
    assert G.apply_downgrade(G.NOT_PROVEN, G.CANDIDATE) == G.NOT_PROVEN
    assert G.apply_downgrade(G.REJECT, G.CANDIDATE) == G.REJECT


def test_describe_blocks_are_json_safe():
    import json
    blob = json.dumps({"protocol": S.describe(), "gates": G.describe()})
    assert '"protocol_version": "v1"' in blob and '"gates_version": "v1"' in blob


def test_post_p0_user_decisions():
    """Decisions taken by the user after P0 (2026-10-12)."""
    # Budget exhausted with an adequate sample -> REJECT, not NOT_PROVEN.
    assert G.BUDGET_EXHAUSTED_VERDICT == G.REJECT
    # Zero-based params: ints up to 23 (UTC hours); ratio rule unchanged for min > 0.
    assert G.MAX_ZERO_BASED_INT_MAX == 23 and G.MAX_PARAM_RANGE_RATIO == 10
    # Rollover flag is a half-open hour window that includes the 23:xx hour.
    lo, hi = G.ROLLOVER_FLAG_UTC_HOURS
    assert (lo, hi) == (21, 24) and all(lo <= h < hi for h in (21, 22, 23)) and not (lo <= 20 < hi)
    # G8 compares Sharpe/DD as margins against the frozen ratios (no division by DEV DD).
    assert (G.MIN_HOLDOUT_SHARPE_RATIO, G.MAX_HOLDOUT_DD_RATIO) == (0.5, 1.5)
    metrics = {r.metric for r in G.GATES_BY_ID["G8"].rules}
    assert {"holdout_sharpe_margin", "holdout_dd_margin"} <= metrics
    assert not any("over_dev" in m for m in metrics)
