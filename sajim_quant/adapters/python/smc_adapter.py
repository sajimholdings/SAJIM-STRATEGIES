"""
SAJIM Quant — SMC Fractal Daemon Adapter

Drives the `smc_daemon_project` state machine over historical bars and emits
normalized trades.

Execution model
---------------
The previous revision booked every loss at exactly `-(balance * 0.02)` and every
win at `+(balance * 0.02) * 2` — annotated "Roughly 1:2" — so the reported profit
factor and Sharpe measured the strategy's hit rate against an assumed payoff and
nothing else. A trade that reached its target by a hair and one that overshot it
by ten recorded identical profit, and `position_size` was hardcoded to 0.01 lots
regardless of either. It also loaded M5 data unconditionally while accepting a
`timeframe` argument, so an H1 node and an M5 node were the same backtest with
different labels.

Both are fixed here: positions are sized fixed-fractionally off the real stop
distance, PnL is the actual price move through `core.instruments` contract
mechanics less a round-trip cost, and the requested timeframe is the one that
gets loaded (with the daemon's higher-timeframe bias series resampled from it).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, List, Optional, Tuple, Union

import pandas as pd

from sajim_quant.adapters.base import BaseStrategyAdapter
from sajim_quant.core.fx import quote_rate_series
from sajim_quant.core.instruments import cost_assumptions, get_spec
from sajim_quant.core.schema import (
    BacktestResult,
    Direction,
    ExitReason,
    RunMetadata,
    Trade,
)
from sajim_quant.data.loader import DataLoader

logger = logging.getLogger("sajim_quant.adapters.python.smc")

#: The daemon reads a lower timeframe for structure and a higher one for bias.
#: Each entry maps the requested (execution) timeframe to the pandas resample
#: rule used to build its bias series — roughly a 12x step up, matching the
#: M5/H1 pairing the daemon was written against.
HTF_RULE = {
    "M1": "15min",
    "M5": "1h",
    "M15": "4h",
    "M30": "4h",
    "H1": "1D",
    "H4": "1W",
}

RUIN_FLOOR_PCT = 0.10


class SMCAdapter(BaseStrategyAdapter):
    """Replays the SMC daemon's async state machine against cached bars."""

    def __init__(self, strategy_name: str):
        super().__init__(strategy_name=strategy_name, engine_name="python:custom")

    def run(
        self,
        symbol: str,
        timeframe: str = "M5",
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        initial_balance: float = 10000.0,
        risk_pct: float = 0.02,
        step_bars: int = 5,
        max_hold_bars: int = 100,
        **strategy_params: Any,
    ) -> BacktestResult:
        tf = (timeframe or "M5").upper()
        if tf not in HTF_RULE:
            raise ValueError(
                f"SMC adapter cannot derive a bias timeframe for '{tf}'. Supported: {sorted(HTF_RULE)}"
            )

        df_ltf = DataLoader().load(symbol, tf, start_date, end_date)
        if df_ltf.empty:
            return BacktestResult(
                metadata=self._metadata(symbol, tf, start_date, end_date, initial_balance, "No data available"),
                trades=[],
            )

        if start_date:
            df_ltf = df_ltf[df_ltf.index >= pd.to_datetime(start_date)]
        if end_date:
            df_ltf = df_ltf[df_ltf.index <= pd.to_datetime(end_date)]

        df_ltf = df_ltf.copy()
        df_ltf.index.name = "time"
        df_htf = (
            df_ltf.resample(HTF_RULE[tf])
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )

        ltf = df_ltf.reset_index()
        htf = df_htf.reset_index()

        trader = self._load_daemon(symbol)
        trades, equity, halted, fx_desc = self._replay(
            trader=trader,
            ltf=ltf,
            htf=htf,
            symbol=symbol,
            timeframe=tf,
            initial_balance=initial_balance,
            risk_pct=risk_pct,
            step_bars=step_bars,
            max_hold_bars=max_hold_bars,
        )

        spec = get_spec(symbol)
        notes = (
            f"SMC daemon replay on {tf} structure with a {HTF_RULE[tf]} bias series resampled from it. "
            f"Fixed-fractional risk {risk_pct:.2%} of equity against the daemon's own stop; "
            f"PnL priced from the actual exit level ({spec.contract_size:g} units/lot), "
            f"stop resolved intrabar and gap-filled at the open, "
            f"round-trip cost {spec.round_trip_bps} bps + {spec.slippage_points} pts slippage/side. "
            f"Bars stepped {step_bars} at a time; unresolved positions time out after {max_hold_bars} bars. "
            f"FX: {fx_desc}."
        )
        if halted:
            notes += " RUN HALTED EARLY: equity breached the ruin floor."

        return BacktestResult(
            metadata=self._metadata(
                symbol, tf, start_date, end_date, initial_balance, notes, df_ltf
            ),
            trades=trades,
        )

    # ── daemon loading ──────────────────────────────────────────────────────
    @staticmethod
    def _load_daemon(symbol: str):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
        smc_src = os.path.join(project_root, "strategies/smc_daemon_project")
        if smc_src not in sys.path:
            sys.path.insert(0, smc_src)
        try:
            from smc_daemon import AssetTrader
        except ImportError as e:
            raise RuntimeError(f"Could not load SMC Daemon logic: {e}")

        # The daemon logs every state transition; that is far too chatty per bar.
        logging.getLogger("SMCDaemon").setLevel(logging.WARNING)
        return AssetTrader(symbol=symbol, config={}, is_mock=True)

    # ── replay loop ─────────────────────────────────────────────────────────
    def _replay(
        self,
        trader,
        ltf: pd.DataFrame,
        htf: pd.DataFrame,
        symbol: str,
        timeframe: str,
        initial_balance: float,
        risk_pct: float,
        step_bars: int,
        max_hold_bars: int,
    ) -> Tuple[List[Trade], float, bool, str]:
        spec = get_spec(symbol)
        rates, fx_desc = quote_rate_series(
            spec.quote_currency, timeframe, pd.DatetimeIndex(ltf["time"])
        )
        rate_arr = rates.to_numpy()
        equity = initial_balance
        ruin_floor = initial_balance * RUIN_FLOOR_PCT
        trades: List[Trade] = []
        halted = False

        highs = ltf["high"].to_numpy()
        lows = ltf["low"].to_numpy()
        closes = ltf["close"].to_numpy()
        opens = ltf["open"].to_numpy()
        htf_times = htf["time"]

        async def drive() -> None:
            nonlocal equity, halted
            i = 200
            n = len(ltf)

            while i < n - 1:
                current_time = ltf["time"].iloc[i]

                # Bias window: completed higher-timeframe bars only.
                htf_upto = htf[htf_times <= current_time]
                if len(htf_upto) < 50:
                    i += step_bars
                    continue

                window_htf = htf_upto.iloc[-50:].copy()
                window_htf["atr"] = trader.calculate_atr(window_htf, period=14)
                window_ltf = ltf.iloc[max(0, i - 200): i + 1].copy()

                await trader.process_tick(mock_dfs={"df_1H": window_htf, "df_5M": window_ltf})

                if trader.state != trader.STATE_POSITION_ACTIVE:
                    i += step_bars
                    continue

                is_long = trader.directional_bias == "BUY"
                entry = float(trader.entry_price)
                sl = float(trader.sl_price)
                tp = float(trader.tp_price)
                stop_distance = abs(entry - sl)

                quote_rate = float(rate_arr[i])
                lots = spec.lots_for_risk(
                    equity * risk_pct, stop_distance, quote_rate,
                    entry_price=entry, equity=equity,
                )
                if lots <= 0:
                    # Unusable stop or equity below the minimum lot: don't invent a fill.
                    trader.reset_to_scanning()
                    i += step_bars
                    continue

                exit_i, exit_price, reason, mae, mfe = self._resolve(
                    i, is_long, entry, sl, tp, lots, spec, quote_rate,
                    opens, highs, lows, closes, max_hold_bars, len(ltf),
                )
                trader.reset_to_scanning()

                if exit_i is None:
                    # Ran off the end of the series with the position still open.
                    break

                gross = spec.gross_pnl(entry, exit_price, lots, is_long, quote_rate)
                cost = spec.round_trip_cost(entry, lots, quote_rate)
                net = gross - cost

                trades.append(Trade(
                    trade_id=f"SMC_{len(trades) + 1:05d}",
                    strategy_name=self.strategy_name,
                    engine=self.engine_name,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=Direction.BUY if is_long else Direction.SELL,
                    entry_time=_iso(current_time),
                    entry_price=round(entry, spec.digits),
                    position_size=lots,
                    exit_time=_iso(ltf["time"].iloc[exit_i]),
                    exit_price=round(exit_price, spec.digits),
                    exit_reason=reason,
                    gross_pnl=round(gross, 2),
                    net_pnl=round(net, 2),
                    commission=round(cost, 2),
                    return_pct=round(net / equity, 6) if equity > 0 else 0.0,
                    mae=round(mae, 2),
                    mfe=round(mfe, 2),
                ))
                equity += net

                if equity <= ruin_floor:
                    logger.warning(
                        "%s: equity fell to %.2f (floor %.2f) after %d trades — halting.",
                        self.strategy_name, equity, ruin_floor, len(trades),
                    )
                    halted = True
                    return

                # Resume after the exit so positions never overlap in time.
                i = max(i + step_bars, exit_i + 1)

        asyncio.run(drive())
        return trades, equity, halted, fx_desc

    @staticmethod
    def _resolve(
        entry_i: int,
        is_long: bool,
        entry: float,
        sl: float,
        tp: float,
        lots: float,
        spec,
        quote_rate: float,
        opens, highs, lows, closes,
        max_hold_bars: int,
        n: int,
    ):
        """
        Walk forward from the bar after entry until the stop, the target or the
        hold limit resolves the position.

        A bar that touches both levels is resolved as a stop-out — the
        pessimistic reading, since bar data cannot say which came first.
        Returns (exit_index, exit_price, ExitReason, mae, mfe) in account
        currency for the excursions, or (None, ...) if the series runs out.
        """
        mae = 0.0
        mfe = 0.0
        last = min(entry_i + max_hold_bars, n - 1)

        for j in range(entry_i + 1, last + 1):
            mfe = max(mfe, spec.gross_pnl(entry, highs[j] if is_long else lows[j], lots, is_long, quote_rate))
            mae = min(mae, spec.gross_pnl(entry, lows[j] if is_long else highs[j], lots, is_long, quote_rate))

            hit_sl = lows[j] <= sl if is_long else highs[j] >= sl
            hit_tp = highs[j] >= tp if is_long else lows[j] <= tp

            if hit_sl:
                gapped = opens[j] <= sl if is_long else opens[j] >= sl
                return j, (opens[j] if gapped else sl), ExitReason.STOP_LOSS, mae, mfe
            if hit_tp:
                gapped = opens[j] >= tp if is_long else opens[j] <= tp
                return j, (opens[j] if gapped else tp), ExitReason.TAKE_PROFIT, mae, mfe

        if last > entry_i:
            return last, float(closes[last]), ExitReason.TIMEOUT, mae, mfe
        return None, 0.0, ExitReason.UNKNOWN, mae, mfe

    # ── metadata ────────────────────────────────────────────────────────────
    def _metadata(
        self,
        symbol: str,
        timeframe: str,
        start_date,
        end_date,
        initial_balance: float,
        notes: str,
        df: Optional[pd.DataFrame] = None,
    ) -> RunMetadata:
        if df is not None and not df.empty:
            actual_start, actual_end = str(df.index[0]), str(df.index[-1])
        else:
            actual_start = str(start_date) if start_date else ""
            actual_end = str(end_date) if end_date else ""

        costs = cost_assumptions(symbol)
        return RunMetadata(
            strategy_name=self.strategy_name,
            engine=self.engine_name,
            symbol=symbol,
            timeframe=timeframe,
            start_date=actual_start,
            end_date=actual_end,
            initial_balance=initial_balance,
            spread_model=str(costs["spread_model"]),
            commission_model=str(costs["commission_model"]),
            commission_rate=costs["commission_rate"],
            slippage_pips=float(costs["slippage_pips"] or 0.0),
            notes=notes,
        )


def _iso(ts) -> str:
    return ts.isoformat() if isinstance(ts, pd.Timestamp) else str(ts)
