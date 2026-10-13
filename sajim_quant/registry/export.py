"""
Dashboard export: turns the results registry into the JSON files the Next.js cockpit reads.

    leaderboard.json              one row per strategy (its primary run), sorted best to worst
    registry.json                 every run, grouped by strategy (the "Strategies" tab)
    correlation.json              pairwise daily-PnL correlation of primary runs that have trades
    strategy_universe_matrix.json symbol x timeframe grid per strategy, built ONLY from real runs

Nothing here fabricates a number. A grid cell with no run is `null` and the UI shows "not tested".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sajim_quant.analytics.behavioral_diagnostics import BehavioralDiagnosticsEngine
from sajim_quant.analytics.portfolio import PortfolioEngine
from sajim_quant.core.schema import BacktestResult
from sajim_quant.data.loader import find_repo_root
from sajim_quant.registry.store import ResultsRegistry, RunRecord
from sajim_quant.registry.tiers import tier_rank

# Canonical grid. Symbols/timeframes seen in the registry are added on top of these.
UNIVERSE_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP",
    "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "XAUUSD", "XAGUSD",
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "US30", "US500", "GER40", "UK100",
]
UNIVERSE_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"]

EXPORT_FILES = ("leaderboard.json", "registry.json", "correlation.json", "strategy_universe_matrix.json")


def default_dashboard_public_dir() -> Path:
    root = find_repo_root()
    return root / "apps" / "dashboard" / "public"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _leaderboard_row(r: RunRecord) -> Dict[str, Any]:
    return {
        "id": f"{r.strategy_id}:{r.run_id}",
        "strategy_id": r.strategy_id,
        "run_id": r.run_id,
        "name": r.name,
        "folder": r.folder,
        "engine": r.engine,
        "symbol": r.symbol,
        "timeframe": r.timeframe,
        "trades": r.trades,
        "win_rate_pct": r.win_rate_pct,
        "profit_factor": r.profit_factor,
        "net_pnl": r.net_pnl,
        "return_pct": r.return_pct,
        "max_drawdown_pct": r.max_drawdown_pct,
        "sharpe_ratio": r.sharpe_ratio,
        "sortino_ratio": r.sortino_ratio,
        "tier": r.tier,
        "edge_rating": r.edge_rating,
        "description": r.description,
        "tear_sheet_path": r.artifacts.get("tear_sheet.md"),
        "has_artifacts": "result.json" in r.artifacts,
        "source": r.source,
        "start_date": r.start_date,
        "end_date": r.end_date,
        "created_at": r.created_at,
        "monte_carlo_summary": r.monte_carlo_summary,
    }


def build_leaderboard(reg: ResultsRegistry) -> Dict[str, Any]:
    rows = [_leaderboard_row(r) for r in reg.primary_runs()]
    rows.sort(key=lambda d: (tier_rank(d["tier"]), -d["sharpe_ratio"], -d["net_pnl"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return {"last_updated": _now(), "total_strategies": len(rows), "strategies": rows}


def build_registry_export(reg: ResultsRegistry) -> Dict[str, Any]:
    groups = reg.strategies()
    return {
        "last_updated": _now(),
        "total_strategies": len(groups),
        "total_runs": sum(len(g["runs"]) for g in groups),
        "strategies": groups,
    }


def build_correlation(reg: ResultsRegistry) -> Dict[str, Any]:
    results: List[BacktestResult] = []
    for r in reg.primary_runs():
        res = reg.load_result(r.strategy_id, r.run_id)
        if res is not None and res.total_trades > 0:
            res.metadata.strategy_name = r.name  # unique per strategy, matches the leaderboard
            results.append(res)
    if len(results) < 2:
        return {
            "generated_at": _now(),
            "strategies": [r.metadata.strategy_name for r in results],
            "matrix": {},
            "diversification_ratio": None,
            "note": "Correlation needs at least two recorded runs with trades.",
        }
    port = PortfolioEngine.evaluate_portfolio(results)
    return {
        "generated_at": _now(),
        "strategies": port.strategies,
        "matrix": port.correlation_matrix,
        "diversification_ratio": round(port.diversification_ratio, 2),
        "portfolio_sharpe": round(port.portfolio_sharpe, 2),
        "portfolio_max_drawdown_pct": round(port.portfolio_max_drawdown_pct, 2),
        "note": "Pairwise correlation of daily realized PnL (exit-date buckets) across primary runs.",
    }


def build_universe(reg: ResultsRegistry) -> Dict[str, Any]:
    runs = reg.runs()
    symbols = list(UNIVERSE_SYMBOLS) + sorted({r.symbol for r in runs} - set(UNIVERSE_SYMBOLS))
    timeframes = list(UNIVERSE_TIMEFRAMES) + sorted({r.timeframe for r in runs} - set(UNIVERSE_TIMEFRAMES))

    out: Dict[str, Any] = {"generated_at": _now(), "symbols": symbols, "timeframes": timeframes, "strategies": []}
    for group in reg.strategies():
        sid = group["strategy_id"]
        best: Dict[tuple, Dict[str, Any]] = {}
        for run in group["runs"]:
            key = (run["symbol"], run["timeframe"])
            score = (tier_rank(run["tier"]), -run["sharpe_ratio"])
            if key not in best or score < (tier_rank(best[key]["tier"]), -best[key]["sharpe_ratio"]):
                best[key] = run

        combos: List[Dict[str, Any]] = []
        for (sym, tf), run in best.items():
            combos.append({
                "strategy_id": sid,
                "run_id": run["run_id"],
                "symbol": sym,
                "asset_class": BehavioralDiagnosticsEngine.get_asset_class(sym),
                "timeframe": tf,
                "trades": run["trades"],
                "win_rate_pct": run["win_rate_pct"],
                "profit_factor": run["profit_factor"],
                "net_pnl": run["net_pnl"],
                "max_drawdown_pct": run["max_drawdown_pct"],
                "sharpe_ratio": run["sharpe_ratio"],
                "tier": run["tier"],
                "optimal": False,
                "status": run["tier"],
                "source": run["source"],
                "diagnosis": reg.load_artifact(sid, run["run_id"], "diagnosis.json"),
                "monte_carlo": reg.load_artifact(sid, run["run_id"], "monte_carlo.json"),
            })
        combos.sort(key=lambda c: (tier_rank(c["tier"]), -c["sharpe_ratio"], -c["net_pnl"]))
        for i, c in enumerate(combos, 1):
            c["rank"] = i
        if combos:
            combos[0]["optimal"] = True

        heatmap = {sym: {tf: None for tf in timeframes} for sym in symbols}
        for c in combos:
            heatmap.setdefault(c["symbol"], {})[c["timeframe"]] = c["sharpe_ratio"]

        tested = len(combos)
        profitable = sum(1 for c in combos if c["net_pnl"] > 0)
        cells = len(symbols) * len(timeframes)
        out["strategies"].append({
            "id": sid,
            "name": group["name"],
            "folder": group["folder"],
            "engine": group["engine"],
            "tier": group.get("tier", "UNTESTED"),
            "description": group.get("description", ""),
            "grid_cells": cells,
            "total_combinations_tested": tested,
            "profitable_combinations": profitable,
            "universality_pct": round(profitable / tested * 100, 1) if tested else 0.0,
            "coverage_pct": round(tested / cells * 100, 1) if cells else 0.0,
            "peak_combination": _combo_summary(combos[0] if combos else None),
            "worst_combination": _combo_summary(combos[-1] if combos else None),
            "heatmap": heatmap,
            "ranked_combinations": combos,
        })
    return out


def _combo_summary(c: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not c:
        return None
    return {k: c[k] for k in ("symbol", "timeframe", "sharpe_ratio", "profit_factor", "net_pnl", "win_rate_pct", "max_drawdown_pct")}


def export_dashboard(reg: Optional[ResultsRegistry] = None, public_dir: Optional[Path] = None) -> Dict[str, Path]:
    """Writes all four JSON files. Returns {filename: path}."""
    reg = reg or ResultsRegistry()
    public_dir = Path(public_dir) if public_dir else default_dashboard_public_dir()
    public_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "leaderboard.json": build_leaderboard(reg),
        "registry.json": build_registry_export(reg),
        "correlation.json": build_correlation(reg),
        "strategy_universe_matrix.json": build_universe(reg),
    }
    written: Dict[str, Path] = {}
    for name, payload in payloads.items():
        p = public_dir / name
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        written[name] = p
    return written
