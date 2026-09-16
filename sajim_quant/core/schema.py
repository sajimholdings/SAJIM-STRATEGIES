"""
SAJIM Quant - Universal Trade Schema & Run Contract

This module defines the frozen output contract that every strategy execution adapter
(Python, MQL5 Headless, or Pine Script) must satisfy.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExitReason(str, Enum):
    TAKE_PROFIT = "TP"
    STOP_LOSS = "SL"
    SIGNAL = "SIGNAL"
    TIMEOUT = "TIMEOUT"
    STOP_OUT = "STOP_OUT"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


class EngineType(str, Enum):
    PYTHON_BACKTRADER = "python:backtrader"
    PYTHON_VECTORBT = "python:vectorbt"
    PYTHON_CUSTOM = "python:custom"
    MT5_HEADLESS = "mt5:headless"
    PINE_IMPORTED = "pine:imported"


def _format_datetime(val: Union[datetime, str, None]) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return str(val)


@dataclass
class Trade:
    """
    Normalized single trade record.
    All financial metrics are denominated in the account quote currency (default USD).
    """
    trade_id: str
    strategy_name: str
    engine: str
    symbol: str
    timeframe: str
    direction: Direction
    entry_time: str
    entry_price: float
    position_size: float
    exit_time: str
    exit_price: float
    exit_reason: ExitReason
    gross_pnl: float
    net_pnl: float
    commission: float = 0.0
    swap_cost: float = 0.0
    return_pct: float = 0.0
    mae: Optional[float] = None          # Maximum Adverse Excursion (peak unrealized drawdown)
    mfe: Optional[float] = None          # Maximum Favorable Excursion (peak unrealized profit)
    duration_seconds: Optional[float] = None
    custom_fields: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.trade_id:
            self.trade_id = str(uuid.uuid4())[:8]
        if isinstance(self.direction, str) and not isinstance(self.direction, Direction):
            self.direction = Direction(self.direction.upper())
        if isinstance(self.exit_reason, str) and not isinstance(self.exit_reason, ExitReason):
            try:
                self.exit_reason = ExitReason(self.exit_reason)
            except ValueError:
                self.exit_reason = ExitReason.UNKNOWN
        
        # Calculate duration if not provided
        if self.duration_seconds is None and self.entry_time and self.exit_time:
            try:
                t0 = datetime.fromisoformat(self.entry_time.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(self.exit_time.replace("Z", "+00:00"))
                self.duration_seconds = max(0.0, (t1 - t0).total_seconds())
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["exit_reason"] = self.exit_reason.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Trade:
        data_clean = dict(data)
        if "direction" in data_clean:
            data_clean["direction"] = Direction(str(data_clean["direction"]).upper())
        if "exit_reason" in data_clean:
            try:
                data_clean["exit_reason"] = ExitReason(str(data_clean["exit_reason"]))
            except ValueError:
                data_clean["exit_reason"] = ExitReason.UNKNOWN
        return cls(**data_clean)


@dataclass
class RunMetadata:
    """
    Execution context and assumption tags for a backtest run.
    """
    strategy_name: str
    engine: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float = 10000.0
    currency: str = "USD"
    spread_model: str = "floating"      # "floating", "constant", "zero"
    spread_pips: Optional[float] = None
    commission_model: str = "per_lot"   # "per_lot", "percentage", "zero"
    commission_rate: Optional[float] = None
    slippage_pips: float = 0.0
    execution_latency_ms: float = 0.0
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RunMetadata:
        return cls(**data)


@dataclass
class BacktestResult:
    """
    Full container combining run assumptions and normalized trades.
    """
    metadata: RunMetadata
    trades: List[Trade] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def net_profit(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "trades": [t.to_dict() for t in self.trades],
        }

    def save_json(self, path: Union[str, Path]) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_json(cls, path: Union[str, Path]) -> BacktestResult:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta = RunMetadata.from_dict(data["metadata"])
        trades = [Trade.from_dict(t) for t in data.get("trades", [])]
        return cls(metadata=meta, trades=trades)

    def save_trades_csv(self, path: Union[str, Path]) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not self.trades:
            with open(p, "w", newline="", encoding="utf-8") as f:
                f.write("trade_id,strategy_name,engine,symbol,timeframe,direction,entry_time,entry_price,position_size,exit_time,exit_price,exit_reason,gross_pnl,net_pnl,commission,swap_cost,return_pct,mae,mfe,duration_seconds\n")
            return

        fieldnames = [
            "trade_id", "strategy_name", "engine", "symbol", "timeframe",
            "direction", "entry_time", "entry_price", "position_size",
            "exit_time", "exit_price", "exit_reason", "gross_pnl", "net_pnl",
            "commission", "swap_cost", "return_pct", "mae", "mfe", "duration_seconds"
        ]
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for t in self.trades:
                writer.writerow(t.to_dict())

    @classmethod
    def load_trades_csv(cls, path: Union[str, Path], metadata: RunMetadata) -> BacktestResult:
        trades: List[Trade] = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(Trade(
                    trade_id=row.get("trade_id", ""),
                    strategy_name=row.get("strategy_name", metadata.strategy_name),
                    engine=row.get("engine", metadata.engine),
                    symbol=row.get("symbol", metadata.symbol),
                    timeframe=row.get("timeframe", metadata.timeframe),
                    direction=Direction(row["direction"]),
                    entry_time=row["entry_time"],
                    entry_price=float(row["entry_price"]),
                    position_size=float(row["position_size"]),
                    exit_time=row["exit_time"],
                    exit_price=float(row["exit_price"]),
                    exit_reason=ExitReason(row.get("exit_reason", "UNKNOWN")),
                    gross_pnl=float(row.get("gross_pnl", 0.0)),
                    net_pnl=float(row["net_pnl"]),
                    commission=float(row.get("commission", 0.0)),
                    swap_cost=float(row.get("swap_cost", 0.0)),
                    return_pct=float(row.get("return_pct", 0.0)),
                    mae=float(row["mae"]) if row.get("mae") else None,
                    mfe=float(row["mfe"]) if row.get("mfe") else None,
                    duration_seconds=float(row["duration_seconds"]) if row.get("duration_seconds") else None,
                ))
        return cls(metadata=metadata, trades=trades)
