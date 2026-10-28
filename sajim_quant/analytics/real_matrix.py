"""
SAJIM Quant — Real Backtest Universe Matrix Builder
=====================================================
Replaces the synthetic matrix with ACTUAL backtest execution across
all strategies × symbols × timeframes where an adapter is available.

Results are cached per-node as JSON files so re-runs are instant
(only re-tests if source code changed or cache is explicitly cleared).

For strategies without a wired adapter (custom Python engines), the
node is marked as 'PENDING_ADAPTER' rather than fabricated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("sajim_quant.analytics.real_matrix")

# ── Project root ────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent

# ── Symbols & Timeframes ────────────────────────────────────────────────────
ALL_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP",
    "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "XAUUSD", "XAGUSD",
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "US30", "US500", "GER40", "UK100",
]

ALL_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Strategy Registry: maps strategy ID → how to instantiate its adapter
# ═══════════════════════════════════════════════════════════════════════════════

def _load_strategy_registry() -> List[Dict[str, Any]]:
    """
    Strategy registry, read from the single source of truth:
    `sajim_quant/core/strategy_catalog.json`.

    This used to be a hardcoded list here, with its own id scheme
    (`strat_sunrise_ogle` vs the catalog's `sunrise_ogle_xauusd`). Between this
    module, the catalog, the leaderboard JSON and the discovery scanner there
    were four disagreeing registries, and adding a strategy meant editing three
    of them by hand. The catalog now feeds all of them.
    """
    from sajim_quant.core.catalog import load_strategy_catalog

    registry: List[Dict[str, Any]] = []
    for item in load_strategy_catalog():
        adapter = item.get("run_adapter")
        if not adapter:
            # No adapter wired: the node is reported as PENDING_ADAPTER rather
            # than skipped, so the gap is visible on the matrix.
            adapter = "pending"
        registry.append({
            "id": item["id"],
            "name": item.get("name", item["id"]),
            "adapter": adapter,
            "source": item.get("source_file", ""),
            "default_symbol": item.get("default_symbol", "XAUUSD"),
            "default_timeframe": item.get("default_timeframe", "H1"),
            "tier": item.get("tier", "UNRANKED"),
            "description": item.get("description", ""),
            "supported_symbols": item.get("supported_symbols") or [],
            "supported_timeframes": item.get("supported_timeframes") or [],
        })
    return registry


#: Resolved once at import for callers that expect a module-level list.
STRATEGY_REGISTRY = _load_strategy_registry()


def _get_data_availability(symbol: str, timeframe: str) -> bool:
    """Check if Parquet data exists for this symbol/timeframe."""
    parquet_dir = WORKSPACE_ROOT / "data" / "parquet" / symbol
    if not parquet_dir.exists():
        return False
    # Check for any parquet file matching the timeframe
    pattern = f"*{timeframe}*"
    return any(parquet_dir.glob(pattern))


#: Modules whose behaviour changes a node's numbers. Hashing only the strategy
#: source meant an edit to an adapter or to the metrics maths left every cached
#: node stale but still served.
_PRICING_MODULES = (
    "sajim_quant/core/schema.py",
    "sajim_quant/core/instruments.py",
    "sajim_quant/core/fx.py",
    "sajim_quant/analytics/metrics.py",
    "sajim_quant/adapters/base.py",
    "sajim_quant/adapters/python/backtrader_adapter.py",
    "sajim_quant/adapters/python/beep_adapter.py",
    "sajim_quant/adapters/python/smc_adapter.py",
    "sajim_quant/adapters/mql5/mt5_adapter.py",
)


def _compute_source_hash(source_path: Path, workspace: Optional[Path] = None) -> str:
    """
    Hash the strategy source together with the pricing/execution code, so a node
    is re-run when either the strategy or the way it is priced changes.
    """
    h = hashlib.sha256()
    if source_path.exists():
        h.update(source_path.read_bytes())
    else:
        h.update(b"missing-source")

    root = workspace or WORKSPACE_ROOT
    for rel in _PRICING_MODULES:
        mod = root / rel
        if mod.exists():
            h.update(mod.read_bytes())
    return h.hexdigest()[:16]


def _classify_status(sharpe: float, pf: Optional[float], dd: float, pnl: float) -> str:
    """
    Derive tier status from performance metrics.

    Drawdown is tested FIRST: a profitable strategy with a 40% drawdown is the
    exact case TOXIC_DD exists to flag, but the MARGINAL branch used to match it
    first (sharpe >= 0 and pnl >= 0), so the label was unreachable.

    `pf` is None when a run has no losing trades; it is treated as not meeting
    any profit-factor bar rather than as infinitely good.
    """
    if dd >= 25.0:
        return "TOXIC_DD"
    pf_val = pf if pf is not None else 0.0
    if sharpe >= 1.8 and pf_val >= 1.5 and dd <= 5.0 and pnl > 0:
        return "ELITE"
    if sharpe >= 1.0 and pf_val >= 1.1 and pnl > 0:
        return "VIABLE"
    if sharpe >= 0.0 and pnl >= 0:
        return "MARGINAL"
    return "DEGRADED"


# ═══════════════════════════════════════════════════════════════════════════════
#  Single-Node Runner (can be called in a process pool)
# ═══════════════════════════════════════════════════════════════════════════════

def run_single_node(
    strategy_def: Dict[str, Any],
    symbol: str,
    timeframe: str,
    workspace_root: str,
    cache_dir: str,
    start_date: str = "2024-09-01",
    end_date: str = "2026-09-01",
    initial_balance: float = 10000.0,
) -> Dict[str, Any]:
    """
    Execute a REAL backtest for one strategy × symbol × timeframe node.
    Returns a metrics dict. Uses cache if available.
    """
    import sys
    ws = Path(workspace_root)
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))

    strat_id = strategy_def["id"]
    strat_name = strategy_def["name"]
    adapter_type = strategy_def["adapter"]
    source_rel = strategy_def["source"]
    source_path = ws / source_rel

    # ── Build cache key ──
    source_hash = _compute_source_hash(source_path, ws)
    cache_key = f"{strat_id}__{symbol}__{timeframe}__{source_hash}"
    cache_path = Path(cache_dir) / f"{cache_key}.json"

    # Check cache
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except Exception:
            pass  # Re-run if cache is corrupted

    # ── Strategies without adapters ──
    if adapter_type == "pending":
        result = {
            "strategy_id": strat_id,
            "strategy_name": strat_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "PENDING_ADAPTER",
            "data_source": "none",
            "trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": None,
            "net_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "error": f"Strategy '{strat_name}' uses a custom engine without a BaseStrategyAdapter. Wire it up to enable real backtesting.",
            "from_cache": False,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # ── Check data availability ──
    has_data = _get_data_availability(symbol, timeframe)
    if not has_data:
        result = {
            "strategy_id": strat_id,
            "strategy_name": strat_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "NO_DATA",
            "data_source": "missing",
            "trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": None,
            "net_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "error": f"No Parquet data found for {symbol}/{timeframe}.",
            "from_cache": False,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # ── Run real backtest ──
    try:
        from sajim_quant.analytics.metrics import calculate_metrics

        if adapter_type == "backtrader":
            from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
            import importlib.util
            import backtrader as bt

            # Dynamically load strategy class
            spec = importlib.util.spec_from_file_location(source_path.stem, str(source_path))
            module = importlib.util.module_from_spec(spec)
            parent = str(source_path.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            spec.loader.exec_module(module)

            strat_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, bt.Strategy) and attr is not bt.Strategy:
                    strat_class = attr
                    break

            if strat_class is None:
                raise ValueError(f"No bt.Strategy subclass found in {source_path.name}")

            adapter = BacktraderAdapter(
                strategy_class=strat_class,
                strategy_name=strat_name,
            )
            backtest_result = adapter.run(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_balance=initial_balance,
            )

        elif adapter_type == "mt5":
            from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter

            adapter = MT5HeadlessAdapter(
                mq5_source_path=source_path,
                strategy_name=strat_name,
            )
            backtest_result = adapter.run(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_balance=initial_balance,
            )

        elif adapter_type == "beep_vacuum":
            from sajim_quant.adapters.python.beep_adapter import BEEPVacuumAdapter
            adapter = BEEPVacuumAdapter(strategy_name=strat_name)
            backtest_result = adapter.run(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_balance=initial_balance,
            )

        elif adapter_type == "beep_baseline":
            from sajim_quant.adapters.python.beep_adapter import BEEPBaselineAdapter
            adapter = BEEPBaselineAdapter(strategy_name=strat_name)
            backtest_result = adapter.run(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_balance=initial_balance,
            )

        elif adapter_type == "smc":
            from sajim_quant.adapters.python.smc_adapter import SMCAdapter
            adapter = SMCAdapter(strategy_name=strat_name)
            backtest_result = adapter.run(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_balance=initial_balance,
            )

        else:
            raise ValueError(f"Unknown adapter type: {adapter_type}")

        # Compute metrics
        metrics = calculate_metrics(backtest_result)

        status = _classify_status(
            sharpe=metrics.sharpe_ratio,
            pf=metrics.profit_factor,
            dd=metrics.max_drawdown_pct,
            pnl=metrics.net_profit,
        )

        # Mark optimal if default pair
        is_optimal = (
            symbol == strategy_def["default_symbol"]
            and timeframe == strategy_def["default_timeframe"]
        )
        if is_optimal and status == "ELITE":
            status = "OPTIMAL"

        def _r(v, nd=2):
            return None if v is None else round(v, nd)

        result = {
            "strategy_id": strat_id,
            "strategy_name": strat_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": status,
            "data_source": "real_backtest",
            "trades": metrics.total_trades,
            "win_rate_pct": _r(metrics.win_rate_pct, 1),
            "profit_factor": _r(metrics.profit_factor),
            "net_pnl": _r(metrics.net_profit),
            "max_drawdown_pct": _r(metrics.max_drawdown_pct),
            "max_drawdown_pct_closed": _r(metrics.max_drawdown_pct_closed),
            "drawdown_includes_intratrade": metrics.drawdown_includes_intratrade,
            "sharpe_ratio": _r(metrics.sharpe_ratio),
            "sortino_ratio": _r(metrics.sortino_ratio),
            "calmar_ratio": _r(metrics.calmar_ratio),
            "cagr_pct": _r(metrics.cagr_pct),
            "span_days": metrics.span_days,
            "expectancy": _r(metrics.expectancy),
            "avg_win": _r(metrics.avg_win),
            "avg_loss": _r(metrics.avg_loss),
            "winning_trades": metrics.winning_trades,
            "losing_trades": metrics.losing_trades,
            "assumptions": backtest_result.metadata.notes,
            "optimal": is_optimal,
            "error": None,
            "from_cache": False,
        }

    except Exception as e:
        # NOT cached: an error is usually environmental (MT5 terminal busy, a
        # locked file), and caching it meant one transient failure stuck until
        # someone remembered --clear-cache.
        logger.warning("Node %s %s %s failed: %s", strat_id, symbol, timeframe, e)
        return {
            "strategy_id": strat_id,
            "strategy_name": strat_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "ERROR",
            "data_source": "failed",
            "trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": None,
            "net_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "error": str(e),
            "from_cache": False,
            "cacheable": False,
        }

    # Cache the result
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Matrix Builder — Orchestrates all nodes
# ═══════════════════════════════════════════════════════════════════════════════

class RealMatrixBuilder:
    """
    Builds the strategy × symbol × timeframe performance matrix using
    REAL backtest execution. Caches results per-node for instant re-runs.
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        output_path: Optional[Path] = None,
        max_workers: int = 1,
        cache_dir: Optional[Path] = None,
    ):
        self.workspace = Path(workspace_root or WORKSPACE_ROOT).resolve()
        self.output_path = output_path or (self.workspace / "data" / "strategy_universe_matrix.json")
        self.max_workers = max_workers
        self.cache_dir = cache_dir or (self.workspace / "data" / ".matrix_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        strategies: Optional[List[str]] = None,
        start_date: str = "2024-09-01",
        end_date: str = "2026-09-01",
    ) -> Dict[str, Any]:
        """
        Build the full matrix. Returns the matrix dict and saves to output_path.
        """
        syms = symbols or ALL_SYMBOLS
        tfs = timeframes or ALL_TIMEFRAMES
        strats = [
            s for s in STRATEGY_REGISTRY
            if strategies is None or s["id"] in strategies
        ]

        total_nodes = len(strats) * len(syms) * len(tfs)
        print(f"\n  📐 Matrix dimensions: {len(strats)} strategies × {len(syms)} symbols × {len(tfs)} timeframes = {total_nodes} nodes")

        t0 = time.time()
        all_results: List[Dict[str, Any]] = []
        completed = 0
        cached_count = 0
        error_count = 0

        # Build node tasks
        tasks = []
        for strat in strats:
            for sym in syms:
                for tf in tfs:
                    tasks.append((strat, sym, tf))

        if self.max_workers <= 1:
            # Sequential execution (simpler, good for debugging)
            for strat, sym, tf in tasks:
                result = run_single_node(
                    strategy_def=strat,
                    symbol=sym,
                    timeframe=tf,
                    workspace_root=str(self.workspace),
                    cache_dir=str(self.cache_dir),
                    start_date=start_date,
                    end_date=end_date,
                )
                all_results.append(result)
                completed += 1
                if result.get("from_cache"):
                    cached_count += 1
                if result.get("status") == "ERROR":
                    error_count += 1

                # Progress indicator every 20 nodes
                if completed % 20 == 0 or completed == total_nodes:
                    pct = completed / total_nodes * 100
                    print(f"  [{completed}/{total_nodes}] ({pct:.0f}%) — {strat['name']} {sym} {tf}: {result['status']}")
        else:
            # Parallel execution
            with ProcessPoolExecutor(max_workers=self.max_workers) as pool:
                future_map = {}
                for strat, sym, tf in tasks:
                    fut = pool.submit(
                        run_single_node,
                        strategy_def=strat,
                        symbol=sym,
                        timeframe=tf,
                        workspace_root=str(self.workspace),
                        cache_dir=str(self.cache_dir),
                        start_date=start_date,
                        end_date=end_date,
                    )
                    future_map[fut] = (strat["name"], sym, tf)

                for fut in as_completed(future_map):
                    name, sym, tf = future_map[fut]
                    try:
                        result = fut.result()
                    except Exception as e:
                        result = {
                            "strategy_name": name,
                            "symbol": sym,
                            "timeframe": tf,
                            "status": "WORKER_ERROR",
                            "error": str(e),
                            "trades": 0,
                        }
                        error_count += 1

                    all_results.append(result)
                    completed += 1
                    if result.get("from_cache"):
                        cached_count += 1

                    if completed % 20 == 0 or completed == total_nodes:
                        pct = completed / total_nodes * 100
                        print(f"  [{completed}/{total_nodes}] ({pct:.0f}%) — {name} {sym} {tf}: {result.get('status', '?')}")

        elapsed = time.time() - t0

        # ── Build output structure ───────────────────────────────────────
        matrix_output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "build_mode": "REAL_BACKTEST",
            "total_nodes": total_nodes,
            "completed_nodes": completed,
            "cached_nodes": cached_count,
            "error_nodes": error_count,
            "elapsed_seconds": round(elapsed, 1),
            "symbols": list(syms),
            "timeframes": list(tfs),
            "strategies": [],
        }

        # Group results by strategy
        from collections import defaultdict
        strat_groups: Dict[str, List[Dict]] = defaultdict(list)
        for r in all_results:
            sid = r.get("strategy_id", r.get("strategy_name", "unknown"))
            strat_groups[sid].append(r)

        for strat_def in strats:
            sid = strat_def["id"]
            nodes = strat_groups.get(sid, [])

            # Sort by Sharpe desc, then PnL desc
            nodes.sort(key=lambda x: (x.get("sharpe_ratio", 0), x.get("net_pnl", 0)), reverse=True)

            # Add rank
            for rank_idx, node in enumerate(nodes, 1):
                node["rank"] = rank_idx

            # Compute summary stats
            real_nodes = [n for n in nodes if n.get("data_source") == "real_backtest"]
            profitable = [n for n in real_nodes if n.get("net_pnl", 0) > 0]

            peak = nodes[0] if nodes else {}
            worst = nodes[-1] if nodes else {}

            # Build heatmap
            heatmap: Dict[str, Dict[str, float]] = {}
            for sym in syms:
                heatmap[sym] = {}
                for tf in tfs:
                    match = next((n for n in nodes if n.get("symbol") == sym and n.get("timeframe") == tf), None)
                    heatmap[sym][tf] = match.get("sharpe_ratio", 0.0) if match else 0.0

            strat_summary = {
                "id": sid,
                "name": strat_def["name"],
                "adapter": strat_def["adapter"],
                "source": strat_def["source"],
                "tier": strat_def["tier"],
                "description": strat_def["description"],
                "total_combinations_tested": len(nodes),
                "real_backtest_nodes": len(real_nodes),
                "profitable_combinations": len(profitable),
                "universality_pct": round(len(profitable) / max(len(real_nodes), 1) * 100, 1),
                "peak_combination": {
                    "symbol": peak.get("symbol"),
                    "timeframe": peak.get("timeframe"),
                    "sharpe_ratio": peak.get("sharpe_ratio"),
                    "profit_factor": peak.get("profit_factor"),
                    "net_pnl": peak.get("net_pnl"),
                    "trades": peak.get("trades"),
                    "status": peak.get("status"),
                },
                "worst_combination": {
                    "symbol": worst.get("symbol"),
                    "timeframe": worst.get("timeframe"),
                    "sharpe_ratio": worst.get("sharpe_ratio"),
                    "net_pnl": worst.get("net_pnl"),
                    "status": worst.get("status"),
                },
                "heatmap": heatmap,
                "ranked_combinations": nodes,
            }
            matrix_output["strategies"].append(strat_summary)

        # ── Save output ──────────────────────────────────────────────────
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(matrix_output, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n  💾 Matrix saved: {self.output_path}")
        print(f"     Size: {self.output_path.stat().st_size:,} bytes")

        # Sync to sim directories if they exist
        for sync_dir in ["sim/data", "sim/public"]:
            target = self.workspace / sync_dir
            if target.parent.exists():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy(self.output_path, target / "strategy_universe_matrix.json")
                print(f"  🔄 Synced to {target}")

        # ── Summary ──────────────────────────────────────────────────────
        print(f"\n  ═══ MATRIX BUILD SUMMARY ═══")
        print(f"  Total nodes:     {total_nodes}")
        print(f"  Real backtests:  {sum(1 for r in all_results if r.get('data_source') == 'real_backtest')}")
        print(f"  From cache:      {cached_count}")
        print(f"  No data:         {sum(1 for r in all_results if r.get('status') == 'NO_DATA')}")
        print(f"  Pending adapter: {sum(1 for r in all_results if r.get('status') == 'PENDING_ADAPTER')}")
        print(f"  Errors:          {error_count}")
        print(f"  Elapsed:         {elapsed:.1f}s")

        return matrix_output

    def clear_cache(self, strategy_id: Optional[str] = None):
        """Clear cached results. If strategy_id is given, only clear that strategy."""
        if strategy_id:
            for f in self.cache_dir.glob(f"{strategy_id}__*"):
                f.unlink()
            print(f"  🗑 Cleared cache for {strategy_id}")
        else:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            print(f"  🗑 Cleared entire matrix cache")


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI entrypoint
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    parser = argparse.ArgumentParser(description="Build real backtest universe matrix")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Parallel workers")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols")
    parser.add_argument("--timeframes", default=None, help="Comma-separated timeframes")
    parser.add_argument("--output", "-o", default=None, help="Output path")
    parser.add_argument("--clear-cache", action="store_true", help="Clear cache before building")
    args = parser.parse_args()

    builder = RealMatrixBuilder(
        max_workers=args.workers,
        output_path=Path(args.output) if args.output else None,
    )

    if args.clear_cache:
        builder.clear_cache()

    builder.build(
        symbols=args.symbols.split(",") if args.symbols else None,
        timeframes=args.timeframes.split(",") if args.timeframes else None,
    )
