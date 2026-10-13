"""
Verification Suite for SAJIM Quant - Phase 1 Foundation
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sajim_quant.core.schema import Trade, RunMetadata, BacktestResult, Direction, ExitReason
from sajim_quant.data.loader import DataLoader


def test_schema_serialization():
    print("Testing Universal Trade Schema Contract...")
    meta = RunMetadata(
        strategy_name="GoldOrb_ORB",
        engine="mt5:headless",
        symbol="XAUUSD",
        timeframe="M15",
        start_date="2024-01-01T00:00:00Z",
        end_date="2024-12-31T23:59:59Z",
        initial_balance=10000.0,
        spread_model="floating",
        notes="Phase 1 test verification"
    )

    trade1 = Trade(
        trade_id="T001",
        strategy_name="GoldOrb_ORB",
        engine="mt5:headless",
        symbol="XAUUSD",
        timeframe="M15",
        direction=Direction.BUY,
        entry_time="2024-09-02T10:00:00Z",
        entry_price=2498.50,
        position_size=1.0,
        exit_time="2024-09-02T12:00:00Z",
        exit_price=2506.20,
        exit_reason=ExitReason.TAKE_PROFIT,
        gross_pnl=770.0,
        net_pnl=765.0,
        commission=5.0,
        mae=-45.0,
        mfe=810.0
    )

    result = BacktestResult(metadata=meta, trades=[trade1])
    assert result.total_trades == 1
    assert result.net_profit == 765.0
    assert trade1.duration_seconds == 7200.0

    # Test JSON serialization round-trip
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="sajim_phase1_"))
    test_json_path = scratch / "test_output.json"
    result.save_json(test_json_path)
    loaded_result = BacktestResult.load_json(test_json_path)
    assert loaded_result.total_trades == 1
    assert loaded_result.trades[0].trade_id == "T001"
    assert loaded_result.trades[0].direction == Direction.BUY
    assert loaded_result.trades[0].exit_reason == ExitReason.TAKE_PROFIT
    test_json_path.unlink()

    # Test CSV serialization round-trip
    test_csv_path = scratch / "test_output.csv"
    result.save_trades_csv(test_csv_path)
    loaded_csv = BacktestResult.load_trades_csv(test_csv_path, meta)
    assert loaded_csv.total_trades == 1
    assert loaded_csv.trades[0].net_pnl == 765.0
    test_csv_path.unlink()

    print("[PASS] Universal Trade Schema serialization verified.")


def test_data_loader_speed():
    print("Testing DataLoader & Parquet access speed...")
    loader = DataLoader()
    symbols = loader.list_symbols()
    print(f"Available symbols: {len(symbols)} found -> {symbols[:5]}...")

    if "XAUUSD" in symbols:
        # Benchmark loading H1
        t0 = time.perf_counter()
        df_h1 = loader.load("XAUUSD", "H1")
        t_h1 = (time.perf_counter() - t0) * 1000
        print(f"[PASS] XAUUSD H1 loaded: {len(df_h1):,} rows in {t_h1:.2f}ms")

        # Benchmark loading M15 with date filter
        t0 = time.perf_counter()
        df_m15 = loader.load("XAUUSD", "M15", start_date="2024-09-01", end_date="2024-10-01")
        t_m15 = (time.perf_counter() - t0) * 1000
        print(f"[PASS] XAUUSD M15 (1 month filter) loaded: {len(df_m15):,} rows in {t_m15:.2f}ms")

        # Benchmark loading M1 (heavy test)
        t0 = time.perf_counter()
        df_m1 = loader.load("XAUUSD", "M1")
        t_m1 = (time.perf_counter() - t0) * 1000
        print(f"[PASS] XAUUSD M1 loaded: {len(df_m1):,} rows in {t_m1:.2f}ms")


if __name__ == "__main__":
    test_schema_serialization()
    test_data_loader_speed()
