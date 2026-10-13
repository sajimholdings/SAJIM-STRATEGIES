import json

from sajim_quant.cli import main
from tests.conftest import make_result
from sajim_quant.registry import ResultsRegistry


def test_cli_list_export_show_remove(tmp_path, capsys):
    results = tmp_path / "results"
    reg = ResultsRegistry(results)
    rec = reg.record_run(make_result(name="CLI_Strat", seed=5), strategy_id="cli_strat", run_id="r1")

    assert main(["--results-dir", str(results), "list"]) == 0
    assert "cli_strat" in capsys.readouterr().out

    public = tmp_path / "public"
    assert main(["--results-dir", str(results), "export", "--public-dir", str(public)]) == 0
    assert json.loads((public / "leaderboard.json").read_text())["strategies"][0]["run_id"] == rec.run_id
    capsys.readouterr()

    assert main(["--results-dir", str(results), "show", "cli_strat", "r1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["monte_carlo"]["num_trades"] == 60 and "equity_series" not in out["metrics"]

    assert main(["--results-dir", str(results), "remove", "cli_strat", "r1", "--delete-artifacts"]) == 0
    assert reg.runs() == [] and not (results / "cli_strat" / "r1").exists()
    assert main(["--results-dir", str(results), "show", "cli_strat", "r1"]) == 1
