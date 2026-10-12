"""
SAJIM Strategy Lab — frozen gate thresholds, trial budgets and system defaults.

Approved by the user on 2026-10-12 (docs/STRATEGY_LAB_PLAN.md, "Decisions" and
"evaluation_gates"). GATES_VERSION is stamped into every scorecard. Changing
any number here after seeing results is p-hacking by hand: bump GATES_VERSION,
and old verdicts are relabelled with the version they were judged under.

Conventions
  * Fractions, not percents: 0.20 means 20%.
  * Every threshold is a Rule(metric, op, value) so the comparison direction
    (strict or not) is frozen too; scorecards print `value op threshold`.
  * Money thresholds are relative to INITIAL_BALANCE or to the run's own DEV
    numbers; nothing here depends on the symbol.
  * `phase` is the plan phase in which a gate becomes active (hard) — G4b, G9
    and G10 are computed from Phase 2 onward.

Pure Python, no imports beyond the standard library.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Dict, Mapping, Optional, Tuple

GATES_VERSION = "v1"

# ── Verdicts (worst -> best) ────────────────────────────────────────────────
REJECT = "REJECT"
NOT_PROVEN = "NOT_PROVEN"
CANDIDATE = "CANDIDATE"          # "eligible for paper/forward test" — never "an edge"
VERDICTS: Tuple[str, ...] = (REJECT, NOT_PROVEN, CANDIDATE)
CANDIDATE_MEANING = "eligible for paper/forward test"

# Never emitted for ai_lab strategies, in any casing.
FORBIDDEN_LABELS: Tuple[str, ...] = ("ELITE", "OPTIMAL", "Tier 1", "edge found")

# Verdict precedence (user decision 2026-10-12): a family whose DEV trial budget is used
# up while a hard gate still fails, with an adequate sample (G2 passed), is REJECTed.
# NOT_PROVEN is reserved for too few trades, or no sealed holdout to judge on.
BUDGET_EXHAUSTED_VERDICT = REJECT

# ── Trial budgets (server-enforced; ledger.jsonl on the Volume is the truth) ─
MAX_DEV_TRIALS_PER_FAMILY = 3            # the 4th DEV trial is refused
MAX_HOLDOUT_TOUCHES_PER_FAMILY = 3       # after that the holdout is burned
MAX_HOLDOUT_RUNS_PER_SHA = 1             # once per frozen code sha
INTEGRITY_CHECKS_ARE_TRIALS = False      # /lab/check is free and returns no performance

HTTP_DEV_BUDGET_EXHAUSTED = 429
HTTP_HOLDOUT_ALREADY_RUN = 409
HTTP_WRONG_TOKEN_SCOPE = 403

# ── System defaults (NOT strategy params; the model never picks these) ──────
RISK_PCT = 0.01                  # fixed-fractional risk per trade
MAX_RISK_PCT = 0.02              # hard clamp if RISK_PCT is ever changed
INITIAL_BALANCE = 10_000.0       # USD, equity at each scored window's start
ONE_POSITION = True              # one open position at a time, per run
RUIN_FLOOR = 0.10                # run halts (STOP_OUT) once equity <= RUIN_FLOOR * INITIAL_BALANCE
COMMISSION_MODEL = "instruments_v1"
ENGINE = "python:signal"
RUN_ADAPTER = "signal"
ORIGIN = "ai_lab"

# ── Contract limits (lint + manifest checks; see CONTRACT.md) ───────────────
MIN_PARAMS = 1
MAX_PARAMS = 5
MAX_PARAM_RANGE_RATIO = 10       # max/min of a PARAMS range whose min > 0
# Zero-based PARAMS (user decision 2026-10-12): min == 0 is allowed. An int param then
# needs max <= MAX_ZERO_BASED_INT_MAX (UTC hours, small bar counts); a float param needs
# default > 0 and max <= MAX_PARAM_RANGE_RATIO * default.
MAX_ZERO_BASED_INT_MAX = 23
MAX_SOURCE_BYTES = 64 * 1024
MAX_HOLD_BARS = 2000
MAX_WARMUP_BARS = 2000
MAX_BREADTH_SYMBOLS = 2
MAX_SIGNAL_RUNTIME_S = 60        # generate_signals on DEV M5
LAB_SIGNALS_TIMEOUT_S = 300      # hard timeout of the untrusted Modal function
LAB_ID_PATTERN = r"^lab_[a-z0-9_]{3,40}$"
MANIFEST_SCHEMA = "sajim.lab.manifest/1"
SCORECARD_SCHEMA = "sajim.lab.scorecard/1"

# ── G0 Integrity ────────────────────────────────────────────────────────────
MAX_UNTRADABLE_STOP_FRAC = 0.30          # signals skipped for a 0-lot stop
CAUSALITY_CUTS: Tuple[float, ...] = (0.20, 0.40, 0.60, 0.80, 0.95)
CAUSALITY_ATOL = 1e-9
DETERMINISM_RUNS = 2
MIN_SIGNALS = 1
UNCONVERTED_FX_PREFIX = "UNCONVERTED"    # fx description must not start with this

# ── G2 Sample (failure -> NOT_PROVEN, not REJECT) ───────────────────────────
MIN_DEV_TRADES = 100
MIN_DEV_MONTHS_WITH_TRADES_FRAC = 0.70   # of splits.months(DEV)
MIN_HOLDOUT_TRADES = 30

# ── G3 DEV performance after base costs ─────────────────────────────────────
MIN_DEV_PF = 1.20
MIN_DEV_EXPECTANCY = 0.0                 # strictly greater
MAX_DEV_DD = 0.20                        # incl. intra-trade MAE
MAX_TOP5_PROFIT_SHARE = 0.30             # strictly less
MAX_SINGLE_MONTH_PROFIT_SHARE = 0.25
MIN_SIDE_PF = 0.9                        # each side actually traded

# ── G4 Null / overfitting statistics ────────────────────────────────────────
NULL_RUNS = 300
NULL_PERCENTILE = 95.0                   # strategy net must exceed it (strictly)
MIN_PSR = 0.95                           # G4b, Phase 2
MIN_DSR = 0.95                           # G4b, Phase 2

# ── G5 Fold consistency (fixed params; not walk-forward optimisation) ───────
N_FOLDS = 4
MIN_POSITIVE_FOLDS = 3
MIN_FOLD_PF = 0.8

# ── G6 Cost and latency stress ──────────────────────────────────────────────
COST_STRESS_NET_MULT = 1.5               # net > 0 at 1.5x costs
COST_STRESS_PF_MULT = 2.0                # PF >= MIN_PF_AT_2X_COST at 2x costs
MIN_PF_AT_2X_COST = 1.0
DELAY_BARS = 1
MIN_PF_DELAYED = 1.0
BREAKEVEN_MULT_RANGE: Tuple[float, float] = (0.5, 4.0)   # bisection bounds, reported only

# ── G7 Parameter plateau (aggregates only reach the builder) ────────────────
PLATEAU_FACTORS: Tuple[float, ...] = (0.8, 1.2)
PLATEAU_MIN_INT_STEP = 1
MIN_PLATEAU_POSITIVE_FRAC = 0.70
MIN_PLATEAU_MEDIAN_PF = 1.10

# ── G8 Sealed holdout ───────────────────────────────────────────────────────
MIN_HOLDOUT_PF = 1.10
HOLDOUT_COST_STRESS_MULT = 1.5
MIN_HOLDOUT_PF_STRESSED = 1.0
MIN_HOLDOUT_SHARPE_RATIO = 0.5           # holdout daily Sharpe vs DEV daily Sharpe
MAX_HOLDOUT_DD_RATIO = 1.5               # holdout max DD vs DEV max DD
# Compared as margins, never as ratios (a DEV max DD of 0 would divide by zero):
#   holdout_sharpe_margin = holdout_sharpe - MIN_HOLDOUT_SHARPE_RATIO * dev_sharpe  (>= 0)
#   holdout_dd_margin     = holdout_dd     - MAX_HOLDOUT_DD_RATIO     * dev_dd      (<= 0)

# ── G9 Monte Carlo (block bootstrap, Phase 2) ───────────────────────────────
MC_BLOCK_TRADES = 10
MC_PATHS = 2000
MC_DD_LEVEL = 0.20
MAX_MC_P_DD = 0.15                       # P(max DD >= MC_DD_LEVEL)
MAX_MC_P95_WORST_DD = 0.30

# ── G11 Context flags (report only) ─────────────────────────────────────────
REGIME_PROFIT_SHARE_FLAG = 1.0           # one regime carries > 100% of profit
FINANCING_FLAG_MEDIAN_HOLD_DAYS = 1.0
ROLLOVER_FLAG_UTC_HOURS: Tuple[int, int] = (21, 24)     # half-open: entries 21:00 <= t < 24:00 UTC


# ── Rules and gate specs ────────────────────────────────────────────────────
_OPS: Dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq,
}


@dataclass(frozen=True)
class Rule:
    """One pass condition: `metric op value` must hold."""

    metric: str
    op: str
    value: float

    def __post_init__(self):
        if self.op not in _OPS:
            raise ValueError(f"unknown op {self.op!r}")

    def passes(self, observed: Optional[float]) -> bool:
        """None/NaN never pass."""
        if observed is None or observed != observed:
            return False
        return _OPS[self.op](observed, self.value)

    def describe(self) -> str:
        return f"{self.metric} {self.op} {self.value:g}"


@dataclass(frozen=True)
class GateSpec:
    """
    id/name as printed on the scorecard. `hard` gates decide the verdict;
    `on_fail` is the verdict a hard failure produces (None = report only).
    `phase` is the plan phase from which the gate is enforced.
    """

    id: str
    name: str
    hard: bool
    phase: int
    on_fail: Optional[str]
    rules: Tuple[Rule, ...] = ()
    note: str = ""
    params: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


def _p(**kw) -> Mapping[str, object]:
    return MappingProxyType(dict(kw))


GATES: Tuple[GateSpec, ...] = (
    GateSpec(
        "G0", "Integrity", True, 1, REJECT,
        rules=(
            Rule("integrity_checks_passed", "==", 1),
            Rule("cost_model_checks_passed", "==", 1),
            Rule("slippage_points", ">", 0.0),
            Rule("untradable_stop_frac", "<=", MAX_UNTRADABLE_STOP_FRAC),
            Rule("n_signals", ">=", MIN_SIGNALS),
        ),
        note=("Server checks for the exact sha: lint, schema/dtype/index, determinism, "
              "truncation causality, future-scramble. cost_model_checks_passed = "
              "commission_model != 'zero' AND fx description not UNCONVERTED* AND "
              "engine == python:signal. slippage_points is the run's cost_assumptions value."),
        params=_p(causality_cuts=CAUSALITY_CUTS, causality_atol=CAUSALITY_ATOL,
                  determinism_runs=DETERMINISM_RUNS, engine=ENGINE,
                  unconverted_fx_prefix=UNCONVERTED_FX_PREFIX),
    ),
    GateSpec(
        "G1", "Complexity", True, 1, REJECT,
        rules=(Rule("n_params", "<=", MAX_PARAMS), Rule("n_params", ">=", MIN_PARAMS)),
        note="The trial is logged in the ledger before the run starts.",
        params=_p(max_param_range_ratio=MAX_PARAM_RANGE_RATIO),
    ),
    GateSpec(
        "G2", "Sample", True, 1, NOT_PROVEN,
        rules=(
            Rule("dev_trades", ">=", MIN_DEV_TRADES),
            Rule("dev_months_with_trades_frac", ">=", MIN_DEV_MONTHS_WITH_TRADES_FRAC),
            Rule("holdout_trades", ">=", MIN_HOLDOUT_TRADES),
        ),
        note="holdout_trades is only checked once the holdout has run.",
    ),
    GateSpec(
        "G3", "DEV after base costs", True, 1, REJECT,
        rules=(
            Rule("pf", ">=", MIN_DEV_PF),
            Rule("expectancy", ">", MIN_DEV_EXPECTANCY),
            Rule("max_dd_frac", "<=", MAX_DEV_DD),
            Rule("top5_profit_share", "<", MAX_TOP5_PROFIT_SHARE),
            Rule("max_month_profit_share", "<=", MAX_SINGLE_MONTH_PROFIT_SHARE),
            Rule("min_side_pf", ">=", MIN_SIDE_PF),
        ),
        note="Max DD includes intra-trade MAE. min_side_pf is over sides that traded.",
    ),
    GateSpec(
        "G4a", "Random-entry null", True, 1, REJECT,
        rules=(Rule("net_minus_null_pctl", ">", 0.0),),
        note=("Seeded runs with the same trade count, long/short mix, stop/tp/hold "
              "distribution, simulator and costs; percentile rank reported."),
        params=_p(runs=NULL_RUNS, percentile=NULL_PERCENTILE),
    ),
    GateSpec(
        "G4b", "PSR / DSR", True, 2, REJECT,
        rules=(Rule("psr", ">=", MIN_PSR), Rule("dsr", ">=", MIN_DSR)),
        note="Zero-filled business-day daily series; n_trials = all ledger trials in the family.",
    ),
    GateSpec(
        "G5", "Fold consistency", True, 1, REJECT,
        rules=(Rule("positive_folds", ">=", MIN_POSITIVE_FOLDS), Rule("min_fold_pf", ">=", MIN_FOLD_PF)),
        note="Contiguous equal DEV folds, fixed params. Not walk-forward optimisation.",
        params=_p(n_folds=N_FOLDS),
    ),
    GateSpec(
        "G6", "Cost and latency stress", True, 1, REJECT,
        rules=(
            Rule("net_at_1_5x_cost", ">", 0.0),
            Rule("pf_at_2x_cost", ">=", MIN_PF_AT_2X_COST),
            Rule("pf_delay_1", ">=", MIN_PF_DELAYED),
        ),
        note="Break-even cost multiplier (bisection) is reported, not gated.",
        params=_p(net_mult=COST_STRESS_NET_MULT, pf_mult=COST_STRESS_PF_MULT,
                  delay_bars=DELAY_BARS, breakeven_range=BREAKEVEN_MULT_RANGE),
    ),
    GateSpec(
        "G7", "Parameter plateau", True, 1, REJECT,
        rules=(
            Rule("plateau_positive_frac", ">=", MIN_PLATEAU_POSITIVE_FRAC),
            Rule("plateau_median_pf", ">=", MIN_PLATEAU_MEDIAN_PF),
        ),
        note="One param at a time, clipped to range, ints moved by >= 1. Aggregates only.",
        params=_p(factors=PLATEAU_FACTORS, min_int_step=PLATEAU_MIN_INT_STEP),
    ),
    GateSpec(
        "G8", "Sealed holdout", True, 1, REJECT,
        rules=(
            Rule("holdout_net", ">", 0.0),
            Rule("holdout_pf", ">=", MIN_HOLDOUT_PF),
            Rule("holdout_pf_at_1_5x_cost", ">=", MIN_HOLDOUT_PF_STRESSED),
            Rule("holdout_sharpe_margin", ">=", 0.0),
            Rule("holdout_dd_margin", "<=", 0.0),
        ),
        note=("Judge token, once per sha, only after every hard DEV gate passed. REJECT only "
              "with >= MIN_HOLDOUT_TRADES holdout trades (else G2 -> NOT_PROVEN). Negative "
              "H-B is a warning, and caps the verdict at NOT_PROVEN from Phase 2."),
        params=_p(cost_mult=HOLDOUT_COST_STRESS_MULT, min_trades_to_reject=MIN_HOLDOUT_TRADES),
    ),
    GateSpec(
        "G9", "Monte Carlo (block bootstrap)", True, 2, REJECT,
        rules=(
            Rule("mc_p5_terminal_over_initial", ">", 1.0),
            Rule("mc_p_dd_ge_level", "<=", MAX_MC_P_DD),
            Rule("mc_p95_worst_dd", "<=", MAX_MC_P95_WORST_DD),
        ),
        note="fragility_index is ignored until its annualisation mismatch is fixed.",
        params=_p(block_trades=MC_BLOCK_TRADES, paths=MC_PATHS, dd_level=MC_DD_LEVEL),
    ),
    GateSpec(
        "G10", "Breadth", True, 2, REJECT,
        rules=(Rule("profitable_nodes_beyond_primary", ">=", 1),),
        note=("Soft score (universality_pct), hard floor. Primary-only profit -> REJECT, "
              "or NOT_PROVEN if spec.md pre-registered it as instrument-specific with a mechanism."),
        params=_p(max_breadth_symbols=MAX_BREADTH_SYMBOLS),
    ),
    GateSpec(
        "G11", "Context", False, 1, None,
        note="Noise regime/session breakdown, financing and rollover flags, cost-model echo.",
        params=_p(regime_profit_share_flag=REGIME_PROFIT_SHARE_FLAG,
                  financing_flag_median_hold_days=FINANCING_FLAG_MEDIAN_HOLD_DAYS,
                  rollover_flag_utc_hours=ROLLOVER_FLAG_UTC_HOURS),
    ),
)

GATES_BY_ID: Mapping[str, GateSpec] = MappingProxyType({g.id: g for g in GATES})


def active_gates(phase: int) -> Tuple[GateSpec, ...]:
    """Gates enforced in a given plan phase (G4b/G9/G10 start in Phase 2)."""
    return tuple(g for g in GATES if g.phase <= phase)


def worse(a: str, b: str) -> str:
    """The lower of two verdicts (REJECT < NOT_PROVEN < CANDIDATE)."""
    return a if VERDICTS.index(a) <= VERDICTS.index(b) else b


def apply_downgrade(current: str, proposed: str) -> str:
    """The critic can only downgrade: an 'upgrade' leaves the verdict unchanged."""
    return worse(current, proposed)


def contains_forbidden_label(text: str) -> bool:
    """True if `text` contains any FORBIDDEN_LABELS entry, case-insensitively."""
    t = (text or "").casefold()
    return any(label.casefold() in t for label in FORBIDDEN_LABELS)


def describe() -> dict:
    """The gates block stamped into every scorecard (JSON-safe)."""
    return {
        "gates_version": GATES_VERSION,
        "gates": [
            {"id": g.id, "name": g.name, "hard": g.hard, "phase": g.phase, "on_fail": g.on_fail,
             "rules": [r.describe() for r in g.rules]}
            for g in GATES
        ],
    }
