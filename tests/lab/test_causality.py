"""Integrity checks: the frozen check plan, assess_check, and look-ahead canaries fed straight to the sandbox."""

from __future__ import annotations

import hashlib
import math
import re

import numpy as np
import pytest

from sajim_quant.lab import causality as C
from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I
from sajim_quant.lab.sandbox_io import events_equal

from _sandbox_testkit import make_job, run_jobs, strategy, synthetic_frame

SHA = "ab" * 32


# ── plan ────────────────────────────────────────────────────────────────────
def test_cut_index_and_scramble_seed_are_frozen():
    assert C.cut_index(0.2, 104211) == 20842
    assert C.cut_index(0.95, 100) == 95
    assert C.cut_index(0.2, 3) == 1                       # never 0
    want = int.from_bytes(hashlib.sha256(b"sajim.lab.scramble/1|" + SHA.encode()).digest()[:8], "big") & (2**63 - 1)
    assert C.scramble_seed(SHA) == want and 0 <= want < 2**63
    assert C.scramble_seed("cd" * 32) != want


def test_build_check_plan_order_cuts_and_scramble():
    n = 1000
    plan = C.build_check_plan(n, {"a": 3, "b": 1.5}, SHA)
    assert tuple(r.run_id for r in plan) == C.CHECK_RUN_IDS
    assert [r.cut for r in plan[:5]] == [math.floor(f * n) for f in gates.CAUSALITY_CUTS]
    scr = plan[5]
    assert scr.cut is None and scr.scramble == I.ScrambleSpec(start=500, seed=C.scramble_seed(SHA))
    assert all(r.cut is None and r.scramble is None for r in plan[6:])
    assert all(dict(r.params) == {"a": 3, "b": 1.5} for r in plan)
    assert len(C.build_check_plan(1, {}, SHA)) == 8
    with pytest.raises(ValueError):
        C.build_check_plan(0, {}, SHA)


# ── assess_check on synthetic decoded jobs ──────────────────────────────────
N = 1000
PLAN = C.build_check_plan(N, {"a": 1}, SHA)


def ev(bars, signal=1):
    a = np.zeros(len(bars), dtype=I.EVENTS_DTYPE)
    a["bar"] = bars
    a["signal"] = signal
    a["stop_dist"] = 0.5
    a["tp_dist"] = np.nan
    return a


BASE = ev([50, 150, 250, 450, 650, 850, 990])


def decoded(overrides=None, errors=(), drop=()):
    events = {}
    for r in PLAN:
        n = r.n_bars(N)
        events[r.run_id] = BASE[BASE["bar"] < n].copy()
    events.update(overrides or {})
    for rid in drop:
        events.pop(rid, None)
    return I.DecodedJob(events=events, errors=tuple(errors), timings={"_total": 1.0}, versions={},
                        sensitive_env_names=())


def test_assess_check_passes_a_causal_deterministic_strategy():
    rep = C.assess_check(PLAN, decoded(), N)
    assert rep.passed and rep.reasons == ()
    assert tuple(rep.checks) == I.CHECK_KEYS and all(rep.checks.values())
    assert rep.n_signals == len(BASE)
    assert events_equal(rep.base_events, BASE) == (True, None)
    assert rep.timings == {"_total": 1.0}


def test_assess_check_flags_truncation_look_ahead_with_the_first_differing_bar():
    bad = ev([50, 150, 250, 399])                         # bar 399 appears only when bars >= 400 are gone
    rep = C.assess_check(PLAN, decoded({"cut40": bad}), N)
    assert not rep.passed and not rep.checks["causality"]
    assert rep.checks["determinism"] and rep.checks["scramble"] and rep.checks["sandbox"]
    assert ("causality: outputs before the 40% cut changed when later bars were removed "
            "(first differing bar 399)") in rep.reasons


def test_assess_check_flags_nondeterminism_scramble_and_zero_signals():
    rep = C.assess_check(PLAN, decoded({"full2": ev([50, 151, 250, 450, 650, 850, 990])}), N)
    assert not rep.checks["determinism"] and rep.checks["causality"]
    assert "determinism: two identical runs differed (first differing bar 150)" in rep.reasons
    rep = C.assess_check(PLAN, decoded({"scramble": ev([50, 150, 250])}), N)
    assert not rep.checks["scramble"] and rep.checks["causality"]
    assert any(r.startswith("scramble: outputs before bar 500 changed") and "first differing bar 450" in r
               for r in rep.reasons)
    exits_only = ev([5, 9], signal=0)
    exits_only["exit"] = True
    exits_only["stop_dist"] = np.nan
    rep = C.assess_check(PLAN, decoded({r.run_id: exits_only for r in PLAN}), N)
    assert not rep.checks["min_signals"] and rep.n_signals == 0 and not rep.passed
    assert "min_signals: full1 produced 0 entry signals (need >= 1)" in rep.reasons


def test_assess_check_groups_run_failures_and_separates_sandbox_from_schema():
    msg = "KeyError: 'close_' (strategy.py line 22)\nTraceback ..."
    errs = [I.RunError(r.run_id, "exception", msg) for r in PLAN]
    rep = C.assess_check(PLAN, decoded(errors=errs, drop=[r.run_id for r in PLAN]), N)
    assert not rep.passed and not rep.checks["sandbox"] and rep.checks["schema"]
    assert ("sandbox: cut20, cut40, cut60, cut80, cut95, scramble, full1, full2 exception: "
            "KeyError: 'close_' (strategy.py line 22)") in rep.reasons
    assert rep.base_events is None and rep.n_signals == 0
    assert not rep.checks["determinism"] and not rep.checks["causality"] and not rep.checks["min_signals"]

    rep = C.assess_check(PLAN, decoded(errors=[I.RunError("cut20", "decode", "bad")], drop=["cut20"]), N)
    assert rep.checks["sandbox"] and not rep.checks["schema"] and not rep.checks["causality"]
    assert rep.checks["determinism"] and rep.checks["scramble"]

    rep = C.assess_check(PLAN, decoded(errors=[I.RunError(None, "timeout", "sandbox job exceeded 300 s")]), N)
    assert not rep.checks["sandbox"] and not rep.passed
    assert "sandbox: job timeout: sandbox job exceeded 300 s" in rep.reasons


def test_assess_check_reasons_never_carry_performance_words():
    errs = [I.RunError("full2", "value", "stop_dist must be finite")]
    rep = C.assess_check(PLAN, decoded({"cut40": ev([1])}, errors=errs, drop=["full2"]), N)
    text = " ".join(rep.reasons).lower()
    for word in ("net", "profit", "pf ", "sharpe", "drawdown", "$", "return"):
        assert word not in text


# ── canaries straight into the sandbox (they would also fail lint) ──────────
# M15 from Monday 00:00 (4h buckets of 16 bars). 1210 bars puts every truncation cut mid-bucket;
# with 1200 bars four of the five cuts fall exactly on 4h boundaries, where an unshifted resample
# leaks nothing that truncation can see (the scramble run still catches it).
FRAME = synthetic_frame(1210, seed=5)

_HEAD = '''
    import numpy as np
    import pandas as pd
    from sajim_quant.lab import indicators as ind

    PARAMS = {"n": {"default": 20, "min": 5, "max": 50, "type": "int"}}

    def _out(df, cond):
        out = pd.DataFrame(index=df.index)
        out["signal"] = cond.fillna(False).astype("int8")
        out["stop_dist"] = 0.5
        out["tp_dist"] = np.nan
        return out
'''

CANARIES = {
    "benign": '''
        def generate_signals(df, params):
            m = ind.sma(df["close"], params["n"])
            return _out(df, (df["close"] > m) & (df["close"].shift(1) <= m.shift(1)))
    ''',
    "shift_minus_1": '''
        def generate_signals(df, params):
            return _out(df, df["close"].shift(-1) > df["close"])
    ''',
    "centered_rolling": '''
        def generate_signals(df, params):
            m = df["close"].rolling(params["n"] + 1, center=True).mean()
            return _out(df, (df["close"] > m) & (df["close"].shift(1) <= m.shift(1)))
    ''',
    "unshifted_resample": '''
        def generate_signals(df, params):
            h4 = df["close"].resample("4h").last().reindex(df.index, method="ffill")
            return _out(df, (df["close"] < h4) & (df["close"].shift(1) >= h4.shift(1)))
    ''',
    "whole_series_stat": '''
        def generate_signals(df, params):
            return _out(df, df["close"] > df["close"].quantile(0.9))
    ''',
    "unseeded_random": '''
        def generate_signals(df, params):
            return _out(df, pd.Series(np.random.default_rng().random(len(df)) < 0.05, index=df.index))
    ''',
}


@pytest.fixture(scope="module")
def canary_reports():
    jobs, plans = {}, {}
    for name, body in CANARIES.items():
        src = strategy(_HEAD) + strategy(body)
        plans[name] = C.build_check_plan(len(FRAME), {"n": 20}, I.sha256_hex(src))
        jobs[name] = make_job(src, plans[name], FRAME, label=f"canary_{name}")
    out = {}
    for name, (res, dec) in run_jobs(jobs).items():
        out[name] = (C.assess_check(plans[name], dec, len(FRAME)), dec)
    return out


def test_benign_strategy_passes_every_integrity_check(canary_reports):
    rep, dec = canary_reports["benign"]
    assert rep.passed, rep.reasons
    assert all(rep.checks.values()) and rep.n_signals >= gates.MIN_SIGNALS
    assert rep.base_events is not None and rep.base_events.dtype == I.EVENTS_DTYPE
    assert dec.sensitive_env_names == ()


@pytest.mark.parametrize("name", ["shift_minus_1", "centered_rolling", "unshifted_resample", "whole_series_stat"])
def test_look_ahead_canaries_fail_the_integrity_check(canary_reports, name):
    """Each canary runs cleanly but is caught by truncation causality and/or future-scramble."""
    rep, _ = canary_reports[name]
    assert not rep.passed
    assert rep.checks["sandbox"] and rep.checks["schema"] and rep.checks["determinism"]
    assert not (rep.checks["causality"] and rep.checks["scramble"]), rep.reasons
    assert any(r.startswith(("causality: outputs before the", "scramble: outputs before bar")) for r in rep.reasons)


@pytest.mark.parametrize("name", ["shift_minus_1", "centered_rolling", "unshifted_resample", "whole_series_stat"])
def test_look_ahead_canaries_fail_truncation_causality(canary_reports, name):
    rep, _ = canary_reports[name]
    assert not rep.checks["causality"], rep.reasons


# ── adaptive cuts (INTERFACES §5.2, p1.2) ───────────────────────────────────
def _sparse_sma_cross(df, peek=0, with_exit=False):
    """A sparse SMA(200) cross (~200 signals in 20k bars); peek > 0 also filters on the close `peek` bars ahead."""
    import pandas as pd

    c = df["close"]
    m = c.rolling(200).mean()
    cond = (c > m) & (c.shift(1) <= m.shift(1))
    if peek:
        nxt = pd.Series(np.r_[c.to_numpy()[peek:], [np.nan] * peek], index=c.index)   # lint cannot see this
        cond &= nxt > c
    out = pd.DataFrame(index=df.index)
    out["signal"] = cond.astype("int8")
    out["stop_dist"] = 0.5
    out["tp_dist"] = np.nan
    if with_exit:
        out["exit"] = ((c < m) & (c.shift(1) >= m.shift(1))).to_numpy()
    return out


def _decode_in_process(plan, df, fn):
    """Runner validation in-process (no subprocess): the same frames and sparse events the sandbox makes."""
    from sajim_quant.lab import sandbox_runner as R

    events = {}
    for r in plan:
        scr = None if r.scramble is None else {"start": r.scramble.start, "seed": r.scramble.seed}
        frame = R._run_frame(df, {"cut": r.cut, "scramble": scr})
        events[r.run_id] = R.dense_to_events(fn(frame), frame.index, allow_short=False, max_events=len(frame))
    return I.DecodedJob(events, (), {}, {}, ())


def _check_with_adaptive(df, fn, sha, stage="dev", reveal_bars=True):
    plan = C.build_check_plan(len(df), {}, sha)
    rep = C.assess_check(plan, _decode_in_process(plan, df, fn), len(df), reveal_bars=reveal_bars)
    adaptive = C.build_adaptive_plan(rep.base_events, len(df), {}, sha, stage)
    return rep, adaptive, C.assess_adaptive(rep, adaptive, _decode_in_process(adaptive, df, fn),
                                            reveal_bars=reveal_bars)


@pytest.mark.parametrize("peek", [3, 1])
def test_sparse_entry_with_a_slice_peek_is_caught(peek):
    """
    The part-1 gap: the 5 fixed cuts + scramble caught a sparse 3-bar slice peek in only 3 of 40
    seeds. The adaptive cuts, placed 1..3 bars after full1's own signal bars, catch it on every seed.
    """
    fixed_missed, missed = [], []
    for seed in range(10):
        df = synthetic_frame(20000, seed=seed)
        fixed, adaptive, rep = _check_with_adaptive(df, lambda d: _sparse_sma_cross(d, peek), f"{seed:064x}")
        assert len(adaptive) == C.ADAPTIVE_CUTS
        fixed_missed.append(fixed.passed)
        if rep.passed:
            missed.append(seed)
        else:
            assert not rep.checks["causality"] and rep.checks["sandbox"] and rep.checks["determinism"]
            assert any(r.startswith("causality: outputs before an adaptive cut (") for r in rep.reasons)
    assert missed == []
    assert sum(fixed_missed) >= 5              # the fixed checks alone miss most seeds (why §5.2 changed)


def test_a_clean_sparse_strategy_is_never_flagged_by_the_adaptive_cuts():
    for seed in range(10):
        df = synthetic_frame(20000, seed=seed)
        fixed, adaptive, rep = _check_with_adaptive(df, lambda d: _sparse_sma_cross(d, 0, with_exit=True),
                                                    f"{seed:064x}")
        assert fixed.passed, fixed.reasons
        assert len(adaptive) == C.ADAPTIVE_CUTS
        assert rep.passed and rep.reasons == (), (seed, rep.reasons)
        assert all(rep.checks.values()) and rep.n_signals == fixed.n_signals
        assert events_equal(rep.base_events, fixed.base_events) == (True, None)


def test_adaptive_plan_is_frozen_seeded_and_placed_just_after_event_bars():
    base = ev([5, 50, 150, 250, 450, 650, 850, 990, 998])
    exits = ev([300, 700], signal=0)
    exits["exit"] = True
    exits["stop_dist"] = np.nan
    both = np.sort(np.concatenate([base, exits]), order="bar")
    plan = C.build_adaptive_plan(both, N, {"a": 3}, SHA, "dev")
    assert [r.run_id for r in plan] == [f"acut{j}" for j in range(1, len(plan) + 1)]
    assert len(plan) == C.ADAPTIVE_CUTS and all(C.is_adaptive_run(r.run_id) for r in plan)
    cuts = [r.cut for r in plan]
    assert cuts == sorted(cuts) and len(set(cuts)) == len(cuts) and all(1 <= c < N for c in cuts)
    assert all(dict(r.params) == {"a": 3} and r.scramble is None for r in plan)
    bars = both["bar"]
    offsets = [c - int(bars[bars < c].max()) for c in cuts]
    assert all(1 <= d <= C.ADAPTIVE_MAX_OFFSET for d in offsets) and 1 in offsets
    exit_cuts = [c for c in cuts if int(bars[bars < c].max()) in (300, 700)]
    assert len(exit_cuts) == C.ADAPTIVE_CUTS // 3               # a third of the cuts follow exit-only events
    # frozen: same sha+stage -> same plan; another stage or sha -> a different draw
    assert C.build_adaptive_plan(both, N, {"a": 3}, SHA, "dev") == plan
    assert [r.cut for r in C.build_adaptive_plan(both, N, {"a": 3}, SHA, "holdout")] != cuts
    want = int.from_bytes(hashlib.sha256(f"sajim.lab.adaptive/1|{SHA}|dev".encode()).digest()[:8], "big") & (2**63 - 1)
    assert C.adaptive_seed(SHA, "dev") == want
    # min_bar keeps the cuts in the scored window when it has events; no events -> no plan
    assert all(r.cut > 450 for r in C.build_adaptive_plan(both, N, {}, SHA, "dev", min_bar=451))
    assert C.build_adaptive_plan(I.empty_events(), N, {}, SHA, "dev") == ()
    assert C.build_adaptive_plan(None, N, {}, SHA, "dev") == ()
    assert len(C.build_adaptive_plan(base[:2], N, {}, SHA, "dev")) == 2       # fewer events than k
    assert C.build_adaptive_plan(base, N, {}, SHA, "dev", k=0) == ()
    assert all(r.cut <= N - 1 for r in C.build_adaptive_plan(ev([N - 2]), N, {}, SHA, "dev"))


def test_assess_adaptive_folds_failures_into_the_report():
    base_rep = C.assess_check(PLAN, decoded(), N)
    plan = C.build_adaptive_plan(base_rep.base_events, N, {"a": 1}, SHA, "dev")

    def job(overrides=None, errors=(), drop=()):
        events = {r.run_id: BASE[BASE["bar"] < r.cut].copy() for r in plan}
        events.update(overrides or {})
        for rid in drop:
            events.pop(rid, None)
        return I.DecodedJob(events, tuple(errors), {"acut1": 0.5, "nb00": 9.0}, {}, ())

    assert C.assess_adaptive(base_rep, (), job()) is base_rep
    ok = C.assess_adaptive(base_rep, plan, job(errors=[I.RunError("nb00", "timeout", "neighbour slow")]))
    assert ok.passed and ok.reasons == () and ok.timings["acut1"] == 0.5 and "nb00" not in ok.timings
    first = plan[0]
    gone = BASE[BASE["bar"] < first.cut][:-1]                  # the event just before the cut vanished
    rep = C.assess_adaptive(base_rep, plan, job({first.run_id: gone}))
    assert not rep.passed and not rep.checks["causality"] and rep.checks["sandbox"]
    (reason,) = rep.reasons
    anchor = int(BASE["bar"][BASE["bar"] < first.cut].max())
    assert reason.startswith(f"causality: outputs before an adaptive cut ({first.cut - anchor} bar(s) after a "
                             f"signal on bar {anchor}) changed when later bars were removed (first differing "
                             f"bar {anchor})")
    rep = C.assess_adaptive(base_rep, plan, job(errors=[I.RunError(first.run_id, "timeout", "too slow")],
                                                drop=[first.run_id]))
    assert not rep.passed and not rep.checks["sandbox"] and not rep.checks["causality"]
    assert f"sandbox: {first.run_id} timeout: too slow" in rep.reasons
    rep = C.assess_adaptive(base_rep, plan, job(errors=[I.RunError(None, "timeout", "sandbox job exceeded 300 s")]))
    assert not rep.passed and "sandbox: adaptive-cut job timeout: sandbox job exceeded 300 s" in rep.reasons
    rep = C.assess_adaptive(base_rep, plan, job(errors=[I.RunError(first.run_id, "decode", "bad")],
                                                drop=[first.run_id]))
    assert not rep.checks["schema"] and rep.checks["sandbox"] and not rep.passed


def test_holdout_reasons_never_carry_a_bar_index():
    """reveal_bars=False (holdout): no bar numbers in trusted text, digits masked in untrusted text."""
    rep = C.assess_check(PLAN, decoded({"cut40": ev([50, 150, 250, 399]), "full2": ev([50, 151]),
                                        "scramble": ev([50, 150, 250])},
                                       errors=[I.RunError("cut20", "exception", "IndexError: index 41522 (line 7)")],
                                       drop=["cut20"]), N, reveal_bars=False)
    assert not rep.passed

    def bar_free(text):          # run ids (cut20, full1, acut3) and "40% cut" are not bar indices
        return not any(ch.isdigit() for ch in re.sub(r"\b(?:a?cut\d+|full\d|\d+% cut)\b", "", text))

    for r in rep.reasons:
        assert bar_free(r), r
    assert "sandbox: cut20 exception: IndexError: index # (line #)" in rep.reasons
    df = synthetic_frame(20000, seed=1)
    _, adaptive, rep = _check_with_adaptive(df, lambda d: _sparse_sma_cross(d, 3), "1" * 64, stage="holdout",
                                            reveal_bars=False)
    assert not rep.passed and adaptive
    assert all(bar_free(r) for r in rep.reasons), rep.reasons
    assert any("adaptive cut (placed just after one of the strategy's own event bars)" in r for r in rep.reasons)


def test_unseeded_randomness_fails_determinism(canary_reports):
    rep, _ = canary_reports["unseeded_random"]
    assert not rep.passed and not rep.checks["determinism"]
    assert any(r.startswith("determinism: two identical runs differed") for r in rep.reasons)
