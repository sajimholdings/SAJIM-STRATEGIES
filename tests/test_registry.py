import json

from sajim_quant.registry import ResultsRegistry, classify_tier, export_dashboard
from sajim_quant.registry.legacy import import_legacy_leaderboard, import_legacy_timeframe_matrix
from tests.conftest import make_result


def test_record_run_writes_all_artifacts(fixture_result, results_dir):
    reg = ResultsRegistry(results_dir)
    rec = reg.record_run(fixture_result, strategy_id="fixture", folder="tests", description="synthetic")
    run_dir = results_dir / rec.strategy_id / rec.run_id
    for name in ("result.json", "trades.csv", "metrics.json", "monte_carlo.json", "diagnosis.json", "tear_sheet.md"):
        assert (run_dir / name).exists(), name
    assert rec.trades == 60 and rec.primary is True
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert len(metrics["equity_series"]) == 61
    mc = json.loads((run_dir / "monte_carlo.json").read_text())
    assert mc["num_simulations"] == 1000 and len(mc["p50_curve"]) == len(mc["trade_steps"])
    assert rec.tier == classify_tier(metrics)
    assert reg.run_detail("fixture", rec.run_id)["tear_sheet_md"].startswith("# SAJIM Benchmark Tear Sheet")


def test_primary_selection_and_grouping(results_dir):
    reg = ResultsRegistry(results_dir)
    weak = reg.record_run(make_result(win_rate=0.3, seed=1), strategy_id="s1", run_id="weak")
    strong = reg.record_run(make_result(win_rate=0.7, seed=2, timeframe="M15"), strategy_id="s1", run_id="strong")
    runs = {r.run_id: r for r in reg.runs()}
    assert runs["strong"].primary and not runs["weak"].primary
    assert strong.sharpe_ratio > weak.sharpe_ratio
    groups = reg.strategies()
    assert len(groups) == 1 and groups[0]["primary_run_id"] == "strong" and len(groups[0]["runs"]) == 2
    # explicit primary wins
    reg.record_run(make_result(win_rate=0.4, seed=3, timeframe="M5"), strategy_id="s1", run_id="forced", primary=True)
    assert {r.run_id: r.primary for r in reg.runs()}["forced"] is True


def test_export_dashboard_from_registry(results_dir, tmp_path):
    reg = ResultsRegistry(results_dir)
    reg.record_run(make_result(name="A", seed=11), strategy_id="a")
    reg.record_run(make_result(name="B", seed=12, symbol="XAUUSD", timeframe="M15"), strategy_id="b")
    public = tmp_path / "public"
    written = export_dashboard(reg, public)
    lb = json.loads(written["leaderboard.json"].read_text())
    assert [s["rank"] for s in lb["strategies"]] == [1, 2]
    assert lb["strategies"][0]["sharpe_ratio"] >= lb["strategies"][1]["sharpe_ratio"] or lb["strategies"][0]["tier"] != lb["strategies"][1]["tier"]
    corr = json.loads(written["correlation.json"].read_text())
    assert set(corr["strategies"]) == {"A", "B"} and corr["matrix"]["A"]["A"] == 1.0
    uni = json.loads(written["strategy_universe_matrix.json"].read_text())
    a = next(s for s in uni["strategies"] if s["id"] == "a")
    assert a["total_combinations_tested"] == 1
    assert a["heatmap"]["EURUSD"]["H1"] is not None and a["heatmap"]["EURUSD"]["M1"] is None
    assert a["ranked_combinations"][0]["monte_carlo"]["num_simulations"] == 1000
    reg_export = json.loads(written["registry.json"].read_text())
    assert reg_export["total_runs"] == 2


def test_legacy_import(results_dir, tmp_path):
    lb = {"strategies": [{"id": "x", "name": "Old Strat", "folder": "strategies/old", "engine": "mt5:headless",
                          "symbol": "XAUUSD", "timeframe": "H1", "trades": 2, "win_rate_pct": 50.0, "profit_factor": 2.1,
                          "net_pnl": 160.0, "max_drawdown_pct": 0.3, "sharpe_ratio": 1.15, "tier": "TIER 2: VIABLE",
                          "tear_sheet_path": "tests/old_tear_sheet.md", "description": "d"}]}
    (tmp_path / "lb.json").write_text(json.dumps(lb))
    (tmp_path / "old_tear_sheet.md").write_text("# old")
    tf = {"combinations": [{"strategy_name": "Old Strat", "symbol": "XAUUSD", "timeframe": "M15", "trades": 40,
                            "win_rate_pct": 55, "profit_factor": 1.4, "net_pnl": 900, "max_drawdown_pct": 3.0,
                            "sharpe_ratio": 1.3, "optimal": False, "status": "VIABLE", "recommendation": "ok"}]}
    (tmp_path / "tf.json").write_text(json.dumps(tf))
    reg = ResultsRegistry(results_dir)
    recs = import_legacy_leaderboard(reg, tmp_path / "lb.json", tear_sheet_root=tmp_path)
    assert recs[0].source == "legacy_manual" and recs[0].tier == "TIER 3: INSUFFICIENT DATA"
    assert (results_dir / "old_strat" / recs[0].run_id / "tear_sheet.md").read_text() == "# old"
    more = import_legacy_timeframe_matrix(reg, tmp_path / "tf.json")
    assert len(more) == 1 and more[0].engine == "mt5:headless" and more[0].tier == "TIER 2: VIABLE"
    assert len(reg.runs()) == 2


def test_classify_tier_rules():
    assert classify_tier({"total_trades": 0}) == "UNTESTED"
    assert classify_tier({"total_trades": 100, "net_profit": 1, "sharpe_ratio": 3, "profit_factor": 2, "max_drawdown_pct": 30}) == "TIER 3: TOXIC DD"
    assert classify_tier({"total_trades": 14, "net_profit": 4000, "sharpe_ratio": 3.4, "profit_factor": 5.7, "max_drawdown_pct": 0.8}) == "TIER 2: VIABLE"
    assert classify_tier({"total_trades": 200, "net_profit": 4000, "sharpe_ratio": 2.2, "profit_factor": 1.8, "max_drawdown_pct": 4}) == "TIER 1: ELITE"
    assert classify_tier({"total_trades": 200, "net_profit": -50, "sharpe_ratio": -3, "profit_factor": 0.5, "max_drawdown_pct": 10}) == "TIER 3: UNPROFITABLE"
