"""
SAJIM Strategy Lab — SignalSimulator: the trusted, cost-honest execution model.

A strategy only emits sparse signal events (interfaces.EVENTS_DTYPE). Everything that
turns them into money happens here, generalised from SMCAdapter._resolve:

  * one position at a time; a signal on bar i fills at open[i + 1 + delay], and only
    when that fill bar lies inside the scored window (no position is carried in);
  * stop/target are checked from the entry bar itself; a bar touching both books the
    STOP; a stop gap fills at the (worse) open; a target gap fills at the target level;
  * then the hold limit (close of bar f + max_hold - 1), then the window end (close of
    the last window bar), then an exit flag / opposite signal (next bar's open, never a
    same-fill reversal) — INTERFACES.md section 3.3 is the frozen order;
  * lots from InstrumentSpec.lots_for_risk (leverage-capped, lot-stepped); 0 lots means
    the signal is skipped and counted as untradable_stop;
  * net = gross - round_trip_cost x cost_mult, all at the entry bar's quote rate; equity
    compounds; the run halts once equity <= RUIN_FLOOR x initial (that trade is STOP_OUT).

The simulator never loads data: the caller passes the frame and the quote-rate array
(core.fx.quote_rate_series). Never use BacktraderAdapter for lab runs — it charges no cost.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from sajim_quant.core.instruments import InstrumentSpec, cost_assumptions
from sajim_quant.core.schema import BacktestResult, Direction, ExitReason, RunMetadata, Trade
from sajim_quant.lab import gates, splits
from sajim_quant.lab.interfaces import (
    EVENTS_DTYPE, RUN_NOTES_SCHEMA, SimOverrides, SimResult,
)

logger = logging.getLogger("sajim_quant.lab.simulator")

ENGINE_VERSION = "signal_sim/1"
_FIRST_CHUNK = 32
_EPOCH = datetime(1970, 1, 1)


class SignalSimulator:
    """Simulates sparse signal events on one OHLC frame. Cheap to build; reusable across runs."""

    def __init__(self, spec: InstrumentSpec, *, initial_balance: float = gates.INITIAL_BALANCE,
                 risk_pct: float = gates.RISK_PCT, cost_mult: float = 1.0, delay_bars: int = 0,
                 max_hold_bars: int, allow_short: bool, window: Optional[Tuple[int, int]] = None,
                 strategy_name: str = "lab", timeframe: str = ""):
        if not (isinstance(initial_balance, (int, float)) and math.isfinite(initial_balance)
                and initial_balance > 0):
            raise ValueError("initial_balance must be a positive finite number")
        if not (isinstance(risk_pct, (int, float)) and math.isfinite(risk_pct) and risk_pct > 0):
            raise ValueError("risk_pct must be a positive finite number")
        if risk_pct > gates.MAX_RISK_PCT:
            logger.warning("risk_pct %.4f clamped to MAX_RISK_PCT %.4f", risk_pct, gates.MAX_RISK_PCT)
            risk_pct = gates.MAX_RISK_PCT
        if not (isinstance(cost_mult, (int, float)) and math.isfinite(cost_mult) and cost_mult >= 0):
            raise ValueError("cost_mult must be a finite number >= 0")
        if isinstance(delay_bars, bool) or not isinstance(delay_bars, (int, np.integer)) or delay_bars < 0:
            raise ValueError("delay_bars must be an integer >= 0")
        if isinstance(max_hold_bars, bool) or not isinstance(max_hold_bars, (int, np.integer)) or max_hold_bars < 1:
            raise ValueError("max_hold_bars must be an integer >= 1")
        if window is not None:
            ws, we = window
            if not (isinstance(ws, (int, np.integer)) and isinstance(we, (int, np.integer)) and 0 <= ws <= we):
                raise ValueError("window must be (start_bar, stop_bar) with 0 <= start_bar <= stop_bar")
            window = (int(ws), int(we))
        self.spec = spec
        self.initial_balance = float(initial_balance)
        self.risk_pct = float(risk_pct)
        self.cost_mult = float(cost_mult)
        self.delay_bars = int(delay_bars)
        self.max_hold_bars = int(max_hold_bars)
        self.allow_short = bool(allow_short)
        self.window = window
        self.strategy_name = strategy_name
        self.timeframe = timeframe

    def with_overrides(self, overrides: SimOverrides) -> "SignalSimulator":
        """A copy with robustness overrides applied (window=None keeps this simulator's window)."""
        return SignalSimulator(
            self.spec, initial_balance=self.initial_balance, risk_pct=self.risk_pct,
            cost_mult=overrides.cost_mult, delay_bars=overrides.delay_bars,
            max_hold_bars=self.max_hold_bars, allow_short=self.allow_short,
            window=overrides.window if overrides.window is not None else self.window,
            strategy_name=self.strategy_name, timeframe=self.timeframe,
        )

    # ── validation ─────────────────────────────────────────────────────────
    @staticmethod
    def _arrays(frame: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("frame must be a DataFrame with a DatetimeIndex")
        missing = [c for c in ("open", "high", "low", "close") if c not in frame.columns]
        if missing:
            raise ValueError(f"frame is missing columns {missing}")
        if len(frame) == 0:
            raise ValueError("frame is empty")
        return tuple(frame[c].to_numpy(dtype=np.float64) for c in ("open", "high", "low", "close"))  # type: ignore

    def _rates(self, quote_rate: Optional[np.ndarray], n: int) -> np.ndarray:
        if quote_rate is None:
            if (self.spec.quote_currency or "USD").upper() != "USD":
                raise ValueError(f"{self.spec.symbol} is quoted in {self.spec.quote_currency}; "
                                 f"a quote_rate array is required")
            return np.ones(n)
        r = np.asarray(quote_rate, dtype=np.float64)
        if r.shape != (n,):
            raise ValueError(f"quote_rate must have shape ({n},), got {r.shape}")
        if not (np.isfinite(r).all() and (r > 0).all()):
            raise ValueError("quote_rate must be finite and > 0")
        return r

    def _events(self, events: np.ndarray, n: int) -> np.ndarray:
        if not isinstance(events, np.ndarray) or events.dtype != EVENTS_DTYPE or events.ndim != 1:
            raise ValueError("events must be a 1-D array of interfaces.EVENTS_DTYPE")
        if len(events) == 0:
            return events
        bars = events["bar"]
        if bars[0] < 0 or bars[-1] >= n or (len(bars) > 1 and not (np.diff(bars) > 0).all()):
            raise ValueError("event bars must be strictly increasing and inside the frame")
        sig = events["signal"]
        if not np.isin(sig, (-1, 0, 1)).all():
            raise ValueError("signal must be -1, 0 or 1")
        if not self.allow_short and (sig < 0).any():
            raise ValueError("short signal while allow_short is false")
        on = sig != 0
        sd, tp = events["stop_dist"][on], events["tp_dist"][on]
        if not (np.isfinite(sd).all() and (sd > 0).all()):
            raise ValueError("stop_dist must be finite and > 0 on every signal")
        if not (np.isnan(tp) | (np.isfinite(tp) & (tp > 0))).all():
            raise ValueError("tp_dist must be NaN or finite and > 0 on every signal")
        return events

    # ── core ───────────────────────────────────────────────────────────────
    @staticmethod
    def _first_touch(h: np.ndarray, l: np.ndarray, a: int, b: int, is_long: bool,
                     sl: float, tp: Optional[float]) -> Tuple[Optional[int], bool]:
        """First bar in [a, b] touching SL or TP, scanning in growing chunks: (bar, stop_touched)."""
        start, chunk = a, _FIRST_CHUNK
        while start <= b:
            end = min(b, start + chunk - 1) + 1
            if is_long:
                m_sl = l[start:end] <= sl
                hit = m_sl if tp is None else (m_sl | (h[start:end] >= tp))
            else:
                m_sl = h[start:end] >= sl
                hit = m_sl if tp is None else (m_sl | (l[start:end] <= tp))
            if hit.any():
                k = int(hit.argmax())
                return start + k, bool(m_sl[k])
            start, chunk = end, chunk * 4
        return None, False

    def run(self, frame: pd.DataFrame, events: np.ndarray,
            quote_rate: Optional[np.ndarray] = None) -> SimResult:
        """Simulate `events` on `frame` (see the module docstring for the frozen rules)."""
        o, h, l, c = self._arrays(frame)
        n = len(o)
        ws, we = self.window if self.window is not None else (0, n)
        if we > n:
            raise ValueError(f"window {ws, we} exceeds the frame ({n} bars)")
        rate = self._rates(quote_rate, n)
        ev = self._events(events, n)
        idx = frame.index
        tns, unit = _naive_ints(idx)
        to_ns = _NS_PER_UNIT[unit]
        spec, d, mh = self.spec, self.delay_bars, self.max_hold_bars

        sig_all = ev["signal"]
        on = sig_all != 0
        s_bar = ev["bar"][on]
        s_sig = sig_all[on]
        s_stop = ev["stop_dist"][on]
        s_tp = ev["tp_dist"][on]
        fills = s_bar + 1 + d
        n_signals = int(np.count_nonzero((fills >= ws) & (fills < we)))
        exit_flag = ev["exit"].astype(bool)
        exit_bars = {True: ev["bar"][exit_flag | (sig_all == -1)],     # closes a long
                     False: ev["bar"][exit_flag | (sig_all == 1)]}     # closes a short

        trades: List[Trade] = []
        eq_times = [int(tns[min(ws, n - 1)])]          # in the index's own unit
        eq_vals = [self.initial_balance]
        equity = self.initial_balance
        floor = gates.RUIN_FLOOR * self.initial_balance
        untradable = attempts = window_end_closes = 0
        halted = False
        next_free = 0
        min_signal_bar = ws - 1 - d                 # the signal bar whose fill is ws
        m = len(s_bar)

        while ws < we:
            p = int(np.searchsorted(s_bar, max(next_free, min_signal_bar), side="left"))
            if p >= m:
                break
            i = int(s_bar[p])
            f = i + 1 + d
            if f >= we:
                break
            attempts += 1
            is_long = bool(s_sig[p] > 0)
            s = 1.0 if is_long else -1.0
            fill = float(o[f])
            r = float(rate[f])
            stop = float(s_stop[p])
            tp_dist = float(s_tp[p])
            lots = spec.lots_for_risk(equity * self.risk_pct, stop, r, entry_price=fill, equity=equity)
            if lots <= 0:
                untradable += 1
                next_free = f
                continue
            sl = fill - s * stop
            tp = None if math.isnan(tp_dist) else fill + s * tp_dist

            hold_end = f + mh - 1
            limit = min(hold_end, we - 1)
            cand = exit_bars[is_long]
            q = int(np.searchsorted(cand, f, side="left"))
            j_sig = int(cand[q]) if q < len(cand) and cand[q] < limit else None
            j_hit, stop_hit = self._first_touch(h, l, f, j_sig if j_sig is not None else limit,
                                                is_long, sl, tp)
            if j_hit is not None:
                j = j_hit
                if stop_hit:
                    reason = ExitReason.STOP_LOSS
                    gapped = o[j] <= sl if is_long else o[j] >= sl
                    exit_px = float(o[j]) if gapped else sl
                else:
                    reason, exit_px = ExitReason.TAKE_PROFIT, float(tp)
                exit_bar = j
                next_free = j
            elif j_sig is not None:
                j = j_sig
                reason, exit_px, exit_bar = ExitReason.SIGNAL, float(o[j + 1]), j + 1
                next_free = j + 1
            else:
                j = limit
                reason, exit_px, exit_bar = ExitReason.TIMEOUT, float(c[j]), j
                next_free = j
                if j == we - 1 and j != hold_end:
                    window_end_closes += 1

            # excursions over f..j. On an SL/TP exit bar the intrabar order is unknown, so the
            # favourable side never counts beyond what certainly happened while the trade was
            # open: the TP level on a TP bar; the bar's open (or the stop fill, if better) on
            # an SL bar. The adverse side is clamped at the stop fill on an SL bar and kept
            # whole on a TP bar (pessimistic MAE).
            hi = float(h[f:j].max()) if j > f else -math.inf
            lo = float(l[f:j].min()) if j > f else math.inf
            hj, lj = float(h[j]), float(l[j])
            if reason == ExitReason.STOP_LOSS:
                if is_long:
                    lj = max(lj, exit_px)
                    hj = min(hj, max(float(o[j]), exit_px))
                else:
                    hj = min(hj, exit_px)
                    lj = max(lj, min(float(o[j]), exit_px))
            elif reason == ExitReason.TAKE_PROFIT:
                if is_long:
                    hj = min(hj, exit_px)
                else:
                    lj = max(lj, exit_px)
            hi, lo = max(hi, hj), min(lo, lj)
            if reason == ExitReason.SIGNAL:
                hi, lo = max(hi, exit_px), min(lo, exit_px)
            adverse, favourable = (lo, hi) if is_long else (hi, lo)
            mae = min(0.0, spec.gross_pnl(fill, adverse, lots, is_long, r))
            mfe = max(0.0, spec.gross_pnl(fill, favourable, lots, is_long, r))

            gross = spec.gross_pnl(fill, exit_px, lots, is_long, r)
            cost = spec.round_trip_cost(fill, lots, r) * self.cost_mult
            net = gross - cost
            equity_before = equity
            equity += net
            custom: Dict[str, Any] = {
                "signal_bar": i, "entry_bar": f, "exit_bar": exit_bar, "stop_dist": stop,
                "tp_dist": None if tp is None else tp_dist, "quote_rate": r, "cost_mult": self.cost_mult,
            }
            if equity <= floor:
                custom["original_exit_reason"] = reason.value
                reason = ExitReason.STOP_OUT
                halted = True
            exit_t = int(tns[exit_bar])
            trades.append(Trade(
                trade_id=f"{self.strategy_name}_{len(trades) + 1:05d}",
                strategy_name=self.strategy_name, engine=gates.ENGINE, symbol=spec.symbol,
                timeframe=self.timeframe, direction=Direction.BUY if is_long else Direction.SELL,
                entry_time=_iso(int(tns[f]) * to_ns), entry_price=fill, position_size=lots,
                exit_time=_iso(exit_t * to_ns), exit_price=exit_px, exit_reason=reason,
                gross_pnl=gross, net_pnl=net, commission=cost, swap_cost=0.0,
                return_pct=net / equity_before, mae=mae, mfe=mfe, custom_fields=custom,
            ))
            eq_times.append(exit_t)
            eq_vals.append(equity)
            if halted:
                logger.info("%s: equity %.2f at or below the ruin floor %.2f after %d trades; halting",
                            self.strategy_name, equity, floor, len(trades))
                break

        has_bars = ws < we
        notes = {
            "cost_mult": self.cost_mult, "delay_bars": d, "risk_pct": self.risk_pct,
            "initial_balance": self.initial_balance, "max_hold_bars": mh, "allow_short": self.allow_short,
            "window": {"start_bar": ws, "stop_bar": we,
                       "first_bar": _iso(int(tns[ws]) * to_ns) if has_bars else None,
                       "last_bar": _iso(int(tns[we - 1]) * to_ns) if has_bars else None},
            "untradable_stop": untradable, "n_signals": n_signals, "n_entry_attempts": attempts,
            "window_end_closes": window_end_closes, "halted": halted, "ruin_floor": gates.RUIN_FLOOR,
        }
        eq_index = pd.DatetimeIndex(np.array(eq_times, dtype=np.int64).view(f"datetime64[{unit}]"), name="time")
        return SimResult(
            trades=trades,
            equity=pd.Series(eq_vals, index=eq_index, dtype=np.float64, name="equity"),
            untradable_stop=untradable, n_signals=n_signals, n_entry_attempts=attempts,
            window_end_closes=window_end_closes, halted=halted, notes=notes,
        )


_NS_PER_UNIT = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}


def _naive_ints(index: pd.DatetimeIndex) -> Tuple[np.ndarray, str]:
    """
    Bar open times as int64 in the index's own unit (no copy; pandas 3 defaults to 'us'),
    naive UTC (tz-aware input is converted to UTC first).
    """
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    vals = index.values
    unit, _ = np.datetime_data(vals.dtype)
    if unit not in _NS_PER_UNIT:
        vals, unit = vals.astype("datetime64[ns]"), "ns"
    return vals.view(np.int64), unit


def _iso(ns: int) -> str:
    """Same text as pd.Timestamp(ns).isoformat() for a naive time ('2024-09-01T08:20:00')."""
    if ns % 1000 == 0:
        return (_EPOCH + timedelta(microseconds=ns // 1000)).isoformat()
    return pd.Timestamp(ns).isoformat()


def _json_safe(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


def to_backtest_result(sim: SimResult, *, lab_id: str, symbol: str, timeframe: str, stage: str,
                       code_sha256: str, params: Mapping, fx_desc: str) -> BacktestResult:
    """
    The universal-schema result of one simulation (RunMetadata per INTERFACES.md 3.3;
    `notes` is sorted-key JSON, schema sajim.lab.run_notes/1). Trades are copied and
    labelled with lab_id, the leaderboard join key.
    """
    stage = splits.normalize_stage(stage)
    w_start, w_stop = splits.window(stage)
    sn = sim.notes
    win = sn.get("window", {})
    cost_mult = float(sn.get("cost_mult", 1.0))
    costs = cost_assumptions(symbol)
    notes = {
        "schema": RUN_NOTES_SCHEMA, "code_sha256": code_sha256, "params": _json_safe(dict(params)),
        "stage": stage,
        "window": {"start": w_start.isoformat(), "stop": None if w_stop is None else w_stop.isoformat(),
                   "first_bar": win.get("first_bar"), "last_bar": win.get("last_bar")},
        "cost_mult": cost_mult, "delay": int(sn.get("delay_bars", 0)), "risk_pct": sn.get("risk_pct"),
        "untradable_stop": sim.untradable_stop, "n_signals": sim.n_signals,
        "n_entry_attempts": sim.n_entry_attempts, "window_end_closes": sim.window_end_closes,
        "halted": sim.halted, "fx_desc": fx_desc, "gates_version": gates.GATES_VERSION,
        "protocol_version": splits.PROTOCOL_VERSION, "engine_version": ENGINE_VERSION,
    }
    meta = RunMetadata(
        strategy_name=lab_id, engine=gates.ENGINE, symbol=symbol, timeframe=timeframe,
        start_date=win.get("first_bar") or "", end_date=win.get("last_bar") or "",
        initial_balance=float(sn.get("initial_balance", gates.INITIAL_BALANCE)), currency="USD",
        spread_model="constant", spread_pips=None, commission_model=gates.COMMISSION_MODEL,
        commission_rate=float(costs["commission_rate"] or 0.0) * cost_mult,
        slippage_pips=float(costs["slippage_pips"] or 0.0) * cost_mult,
        execution_latency_ms=0.0, notes=json.dumps(_json_safe(notes), sort_keys=True),
    )
    trades = [dataclasses.replace(t, strategy_name=lab_id, timeframe=t.timeframe or timeframe,
                                  custom_fields=dict(t.custom_fields))
              for t in sim.trades]
    return BacktestResult(metadata=meta, trades=trades)
