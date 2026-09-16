"""
========================================================================================
PINE SCRIPT PORT: EMA PULLBACK SYSTEM — STRATEGY
========================================================================================
Faithfully translates the Pine Script v5 "EMA Pullback System - Strategy" to Python:
  - H4 Trend Filter: EMA 50 > EMA 200 (Uptrend) / EMA 50 < EMA 200 (Downtrend)
  - H1 Micro Retracement: Low <= EMA 20 and High >= EMA 20
  - Candle Trigger: Bullish / Bearish Engulfing
  - Structural Swing Pivots: 8-bar lookback (ta.pivothigh / ta.pivotlow)
  - Structural SL: last confirmed swing low (Long) / swing high (Short)
  - Room-to-Target Trimming:
      If opposing swing wall is within target path and room / full_dist < 0.8:
          Trim TP to wall - 50 * mintick
      Only accept trade if trimmed TP >= 1.0 R:R
  - Max trades per day: 2
  - Friday 20:00 forced exit (weekend liquidity protection)
  - Realistic spread deduction & R-multiple / USD accounting ($1,000 base)
========================================================================================
"""

import os
import sys
import argparse
import math
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any
import numpy as np
import pandas as pd

# Setup paths
SANDBOX_ROOT = Path(__file__).parent.parent
LAB_ROOT = SANDBOX_ROOT.parent
PARQUET_DIR = LAB_ROOT / "data" / "parquet"

SPREAD_MAP = {
    "EURUSD": 0.00010, "GBPUSD": 0.00014, "USDCAD": 0.00012, "USDJPY": 0.012,
    "USDCHF": 0.00013, "AUDUSD": 0.00012, "NZDUSD": 0.00015, "EURJPY": 0.015,
    "GBPJPY": 0.020,   "EURGBP": 0.00014, "XAUUSD": 0.35,    "XAGUSD": 0.020,
    "BTCUSD": 15.0,    "ETHUSD": 0.80,    "LTCUSD": 0.05,    "XRPUSD": 0.0003,
    "US30": 2.5,       "US500": 0.5,      "GER40": 1.5,      "UK100": 1.0,
}

PIP_MAP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01,
    "USDCHF": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001, "EURJPY": 0.01,
    "GBPJPY": 0.01,   "EURGBP": 0.0001, "XAUUSD": 0.1,     "XAGUSD": 0.01,
    "BTCUSD": 1.0,    "ETHUSD": 0.1,     "LTCUSD": 0.01,    "XRPUSD": 0.0001,
    "US30": 1.0,      "US500": 0.1,      "GER40": 1.0,      "UK100": 1.0,
}


@dataclass
class EmaPullbackTrade:
    entry_bar: int
    exit_bar: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    symbol: str
    direction: str       # "LONG" or "SHORT"
    entry_price: float
    initial_sl: float
    initial_tp: float
    trimmed_tp: bool
    exit_price: float
    pnl_r: float
    exit_reason: str     # "FULL_TP", "FULL_SL", "FRIDAY_CLOSE", "MAX_BARS"


def compute_ema(series: np.ndarray, period: int) -> np.ndarray:
    n = len(series)
    ema = np.zeros(n)
    if n < period:
        return ema
    alpha = 2.0 / (period + 1.0)
    ema[period - 1] = np.mean(series[:period])
    for i in range(period, n):
        ema[i] = alpha * series[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def find_pivots(high: np.ndarray, low: np.ndarray, swing_len: int = 8) -> Tuple[np.ndarray, np.ndarray]:
    """
    Replicates TradingView ta.pivothigh(high, 8, 8) and ta.pivotlow(low, 8, 8).
    A pivot at bar k is confirmed at bar k + swing_len.
    Returns array of last confirmed swing high and low at each bar.
    Vectorized with pandas rolling window for rapid multi-timeframe processing.
    """
    window = 2 * swing_len + 1
    s_h = pd.Series(high)
    s_l = pd.Series(low)

    roll_max = s_h.rolling(window, center=True).max()
    roll_min = s_l.rolling(window, center=True).min()

    is_pivot_h = (s_h == roll_max)
    is_pivot_l = (s_l == roll_min)

    sh = pd.Series(np.where(is_pivot_h, s_h, np.nan)).shift(swing_len).ffill().values
    sl = pd.Series(np.where(is_pivot_l, s_l, np.nan)).shift(swing_len).ffill().values

    return sh, sl



def run_ema_pullback_backtest(
    symbol: str = "EURUSD",
    tf: str = "H1",
    htf: str = "H4",
    rr_ratio: float = 2.0,
    swing_len: int = 8,
    min_room_ratio: float = 0.8,
    use_room_filter: bool = True,
    close_by_friday: bool = True,
    friday_hour: int = 20,
    max_trades_day: int = 2,
    risk_pct: float = 0.005,  # 0.5% Pine default
    initial_balance: float = 1000.0,
) -> Dict[str, Any]:
    tf_path = PARQUET_DIR / symbol / f"{tf}.parquet"
    if not tf_path.exists():
        tf_path = PARQUET_DIR / f"{symbol} {tf}.parquet"
    if not tf_path.exists():
        raise FileNotFoundError(f"Cannot find {tf} data for {symbol}")

    df_tf = pd.read_parquet(tf_path)
    df_tf.columns = [c.lower() for c in df_tf.columns]
    df_tf["time"] = pd.to_datetime(df_tf["time"])
    df_tf.sort_values("time", inplace=True)
    df_tf.reset_index(drop=True, inplace=True)

    htf_path = PARQUET_DIR / symbol / f"{htf}.parquet"
    if not htf_path.exists():
        htf_path = PARQUET_DIR / f"{symbol} {htf}.parquet"
    if not htf_path.exists():
        raise FileNotFoundError(f"Cannot find {htf} data for {symbol}")

    df_htf = pd.read_parquet(htf_path)
    df_htf.columns = [c.lower() for c in df_htf.columns]
    df_htf["time"] = pd.to_datetime(df_htf["time"])
    df_htf.sort_values("time", inplace=True)
    df_htf.reset_index(drop=True, inplace=True)

    # 1. Compute HTF EMAs
    htf_closes = df_htf["close"].values
    htf_times = df_htf["time"].values
    n_htf = len(htf_closes)
    htf_ema50 = compute_ema(htf_closes, 50)
    htf_ema200 = compute_ema(htf_closes, 200)

    # 2. Compute TF indicators
    tf_opens = df_tf["open"].values
    tf_highs = df_tf["high"].values
    tf_lows = df_tf["low"].values
    tf_closes = df_tf["close"].values
    tf_times = df_tf["time"].values
    n_tf = len(tf_closes)

    tf_ema20 = compute_ema(tf_closes, 20)
    last_swing_high, last_swing_low = find_pivots(tf_highs, tf_lows, swing_len=swing_len)

    # 3. Synchronize HTF trend strictly without lookahead
    # For bar at time t, use the most recently closed HTF bar where htf_time <= t
    htf_idx = np.searchsorted(htf_times, tf_times, side="right") - 1
    valid_htf = (htf_idx >= 200) & (htf_idx < n_htf)
    safe_idx = np.clip(htf_idx, 0, n_htf - 1)
    htf_uptrend = np.where(valid_htf, htf_ema50[safe_idx] > htf_ema200[safe_idx], False)
    htf_downtrend = np.where(valid_htf, htf_ema50[safe_idx] < htf_ema200[safe_idx], False)

    # Pre-filter candidate trigger bars vectorized
    touched_ema = (tf_lows <= tf_ema20) & (tf_highs >= tf_ema20)
    prev_opens = np.roll(tf_opens, 1)
    prev_closes = np.roll(tf_closes, 1)
    prev_opens[0] = np.nan
    prev_closes[0] = np.nan

    bullish_engulf = (tf_closes > tf_opens) & (prev_closes < prev_opens) & (tf_closes > prev_opens) & (tf_opens < prev_closes)
    bearish_engulf = (tf_closes < tf_opens) & (prev_closes > prev_opens) & (tf_closes < prev_opens) & (tf_opens > prev_closes)

    raw_long_arr = htf_uptrend & touched_ema & bullish_engulf
    raw_short_arr = htf_downtrend & touched_ema & bearish_engulf
    candidate_bars = np.where(raw_long_arr | raw_short_arr)[0]

    # Sizing & parameters
    pip_size = PIP_MAP.get(symbol, 0.0001)
    spread_cost = SPREAD_MAP.get(symbol, 0.00010)
    mintick = 0.00001 if "JPY" not in symbol and "XAU" not in symbol else (0.001 if "JPY" in symbol else 0.01)
    wall_buffer = mintick * 50
    tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}.get(tf, 60)
    max_holding_bars = int(5 * 24 * (60 / tf_minutes))  # 5 trading days holding horizon

    trades: List[EmaPullbackTrade] = []
    in_trade_until = -1
    daily_trades_count = 0
    current_day = None

    for i in candidate_bars:
        if i < max(200, swing_len * 2 + 5) or i >= n_tf - 1:
            continue

        bar_time = pd.to_datetime(tf_times[i])
        bar_day = bar_time.date()

        if bar_day != current_day:
            current_day = bar_day
            daily_trades_count = 0

        # Friday close check: no new entries on Friday >= 20:00
        is_friday = (bar_time.weekday() == 4)  # 4 = Friday
        friday_close_time = is_friday and (bar_time.hour >= friday_hour)

        if i < in_trade_until or friday_close_time or daily_trades_count >= max_trades_day:
            continue

        cur_p = tf_closes[i]
        raw_long = raw_long_arr[i]
        raw_short = raw_short_arr[i]

        long_signal = False
        short_signal = False
        long_sl = np.nan
        long_tp = np.nan
        short_sl = np.nan
        short_tp = np.nan
        trimmed_tp = False

        sw_low = last_swing_low[i]
        sw_high = last_swing_high[i]

        # Step 4: Long SL/TP
        if raw_long and not np.isnan(sw_low):
            entry = cur_p
            sl = sw_low
            stop_dist = entry - sl
            if stop_dist > 0:
                tp = entry + stop_dist * rr_ratio
                if use_room_filter and not np.isnan(sw_high) and sw_high > entry and sw_high < tp:
                    full_dist = tp - entry
                    room_dist = sw_high - entry
                    if (room_dist / full_dist) < min_room_ratio:
                        tp = sw_high - wall_buffer
                        trimmed_tp = True
                if (tp - entry) >= stop_dist:
                    long_sl = sl
                    long_tp = tp
                    long_signal = True

        # Step 4: Short SL/TP
        if raw_short and not np.isnan(sw_high):
            entry = cur_p
            sl = sw_high
            stop_dist = sl - entry
            if stop_dist > 0:
                tp = entry - stop_dist * rr_ratio
                if use_room_filter and not np.isnan(sw_low) and sw_low < entry and sw_low > tp:
                    full_dist = entry - tp
                    room_dist = entry - sw_low
                    if (room_dist / full_dist) < min_room_ratio:
                        tp = sw_low + wall_buffer
                        trimmed_tp = True
                if (entry - tp) >= stop_dist:
                    short_sl = sl
                    short_tp = tp
                    short_signal = True

        if not long_signal and not short_signal:
            continue

        direction = "LONG" if long_signal else "SHORT"
        sl = long_sl if long_signal else short_sl
        tp = long_tp if long_signal else short_tp
        entry_price = cur_p
        risk_dist = abs(entry_price - sl)
        target_rr = abs(tp - entry_price) / risk_dist

        entry_bar = i
        exit_bar = entry_bar
        exit_price = entry_price
        exit_reason = "MAX_BARS"
        pnl_r = 0.0

        # Bar-by-bar progression on subsequent bars
        for j in range(i + 1, min(n_tf, i + 1 + max_holding_bars)):
            t_j = pd.to_datetime(tf_times[j])
            h_j = tf_highs[j]
            l_j = tf_lows[j]

            # Friday forced close at 20:00
            if close_by_friday and (t_j.weekday() == 4) and (t_j.hour >= friday_hour):
                exit_bar = j
                exit_price = tf_closes[j]
                pnl_r = ((exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)) / risk_dist
                exit_reason = "FRIDAY_CLOSE"
                break

            if direction == "LONG":
                adverse_hit = (l_j <= sl)
                tp_hit = (h_j >= tp)
            else:
                adverse_hit = (h_j >= sl)
                tp_hit = (l_j <= tp)

            if adverse_hit:
                exit_bar = j
                exit_price = sl
                pnl_r = -1.0
                exit_reason = "FULL_SL"
                break

            if tp_hit:
                exit_bar = j
                exit_price = tp
                pnl_r = target_rr
                exit_reason = "FULL_TP"
                break
        else:
            exit_bar = min(n_tf - 1, i + max_holding_bars)
            exit_price = tf_closes[exit_bar]
            pnl_r = ((exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)) / risk_dist
            exit_reason = "MAX_BARS"

        friction_r = spread_cost / risk_dist
        net_pnl_r = round(pnl_r - friction_r, 3)

        trades.append(
            EmaPullbackTrade(
                entry_bar=entry_bar,
                exit_bar=exit_bar,
                entry_time=bar_time,
                exit_time=pd.to_datetime(tf_times[exit_bar]),
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                initial_sl=sl,
                initial_tp=tp,
                trimmed_tp=trimmed_tp,
                exit_price=exit_price,
                pnl_r=net_pnl_r,
                exit_reason=exit_reason,
            )
        )

        daily_trades_count += 1
        in_trade_until = exit_bar

    # Performance calculations
    total_trades = len(trades)
    if total_trades == 0:
        return {"symbol": symbol, "tf": tf, "total_trades": 0}

    pnl_series = [t.pnl_r for t in trades]
    wins = [r for r in pnl_series if r > 0]
    losses = [abs(r) for r in pnl_series if r < 0]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100.0
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    expectancy_r = float(np.mean(pnl_series))
    total_pnl_r = float(sum(pnl_series))

    # Dollar P&L on $1000 account
    dollar_per_r_05 = initial_balance * risk_pct           # $5.00 per R at 0.5%
    dollar_per_r_15 = initial_balance * 0.015              # $15.00 per R at 1.5%
    net_usd_05 = total_pnl_r * dollar_per_r_05
    net_usd_15 = total_pnl_r * dollar_per_r_15

    cum_pnl = np.cumsum(pnl_series)
    peak = np.maximum.accumulate(cum_pnl)
    drawdowns = peak - cum_pnl
    max_dd_r = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    max_dd_pct_05 = max_dd_r * (risk_pct * 100.0)
    max_dd_pct_15 = max_dd_r * 1.5

    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    trimmed_count = sum(1 for t in trades if t.trimmed_tp)

    return {
        "symbol": symbol,
        "tf": tf,
        "htf": htf,
        "bars_scanned": n_tf,
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_r": round(expectancy_r, 3),
        "total_pnl_r": round(total_pnl_r, 2),
        "net_usd_05_risk": round(net_usd_05, 2),
        "net_usd_15_risk": round(net_usd_15, 2),
        "max_drawdown_r": round(max_dd_r, 2),
        "max_drawdown_pct_05": round(max_dd_pct_05, 2),
        "max_drawdown_pct_15": round(max_dd_pct_15, 2),
        "exit_reasons": reasons,
        "trimmed_trades_count": trimmed_count,
        "trades": trades,
    }


def print_report(res: Dict):
    sym = res["symbol"]
    tf = res["tf"]
    htf = res.get("htf", "H4")
    print("=" * 80)
    print(f"       EMA PULLBACK SYSTEM (PINE SCRIPT PORT) VALIDATION: {sym} {tf} (HTF: {htf})")
    print("=" * 80)
    print(f"Bars Evaluated:         {res['bars_scanned']:,}")
    print(f"Total Completed Trades: {res['total_trades']:,}")
    if res["total_trades"] == 0:
        print("No trades generated.")
        return

    print(f"Win Rate:              {res['win_rate']:.1f}%")
    print(f"Profit Factor:         {res['profit_factor']:.2f}")
    print(f"Net Expectancy (R):    {res['expectancy_r']:+.3f} R")
    print(f"Total Net P&L:         {res['total_pnl_r']:+.1f} R")
    print(f"Max Drawdown:          {res['max_drawdown_r']:.2f} R")
    print("-" * 80)
    print("THEORETICAL $1,000 USD ACCOUNT TRANSLATION:")
    print(f"  - At 0.5% Risk ($5.00/R, Pine default):")
    print(f"      Net Profit:     +${res['net_usd_05_risk']:.2f} USD")
    print(f"      Max Drawdown:   {res['max_drawdown_pct_05']:.2f}% (${res['max_drawdown_r'] * 5.0:.2f})")
    print(f"  - At 1.5% Risk ($15.00/R, Fleet standard):")
    print(f"      Net Profit:     +${res['net_usd_15_risk']:.2f} USD")
    print(f"      Max Drawdown:   {res['max_drawdown_pct_15']:.2f}% (${res['max_drawdown_r'] * 15.0:.2f})")
    print("-" * 80)
    print("EXIT REASON DISTRIBUTION:")
    for reason, count in res["exit_reasons"].items():
        pct = (count / res["total_trades"]) * 100
        print(f"  - {reason:<16}: {count:>5} trades ({pct:5.1f}%)")
    print(f"  - Room Trimming Active: {res['trimmed_trades_count']} trades ({res['trimmed_trades_count']/res['total_trades']*100:.1f}%) had TP trimmed")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EMA Pullback System Backtester")
    parser.add_argument("--symbol", type=str, default="EURUSD")
    parser.add_argument("--tf", type=str, default="H1")
    parser.add_argument("--rr", type=float, default=2.0)
    parser.add_argument("--no-room-filter", action="store_true")
    parser.add_argument("--no-friday-close", action="store_true")
    args = parser.parse_args()

    res = run_ema_pullback_backtest(
        symbol=args.symbol,
        tf=args.tf,
        rr_ratio=args.rr,
        use_room_filter=not args.no_room_filter,
        close_by_friday=not args.no_friday_close,
    )
    print_report(res)
