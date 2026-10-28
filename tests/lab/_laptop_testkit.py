"""Shared fakes for builder D's tests (lab.cli / lab.run). No network, no claude, no data/**."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I

sys.dont_write_bytecode = True        # no __pycache__ in .claude/lab/hooks or .claude/skills

STRATEGY_SRC = '''\
import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind

PARAMS = {
    "fast": {"default": 10, "min": 5, "max": 20, "type": "int"},
    "rr": {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"},
}


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    fast = ind.ema(df["close"], params["fast"])
    rng = ind.atr(df, 14)
    out = pd.DataFrame(index=df.index)
    out["signal"] = ((df["close"] > fast) & rng.notna()).astype("int8")
    out["stop_dist"] = 2.0 * rng
    out["tp_dist"] = params["rr"] * 2.0 * rng
    return out
'''

LOOKAHEAD_SRC = STRATEGY_SRC.replace('fast = ind.ema(df["close"], params["fast"])',
                                     'fast = df["close"].shift(-1)')

SPEC_MD = "# Spec\n\n1. Description\n> Buy gold above the EMA\n"


def manifest(lab_id: str, **over: Any) -> Dict[str, Any]:
    m = {"schema": gates.MANIFEST_SCHEMA, "id": lab_id, "family": lab_id, "name": "EMA test (XAUUSD M15)",
         "description": "Buy gold above the EMA", "origin": gates.ORIGIN, "run_adapter": gates.RUN_ADAPTER,
         "engine": gates.ENGINE, "symbol": "XAUUSD", "timeframe": "M15", "supported_symbols": ["XAUUSD"],
         "breadth_symbols": ["XAGUSD"], "max_hold_bars": 96, "allow_short": False, "warmup_bars": 200,
         "spec_sha256": "", "code_sha256": ""}
    m.update(over)
    return m


def make_lab_folder(root: Path, lab_id: str = "lab_test_ema_xauusd", *, src: str = STRATEGY_SRC,
                    spec: Optional[str] = SPEC_MD, man: Optional[dict] = None) -> Path:
    folder = Path(root) / I.GENERATED_DIR / lab_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "strategy.py").write_bytes(src.encode("utf-8"))
    if spec is not None:
        (folder / "spec.md").write_bytes(spec.encode("utf-8"))
    (folder / "manifest.json").write_text(json.dumps(man or manifest(lab_id), indent=2), encoding="utf-8")
    return folder


def sha_of(folder: Path, name: str = "strategy.py") -> str:
    return hashlib.sha256((Path(folder) / name).read_bytes()).hexdigest()


def _rule(metric: str, op: str, thr: float, value: Any, passed: Optional[bool]) -> dict:
    return {"metric": metric, "op": op, "threshold": thr, "value": value, "passed": passed}


def make_gates(stage: str = "dev", *, fail: Optional[str] = None, holdout_pass: bool = True,
               holdout_trades: int = 40) -> List[dict]:
    """A gates list in gates.GATES order. `fail` = id of one gate whose first rule fails."""
    out = []
    for spec in gates.GATES:
        active = spec.phase <= I.LAB_PHASE
        rules = []
        for r in spec.rules:
            value: Any = r.value
            passed: Optional[bool] = True
            if not active:
                value, passed = None, None
            elif spec.id == "G8" and stage == "dev":
                value, passed = None, None
            elif spec.id == "G2" and r.metric == "holdout_trades":
                if stage == "dev":
                    value, passed = None, None
                else:
                    value, passed = holdout_trades, holdout_trades >= gates.MIN_HOLDOUT_TRADES
            elif spec.id == "G8" and not holdout_pass and r.metric == "holdout_pf":
                value, passed = 0.9, False
            if fail == spec.id and r is spec.rules[0]:
                value, passed = (1.08 if r.metric == "pf" else 0.0), False
            rules.append(_rule(r.metric, r.op, r.value, value, passed))
        evaluated = [x["passed"] for x in rules if x["passed"] is not None]
        gpassed = None if (not active or not evaluated or spec.id == "G11") else all(evaluated)
        out.append({"id": spec.id, "name": spec.name, "hard": spec.hard, "phase": spec.phase, "active": active,
                    "passed": gpassed, "value": None, "threshold": None, "note": spec.note, "rules": rules})
    return out


def make_scorecard(lab_id: str, sha: str, *, stage: str = "dev", fail: Optional[str] = None,
                   verdict: Optional[str] = None, final: bool = False, trial: int = 1,
                   holdout_pass: bool = True, holdout_trades: int = 40, created_at: str = "2026-10-12T22:15:30+00:00",
                   detail: bool = False, holdout_touches: int = 0) -> Dict[str, Any]:
    dev_pass = fail is None
    if verdict is None:
        if stage == "dev":
            verdict = gates.NOT_PROVEN if dev_pass else gates.REJECT
        else:
            verdict = gates.CANDIDATE if holdout_pass else gates.REJECT
    sc: Dict[str, Any] = {
        "schema": gates.SCORECARD_SCHEMA, "id": lab_id, "family": lab_id, "code_sha256": sha,
        "spec_sha256": "0" * 64, "sha12": sha[:12], "gates_version": gates.GATES_VERSION,
        "protocol_version": "v1", "lab_phase": 1, "stage": stage,
        "label": "IN-SAMPLE" if stage == "dev" else "HOLDOUT", "symbol": "XAUUSD", "timeframe": "M15",
        "window": {"start": "2024-09-01T00:00:00", "stop": "2026-01-01T00:00:00", "first_bar": None,
                   "last_bar": None, "n_bars": 33000, "warmup_bars": 200},
        "params": {"fast": 10, "rr": 2.0}, "trial": trial, "max_trials": 3, "n_trials_family": trial,
        "n_trials_global": 7, "holdout_touches": holdout_touches, "max_holdout_touches": 3, "holdout_burned": False,
        "cost_model": {"spread_model": "constant", "commission_model": "instruments_v1", "commission_rate": 0.0003,
                       "slippage_pips": 0.5, "round_trip_bps": 3.0, "slippage_points": 5.0,
                       "fx_desc": "quote currency USD (no conversion)", "breakeven_cost_mult": 1.84,
                       "breakeven_flag": "in_range"},
        "headline": {"trades": 212, "net": 1234.5, "return_pct": 12.3, "pf": 1.31, "expectancy": 5.82,
                     "win_rate": 0.41, "max_dd_pct": 7.9, "daily_sharpe": 1.12},
        "sides": {"long": {"trades": 212, "net": 1234.5, "pf": 1.31}, "short": {"trades": 0, "net": 0.0, "pf": None}},
        "holdout_split": None if stage == "dev" else {"H_A": {"trades": 25, "net": 300.0, "pf": 1.2},
                                                      "H_B": {"trades": 15, "net": -50.0, "pf": 0.9}},
        "gates": make_gates(stage, fail=fail, holdout_pass=holdout_pass, holdout_trades=holdout_trades),
        "dev_hard_gates_passed": dev_pass, "verdict": verdict, "final": final,
        "reasons": [] if dev_pass else [f"{fail} failed"], "warnings": [],
        "context": {"median_hold_days": 0.2, "financing_flag": False, "rollover_entry_share": 0.0,
                    "rollover_flag": False, "window_end_closes": 1, "untradable_stop": 3, "n_signals": 240,
                    "halted": False},
        "critic": {"decision": None, "to": None, "reason": "", "notes": ""},
        "seal_note": "procedural",
        "result_file": f"{lab_id}_XAUUSD_M15_{stage}_result.json", "deploy_id": "d1", "created_at": created_at,
    }
    if detail:
        sc["detail"] = {"plateau": {"neighbours": [{"run_id": "nb00", "net": 999.0}]}}
    return sc


def review(lab_id: str, sha: str, **over: Any) -> Dict[str, Any]:
    r = {"schema": I.REVIEW_SCHEMA, "lab_id": lab_id, "code_sha256": sha, "decision": "KEEP", "to": None,
         "reason": "rules match the spec", "mismatches": [], "smells": [], "cost_sensitivity": "moderate",
         "next_tests": ["try the adjacent timeframe"]}
    r.update(over)
    return r


class FakeClient:
    """Stands in for cli.LabClient. Scripted responses; every call is recorded on `calls`."""

    def __init__(self, base_url: str = "https://lab.example", *, lab_token: Optional[str] = None,
                 judge_token: Optional[str] = None, script: Optional[dict] = None,
                 calls: Optional[list] = None, **kw: Any):
        self.base_url = base_url
        self.scope = "judge" if judge_token else "builder"
        self.token = judge_token or lab_token
        self.script = script if script is not None else {}
        self.calls = calls if calls is not None else []
        self.kw = kw

    def _do(self, name: str, *args: Any) -> Any:
        self.calls.append((self.scope, name, args))
        resp = self.script.get(name)
        if callable(resp):
            resp = resp(*args)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def check(self, body):
        return self._do("check", body)

    def evaluate(self, body):
        return self._do("evaluate", body)

    def job(self, job_id):
        return self._do("job", job_id)

    def wait(self, job_id, **kw):
        return self._do("wait", job_id)

    def family(self, family):
        return self._do("family", family)

    def scorecard(self, family, sha12, stage="dev"):
        return self._do("scorecard", family, sha12, stage)

    def finalize(self, body):
        return self._do("finalize", body)
