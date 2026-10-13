"""Shared fixtures: a deterministic synthetic BacktestResult so the suite runs without market data."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sajim_quant.core.schema import BacktestResult, Direction, ExitReason, RunMetadata, Trade


def make_result(name: str = "Fixture_Strategy", symbol: str = "EURUSD", timeframe: str = "H1",
                n: int = 60, seed: int = 7, win_rate: float = 0.55, engine: str = "python:custom") -> BacktestResult:
    rng = random.Random(seed)
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    trades = []
    t = start
    for i in range(n):
        win = rng.random() < win_rate
        pnl = round(rng.uniform(40, 120), 2) if win else -round(rng.uniform(30, 80), 2)
        entry = t
        exit_ = entry + timedelta(hours=rng.randint(1, 12))
        trades.append(Trade(
            trade_id=f"T{i:04d}", strategy_name=name, engine=engine, symbol=symbol, timeframe=timeframe,
            direction=Direction.BUY if rng.random() < 0.5 else Direction.SELL,
            entry_time=entry.isoformat(), entry_price=1.1, position_size=1.0,
            exit_time=exit_.isoformat(), exit_price=1.1 + pnl / 10000,
            exit_reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
            gross_pnl=pnl + 2.0, net_pnl=pnl, commission=2.0,
        ))
        t = exit_ + timedelta(hours=rng.randint(2, 30))
    meta = RunMetadata(
        strategy_name=name, engine=engine, symbol=symbol, timeframe=timeframe,
        start_date=start.isoformat(), end_date=t.isoformat(), initial_balance=10000.0,
        spread_model="constant", spread_pips=1.0, commission_model="per_lot", commission_rate=2.0,
    )
    return BacktestResult(metadata=meta, trades=trades)


@pytest.fixture
def fixture_result() -> BacktestResult:
    return make_result()


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    return tmp_path / "results"
