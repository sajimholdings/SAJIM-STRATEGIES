"""evaluate.py: trusted orchestration of check / DEV / holdout (synthetic OHLC only).

Most tests use an in-process fake SandboxExecutor (trusted, hand-written strategies only).
The integration tests at the bottom run the real LocalSubprocessExecutor + sandbox_runner
when those modules import.
"""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

for _mod in ("simulator", "sandbox_io", "causality", "contract", "sandbox_runner"):
    pytest.importorskip(f"sajim_quant.lab.{_mod}")

from sajim_quant.core.schema import BacktestResult  # noqa: E402
from sajim_quant.lab import evaluate as E, gates, ledger as L, scorecard as S, splits  # noqa: E402
from sajim_quant.lab.interfaces import ContractRefused, DevNotPassed, derive_request_id  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from _judging_testkit import (  # noqa: E402
    COIN_SRC, PEEK_SRC, PLANTED_SRC, InProcessExecutor, budget, dev_raw, holdout_raw, submission,
)
from sajim_quant.lab import causality as C  # noqa: E402
from sajim_quant.lab.interfaces import MAX_RUNS_PER_JOB, PER_RUN_TIMEOUT_S  # noqa: E402

USD = "quote currency USD (no conversion)"
PLANTED_DATA = dict(drift=0.6, marker_every=75)


def ones(sframe):
    return np.ones(len(sframe.frame))


def run_dev(src, raw, *, trial=1, executor=None, **mkw):
    sub = submission(src, **mkw)
    sframe = E.prepare_frame(raw, "dev", 100)
    ex = executor or InProcessExecutor()
    ev = E.evaluate_dev(sub, sframe, ex, quote_rate=ones(sframe), fx_desc=USD, budget=budget(trial),
                        deploy_id="test")
    return sub, sframe, ex, ev


def run_holdout(src, raw, dev_scorecard, *, executor=None, store=None, **mkw):
    sub = submission(src, **mkw)
    sframe = E.prepare_frame(raw, "holdout", 100)
    ex = executor or InProcessExecutor()
    ev = E.evaluate_holdout(sub, sframe, ex, quote_rate=ones(sframe), fx_desc=USD, budget=budget(None),
                            dev_scorecard=dev_scorecard, store=store)
    return sub, sframe, ex, ev


def roundtrip(sc):
    """What the store holds: the strict JSON of the server scorecard."""
    return json.loads(S.to_json_bytes(sc))


@pytest.fixture(scope="module")
def planted_dev():
    return run_dev(PLANTED_SRC, dev_raw(**PLANTED_DATA))


@pytest.fixture(scope="module")
def coin_dev_last_trial():
    return run_dev(COIN_SRC, dev_raw(markers=False), trial=gates.MAX_DEV_TRIALS_PER_FAMILY, allow_short=True)


@pytest.fixture(scope="module")
def planted_holdout(planted_dev):
    return run_holdout(PLANTED_SRC, holdout_raw(**PLANTED_DATA), roundtrip(planted_dev[3].scorecard))


# ── frames ──────────────────────────────────────────────────────────────────

def test_prepare_frame_half_open_windows_and_warmup():
    raw = pd.DataFrame({c: 1.0 for c in E.OHLCV},
                       index=pd.date_range("2024-09-01", "2026-01-10", freq="1h", inclusive="left", name="time"))
    raw["extra"] = 0.0
    dev = E.prepare_frame(raw, "dev", 10)
    assert dev.start_bar == 0 and dev.frame.index[-1] == pd.Timestamp("2025-12-31 23:00")   # half-open stop
    assert dev.stop_bar == len(dev.frame) == int(splits.window_mask(raw.index, "dev").sum())
    assert list(dev.frame.columns) == list(E.OHLCV)
    hold = E.prepare_frame(raw, "holdout", 10)
    assert hold.frame.index[hold.start_bar] == pd.Timestamp("2026-01-05 00:00")
    assert hold.start_bar == 10 and hold.frame.index[0] == pd.Timestamp("2026-01-04 14:00")
    assert hold.window == (10, len(hold.frame)) and hold.frame.index[-1] == raw.index[-1]


def test_prepare_frame_refuses_missing_or_short_coverage():
    raw = pd.DataFrame({c: 1.0 for c in E.OHLCV},
                       index=pd.date_range("2025-06-01", "2025-12-01", freq="1h", name="time"))
    with pytest.raises(ContractRefused) as exc:
        E.prepare_frame(raw, "dev", 0)                           # DEV coverage ~ 38%
    assert exc.value.code == "coverage" and exc.value.status == 422
    with pytest.raises(ContractRefused):
        E.prepare_frame(raw, "holdout", 0)                       # no holdout bars at all
    with pytest.raises(ContractRefused):                         # open-ended holdout vs a later reference end
        E.prepare_frame(holdout_raw(end="2026-02-01"), "holdout", 0, reference_end=datetime(2026, 9, 30))


# ── check ───────────────────────────────────────────────────────────────────

def test_check_lint_or_manifest_failure_never_reaches_the_sandbox():
    sframe = E.prepare_frame(dev_raw(), "dev", 100)
    ex = InProcessExecutor()
    bad_src = PLANTED_SRC.replace(b".shift(1)", b".shift(-1)")
    rep = E.check(submission(bad_src), sframe, ex)
    assert rep.passed is False and rep.checks["lint"] is False and ex.jobs == []
    sub = submission(PLANTED_SRC, code_sha256="0" * 64)
    rep = E.check(sub, sframe, ex)
    assert rep.checks["manifest"] is False and rep.checks["lint"] is True and ex.jobs == []


def test_shorts_with_allow_short_false_fail_integrity():
    _, _, ex, ev = run_dev(COIN_SRC, dev_raw(markers=False))       # coin flips short; manifest forbids shorts
    assert ev.scorecard["verdict"] == gates.REJECT and ev.scorecard["final"] is True
    g0 = next(g for g in ev.scorecard["gates"] if g["id"] == "G0")
    assert g0["passed"] is False and ev.result is None and len(ex.jobs) == 1
    assert S.validate_scorecard(roundtrip(ev.scorecard)) == []


# ── DEV ─────────────────────────────────────────────────────────────────────

def test_coin_flip_rejects(coin_dev_last_trial):
    _, _, _, ev = coin_dev_last_trial
    sc = ev.scorecard
    gates_by = {g["id"]: g for g in sc["gates"]}
    assert gates_by["G2"]["passed"] is True                        # adequate sample...
    assert gates_by["G3"]["passed"] is False                       # ...and it loses after costs
    assert sc["dev_hard_gates_passed"] is False
    # budget exhausted with an adequate sample -> REJECT, final
    assert (sc["verdict"], sc["final"]) == (gates.BUDGET_EXHAUSTED_VERDICT, True) == (gates.REJECT, True)
    assert any("budget" in r for r in sc["reasons"])
    # the same gate results at trial 1: REJECT, but a revision is still allowed
    early = S.verdict(ev.gate_results, "dev", False)
    assert (early.verdict, early.final) == (gates.REJECT, False)


def test_planted_edge_passes_dev_gates(planted_dev):
    sub, sframe, ex, ev = planted_dev
    sc = ev.scorecard
    active_hard = [g for g in sc["gates"] if g["active"] and g["hard"] and g["id"] != "G8"]
    assert all(g["passed"] is True for g in active_hard), [(g["id"], g["value"]) for g in active_hard]
    assert sc["dev_hard_gates_passed"] is True
    assert (sc["verdict"], sc["final"], sc["reasons"]) == (gates.NOT_PROVEN, False, [S.REASON_DEV_PASSED])
    assert sc["label"] == splits.IN_SAMPLE_LABEL and sc["trial"] == 1
    assert sc["headline"]["trades"] >= gates.MIN_DEV_TRADES and sc["headline"]["pf"] > gates.MIN_DEV_PF
    assert sc["cost_model"]["breakeven_flag"] in ("in_range", "above_range")
    # at most two sandbox jobs: the check (full1 = base), then the adaptive cuts + plateau neighbours
    assert [j.label for j in ex.jobs] == ["check", "adaptive_neighbours"]
    assert [r.run_id for r in ex.jobs[1].runs] == ([f"acut{i}" for i in range(1, C.ADAPTIVE_CUTS + 1)]
                                                   + [f"nb{i:02d}" for i in range(6)])
    assert len(ex.jobs[1].runs) <= MAX_RUNS_PER_JOB
    assert all(r.cut is not None and r.cut < sframe.stop_bar for r in ex.jobs[1].runs[:C.ADAPTIVE_CUTS])
    assert sc["detail"]["plateau"]["n"] == 6 and sc["detail"]["null"]["runs"] == gates.NULL_RUNS
    assert S.validate_scorecard(roundtrip(sc)) == []
    view = S.builder_view(sc)
    assert "detail" not in view and S.validate_scorecard(roundtrip(view)) == []


def test_dev_result_is_cost_honest_universal_schema(planted_dev):
    sub, _, _, ev = planted_dev
    res = ev.result
    assert isinstance(res, BacktestResult) and res.metadata.engine == gates.ENGINE
    assert res.metadata.commission_model == gates.COMMISSION_MODEL and res.metadata.strategy_name == sub.lab_id
    assert all(t.commission > 0 for t in res.trades)
    notes = json.loads(res.metadata.notes)
    assert notes["code_sha256"] == sub.code_sha256 and notes["stage"] == "dev"
    payload = E.result_payload(res)
    assert set(payload) == {"metadata", "total_trades", "metrics", "trades"}
    json.dumps(payload, allow_nan=False)
    assert ev.scorecard["result_file"] == "lab_test_strategy_XAUUSD_H1_dev_result.json"


def test_too_few_trades_is_not_proven_and_failed_neighbours_count_against_plateau():
    raw = dev_raw(**PLANTED_DATA, n_markers=40)
    _, _, ex, ev = run_dev(PLANTED_SRC, raw, executor=InProcessExecutor(fail_runs={"nb00"}))
    sc = ev.scorecard
    assert sc["verdict"] == gates.NOT_PROVEN and sc["final"] is False
    assert sc["reasons"][0].startswith("G2 Sample: dev_trades 40 >= 100 failed")
    nbs = sc["detail"]["plateau"]["neighbours"]
    assert nbs[0]["failed"] is True and nbs[0]["pf"] == 0.0 and all(not n["failed"] for n in nbs[1:])
    assert S.validate_scorecard(roundtrip(sc)) == []


def test_check_runs_the_adaptive_cuts_as_a_second_job():
    sframe = E.prepare_frame(dev_raw(**PLANTED_DATA), "dev", 100)
    ex = InProcessExecutor()
    rep = E.check(submission(PLANTED_SRC), sframe, ex)
    assert rep.passed, rep.reasons
    assert [j.label for j in ex.jobs] == ["check", "adaptive"]
    assert [r.run_id for r in ex.jobs[1].runs] == [f"acut{i}" for i in range(1, C.ADAPTIVE_CUTS + 1)]
    # a failed first job never starts the second one
    ex = InProcessExecutor(fail_runs={"full2"})
    assert not E.check(submission(PLANTED_SRC), sframe, ex).passed and [j.label for j in ex.jobs] == ["check"]


def test_a_short_slice_peek_passes_the_fixed_check_but_fails_dev_on_the_adaptive_cuts():
    raw = dev_raw(**PLANTED_DATA)
    sframe = E.prepare_frame(raw, "dev", 100)
    sub = submission(PEEK_SRC)
    from sajim_quant.lab import contract
    assert contract.lint_source(PEEK_SRC) == []                   # lint cannot see the slice peek
    assert E._fixed_check(sub, sframe, InProcessExecutor(), indicators_py=None).passed
    rep = E.check(sub, sframe, InProcessExecutor())               # the free check already tells the builder
    assert not rep.passed and not rep.checks["causality"]
    _, _, ex, ev = run_dev(PEEK_SRC, raw)
    sc = ev.scorecard
    assert [j.label for j in ex.jobs] == ["check", "adaptive_neighbours"]
    assert (sc["verdict"], sc["final"]) == (gates.REJECT, True) and ev.result is None
    g0 = next(g for g in sc["gates"] if g["id"] == "G0")
    assert g0["passed"] is False
    assert any(r.startswith("causality: outputs before an adaptive cut (") for r in sc["reasons"])
    assert "plateau" not in sc["detail"] and S.validate_scorecard(roundtrip(sc)) == []


def test_timed_out_neighbours_make_the_plateau_not_computable_with_a_runtime_reason():
    raw = dev_raw(**PLANTED_DATA)
    for kind in ("timeout", "budget"):
        _, _, _, ev = run_dev(PLANTED_SRC, raw, executor=InProcessExecutor(fail_runs={"nb04": kind, "nb05": kind}))
        sc = ev.scorecard
        g7 = next(g for g in sc["gates"] if g["id"] == "G7")
        assert g7["passed"] is False and g7["value"] is None              # not computable, not a plateau number
        assert all(r["value"] is None for r in g7["rules"])
        assert sc["reasons"][0].startswith("G7 plateau not computable: 2 of 6 neighbour runs were stopped for time")
        assert f"{PER_RUN_TIMEOUT_S:g} s" in sc["reasons"][0]
        assert (sc["verdict"], sc["final"]) == (gates.REJECT, False)     # a revision (make it faster) is allowed
        assert sc["detail"]["plateau"]["timed_out"] == ["nb04", "nb05"]
        g0 = next(g for g in sc["gates"] if g["id"] == "G0")
        assert g0["passed"] is True                                       # integrity is not blamed
        assert S.validate_scorecard(roundtrip(sc)) == []
    # a timed-out ADAPTIVE cut is an integrity failure (it could hide look-ahead): G0 -> REJECT
    _, _, _, ev = run_dev(PLANTED_SRC, raw, executor=InProcessExecutor(fail_runs={"acut2": "timeout"}))
    assert ev.scorecard["verdict"] == gates.REJECT and ev.scorecard["final"] is True
    assert any(r.startswith("sandbox: acut2 timeout:") for r in ev.scorecard["reasons"])


def test_make_simulate_two_trades_by_hand():
    """XAUUSD: contract 100, point 0.01, 3.0 bps round trip, 5 points slippage per side; 1% risk."""
    from sajim_quant.core.instruments import get_spec
    from sajim_quant.lab.interfaces import EVENTS_DTYPE, SimOverrides, StageFrame
    n = 20
    idx = pd.date_range("2024-09-02", periods=n, freq="1h", name="time")
    o = np.full(n, 2000.0)
    h, l, c = o + 1.0, o - 1.0, o.copy()
    h[5] = 2025.0                                   # long: TP 2020 touched on bar 5
    o[12], h[12], l[12], c[12] = 2015.0, 2016.0, 2014.0, 2015.0   # short: gaps through SL 2010
    frame = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)
    sframe = StageFrame(frame=frame, stage="dev", start_bar=0, stop_bar=n, warmup_bars=0)
    sim = E.make_simulate(sframe, get_spec("XAUUSD"), np.ones(n), max_hold_bars=50, allow_short=True,
                          strategy_name="lab_hand", timeframe="H1")
    ev = np.zeros(2, dtype=EVENTS_DTYPE)
    ev[0] = (2, 1, 10.0, 20.0, False)               # long, fills open[3] = 2000
    ev[1] = (9, -1, 10.0, np.nan, False)            # short, fills open[10] = 2000, no target
    t1, t2 = sim(ev, SimOverrides()).trades
    # trade 1: lots = 100 / (10 * 100) = 0.10; gross = 20 * 0.10 * 100 = 200
    #   cost = 2000*0.10*100 * 3e-4 (6.00) + 5*0.01*2*0.10*100 (1.00) = 7.00 -> net 193.00
    assert (t1.entry_time, t1.exit_time, t1.exit_reason.value) == ("2024-09-02T03:00:00", "2024-09-02T05:00:00", "TP")
    assert (t1.position_size, t1.exit_price) == (pytest.approx(0.10), 2020.0)
    assert (t1.gross_pnl, t1.commission, t1.net_pnl) == (pytest.approx(200.0), pytest.approx(7.00),
                                                          pytest.approx(193.00))
    # trade 2: equity 10193.00 -> lots 101.93 / 1000 = 0.1019 -> 0.10 (lot step); SL gap fills at open 2015
    #   gross = (2000 - 2015) * 0.10 * 100 = -150; cost 7.00 -> net -157.00
    assert (t2.exit_time, t2.exit_reason.value, t2.exit_price) == ("2024-09-02T12:00:00", "SL", 2015.0)
    assert (t2.position_size, t2.gross_pnl, t2.net_pnl) == (pytest.approx(0.10), pytest.approx(-150.0),
                                                             pytest.approx(-157.00))
    # cost stress multiplies the cost only (same lots, same gross)
    s1, s2 = sim(ev, SimOverrides(cost_mult=2.0)).trades
    assert (s1.commission, s2.commission) == (pytest.approx(14.00), pytest.approx(14.00))
    assert s1.net_pnl == pytest.approx(200.0 - 14.00)


# ── HOLDOUT ─────────────────────────────────────────────────────────────────

def test_holdout_with_no_signals_is_not_proven_not_rejected(planted_dev):
    # Zero holdout signals is a thin sample (G2 -> NOT_PROVEN), not an integrity failure that REJECTs.
    raw = holdout_raw(markers=False)                                    # no volume spikes -> no signals
    _, _, ex, ev = run_holdout(PLANTED_SRC, raw, roundtrip(planted_dev[3].scorecard))
    sc = ev.scorecard
    assert (sc["verdict"], sc["final"]) == (gates.NOT_PROVEN, True), sc["reasons"]
    assert sc["headline"]["trades"] == 0 and len(ex.jobs) == 1
    g0 = next(g for g in sc["gates"] if g["id"] == "G0")
    assert g0["passed"] is True                                         # DEV integrity result kept
    assert S.validate_scorecard(roundtrip(sc)) == []


def test_holdout_refused_when_manifest_differs_from_the_dev_pass(planted_dev):
    # code_sha256 covers strategy.py only: a manifest edited after the DEV pass (other symbol or
    # timeframe) must not spend the once-per-sha holdout on a configuration DEV never judged.
    good = roundtrip(planted_dev[3].scorecard)
    raw = holdout_raw(**PLANTED_DATA)
    for kw in (dict(symbol="XAGUSD"), dict(timeframe="M30"), dict(lab_id="lab_other_variant",
                                                                  family="lab_test_strategy")):
        ex = InProcessExecutor()
        with pytest.raises(DevNotPassed):
            run_holdout(PLANTED_SRC, raw, good, executor=ex, **kw)
        assert ex.jobs == []


def test_holdout_requires_a_dev_pass_for_that_sha(planted_dev, coin_dev_last_trial, tmp_path):
    good = roundtrip(planted_dev[3].scorecard)
    raw = holdout_raw(**PLANTED_DATA)
    for bad in (roundtrip(coin_dev_last_trial[3].scorecard),          # other sha, DEV failed
                dict(good, dev_hard_gates_passed=False), dict(good, code_sha256="f" * 64),
                dict(good, stage="holdout")):
        ex = InProcessExecutor()
        with pytest.raises(DevNotPassed) as exc:
            run_holdout(PLANTED_SRC, raw, bad, executor=ex)
        assert exc.value.status == 412 and ex.jobs == []
    store = L.LocalDirStore(tmp_path)                                   # scorecard OK but no DEV-pass record
    with pytest.raises(DevNotPassed):
        run_holdout(PLANTED_SRC, raw, good, store=store)


def test_planted_edge_holdout_is_candidate(planted_dev, planted_holdout):
    _, _, ex, ev = planted_holdout
    sc = ev.scorecard
    assert (sc["verdict"], sc["final"]) == (gates.CANDIDATE, True)
    assert sc["label"] == "HOLDOUT" and sc["stage"] == "holdout" and sc["trial"] == 1
    assert [j.label for j in ex.jobs] == ["check", "adaptive"]          # the adaptive cuts are a 2nd holdout job
    assert all(C.is_adaptive_run(r.run_id) for r in ex.jobs[1].runs)
    g = {x["id"]: x for x in sc["gates"]}
    assert g["G8"]["passed"] is True
    assert {r["metric"]: r["passed"] for r in g["G2"]["rules"]}["holdout_trades"] is True
    dev_g = {x["id"]: x for x in planted_dev[3].scorecard["gates"]}
    for gid in ("G0", "G1", "G3", "G4a", "G5", "G6", "G7"):
        assert g[gid]["passed"] == dev_g[gid]["passed"] and g[gid]["value"] == pytest.approx(dev_g[gid]["value"])
    hs = sc["holdout_split"]
    assert hs["H_A"]["trades"] > 0 and hs["H_B"]["trades"] > 0
    assert hs["H_A"]["trades"] + hs["H_B"]["trades"] == sc["headline"]["trades"]
    assert sc["dev_hard_gates_passed"] is True and sc["result_file"].endswith("_holdout_result.json")
    assert S.validate_scorecard(roundtrip(sc)) == []
    with pytest.raises(ValueError):
        S.builder_view(sc)                                              # never reaches the builder
    down = S.apply_critic(sc, decision="DOWNGRADE", to=gates.NOT_PROVEN, reason="spec mismatch")
    assert down["verdict"] == gates.NOT_PROVEN
    assert S.apply_critic(down, decision="DOWNGRADE", to=gates.CANDIDATE)["verdict"] == gates.NOT_PROVEN


def test_holdout_with_store_and_dev_pass_record(planted_dev, tmp_path):
    sub, _, _, dev_ev = planted_dev
    store = L.LocalDirStore(tmp_path)
    L.put_record(store, L.make_record(
        "dev_trial", family=sub.family, lab_id=sub.lab_id, request_id=derive_request_id(sub.family, sub.code_sha256,
                                                                                         "dev"),
        actor="api", code_sha256=sub.code_sha256, stage="dev",
        payload={"trial": 1, "job_id": "j", "status": "done", "verdict": dev_ev.verdict.verdict, "final": False,
                 "dev_hard_gates_passed": True, "scorecard_path": "x", "result_file": "y", "error": None}))
    raw = holdout_raw(**PLANTED_DATA, n_markers=10)                     # thin holdout
    _, _, _, ev = run_holdout(PLANTED_SRC, raw, roundtrip(dev_ev.scorecard), store=store)
    assert (ev.scorecard["verdict"], ev.scorecard["final"]) == (gates.NOT_PROVEN, True)
    assert any("holdout_trades" in r for r in ev.scorecard["reasons"])


def test_holdout_adaptive_cut_failure_rejects_without_a_bar_index_and_keeps_dev_flag(planted_dev):
    """The peek strategy on the holdout (its DEV scorecard forged as passed): REJECT on G0, no bar numbers."""
    import re
    good = roundtrip(planted_dev[3].scorecard)
    peek_sub = submission(PEEK_SRC)
    forged = dict(good, code_sha256=peek_sub.code_sha256, sha12=peek_sub.sha12)
    _, _, ex, ev = run_holdout(PEEK_SRC, holdout_raw(**PLANTED_DATA), forged)
    sc = ev.scorecard
    assert [j.label for j in ex.jobs] == ["check", "adaptive"]
    assert (sc["verdict"], sc["final"]) == (gates.REJECT, True)
    assert sc["dev_hard_gates_passed"] is True                          # section 6.5: copied from DEV
    assert "holdout integrity check failed" in sc["reasons"]
    texts = sc["reasons"] + sc["detail"]["check"]["reasons"]
    assert any("adaptive cut (placed just after one of the strategy's own event bars)" in r for r in texts)
    for r in texts:
        assert not re.search(r"\bbar \d|differing bar", r), r
    assert S.validate_scorecard(roundtrip(sc)) == []


def test_holdout_failure_with_adequate_sample_rejects(planted_dev):
    raw = holdout_raw(drift=-0.6, marker_every=75)                      # the pattern reverses out of sample
    _, _, _, ev = run_holdout(PLANTED_SRC, raw, roundtrip(planted_dev[3].scorecard))
    sc = ev.scorecard
    assert sc["headline"]["trades"] >= gates.MIN_HOLDOUT_TRADES
    assert (sc["verdict"], sc["final"]) == (gates.REJECT, True)
    assert any(r.startswith("G8 Sealed holdout") for r in sc["reasons"])
    assert scorecard_texts_clean(sc)


def scorecard_texts_clean(sc):
    return not any(gates.contains_forbidden_label(t) for t in S._human_texts(sc))


# ── integration: the real local sandbox executor ────────────────────────────

def _real_executor():
    mod = pytest.importorskip("sajim_quant.lab.sandbox_local")
    from _judging_testkit import LocalTimeoutExecutor
    return LocalTimeoutExecutor(mod.LocalSubprocessExecutor())


def test_integration_real_sandbox_dev_two_jobs():
    ex = _real_executor()
    raw = dev_raw(**PLANTED_DATA, n_markers=40)
    _, _, _, fake = run_dev(PLANTED_SRC, raw)
    _, _, _, real = run_dev(PLANTED_SRC, raw, executor=ex)
    assert real.check.passed, real.check.reasons
    for key in ("verdict", "final", "headline", "dev_hard_gates_passed"):
        assert real.scorecard[key] == fake.scorecard[key], key
    assert real.scorecard["detail"]["plateau"] == fake.scorecard["detail"]["plateau"]
    # the holdout path reuses the same check() job; look-ahead canaries live in test_causality.py (B)


def test_evaluation_inputs_are_not_mutated(planted_dev):
    sub, sframe, _, _ = planted_dev
    before = copy.deepcopy(dict(sub.manifest))
    E.check(sub, sframe, InProcessExecutor())
    assert dict(sub.manifest) == before
