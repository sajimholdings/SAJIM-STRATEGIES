"""
SAJIM Quant — CLI Runner
=========================
Single entry point for all strategy testing operations.

Usage:
    python -m sajim_quant test     --strategy <path> --symbol XAUUSD --tf H1
    python -m sajim_quant import   --csv <path> --name "My_Strategy"
    python -m sajim_quant scan     [--workspace <root>]
    python -m sajim_quant matrix   [--output <path>] [--workers N]
    python -m sajim_quant report   --result <json_path>
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# ── Project root resolution ──────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

# Load credentials/config from the workspace .env file if present (MT5_LOGIN etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(WORKSPACE_ROOT / ".env")
except ImportError:
    pass

from sajim_quant.core.schema import BacktestResult
from sajim_quant.analytics.metrics import calculate_metrics
from sajim_quant.analytics.report import generate_text_tear_sheet, generate_markdown_tear_sheet

logger = logging.getLogger("sajim_quant.cli")


# Engine name aliases: normalize catalog spellings (discovered_strategies.json)
# to the CLI engine names used by cmd_test.
ENGINE_ALIASES = {
    "python:backtrader": "backtrader",
    "mt5:headless": "mt5",
    "pine:imported": "pine_csv",
    "python:breakout_system": "btc_csv",
    "deriv:binary_options": "deriv_csv",
    "deriv:websocket_bot": "deriv_csv",
    "python:custom": "python_custom",
    "python:sajim_v2": "python_custom",
    "python:beep_vacuum": "beep_vacuum",
    "python:beep_physics": "beep_baseline",
    "python:beep_baseline": "beep_baseline",
    "python:structural_ai": "python_custom",
    "python:sovereign_sniper": "python_custom",
    "python:smc_daemon": "smc",
    # Catalog run_adapter values pass through unchanged.
    "backtrader": "backtrader",
    "mt5": "mt5",
    "beep_vacuum": "beep_vacuum",
    "beep_baseline": "beep_baseline",
    "smc": "smc",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO-DETECTION: Infer adapter type from file extension / content
# ═══════════════════════════════════════════════════════════════════════════════

def detect_engine(source_path: Path) -> str:
    """
    Detects strategy engine type from file extension and content.
    Returns one of: 'mt5', 'backtrader', 'python_custom', 'pine_csv',
                     'btc_csv', 'deriv_csv', 'unknown'
    """
    suffix = source_path.suffix.lower()

    # MQL5 Expert Advisor
    if suffix == ".mq5":
        return "mt5"

    # CSV files — detect subtype from header
    if suffix == ".csv":
        try:
            with open(source_path, "r", encoding="utf-8", errors="ignore") as f:
                header = f.readline().lower()
            if "trade #" in header and "profit" in header:
                return "pine_csv"
            if "entry_time" in header and "pnl_usd" in header:
                return "btc_csv"
            if "epoch" in header and "win" in header:
                return "deriv_csv"
        except Exception:
            pass
        return "generic_csv"

    # Python files — detect Backtrader vs custom
    if suffix == ".py":
        try:
            content = source_path.read_text(encoding="utf-8", errors="ignore")
            if "bt.Strategy" in content:
                return "backtrader"
            return "python_custom"
        except Exception:
            return "python_custom"

    return "unknown"


def _load_backtrader_class(py_path: Path):
    """
    Dynamically imports a .py file containing a bt.Strategy subclass and returns
    the first Strategy class found.
    """
    import backtrader as bt

    spec = importlib.util.spec_from_file_location(py_path.stem, str(py_path))
    module = importlib.util.module_from_spec(spec)

    # Add parent to sys.path so relative imports work
    parent = str(py_path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    spec.loader.exec_module(module)

    # Find the first bt.Strategy subclass
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, bt.Strategy)
            and attr is not bt.Strategy
        ):
            return attr

    raise ValueError(f"No bt.Strategy subclass found in {py_path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND: test — Run a single strategy backtest
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_test(args):
    """Run a single strategy backtest and output tear sheet."""
    # Check if args.strategy is a catalog ID
    from sajim_quant.core.catalog import get_catalog_item
    catalog_item = get_catalog_item(args.strategy)
    if catalog_item and not Path(args.strategy).exists():
        source = (WORKSPACE_ROOT / catalog_item.get("source_file", "")).resolve()
        if not args.name:
            args.name = catalog_item.get("id")
        if not args.engine:
            # The catalog's run_adapter IS the adapter name. This used to collapse
            # everything that was not backtrader/mt5 into "python_custom", which
            # then guessed the adapter from substrings in the filename — so
            # renaming a file silently changed which engine ran it.
            args.engine = catalog_item.get("run_adapter") or "python_custom"
    else:
        source = Path(args.strategy).resolve()

    if not source.exists():
        print(f"✗ Strategy source not found: {source}")
        sys.exit(1)

    engine = args.engine or detect_engine(source)
    engine = ENGINE_ALIASES.get(engine, engine)
    symbol = args.symbol
    tf = args.timeframe
    balance = args.balance

    print("=" * 70)
    print(f"  SAJIM QUANT — Strategy Backtest Runner")
    print(f"  Source:    {source.name}")
    print(f"  Engine:    {engine}")
    print(f"  Symbol:    {symbol}  |  Timeframe: {tf}")
    print(f"  Balance:   ${balance:,.0f}")
    print("=" * 70)

    t0 = time.time()
    result: Optional[BacktestResult] = None

    try:
        # ── Backtrader Adapter ───────────────────────────────────────────────
        if engine == "backtrader":
            from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter

            strat_class = _load_backtrader_class(source)
            adapter = BacktraderAdapter(
                strategy_class=strat_class,
                strategy_name=args.name or source.stem,
            )
            result = adapter.run(
                symbol=symbol,
                timeframe=tf,
                start_date=args.start,
                end_date=args.end,
                initial_balance=balance,
            )

        # ── MT5 Headless Adapter ─────────────────────────────────────────────
        elif engine == "mt5":
            from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter

            adapter = MT5HeadlessAdapter(
                mq5_source_path=source,
                strategy_name=args.name or source.stem,
            )
            result = adapter.run(
                symbol=symbol,
                timeframe=tf,
                start_date=args.start,
                end_date=args.end,
                initial_balance=balance,
            )

        # ── TradingView Pine CSV Import ──────────────────────────────────────
        elif engine == "pine_csv":
            from sajim_quant.adapters.pine.tradingview_importer import TradingViewTradeImporter

            result = TradingViewTradeImporter.import_trades_csv(
                csv_path=source,
                strategy_name=args.name or source.stem,
                symbol=symbol,
                timeframe=tf,
                initial_balance=balance,
            )

        # ── BTC Breakout CSV Import ──────────────────────────────────────────
        elif engine == "btc_csv":
            from sajim_quant.adapters.csv_importer import UniversalLogImporter

            result = UniversalLogImporter.import_btc_breakout_csv(
                csv_path=source,
                strategy_name=args.name or source.stem,
                symbol=symbol,
                timeframe=tf,
            )

        # ── Deriv UpDown CSV Import ──────────────────────────────────────────
        elif engine == "deriv_csv":
            from sajim_quant.adapters.csv_importer import UniversalLogImporter

            result = UniversalLogImporter.import_deriv_updown_csv(
                csv_path=source,
                strategy_name=args.name or source.stem,
                symbol=symbol,
                timeframe=tf,
            )

        # ── Custom Python strategies (BEEP / SMC daemons) ────────────────────
        elif engine in ("smc", "beep_vacuum", "beep_baseline", "python_custom"):
            strat_name = args.name or source.stem
            resolved = engine

            if resolved == "python_custom":
                # No catalog entry named an adapter. Fall back to filename hints,
                # but say so — this path is a guess, not a registration.
                src_lower = str(source).lower()
                name_lower = strat_name.lower()
                if "smc" in src_lower or "smc" in name_lower:
                    resolved = "smc"
                elif "vacuum" in src_lower or "vacuum" in name_lower:
                    resolved = "beep_vacuum"
                elif "beep" in src_lower or "beep" in name_lower:
                    resolved = "beep_baseline"
                else:
                    print(f"✗ No adapter registered for custom Python strategy '{source.name}'.")
                    print(f"  Add it to sajim_quant/core/strategy_catalog.json with a 'run_adapter' of")
                    print(f"  'smc', 'beep_vacuum', 'beep_baseline', 'backtrader' or 'mt5'.")
                    sys.exit(1)
                print(f"  ⚠ Adapter inferred from the filename as '{resolved}'. Add a catalog entry to pin it.")

            if resolved == "smc":
                from sajim_quant.adapters.python.smc_adapter import SMCAdapter
                adapter = SMCAdapter(strategy_name=strat_name)
            elif resolved == "beep_vacuum":
                from sajim_quant.adapters.python.beep_adapter import BEEPVacuumAdapter
                adapter = BEEPVacuumAdapter(strategy_name=strat_name)
            else:
                from sajim_quant.adapters.python.beep_adapter import BEEPBaselineAdapter
                adapter = BEEPBaselineAdapter(strategy_name=strat_name)

            result = adapter.run(
                symbol=symbol,
                timeframe=tf,
                start_date=args.start,
                end_date=args.end,
                initial_balance=balance,
            )

        else:
            print(f"✗ Unknown engine type: '{engine}'. Use --engine to specify.")
            print(f"  Supported: backtrader, mt5, pine_csv, btc_csv, deriv_csv, smc, beep_vacuum, beep_baseline")
            sys.exit(1)
    except FileNotFoundError as e:
        print(f"✗ Historical data not available: {e}")
        print(f"  Check data/parquet and data/dukascopy for {symbol} {tf} data.")
        sys.exit(1)

    elapsed = time.time() - t0

    metrics = None
    if result is not None and result.total_trades > 0:
        # ── Compute metrics & output ─────────────────────────────────────
        metrics = calculate_metrics(result)

        # Console output
        print(generate_text_tear_sheet(metrics))
        print(f"\n  ⏱ Completed in {elapsed:.1f}s  |  {result.total_trades} trades harvested")
    else:
        print(f"\n⚠ Strategy completed with 0 trades ({elapsed:.1f}s).")

    # ── Save outputs (always write the JSON so downstream tooling, including
    #    the dashboard AI agent, can read a well-formed result file) ───────
    output_dir = Path(args.output) if args.output else (WORKSPACE_ROOT / "data" / "results")
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (args.name or source.stem).replace(" ", "_")

    # Save markdown report
    if metrics is not None:
        md_path = output_dir / f"{safe_name}_{symbol}_{tf}_tearsheet.md"
        md_path.write_text(generate_markdown_tear_sheet(metrics), encoding="utf-8")
        print(f"  📄 Markdown tear sheet: {md_path}")

    # Save JSON result (trades + metrics) via the schema's own serializers
    json_path = output_dir / f"{safe_name}_{symbol}_{tf}_result.json"
    json_data = {
        "metadata": result.metadata.to_dict() if result is not None else {},
        "total_trades": result.total_trades if result is not None else 0,
        "metrics": (
            {k: v for k, v in metrics.__dict__.items()
             if k not in ("equity_series", "monthly_returns")}
            if metrics is not None else {}
        ),
        "trades": [t.to_dict() for t in result.trades] if result is not None else [],
    }
    json_path.write_text(json.dumps(json_data, indent=2, default=str), encoding="utf-8")
    print(f"  📊 JSON result:        {json_path}")

    # Mirror to sim/public/test_results for immediate static web serving
    try:
        public_dir = WORKSPACE_ROOT / "sim" / "public" / "test_results"
        if public_dir.parent.exists():
            public_dir.mkdir(parents=True, exist_ok=True)
            (public_dir / json_path.name).write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND: import — Import external trade CSV
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_import(args):
    """Import an external CSV file and produce metrics."""
    # Reuse test command with auto-detected engine
    args.strategy = args.csv
    args.engine = None  # auto-detect from CSV header
    if not args.name:
        args.name = Path(args.csv).stem
    cmd_test(args)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND: scan — Discover all strategies in workspace
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_scan(args):
    """Scan workspace for strategies and display catalog."""
    from sajim_quant.core.discovery import StrategyDiscoveryScanner

    root = Path(args.workspace or WORKSPACE_ROOT).resolve()
    print(f"🔍 Scanning {root} for strategies...\n")

    scanner = StrategyDiscoveryScanner(root)
    strategies = scanner.scan()

    if not strategies:
        print("  No strategies found.")
        return

    # Pretty table output
    print(f"  {'#':>3}  {'Name':<35}  {'Engine':<20}  {'Symbol':<10}  {'TF':<5}  Source")
    print(f"  {'─'*3}  {'─'*35}  {'─'*20}  {'─'*10}  {'─'*5}  {'─'*40}")

    for i, s in enumerate(strategies, 1):
        print(f"  {i:>3}  {s.name:<35}  {s.engine_type:<20}  {s.symbol_hint:<10}  {s.timeframe_hint:<5}  {s.source_file}")

    print(f"\n  Total: {len(strategies)} strategies discovered.")

    # Save catalog JSON
    catalog_path = scanner.save_catalog()
    print(f"  💾 Catalog saved: {catalog_path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND: matrix — Build strategy universe matrix with REAL backtests
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_matrix(args):
    """Build strategy × symbol × timeframe performance matrix using real backtests."""
    from sajim_quant.analytics.real_matrix import RealMatrixBuilder

    output = Path(args.output) if args.output else (WORKSPACE_ROOT / "data" / "strategy_universe_matrix.json")
    workers = int(args.workers) if args.workers else 1
    symbols = args.symbols.split(",") if args.symbols else None
    timeframes = args.timeframes.split(",") if args.timeframes else None
    strategies = args.strategies.split(",") if getattr(args, "strategies", None) else None

    builder = RealMatrixBuilder(
        workspace_root=WORKSPACE_ROOT,
        output_path=output,
        max_workers=workers,
    )

    if getattr(args, "clear_cache", False):
        builder.clear_cache()

    print("=" * 70)
    print("  SAJIM QUANT — Real Backtest Universe Matrix Builder")
    print(f"  Workers:     {workers}")
    print(f"  Symbols:     {symbols or 'ALL'}")
    print(f"  Timeframes:  {timeframes or 'ALL'}")
    print(f"  Strategies:  {strategies or 'ALL'}")
    print(f"  Output:      {output}")
    print("=" * 70)

    matrix = builder.build(symbols=symbols, timeframes=timeframes, strategies=strategies)
    print(f"\n✅ Matrix complete: {matrix['total_nodes']} nodes evaluated.")


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND: report — Generate tear sheet from existing JSON result
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_report(args):
    """Generate tear sheet from a previously saved JSON result."""
    json_path = Path(args.result).resolve()
    if not json_path.exists():
        print(f"✗ Result file not found: {json_path}")
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if "metrics" in data:
        import dataclasses

        from sajim_quant.analytics.metrics import PerformanceMetrics

        # Keep only keys the dataclass still declares, so a result written by an
        # older build (or a newer one) reports instead of raising TypeError.
        known = {f.name for f in dataclasses.fields(PerformanceMetrics)}
        m_dict = {k: v for k, v in data["metrics"].items() if k in known}
        missing = [
            f.name for f in dataclasses.fields(PerformanceMetrics)
            if f.name not in m_dict
            and f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
        ]
        if missing:
            print(f"✗ Result file is missing required metrics: {', '.join(missing)}")
            print(f"  Re-run the backtest to regenerate it.")
            sys.exit(1)
        m = PerformanceMetrics(**m_dict)
        print(generate_text_tear_sheet(m))

        md_path = json_path.with_suffix(".md")
        md_path.write_text(generate_markdown_tear_sheet(m), encoding="utf-8")
        print(f"\n  📄 Markdown saved: {md_path}")
    else:
        print("✗ JSON file does not contain 'metrics' key.")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
#  ARGUMENT PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sajim_quant",
        description="SAJIM Quant Strategy Benchmarking Platform — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Test a Backtrader strategy on Gold H1
  python -m sajim_quant test --strategy strategies/backtrader-pullback-window-xauusd/strategy.py --symbol XAUUSD --tf H1

  # Test an MT5 EA
  python -m sajim_quant test --strategy strategies/Scalper1/Scalper1.mq5 --symbol XAUUSD --tf M5

  # Import TradingView trade list
  python -m sajim_quant import --csv exports/tradingview_trades.csv --name "Pine_RSI_Gold"

  # Scan for all strategies
  python -m sajim_quant scan

  # Build real backtest matrix (parallelized)
  python -m sajim_quant matrix --workers 4
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── test ──
    p_test = subparsers.add_parser("test", help="Run a single strategy backtest")
    p_test.add_argument("--strategy", "-s", required=True, help="Path to strategy source file (.py, .mq5, .csv)")
    p_test.add_argument("--symbol", default="XAUUSD", help="Trading symbol (default: XAUUSD)")
    p_test.add_argument("--tf", "--timeframe", dest="timeframe", default="H1", help="Timeframe (default: H1)")
    p_test.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    p_test.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    p_test.add_argument("--balance", type=float, default=10000.0, help="Initial balance (default: 10000)")
    p_test.add_argument("--name", default=None, help="Strategy name override")
    p_test.add_argument("--engine", default=None, help="Force engine type: backtrader, mt5, pine_csv, btc_csv, deriv_csv")
    p_test.add_argument("--output", "-o", default=None, help="Output directory for results")
    p_test.set_defaults(func=cmd_test)

    # ── import ──
    p_import = subparsers.add_parser("import", help="Import external trade CSV")
    p_import.add_argument("--csv", required=True, help="Path to trade CSV file")
    p_import.add_argument("--symbol", default="XAUUSD", help="Trading symbol")
    p_import.add_argument("--tf", "--timeframe", dest="timeframe", default="H1", help="Timeframe")
    p_import.add_argument("--name", default=None, help="Strategy name")
    p_import.add_argument("--start", default=None, help="Start date")
    p_import.add_argument("--end", default=None, help="End date")
    p_import.add_argument("--balance", type=float, default=10000.0, help="Initial balance")
    p_import.add_argument("--engine", default=None, help="Force engine type")
    p_import.add_argument("--output", "-o", default=None, help="Output directory")
    p_import.set_defaults(func=cmd_import)

    # ── scan ──
    p_scan = subparsers.add_parser("scan", help="Discover strategies in workspace")
    p_scan.add_argument("--workspace", "-w", default=None, help="Workspace root to scan")
    p_scan.set_defaults(func=cmd_scan)

    # ── matrix ──
    p_matrix = subparsers.add_parser("matrix", help="Build real backtest universe matrix")
    p_matrix.add_argument("--output", "-o", default=None, help="Output JSON path")
    p_matrix.add_argument("--workers", "-w", default="1", help="Parallel workers (default: 1)")
    p_matrix.add_argument("--symbols", default=None, help="Comma-separated symbols to test (default: all)")
    p_matrix.add_argument("--timeframes", default=None, help="Comma-separated timeframes (default: all)")
    p_matrix.add_argument("--strategies", default=None, help="Comma-separated strategy IDs to test (default: all)")
    p_matrix.add_argument("--clear-cache", action="store_true", help="Clear the matrix cache before building")
    p_matrix.set_defaults(func=cmd_matrix)

    # ── report ──
    p_report = subparsers.add_parser("report", help="Generate tear sheet from saved JSON")
    p_report.add_argument("--result", "-r", required=True, help="Path to result JSON file")
    p_report.set_defaults(func=cmd_report)

    return parser


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # Force UTF-8 output on Windows to avoid cp1252 encoding errors
    import io
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
