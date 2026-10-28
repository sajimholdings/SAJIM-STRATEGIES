"""
SAJIM Quant - Universal CSV & Trade Log Ingestion Adapter
Normalizes external backtest trade CSVs and live trading logs
(from BTC data collector, Deriv bots, etc.) into the frozen SAJIM Universal Trade Schema.
"""

from __future__ import annotations

import csv
import logging
import re
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

logger = logging.getLogger("sajim_quant.adapters.importer")


class UniversalLogImporter:
    """
    Imports trade data from external system logs and CSV formats into SAJIM Universal Schema.
    """

    @staticmethod
    def import_btc_breakout_csv(
        csv_path: Union[str, Path],
        strategy_name: str = "BTC_Volatility_Breakout",
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> BacktestResult:
        """
        Parses trade logs from 'BTC data collector' folder:
        entry_time,exit_time,direction,entry_price,exit_price,sl_price,tp_price,size_usd,pnl_usd,pnl_r,exit_reason,bars_held
        """
        file_path = Path(csv_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Infer symbol and timeframe from filename if not provided (e.g. BTCUSDT_15M_trades.csv)
        stem = file_path.stem.replace("_trades", "")
        parts = stem.split("_")
        detected_sym = symbol or (parts[0] if len(parts) >= 1 else "BTCUSDT")
        detected_tf = timeframe or (parts[1] if len(parts) >= 2 else "15M")

        df = pd.read_csv(file_path)
        trades: List[Trade] = []

        for row in df.itertuples(index=True):
            entry_t = pd.to_datetime(row.entry_time)
            if entry_t.tzinfo is None:
                entry_t = entry_t.replace(tzinfo=timezone.utc)

            exit_t = pd.to_datetime(row.exit_time)
            if exit_t.tzinfo is None:
                exit_t = exit_t.replace(tzinfo=timezone.utc)

            dir_str = str(row.direction).strip().upper()
            direction = Direction.BUY if dir_str in ("LONG", "BUY") else Direction.SELL

            # Map exit reason
            reason_raw = str(getattr(row, 'exit_reason', '')).strip().upper()
            if "TP" in reason_raw:
                exit_reason = ExitReason.TAKE_PROFIT
            elif "SL" in reason_raw:
                exit_reason = ExitReason.STOP_LOSS
            elif "TIMEOUT" in reason_raw or "TIME" in reason_raw:
                exit_reason = ExitReason.TIMEOUT
            else:
                exit_reason = ExitReason.MANUAL

            pnl = float(row.pnl_usd)
            size = float(getattr(row, 'size_usd', 1000.0))
            entry_p = float(row.entry_price)
            exit_p = float(row.exit_price)

            trade = Trade(
                trade_id=f"BTC_{row.Index+1}",
                strategy_name=strategy_name,
                engine="python:breakout_system",
                symbol=detected_sym,
                timeframe=detected_tf,
                direction=direction,
                entry_time=entry_t.isoformat(),
                entry_price=entry_p,
                position_size=size,
                exit_time=exit_t.isoformat(),
                exit_price=exit_p,
                exit_reason=exit_reason,
                gross_pnl=pnl,
                net_pnl=pnl,
                duration_seconds=(exit_t - entry_t).total_seconds(),
                custom_fields={
                    "sl_price": float(getattr(row, 'sl_price', 0.0)),
                    "tp_price": float(getattr(row, 'tp_price', 0.0)),
                    "pnl_r": float(getattr(row, 'pnl_r', 0.0)),
                    "bars_held": int(getattr(row, 'bars_held', 0)),
                }
            )
            trades.append(trade)

        start_dt = trades[0].entry_time if trades else ""
        end_dt = trades[-1].exit_time if trades else ""

        meta = RunMetadata(
            strategy_name=strategy_name,
            engine="python:breakout_system",
            symbol=detected_sym,
            timeframe=detected_tf,
            start_date=start_dt,
            end_date=end_dt,
            initial_balance=50000.0,
            commission_model="zero",
            slippage_pips=0.0,
            notes=f"Ingested from {file_path.name}",
        )

        return BacktestResult(metadata=meta, trades=trades)

    @staticmethod
    def import_deriv_log(
        log_path: Union[str, Path],
        strategy_name: str = "Deriv_V3_Momentum",
    ) -> BacktestResult:
        """
        Parses live execution logs from 'Deriv trading' folder:
        e.g. trades_v3_fully_tuned.log
        """
        file_path = Path(log_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        trades: List[Trade] = []
        open_orders: Dict[str, Dict[str, Any]] = {}

        # Regex patterns
        entry_pat = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+\|\s+Asset:\s+([A-Z0-9]+)\s+\|\s+Entry:\s+([\d\.]+)\s+\|\s+OrderID:\s+(\d+)")
        close_pat = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+\|\s+\[CLOSED\]\s+Asset:\s+([A-Z0-9]+)\s+\|\s+Entry\s+->\s+Exit\s+\|\s+PnL:\s+\$([-+]?[\d\.]+)")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                m_entry = entry_pat.match(line)
                if m_entry:
                    t_str, asset, entry_p, order_id = m_entry.groups()
                    open_orders[asset] = {
                        "entry_time": t_str,
                        "entry_price": float(entry_p),
                        "order_id": order_id,
                    }
                    continue

                m_close = close_pat.match(line)
                if m_close:
                    t_close, asset, pnl_str = m_close.groups()
                    pnl = float(pnl_str)
                    entry_info = open_orders.pop(asset, None)

                    if entry_info:
                        entry_t = pd.to_datetime(entry_info["entry_time"])
                        exit_t = pd.to_datetime(t_close)
                        dur = (exit_t - entry_t).total_seconds()
                        trade = Trade(
                            trade_id=f"DERIV_{entry_info['order_id'][-6:]}",
                            strategy_name=strategy_name,
                            engine="deriv:websocket_bot",
                            symbol=asset,
                            timeframe="TICK",
                            direction=Direction.BUY,
                            entry_time=entry_info["entry_time"],
                            entry_price=entry_info["entry_price"],
                            position_size=1.0,  # Deriv contract stake
                            exit_time=t_close,
                            # Binary options settle at the entry price: the PnL is a
                            # contract payout, not a price move, so do not fabricate
                            # an exit price from the PnL.
                            exit_price=entry_info["entry_price"],
                            exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
                            gross_pnl=pnl,
                            net_pnl=pnl,
                            duration_seconds=dur,
                            custom_fields={"order_id": entry_info["order_id"]}
                        )
                        trades.append(trade)

        start_dt = trades[0].entry_time if trades else ""
        end_dt = trades[-1].exit_time if trades else ""

        meta = RunMetadata(
            strategy_name=strategy_name,
            engine="deriv:websocket_bot",
            symbol="SYNTHETIC_BASKET",
            timeframe="TICK",
            start_date=start_dt,
            end_date=end_dt,
            initial_balance=1000.0,
            commission_model="zero",
            slippage_pips=0.0,
            notes=f"Ingested from {file_path.name}",
        )

        return BacktestResult(metadata=meta, trades=trades)

    @staticmethod
    def import_deriv_updown_csv(
        csv_path: Union[str, Path],
        strategy_name: str = "Deriv_UpDown_Momentum",
        symbol: str = "1HZ100V",
        timeframe: str = "5M",
        ahead_filter: Optional[int] = 1,
        stake: float = 10.0,
        payout_rate: float = 0.95,
    ) -> BacktestResult:
        """
        Parses Deriv Up/Down (Rise/Fall) binary option backtest results:
        epoch,date,type,entry,exitPrice,win,ema10,ema30,rsi,ahead
        """
        file_path = Path(csv_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        df = pd.read_csv(file_path)
        if ahead_filter is not None and "ahead" in df.columns:
            df = df[df["ahead"] == ahead_filter].copy()

        trades: List[Trade] = []

        for row in df.itertuples(index=True):
            entry_epoch = int(row.epoch)
            ahead_val = int(getattr(row, 'ahead', 1))
            # 5m candles = 300s per candle
            dur = ahead_val * 300
            exit_epoch = entry_epoch + dur

            entry_t = datetime.fromtimestamp(entry_epoch, tz=timezone.utc).isoformat()
            exit_t = datetime.fromtimestamp(exit_epoch, tz=timezone.utc).isoformat()

            call_put = str(row.type).strip().upper()
            direction = Direction.BUY if call_put == "CALL" else Direction.SELL
            is_win = int(row.win) == 1

            pnl = (stake * payout_rate) if is_win else -stake

            trade = Trade(
                trade_id=f"DERIV_UD_{row.Index+1:05d}",
                strategy_name=strategy_name,
                engine="deriv:binary_options",
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                entry_time=entry_t,
                entry_price=float(row.entry),
                position_size=stake,
                exit_time=exit_t,
                exit_price=float(row.exitPrice),
                exit_reason=ExitReason.TAKE_PROFIT if is_win else ExitReason.STOP_LOSS,
                gross_pnl=pnl,
                net_pnl=pnl,
                duration_seconds=float(dur),
                custom_fields={
                    "ema10": float(getattr(row, 'ema10', 0.0)),
                    "ema30": float(getattr(row, 'ema30', 0.0)),
                    "rsi": float(getattr(row, 'rsi', 0.0)),
                    "ahead": ahead_val,
                },
            )
            trades.append(trade)

        start_dt = trades[0].entry_time if trades else ""
        end_dt = trades[-1].exit_time if trades else ""

        meta = RunMetadata(
            strategy_name=strategy_name,
            engine="deriv:binary_options",
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_dt,
            end_date=end_dt,
            initial_balance=10000.0,
            commission_model="binary_payout",
            slippage_pips=0.0,
            notes=f"Ingested from {file_path.name} (ahead={ahead_filter})",
        )

        return BacktestResult(metadata=meta, trades=trades)
