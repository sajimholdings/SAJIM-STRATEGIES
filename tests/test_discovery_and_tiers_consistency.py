"""Guards against the registry drifting from the exported dashboard contract."""

import json
from pathlib import Path

from sajim_quant.data.loader import find_repo_root
from sajim_quant.registry import ResultsRegistry, classify_tier


def test_committed_registry_is_consistent():
    root = find_repo_root()
    reg = ResultsRegistry(root / "results")
    runs = reg.runs()
    assert runs, "results/registry.json should contain the imported legacy rows and the demo run"
    primaries = [r for r in runs if r.primary]
    assert len({r.strategy_id for r in primaries}) == len(primaries) == len({r.strategy_id for r in runs})
    for r in runs:
        assert r.tier == classify_tier({
            "total_trades": r.trades, "net_profit": r.net_pnl, "sharpe_ratio": r.sharpe_ratio,
            "profit_factor": r.profit_factor, "max_drawdown_pct": r.max_drawdown_pct,
        })
        for name, rel in r.artifacts.items():
            assert (root / "results" / rel).exists(), f"missing artifact {rel}"


def test_exported_dashboard_json_matches_registry():
    root = find_repo_root()
    public = root / "apps" / "dashboard" / "public"
    reg = ResultsRegistry(root / "results")
    lb = json.loads((public / "leaderboard.json").read_text())
    assert {s["run_id"] for s in lb["strategies"]} == {r.run_id for r in reg.primary_runs()}
    exported = json.loads((public / "registry.json").read_text())
    assert exported["total_runs"] == len(reg.runs())
    uni = json.loads((public / "strategy_universe_matrix.json").read_text())
    for s in uni["strategies"]:
        filled = sum(1 for row in s["heatmap"].values() for v in row.values() if v is not None)
        assert filled == s["total_combinations_tested"]
