"""
SAJIM Quant - TradingView / Pine Script Trade Importer
Parses native TradingView Strategy Tester "List of Trades" CSV exports
and maps them into the frozen SAJIM Universal Trade Schema.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from sajim_quant.core.schema import (
    BacktestResult,
    Direction,
    EngineType,
    ExitReason,
    RunMetadata,
    Trade,
)

logger = logging.getLogger("sajim_quant.adapters.pine")


class TradingViewTradeImporter:
    """
    Imports and normalizes exported TradingView strategy trade list CSVs.
    """

    @staticmethod
    def import_trades_csv(
        csv_path: Union[str, Path],
        strategy_name: str = "TradingView_Pine_Strategy",
        symbol: str = "XAUUSD",
        timeframe: str = "H1",
        initial_balance: float = 50000.0,
    ) -> BacktestResult:
        file_path = Path(csv_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"TradingView trade list not found: {file_path}")

        df = pd.read_csv(file_path)
        
        # Clean column names (strip whitespace, lowercase, remove currency symbols)
        clean_cols = {}
        for c in df.columns:
            cleaned = c.strip().lower().replace(" ", "_")
            cleaned = cleaned.replace("_usd", "").replace("_eur", "")
            clean_cols[c] = cleaned
        df = df.rename(columns=clean_cols)

        # Expected columns in TV trade export:
        # trade_#, type, signal, date/time, price, contracts, profit, profit_%, cum._profit, run-up, drawdown
        trades: List[Trade] = []
        
        # Group by trade number if trades have paired Entry / Exit rows
        # Or parse consecutive entry/exit rows
        trade_id_col = "trade_#" if "trade_#" in df.columns else "trade"
        type_col = "type" if "type" in df.columns else None
        time_col = "date/time" if "date/time" in df.columns else "datetime"
        if time_col not in df.columns:
            time_col = [c for c in df.columns if "date" in c or "time" in c][0]

        price_col = "price"
        profit_col = "profit"
        contracts_col = "contracts" if "contracts" in df.columns else "size"

        if trade_id_col in df.columns and type_col in df.columns:
            # Group by trade_#
            for trade_num, group in df.groupby(trade_id_col, sort=False):
                entry_row = None
                exit_row = None

                for _, r in group.iterrows():
                    t_type = str(r[type_col]).lower()
                    if "entry" in t_type:
                        entry_row = r
                    elif "exit" in t_type:
                        exit_row = r

                if entry_row is not None and exit_row is not None:
                    # Direction
                    is_buy = "long" in str(entry_row[type_col]).lower()
                    direction = Direction.BUY if is_buy else Direction.SELL

                    # Timestamps
                    t0 = pd.to_datetime(entry_row[time_col])
                    if t0.tzinfo is None:
                        t0 = t0.replace(tzinfo=timezone.utc)
                    t1 = pd.to_datetime(exit_row[time_col])
                    if t1.tzinfo is None:
                        t1 = t1.replace(tzinfo=timezone.utc)

                    entry_p = float(entry_row[price_col])
                    exit_p = float(exit_row[price_col])
                    size = float(entry_row[contracts_col]) if contracts_col in entry_row else 1.0
                    pnl = float(exit_row[profit_col]) if profit_col in exit_row else 0.0

                    # Optional MAE / MFE
                    mae = None
                    mfe = None
                    for c in exit_row.index:
                        if "drawdown" in c and "%" not in c:
                            try: mae = abs(float(exit_row[c]))
                            except Exception: pass
                        elif "run-up" in c and "%" not in c:
                            try: mfe = abs(float(exit_row[c]))
                            except Exception: pass

                    # Determine exit reason from signal text
                    sig = str(exit_row.get("signal", "")).upper()
                    if "TP" in sig or "TAKE" in sig:
                        reason = ExitReason.TAKE_PROFIT
                    elif "SL" in sig or "STOP" in sig:
                        reason = ExitReason.STOP_LOSS
                    elif "TRAILING" in sig:
                        reason = ExitReason.TRAILING_STOP
                    else:
                        reason = ExitReason.SIGNAL

                    trade = Trade(
                        trade_id=f"PINE_{trade_num}",
                        strategy_name=strategy_name,
                        engine=EngineType.PINE_IMPORTED.value,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        entry_time=t0.isoformat(),
                        entry_price=entry_p,
                        position_size=size,
                        exit_time=t1.isoformat(),
                        exit_price=exit_p,
                        exit_reason=reason,
                        gross_pnl=pnl,
                        net_pnl=pnl,
                        mae=mae,
                        mfe=mfe,
                        duration_seconds=(t1 - t0).total_seconds(),
                        custom_fields={"pine_signal": sig}
                    )
                    trades.append(trade)

        start_dt = trades[0].entry_time if trades else ""
        end_dt = trades[-1].exit_time if trades else ""

        meta = RunMetadata(
            strategy_name=strategy_name,
            engine=EngineType.PINE_IMPORTED.value,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_dt,
            end_date=end_dt,
            initial_balance=initial_balance,
            commission_model="tradingview",
            slippage_pips=0.0,
            notes=f"Imported from TradingView List of Trades: {file_path.name}",
        )

        return BacktestResult(metadata=meta, trades=trades)
