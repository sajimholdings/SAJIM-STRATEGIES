"""simulator.py: SignalSimulator execution rules, hand-computed costs/FX, and to_backtest_result."""

import json
import math
import time

import numpy as np
import pandas as pd
import pytest

from sajim_quant.core.instruments import cost_assumptions, get_spec
from sajim_quant.core.schema import BacktestResult, Direction, ExitReason
from sajim_quant.lab import gates as G
from sajim_quant.lab.interfaces import EVENTS_DTYPE, RUN_NOTES_SCHEMA, SimOverrides, SimResult
from sajim_quant.lab.simulator import SignalSimulator, to_backtest_result

XAU = get_spec("XAUUSD")       # contract 100, point 0.01, 3.0 bps, 5 points slippage
JPY = get_spec("USDJPY")       # contract 100k, point 0.001, 1.2 bps, 5 points slippage, quote JPY


def frame(rows, start="2024-09-02 00:00", freq="5min"):
    """rows = [(open, high, low, close), ...] -> OHLC frame with a naive UTC index."""
    a = np.asarray(rows, dtype=float)
    idx = pd.date_range(start, periods=len(a), freq=freq, name="time")
    return pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3],
                         "volume": 1.0}, index=idx)


def events(*recs):
    """recs = (bar, signal, stop_dist, tp_dist, exit)."""
    ev = np.zeros(len(recs), dtype=EVENTS_DTYPE)
    for k, (bar, sig, sd, tp, ex) in enumerate(recs):
        ev[k] = (bar, sig, sd if sig else np.nan, tp if sig else np.nan, ex)
    return ev


def flat_rows(n, px=2000.0, width=1.0):
    return [(px, px + width, px - width, px)] * n


def sim(spec=XAU, **kw):
    kw.setdefault("max_hold_bars", 100)
    kw.setdefault("allow_short", True)
    return SignalSimulator(spec, **kw)


# ── hand-computed trades ────────────────────────────────────────────────────

def test_xauusd_long_tp_hand_computed():
    rows = flat_rows(8)
    rows[1] = (2000.0, 2004.0, 1998.0, 2003.0)       # fill bar: no touch
    rows[2] = (2003.0, 2010.5, 2002.0, 2009.0)       # TP 2010 touched
    res = sim().run(frame(rows), events((0, 1, 5.0, 10.0, False)))
    assert len(res.trades) == 1
    t = res.trades[0]
    # lots: risk $100 / (5.0 * 100 oz) = 0.2 lot (leverage cap 1.0 lot)
    assert t.position_size == pytest.approx(0.2)
    assert t.direction == Direction.BUY and t.exit_reason == ExitReason.TAKE_PROFIT
    assert (t.entry_price, t.exit_price) == (2000.0, 2010.0)
    assert t.gross_pnl == pytest.approx(10.0 * 0.2 * 100)                      # 200
    # cost: 40 000 notional x 3 bps = 12, plus 5 points x 0.01 x 2 sides x 0.2 x 100 = 2
    assert t.commission == pytest.approx(14.0)
    assert t.commission == pytest.approx(XAU.round_trip_cost(2000.0, 0.2, 1.0))
    assert t.net_pnl == pytest.approx(186.0)
    assert t.return_pct == pytest.approx(186.0 / 10_000)
    assert t.mae == pytest.approx((1998.0 - 2000.0) * 0.2 * 100)               # -40
    assert t.mfe == pytest.approx(200.0)                                       # clamped at the TP
    assert t.entry_time == "2024-09-02T00:05:00" and t.exit_time == "2024-09-02T00:10:00"
    assert t.custom_fields == {"signal_bar": 0, "entry_bar": 1, "exit_bar": 2, "stop_dist": 5.0,
                               "tp_dist": 10.0, "quote_rate": 1.0, "cost_mult": 1.0}
    assert t.trade_id == "lab_00001" and t.engine == G.ENGINE and t.symbol == "XAUUSD"
    assert res.equity.tolist() == [10_000.0, pytest.approx(10_186.0)]
    assert res.equity.index[0] == pd.Timestamp("2024-09-02 00:00")
    assert res.n_signals == 1 and res.n_entry_attempts == 1 and res.untradable_stop == 0


def test_usdjpy_short_hand_computed_with_fx():
    rows = [(150.0, 150.1, 149.9, 150.0)] * 6
    rows[2] = (149.8, 149.9, 148.9, 149.0)           # TP 149.0 touched
    rate = np.full(6, 1 / 140.0)
    rate[1] = 1 / 150.0                              # the entry bar's rate is the one used
    res = sim(JPY).run(frame(rows), events((0, -1, 0.5, 1.0, False)), quote_rate=rate)
    t = res.trades[0]
    # lots: $100 / (0.5 x 100 000 / 150) = 0.3
    assert t.position_size == pytest.approx(0.3)
    assert t.direction == Direction.SELL and t.exit_price == 149.0
    assert t.gross_pnl == pytest.approx(1.0 * 0.3 * 100_000 / 150)            # 200 USD, not 30 000 JPY
    # cost: 30 000 USD notional x 1.2 bps = 3.6, plus 5 x 0.001 x 2 x 0.3 x 100 000 / 150 = 2.0
    assert t.commission == pytest.approx(5.6)
    assert t.commission == pytest.approx(JPY.round_trip_cost(150.0, 0.3, 1 / 150.0))
    assert t.net_pnl == pytest.approx(194.4)
    assert t.custom_fields["quote_rate"] == pytest.approx(1 / 150.0)


def test_fx_parity_stop_loses_the_risk_amount_in_usd():
    """A stop-out costs ~1% of equity in USD whether the symbol is quoted in USD or JPY."""
    gold = flat_rows(5)
    gold[2] = (2000.0, 2001.0, 1994.0, 1995.0)
    g = sim().run(frame(gold), events((0, 1, 5.0, 10.0, False))).trades[0]
    yen = [(150.0, 150.1, 149.9, 150.0)] * 5
    yen[2] = (150.0, 150.6, 149.95, 150.4)
    y = sim(JPY).run(frame(yen), events((0, -1, 0.5, 1.0, False)), quote_rate=np.full(5, 1 / 150.0)).trades[0]
    assert g.exit_reason == y.exit_reason == ExitReason.STOP_LOSS
    assert g.gross_pnl == pytest.approx(-100.0) and y.gross_pnl == pytest.approx(-100.0)
    with pytest.raises(ValueError):
        sim(JPY).run(frame(yen), events((0, -1, 0.5, 1.0, False)))      # JPY needs a rate array


# ── stop / target rules ─────────────────────────────────────────────────────

def test_both_touched_bar_books_the_stop():
    rows = flat_rows(6)
    rows[2] = (2000.0, 2011.0, 1994.0, 2005.0)       # touches SL 1995 and TP 2010
    t = sim().run(frame(rows), events((0, 1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.STOP_LOSS and t.exit_price == 1995.0
    assert t.mae == pytest.approx(-5.0 * 20)          # clamped at the stop, not the 1994 low
    # the TP touch on the stop bar is not credited: MFE = fill bar's high 2001 (the stop bar opens at 2000)
    assert t.mfe == pytest.approx(1.0 * 20)


def test_stop_bar_mfe_is_clamped_for_shorts_and_keeps_a_favourable_open():
    rows = flat_rows(6)
    rows[2] = (1998.0, 2006.0, 1989.0, 2004.0)       # short: opens 2 lower, touches TP 1990 and SL 2005
    t = sim().run(frame(rows), events((0, -1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.STOP_LOSS and t.exit_price == 2005.0
    assert t.mae == pytest.approx(-5.0 * 20)          # clamped at the stop, not the 2006 high
    assert t.mfe == pytest.approx(2.0 * 20)           # the 1998 open counts; the 1989 low does not
    rows[2] = (2003.0, 2006.0, 1989.0, 2004.0)       # opens against the trade: nothing favourable on that bar
    t = sim().run(frame(rows), events((0, -1, 5.0, 10.0, False))).trades[0]
    assert t.mfe == pytest.approx(1.0 * 20)           # only the fill bar's 1999 low


def test_entry_bar_stop_is_honoured():
    rows = flat_rows(6)
    rows[1] = (2000.0, 2001.0, 1990.0, 1992.0)       # the fill bar itself breaks the stop
    t = sim().run(frame(rows), events((0, 1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.STOP_LOSS
    assert t.custom_fields["entry_bar"] == t.custom_fields["exit_bar"] == 1
    assert t.exit_price == 1995.0


def test_entry_bar_target_is_honoured_for_shorts():
    rows = flat_rows(6)
    rows[1] = (2000.0, 2001.0, 1989.0, 1991.0)
    t = sim().run(frame(rows), events((0, -1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.TAKE_PROFIT and t.exit_price == 1990.0


def test_stop_gap_fills_at_the_worse_open_and_target_gap_at_the_level():
    rows = flat_rows(6)
    rows[2] = (1990.0, 1991.0, 1985.0, 1988.0)       # opens through the 1995 stop
    t = sim().run(frame(rows), events((0, 1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.STOP_LOSS and t.exit_price == 1990.0
    assert t.gross_pnl == pytest.approx(-10.0 * 20)
    rows = flat_rows(6)
    rows[2] = (2015.0, 2016.0, 2012.0, 2014.0)       # opens through the 2010 target
    t = sim().run(frame(rows), events((0, 1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.TAKE_PROFIT and t.exit_price == 2010.0
    rows = flat_rows(6)
    rows[2] = (2008.0, 2009.0, 2006.0, 2007.0)       # short: opens through the 2005 stop
    t = sim().run(frame(rows), events((0, -1, 5.0, 10.0, False))).trades[0]
    assert t.exit_reason == ExitReason.STOP_LOSS and t.exit_price == 2008.0


def test_no_target_runs_to_the_hold_limit():
    rows = flat_rows(10)
    t = sim(max_hold_bars=3).run(frame(rows), events((0, 1, 5.0, np.nan, False))).trades[0]
    assert t.exit_reason == ExitReason.TIMEOUT and t.custom_fields["tp_dist"] is None
    assert t.custom_fields["exit_bar"] == 3                         # f=1, close of f + 3 - 1


# ── costs ───────────────────────────────────────────────────────────────────

def test_double_cost_changes_net_by_exactly_the_extra_cost():
    rows = flat_rows(8)
    rows[3] = (2003.0, 2012.0, 2002.0, 2011.0)
    ev = events((0, 1, 5.0, 10.0, False))
    base = sim().run(frame(rows), ev).trades[0]
    dbl = sim(cost_mult=2.0).run(frame(rows), ev).trades[0]
    assert dbl.position_size == base.position_size                  # sizing is unchanged by stress
    assert dbl.commission == pytest.approx(2 * base.commission)
    assert dbl.net_pnl == pytest.approx(base.net_pnl - base.commission)
    assert dbl.gross_pnl == base.gross_pnl
    assert dbl.custom_fields["cost_mult"] == 2.0


def test_every_trade_pays_exactly_the_spec_cost_times_mult():
    rng = np.random.default_rng(5)
    n = 3000
    c = 2000 + np.cumsum(rng.normal(0, 1.0, n))
    o = np.r_[c[0], c[:-1]]
    rows = np.c_[o, np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5, c]
    bars = np.sort(rng.choice(n - 2, 120, replace=False))
    ev = np.zeros(len(bars), dtype=EVENTS_DTYPE)
    ev["bar"], ev["signal"], ev["stop_dist"], ev["tp_dist"] = bars, rng.choice([-1, 1], len(bars)), 4.0, 8.0
    for mult in (1.0, 1.5, 2.0):
        res = sim(cost_mult=mult, max_hold_bars=40).run(frame(rows), ev)
        assert len(res.trades) > 20
        for t in res.trades:
            assert t.commission == pytest.approx(XAU.round_trip_cost(t.entry_price, t.position_size, 1.0) * mult)
            assert t.net_pnl == pytest.approx(t.gross_pnl - t.commission)
        assert res.equity.iloc[-1] == pytest.approx(10_000 + sum(t.net_pnl for t in res.trades))


# ── timing rules ────────────────────────────────────────────────────────────

def test_delay_shifts_the_fill_by_one_bar():
    rows = [(2000.0 + k, 2001.0 + k, 1999.0 + k, 2000.0 + k) for k in range(10)]
    ev = events((0, 1, 5.0, 50.0, False), (1, 1, 5.0, 50.0, False))
    t0 = sim(max_hold_bars=3).run(frame(rows), ev).trades[0]
    t1 = sim(max_hold_bars=3, delay_bars=1).run(frame(rows), ev).trades[0]
    assert (t0.custom_fields["entry_bar"], t0.entry_price) == (1, 2001.0)
    assert (t1.custom_fields["entry_bar"], t1.entry_price) == (2, 2002.0)
    assert t1.custom_fields["signal_bar"] == 0                     # bar 1's signal was pending, ignored
    over = sim(max_hold_bars=3).with_overrides(SimOverrides(delay_bars=1))
    assert over.run(frame(rows), ev).trades[0].custom_fields["entry_bar"] == 2


def test_window_end_closes_as_timeout_and_nothing_is_carried_in():
    rows = flat_rows(12)
    res = sim(window=(2, 6)).run(frame(rows), events((0, 1, 5.0, 50.0, False), (1, 1, 5.0, 50.0, False)))
    # bar 0's fill (bar 1) is before the window: ignored. bar 1 fills at bar 2.
    assert res.n_signals == 1 and len(res.trades) == 1
    t = res.trades[0]
    assert t.custom_fields["entry_bar"] == 2 and t.custom_fields["exit_bar"] == 5
    assert t.exit_reason == ExitReason.TIMEOUT and res.window_end_closes == 1
    assert res.equity.index[0] == frame(rows).index[2]
    assert res.notes["window"] == {"start_bar": 2, "stop_bar": 6, "first_bar": "2024-09-02T00:10:00",
                                   "last_bar": "2024-09-02T00:25:00"}
    # a signal on the last window bar would fill outside the window: not a signal at all
    res = sim(window=(2, 6)).run(frame(rows), events((5, 1, 5.0, 50.0, False)))
    assert res.n_signals == 0 and res.trades == []


def test_hold_limit_timeout_and_max_hold_one():
    rows = flat_rows(10)
    t = sim(max_hold_bars=1).run(frame(rows), events((0, 1, 5.0, 50.0, False))).trades[0]
    assert t.custom_fields["entry_bar"] == t.custom_fields["exit_bar"] == 1 and t.exit_reason == ExitReason.TIMEOUT
    res = sim(max_hold_bars=4, window=(0, 5)).run(frame(rows), events((0, 1, 5.0, 50.0, False)))
    assert res.trades[0].custom_fields["exit_bar"] == 4 and res.window_end_closes == 0   # hold limit first


def test_exit_flag_and_opposite_signal_close_next_open_without_reversal():
    rows = [(2000.0 + k, 2001.0 + k, 1999.0 + k, 2000.5 + k) for k in range(12)]
    res = sim().run(frame(rows), events((0, 1, 5.0, 50.0, False), (2, 0, 0, 0, True), (4, 1, 5.0, 50.0, False)))
    a, b = res.trades
    assert a.exit_reason == ExitReason.SIGNAL and a.custom_fields["exit_bar"] == 3 and a.exit_price == 2003.0
    assert a.exit_time == "2024-09-02T00:15:00"
    assert b.custom_fields["entry_bar"] == 5
    res = sim().run(frame(rows), events((0, 1, 5.0, 50.0, False), (3, -1, 5.0, 50.0, False),
                                        (4, -1, 5.0, 50.0, False)))
    a, b = res.trades
    assert a.exit_reason == ExitReason.SIGNAL and a.custom_fields["exit_bar"] == 4
    assert b.direction == Direction.SELL and b.custom_fields["signal_bar"] == 4   # not bar 3's signal
    assert res.n_entry_attempts == 2


def test_same_direction_signals_while_in_position_are_ignored_and_flat_exit_flags_too():
    rows = flat_rows(12)
    res = sim(max_hold_bars=5).run(frame(rows), events((0, 0, 0, 0, True), (1, 1, 5.0, 50.0, False),
                                                       (2, 1, 5.0, 50.0, False), (3, 1, 5.0, 50.0, False)))
    assert len(res.trades) == 1 and res.n_signals == 3 and res.n_entry_attempts == 1


def test_signal_on_the_stop_bar_may_enter_next_bar():
    rows = flat_rows(10)
    rows[2] = (2000.0, 2001.0, 1994.0, 1996.0)
    res = sim().run(frame(rows), events((0, 1, 5.0, 50.0, False), (2, 1, 5.0, 50.0, False)))
    assert [t.custom_fields["entry_bar"] for t in res.trades] == [1, 3]


# ── sizing and ruin ─────────────────────────────────────────────────────────

def test_untradable_stop_is_skipped_and_counted():
    rows = flat_rows(10)
    ev = events((0, 1, 0.1, 1.0, False), (1, 1, 5.0, 50.0, False))   # 0.1 < min_tradable_stop 0.2
    res = sim(max_hold_bars=2).run(frame(rows), ev)
    assert res.untradable_stop == 1 and res.n_entry_attempts == 2 and len(res.trades) == 1
    assert res.trades[0].custom_fields["signal_bar"] == 1


def test_ruin_floor_relabels_stop_out_and_halts():
    rows = flat_rows(10)
    rows[2] = (1540.0, 1541.0, 1530.0, 1535.0)      # gaps 455 below a 1995 stop
    res = sim().run(frame(rows), events((0, 1, 5.0, 50.0, False), (4, 1, 5.0, 50.0, False)))
    assert res.halted and len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == ExitReason.STOP_OUT
    assert t.custom_fields["original_exit_reason"] == "SL"
    assert t.exit_price == 1540.0 and t.net_pnl == pytest.approx(-460 * 20 - 14.0)
    assert res.equity.iloc[-1] <= G.RUIN_FLOOR * G.INITIAL_BALANCE
    assert res.notes["halted"] is True


def test_compounding_sizes_from_current_equity():
    rows = flat_rows(12)
    rows[2] = (2000.0, 2051.0, 1999.0, 2050.0)      # +50 on 0.2 lot
    res = sim(max_hold_bars=50).run(frame(rows), events((0, 1, 5.0, 50.0, False), (3, 1, 5.0, 50.0, False)))
    a, b = res.trades
    eq = 10_000 + a.net_pnl
    assert b.position_size == pytest.approx(XAU.lots_for_risk(eq * 0.01, 5.0, 1.0, entry_price=2000.0, equity=eq))
    assert b.position_size > a.position_size


def test_risk_pct_is_clamped(caplog):
    s = sim(risk_pct=0.05)
    assert s.risk_pct == G.MAX_RISK_PCT


# ── validation ──────────────────────────────────────────────────────────────

def test_event_validation():
    f = frame(flat_rows(5))
    s = sim(allow_short=False)
    with pytest.raises(ValueError):
        s.run(f, events((0, -1, 5.0, 1.0, False)))                       # short not allowed
    with pytest.raises(ValueError):
        s.run(f, events((2, 1, 5.0, 1.0, False), (1, 1, 5.0, 1.0, False)))   # not increasing
    with pytest.raises(ValueError):
        s.run(f, events((5, 1, 5.0, 1.0, False)))                         # out of range
    with pytest.raises(ValueError):
        s.run(f, events((1, 1, np.nan, 1.0, False)))
    with pytest.raises(ValueError):
        s.run(f, events((1, 1, 5.0, -1.0, False)))
    with pytest.raises(ValueError):
        s.run(f, np.zeros(1, dtype=[("bar", "<i8"), ("signal", "i1")]))
    with pytest.raises(ValueError):
        sim(window=(0, 9)).run(f, events())
    with pytest.raises(ValueError):
        sim().run(f, events(), quote_rate=np.ones(4))
    with pytest.raises(ValueError):
        sim(max_hold_bars=0)
    assert sim().run(f, events()).trades == []


def test_empty_window_gives_no_trades():
    res = sim(window=(3, 3)).run(frame(flat_rows(5)), events((1, 1, 5.0, 1.0, False)))
    assert res.trades == [] and res.equity.tolist() == [10_000.0]


# ── output ──────────────────────────────────────────────────────────────────

def test_to_backtest_result_metadata_and_notes():
    rows = flat_rows(8)
    rows[2] = (2003.0, 2010.5, 2002.0, 2009.0)
    res = sim(cost_mult=1.5, strategy_name="lab_x_xauusd", timeframe="M5").run(
        frame(rows), events((0, 1, 5.0, 10.0, False)))
    br = to_backtest_result(res, lab_id="lab_x_xauusd", symbol="XAUUSD", timeframe="M5", stage="DEV",
                            code_sha256="ab" * 32, params={"n": np.int64(5), "rr": 2.0},
                            fx_desc="quote currency USD (no conversion)")
    assert isinstance(br, BacktestResult) and len(br.trades) == 1
    m = br.metadata
    assert (m.strategy_name, m.engine, m.symbol, m.timeframe) == ("lab_x_xauusd", "python:signal", "XAUUSD", "M5")
    assert (m.start_date, m.end_date) == ("2024-09-02T00:00:00", "2024-09-02T00:35:00")
    assert m.spread_model == "constant" and m.spread_pips is None and m.currency == "USD"
    assert m.commission_model == G.COMMISSION_MODEL
    ca = cost_assumptions("XAUUSD")
    assert m.commission_rate == pytest.approx(ca["commission_rate"] * 1.5)
    assert m.slippage_pips == pytest.approx(ca["slippage_pips"] * 1.5)
    assert m.initial_balance == G.INITIAL_BALANCE and m.execution_latency_ms == 0
    notes = json.loads(m.notes)
    assert set(notes) == {"schema", "code_sha256", "params", "stage", "window", "cost_mult", "delay",
                          "risk_pct", "untradable_stop", "n_signals", "n_entry_attempts",
                          "window_end_closes", "halted", "fx_desc", "gates_version",
                          "protocol_version", "engine_version"}
    assert notes["schema"] == RUN_NOTES_SCHEMA and notes["stage"] == "dev" and notes["cost_mult"] == 1.5
    assert notes["window"] == {"start": "2024-09-01T00:00:00", "stop": "2026-01-01T00:00:00",
                               "first_bar": "2024-09-02T00:00:00", "last_bar": "2024-09-02T00:35:00"}
    assert notes["params"] == {"n": 5, "rr": 2.0} and notes["engine_version"] == "signal_sim/1"
    assert m.notes == json.dumps(notes, sort_keys=True)
    assert br.trades[0].strategy_name == "lab_x_xauusd"
    json.dumps(br.to_dict())                                         # JSON-safe end to end
    hold = to_backtest_result(res, lab_id="lab_x_xauusd", symbol="XAUUSD", timeframe="M5", stage="holdout",
                              code_sha256="ab" * 32, params={}, fx_desc="x")
    assert json.loads(hold.metadata.notes)["window"]["stop"] is None


def test_sim_result_notes_are_json_safe():
    res = sim().run(frame(flat_rows(6)), events((0, 1, 5.0, 10.0, False)))
    assert isinstance(res, SimResult)
    json.dumps(res.notes)
    for k in ("cost_mult", "delay_bars", "risk_pct", "initial_balance", "max_hold_bars", "allow_short",
              "window", "untradable_stop", "n_signals", "n_entry_attempts", "window_end_closes",
              "halted", "ruin_floor"):
        assert k in res.notes


# ── performance ─────────────────────────────────────────────────────────────

def test_100k_bars_300_trades_under_50ms():
    n = 100_000
    rng = np.random.default_rng(0)
    c = 2000 + np.cumsum(rng.normal(0, 0.5, n))
    o = np.r_[c[0], c[:-1]]
    f = frame(np.c_[o, np.maximum(o, c) + rng.uniform(0, 0.5, n), np.minimum(o, c) - rng.uniform(0, 0.5, n), c])
    bars = np.sort(rng.choice(n - 10, 400, replace=False))
    ev = np.zeros(len(bars), dtype=EVENTS_DTYPE)
    ev["bar"], ev["signal"], ev["stop_dist"], ev["tp_dist"] = bars, rng.choice([-1, 1], len(bars)), 3.0, 6.0
    s = sim(max_hold_bars=200)
    best = math.inf
    for _ in range(10):
        t0 = time.perf_counter()
        res = s.run(f, ev)
        best = min(best, time.perf_counter() - t0)
    assert len(res.trades) >= 300
    assert best < 0.050, f"best of 10 took {best * 1000:.1f} ms"


# ── adversarial review: independent hand computations ──────────────────────

def test_review_eurusd_short_stop_hand_computed():
    """EURUSD short, 20-pip stop: 0.5 lot, gross -100, cost 55 000 x 1.2 bps (6.60) + 5pt x 2 x 0.5 lot (5.00)."""
    eur = get_spec("EURUSD")
    rows = [(1.10000, 1.10050, 1.09950, 1.10000)] * 6
    rows[3] = (1.10100, 1.10250, 1.10080, 1.10200)          # high 1.1025 >= SL 1.1020 (open below it)
    t = sim(eur).run(frame(rows), events((1, -1, 0.0020, 0.0040, False))).trades[0]
    assert t.custom_fields["entry_bar"] == 2 and t.entry_price == 1.10000
    assert t.position_size == pytest.approx(0.5)
    assert t.exit_reason == ExitReason.STOP_LOSS and t.exit_price == pytest.approx(1.10200)
    assert t.gross_pnl == pytest.approx(-100.0)
    assert t.commission == pytest.approx(6.6 + 5.0)
    assert t.net_pnl == pytest.approx(-111.6)
    assert t.exit_time == "2024-09-02T00:15:00"


def test_review_ger40_long_eur_quote_hand_computed():
    """GER40 (EUR-quoted, contract 1, point 0.1, 2 bps, 20pt slippage) at EURUSD 1.10."""
    ger = get_spec("GER40")
    rows = [(18000.0, 18010.0, 17990.0, 18000.0)] * 6
    rows[2] = (18020.0, 18105.0, 18015.0, 18090.0)          # TP 18100 touched
    rate = np.full(6, 1.10)
    t = sim(ger).run(frame(rows), events((0, 1, 50.0, 100.0, False)), quote_rate=rate).trades[0]
    # lots: $100 / (50 x 1 x 1.10) = 1.818 -> 1.82 (lot step 0.01)
    assert t.position_size == pytest.approx(1.82)
    assert t.gross_pnl == pytest.approx(100.0 * 1.82 * 1.10)                  # 200.20 USD
    # cost: 18 000 x 1.82 x 1.10 = 36 036 USD x 2 bps = 7.2072; 20 x 0.1 x 2 x 1.82 x 1.10 = 8.008
    assert t.commission == pytest.approx(7.2072 + 8.008)
    assert t.net_pnl == pytest.approx(200.2 - 15.2152)
    with pytest.raises(ValueError):
        sim(ger).run(frame(rows), events((0, 1, 50.0, 100.0, False)))      # EUR quote needs rates


def test_review_leverage_cap_limits_lots():
    """A 0.5 stop on XAUUSD asks for 2.0 lots; 20x leverage on $10k at 2000 caps it at 1.0."""
    rows = flat_rows(6, width=0.1)
    t = sim(max_hold_bars=2).run(frame(rows), events((0, 1, 0.5, np.nan, False))).trades[0]
    assert t.position_size == pytest.approx(1.0)
    assert t.commission == pytest.approx(200_000 * 3e-4 + 5 * 0.01 * 2 * 1.0 * 100)   # 60 + 10


def test_review_opposite_signal_on_hold_limit_bar_is_a_timeout():
    """Order b (hold limit) beats d (signal exit): an exit on bar f+max_hold-1 closes at that close."""
    rows = [(2000.0 + k, 2001.0 + k, 1999.0 + k, 2000.5 + k) for k in range(10)]
    t = sim(max_hold_bars=2).run(frame(rows), events((0, 1, 5.0, 50.0, False), (2, 0, 0, 0, True))).trades[0]
    assert t.exit_reason == ExitReason.TIMEOUT and t.custom_fields["exit_bar"] == 2 and t.exit_price == 2002.5
