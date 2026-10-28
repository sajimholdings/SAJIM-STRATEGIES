"""
SAJIM Strategy Lab — trusted orchestration of one check / DEV / holdout evaluation
(INTERFACES.md sections 4 and 8).

Untrusted strategy code only ever runs inside the SandboxExecutor (a fresh modal.Sandbox
in production: no network, no secrets, no volumes, no modal package). What comes back is
decoded by sandbox_io's strict non-pickle decoder; everything after that — simulation,
robustness, gates, verdict, scorecard — happens here, on the trusted side.

Sandbox jobs per evaluation (INTERFACES.md section 8, p1.2):
  * check:    2 jobs (the 8 frozen check runs; then, if those passed, <= 6 adaptive cuts)
  * DEV:      2 jobs (the check job, whose full1 is the base run; then the adaptive cuts AND
              the <= 10 plateau neighbours together, <= MAX_RUNS_PER_JOB runs)
  * holdout:  2 jobs (the check job on the holdout frame, full1 = base; then the adaptive cuts)
The second job runs only when the first one passed (for the holdout: passed apart from a
zero signal count). Cost stress, delay, folds, the random-entry null and break-even run in
the simulator only. Every sandbox run gets interfaces.PER_RUN_TIMEOUT_S; neighbours stopped
for time make G7 "not computable" with a runtime reason instead of a plateau result.

Windows are half-open naive UTC: frames are cut with splits.window_mask(), never by
comparing against *_END constants. The Modal job (integrator) loads data, calls
prepare_frame + core.fx.quote_rate_series, and writes the artefacts and ledger records.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from sajim_quant.analytics.metrics import calculate_metrics
from sajim_quant.core.instruments import InstrumentSpec, cost_assumptions, get_spec
from sajim_quant.core.schema import BacktestResult
from sajim_quant.lab import gates, robustness, scorecard, splits
from sajim_quant.lab.interfaces import (
    CHECK_KEYS, MAX_RUNS_PER_JOB, PER_RUN_TIMEOUT_S, RUN_TIMEOUT_KINDS, BudgetInfo, CheckReport, ContractRefused,
    DecodedJob, DevNotPassed, Evaluation, GateResult, LedgerStore, SandboxExecutor, SandboxJob, SandboxRun,
    SimOverrides, SimResult, SimulateFn, StageFrame, Submission, VerdictOutcome, result_file_name,
)

logger = logging.getLogger(__name__)

OHLCV = ("open", "high", "low", "close", "volume")


# ── Frames ──────────────────────────────────────────────────────────────────
def prepare_frame(raw: pd.DataFrame, stage: str, warmup_bars: int,
                  reference_end: Optional[datetime] = None) -> StageFrame:
    """
    The stage frame: [first in-window bar - warmup, last in-window bar], scored window
    [start_bar, stop_bar). Refuses (ContractRefused code="coverage") a window with no bars
    or below splits.MIN_COVERAGE.
    """
    stage = splits.normalize_stage(stage)
    if not isinstance(raw, pd.DataFrame) or not isinstance(raw.index, pd.DatetimeIndex):
        raise ValueError("raw must be a DataFrame with a DatetimeIndex")
    missing = [c for c in OHLCV if c not in raw.columns]
    if missing:
        raise ValueError(f"raw frame is missing columns {missing}")
    if not (raw.index.is_monotonic_increasing and raw.index.is_unique):
        raise ValueError("raw frame index must be ascending and unique")
    if warmup_bars < 0:
        raise ValueError("warmup_bars must be >= 0")
    mask = np.asarray(splits.window_mask(raw.index, stage), dtype=bool)
    pos = np.flatnonzero(mask)
    if len(pos) == 0:
        raise ContractRefused(f"no {stage} bars in the data", code="coverage")
    w0, w_last = int(pos[0]), int(pos[-1])
    if not splits.has_min_coverage(stage, raw.index[w0], raw.index[w_last], reference_end):
        cov = splits.coverage(stage, raw.index[w0], raw.index[w_last], reference_end)
        raise ContractRefused(f"{stage} coverage {cov:.0%} is below {splits.MIN_COVERAGE:.0%}", code="coverage")
    offset = max(0, w0 - int(warmup_bars))
    frame = raw.iloc[offset:w_last + 1][list(OHLCV)].astype("float64")
    frame.index = frame.index.rename("time")
    return StageFrame(frame=frame, stage=stage, start_bar=w0 - offset, stop_bar=len(frame),
                      warmup_bars=int(warmup_bars))


def _bar_time(sframe: StageFrame, i: int) -> datetime:
    return splits.to_utc_naive(sframe.frame.index[i])


def _first_last(sframe: StageFrame) -> Tuple[datetime, datetime]:
    return _bar_time(sframe, sframe.start_bar), _bar_time(sframe, sframe.stop_bar - 1)


# ── Simulator binding ───────────────────────────────────────────────────────
def make_simulate(sframe: StageFrame, spec: InstrumentSpec, quote_rate: np.ndarray, *, max_hold_bars: int,
                  allow_short: bool, strategy_name: str, timeframe: str) -> SimulateFn:
    """simulate(events, overrides) on this frame; overrides.window None = the scored window."""
    from sajim_quant.lab.simulator import SignalSimulator

    rate = np.asarray(quote_rate, dtype=np.float64)
    frame = sframe.frame

    def simulate(events: np.ndarray, overrides: SimOverrides = SimOverrides()) -> SimResult:
        sim = SignalSimulator(spec, cost_mult=overrides.cost_mult, delay_bars=overrides.delay_bars,
                              max_hold_bars=max_hold_bars, allow_short=allow_short,
                              window=overrides.window if overrides.window is not None else sframe.window,
                              strategy_name=strategy_name, timeframe=timeframe)
        return sim.run(frame, events, rate)

    return simulate


# ── Integrity check ─────────────────────────────────────────────────────────
def _failed_report(reasons: List[str], **flags: bool) -> CheckReport:
    checks = {k: False for k in CHECK_KEYS}
    checks.update(flags)
    return CheckReport(passed=False, reasons=tuple(reasons), checks=checks, n_signals=0, base_events=None)


def _manifest_bool(sub: Submission, key: str) -> bool:
    return bool(sub.manifest.get(key, False))


def _reveal_bars(sframe: StageFrame) -> bool:
    """Bar indices may appear in reasons on DEV only; never for the sealed holdout."""
    return sframe.stage != splits.HOLDOUT


def _run_job(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, runs: Tuple[SandboxRun, ...], *,
             label: str, indicators_py: Optional[bytes]) -> DecodedJob:
    """One sandbox job on the full stage frame, decoded by the strict non-pickle decoder."""
    from sajim_quant.lab import sandbox_io

    job = SandboxJob(frame_parquet=sandbox_io.frame_to_parquet(sframe.frame), n_bars=len(sframe.frame),
                     strategy_py=sub.strategy_py, indicators_py=indicators_py or sandbox_io.indicators_source(),
                     runs=tuple(runs), allow_short=_manifest_bool(sub, "allow_short"), label=label)
    decoded = sandbox_io.decode_result(job, executor.run(job))
    if decoded.sensitive_env_names:
        logger.error("SECURITY: sandbox saw sensitive env var names %s", list(decoded.sensitive_env_names))
    return decoded


def _fixed_check(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *,
                 indicators_py: Optional[bytes]) -> CheckReport:
    """Lint + manifest, then the first sandbox job with the 8 frozen check runs."""
    from sajim_quant.lab import causality, contract

    lint = [f"lint: {i.format('strategy.py')}" for i in contract.lint_source(sub.strategy_py)]
    man = contract.validate_manifest(sub.manifest, strategy_py=sub.strategy_py, spec_md=sub.spec_md,
                                     require_shas=True)
    if sub.manifest.get("id") != sub.lab_id:
        man.append("manifest: id does not match the submission's lab_id")
    if sub.manifest.get("family", sub.lab_id) != sub.family:
        man.append("manifest: family does not match the submission's family")
    if lint or man:
        return _failed_report(lint + man, lint=not lint, manifest=not man)

    params = contract.default_params(contract.parse_params(sub.strategy_py))
    n = len(sframe.frame)
    plan = causality.build_check_plan(n, params, sub.code_sha256)
    decoded = _run_job(sub, sframe, executor, plan, label="check", indicators_py=indicators_py)
    return causality.assess_check(plan, decoded, n, reveal_bars=_reveal_bars(sframe))


def _passed_but_signals(chk: CheckReport) -> bool:
    """Every integrity check passed except (possibly) the minimum signal count."""
    return chk.base_events is not None and all(ok for k, ok in chk.checks.items() if k != "min_signals")


def _adaptive_plan(sub: Submission, sframe: StageFrame, chk: CheckReport, *, k: int) -> Tuple[SandboxRun, ...]:
    """The adaptive cuts for this sha and stage, or () when the first job did not pass."""
    from sajim_quant.lab import causality, contract

    if k <= 0 or not _passed_but_signals(chk):
        return ()
    params = contract.default_params(contract.parse_params(sub.strategy_py))
    return causality.build_adaptive_plan(chk.base_events, len(sframe.frame), params, sub.code_sha256,
                                         sframe.stage, k=k, min_bar=sframe.start_bar)


def _check_with_adaptive(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *,
                         indicators_py: Optional[bytes]) -> CheckReport:
    from sajim_quant.lab import causality

    chk = _fixed_check(sub, sframe, executor, indicators_py=indicators_py)
    runs = _adaptive_plan(sub, sframe, chk, k=causality.ADAPTIVE_CUTS)
    if runs:
        decoded = _run_job(sub, sframe, executor, runs, label="adaptive", indicators_py=indicators_py)
        chk = causality.assess_adaptive(chk, runs, decoded, reveal_bars=_reveal_bars(sframe))
    return chk


def check(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *,
          indicators_py: Optional[bytes] = None) -> CheckReport:
    """
    Lint + manifest, then the check job (8 frozen runs) and, when it passed, a second job with
    the adaptive cuts after full1's own event bars, so a free check predicts DEV's G0. Never a trial.
    """
    report = _check_with_adaptive(sub, sframe, executor, indicators_py=indicators_py)
    logger.info("check %s %s: passed=%s", sub.lab_id, sub.sha12, report.passed)
    return report


# ── Shared evaluation pieces ────────────────────────────────────────────────
def _n_params(sub: Submission) -> Optional[int]:
    from sajim_quant.lab import contract
    try:
        return len(contract.parse_params(sub.strategy_py))
    except contract.ContractError:
        return None


def _cost_model(spec: InstrumentSpec, symbol: str, fx_desc: str, *, breakeven: Optional[float] = None,
                breakeven_flag: Optional[str] = None) -> Dict[str, Any]:
    ca = cost_assumptions(symbol)
    return {"spread_model": ca["spread_model"], "commission_model": gates.COMMISSION_MODEL,
            "commission_rate": ca["commission_rate"], "slippage_pips": ca["slippage_pips"],
            "round_trip_bps": spec.round_trip_bps, "slippage_points": spec.slippage_points,
            "fx_desc": fx_desc, "breakeven_cost_mult": breakeven, "breakeven_flag": breakeven_flag}


def _context(sim: Optional[SimResult]) -> Dict[str, Any]:
    if sim is None:
        return {}
    return {"window_end_closes": sim.window_end_closes, "untradable_stop": sim.untradable_stop,
            "n_signals": sim.n_signals, "halted": sim.halted}


def _split_metrics(*dicts: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge metric dicts; their `detail` blocks are merged separately (server-only)."""
    metrics: Dict[str, Any] = {}
    detail: Dict[str, Any] = {}
    for d in dicts:
        for k, v in d.items():
            if k == "detail":
                detail.update(v)
            else:
                metrics[k] = v
    return metrics, detail


def _check_detail(chk: CheckReport) -> Dict[str, Any]:
    return {"check": {"passed": chk.passed, "checks": dict(chk.checks), "reasons": list(chk.reasons)}}


def _neighbour_failures(decoded: Optional[DecodedJob], neighbours: List[Any]) -> Dict[str, str]:
    """run_id -> error kind for every neighbour without valid events (job-level kinds apply to all)."""
    if decoded is None:
        return {}
    job_kind = next((e.kind for e in decoded.errors if e.run_id is None), None)
    out: Dict[str, str] = {}
    for nb in neighbours:
        if decoded.ok(nb.run_id):
            continue
        kind = next((e.kind for e in decoded.errors if e.run_id == nb.run_id), None)
        out[nb.run_id] = kind or job_kind or "missing_output"
    return out


def _plateau_runtime(plateau: Dict[str, Any], failures: Mapping[str, str],
                     n: int) -> Tuple[List[str], List[str]]:
    """
    (lead reasons, trailing reasons). Neighbours stopped for time (kind timeout/budget) make G7
    not computable (value None, a fail) with a runtime reason listed FIRST, so a slow strategy is
    told why instead of seeing a plateau number. Other failed neighbours keep counting as losing
    neighbours (section 6.3); that note goes after the gate reasons.
    """
    reasons: List[str] = []
    notes: List[str] = []
    timed_out = sorted(r for r, k in failures.items() if k in RUN_TIMEOUT_KINDS)
    other = sorted(r for r, k in failures.items() if k not in RUN_TIMEOUT_KINDS)
    detail = plateau.setdefault("detail", {}).setdefault("plateau", {})
    detail["timed_out"] = timed_out
    detail["failed"] = {r: failures[r] for r in other}
    if timed_out:
        plateau["plateau_positive_frac"] = None
        plateau["plateau_median_pf"] = None
        reasons.append(f"G7 plateau not computable: {len(timed_out)} of {n} neighbour runs were stopped for time "
                       f"(each run gets {PER_RUN_TIMEOUT_S:g} s including process start-up); this is a runtime "
                       f"problem, not a plateau result: make generate_signals faster")
    if other:
        kinds = ", ".join(sorted({failures[r] for r in other}))
        notes.append(f"plateau: {len(other)} of {n} neighbour runs failed ({kinds}) and count as losing "
                     f"neighbours")
    return reasons, notes


def _params(sub: Submission) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from sajim_quant.lab import contract
    try:
        spec = contract.parse_params(sub.strategy_py)
    except contract.ContractError:
        return {}, {}
    return spec, contract.default_params(spec)


# ── DEV ─────────────────────────────────────────────────────────────────────
def evaluate_dev(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *, quote_rate: np.ndarray,
                 fx_desc: str, budget: BudgetInfo, deploy_id: str = "",
                 indicators_py: Optional[bytes] = None) -> Evaluation:
    """One DEV trial (the caller has already claimed and recorded the trial slot)."""
    from sajim_quant.lab.simulator import to_backtest_result

    if sframe.stage != splits.DEV:
        raise ValueError(f"evaluate_dev needs a DEV frame, got {sframe.stage!r}")
    m = sub.manifest
    symbol, timeframe = m.get("symbol"), m.get("timeframe")
    spec = get_spec(symbol)
    params_spec, params = _params(sub)
    rfile = result_file_name(sub.lab_id, symbol, timeframe, splits.DEV)
    chk = _fixed_check(sub, sframe, executor, indicators_py=indicators_py)

    def integrity_failed(report: CheckReport) -> Evaluation:
        metrics, detail = _split_metrics(robustness.integrity_metrics(
            report, None, result=None, fx_desc=fx_desc, spec=spec, n_params=_n_params(sub)))
        detail.update(_check_detail(report))
        return _finish(sub, splits.DEV, sframe, params, metrics, detail, budget, perf={}, sim=None, result=None,
                       chk=report, cost_model=_cost_model(spec, symbol, fx_desc), holdout_split=None,
                       result_file=rfile, deploy_id=deploy_id, extra_reasons=list(report.reasons))

    if not chk.passed or chk.base_events is None:
        return integrity_failed(chk)

    # the second (last) sandbox job: adaptive cuts first, then the plateau neighbours
    from sajim_quant.lab import causality
    neighbours = robustness.plateau_neighbours(params_spec, params)
    adaptive = _adaptive_plan(sub, sframe, chk, k=min(causality.ADAPTIVE_CUTS, MAX_RUNS_PER_JOB - len(neighbours)))
    runs = adaptive + tuple(SandboxRun(nb.run_id, dict(nb.params)) for nb in neighbours)
    decoded: Optional[DecodedJob] = None
    if runs:
        decoded = _run_job(sub, sframe, executor, runs, label="adaptive_neighbours", indicators_py=indicators_py)
        chk = causality.assess_adaptive(chk, adaptive, decoded, reveal_bars=True)
        if not chk.passed:
            return integrity_failed(chk)
    nb_events: Dict[str, Optional[np.ndarray]] = {
        nb.run_id: (decoded.events[nb.run_id] if decoded is not None and decoded.ok(nb.run_id) else None)
        for nb in neighbours}
    nb_failures = _neighbour_failures(decoded, neighbours)

    simulate = make_simulate(sframe, spec, quote_rate, max_hold_bars=int(m["max_hold_bars"]),
                             allow_short=bool(m.get("allow_short", False)), strategy_name=sub.lab_id,
                             timeframe=timeframe)
    events = chk.base_events
    base = simulate(events, SimOverrides())
    result = to_backtest_result(base, lab_id=sub.lab_id, symbol=symbol, timeframe=timeframe, stage=splits.DEV,
                                code_sha256=sub.code_sha256, params=params, fx_desc=fx_desc)
    first, last = _first_last(sframe)
    dev_months = splits.months(splits.DEV)
    perf = robustness.performance_metrics(result, months=dev_months, first_bar=first, last_bar=last)

    plateau = robustness.plateau_metrics(simulate, nb_events, neighbours)
    runtime_reasons, plateau_notes = _plateau_runtime(plateau, nb_failures, len(neighbours))
    stress = robustness.stress_metrics(simulate, events)
    metrics, detail = _split_metrics(
        robustness.integrity_metrics(chk, base, result=result, fx_desc=fx_desc, spec=spec,
                                     n_params=_n_params(sub)),
        robustness.sample_metrics(base, splits.DEV, months=dev_months),
        {k: perf[k] for k in ("pf", "expectancy", "max_dd_frac", "top5_profit_share",
                              "max_month_profit_share", "min_side_pf")},
        robustness.null_metrics(simulate, base, sframe.window, code_sha256=sub.code_sha256, stage=splits.DEV),
        robustness.fold_metrics(simulate, events, sframe.window),
        stress,
        plateau,
    )
    detail.update(_check_detail(chk))
    cost = _cost_model(spec, symbol, fx_desc, breakeven=stress["breakeven_cost_mult"],
                       breakeven_flag=stress["breakeven_flag"])
    return _finish(sub, splits.DEV, sframe, params, metrics, detail, budget, perf=perf, sim=base, result=result,
                   chk=chk, cost_model=cost, holdout_split=None, result_file=rfile, deploy_id=deploy_id,
                   lead_reasons=runtime_reasons, extra_reasons=plateau_notes)


def _finish(sub: Submission, stage: str, sframe: StageFrame, params: Mapping, metrics: Mapping,
            detail: Mapping, budget: BudgetInfo, *, perf: Mapping, sim: Optional[SimResult],
            result: Optional[BacktestResult], chk: CheckReport, cost_model: Mapping,
            holdout_split: Optional[Mapping], result_file: str, deploy_id: str,
            gate_results: Optional[List[GateResult]] = None, warnings: Tuple[str, ...] = (),
            extra_reasons: Optional[List[str]] = None, lead_reasons: Optional[List[str]] = None,
            dev_hard_gates_passed: Optional[bool] = None) -> Evaluation:
    if gate_results is None:
        gate_results = scorecard.evaluate_gates(metrics)
    outcome = scorecard.verdict(gate_results, stage, budget.budget_exhausted, holdout_burned=budget.holdout_burned)
    if extra_reasons or lead_reasons:
        lead = tuple(scorecard.scrub(r) for r in lead_reasons or ())
        extra = tuple(scorecard.scrub(r) for r in extra_reasons or ())
        body = tuple(r for r in outcome.reasons if r not in lead)
        outcome = VerdictOutcome(outcome.verdict, outcome.final,
                                 lead + body + tuple(r for r in extra if r not in body and r not in lead))
    sc = scorecard.build_scorecard(submission=sub, stage=stage, sframe=sframe, params=params,
                                   gate_results=gate_results, outcome=outcome, budget=budget, perf=perf,
                                   holdout_split=holdout_split, cost_model=cost_model, context=_context(sim),
                                   detail=detail, result_file=result_file, deploy_id=deploy_id,
                                   warnings=warnings, dev_hard_gates_passed=dev_hard_gates_passed)
    logger.info("%s %s %s: verdict %s (final=%s)", stage, sub.lab_id, sub.sha12, outcome.verdict, outcome.final)
    return Evaluation(scorecard=sc, result=result, check=chk, gate_results=list(gate_results), verdict=outcome)


# ── HOLDOUT ─────────────────────────────────────────────────────────────────
def _require_dev_pass(sub: Submission, dev_scorecard: Mapping, store: Optional[LedgerStore]) -> None:
    problems = []
    if dev_scorecard.get("stage") != splits.DEV:
        problems.append("the DEV scorecard is not a dev-stage scorecard")
    if dev_scorecard.get("code_sha256") != sub.code_sha256:
        problems.append("the DEV scorecard is for a different code sha")
    if dev_scorecard.get("family") != sub.family:
        problems.append("the DEV scorecard is for a different family")
    if dev_scorecard.get("dev_hard_gates_passed") is not True:
        problems.append("not every hard DEV gate passed for this sha")
    # code_sha256 covers strategy.py only; the manifest must still describe what DEV judged.
    for key, want in (("id", sub.lab_id), ("symbol", sub.manifest.get("symbol")),
                      ("timeframe", sub.manifest.get("timeframe"))):
        if dev_scorecard.get(key) != want:
            problems.append(f"the DEV scorecard is for a different {key}")
    if store is not None:
        from sajim_quant.lab import ledger
        if ledger.dev_pass_record(store, sub.family, sub.code_sha256) is None:
            problems.append("no DEV-pass ledger record for this sha")
    if problems:
        raise DevNotPassed("holdout refused: " + "; ".join(problems), reasons=problems)


def evaluate_holdout(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *, quote_rate: np.ndarray,
                     fx_desc: str, budget: BudgetInfo, dev_scorecard: Mapping, deploy_id: str = "",
                     indicators_py: Optional[bytes] = None, store: Optional[LedgerStore] = None) -> Evaluation:
    """
    The one-shot sealed holdout for a sha whose DEV scorecard passed every hard gate
    (raises DevNotPassed otherwise; with `store`, the DEV-pass ledger record is required too).
    The caller has already claimed the holdout (ledger.claim_holdout).
    """
    from sajim_quant.lab.simulator import to_backtest_result

    if sframe.stage != splits.HOLDOUT:
        raise ValueError(f"evaluate_holdout needs a HOLDOUT frame, got {sframe.stage!r}")
    _require_dev_pass(sub, dev_scorecard, store)
    m = sub.manifest
    symbol, timeframe = m.get("symbol"), m.get("timeframe")
    spec = get_spec(symbol)
    _, params = _params(sub)
    rfile = result_file_name(sub.lab_id, symbol, timeframe, splits.HOLDOUT)
    if budget.trial is None and isinstance(dev_scorecard.get("trial"), int):
        budget = BudgetInfo(trial=dev_scorecard["trial"], n_trials_family=budget.n_trials_family,
                            n_trials_global=budget.n_trials_global, holdout_touches=budget.holdout_touches,
                            budget_exhausted=budget.budget_exhausted, holdout_burned=budget.holdout_burned)
    dev_gates = scorecard.gate_results_from_json(dev_scorecard["gates"])
    # section 6.5: a holdout scorecard copies dev_hard_gates_passed from DEV (also on the failure path)
    dev_passed = dev_scorecard.get("dev_hard_gates_passed") is True
    # The check job, then (when it passed, a zero signal count aside) the adaptive cuts as a second job.
    chk = _check_with_adaptive(sub, sframe, executor, indicators_py=indicators_py)
    cost = _cost_model(spec, symbol, fx_desc)

    # No signals on the holdout bars is a thin sample (G2 holdout_trades -> NOT_PROVEN), not an
    # integrity failure: every other check passed, so the (empty) full1 run is a valid base.
    only_no_signals = (chk.base_events is not None
                       and [k for k, ok in chk.checks.items() if not ok] == ["min_signals"])
    if (not chk.passed or chk.base_events is None) and not only_no_signals:
        # Integrity failed on the holdout bars: G0 fails now (REJECT), G8 cannot be computed.
        g0_metrics = robustness.integrity_metrics(chk, None, result=None, fx_desc=fx_desc, spec=spec,
                                                  n_params=_n_params(sub))
        fresh = {g.id: g for g in scorecard.evaluate_gates(g0_metrics)}
        gate_results = [fresh["G0"] if g.id == "G0" else g for g in dev_gates]
        detail = _check_detail(chk)
        return _finish(sub, splits.HOLDOUT, sframe, params, g0_metrics, detail, budget, perf={}, sim=None,
                       result=None, chk=chk, cost_model=cost,
                       holdout_split={"H_A": {"trades": 0, "net": 0.0, "pf": None},
                                      "H_B": {"trades": 0, "net": 0.0, "pf": None}},
                       result_file=rfile, deploy_id=deploy_id, gate_results=gate_results,
                       extra_reasons=["holdout integrity check failed"] + list(chk.reasons),
                       dev_hard_gates_passed=dev_passed)

    simulate = make_simulate(sframe, spec, quote_rate, max_hold_bars=int(m["max_hold_bars"]),
                             allow_short=bool(m.get("allow_short", False)), strategy_name=sub.lab_id,
                             timeframe=timeframe)
    base = simulate(chk.base_events, SimOverrides())
    stressed = simulate(chk.base_events, SimOverrides(cost_mult=gates.HOLDOUT_COST_STRESS_MULT))
    result = to_backtest_result(base, lab_id=sub.lab_id, symbol=symbol, timeframe=timeframe,
                                stage=splits.HOLDOUT, code_sha256=sub.code_sha256, params=params, fx_desc=fx_desc)
    first, last = _first_last(sframe)
    months = splits.months(splits.HOLDOUT, last_bar=last)
    perf = robustness.performance_metrics(result, months=months, first_bar=first, last_bar=last)
    hm = robustness.holdout_metrics(base, stressed, result, dev_headline=dev_scorecard.get("headline") or {},
                                    first_bar=first, last_bar=last)
    metrics, detail = _split_metrics(robustness.sample_metrics(base, splits.HOLDOUT, months=months),
                                     {k: v for k, v in hm.items() if k not in ("h_a", "h_b")})
    detail.update(_check_detail(chk))
    detail["holdout"] = {"sharpe": hm["holdout_sharpe"], "max_dd_frac": hm["holdout_max_dd_frac"],
                         "stressed_net": stressed.net, "stressed_trades": len(stressed.trades)}
    gate_results = scorecard.merge_holdout_gates(dev_gates, metrics)
    warnings: Tuple[str, ...] = ()
    if hm["h_b"]["trades"] > 0 and hm["h_b"]["net"] < 0:
        warnings = (scorecard.WARNING_HB_NEGATIVE,)
    return _finish(sub, splits.HOLDOUT, sframe, params, metrics, detail, budget, perf=perf, sim=base,
                   result=result, chk=chk, cost_model=cost,
                   holdout_split={"H_A": hm["h_a"], "H_B": hm["h_b"]}, result_file=rfile,
                   deploy_id=deploy_id, gate_results=gate_results, warnings=warnings,
                   dev_hard_gates_passed=dev_passed)


# ── Result file ─────────────────────────────────────────────────────────────
def result_payload(result: BacktestResult) -> Dict[str, Any]:
    """The canonical result JSON (same shape as run_cloud_strategy writes), JSON-safe."""
    metrics = calculate_metrics(result) if result.total_trades > 0 else None
    payload = {
        "metadata": result.metadata.to_dict(),
        "total_trades": result.total_trades,
        "metrics": ({k: v for k, v in metrics.__dict__.items() if k not in ("equity_series", "monthly_returns")}
                    if metrics is not None else {}),
        "trades": [t.to_dict() for t in result.trades],
    }
    return scorecard.json_safe(payload)
