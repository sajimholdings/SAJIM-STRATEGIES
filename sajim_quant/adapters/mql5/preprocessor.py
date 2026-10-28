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
            else self._detect_terminal_data_path()
        )
        self.include_dir = self.mt5_data_path / "MQL5" / "Include"
        self.experts_dir = self.mt5_data_path / "MQL5" / "Experts" / "Sajim"

    @staticmethod
    def _detect_terminal_data_path() -> Path:
        """Locates the active MT5 terminal data directory.

        Order of preference:
        1. $SAJIM_MT5_DATA_DIR env override
        2. A terminal dir that already contains Experts/Sajim (previously used)
        3. The first terminal dir containing an MQL5/Experts tree
        """
        env_override = os.getenv("SAJIM_MT5_DATA_DIR")
        if env_override:
            return Path(env_override)

        terminals_root = Path(
            os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal")
        )
        if not terminals_root.exists():
            raise FileNotFoundError(
                f"No MT5 terminal data directory found under {terminals_root}. "
                "Set SAJIM_MT5_DATA_DIR to the correct path."
            )

        candidate_dirs = [d for d in terminals_root.iterdir() if d.is_dir()]
        for d in candidate_dirs:
            if (d / "MQL5" / "Experts" / "Sajim").is_dir():
                logger.info(f"Detected MT5 data dir (already instrumented): {d}")
                return d
        for d in candidate_dirs:
            if (d / "MQL5" / "Experts").is_dir():
                logger.info(f"Detected MT5 data dir: {d}")
                return d
        raise FileNotFoundError(
            f"No usable MT5 terminal data directory under {terminals_root}. "
            "Set SAJIM_MT5_DATA_DIR to the correct path."
        )

    def ensure_include_installed(self) -> Path:
        """
        Copies SajimLogger.mqh into MT5's MQL5/Include directory.

        This is the only file this preprocessor writes to the global include
        root; `SajimLogger.mqh` is our own name and collides with nothing in the
        MetaTrader standard library. An EA's bundled headers go to a namespaced
        subdirectory instead — see instrument_source().
        """
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

        # Copy the EA's own Include tree next to the instrumented source only.
        #
        # This used to also merge the tree into the terminal's global
        # MQL5/Include with dirs_exist_ok=True. That overwrites MetaTrader's own
        # standard library: GoldOrb ships Include/Math/Alglib/ (19 files) and MT5
        # ships Include/Math/Alglib/ (18), so instrumenting one EA silently
        # replaced the Alglib headers every other EA on the terminal compiles
        # against — with no backup and no way back short of reinstalling.
        for candidate in [source_mq5.parent / "Include", source_mq5.parent.parent / "Include"]:
            if candidate.exists() and candidate.is_dir():
                target_local_include = self.experts_dir / "Include"
                shutil.copytree(candidate, target_local_include, dirs_exist_ok=True)
                logger.info(f"Synchronized Include tree from {candidate} -> {target_local_include}")

                # Angle-bracket includes (<Foo.mqh>) resolve from the global
                # include root, so mirror there too — but namespaced under
                # Sajim/<EA>/ where nothing can collide with the stdlib.
                namespaced = self.include_dir / "Sajim" / source_mq5.stem
                shutil.copytree(candidate, namespaced, dirs_exist_ok=True)
                logger.info(f"Mirrored Include tree -> {namespaced} (namespaced, stdlib untouched)")
                break

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
        try:
            subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), timeout=300)
        except subprocess.TimeoutExpired:
            return False, ex5_path, "Compile timed out after 300s (MetaEditor dialog blocking?)"

        log_content = ""
        if log_path.exists():
            log_content = log_path.read_text(encoding="utf-16", errors="ignore")
            if not log_content.strip():
                log_content = log_path.read_text(encoding="utf-8", errors="ignore")

        is_success = ex5_path.exists() and "0 errors" in log_content
        return is_success, ex5_path, log_content
