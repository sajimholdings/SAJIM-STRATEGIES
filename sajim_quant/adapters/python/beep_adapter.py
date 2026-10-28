"""
SAJIM Quant — BEEP Strategy Adapters

Wraps the BEEP_MASTER_PACKAGE signal logic behind the universal adapter contract.

Execution model
---------------
Earlier revisions of this module sized every trade as
`balance * pnl_pct * leverage` with leverage of 10x (baseline) and 20x (vacuum).
That stakes the entire account on each entry, compounds it, applies no costs and
has no bankruptcy check — so the balance could pass through zero and keep
compounding, and the resulting Sharpe/PnL could not be compared against the
Backtrader adapter (which prices commission and leverage through Cerebro) or the
MT5 adapter (which uses real spread). Both adapters now:

  * size positions from a declared risk/exposure budget, in real lots;
  * price the move through `core.instruments` contract mechanics;
  * charge a round-trip cost (commission + spread + slippage allowance);
  * check the stop against the bar's high/low rather than its close only;
  * halt on ruin instead of trading a negative balance;
  * record intra-trade MAE/MFE so drawdown is not understated;
  * publish the assumptions they ran under in RunMetadata.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from sajim_quant.adapters.base import BaseStrategyAdapter
from sajim_quant.core.fx import quote_rate_series
from sajim_quant.core.instruments import InstrumentSpec, cost_assumptions, get_spec
from sajim_quant.core.schema import (
    BacktestResult,
    Direction,
    ExitReason,
    RunMetadata,
    Trade,
)
from sajim_quant.data.loader import DataLoader

logger = logging.getLogger("sajim_quant.adapters.python.beep")

#: Equity floor below which an account is treated as blown and trading halts.
RUIN_FLOOR_PCT = 0.10


def _beep_src_on_path() -> None:
    """Make the BEEP package importable without mutating the caller's cwd."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    beep_src = os.path.join(project_root, "strategies/BEEP_MASTER_PACKAGE/src")
    if beep_src not in sys.path:
        sys.path.insert(0, beep_src)


class _BeepAdapterBase(BaseStrategyAdapter):
    """Shared data loading, metadata assembly and position bookkeeping."""

    def __init__(self, strategy_name: str):
        super().__init__(strategy_name=strategy_name, engine_name="python:custom")

    # ── data ────────────────────────────────────────────────────────────────
    @staticmethod
    def _load(symbol: str, timeframe: str, start_date, end_date) -> pd.DataFrame:
        df = DataLoader().load(symbol, timeframe, start_date, end_date)
        if df.empty:
            return df
        if start_date:
            df = df[df.index >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df.index <= pd.to_datetime(end_date)]
        return df.rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        )

    @staticmethod
    def _quote_rates(symbol: str, timeframe: str, index) -> Tuple[Any, str]:
        """Per-bar quote-currency -> USD rate, so JPY/EUR/GBP-quoted PnL lands in USD."""
        spec = get_spec(symbol)
        return quote_rate_series(spec.quote_currency, timeframe, index)

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

    def _empty(self, symbol, timeframe, start_date, end_date, initial_balance, reason) -> BacktestResult:
        return BacktestResult(
            metadata=self._metadata(symbol, timeframe, start_date, end_date, initial_balance, reason),
            trades=[],
        )

    # ── trade construction ──────────────────────────────────────────────────
    def _build_trade(
        self,
        idx: int,
        symbol: str,
        timeframe: str,
        spec: InstrumentSpec,
        is_long: bool,
        entry_time,
        entry_price: float,
        exit_time,
        exit_price: float,
        lots: float,
        exit_reason: ExitReason,
        mae: float,
        mfe: float,
        equity_at_entry: float,
        quote_rate: float = 1.0,
    ) -> Tuple[Trade, float]:
        """Returns the normalized trade and its net PnL in account currency."""
        gross = spec.gross_pnl(entry_price, exit_price, lots, is_long, quote_rate)
        cost = spec.round_trip_cost(entry_price, lots, quote_rate)
        net = gross - cost
        return (
            Trade(
                trade_id=f"BEEP_{idx:05d}",
                strategy_name=self.strategy_name,
                engine=self.engine_name,
                symbol=symbol,
                timeframe=timeframe,
                direction=Direction.BUY if is_long else Direction.SELL,
                entry_time=_iso(entry_time),
                entry_price=round(float(entry_price), spec.digits),
                position_size=lots,
                exit_time=_iso(exit_time),
                exit_price=round(float(exit_price), spec.digits),
                exit_reason=exit_reason,
                gross_pnl=round(gross, 2),
                net_pnl=round(net, 2),
                commission=round(cost, 2),
                return_pct=round(net / equity_at_entry, 6) if equity_at_entry > 0 else 0.0,
                mae=round(mae, 2),
                mfe=round(mfe, 2),
            ),
            net,
        )


def _iso(ts) -> str:
    return ts.isoformat() if isinstance(ts, pd.Timestamp) else str(ts)


class BEEPBaselineAdapter(_BeepAdapterBase):
    """
    BEEP Baseline Follower — always in the market, flipping with the sign of the
    causal baseline's first derivative.

    The strategy carries no stop, so risk is bounded by capping notional exposure
    rather than by a stop distance: `exposure` is the fraction of equity put to
    work (1.0 = unleveraged). This is the honest substitute for the old 10x
    full-equity compounding; it cannot produce a negative balance in one trade.
    """

    def run(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        initial_balance: float = 10000.0,
        exposure: float = 1.0,
        passes: int = 3,
        **strategy_params: Any,
    ) -> BacktestResult:
        df = self._load(symbol, timeframe, start_date, end_date)
        if df.empty:
            return self._empty(symbol, timeframe, start_date, end_date, initial_balance, "No data available")

        _beep_src_on_path()
        try:
            from test_baseline import get_causal_beep_baseline
        except ImportError as e:
            raise RuntimeError(f"Could not load BEEP baseline logic: {e}")

        df["Baseline"] = get_causal_beep_baseline(df, passes=passes)
        df["Baseline_Slope"] = df["Baseline"].diff()

        spec = get_spec(symbol)
        rates, fx_desc = self._quote_rates(symbol, timeframe, df.index)
        rate_arr = rates.to_numpy()
        equity = initial_balance
        ruin_floor = initial_balance * RUIN_FLOOR_PCT
        trades: List[Trade] = []

        position = 0            # 1 long, -1 short, 0 flat
        entry_price = 0.0
        entry_time = None
        lots = 0.0
        equity_at_entry = equity
        mae = mfe = 0.0
        halted = False

        highs = df["High"].to_numpy()
        lows = df["Low"].to_numpy()
        closes = df["Close"].to_numpy()
        slopes = df["Baseline_Slope"].to_numpy()
        index = df.index

        for i in range(20, len(df)):
            slope = slopes[i]
            if not np.isfinite(slope):
                continue

            rate = float(rate_arr[i])

            # Track unrealized excursion on the open position before acting on it.
            if position != 0 and lots > 0:
                is_long = position == 1
                best = spec.gross_pnl(entry_price, highs[i] if is_long else lows[i], lots, is_long, rate)
                worst = spec.gross_pnl(entry_price, lows[i] if is_long else highs[i], lots, is_long, rate)
                mfe = max(mfe, best)
                mae = min(mae, worst)

            desired = 1 if slope > 0 else -1
            if position == desired:
                continue

            if position != 0 and lots > 0:
                trade, net = self._build_trade(
                    len(trades) + 1, symbol, timeframe, spec, position == 1,
                    entry_time, entry_price, index[i], closes[i], lots,
                    ExitReason.SIGNAL, mae, mfe, equity_at_entry, rate,
                )
                trades.append(trade)
                equity += net

                if equity <= ruin_floor:
                    logger.warning(
                        "%s: equity fell to %.2f (floor %.2f) after %d trades — halting.",
                        self.strategy_name, equity, ruin_floor, len(trades),
                    )
                    halted = True
                    break

            # Open the new side, sized off current equity.
            price = closes[i]
            lots = spec.lots_for_notional(price, equity * exposure, rate, equity=equity)
            if lots <= 0:
                logger.info("%s: equity %.2f too small for the minimum lot — stopping.", self.strategy_name, equity)
                halted = True
                break

            position = desired
            entry_price = price
            entry_time = index[i]
            equity_at_entry = equity
            mae = mfe = 0.0

        notes = (
            f"BEEP baseline follower, {passes}-pass causal filter. "
            f"Fixed-fractional notional exposure {exposure:.2f}x equity (no leverage), "
            f"round-trip cost {spec.round_trip_bps} bps + {spec.slippage_points} pts slippage/side. "
            f"Strategy carries no stop-loss; risk is bounded by exposure only. FX: {fx_desc}."
        )
        if halted:
            notes += " RUN HALTED EARLY: equity breached the ruin floor."

        return BacktestResult(
            metadata=self._metadata(symbol, timeframe, start_date, end_date, initial_balance, notes, df),
            trades=trades,
        )


class BEEPVacuumAdapter(_BeepAdapterBase):
    """
    BEEP Vacuum Hunter (11-pass harmonic) — enters when accumulated directional
    "mass" crosses a dynamic threshold, exits on velocity exhaustion or a stop.

    The stop is a fixed percentage of the entry price, so positions are sized
    fixed-fractionally: `risk_pct` of equity is lost if the stop is hit. The stop
    is tested against each bar's high/low and filled at the stop price, instead
    of the old close-only test which made gaps and intrabar spikes free.
    """

    def run(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[Union[str, pd.Timestamp]] = None,
        end_date: Optional[Union[str, pd.Timestamp]] = None,
        initial_balance: float = 10000.0,
        risk_pct: float = 0.01,
        stop_pct: float = 0.003,
        warmup_bars: int = 500,
        **strategy_params: Any,
    ) -> BacktestResult:
        df = self._load(symbol, timeframe, start_date, end_date)
        if df.empty:
            return self._empty(symbol, timeframe, start_date, end_date, initial_balance, "No data available")

        _beep_src_on_path()
        try:
            from test_11_pass_vacuum import get_asymptotic_quality_score
        except ImportError as e:
            raise RuntimeError(f"Could not load BEEP Vacuum logic: {e}")

        df["Baseline"] = get_asymptotic_quality_score(df, warmup_bars=warmup_bars)
        df = self._compute_harmonic_mass(df)

        spec = get_spec(symbol)
        rates, fx_desc = self._quote_rates(symbol, timeframe, df.index)
        rate_arr = rates.to_numpy()
        equity = initial_balance
        ruin_floor = initial_balance * RUIN_FLOOR_PCT
        trades: List[Trade] = []

        pos = 0
        entry_price = 0.0
        entry_time = None
        stop_price = 0.0
        lots = 0.0
        equity_at_entry = equity
        mae = mfe = 0.0
        halted = False

        opens = df["Open"].to_numpy()
        highs = df["High"].to_numpy()
        lows = df["Low"].to_numpy()
        closes = df["Close"].to_numpy()
        mass = df["Harmonic_Mass"].to_numpy()
        thresh = df["Dynamic_Threshold"].to_numpy()
        direction = df["Direction"].to_numpy()
        velocity = df["Velocity"].to_numpy()
        index = df.index

        # Don't trade inside the slice the pass-count was fitted on.
        start_i = max(100, warmup_bars)

        for i in range(start_i, len(df)):
            rate = float(rate_arr[i])

            if pos != 0 and lots > 0:
                is_long = pos == 1
                mfe = max(mfe, spec.gross_pnl(entry_price, highs[i] if is_long else lows[i], lots, is_long, rate))
                mae = min(mae, spec.gross_pnl(entry_price, lows[i] if is_long else highs[i], lots, is_long, rate))

                # Stop first: a bar that touches both the stop and a better exit
                # is resolved pessimistically.
                stop_touched = (is_long and lows[i] <= stop_price) or (not is_long and highs[i] >= stop_price)
                # A gap through the stop fills at the open, not the stop price.
                gapped = (is_long and opens[i] <= stop_price) or (not is_long and opens[i] >= stop_price)
                exhausted = (is_long and velocity[i] < 0) or (not is_long and velocity[i] > 0)

                exit_price = exit_reason = None
                if stop_touched:
                    exit_price = opens[i] if gapped else stop_price
                    exit_reason = ExitReason.STOP_LOSS
                elif exhausted:
                    exit_price = closes[i]
                    exit_reason = ExitReason.SIGNAL

                if exit_price is not None:
                    trade, net = self._build_trade(
                        len(trades) + 1, symbol, timeframe, spec, is_long,
                        entry_time, entry_price, index[i], exit_price, lots,
                        exit_reason, mae, mfe, equity_at_entry, rate,
                    )
                    trades.append(trade)
                    equity += net
                    pos = 0
                    lots = 0.0

                    if equity <= ruin_floor:
                        logger.warning(
                            "%s: equity fell to %.2f (floor %.2f) after %d trades — halting.",
                            self.strategy_name, equity, ruin_floor, len(trades),
                        )
                        halted = True
                        break
                continue

            # Flat: look for a fresh threshold crossing.
            if mass[i] >= thresh[i] and mass[i - 1] < thresh[i - 1] and direction[i] != 0:
                is_long = direction[i] == 1
                price = closes[i]
                stop = price * (1 - stop_pct) if is_long else price * (1 + stop_pct)
                sized = spec.lots_for_risk(
                    equity * risk_pct, abs(price - stop), rate,
                    entry_price=price, equity=equity,
                )
                if sized <= 0:
                    continue

                pos = 1 if is_long else -1
                entry_price = price
                entry_time = index[i]
                stop_price = stop
                lots = sized
                equity_at_entry = equity
                mae = mfe = 0.0

        notes = (
            f"BEEP vacuum hunter, causal 11-pass filter (passes fitted on the first "
            f"{warmup_bars} bars; trading starts after them). Fixed-fractional risk "
            f"{risk_pct:.2%} of equity per trade against a {stop_pct:.2%} stop, "
            f"stop tested intrabar on high/low and gap-filled at the open, "
            f"round-trip cost {spec.round_trip_bps} bps + {spec.slippage_points} pts slippage/side. "
            f"FX: {fx_desc}."
        )
        if halted:
            notes += " RUN HALTED EARLY: equity breached the ruin floor."

        return BacktestResult(
            metadata=self._metadata(symbol, timeframe, start_date, end_date, initial_balance, notes, df),
            trades=trades,
        )

    # ── signal logic (unchanged from the original research script) ──────────
    @staticmethod
    def _compute_harmonic_mass(df_beep: pd.DataFrame) -> pd.DataFrame:
        """
        Accumulate directional "harmonic mass" from z-scored deviation spikes and
        derive the dynamic entry threshold. Causal throughout: every statistic
        reads bar i or earlier.
        """
        df_beep["Velocity"] = df_beep["Baseline"].diff()
        df_beep["Deviation"] = df_beep["Close"] - df_beep["Baseline"]
        rolling_std = df_beep["Deviation"].rolling(window=100).std()
        df_beep["Z_Score"] = df_beep["Deviation"] / rolling_std

        cycle_lengths: List[int] = []
        last_cross = 0
        for i in range(1, min(100, len(df_beep))):
            z, pz = df_beep.iloc[i]["Z_Score"], df_beep.iloc[i - 1]["Z_Score"]
            if (z > 0 and pz <= 0) or (z < 0 and pz >= 0):
                if last_cross != 0:
                    cycle_lengths.append(i - last_cross)
                last_cross = i

        current_local_cycle = float(np.mean(cycle_lengths)) if cycle_lengths else 20.0

        n = len(df_beep)
        mass_series = np.zeros(n)
        direction_series = np.zeros(n)
        threshold_series = np.ones(n) * 11.0

        current_mass = 0.0
        current_dir = 0.0
        spike_duration = 0
        gap_cooldown = 0
        historical_masses: List[float] = []

        z_arr = df_beep["Z_Score"].to_numpy()
        open_arr = df_beep["Open"].to_numpy()
        close_arr = df_beep["Close"].to_numpy()
        std_arr = rolling_std.to_numpy()

        for i in range(100, n):
            z = z_arr[i]
            prev_z = z_arr[i - 1]

            if (z > 0 and prev_z <= 0) or (z < 0 and prev_z >= 0):
                current_local_cycle = (current_local_cycle * 0.8) + ((i - last_cross) * 0.2)
                last_cross = i
                if current_mass > 0:
                    historical_masses.append(current_mass)

            if not np.isfinite(z):
                continue

            if i > 100 and np.isfinite(std_arr[i - 1]):
                if abs(open_arr[i] - close_arr[i - 1]) > std_arr[i - 1] * 3:
                    gap_cooldown = 20

            if gap_cooldown > 0:
                gap_cooldown -= 1
                current_mass = 0.0
                current_dir = 0.0
                spike_duration = 0
                continue

            if abs(z) > 1.5:
                if current_dir == 0 or np.sign(z) != current_dir:
                    current_dir = np.sign(z)
                    spike_duration = 1
                else:
                    spike_duration += 1
                time_ratio = spike_duration / max(1.0, current_local_cycle)
                current_mass = current_mass + (abs(z) * time_ratio * 10)
            else:
                current_mass = 0.0
                current_dir = 0.0
                spike_duration = 0

            if len(historical_masses) >= 5:
                recent = historical_masses[-50:]
                threshold_series[i] = max(float(np.mean(recent) + 3 * np.std(recent)), 8.0)
            else:
                threshold_series[i] = 11.0

            mass_series[i] = current_mass
            direction_series[i] = current_dir

        df_beep["Harmonic_Mass"] = mass_series
        df_beep["Direction"] = direction_series
        df_beep["Dynamic_Threshold"] = threshold_series
        return df_beep
