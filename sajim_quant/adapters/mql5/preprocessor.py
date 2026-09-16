"""
SAJIM Quant - MQL5 Compile-Time Injection Preprocessor

Enables true "zero manual intervention" automated testing:
Takes an unmodified `.mq5` file, non-destructively injects the `SajimLogger.mqh`
harvesting hook, and invokes `metaeditor64.exe` to compile a schema-reporting `.ex5`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("sajim_quant.adapters.mql5.preprocessor")


class Mql5Preprocessor:
    """
    Handles non-destructive injection of SajimLogger into any MQL5 expert.
    """

    def __init__(
        self,
        metaeditor_path: Optional[Path] = None,
        mt5_terminal_data_path: Optional[Path] = None,
    ):
        self.metaeditor_path = (
            Path(metaeditor_path)
            if metaeditor_path
            else Path(r"C:\Program Files\MetaTrader 5\metaeditor64.exe")
        )
        self.mt5_data_path = (
            Path(mt5_terminal_data_path)
            if mt5_terminal_data_path
            else Path(os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"))
        )
        self.include_dir = self.mt5_data_path / "MQL5" / "Include"
        self.experts_dir = self.mt5_data_path / "MQL5" / "Experts" / "Sajim"

    def ensure_include_installed(self) -> Path:
        """Copies SajimLogger.mqh into MT5's MQL5/Include directory."""
        source_header = Path(__file__).resolve().parent / "SajimLogger.mqh"
        if not source_header.exists():
            raise FileNotFoundError(f"Source header not found at {source_header}")

        self.include_dir.mkdir(parents=True, exist_ok=True)
        target_header = self.include_dir / "SajimLogger.mqh"
        shutil.copy2(source_header, target_header)
        logger.info(f"Installed SajimLogger header -> {target_header}")
        return target_header

    def instrument_source(self, source_mq5: Path, strategy_name: str) -> Path:
        """
        Creates an instrumented copy of the .mq5 file in MT5's Experts/Sajim directory.
        Original source file is never modified.
        """
        if not source_mq5.exists():
            raise FileNotFoundError(f"Source .mq5 not found: {source_mq5}")

        self.ensure_include_installed()
        self.experts_dir.mkdir(parents=True, exist_ok=True)

        # Copy local Include directory if present in the EA's source tree
        for candidate in [source_mq5.parent / "Include", source_mq5.parent.parent / "Include"]:
            if candidate.exists() and candidate.is_dir():
                # Copy into MT5 global MQL5/Include for <Include/...> paths
                for item in candidate.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, self.include_dir / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, self.include_dir / item.name)
                # Also copy into Experts/Sajim/Include for "Include/..." paths
                target_local_include = self.experts_dir / "Include"
                shutil.copytree(candidate, target_local_include, dirs_exist_ok=True)
                logger.info(f"Synchronized Include tree from {candidate} -> {self.include_dir} and {target_local_include}")

        content = source_mq5.read_text(encoding="utf-8", errors="ignore")

        # Check if already instrumented
        if "SajimHarvestTrades" in content:
            instrumented_path = self.experts_dir / f"{source_mq5.stem}_Sajim.mq5"
            instrumented_path.write_text(content, encoding="utf-8")
            return instrumented_path

        # Construct instrumented code
        injection = f"""

// ====================================================================
// SAJIM BENCHMARK HARVESTER INJECTION (AUTOMATED)
// ====================================================================
#include <SajimLogger.mqh>

double OnTester()
{{
    SajimHarvestTrades("{strategy_name}", _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
    return 0.0;
}}
"""
        # If OnTester already exists in source, rename it or hook into it
        if "double OnTester(" in content:
            content = content.replace("double OnTester(", "double _Original_OnTester(")
            injection = f"""

// ====================================================================
// SAJIM BENCHMARK HARVESTER INJECTION (AUTOMATED)
// ====================================================================
#include <SajimLogger.mqh>

double OnTester()
{{
    double res = _Original_OnTester();
    SajimHarvestTrades("{strategy_name}", _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
    return res;
}}
"""

        instrumented_content = content + injection
        instrumented_path = self.experts_dir / f"{source_mq5.stem}_Sajim.mq5"
        instrumented_path.write_text(instrumented_content, encoding="utf-8")
        logger.info(f"Generated instrumented source -> {instrumented_path}")
        return instrumented_path

    def compile(self, mq5_path: Path) -> Tuple[bool, Path, str]:
        """
        Invokes metaeditor64.exe to compile the .mq5 into .ex5.
        Returns: (success, ex5_path, log_content)
        """
        if not self.metaeditor_path.exists():
            raise FileNotFoundError(f"MetaEditor executable not found: {self.metaeditor_path}")

        log_path = mq5_path.parent / f"{mq5_path.stem}.log"
        ex5_path = mq5_path.with_suffix(".ex5")

        cmd = [
            str(self.metaeditor_path),
            "/portable",
        ]
        
        cwd = self.metaeditor_path.parent
        try:
            rel_mq5 = mq5_path.relative_to(cwd)
            rel_log = log_path.relative_to(cwd)
            cmd.extend([f"/compile:{str(rel_mq5)}", f"/log:{str(rel_log)}"])
        except ValueError:
            # Fallback if not inside the same directory tree
            cmd.extend([f"/compile:{str(mq5_path)}", f"/log:{str(log_path)}"])

        logger.info(f"Compiling: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd))

        log_content = ""
        if log_path.exists():
            log_content = log_path.read_text(encoding="utf-16", errors="ignore")
            if not log_content.strip():
                log_content = log_path.read_text(encoding="utf-8", errors="ignore")

        is_success = ex5_path.exists() and "0 errors" in log_content
        return is_success, ex5_path, log_content
