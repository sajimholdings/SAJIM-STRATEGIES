"""
Imports the hand-maintained leaderboard rows that existed before the registry.

Those rows have metrics but no trade list, so they are stored with source="legacy_manual",
no Monte Carlo and no equity curve. They stay visible in the Strategies list (tagged "legacy")
until the strategy is re-run through an adapter and `record_run` produces real artifacts.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from sajim_quant.registry.store import ResultsRegistry, RunRecord, slugify
from sajim_quant.registry.tiers import classify_tier


def _legacy_run_id(symbol: str, timeframe: str) -> str:
    return "legacy_" + re.sub(r"[^A-Za-z0-9]+", "_", f"{symbol}_{timeframe}").strip("_")


def _metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "total_trades": row.get("trades", 0),
        "net_profit": row.get("net_pnl", 0.0),
        "sharpe_ratio": row.get("sharpe_ratio", 0.0),
        "profit_factor": row.get("profit_factor", 0.0),
        "max_drawdown_pct": row.get("max_drawdown_pct", 0.0),
    }


def import_legacy_leaderboard(
    reg: ResultsRegistry,
    leaderboard_json: Path,
    tear_sheet_root: Optional[Path] = None,
) -> List[RunRecord]:
    """Each row becomes a legacy run; its tear sheet (if present) is copied into the run folder."""
    with open(leaderboard_json, "r", encoding="utf-8") as f:
        rows = json.load(f).get("strategies", [])
    out: List[RunRecord] = []
    for row in rows:
        sid = slugify(row["name"])
        rid = _legacy_run_id(row.get("symbol", "NA"), row.get("timeframe", "NA"))
        artifacts: Dict[str, str] = {}
        ts = row.get("tear_sheet_path")
        if ts and tear_sheet_root is not None:
            src = tear_sheet_root / Path(ts).name
            if src.exists():
                dst = reg.results_dir / sid / rid / "tear_sheet.md"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                artifacts["tear_sheet.md"] = f"{sid}/{rid}/tear_sheet.md"
        rec = RunRecord(
            run_id=rid, strategy_id=sid, name=row["name"],
            folder=row.get("folder", ""), engine=row.get("engine", "unknown"),
            symbol=row.get("symbol", "NA"), timeframe=row.get("timeframe", "NA"),
            start_date="", end_date="", initial_balance=0.0,
            trades=int(row.get("trades", 0)),
            win_rate_pct=float(row.get("win_rate_pct", 0.0)),
            profit_factor=float(row.get("profit_factor", 0.0)),
            net_pnl=float(row.get("net_pnl", 0.0)),
            return_pct=0.0,
            max_drawdown_pct=float(row.get("max_drawdown_pct", 0.0)),
            sharpe_ratio=float(row.get("sharpe_ratio", 0.0)),
            sortino_ratio=float(row.get("sortino_ratio", 0.0) or 0.0),
            tier=classify_tier(_metrics(row)),
            edge_rating="UNRATED",
            description=row.get("description", ""),
            tags=["legacy", "manual-entry", f"legacy-tier:{row.get('tier', '')}"],
            assumptions={"note": "Metrics transcribed from the pre-registry leaderboard; no trade list available."},
            artifacts=artifacts,
            source="legacy_manual",
        )
        reg.add_legacy_record(rec)
        out.append(rec)
    return out


def import_legacy_timeframe_matrix(reg: ResultsRegistry, matrix_json: Path) -> List[RunRecord]:
    """The old 'Legacy TF Matrix' rows become additional legacy runs of the same strategies."""
    with open(matrix_json, "r", encoding="utf-8") as f:
        combos = json.load(f).get("combinations", [])
    known: Dict[str, RunRecord] = {}
    for r in reg.runs():
        if r.engine != "unknown":
            known.setdefault(r.strategy_id, r)
    out: List[RunRecord] = []
    for c in combos:
        sid = slugify(c["strategy_name"])
        rid = _legacy_run_id(c.get("symbol", "NA"), c.get("timeframe", "NA"))
        if reg.get(sid, rid) is not None:
            continue  # the leaderboard row already covers this symbol/timeframe
        sib = known.get(sid)
        rec = RunRecord(
            run_id=rid, strategy_id=sid, name=c["strategy_name"],
            folder=sib.folder if sib else "", engine=sib.engine if sib else "unknown",
            symbol=c.get("symbol", "NA"), timeframe=c.get("timeframe", "NA"),
            start_date="", end_date="", initial_balance=0.0,
            trades=int(c.get("trades", 0)), win_rate_pct=float(c.get("win_rate_pct", 0.0)),
            profit_factor=float(c.get("profit_factor", 0.0)), net_pnl=float(c.get("net_pnl", 0.0)),
            return_pct=0.0, max_drawdown_pct=float(c.get("max_drawdown_pct", 0.0)),
            sharpe_ratio=float(c.get("sharpe_ratio", 0.0)), sortino_ratio=0.0,
            tier=classify_tier(_metrics(c)), edge_rating="UNRATED",
            description=c.get("recommendation", ""),
            tags=["legacy", "manual-entry", "timeframe-matrix", f"legacy-status:{c.get('status', '')}"],
            assumptions={"note": "Transcribed from the pre-registry timeframe matrix; no trade list available."},
            artifacts={}, source="legacy_manual",
        )
        reg.add_legacy_record(rec)
        out.append(rec)
    return out
