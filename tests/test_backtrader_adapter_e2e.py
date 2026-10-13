"""End-to-end: synthetic bars -> Backtrader adapter -> registry -> dashboard export."""

import json

import pytest

bt = pytest.importorskip("backtrader")

from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
from sajim_quant.data import DataLoader
from sajim_quant.data.synthetic import random_walk_ohlcv
from sajim_quant.registry import ResultsRegistry, export_dashboard


class SmaCross(bt.Strategy):
    params = (("fast", 10), ("slow", 30))

    def __init__(self):
        self.cross = bt.indicators.CrossOver(
            bt.indicators.SMA(self.data.close, period=self.p.fast),
            bt.indicators.SMA(self.data.close, period=self.p.slow),
        )

    def next(self):
        if not self.position:
            if self.cross > 0:
                self.buy(size=10000)
            elif self.cross < 0:
                self.sell(size=10000)
        elif (self.position.size > 0 and self.cross < 0) or (self.position.size < 0 and self.cross > 0):
            self.close()


def test_adapter_to_registry_to_dashboard(tmp_path):
    loader = DataLoader(parquet_root=tmp_path / "parquet", csv_root=tmp_path / "csv")
    loader.save_frame(random_walk_ohlcv(bars=2000, timeframe="H1", seed=3), "EURUSD", "H1")

    result = BacktraderAdapter(SmaCross, strategy_name="SMA_Cross_Fixture", data_loader=loader).run(
        symbol="EURUSD", timeframe="H1", initial_balance=10000.0, commission_per_lot=0.0,
    )
    assert result.total_trades > 10
    assert all(t.engine == "python:backtrader" and t.symbol == "EURUSD" for t in result.trades)

    reg = ResultsRegistry(tmp_path / "results")
    rec = reg.record_run(result, strategy_id="sma_cross_fixture", folder="tests", tags=["synthetic-data"])
    assert rec.trades == result.total_trades
    detail = reg.run_detail(rec.strategy_id, rec.run_id)
    assert detail["monte_carlo"]["num_trades"] == rec.trades
    assert len(detail["metrics"]["equity_series"]) == rec.trades + 1

    written = export_dashboard(reg, tmp_path / "public")
    lb = json.loads(written["leaderboard.json"].read_text())
    assert lb["strategies"][0]["strategy_id"] == "sma_cross_fixture" and lb["strategies"][0]["has_artifacts"]
