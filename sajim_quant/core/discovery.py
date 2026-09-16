"""
SAJIM Quant - Universal Strategy Discovery Scanner
Crawls workspace directories, catalogs strategy systems across runtimes
(MQL5, Backtrader, Pine Script, Async Daemons, and Trade Logs),
and compiles a unified registry and leaderboard database.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sajim_quant.core.discovery")


@dataclass
class DiscoveredStrategy:
    name: str
    folder: str
    engine_type: str
    source_file: str
    symbol_hint: str = "XAUUSD"
    timeframe_hint: str = "H1"
    has_manifest: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class StrategyDiscoveryScanner:
    """
    Crawls workspaces to discover strategy files and manifests.
    """

    EXCLUDED_DIRS = {
        ".git", ".venv", "node_modules", ".next", "__pycache__",
        "python_env", ".tmp_session_catalogs", ".tmp_session_catalogs2",
        "temp_reports", "logs", "data", "sim", "docs", "tests", "sajim_quant", "files"
    }

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def scan(self) -> List[DiscoveredStrategy]:
        discovered: List[DiscoveredStrategy] = []

        for root, dirs, files in os.walk(self.workspace_root):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS]
            curr_dir = Path(root)
            rel_folder = str(curr_dir.relative_to(self.workspace_root))

            # 1. Check for sajim_strategy.yaml manifest
            manifest_path = curr_dir / "sajim_strategy.yaml"
            if manifest_path.exists():
                # Read manifest
                try:
                    import yaml
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        strat = DiscoveredStrategy(
                            name=data.get("name", curr_dir.name),
                            folder=rel_folder,
                            engine_type=data.get("engine", "python:custom"),
                            source_file=data.get("entrypoint", str(manifest_path.name)),
                            symbol_hint=data.get("symbol", "XAUUSD"),
                            timeframe_hint=data.get("timeframe", "H1"),
                            has_manifest=True,
                            metadata=data,
                        )
                        discovered.append(strat)
                        continue
                except Exception as e:
                    logger.warning(f"Error reading manifest {manifest_path}: {e}")

            # 2. Check for MQL5 EAs (*.mq5 in MQL5/Experts or folder)
            for f in files:
                if f.endswith(".mq5") and not f.endswith("_Sajim.mq5"):
                    source_rel = str((curr_dir / f).relative_to(self.workspace_root))
                    discovered.append(DiscoveredStrategy(
                        name=Path(f).stem,
                        folder=rel_folder,
                        engine_type="mt5:headless",
                        source_file=source_rel,
                        symbol_hint="XAUUSD" if "XAU" in f or "GOLD" in f else "EURUSD",
                        timeframe_hint="M5" if "SCALP" in f else "H1",
                    ))

            # 3. Check for Backtrader strategies
            for f in files:
                if f.endswith(".py") and ("strategy" in f.lower() or "ogle" in f.lower() or "pullback" in f.lower()):
                    source_rel = str((curr_dir / f).relative_to(self.workspace_root))
                    # Quick grep for bt.Strategy
                    try:
                        content = (curr_dir / f).read_text(encoding="utf-8", errors="ignore")
                        if "bt.Strategy" in content:
                            discovered.append(DiscoveredStrategy(
                                name=Path(f).stem,
                                folder=rel_folder,
                                engine_type="python:backtrader",
                                source_file=source_rel,
                                symbol_hint="XAUUSD" if "xau" in f.lower() or "gold" in f.lower() else "EURUSD",
                                timeframe_hint="M5" if "5m" in f.lower() else "H1",
                            ))
                        elif "AssetTrader" in content or "StrategyBase" in content:
                            discovered.append(DiscoveredStrategy(
                                name=Path(f).stem,
                                folder=rel_folder,
                                engine_type="python:custom",
                                source_file=source_rel,
                                symbol_hint="XAUUSD" if "xau" in f.lower() or "gold" in f.lower() else "EURUSD",
                                timeframe_hint="H1",
                            ))
                    except Exception:
                        pass

            # 4. Check for TradingView Trade list exports
            for f in files:
                if f.endswith(".csv") and ("trade" in f.lower() or "tradingview" in f.lower()):
                    source_rel = str((curr_dir / f).relative_to(self.workspace_root))
                    try:
                        with open(curr_dir / f, "r", encoding="utf-8", errors="ignore") as csv_f:
                            header = csv_f.readline().lower()
                            if "trade #" in header and "profit" in header:
                                discovered.append(DiscoveredStrategy(
                                    name=Path(f).stem,
                                    folder=rel_folder,
                                    engine_type="pine:imported",
                                    source_file=source_rel,
                                    symbol_hint="XAUUSD",
                                    timeframe_hint="H1",
                                ))
                            elif "entry_time" in header and "pnl_usd" in header:
                                discovered.append(DiscoveredStrategy(
                                    name=Path(f).stem,
                                    folder=rel_folder,
                                    engine_type="python:breakout_system",
                                    source_file=source_rel,
                                    symbol_hint="BTCUSDT" if "btc" in f.lower() else "EURUSD",
                                    timeframe_hint="15M" if "15m" in f.lower() else "1H",
                                ))
                            elif "epoch" in header and "win" in header:
                                discovered.append(DiscoveredStrategy(
                                    name=Path(f).stem,
                                    folder=rel_folder,
                                    engine_type="deriv:binary_options",
                                    source_file=source_rel,
                                    symbol_hint="1HZ100V",
                                    timeframe_hint="5M",
                                ))
                    except Exception:
                        pass

            # 5. Check for Deriv DBot XML definitions
            for f in files:
                if f.endswith(".xml") and ("bot" in f.lower() or "digit" in f.lower() or "rsi" in f.lower()):
                    source_rel = str((curr_dir / f).relative_to(self.workspace_root))
                    discovered.append(DiscoveredStrategy(
                        name=Path(f).stem,
                        folder=rel_folder,
                        engine_type="deriv:dbot_xml",
                        source_file=source_rel,
                        symbol_hint="R_100",
                        timeframe_hint="TICK",
                    ))

        return discovered

    def save_catalog(self, output_path: Optional[Path] = None) -> Path:
        out = output_path or (self.workspace_root / "data" / "discovered_strategies.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        items = self.scan()
        data = [asdict(item) for item in items]
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info(f"Discovered {len(items)} strategies. Saved catalog to {out}")
        return out
