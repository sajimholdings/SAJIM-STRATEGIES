"""
sajim_quant.lab — the SAJIM Strategy Lab.

A plain-English strategy idea is turned (by the `claude` CLI, on the owner's own
login) into spec.md + strategy.py + manifest.json under
strategies/generated/<lab_id>/, then judged on Modal by a cost-honest
SignalSimulator against a DEV window and a sealed, once-per-sha HOLDOUT.

Frozen protocol (P0):
  splits.py    the DEV / PURGE / HOLDOUT calendar, H-A/H-B model-cutoff split,
               allowed timeframes and symbols            (PROTOCOL_VERSION)
  gates.py     trial budgets, system defaults (risk, balance, ruin floor) and
               every gate threshold G0..G11, verdict words  (GATES_VERSION)
  CONTRACT.md  what a generated strategy folder must contain and how it is run

Full design: docs/STRATEGY_LAB_PLAN.md. Verdicts are REJECT, NOT_PROVEN or
CANDIDATE; CANDIDATE means "eligible for paper/forward test", never an edge.

This package intentionally imports nothing at load time.
"""
