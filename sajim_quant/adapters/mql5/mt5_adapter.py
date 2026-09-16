"""
SAJIM Quant - MT5 Headless Execution Adapter

Automates headless compilation and strategy testing in MetaTrader 5:
1. Syncs Dukascopy Parquet data into MT5 Custom Symbols.
2. Non-destructively instruments the target .mq5 with SajimLogger and compiles via MetaEditor.
3. Generates a .ini configuration and executes terminal64.exe headlessly.
4. Harvests self-reported trades from Terminal/Common/Files/sajim_trades.csv into the SAJIM schema.
"""

from __future__ import annotations

import configparser
import csv
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import MetaTrader5 as mt5
import pandas as pd

from sajim_quant.adapters.base import BaseStrategyAdapter
from sajim_quant.adapters.mql5.custom_symbol_sync import CustomSymbolSync
from sajim_quant.adapters.mql5.preprocessor import Mql5Preprocessor
from sajim_quant.core.schema import (
    BacktestResult,
    Direction,
    EngineType,
    ExitReason,
    RunMetadata,
    Trade,
)

logger = logging.getLogger("sajim_quant.adapters.mql5.headless")


class MT5HeadlessAdapter(BaseStrategyAdapter):
    """
    Automated headless runner for MQL5 Expert Advisors.
    """

    PERIOD_MAP = {
        "M1": "M1",
        "M5": "M5",
        "M15": "M15",
        "M30": "M30",
        "H1": "H1",
        "H4": "H4",
        "D1": "Daily",
    }

    def __init__(
        self,
        mq5_source_path: Path,
        strategy_name: Optional[str] = None,
        terminal_exe_path: Optional[Path] = None,
        common_files_path: Optional[Path] = None,
    ):
        self.mq5_path = Path(mq5_source_path).resolve()
        strat_name = strategy_name or self.mq5_path.stem
        super().__init__(strategy_name=strat_name, engine_name=EngineType.MT5_HEADLESS.value)

        self.terminal_exe = (
            Path(terminal_exe_path)
            if terminal_exe_path
            else Path(r"C:\Program Files\MetaTrader 5\terminal64.exe")
        )
        self.common_files = (
            Path(common_files_path)
            if common_files_path
            else Path(os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal\Common\Files"))
        )
        self.account_login = 112597392
        self.account_server = "MetaQuotes-Demo"
        self.preprocessor = Mql5Preprocessor()
        self.syncer = CustomSymbolSync()

    def _generate_ini(
        self,
        ini_path: Path,
        ex5_relative_path: str,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        deposit: float = 50000.0,
        leverage: int = 100,
        model: int = 1,  # 1 = 1 minute OHLC, 0 = Every tick
    ) -> None:
        """
        Creates the .ini configuration file passed to terminal64.exe /config:...
        """
        # Format dates as YYYY.MM.DD
        t_start = pd.to_datetime(start_date).strftime("%Y.%m.%d")
        t_end = pd.to_datetime(end_date).strftime("%Y.%m.%d")
        tf_str = self.PERIOD_MAP.get(timeframe.upper(), "H1")

        content = f"""[Common]
Login=112701746
Password=MyAiL-O6
Server={self.account_server}
ProxyEnable=0

[Tester]
Expert={ex5_relative_path}
Symbol={symbol}
Period={tf_str}
Deposit={int(deposit)}
Currency=USD
Leverage={leverage}
Model={model}
ExecutionMode=0
Optimization=0
FromDate={t_start}
ToDate={t_end}
ForwardMode=0
Report=sajim_report
ReplaceReport=1
ShutdownTerminal=1
Visual=0
"""
        ini_path.write_text(content, encoding="utf-8")
        logger.info(f"Generated tester INI config -> {ini_path}")

    def run(
        self,
        symbol: str,
        timeframe: str = "H1",
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        initial_balance: float = 50000.0,
        use_custom_symbol: bool = False,
        sync_data: bool = False,
        timeout_seconds: int = 300,
        **strategy_params: Any,
    ) -> BacktestResult:
        """
        Full headless test workflow:
        1. Sync data to custom symbol if requested.
        2. Instrument .mq5 and compile to .ex5.
        3. Launch headless MT5 Strategy Tester.
        4. Harvest output trades from Common/Files/sajim_trades.csv.
        """
        target_symbol = f"{symbol}_SAJIM" if use_custom_symbol else symbol

        # 1. Sync custom symbol if requested
        if use_custom_symbol and sync_data:
            logger.info(f"Ensuring custom symbol {target_symbol} has latest Parquet data...")
            self.syncer.sync_symbol(symbol=symbol, custom_name=target_symbol, timeframe="H1")

        # 2. Instrument source & compile
        logger.info(f"Instrumenting source {self.mq5_path.name}...")
        instrumented_mq5 = self.preprocessor.instrument_source(self.mq5_path, self.strategy_name)
        success, ex5_path, compile_log = self.preprocessor.compile(instrumented_mq5)

        if not success:
            raise RuntimeError(f"Compilation failed for {instrumented_mq5.name}!\nLog:\n{compile_log}")

        logger.info(f"Compilation successful -> {ex5_path.name}")

        # Relative expert path for MT5 Tester: resolves to Experts\Sajim\EA_Name.ex5
        rel_expert = f"Sajim\\{ex5_path.name}"

        # 3. Clean previous output trade log in Common/Files
        trades_csv = self.common_files / "sajim_trades.csv"
        if trades_csv.exists():
            try:
                trades_csv.unlink()
            except Exception as e:
                logger.warning(f"Could not remove old trades CSV: {e}")

        # 4. Generate INI config inside MT5 Terminal data directory
        ini_path = self.preprocessor.mt5_data_path / "tester_run.ini"

        s_date = str(start_date) if start_date else "2024-09-01"
        e_date = str(end_date) if end_date else "2026-09-01"

        self._generate_ini(
            ini_path=ini_path,
            ex5_relative_path=rel_expert,
            symbol=target_symbol,
            timeframe=timeframe,
            start_date=s_date,
            end_date=e_date,
            deposit=initial_balance,
        )

        # 5. Launch Headless Terminal via PowerShell to properly wait for the MT5 Strategy Tester process tree
        subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force"], check=False)
        time.sleep(1.0)

        ps_cmd = f'Start-Process "{str(self.terminal_exe)}" -ArgumentList "/config:{str(ini_path)}" -Wait -NoNewWindow'
        logger.info(f"Launching headless MT5 Strategy Tester: {ps_cmd}")

        t0 = time.time()
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=timeout_seconds)

        elapsed = time.time() - t0
        logger.info(f"MT5 execution finished in {elapsed:.1f}s.")
        time.sleep(1.5)

        # 6. Read emitted trades
        trades: List[Trade] = []
        if trades_csv.exists():
            with open(trades_csv, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trades.append(Trade(
                        trade_id=row.get("trade_id", ""),
                        strategy_name=self.strategy_name,
                        engine=self.engine_name,
                        symbol=row.get("symbol", target_symbol),
                        timeframe=row.get("timeframe", timeframe),
                        direction=Direction(row.get("direction", "BUY")),
                        entry_time=row.get("entry_time", ""),
                        entry_price=float(row.get("entry_price", 0.0)),
                        position_size=float(row.get("position_size", 1.0)),
                        exit_time=row.get("exit_time", ""),
                        exit_price=float(row.get("exit_price", 0.0)),
                        exit_reason=ExitReason(row.get("exit_reason", "UNKNOWN")),
                        gross_pnl=float(row.get("gross_pnl", 0.0)),
                        net_pnl=float(row.get("net_pnl", 0.0)),
                        commission=float(row.get("commission", 0.0)),
                        swap_cost=float(row.get("swap_cost", 0.0)),
                        duration_seconds=float(row.get("duration_seconds", 0.0)) if row.get("duration_seconds") else None,
                    ))
            logger.info(f"Successfully harvested {len(trades)} trades from MT5 run.")
        else:
            logger.warning(f"No trades CSV was generated at {trades_csv}. Strategy may have generated 0 trades.")

        # 7. Construct metadata and return
        meta = RunMetadata(
            strategy_name=self.strategy_name,
            engine=self.engine_name,
            symbol=target_symbol,
            timeframe=timeframe,
            start_date=s_date,
            end_date=e_date,
            initial_balance=initial_balance,
            spread_model="custom_points",
            notes=f"Headless MT5 backtest ({elapsed:.1f}s)",
        )

        return BacktestResult(metadata=meta, trades=trades)
