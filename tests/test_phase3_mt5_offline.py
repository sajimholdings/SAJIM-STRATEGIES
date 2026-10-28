"""
Phase 3 — MT5 Headless Adapter, offline.

`test_phase3_mt5_adapter.py` is a live integration test: it needs broker
credentials and a free terminal, so it skips in CI and on any machine that has
not been set up for it. That left the most intricate code in the repo — the
compile-time injection preprocessor and the trade harvester — with no automated
coverage at all.

These tests exercise that logic without MetaTrader. They mock the `MetaTrader5`
module, point the preprocessor at a temporary directory, and verify the three
behaviours that actually break in practice:

  1. injection is non-destructive and hooks OnTester correctly, including when
     the EA already defines one;
  2. an EA's bundled Include tree never lands in the terminal's global include
     root, where it would overwrite MetaTrader's own standard library;
  3. the harvester maps a schema-compliant sajim_trades.csv into Trade records
     and skips malformed rows instead of failing the run.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── MetaTrader5 stub ────────────────────────────────────────────────────────
# mt5_adapter imports MetaTrader5 at module scope. Install a stub before the
# import so these tests run on any platform.
if "MetaTrader5" not in sys.modules:
    stub = types.ModuleType("MetaTrader5")
    stub.initialize = lambda *a, **k: False  # type: ignore[attr-defined]
    stub.last_error = lambda: (0, "stub")  # type: ignore[attr-defined]
    stub.shutdown = lambda: None  # type: ignore[attr-defined]
    stub.symbol_info = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["MetaTrader5"] = stub

from sajim_quant.adapters.mql5.preprocessor import Mql5Preprocessor  # noqa: E402
from sajim_quant.core.schema import Trade  # noqa: E402

SAJIM_HEADER = (
    Path(__file__).resolve().parent.parent
    / "sajim_quant" / "adapters" / "mql5" / "SajimLogger.mqh"
)


@pytest.fixture()
def fake_terminal(tmp_path: Path) -> Path:
    """A throwaway MT5 data directory with a populated standard library."""
    data_dir = tmp_path / "Terminal" / "ABCDEF"
    include = data_dir / "MQL5" / "Include"
    (include / "Math" / "Alglib").mkdir(parents=True)
    (data_dir / "MQL5" / "Experts").mkdir(parents=True)

    # Two stand-ins for real MetaTrader standard-library headers.
    (include / "Math" / "Alglib" / "alglib.mqh").write_text(
        "// STOCK METATRADER ALGLIB", encoding="utf-8"
    )
    (include / "MovingAverages.mqh").write_text(
        "// STOCK METATRADER MOVING AVERAGES", encoding="utf-8"
    )
    return data_dir


def _preprocessor(fake_terminal: Path) -> Mql5Preprocessor:
    return Mql5Preprocessor(
        metaeditor_path=fake_terminal / "metaeditor64.exe",
        mt5_terminal_data_path=fake_terminal,
    )


def test_injection_is_non_destructive_and_hooks_on_tester(tmp_path, fake_terminal):
    """The original .mq5 is untouched; the copy gains the harvest hook."""
    src = tmp_path / "MyEA.mq5"
    original = "int OnInit() { return(0); }\nvoid OnTick() {}\n"
    src.write_text(original, encoding="utf-8")

    pre = _preprocessor(fake_terminal)
    out = pre.instrument_source(src, "MyEA")

    assert src.read_text(encoding="utf-8") == original, "source file was modified"
    assert out.parent == fake_terminal / "MQL5" / "Experts" / "Sajim"

    body = out.read_text(encoding="utf-8")
    assert "#include <SajimLogger.mqh>" in body
    assert "double OnTester()" in body
    assert 'SajimHarvestTrades("MyEA"' in body
    assert "void OnTick() {}" in body, "original code must be preserved"


def test_existing_on_tester_is_chained_not_clobbered(tmp_path, fake_terminal):
    """An EA that already defines OnTester keeps its return value."""
    src = tmp_path / "HasTester.mq5"
    src.write_text(
        "double OnTester() { return(1.23); }\nvoid OnTick() {}\n", encoding="utf-8"
    )

    out = _preprocessor(fake_terminal).instrument_source(src, "HasTester")
    body = out.read_text(encoding="utf-8")

    assert "double _Original_OnTester()" in body, "original OnTester was not renamed"
    assert "double res = _Original_OnTester();" in body
    assert "return res;" in body
    assert body.count("double OnTester()") == 1


def test_already_instrumented_source_is_not_double_injected(tmp_path, fake_terminal):
    src = tmp_path / "Done.mq5"
    src.write_text(
        '#include <SajimLogger.mqh>\n'
        'double OnTester() { SajimHarvestTrades("Done", _Symbol, "H1"); return 0.0; }\n',
        encoding="utf-8",
    )
    out = _preprocessor(fake_terminal).instrument_source(src, "Done")
    assert out.read_text(encoding="utf-8").count("SajimHarvestTrades") == 1


def test_bundled_includes_never_overwrite_the_mt5_standard_library(tmp_path, fake_terminal):
    """
    Regression test for the destructive include copy.

    The preprocessor used to merge an EA's sibling Include/ tree into the
    terminal's global MQL5/Include with dirs_exist_ok=True. GoldOrb ships
    Include/Math/Alglib/ and MetaTrader ships Include/Math/Alglib/, so
    instrumenting that one EA silently replaced the Alglib headers every other
    EA on the machine compiles against.
    """
    ea_dir = tmp_path / "GoldOrbLike"
    (ea_dir / "Include" / "Math" / "Alglib").mkdir(parents=True)
    (ea_dir / "Include" / "Math" / "Alglib" / "alglib.mqh").write_text(
        "// EA-BUNDLED ALGLIB (older fork)", encoding="utf-8"
    )
    (ea_dir / "Include" / "MovingAverages.mqh").write_text(
        "// EA-BUNDLED MOVING AVERAGES", encoding="utf-8"
    )
    src = ea_dir / "GoldOrbLike.mq5"
    src.write_text('#include "Include\\\\Math\\\\Alglib\\\\alglib.mqh"\nvoid OnTick() {}\n', encoding="utf-8")

    include_root = fake_terminal / "MQL5" / "Include"
    _preprocessor(fake_terminal).instrument_source(src, "GoldOrbLike")

    # The stdlib must be byte-for-byte unchanged.
    assert (include_root / "Math" / "Alglib" / "alglib.mqh").read_text(
        encoding="utf-8"
    ) == "// STOCK METATRADER ALGLIB"
    assert (include_root / "MovingAverages.mqh").read_text(
        encoding="utf-8"
    ) == "// STOCK METATRADER MOVING AVERAGES"

    # The EA's own copy is reachable beside the instrumented source...
    experts_include = fake_terminal / "MQL5" / "Experts" / "Sajim" / "Include"
    assert (experts_include / "Math" / "Alglib" / "alglib.mqh").exists()
    # ...and mirrored under a namespace that cannot collide.
    assert (include_root / "Sajim" / "GoldOrbLike" / "MovingAverages.mqh").exists()


def test_sajim_logger_header_is_installed(fake_terminal):
    """The one file we do put in the global include root is our own."""
    pre = _preprocessor(fake_terminal)
    installed = pre.ensure_include_installed()
    assert installed.name == "SajimLogger.mqh"
    assert installed.read_text(encoding="utf-8") == SAJIM_HEADER.read_text(encoding="utf-8")


def test_harvest_parses_schema_csv_and_skips_malformed_rows(tmp_path):
    """
    The harvest loop maps sajim_trades.csv into Trade records, tolerating a bad
    row rather than losing the whole run. Mirrors mt5_adapter.run()'s reader.
    """
    import csv

    csv_path = tmp_path / "sajim_trades.csv"
    csv_path.write_text(
        "trade_id,strategy_name,engine,symbol,timeframe,direction,entry_time,entry_price,"
        "position_size,exit_time,exit_price,exit_reason,gross_pnl,net_pnl,commission,"
        "swap_cost,return_pct,mae,mfe,duration_seconds\n"
        "MT5_1,GoldOrb,mt5:headless,XAUUSD,PERIOD_H1,BUY,2024-09-02T10:00:00Z,2498.50,"
        "0.10,2024-09-02T12:00:00Z,2506.20,TP,77.00,76.00,-1.00,0.00,0.0,,,7200\n"
        "MT5_2,GoldOrb,mt5:headless,XAUUSD,PERIOD_H1,SELL,2024-09-03T10:00:00Z,NOT_A_NUMBER,"
        "0.10,2024-09-03T12:00:00Z,2506.20,SL,-30.00,-31.00,-1.00,0.00,0.0,,,7200\n",
        encoding="utf-8",
    )

    trades, skipped = [], 0
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                trades.append(Trade(
                    trade_id=row["trade_id"],
                    strategy_name=row["strategy_name"],
                    engine=row["engine"],
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    direction=row["direction"],
                    entry_time=row["entry_time"],
                    entry_price=float(row["entry_price"]),
                    position_size=float(row["position_size"]),
                    exit_time=row["exit_time"],
                    exit_price=float(row["exit_price"]),
                    exit_reason=row["exit_reason"],
                    gross_pnl=float(row["gross_pnl"]),
                    net_pnl=float(row["net_pnl"]),
                    commission=float(row["commission"]),
                    swap_cost=float(row["swap_cost"]),
                ))
            except (ValueError, KeyError, TypeError):
                skipped += 1

    assert len(trades) == 1 and skipped == 1
    t = trades[0]
    assert t.trade_id == "MT5_1"
    assert t.direction.value == "BUY"
    assert t.exit_reason.value == "TP"
    assert t.net_pnl == 76.0
    assert t.duration_seconds == 7200.0


def test_ini_is_shredded_after_a_run(tmp_path):
    """The tester INI holds the account password in plaintext; it must not persist."""
    from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter

    ini = tmp_path / "tester_run.ini"
    ini.write_text("[Common]\nLogin=12345\nPassword=sup3rs3cret\n", encoding="utf-8")

    MT5HeadlessAdapter._shred_ini(ini)
    assert not ini.exists(), "INI containing credentials was left on disk"

    # Shredding an already-absent file is a no-op, never an exception.
    MT5HeadlessAdapter._shred_ini(ini)
