"""
SAJIM Strategy Lab — gate evaluation, the verdict, and the sajim.lab.scorecard/1 JSON.

``evaluate_gates`` turns a flat metric dict into one GateResult per ``gates.GATES`` entry
(absent metric = rule not evaluated; present None = evaluated and failed). ``verdict``
applies the frozen Phase 1 precedence (INTERFACES.md 6.4):

  1. G0 / G1 failed                         -> REJECT (final)
  2. G2 DEV sample failed                   -> NOT_PROVEN (never REJECT on a thin sample)
  3. any other active hard DEV gate failed  -> REJECT (BUDGET_EXHAUSTED_VERDICT once the
                                               DEV budget is used up; final only then)
  4. DEV, every hard DEV gate passed        -> NOT_PROVEN until the sealed holdout runs
  5. HOLDOUT: too few trades -> NOT_PROVEN; G8 failed -> REJECT; else CANDIDATE

The critic can only downgrade (``apply_critic`` -> ``gates.apply_downgrade``). Every
human-readable string this module emits is checked against ``gates.FORBIDDEN_LABELS``.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np

from sajim_quant.lab import gates, splits
from sajim_quant.lab.interfaces import (
    LAB_PHASE as _LAB_PHASE, BudgetInfo, GateResult, RuleResult, StageFrame, Submission, VerdictOutcome,
)

logger = logging.getLogger(__name__)

LAB_PHASE = _LAB_PHASE
HOLDOUT_LABEL = "HOLDOUT"

#: Gates whose hard failure is a DEV performance failure (verdict step 3); G4b/G9/G10 count once active.
_DEV_PERF_GATES = ("G3", "G4a", "G4b", "G5", "G6", "G7", "G9", "G10")
_PHASE1_DEV_GATES = ("G0", "G1", "G2", "G3", "G4a", "G5", "G6", "G7")
_G2_DEV_METRICS = ("dev_trades", "dev_months_with_trades_frac")
_G2_HOLDOUT_METRIC = "holdout_trades"

REASON_DEV_PASSED = "DEV gates passed (IN-SAMPLE); sealed holdout not yet run"
REASON_HOLDOUT_BURNED = "holdout burned"
WARNING_HB_NEGATIVE = "H-B (after the model cutoff) net is negative"

SCORECARD_KEYS = (
    "schema", "id", "family", "code_sha256", "spec_sha256", "sha12", "gates_version", "protocol_version",
    "lab_phase", "stage", "label", "symbol", "timeframe", "window", "params", "trial", "max_trials",
    "n_trials_family", "n_trials_global", "holdout_touches", "max_holdout_touches", "holdout_burned",
    "cost_model", "headline", "sides", "holdout_split", "gates", "dev_hard_gates_passed", "verdict",
    "final", "reasons", "warnings", "context", "critic", "detail", "seal_note", "result_file",
    "deploy_id", "created_at",
)
WINDOW_KEYS = ("start", "stop", "first_bar", "last_bar", "n_bars", "warmup_bars")
COST_MODEL_KEYS = ("spread_model", "commission_model", "commission_rate", "slippage_pips", "round_trip_bps",
                   "slippage_points", "fx_desc", "breakeven_cost_mult", "breakeven_flag")
HEADLINE_KEYS = ("trades", "net", "return_pct", "pf", "expectancy", "win_rate", "max_dd_pct", "daily_sharpe")
SIDE_KEYS = ("trades", "net", "pf")
SPLIT_KEYS = ("H_A", "H_B")
GATE_KEYS = ("id", "name", "hard", "phase", "active", "passed", "value", "threshold", "note", "rules")
RULE_KEYS = ("metric", "op", "threshold", "value", "passed")
CONTEXT_KEYS = ("median_hold_days", "financing_flag", "rollover_entry_share", "rollover_flag",
                "window_end_closes", "untradable_stop", "n_signals", "halted")
CRITIC_KEYS = ("decision", "to", "reason", "notes")
CRITIC_DECISIONS = ("KEEP", "DOWNGRADE")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ── Gates ───────────────────────────────────────────────────────────────────
def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return float(v)
    return float(v)


def evaluate_gates(metrics: Mapping[str, Any], *, phase: int = LAB_PHASE) -> List[GateResult]:
    """One GateResult per gates.GATES entry, in order."""
    out: List[GateResult] = []
    for g in gates.GATES:
        active = g.phase <= phase
        rules = []
        for r in g.rules:
            if r.metric not in metrics:
                rules.append(RuleResult(r.metric, r.op, r.value, None, None))
            else:
                value = _num(metrics[r.metric])
                rules.append(RuleResult(r.metric, r.op, r.value, value, r.passes(value)))
        evaluated = [rr.passed for rr in rules if rr.passed is not None]
        passed = None if (not active or not evaluated) else all(evaluated)
        out.append(GateResult(id=g.id, name=g.name, hard=g.hard, phase=g.phase, active=active,
                              on_fail=g.on_fail, passed=passed, rules=tuple(rules), note=g.note))
    return out


def decisive_rule(g: GateResult) -> Optional[RuleResult]:
    """First failing evaluated rule, else the first evaluated rule, else None."""
    evaluated = [r for r in g.rules if r.passed is not None]
    for r in evaluated:
        if r.passed is False:
            return r
    return evaluated[0] if evaluated else None


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return f"{v:.4g}"


def _rule_text(rr: RuleResult) -> str:
    return f"{rr.metric} {_fmt(rr.value)} {rr.op} {rr.threshold:g}"


def _failed(g: GateResult) -> bool:
    return g.active and g.hard and g.passed is False


def _rule_failed(g: GateResult, metrics: Iterable[str]) -> List[RuleResult]:
    names = set(metrics)
    return [r for r in g.rules if r.metric in names and r.passed is False]


def _fail_reasons(g: GateResult, rules: Optional[Sequence[RuleResult]] = None) -> List[str]:
    bad = list(rules) if rules is not None else [r for r in g.rules if r.passed is False]
    return [f"{g.id} {g.name}: {_rule_text(r)} failed" for r in bad] or [f"{g.id} {g.name}: failed"]


def dev_hard_gates_passed(gate_results: Sequence[GateResult]) -> bool:
    """
    True only when every active hard DEV gate (G0, G1, G3..G7 and G4b/G9/G10 once
    active) PASSED, and G2's DEV rules passed. A gate that was not evaluated is not a pass.
    """
    by_id = {g.id: g for g in gate_results}
    if any(gid not in by_id for gid in _PHASE1_DEV_GATES):
        return False
    required = [by_id[gid] for gid in ("G0", "G1") + _DEV_PERF_GATES
                if gid in by_id and by_id[gid].active and by_id[gid].hard]
    if any(g.passed is not True for g in required):
        return False
    g2 = by_id["G2"]
    dev_rules = [r for r in g2.rules if r.metric in _G2_DEV_METRICS]
    return bool(dev_rules) and all(r.passed is True for r in dev_rules)


def verdict(gate_results: Sequence[GateResult], stage: str, budget_exhausted: bool, *,
            holdout_burned: bool = False) -> VerdictOutcome:
    """The frozen Phase 1 verdict precedence (module docstring)."""
    stage = splits.normalize_stage(stage)
    if stage not in splits.SCORED_STAGES:
        raise ValueError(f"verdict: stage must be one of {splits.SCORED_STAGES}, got {stage!r}")
    by_id = {g.id: g for g in gate_results}

    # 1. integrity / complexity
    hard01 = [g for g in (by_id.get("G0"), by_id.get("G1")) if g is not None and _failed(g)]
    if hard01:
        reasons = [r for g in hard01 for r in _fail_reasons(g)]
        return _outcome(gates.REJECT, True, reasons)

    # 2. DEV sample too small -> not proven, never rejected on performance
    g2 = by_id.get("G2")
    if g2 is not None and g2.active:
        bad = _rule_failed(g2, _G2_DEV_METRICS)
        if bad:
            reasons = _fail_reasons(g2, bad) + ["too few DEV trades to judge; performance gates not used"]
            if budget_exhausted:
                reasons.append(f"DEV trial budget used ({gates.MAX_DEV_TRIALS_PER_FAMILY}/"
                               f"{gates.MAX_DEV_TRIALS_PER_FAMILY})")
            return _outcome(gates.NOT_PROVEN, bool(budget_exhausted), reasons)

    # 3. a DEV performance / robustness gate failed
    perf_failed = [by_id[gid] for gid in _DEV_PERF_GATES if gid in by_id and _failed(by_id[gid])]
    if perf_failed:
        reasons = [r for g in perf_failed for r in _fail_reasons(g)]
        if budget_exhausted:
            reasons.append(f"DEV trial budget used ({gates.MAX_DEV_TRIALS_PER_FAMILY}/"
                           f"{gates.MAX_DEV_TRIALS_PER_FAMILY}) with a hard gate still failing")
            return _outcome(gates.BUDGET_EXHAUSTED_VERDICT, True, reasons)
        return _outcome(gates.REJECT, False, reasons)

    # A DEV gate that could not be evaluated is not a pass (defensive; should not happen).
    if not dev_hard_gates_passed(gate_results):
        missing = [g.id for g in gate_results if g.id in _PHASE1_DEV_GATES and g.passed is not True]
        reasons = [f"DEV gates not fully evaluated: {', '.join(missing) or 'G2'}"]
        return _outcome(gates.NOT_PROVEN, bool(budget_exhausted), reasons)

    # 4. DEV passed: provisional until the sealed holdout runs
    if stage == splits.DEV:
        if holdout_burned:
            return _outcome(gates.NOT_PROVEN, True, [REASON_HOLDOUT_BURNED])
        return _outcome(gates.NOT_PROVEN, False, [REASON_DEV_PASSED])

    # 5. sealed holdout
    if g2 is not None:
        bad = _rule_failed(g2, (_G2_HOLDOUT_METRIC,))
        if bad:
            return _outcome(gates.NOT_PROVEN, True, _fail_reasons(g2, bad)
                            + ["too few HOLDOUT trades to judge; the holdout cannot reject"])
    g8 = by_id.get("G8")
    if g8 is not None and _failed(g8):
        return _outcome(gates.REJECT, True, _fail_reasons(g8))
    if g8 is None or g8.passed is not True:
        return _outcome(gates.NOT_PROVEN, True, ["G8 Sealed holdout: not evaluated"])
    return _outcome(gates.CANDIDATE, True, [f"all hard gates passed: {gates.CANDIDATE_MEANING}"])


def _outcome(v: str, final: bool, reasons: Sequence[str]) -> VerdictOutcome:
    _check_texts(reasons, "reason")
    return VerdictOutcome(verdict=v, final=bool(final), reasons=tuple(reasons))


_dev_hard_gates_passed = dev_hard_gates_passed


# ── Holdout gate assembly ───────────────────────────────────────────────────
def gate_results_from_json(gates_json: Sequence[Mapping[str, Any]]) -> List[GateResult]:
    """Rebuild GateResults from a scorecard's "gates" list (JSON null value = None)."""
    out = []
    for g in gates_json:
        spec = gates.GATES_BY_ID.get(g["id"])
        rules = tuple(RuleResult(r["metric"], r["op"], float(r["threshold"]), _num(r.get("value")), r.get("passed"))
                      for r in g.get("rules", []))
        out.append(GateResult(id=g["id"], name=g["name"], hard=bool(g["hard"]), phase=int(g["phase"]),
                              active=bool(g["active"]), on_fail=spec.on_fail if spec else None,
                              passed=g.get("passed"), rules=rules, note=g.get("note", "")))
    return out


def merge_holdout_gates(dev_gates: Sequence[GateResult], metrics: Mapping[str, Any], *,
                        phase: int = LAB_PHASE) -> List[GateResult]:
    """
    Holdout gate set: DEV results copied unchanged, except G2 gains its holdout_trades
    rule and G8 is evaluated from `metrics` now.
    """
    fresh = {g.id: g for g in evaluate_gates(metrics, phase=phase)}
    out = []
    for g in dev_gates:
        if g.id == "G8":
            out.append(fresh["G8"])
        elif g.id == "G2":
            new_rules = []
            for r in g.rules:
                if r.metric == _G2_HOLDOUT_METRIC:
                    new_rules.append(next(x for x in fresh["G2"].rules if x.metric == _G2_HOLDOUT_METRIC))
                else:
                    new_rules.append(r)
            evaluated = [r.passed for r in new_rules if r.passed is not None]
            passed = None if (not g.active or not evaluated) else all(evaluated)
            out.append(GateResult(g.id, g.name, g.hard, g.phase, g.active, g.on_fail, passed,
                                  tuple(new_rules), g.note))
        else:
            out.append(g)
    return out


# ── Critic ──────────────────────────────────────────────────────────────────
def apply_critic(scorecard: Mapping[str, Any], *, decision: str, to: Optional[str] = None,
                 reason: str = "", notes: str = "") -> Dict[str, Any]:
    """
    A copy with the critic block filled. KEEP leaves the verdict; DOWNGRADE moves it to
    the worse of (current, to) via gates.apply_downgrade, so the critic can never upgrade.
    """
    if decision not in CRITIC_DECISIONS:
        raise ValueError(f"critic decision must be one of {CRITIC_DECISIONS}")
    if decision == "DOWNGRADE" and to not in gates.VERDICTS:
        raise ValueError("a DOWNGRADE needs `to` in gates.VERDICTS")
    _check_texts([reason, notes], "critic text")
    sc = copy.deepcopy(dict(scorecard))
    before = sc["verdict"]
    after = gates.apply_downgrade(before, to) if decision == "DOWNGRADE" else before
    sc["critic"] = {"decision": decision, "to": to if decision == "DOWNGRADE" else None,
                    "reason": reason, "notes": notes}
    if after != before:
        sc["verdict"] = after
        sc["final"] = True
        sc["reasons"] = list(sc.get("reasons", [])) + [f"critic downgrade: {before} -> {after}"]
    return sc


# ── Scorecard JSON ──────────────────────────────────────────────────────────
_FORBIDDEN_RE = re.compile("|".join(re.escape(x) for x in gates.FORBIDDEN_LABELS), re.IGNORECASE)


def scrub(text: str) -> str:
    """Untrusted text (e.g. a strategy's exception message) with forbidden labels masked."""
    return _FORBIDDEN_RE.sub("[label removed]", text or "")


def _check_texts(texts: Iterable[Any], what: str) -> None:
    for t in texts:
        if isinstance(t, str) and gates.contains_forbidden_label(t):
            raise ValueError(f"forbidden label in {what}: {t!r}")


def _human_texts(sc: Mapping[str, Any]) -> List[str]:
    """Every human-readable string the scorecard carries (ids and hashes excluded)."""
    out: List[Any] = [sc.get("label"), sc.get("verdict"), sc.get("seal_note")]
    out += list(sc.get("reasons") or []) + list(sc.get("warnings") or [])
    for g in sc.get("gates") or []:
        out += [g.get("name"), g.get("note"), g.get("threshold")]
    critic = sc.get("critic") or {}
    out += [critic.get("reason"), critic.get("notes")]
    out.append((sc.get("cost_model") or {}).get("fx_desc"))
    return [t for t in out if isinstance(t, str)]


def _assert_clean(sc: Mapping[str, Any]) -> None:
    for t in _human_texts(sc):
        if gates.contains_forbidden_label(t):
            raise ValueError(f"scorecard text contains a forbidden label: {t!r}")


def _gate_json(g: GateResult) -> Dict[str, Any]:
    d = decisive_rule(g)
    spec = gates.GATES_BY_ID.get(g.id)
    threshold = None
    if d is not None and spec is not None:
        threshold = next((r.describe() for r in spec.rules
                          if (r.metric, r.op, r.value) == (d.metric, d.op, d.threshold)), None)
    return {
        "id": g.id, "name": g.name, "hard": g.hard, "phase": g.phase, "active": g.active, "passed": g.passed,
        "value": None if d is None else d.value, "threshold": threshold, "note": g.note,
        "rules": [{"metric": r.metric, "op": r.op, "threshold": r.threshold, "value": r.value, "passed": r.passed}
                  for r in g.rules],
    }


def _iso(t: Any) -> Optional[str]:
    return None if t is None else splits.to_utc_naive(t).isoformat()


def build_scorecard(*, submission: Submission, stage: str, sframe: StageFrame, params: Mapping,
                    gate_results: Sequence[GateResult], outcome: VerdictOutcome, budget: BudgetInfo,
                    perf: Mapping, holdout_split: Optional[Mapping], cost_model: Mapping,
                    context: Mapping, detail: Mapping, result_file: str, deploy_id: str = "",
                    warnings: Sequence[str] = (), now: Optional[datetime] = None,
                    dev_hard_gates_passed: Optional[bool] = None) -> Dict[str, Any]:
    """
    The full (server) sajim.lab.scorecard/1 dict, keys in the frozen order. On a holdout the
    caller passes dev_hard_gates_passed copied from the DEV scorecard (section 6.5); when it is
    None the value is computed from gate_results.
    """
    stage = splits.normalize_stage(stage)
    if stage not in splits.SCORED_STAGES:
        raise ValueError(f"build_scorecard: stage must be dev or holdout, got {stage!r}")
    w_start, w_stop = splits.window(stage)
    idx = sframe.frame.index
    has_bars = sframe.stop_bar > sframe.start_bar
    m = submission.manifest
    perf = perf or {}
    median_hold = perf.get("median_hold_days")
    rollover_share = perf.get("rollover_entry_share")
    sc: Dict[str, Any] = {
        "schema": gates.SCORECARD_SCHEMA,
        "id": submission.lab_id,
        "family": submission.family,
        "code_sha256": submission.code_sha256,
        "spec_sha256": submission.spec_sha256,
        "sha12": submission.sha12,
        "gates_version": gates.GATES_VERSION,
        "protocol_version": splits.PROTOCOL_VERSION,
        "lab_phase": LAB_PHASE,
        "stage": stage,
        "label": splits.IN_SAMPLE_LABEL if stage == splits.DEV else HOLDOUT_LABEL,
        "symbol": m.get("symbol"),
        "timeframe": m.get("timeframe"),
        "window": {
            "start": w_start.isoformat(), "stop": None if w_stop is None else w_stop.isoformat(),
            "first_bar": _iso(idx[sframe.start_bar]) if has_bars else None,
            "last_bar": _iso(idx[sframe.stop_bar - 1]) if has_bars else None,
            "n_bars": int(sframe.stop_bar - sframe.start_bar), "warmup_bars": int(sframe.warmup_bars),
        },
        "params": dict(params),
        "trial": budget.trial,
        "max_trials": gates.MAX_DEV_TRIALS_PER_FAMILY,
        "n_trials_family": budget.n_trials_family,
        "n_trials_global": budget.n_trials_global,
        "holdout_touches": budget.holdout_touches,
        "max_holdout_touches": gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY,
        "holdout_burned": bool(budget.holdout_burned),
        "cost_model": {k: cost_model.get(k) for k in COST_MODEL_KEYS},
        "headline": {k: perf.get(k) for k in HEADLINE_KEYS},
        "sides": {
            "long": {"trades": perf.get("long_trades"), "net": perf.get("long_net"), "pf": perf.get("long_pf")},
            "short": {"trades": perf.get("short_trades"), "net": perf.get("short_net"), "pf": perf.get("short_pf")},
        },
        "holdout_split": None if holdout_split is None else {k: dict(holdout_split[k]) for k in SPLIT_KEYS},
        "gates": [_gate_json(g) for g in gate_results],
        "dev_hard_gates_passed": (_dev_hard_gates_passed(gate_results) if dev_hard_gates_passed is None
                                  else bool(dev_hard_gates_passed)),
        "verdict": outcome.verdict,
        "final": bool(outcome.final),
        "reasons": list(outcome.reasons),
        "warnings": list(warnings),
        "context": {
            "median_hold_days": median_hold,
            "financing_flag": None if median_hold is None
            else bool(median_hold > gates.FINANCING_FLAG_MEDIAN_HOLD_DAYS),
            "rollover_entry_share": rollover_share,
            "rollover_flag": None if rollover_share is None else bool(rollover_share > 0),
            "window_end_closes": context.get("window_end_closes"),
            "untradable_stop": context.get("untradable_stop"),
            "n_signals": context.get("n_signals"),
            "halted": context.get("halted"),
        },
        "critic": {"decision": None, "to": None, "reason": "", "notes": ""},
        "detail": copy.deepcopy(dict(detail)),
        "seal_note": splits.SEAL_NOTE,
        "result_file": result_file,
        "deploy_id": deploy_id,
        "created_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
    }
    assert tuple(sc) == SCORECARD_KEYS
    _assert_clean(sc)
    return sc


def builder_view(scorecard: Mapping[str, Any]) -> Dict[str, Any]:
    """What the builder may see: DEV scorecards only, without the server-only detail block."""
    if scorecard.get("stage") != splits.DEV:
        raise ValueError("builder_view: only DEV scorecards reach the builder")
    return {k: copy.deepcopy(v) for k, v in scorecard.items() if k != "detail"}


def json_safe(v: Any) -> Any:
    """Recursively JSON-safe: numpy scalars -> Python, NaN/inf -> None, datetimes -> ISO."""
    if isinstance(v, Mapping):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def to_json_bytes(scorecard: Mapping[str, Any]) -> bytes:
    """Strict JSON (NaN/inf -> null), indent=2, UTF-8. Refuses forbidden labels."""
    _assert_clean(scorecard)
    return json.dumps(json_safe(scorecard), indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")


# ── Validation ──────────────────────────────────────────────────────────────
def _keys_problem(where: str, d: Any, keys: Sequence[str]) -> List[str]:
    if not isinstance(d, Mapping):
        return [f"{where}: must be an object"]
    if tuple(d) != tuple(keys):
        return [f"{where}: keys {list(d)} != {list(keys)}"]
    return []


def _is_num_or_none(v: Any) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def validate_scorecard(d: Mapping[str, Any]) -> List[str]:
    """[] when `d` matches sajim.lab.scorecard/1 (the builder view, without "detail", is accepted)."""
    if not isinstance(d, Mapping):
        return ["scorecard: must be an object"]
    probs: List[str] = []
    keys = tuple(d)
    builder_keys = tuple(k for k in SCORECARD_KEYS if k != "detail")
    if keys not in (SCORECARD_KEYS, builder_keys):
        return [f"scorecard: keys {list(keys)} != {list(SCORECARD_KEYS)}"]
    if d["schema"] != gates.SCORECARD_SCHEMA:
        probs.append("schema: wrong value")
    for k in ("code_sha256", "spec_sha256"):
        if not (isinstance(d[k], str) and _HEX64.fullmatch(d[k])):
            probs.append(f"{k}: must be 64 lowercase hex")
    if not (isinstance(d["sha12"], str) and isinstance(d["code_sha256"], str) and d["sha12"] == d["code_sha256"][:12]):
        probs.append("sha12: must be code_sha256[:12]")
    if not (isinstance(d["id"], str) and re.fullmatch(gates.LAB_ID_PATTERN, d["id"])):
        probs.append("id: must match LAB_ID_PATTERN")
    if not (isinstance(d["family"], str) and re.fullmatch(gates.LAB_ID_PATTERN, d["family"])):
        probs.append("family: must match LAB_ID_PATTERN")
    if d["gates_version"] != gates.GATES_VERSION:
        probs.append("gates_version: unexpected")
    if d["protocol_version"] != splits.PROTOCOL_VERSION:
        probs.append("protocol_version: unexpected")
    stage = d["stage"]
    if stage not in splits.SCORED_STAGES:
        probs.append("stage: must be dev or holdout")
    else:
        want = splits.IN_SAMPLE_LABEL if stage == splits.DEV else HOLDOUT_LABEL
        if d["label"] != want:
            probs.append(f"label: must be {want!r} for stage {stage}")
        if stage == splits.DEV and d["holdout_split"] is not None:
            probs.append("holdout_split: must be null on a DEV scorecard")
        if stage == splits.HOLDOUT:
            probs += _keys_problem("holdout_split", d["holdout_split"], SPLIT_KEYS)
            if isinstance(d["holdout_split"], Mapping):
                for part in SPLIT_KEYS:
                    probs += _keys_problem(f"holdout_split.{part}", d["holdout_split"].get(part), SIDE_KEYS)
    probs += _keys_problem("window", d["window"], WINDOW_KEYS)
    probs += _keys_problem("cost_model", d["cost_model"], COST_MODEL_KEYS)
    probs += _keys_problem("headline", d["headline"], HEADLINE_KEYS)
    if isinstance(d["headline"], Mapping):
        for k, v in d["headline"].items():
            if not _is_num_or_none(v):
                probs.append(f"headline.{k}: must be a number or null")
    probs += _keys_problem("sides", d["sides"], ("long", "short"))
    if isinstance(d["sides"], Mapping):
        for s in ("long", "short"):
            probs += _keys_problem(f"sides.{s}", d["sides"].get(s), SIDE_KEYS)
    probs += _keys_problem("context", d["context"], CONTEXT_KEYS)
    probs += _keys_problem("critic", d["critic"], CRITIC_KEYS)
    if isinstance(d["critic"], Mapping) and d["critic"].get("decision") not in (None,) + CRITIC_DECISIONS:
        probs.append("critic.decision: must be null, KEEP or DOWNGRADE")
    if not isinstance(d["gates"], list):
        probs.append("gates: must be a list")
    else:
        ids = [g.get("id") if isinstance(g, Mapping) else None for g in d["gates"]]
        if ids != [g.id for g in gates.GATES]:
            probs.append("gates: must list every gates.GATES entry in order")
        for g in d["gates"]:
            probs += _keys_problem(f"gates[{g.get('id') if isinstance(g, Mapping) else '?'}]", g, GATE_KEYS)
            if isinstance(g, Mapping) and isinstance(g.get("rules"), list):
                for r in g["rules"]:
                    probs += _keys_problem(f"gates[{g.get('id')}].rules", r, RULE_KEYS)
                    if isinstance(r, Mapping) and r.get("passed") not in (None, True, False):
                        probs.append(f"gates[{g.get('id')}].rules: passed must be bool or null")
            if isinstance(g, Mapping) and g.get("passed") not in (None, True, False):
                probs.append(f"gates[{g.get('id')}]: passed must be bool or null")
    if d["verdict"] not in gates.VERDICTS:
        probs.append("verdict: must be one of gates.VERDICTS")
    for k in ("final", "holdout_burned", "dev_hard_gates_passed"):
        if not isinstance(d[k], bool):
            probs.append(f"{k}: must be a bool")
    for k in ("reasons", "warnings"):
        if not (isinstance(d[k], list) and all(isinstance(x, str) for x in d[k])):
            probs.append(f"{k}: must be a list of strings")
    if "detail" in d and not isinstance(d["detail"], Mapping):
        probs.append("detail: must be an object")
    if d["seal_note"] != splits.SEAL_NOTE:
        probs.append("seal_note: must be splits.SEAL_NOTE")
    for k in ("max_trials", "max_holdout_touches", "n_trials_family", "n_trials_global", "holdout_touches",
              "lab_phase"):
        if not (isinstance(d[k], int) and not isinstance(d[k], bool)):
            probs.append(f"{k}: must be an int")
    if d["trial"] is not None and not (isinstance(d["trial"], int) and not isinstance(d["trial"], bool)):
        probs.append("trial: must be an int or null")
    try:
        _assert_clean(d)
    except ValueError as exc:
        probs.append(str(exc))
    return probs
