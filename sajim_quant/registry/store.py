"""
Results registry: every backtest run that goes through the platform is recorded here.

Layout (relative to the repository root, override with SAJIM_RESULTS_DIR):

    results/
      registry.json                         index of every run + the primary run per strategy
      <strategy_id>/<run_id>/
        result.json       BacktestResult (RunMetadata + normalized trades)
        trades.csv        the same trades as CSV (for Excel / quick inspection)
        metrics.json      PerformanceMetrics incl. equity_series and monthly_returns
        monte_carlo.json  MonteCarloResult on the real trade PnLs
        diagnosis.json    BehavioralDiagnosis
        tear_sheet.md     markdown tear sheet

A "strategy" is identified by a slug (strategy_id). One strategy can have many runs
(different symbols, timeframes, parameter sets, dates). The registry keeps all of them and
marks one as `primary` for leaderboard purposes (highest Sharpe with a valid sample, unless
a run is recorded with primary=True explicitly).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sajim_quant.analytics.behavioral_diagnostics import BehavioralDiagnosticsEngine
from sajim_quant.analytics.metrics import PerformanceMetrics, calculate_metrics
from sajim_quant.analytics.monte_carlo import MonteCarloSimulator
from sajim_quant.analytics.report import generate_markdown_tear_sheet
from sajim_quant.core.schema import BacktestResult
from sajim_quant.data.loader import find_repo_root
from sajim_quant.registry.tiers import classify_tier, tier_rank

REGISTRY_VERSION = 1
ARTIFACT_FILES = ("result.json", "trades.csv", "metrics.json", "monte_carlo.json", "diagnosis.json", "tear_sheet.md")


def default_results_dir() -> Path:
    env = os.environ.get("SAJIM_RESULTS_DIR")
    return Path(env).expanduser().resolve() if env else find_repo_root() / "results"


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s or "strategy"


@dataclass
class RunRecord:
    """One row of registry.json."""
    run_id: str
    strategy_id: str
    name: str
    folder: str
    engine: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float
    trades: int
    win_rate_pct: float
    profit_factor: float
    net_pnl: float
    return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    tier: str
    edge_rating: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    assumptions: Dict[str, Any] = field(default_factory=dict)
    monte_carlo_summary: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)   # relative paths inside results/
    source: str = "registry"                                  # "registry" | "legacy_manual"
    primary: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunRecord":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


class ResultsRegistry:
    def __init__(self, results_dir: Optional[Path] = None) -> None:
        self.results_dir = Path(results_dir) if results_dir else default_results_dir()
        self.index_path = self.results_dir / "registry.json"

    # ------------------------------------------------------------------ index
    def _load_index(self) -> Dict[str, Any]:
        if self.index_path.exists():
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("runs", [])
            return data
        return {"version": REGISTRY_VERSION, "updated_at": None, "runs": []}

    def _save_index(self, data: Dict[str, Any]) -> None:
        data["version"] = REGISTRY_VERSION
        data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data["runs"] = [r.to_dict() if isinstance(r, RunRecord) else r for r in data["runs"]]
        self._assign_primary(data["runs"])
        self.results_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=_json_default)
        tmp.replace(self.index_path)

    @staticmethod
    def _assign_primary(runs: List[Dict[str, Any]]) -> None:
        """Exactly one primary run per strategy: an explicit one wins, else best tier then Sharpe."""
        by_strat: Dict[str, List[Dict[str, Any]]] = {}
        for r in runs:
            by_strat.setdefault(r["strategy_id"], []).append(r)
        for group in by_strat.values():
            explicit = [r for r in group if r.get("primary") and r.get("_explicit_primary")]
            if explicit:
                winner = explicit[-1]
            else:
                winner = sorted(group, key=lambda r: (tier_rank(r["tier"]), -r["sharpe_ratio"], -r["trades"]))[0]
            for r in group:
                r["primary"] = r is winner

    def runs(self) -> List[RunRecord]:
        return [RunRecord.from_dict(r) for r in self._load_index()["runs"]]

    def primary_runs(self) -> List[RunRecord]:
        return [r for r in self.runs() if r.primary]

    def get(self, strategy_id: str, run_id: str) -> Optional[RunRecord]:
        for r in self.runs():
            if r.strategy_id == strategy_id and r.run_id == run_id:
                return r
        return None

    def strategies(self) -> List[Dict[str, Any]]:
        """Grouped view: one dict per strategy with its runs (primary first)."""
        groups: Dict[str, Dict[str, Any]] = {}
        for r in self.runs():
            g = groups.setdefault(r.strategy_id, {
                "strategy_id": r.strategy_id, "name": r.name, "folder": r.folder,
                "engine": r.engine, "description": r.description, "runs": [],
            })
            g["runs"].append(r.to_dict())
            if r.primary:
                g["primary_run_id"] = r.run_id
                g["tier"] = r.tier
        for g in groups.values():
            g["runs"].sort(key=lambda d: (not d["primary"], tier_rank(d["tier"]), -d["sharpe_ratio"]))
        return sorted(groups.values(), key=lambda g: (tier_rank(g.get("tier", "UNTESTED")), g["name"]))

    # -------------------------------------------------------------- recording
    def record_run(
        self,
        result: BacktestResult,
        *,
        strategy_id: Optional[str] = None,
        folder: str = "",
        description: str = "",
        tags: Iterable[str] = (),
        assumptions: Optional[Dict[str, Any]] = None,
        tear_sheet_md: Optional[str] = None,
        num_simulations: int = 1000,
        primary: bool = False,
        run_id: Optional[str] = None,
    ) -> RunRecord:
        """
        Computes every analytic from the real trades, writes the artifact folder and updates the index.
        Re-recording an existing run_id overwrites it.
        """
        meta = result.metadata
        sid = slugify(strategy_id or meta.strategy_name)
        rid = run_id or f"{meta.symbol}_{meta.timeframe}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        rid = re.sub(r"[^A-Za-z0-9_.-]+", "_", rid)
        run_dir = self.results_dir / sid / rid
        run_dir.mkdir(parents=True, exist_ok=True)

        metrics: PerformanceMetrics = calculate_metrics(result)
        pnls = [t.net_pnl for t in sorted(result.trades, key=lambda t: t.exit_time or t.entry_time)]
        mc = MonteCarloSimulator.simulate(
            trade_pnls=pnls,
            initial_balance=metrics.initial_balance,
            historical_max_dd_pct=metrics.max_drawdown_pct,
            historical_sharpe=metrics.sharpe_ratio,
            num_simulations=num_simulations,
        )
        diagnosis = BehavioralDiagnosticsEngine.diagnose(
            strategy_name=meta.strategy_name,
            symbol=meta.symbol,
            timeframe=meta.timeframe,
            sharpe=metrics.sharpe_ratio,
            profit_factor=metrics.profit_factor,
            win_rate=metrics.win_rate_pct,
            max_dd_pct=metrics.max_drawdown_pct,
            trades=metrics.total_trades,
            net_pnl=metrics.net_profit,
        )
        assumptions = dict(assumptions or {})
        assumptions.setdefault("spread_model", meta.spread_model)
        assumptions.setdefault("spread_pips", meta.spread_pips)
        assumptions.setdefault("commission_model", meta.commission_model)
        assumptions.setdefault("commission_rate", meta.commission_rate)
        assumptions.setdefault("slippage_pips", meta.slippage_pips)
        if meta.notes:
            assumptions.setdefault("notes", meta.notes)
        tear_sheet = tear_sheet_md or generate_markdown_tear_sheet(
            metrics, assumptions={k: v for k, v in assumptions.items() if v not in (None, "")}
        )

        # ---- artifacts
        result.save_json(run_dir / "result.json")
        result.save_trades_csv(run_dir / "trades.csv")
        metrics_dict = metrics.to_dict()
        metrics_dict["equity_series"] = metrics.equity_series
        metrics_dict["monthly_returns"] = metrics.monthly_returns
        metrics_dict["tier"] = classify_tier(metrics)
        _dump(run_dir / "metrics.json", metrics_dict)
        _dump(run_dir / "monte_carlo.json", asdict(mc))
        _dump(run_dir / "diagnosis.json", asdict(diagnosis))
        (run_dir / "tear_sheet.md").write_text(tear_sheet, encoding="utf-8")

        rel = f"{sid}/{rid}"
        record = RunRecord(
            run_id=rid,
            strategy_id=sid,
            name=meta.strategy_name,
            folder=folder,
            engine=meta.engine,
            symbol=meta.symbol,
            timeframe=meta.timeframe,
            start_date=meta.start_date,
            end_date=meta.end_date,
            initial_balance=round(metrics.initial_balance, 2),
            trades=metrics.total_trades,
            win_rate_pct=round(metrics.win_rate_pct, 2),
            profit_factor=round(metrics.profit_factor, 2),
            net_pnl=round(metrics.net_profit, 2),
            return_pct=round(metrics.return_pct, 2),
            max_drawdown_pct=round(metrics.max_drawdown_pct, 2),
            sharpe_ratio=round(metrics.sharpe_ratio, 2),
            sortino_ratio=round(metrics.sortino_ratio, 2),
            tier=metrics_dict["tier"],
            edge_rating=diagnosis.edge_rating,
            description=description,
            tags=list(tags),
            assumptions=assumptions,
            monte_carlo_summary={
                "num_simulations": int(mc.num_simulations),
                "worst_drawdown_95_pct": float(mc.worst_drawdown_95_pct),
                "worst_drawdown_99_pct": float(mc.worst_drawdown_99_pct),
                "risk_of_ruin_20_pct": float(mc.risk_of_ruin_20_pct),
                "fragility_index": float(mc.fragility_index),
                "is_robust": bool(mc.is_robust),
                "terminal_median_balance": float(mc.terminal_median_balance),
            },
            artifacts={name: f"{rel}/{name}" for name in ARTIFACT_FILES},
            source="registry",
            primary=primary,
        )

        data = self._load_index()
        data["runs"] = [r for r in data["runs"] if not (r["strategy_id"] == sid and r["run_id"] == rid)]
        row = record.to_dict()
        if primary:
            row["_explicit_primary"] = True
        data["runs"].append(row)
        self._save_index(data)
        record.primary = next(r["primary"] for r in data["runs"] if r["run_id"] == rid and r["strategy_id"] == sid)
        return record

    def add_legacy_record(self, record: RunRecord) -> RunRecord:
        """Registers a run that has no artifacts (hand-entered historical leaderboard rows)."""
        data = self._load_index()
        data["runs"] = [r for r in data["runs"] if not (r["strategy_id"] == record.strategy_id and r["run_id"] == record.run_id)]
        data["runs"].append(record.to_dict())
        self._save_index(data)
        return record

    def remove_run(self, strategy_id: str, run_id: str, delete_artifacts: bool = False) -> bool:
        data = self._load_index()
        before = len(data["runs"])
        data["runs"] = [r for r in data["runs"] if not (r["strategy_id"] == strategy_id and r["run_id"] == run_id)]
        if len(data["runs"]) == before:
            return False
        self._save_index(data)
        if delete_artifacts:
            import shutil
            shutil.rmtree(self.results_dir / strategy_id / run_id, ignore_errors=True)
        return True

    # -------------------------------------------------------------- artifacts
    def load_artifact(self, strategy_id: str, run_id: str, name: str) -> Any:
        p = self.results_dir / strategy_id / run_id / name
        if not p.exists():
            return None
        if p.suffix == ".json":
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return p.read_text(encoding="utf-8")

    def load_result(self, strategy_id: str, run_id: str) -> Optional[BacktestResult]:
        p = self.results_dir / strategy_id / run_id / "result.json"
        return BacktestResult.load_json(p) if p.exists() else None

    def run_detail(self, strategy_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        """Everything the dashboard detail drawer needs, in one dict."""
        rec = self.get(strategy_id, run_id)
        if rec is None:
            return None
        return {
            "run": rec.to_dict(),
            "metrics": self.load_artifact(strategy_id, run_id, "metrics.json"),
            "monte_carlo": self.load_artifact(strategy_id, run_id, "monte_carlo.json"),
            "diagnosis": self.load_artifact(strategy_id, run_id, "diagnosis.json"),
            "tear_sheet_md": self.load_artifact(strategy_id, run_id, "tear_sheet.md"),
        }


def _dump(path: Path, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=_json_default)


def _json_default(o: Any) -> Any:
    try:
        import numpy as np
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)
