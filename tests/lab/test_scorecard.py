"""scorecard.py: gate evaluation, verdict precedence, scorecard schema, builder view, critic."""

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from sajim_quant.lab import gates, scorecard as S, splits
from sajim_quant.lab.interfaces import BudgetInfo, StageFrame

sys.path.insert(0, str(Path(__file__).parent))
from _judging_testkit import PLANTED_SRC, submission  # noqa: E402

#: Every Phase-1 metric at a passing value (DEV).
DEV_PASS = {
    "integrity_checks_passed": 1.0, "cost_model_checks_passed": 1.0, "slippage_points": 5.0,
    "untradable_stop_frac": 0.0, "n_signals": 200, "n_params": 3,
    "dev_trades": 150, "dev_months_with_trades_frac": 1.0,
    "pf": 1.5, "expectancy": 10.0, "max_dd_frac": 0.08, "top5_profit_share": 0.1,
    "max_month_profit_share": 0.2, "min_side_pf": 1.4,
    "net_minus_null_pctl": 500.0,
    "positive_folds": 4, "min_fold_pf": 1.1,
    "net_at_1_5x_cost": 300.0, "pf_at_2x_cost": 1.1, "pf_delay_1": 1.2,
    "plateau_positive_frac": 1.0, "plateau_median_pf": 1.3,
}
HOLDOUT_PASS = {"holdout_trades": 40, "holdout_net": 100.0, "holdout_pf": 1.3, "holdout_pf_at_1_5x_cost": 1.1,
                "holdout_sharpe_margin": 0.2, "holdout_dd_margin": -0.01}


def with_(base, **kw):
    d = dict(base)
    d.update(kw)
    return d


def by_id(results):
    return {g.id: g for g in results}


# ── evaluate_gates ──────────────────────────────────────────────────────────

def test_absent_metric_is_not_evaluated_and_none_fails():
    r = by_id(S.evaluate_gates({"pf": None, "expectancy": 5.0}))
    g3 = r["G3"]
    rules = {x.metric: x for x in g3.rules}
    assert rules["pf"].passed is False and rules["pf"].value is None
    assert rules["expectancy"].passed is True
    assert rules["max_dd_frac"].passed is None
    assert g3.passed is False
    assert r["G0"].passed is None                       # nothing evaluated
    assert r["G11"].passed is None and r["G11"].hard is False


def test_inactive_phase2_gates_never_pass_or_fail():
    r = by_id(S.evaluate_gates(with_(DEV_PASS, psr=0.1, dsr=0.1)))
    assert r["G4b"].active is False and r["G4b"].passed is None
    assert r["G9"].passed is None and r["G10"].passed is None
    assert [g.id for g in S.evaluate_gates({})] == [g.id for g in gates.GATES]


def test_inf_pf_passes():
    r = by_id(S.evaluate_gates(with_(DEV_PASS, pf=math.inf, min_side_pf=math.inf)))
    assert r["G3"].passed is True


def test_decisive_rule():
    g3 = by_id(S.evaluate_gates(with_(DEV_PASS, max_dd_frac=0.3)))["G3"]
    assert S.decisive_rule(g3).metric == "max_dd_frac"
    g3ok = by_id(S.evaluate_gates(DEV_PASS))["G3"]
    assert S.decisive_rule(g3ok).metric == "pf"
    assert S.decisive_rule(by_id(S.evaluate_gates({}))["G3"]) is None


# ── verdict precedence ──────────────────────────────────────────────────────

def v(metrics, stage="dev", exhausted=False, burned=False):
    return S.verdict(S.evaluate_gates(metrics), stage, exhausted, holdout_burned=burned)


def test_g0_g1_reject_final():
    out = v(with_(DEV_PASS, integrity_checks_passed=0.0, dev_trades=3))
    assert (out.verdict, out.final) == (gates.REJECT, True)
    out = v(with_(DEV_PASS, n_params=6))
    assert (out.verdict, out.final) == (gates.REJECT, True)
    assert any("G1 Complexity" in r for r in out.reasons)


def test_too_few_trades_is_not_proven_even_with_bad_performance():
    out = v(with_(DEV_PASS, dev_trades=40, pf=0.5))
    assert out.verdict == gates.NOT_PROVEN and out.final is False
    out = v(with_(DEV_PASS, dev_months_with_trades_frac=0.5), exhausted=True)
    assert out.verdict == gates.NOT_PROVEN and out.final is True


def test_performance_failure_rejects_final_only_when_budget_exhausted():
    for metric, bad in (("pf", 1.1), ("net_minus_null_pctl", -1.0), ("positive_folds", 2),
                        ("pf_delay_1", 0.9), ("plateau_median_pf", 1.0)):
        out = v(with_(DEV_PASS, **{metric: bad}))
        assert (out.verdict, out.final) == (gates.REJECT, False), metric
    out = v(with_(DEV_PASS, pf=1.1), exhausted=True)
    assert (out.verdict, out.final) == (gates.BUDGET_EXHAUSTED_VERDICT, True) == (gates.REJECT, True)
    assert out.reasons[0] == "G3 DEV after base costs: pf 1.1 >= 1.2 failed"


def test_dev_all_pass_is_provisional_not_proven():
    out = v(DEV_PASS)
    assert (out.verdict, out.final, out.reasons) == (gates.NOT_PROVEN, False, (S.REASON_DEV_PASSED,))
    out = v(DEV_PASS, burned=True)
    assert (out.verdict, out.final, out.reasons) == (gates.NOT_PROVEN, True, (S.REASON_HOLDOUT_BURNED,))
    # a DEV gate that was never evaluated is not a pass
    missing = dict(DEV_PASS)
    missing.pop("plateau_positive_frac")
    missing.pop("plateau_median_pf")
    out = v(missing)
    assert out.verdict == gates.NOT_PROVEN and "G7" in out.reasons[0]
    assert S.dev_hard_gates_passed(S.evaluate_gates(missing)) is False


def holdout_gates(dev_metrics, hold_metrics):
    return S.merge_holdout_gates(S.evaluate_gates(dev_metrics), hold_metrics)


def test_holdout_verdicts():
    ok = S.verdict(holdout_gates(DEV_PASS, HOLDOUT_PASS), "holdout", False)
    assert (ok.verdict, ok.final) == (gates.CANDIDATE, True)
    few = S.verdict(holdout_gates(DEV_PASS, with_(HOLDOUT_PASS, holdout_trades=12, holdout_net=-500.0)), "holdout",
                    False)
    assert (few.verdict, few.final) == (gates.NOT_PROVEN, True)          # G8 cannot reject a thin holdout
    bad = S.verdict(holdout_gates(DEV_PASS, with_(HOLDOUT_PASS, holdout_dd_margin=0.05)), "holdout", False)
    assert (bad.verdict, bad.final) == (gates.REJECT, True)
    missing = S.verdict(holdout_gates(DEV_PASS, {"holdout_trades": 40}), "holdout", False)
    assert missing.verdict == gates.NOT_PROVEN


def test_merge_holdout_gates_keeps_dev_results():
    merged = by_id(holdout_gates(DEV_PASS, HOLDOUT_PASS))
    g2 = {r.metric: r.passed for r in merged["G2"].rules}
    assert g2 == {"dev_trades": True, "dev_months_with_trades_frac": True, "holdout_trades": True}
    assert merged["G8"].passed is True and merged["G3"].passed is True


def test_gate_results_json_round_trip():
    res = S.evaluate_gates(with_(DEV_PASS, pf=math.inf))
    js = json.loads(json.dumps(S.json_safe([S._gate_json(g) for g in res])))
    back = S.gate_results_from_json(js)
    assert [(g.id, g.passed, g.active) for g in back] == [(g.id, g.passed, g.active) for g in res]
    assert S.dev_hard_gates_passed(back) is True


# ── scorecard JSON ──────────────────────────────────────────────────────────

def stage_frame(stage):
    start = "2024-09-01" if stage == "dev" else "2026-01-01"
    idx = pd.date_range(start, periods=400, freq="1h", name="time")
    df = pd.DataFrame({c: 1.0 for c in ("open", "high", "low", "close", "volume")}, index=idx)
    return StageFrame(frame=df, stage=stage, start_bar=96 if stage == "holdout" else 0, stop_bar=400,
                      warmup_bars=100)


PERF = {"trades": 150, "net": 1234.5, "return_pct": 12.3, "pf": math.inf, "expectancy": 8.2, "win_rate": 0.4,
        "max_dd_pct": 7.9, "daily_sharpe": 1.1, "long_trades": 150, "long_net": 1234.5, "long_pf": math.inf,
        "short_trades": 0, "short_net": 0.0, "short_pf": None, "median_hold_days": 1.5,
        "rollover_entry_share": 0.0}
COST = {"spread_model": "constant", "commission_model": "instruments_v1", "commission_rate": 0.0003,
        "slippage_pips": 0.5, "round_trip_bps": 3.0, "slippage_points": 5.0,
        "fx_desc": "quote currency USD (no conversion)", "breakeven_cost_mult": 1.84, "breakeven_flag": "in_range"}


def build(stage="dev", metrics=DEV_PASS, **kw):
    sub = submission(PLANTED_SRC)
    gr = S.evaluate_gates(metrics) if stage == "dev" else holdout_gates(DEV_PASS, HOLDOUT_PASS)
    out = S.verdict(gr, stage, False)
    args = dict(submission=sub, stage=stage, sframe=stage_frame(stage), params={"vol_mult": 3.0}, gate_results=gr,
                outcome=out, budget=BudgetInfo(2, 2, 7, 0, False, False), perf=PERF,
                holdout_split=None if stage == "dev" else {"H_A": {"trades": 20, "net": 5.0, "pf": 1.2},
                                                           "H_B": {"trades": 20, "net": 6.0, "pf": 1.3}},
                cost_model=COST, context={"window_end_closes": 1, "untradable_stop": 0, "n_signals": 160,
                                          "halted": False},
                detail={"null": {"runs": 300}}, result_file="lab_test_strategy_XAUUSD_H1_dev_result.json",
                deploy_id="dep-1")
    args.update(kw)
    return S.build_scorecard(**args)


def test_scorecard_keys_order_and_validation():
    sc = build()
    assert tuple(sc) == S.SCORECARD_KEYS
    assert sc["label"] == splits.IN_SAMPLE_LABEL and sc["holdout_split"] is None
    assert sc["dev_hard_gates_passed"] is True
    assert sc["context"]["financing_flag"] is True and sc["context"]["rollover_flag"] is False
    assert sc["window"]["first_bar"] == "2024-09-01T00:00:00" and sc["window"]["n_bars"] == 400
    g3 = next(g for g in sc["gates"] if g["id"] == "G3")
    assert g3["threshold"] == "pf >= 1.2" and g3["rules"][0]["threshold"] == 1.2
    parsed = json.loads(S.to_json_bytes(sc))
    assert S.validate_scorecard(parsed) == []
    assert parsed["headline"]["pf"] is None                       # inf -> null in JSON
    assert parsed["gates"][3]["passed"] is True                  # but the rule result is kept


def test_gate_threshold_names_the_rule_that_decided_it():
    # G1 has two rules on the same metric (n_params <= 5 and n_params >= 1); zero params fails the
    # second one, so the gate's threshold text must be that rule, not the first rule with that metric.
    sc = build(metrics=with_(DEV_PASS, n_params=0))
    g1 = next(g for g in sc["gates"] if g["id"] == "G1")
    assert g1["passed"] is False and g1["value"] == 0
    assert g1["threshold"] == "n_params >= 1"


def test_holdout_scorecard_validates():
    sc = build("holdout")
    assert sc["label"] == "HOLDOUT" and sc["verdict"] == gates.CANDIDATE
    assert set(sc["holdout_split"]) == {"H_A", "H_B"}
    assert S.validate_scorecard(json.loads(S.to_json_bytes(sc))) == []


def test_validate_scorecard_catches_problems():
    sc = json.loads(S.to_json_bytes(build()))
    bad = dict(sc)
    bad["verdict"] = "ELITE"
    probs = S.validate_scorecard(bad)
    assert any("verdict" in p for p in probs) and any("forbidden" in p for p in probs)
    swapped = {k: sc[k] for k in reversed(list(sc))}
    assert S.validate_scorecard(swapped)
    no_label = dict(sc)
    no_label["label"] = "HOLDOUT"
    assert S.validate_scorecard(no_label)


def test_builder_view_drops_detail_and_refuses_holdout():
    sc = build()
    view = S.builder_view(sc)
    assert "detail" not in view and "detail" in sc
    assert S.validate_scorecard(json.loads(S.to_json_bytes(view))) == []
    with pytest.raises(ValueError):
        S.builder_view(build("holdout"))


def test_no_forbidden_labels_anywhere_in_emitted_text():
    for sc in (build(), build("holdout"), build(metrics=with_(DEV_PASS, pf=0.9))):
        text = S.to_json_bytes(sc).decode("utf-8")
        for t in S._human_texts(json.loads(text)):
            assert not gates.contains_forbidden_label(t)
        for label in gates.FORBIDDEN_LABELS:
            assert label.casefold() not in text.casefold()


def test_forbidden_label_in_text_is_refused_and_scrubbable():
    sc = build()
    sc["warnings"] = ["this has an EDGE FOUND moment"]
    with pytest.raises(ValueError):
        S.to_json_bytes(sc)
    with pytest.raises(ValueError):
        build(warnings=["Tier 1 strategy"])
    assert not gates.contains_forbidden_label(S.scrub("KeyError: 'Elite_col' and optimal"))


# ── critic ──────────────────────────────────────────────────────────────────

def test_critic_cannot_upgrade():
    rej = build(metrics=with_(DEV_PASS, pf=0.9))
    assert rej["verdict"] == gates.REJECT
    out = S.apply_critic(rej, decision="DOWNGRADE", to=gates.CANDIDATE, reason="looks fine")
    assert out["verdict"] == gates.REJECT and out["critic"]["decision"] == "DOWNGRADE"
    kept = S.apply_critic(build("holdout"), decision="KEEP", reason="ok")
    assert kept["verdict"] == gates.CANDIDATE and kept["critic"]["to"] is None
    down = S.apply_critic(build("holdout"), decision="DOWNGRADE", to=gates.NOT_PROVEN, reason="spec mismatch")
    assert (down["verdict"], down["final"]) == (gates.NOT_PROVEN, True)
    assert S.validate_scorecard(json.loads(S.to_json_bytes(down))) == []
    with pytest.raises(ValueError):
        S.apply_critic(rej, decision="UPGRADE", to=gates.CANDIDATE)
    with pytest.raises(ValueError):
        S.apply_critic(rej, decision="KEEP", reason="an edge found here")


def test_build_scorecard_copies_dev_hard_gates_passed_when_given():
    """Section 6.5: a holdout scorecard carries the DEV value even when its own G0 now fails."""
    import inspect
    from sajim_quant.lab import scorecard as SC
    sig = inspect.signature(SC.build_scorecard)
    assert sig.parameters["dev_hard_gates_passed"].default is None
    assert SC._dev_hard_gates_passed is SC.dev_hard_gates_passed
