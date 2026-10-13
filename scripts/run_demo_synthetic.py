"""
Demo pipeline run on SYNTHETIC data (random walk) - proves adapter -> registry -> dashboard works
on a machine without the Dukascopy download. The run is tagged `demo` + `synthetic-data`, named
accordingly and uses symbol `SYNTH`, so it can never be mistaken for a market result.

    python scripts/run_demo_synthetic.py        # records the run and re-exports the dashboard JSON
    sajim remove demo_sma_cross_synthetic SYNTH_H1_demo --delete-artifacts   # to drop it again
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtrader as bt  # noqa: E402

from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter  # noqa: E402
from sajim_quant.data import DataLoader  # noqa: E402
from sajim_quant.data.synthetic import random_walk_ohlcv  # noqa: E402
from sajim_quant.registry import ResultsRegistry, export_dashboard  # noqa: E402


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
                self.buy(size=5000)
            elif self.cross < 0:
                self.sell(size=5000)
        elif (self.position.size > 0 and self.cross < 0) or (self.position.size < 0 and self.cross > 0):
            self.close()


def main() -> int:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="sajim_demo_"))
    loader = DataLoader(parquet_root=tmp / "parquet", csv_root=tmp / "csv")
    loader.save_frame(random_walk_ohlcv(bars=4000, timeframe="H1", seed=2024), "SYNTH", "H1")

    result = BacktraderAdapter(SmaCross, strategy_name="DEMO_SMA_Cross_SyntheticData", data_loader=loader).run(
        symbol="SYNTH", timeframe="H1", initial_balance=10000.0, commission_per_lot=0.0,
    )
    result.metadata.notes = "DEMO on synthetic random-walk bars. Not a market result."
    reg = ResultsRegistry()
    rec = reg.record_run(
        result,
        strategy_id="demo_sma_cross_synthetic",
        run_id="SYNTH_H1_demo",
        folder="scripts/run_demo_synthetic.py",
        description="Pipeline demo: 10/30 SMA cross on synthetic random-walk bars. Exists only to show the registry, Monte Carlo and dashboard wiring.",
        tags=["demo", "synthetic-data"],
    )
    print(f"[demo] recorded {rec.strategy_id}/{rec.run_id}: {rec.trades} trades, Sharpe {rec.sharpe_ratio}, tier {rec.tier}")
    for name, p in export_dashboard(reg).items():
        print(f"[export] {name} -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
