"""
Unit Tests for SAJIM Quant Engine Core
=======================================
Covers:
1. Universal Trade Schema serialization, coercion, and round-trips
2. Quantitative analytics & metrics calculations (including edge cases: zero trades, 100% win rate)
3. Engine auto-detection logic
4. Strategy Catalog Single Source of Truth integrity
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest

# Add workspace root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from sajim_quant.core.schema import (
    Trade,
    BacktestResult,
    RunMetadata,
    Direction,
    ExitReason,
    EngineType,
    _coerce_direction,
)
from sajim_quant.analytics.metrics import calculate_metrics
from sajim_quant.core.catalog import load_strategy_catalog, get_strategy_by_id
from sajim_quant.__main__ import detect_engine


# ── 1. Schema & Coercion Tests ────────────────────────────────────────────────

def test_direction_coercion():
    assert _coerce_direction("BUY") == Direction.BUY
    assert _coerce_direction("long") == Direction.BUY
    assert _coerce_direction("B") == Direction.BUY
    assert _coerce_direction("SELL") == Direction.SELL
    assert _coerce_direction("short") == Direction.SELL
    assert _coerce_direction("S") == Direction.SELL
    assert _coerce_direction(Direction.BUY) == Direction.BUY


def test_trade_schema_duration_and_dict_roundtrip():
    t1 = datetime(2025, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 10, 10, 30, 0, tzinfo=timezone.utc)

    trade = Trade(
        trade_id="T_001",
        strategy_name="SunriseOgle",
        engine="python:backtrader",
        symbol="XAUUSD",
        timeframe="M5",
        direction="BUY",
        entry_time=t1.isoformat(),
        entry_price=2650.0,
        position_size=1.0,
        exit_time=t2.isoformat(),
        exit_price=2660.0,
        exit_reason="TP",
        gross_pnl=100.0,
        net_pnl=98.0,
        commission=2.0,
    )

    # Verify duration was auto-calculated in __post_init__
    assert trade.duration_seconds == 1800

    # Verify direction was coerced to enum
    assert trade.direction == Direction.BUY
    assert trade.exit_reason == ExitReason.TAKE_PROFIT

    # Test dictionary serialization round-trip
    d = trade.to_dict()
    assert d["trade_id"] == "T_001"
    assert d["direction"] == "BUY"
    assert d["duration_seconds"] == 1800

    reconstructed = Trade.from_dict(d)
    assert reconstructed.trade_id == trade.trade_id
    assert reconstructed.direction == Direction.BUY
    assert reconstructed.net_pnl == 98.0


# ── 2. Metrics & Analytics Tests ──────────────────────────────────────────────

def test_metrics_empty_trades():
    meta = RunMetadata(
        strategy_name="EmptyStrat",
        engine=EngineType.PYTHON_CUSTOM.value,
        symbol="EURUSD",
        timeframe="H1",
        start_date="2025-01-01",
        end_date="2025-01-10",
        initial_balance=10000.0,
    )
    result = BacktestResult(metadata=meta, trades=[])
    metrics = calculate_metrics(result)

    assert metrics.total_trades == 0
    assert metrics.net_profit == 0.0
    assert metrics.win_rate_pct == 0.0
    # Ratios with no denominator are None, not 0.0 and not a sentinel, so the
    # leaderboard can render "n/a" instead of ranking an empty run.
    assert metrics.profit_factor is None
    assert metrics.win_loss_ratio is None
    assert metrics.recovery_factor is None
    assert metrics.sortino_ratio is None
    assert metrics.calmar_ratio is None
    assert metrics.cagr_pct is None
    # to_dict must survive the Nones rather than raising on round(None).
    assert metrics.to_dict()["profit_factor"] is None


def test_metrics_all_winners_ratios_are_none():
    meta = RunMetadata(
        strategy_name="WinningStrat",
        engine=EngineType.PYTHON_BACKTRADER.value,
        symbol="XAUUSD",
        timeframe="M5",
        start_date="2025-01-01",
        end_date="2025-01-02",
        initial_balance=10000.0,
    )
    t1 = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc).isoformat()

    trades = [
        Trade(trade_id=f"W_{i}", strategy_name="WinningStrat", engine=EngineType.PYTHON_BACKTRADER.value,
              symbol="XAUUSD", timeframe="M5", direction=Direction.BUY, entry_time=t1, entry_price=2600.0,
              position_size=1.0, exit_time=t2, exit_price=2610.0, exit_reason=ExitReason.TAKE_PROFIT,
              gross_pnl=100.0, net_pnl=95.0, commission=5.0)
        for i in range(5)
    ]
    result = BacktestResult(metadata=meta, trades=trades)
    metrics = calculate_metrics(result)

    assert metrics.total_trades == 5
    assert metrics.winning_trades == 5
    assert metrics.losing_trades == 0
    assert metrics.win_rate_pct == 100.0
    # No losing trades means profit factor, win/loss and Sortino have no
    # denominator. They must be None rather than 999.0: a sentinel sorts a
    # 5-trade run above every real strategy on the leaderboard.
    assert metrics.profit_factor is None
    assert metrics.win_loss_ratio is None
    assert metrics.sortino_ratio is None
    assert metrics.net_profit == 475.0
    assert metrics.to_dict()["sortino_ratio"] is None


def test_metrics_standard_performance():
    meta = RunMetadata(
        strategy_name="BalancedStrat",
        engine=EngineType.PYTHON_BACKTRADER.value,
        symbol="XAUUSD",
        timeframe="M5",
        start_date="2025-01-01",
        end_date="2025-01-02",
        initial_balance=10000.0,
    )
    t1 = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc).isoformat()

    trades = [
        Trade(trade_id="T1", strategy_name="BalancedStrat", engine=EngineType.PYTHON_BACKTRADER.value,
              symbol="XAUUSD", timeframe="M5", direction=Direction.BUY, entry_time=t1, entry_price=2600.0,
              position_size=1.0, exit_time=t2, exit_price=2620.0, exit_reason=ExitReason.TAKE_PROFIT,
              gross_pnl=200.0, net_pnl=190.0, commission=10.0),
        Trade(trade_id="T2", strategy_name="BalancedStrat", engine=EngineType.PYTHON_BACKTRADER.value,
              symbol="XAUUSD", timeframe="M5", direction=Direction.SELL, entry_time=t1, entry_price=2620.0,
              position_size=1.0, exit_time=t2, exit_price=2630.0, exit_reason=ExitReason.STOP_LOSS,
              gross_pnl=-100.0, net_pnl=-110.0, commission=10.0),
    ]
    result = BacktestResult(metadata=meta, trades=trades)
    metrics = calculate_metrics(result)

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate_pct == 50.0
    assert metrics.net_profit == 80.0
    # Profit factor: 190 / 110 ≈ 1.727
    assert round(metrics.profit_factor, 2) == 1.73


# ── 3. Engine Auto-Detection Tests ────────────────────────────────────────────

def test_detect_engine_mq5():
    with tempfile.NamedTemporaryFile(suffix=".mq5", delete=False) as f:
        path = Path(f.name)
    try:
        assert detect_engine(path) == "mt5"
    finally:
        path.unlink(missing_ok=True)


def test_detect_engine_backtrader_py():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
        f.write("import backtrader as bt\nclass MyStrat(bt.Strategy):\n    pass\n")
        path = Path(f.name)
    try:
        assert detect_engine(path) == "backtrader"
    finally:
        path.unlink(missing_ok=True)


def test_detect_engine_custom_py():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
        f.write("def run_bot():\n    print('Custom trading daemon')\n")
        path = Path(f.name)
    try:
        assert detect_engine(path) == "python_custom"
    finally:
        path.unlink(missing_ok=True)


def test_detect_engine_pine_csv():
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", delete=False) as f:
        f.write("Trade #,Type,Signal,Date/Time,Price,Profit,Cum. Profit\n")
        path = Path(f.name)
    try:
        assert detect_engine(path) == "pine_csv"
    finally:
        path.unlink(missing_ok=True)


# ── 4. Unified Strategy Catalog Integrity ─────────────────────────────────────

def test_strategy_catalog_integrity():
    catalog = load_strategy_catalog()
    assert len(catalog) > 0, "Strategy catalog should not be empty"

    required_keys = {"id", "name", "source_file", "supported_symbols", "supported_timeframes"}
    for item in catalog:
        for k in required_keys:
            assert k in item, f"Strategy {item.get('id')} missing required key '{k}'"

    # Test individual lookup
    sunrise = get_strategy_by_id("sunrise_ogle_xauusd")
    assert sunrise is not None
    assert sunrise["default_symbol"] == "XAUUSD"
    assert "backtrader" in sunrise["engine"]
