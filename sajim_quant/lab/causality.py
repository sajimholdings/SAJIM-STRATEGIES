"""
SAJIM Strategy Lab — integrity checks (CONTRACT.md §7, INTERFACES.md §5).

Builds the frozen 8-run check plan (five truncation cuts, one future-scramble run,
two full runs) and interprets the decoded sandbox output into a CheckReport:
sandbox health, output schema, determinism, truncation causality, future-scramble
invariance and the minimum signal count. Reasons are short builder-facing strings;
they never carry performance numbers.

Adaptive cuts (INTERFACES.md §5.2, p1.2): the fixed cuts only see look-ahead that changes
an event within a few bars before a cut, so a sparse strategy with a short peek (for
example a positive-offset array slice, which the linter cannot always see) mostly slips
through. After the check job, build_adaptive_plan() places ADAPTIVE_CUTS more cuts 1..3
bars after bars where full1 itself emitted an event (seeded by code sha + stage), and
assess_adaptive() folds their comparison into the same CheckReport: a peek of up to
ADAPTIVE_MAX_OFFSET bars at a sampled event changes that event deterministically.
On the holdout, reasons never name a bar index (reveal_bars=False).

Threat model: these checks catch ACCIDENTAL look-ahead by a model (shift(-1),
centred windows, unshifted higher-timeframe resamples, whole-series statistics).
The real boundary is the sandbox. A deliberately adversarial strategy may defeat
in-sandbox checks (for example by detecting a truncated frame); that only costs the
honesty of its own scorecard and gives no access to the trusted side.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I
from sajim_quant.lab.sandbox_io import events_equal

logger = logging.getLogger(__name__)

SCRAMBLE_FRACTION = 0.5
CHECK_RUN_IDS: Tuple[str, ...] = ("cut20", "cut40", "cut60", "cut80", "cut95", "scramble", "full1", "full2")
BASE_RUN_ID = "full1"

#: Run-level error kinds that mean the strategy did not run to completion.
SANDBOX_KINDS = frozenset({"import", "params", "exception", "timeout", "crash", "budget"})
#: Run-level error kinds that mean it ran but its output broke the contract.
SCHEMA_KINDS = frozenset({"schema", "index", "dtype", "value", "too_many_events", "decode", "missing_output"})

MAX_REASONS = 24
MAX_REASON_CHARS = 400
_CUT_ID_RE = re.compile(r"cut(\d{1,3})")

#: Adaptive cuts per evaluation (DEV job 2 shares MAX_RUNS_PER_JOB with <= 2 * MAX_PARAMS neighbours).
ADAPTIVE_CUTS = 6
#: An adaptive cut is placed d bars after a sampled full1 event bar, d in 1..ADAPTIVE_MAX_OFFSET.
ADAPTIVE_MAX_OFFSET = 3
ADAPTIVE_RUN_PREFIX = "acut"
_ADAPTIVE_ID_RE = re.compile(r"acut\d{1,2}")
_DIGITS_RE = re.compile(r"\d+")


def cut_index(frac: float, n_bars: int) -> int:
    """Prefix length for a truncation cut: max(1, floor(frac * n_bars))."""
    return max(1, int(math.floor(frac * n_bars)))


def scramble_seed(code_sha256: str) -> int:
    """Frozen per-sha seed of the future-scramble run (63 bits)."""
    digest = hashlib.sha256(b"sajim.lab.scramble/1|" + code_sha256.encode()).digest()
    return int.from_bytes(digest[:8], "big") & (2**63 - 1)


def adaptive_seed(code_sha256: str, stage: str) -> int:
    """Frozen per-(sha, stage) seed of the adaptive-cut sampler (63 bits)."""
    digest = hashlib.sha256(f"sajim.lab.adaptive/1|{code_sha256}|{stage}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") & (2**63 - 1)


def is_adaptive_run(run_id: str) -> bool:
    return bool(_ADAPTIVE_ID_RE.fullmatch(run_id or ""))


def build_adaptive_plan(base_events: Optional[np.ndarray], n_bars: int, params: Mapping[str, I.Number],
                        code_sha256: str, stage: str, *, k: int = ADAPTIVE_CUTS,
                        min_bar: int = 0) -> Tuple[I.SandboxRun, ...]:
    """
    Up to k cut runs (acut1.., ascending cut) placed just after full1's own event bars.

    Frozen sampler, rng = default_rng(adaptive_seed(sha, stage)):
      * eligible events: bar + 1 < n_bars and bar >= min_bar (all bar + 1 < n_bars when
        none is >= min_bar); signal events and exit-only events are separate pools;
      * k_exit = min(#exit, k // 3); k_sig = min(#signal, k - k_exit); k_exit = min(#exit, k - k_sig);
      * draws, in this order: rng.choice(signal pool, k_sig, replace=False),
        rng.choice(exit pool, k_exit, replace=False), then
        offsets = rng.permutation(np.resize(arange(1, ADAPTIVE_MAX_OFFSET + 1), k_sig + k_exit));
      * cut = min(bar + offset, n_bars - 1); duplicate cuts keep the first.
    A peek of p bars at a sampled event bar e is seen by every cut with offset <= p, and the
    offsets repeat 1..ADAPTIVE_MAX_OFFSET, so with k >= ADAPTIVE_MAX_OFFSET at least one cut has
    offset 1. Returns () when full1 has no usable event.
    """
    if k <= 0 or base_events is None or len(base_events) == 0 or n_bars < 2:
        return ()
    bars = np.asarray(base_events["bar"], dtype=np.int64)
    is_sig = np.asarray(base_events["signal"]) != 0
    usable = bars + 1 < n_bars
    pool = usable & (bars >= int(min_bar))
    if not pool.any():
        pool = usable
    sig_pool, exit_pool = bars[pool & is_sig], bars[pool & ~is_sig]
    k_exit = min(len(exit_pool), k // 3)
    k_sig = min(len(sig_pool), k - k_exit)
    k_exit = min(len(exit_pool), k - k_sig)
    if k_sig + k_exit == 0:
        return ()
    rng = np.random.default_rng(adaptive_seed(code_sha256, stage))
    chosen: List[int] = []
    if k_sig:
        chosen += [int(b) for b in rng.choice(sig_pool, size=k_sig, replace=False)]
    if k_exit:
        chosen += [int(b) for b in rng.choice(exit_pool, size=k_exit, replace=False)]
    offsets = rng.permutation(np.resize(np.arange(1, ADAPTIVE_MAX_OFFSET + 1), len(chosen)))
    cuts: List[int] = []
    for bar, off in zip(chosen, offsets):
        cut = min(bar + int(off), n_bars - 1)
        if cut not in cuts:
            cuts.append(cut)
    p = dict(params)
    return tuple(I.SandboxRun(f"{ADAPTIVE_RUN_PREFIX}{j}", p, cut=c) for j, c in enumerate(sorted(cuts), start=1))


def build_check_plan(n_bars: int, params: Mapping[str, I.Number], code_sha256: str) -> Tuple[I.SandboxRun, ...]:
    """The 8 check runs in their frozen order: cut20..cut95, scramble, full1, full2."""
    if n_bars < 1:
        raise ValueError("n_bars must be >= 1")
    p = dict(params)
    runs: List[I.SandboxRun] = []
    for frac in gates.CAUSALITY_CUTS:
        runs.append(I.SandboxRun(f"cut{int(round(frac * 100))}", p, cut=cut_index(frac, n_bars)))
    runs.append(I.SandboxRun("scramble", p, scramble=I.ScrambleSpec(
        start=cut_index(SCRAMBLE_FRACTION, n_bars), seed=scramble_seed(code_sha256))))
    runs.append(I.SandboxRun("full1", p))
    runs.append(I.SandboxRun("full2", p))
    assert tuple(r.run_id for r in runs) == CHECK_RUN_IDS
    return tuple(runs)


def _first_line(msg: str) -> str:
    line = (msg or "").strip().splitlines()[0] if (msg or "").strip() else ""
    return line[:240]


def _cap(reason: str) -> str:
    return reason if len(reason) <= MAX_REASON_CHARS else reason[: MAX_REASON_CHARS - 3] + "..."


def _cut_label(run: I.SandboxRun, reveal_bars: bool = True) -> str:
    m = _CUT_ID_RE.fullmatch(run.run_id)
    if m:
        return f"{int(m.group(1))}% cut"
    return f"cut at bar {run.cut}" if reveal_bars else "a cut"


def _at_bar(bar: Optional[int], reveal_bars: bool) -> str:
    return f" (first differing bar {bar})" if reveal_bars and bar is not None else ""


def _untrusted(msg: str, reveal_bars: bool) -> str:
    """First line of an untrusted sandbox message; on the holdout every digit run is masked."""
    line = _first_line(msg)
    return line if reveal_bars else _DIGITS_RE.sub("#", line)


def _error_reasons(errors: Sequence[I.RunError], run_ids: Sequence[str], *, job_word: str,
                   reveal_bars: bool, include_unknown: bool = False) -> Tuple[List[str], bool, bool]:
    """
    (reasons, sandbox_ok, schema_ok) for the job-level errors and the errors of `run_ids`.
    Errors of other runs in the same job (plateau neighbours next to adaptive cuts) are
    ignored unless include_unknown. Run errors are grouped so one bug gives one reason.
    """
    reasons: List[str] = []
    sandbox_ok, schema_ok = True, True
    wanted = set(run_ids)
    grouped: Dict[Tuple[str, str, str], List[str]] = {}
    for e in errors:
        if e.run_id is None:
            sandbox_ok = False
            reasons.append(_cap(f"sandbox: {job_word} {e.kind}: {_untrusted(e.message, reveal_bars)}"))
            continue
        if e.run_id not in wanted and not include_unknown:
            continue
        if e.kind in SCHEMA_KINDS:
            schema_ok, family = False, "schema"
        else:                       # SANDBOX_KINDS, plus run-level internal/unknown kinds
            sandbox_ok, family = False, "sandbox"
        grouped.setdefault((family, e.kind, _untrusted(e.message, reveal_bars)), []).append(e.run_id)
    for (family, kind, msg), rids in grouped.items():
        ordered = [r for r in run_ids if r in rids] + [r for r in rids if r not in wanted]
        reasons.append(_cap(f"{family}: {', '.join(ordered)} {kind}: {msg}"))
    return reasons, sandbox_ok, schema_ok


def _finish_reasons(reasons: Sequence[str]) -> Tuple[str, ...]:
    out = [_cap(r) for r in reasons][:MAX_REASONS]
    if len(reasons) > MAX_REASONS:
        out[-1] = f"... and {len(reasons) - MAX_REASONS + 1} more"
    return tuple(out)


def assess_check(plan: Sequence[I.SandboxRun], decoded: I.DecodedJob, n_bars: int, *,
                 reveal_bars: bool = True) -> I.CheckReport:
    """
    Interpret one decoded check job. checks["lint"] and checks["manifest"] are set True
    here (evaluate.check overwrites them); every other CHECK_KEYS entry is False when it
    failed or could not be evaluated. reveal_bars=False (holdout) keeps bar indices out of
    every reason and masks digits in untrusted sandbox messages.
    """
    run_ids = [r.run_id for r in plan]

    # ── sandbox / schema from reported errors ──
    reasons, sandbox_ok, schema_ok = _error_reasons(decoded.errors, run_ids, job_word="job",
                                                    reveal_bars=reveal_bars, include_unknown=True)

    ok = {r.run_id: decoded.ok(r.run_id) for r in plan}
    ev = {rid: decoded.events[rid] for rid, good in ok.items() if good}

    full_runs = [r for r in plan if r.cut is None and r.scramble is None]
    base_id = BASE_RUN_ID if BASE_RUN_ID in ok else (full_runs[0].run_id if full_runs else None)
    base = ev.get(base_id) if base_id else None

    # ── determinism ──
    others = [r.run_id for r in full_runs if r.run_id != base_id]
    determinism = False
    if base is None or any(r not in ev for r in others):
        failed = [r for r in [base_id, *others] if r is not None and r not in ev]
        reasons.append(f"determinism: not evaluated (run(s) {', '.join(failed) or 'full'} produced no valid output)")
    elif len(others) < gates.DETERMINISM_RUNS - 1:
        reasons.append("determinism: not evaluated (too few full runs in the plan)")
    else:
        determinism = True
        for rid in others:
            same, bar = events_equal(base, ev[rid])
            if not same:
                determinism = False
                reasons.append(f"determinism: two identical runs differed{_at_bar(bar, reveal_bars)}")

    # ── truncation causality ──
    cut_runs = [r for r in plan if r.cut is not None]
    causality = base is not None and bool(cut_runs)
    for r in cut_runs:
        if base is None or r.run_id not in ev:
            causality = False
            continue
        c = int(r.cut)
        same, bar = events_equal(base[base["bar"] < c], ev[r.run_id][ev[r.run_id]["bar"] < c])
        if not same:
            causality = False
            reasons.append(f"causality: outputs before the {_cut_label(r, reveal_bars)} changed when later "
                           f"bars were removed{_at_bar(bar, reveal_bars)}")
    missing_cuts = [r.run_id for r in cut_runs if r.run_id not in ev]
    if base is None or missing_cuts:
        reasons.append(f"causality: not evaluated for {', '.join(missing_cuts) or 'any cut'} "
                       f"(no valid output to compare)")

    # ── future scramble ──
    scr_runs = [r for r in plan if r.scramble is not None]
    scramble = base is not None and bool(scr_runs)
    for r in scr_runs:
        if base is None or r.run_id not in ev:
            scramble = False
            continue
        s = int(r.scramble.start)
        same, bar = events_equal(base[base["bar"] < s], ev[r.run_id][ev[r.run_id]["bar"] < s])
        if not same:
            scramble = False
            where = f"bar {s}" if reveal_bars else "the scramble point"
            reasons.append(f"scramble: outputs before {where} changed when later bars were replaced by a "
                           f"shuffled random walk{_at_bar(bar, reveal_bars)}")
    if scr_runs and (base is None or any(r.run_id not in ev for r in scr_runs)):
        reasons.append("scramble: not evaluated (no valid output to compare)")

    # ── minimum signal count ──
    n_signals = int(np.count_nonzero(base["signal"] != 0)) if base is not None else 0
    min_signals = base is not None and n_signals >= gates.MIN_SIGNALS
    if base is not None and not min_signals:
        reasons.append(f"min_signals: {base_id} produced {n_signals} entry signals (need >= {gates.MIN_SIGNALS})")

    checks = {"lint": True, "manifest": True, "sandbox": sandbox_ok, "schema": schema_ok,
              "determinism": bool(determinism), "causality": bool(causality), "scramble": bool(scramble),
              "min_signals": bool(min_signals)}
    assert tuple(checks) == I.CHECK_KEYS
    passed = all(checks.values())
    logger.info("integrity check: passed=%s checks=%s", passed, checks)
    return I.CheckReport(passed=passed, reasons=_finish_reasons(reasons), checks=checks, n_signals=n_signals,
                         base_events=base if base is not None else None, timings=dict(decoded.timings))


def assess_adaptive(report: I.CheckReport, plan: Sequence[I.SandboxRun], decoded: I.DecodedJob, *,
                    reveal_bars: bool = True) -> I.CheckReport:
    """
    Fold the adaptive cuts of `plan` (decoded from the second sandbox job, which may also hold
    plateau neighbours; their errors are ignored here) into `report`. Each cut c is compared
    as events_equal(full1[bar < c], acut[bar < c]). A difference, a failed adaptive run or a
    job-level error makes the integrity check fail (causality / sandbox / schema), which is
    G0 -> REJECT. With an empty plan the report is returned unchanged.
    """
    if not plan:
        return report
    base = report.base_events
    run_ids = [r.run_id for r in plan]
    reasons = list(report.reasons)
    checks = dict(report.checks)
    err_reasons, sandbox_ok, schema_ok = _error_reasons(decoded.errors, run_ids, job_word="adaptive-cut job",
                                                        reveal_bars=reveal_bars)
    reasons += err_reasons
    checks["sandbox"] = bool(checks.get("sandbox")) and sandbox_ok
    checks["schema"] = bool(checks.get("schema")) and schema_ok

    causality = bool(checks.get("causality")) and base is not None
    missing: List[str] = []
    base_bars = base["bar"] if base is not None else np.zeros(0, dtype=np.int64)
    for r in plan:
        if base is None or r.cut is None or not decoded.ok(r.run_id):
            causality = False
            missing.append(r.run_id)
            continue
        c = int(r.cut)
        ev = decoded.events[r.run_id]
        prior = base[base_bars < c]
        same, bar = events_equal(prior, ev[ev["bar"] < c])
        if same:
            continue
        causality = False
        if reveal_bars:
            if len(prior):
                anchor = int(prior["bar"][-1])
                what = "a signal" if prior["signal"][-1] != 0 else "an exit"
                placed = f"{c - anchor} bar(s) after {what} on bar {anchor}"
            else:
                placed = f"at bar {c}"
            reasons.append(f"causality: outputs before an adaptive cut ({placed}) changed when later bars "
                           f"were removed{_at_bar(bar, True)}; the strategy reads bars after the bar it "
                           f"decides on")
        else:
            reasons.append("causality: outputs before an adaptive cut (placed just after one of the "
                           "strategy's own event bars) changed when later bars were removed; the strategy "
                           "reads bars after the bar it decides on")
    if missing:
        reasons.append(f"causality: adaptive cut(s) {', '.join(missing)} not evaluated (no valid output to compare)")
    checks["causality"] = causality
    assert tuple(checks) == I.CHECK_KEYS
    passed = all(checks.values())
    timings = dict(report.timings)
    timings.update({k: v for k, v in decoded.timings.items() if k in run_ids})
    logger.info("adaptive cuts (%d): passed=%s checks=%s", len(plan), passed, checks)
    return I.CheckReport(passed=passed, reasons=_finish_reasons(reasons), checks=checks,
                         n_signals=report.n_signals, base_events=base, timings=timings)
