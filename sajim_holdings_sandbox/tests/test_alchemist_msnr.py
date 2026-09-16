"""
========================================================================================
ALCHEMIST MSNR & LIT (LIQUIDITY INDUCEMENT THEORY) STRATEGY ENGINE
========================================================================================
Translates the 41-page Alchemist MSNR manual ("Alchemist MSNR (1).pdf") into Python:
  - Market Structure: Rolling lookahead-free swing highs/lows (MSNR key levels)
  - Inducement & Target Sweep (TS / Stop Hunt):
      * Bullish: Price sweeps below prior swing low/inducement, then closes back inside.
      * Bearish: Price sweeps above prior swing high/inducement, then closes back inside.
  - Rejection Confirmation:
      * Pin bar (wick >= 50% of candle range rejecting the sweep) OR
      * Engulfing candle in the reversal direction.
  - HTF Trend Alignment:
      * Optional H4 or H1 trend filter (EMA 50 > EMA 200) or Standalone (NONE).
  - Risk Management:
      * Canonical "SL 10 PIPS" (fixed 10 pips) OR Structural (beyond sweep wick + buffer).
      * Reward-to-Risk (RR): 2.0R to 3.0R.
      * Realistic spread friction deduction from SPREAD_MAP.
      * Friday 20:00 weekend protection exit.
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
    "GBPJPY": 0.01,   "EURGBP": 0.0001, "XAUUSD": 0.10,    "XAGUSD": 0.01,
    "BTCUSD": 1.0,    "ETHUSD": 0.1,     "LTCUSD": 0.01,    "XRPUSD": 0.0001,
    "US30": 1.0,      "US500": 0.1,      "GER40": 1.0,      "UK100": 1.0,
}


@dataclass
class MsnrTrade:
    entry_bar: int
    exit_bar: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    symbol: str
    direction: str       # "LONG" or "SHORT"
    entry_price: float
    initial_sl: float
    initial_tp: float
    exit_price: float
    pnl_r: float
    exit_reason: str     # "FULL_TP", "FULL_SL", "FRIDAY_CLOSE", "MAX_BARS"
    setup_type: str      # "TS_PINBAR", "TS_ENGULFING"


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


def find_pivots(high: np.ndarray, low: np.ndarray, swing_len: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    """
    Lookahead-free rolling swing high and low detection.
    A pivot at bar k is confirmed at bar k + swing_len.
    Returns array of last confirmed swing high and low at each bar.
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


def run_alchemist_msnr_backtest(
    symbol: str = "XAUUSD",
    tf: str = "M15",
    htf: str = "H1",          # "H4", "H1", or "NONE"
    rr_ratio: float = 2.0,
    swing_len: int = 5,
    sl_mode: str = "structural",  # "fixed10" or "structural"
    close_by_friday: bool = True,
    friday_hour: int = 20,
    max_trades_day: int = 3,
    risk_pct: float = 0.005,      # 0.5% standard
    initial_balance: float = 1000.0,
    max_holding_bars: int = 100,
) -> Dict[str, Any]:
    # 1. Load Execution TF Data
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

    # 2. HTF Trend Setup (if requested)
    use_htf = (htf and htf.upper() != "NONE")
    htf_bias_aligned = np.ones(len(df_tf), dtype=int)  # 1 = Bull, -1 = Bear, 0 = Neutral

    if use_htf:
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

        htf_closes = df_htf["close"].values
        htf_times = df_htf["time"].values
        htf_ema50 = compute_ema(htf_closes, 50)
        htf_ema200 = compute_ema(htf_closes, 200)

        # Merge HTF bias into TF using searchsorted (strictly lookahead-free)
        tf_times = df_tf["time"].values
        htf_indices = np.searchsorted(htf_times, tf_times, side="right") - 1

        htf_bias = np.zeros(len(df_tf), dtype=int)
        for i, idx in enumerate(htf_indices):
            if idx >= 199:  # After EMA 200 warms up
                if htf_ema50[idx] > htf_ema200[idx]:
                    htf_bias[i] = 1   # Bullish
                elif htf_ema50[idx] < htf_ema200[idx]:
                    htf_bias[i] = -1  # Bearish
        htf_bias_aligned = htf_bias
    else:
        # Standalone: allow both directions
        htf_bias_aligned = np.ones(len(df_tf), dtype=int) * 999  # Signal that HTF is agnostic

    # 3. Detect Lookahead-Free Swing Levels
    opens = df_tf["open"].values
    highs = df_tf["high"].values
    lows = df_tf["low"].values
    closes = df_tf["close"].values
    times = df_tf["time"].values
    n_bars = len(df_tf)

    swing_highs, swing_lows = find_pivots(highs, lows, swing_len=swing_len)

    spread = SPREAD_MAP.get(symbol, 0.00015)
    pip_size = PIP_MAP.get(symbol, 0.0001)

    # 4. Simulation Engine
    trades: List[MsnrTrade] = []
    in_trade = False
    trade_dir = ""
    entry_bar = -1
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    setup_tag = ""
    current_day = None
    trades_today = 0

    start_idx = max(swing_len * 3, 200 if use_htf else 20)

    for i in range(start_idx, n_bars):
        bar_time = pd.Timestamp(times[i])
        bar_day = bar_time.date()
        if bar_day != current_day:
            current_day = bar_day
            trades_today = 0

        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        prev_o, prev_h, prev_l, prev_c = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]

        # Check in-trade position management first
        if in_trade:
            # Check Friday close
            if close_by_friday and bar_time.weekday() == 4 and bar_time.hour >= friday_hour:
                exit_p = c - (spread / 2.0) if trade_dir == "LONG" else c + (spread / 2.0)
                risk_dist = abs(entry_price - sl_price)
                raw_pnl = (exit_p - entry_price) if trade_dir == "LONG" else (entry_price - exit_p)
                pnl_r = raw_pnl / risk_dist if risk_dist > 0 else 0.0
                trades.append(MsnrTrade(
                    entry_bar=entry_bar, exit_bar=i,
                    entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                    symbol=symbol, direction=trade_dir, entry_price=entry_price,
                    initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                    pnl_r=pnl_r, exit_reason="FRIDAY_CLOSE", setup_type=setup_tag
                ))
                in_trade = False
                continue

            # Check Max Bars
            if (i - entry_bar) >= max_holding_bars:
                exit_p = c - (spread / 2.0) if trade_dir == "LONG" else c + (spread / 2.0)
                risk_dist = abs(entry_price - sl_price)
                raw_pnl = (exit_p - entry_price) if trade_dir == "LONG" else (entry_price - exit_p)
                pnl_r = raw_pnl / risk_dist if risk_dist > 0 else 0.0
                trades.append(MsnrTrade(
                    entry_bar=entry_bar, exit_bar=i,
                    entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                    symbol=symbol, direction=trade_dir, entry_price=entry_price,
                    initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                    pnl_r=pnl_r, exit_reason="MAX_BARS", setup_type=setup_tag
                ))
                in_trade = False
                continue

            # Check TP / SL hit
            if trade_dir == "LONG":
                sl_hit = (l <= sl_price)
                tp_hit = (h >= tp_price)

                if sl_hit and tp_hit:
                    # Conservative: assume SL hit first
                    exit_p = sl_price - (spread / 2.0)
                    pnl_r = -1.0
                    trades.append(MsnrTrade(
                        entry_bar=entry_bar, exit_bar=i,
                        entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                        symbol=symbol, direction=trade_dir, entry_price=entry_price,
                        initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                        pnl_r=pnl_r, exit_reason="FULL_SL", setup_type=setup_tag
                    ))
                    in_trade = False
                elif sl_hit:
                    exit_p = sl_price - (spread / 2.0)
                    pnl_r = -1.0
                    trades.append(MsnrTrade(
                        entry_bar=entry_bar, exit_bar=i,
                        entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                        symbol=symbol, direction=trade_dir, entry_price=entry_price,
                        initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                        pnl_r=pnl_r, exit_reason="FULL_SL", setup_type=setup_tag
                    ))
                    in_trade = False
                elif tp_hit:
                    exit_p = tp_price - (spread / 2.0)
                    pnl_r = rr_ratio
                    trades.append(MsnrTrade(
                        entry_bar=entry_bar, exit_bar=i,
                        entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                        symbol=symbol, direction=trade_dir, entry_price=entry_price,
                        initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                        pnl_r=pnl_r, exit_reason="FULL_TP", setup_type=setup_tag
                    ))
                    in_trade = False

            elif trade_dir == "SHORT":
                sl_hit = (h >= sl_price)
                tp_hit = (l <= tp_price)

                if sl_hit and tp_hit:
                    # Conservative: assume SL hit first
                    exit_p = sl_price + (spread / 2.0)
                    pnl_r = -1.0
                    trades.append(MsnrTrade(
                        entry_bar=entry_bar, exit_bar=i,
                        entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                        symbol=symbol, direction=trade_dir, entry_price=entry_price,
                        initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                        pnl_r=pnl_r, exit_reason="FULL_SL", setup_type=setup_tag
                    ))
                    in_trade = False
                elif sl_hit:
                    exit_p = sl_price + (spread / 2.0)
                    pnl_r = -1.0
                    trades.append(MsnrTrade(
                        entry_bar=entry_bar, exit_bar=i,
                        entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                        symbol=symbol, direction=trade_dir, entry_price=entry_price,
                        initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                        pnl_r=pnl_r, exit_reason="FULL_SL", setup_type=setup_tag
                    ))
                    in_trade = False
                elif tp_hit:
                    exit_p = tp_price + (spread / 2.0)
                    pnl_r = rr_ratio
                    trades.append(MsnrTrade(
                        entry_bar=entry_bar, exit_bar=i,
                        entry_time=pd.Timestamp(times[entry_bar]), exit_time=bar_time,
                        symbol=symbol, direction=trade_dir, entry_price=entry_price,
                        initial_sl=sl_price, initial_tp=tp_price, exit_price=exit_p,
                        pnl_r=pnl_r, exit_reason="FULL_TP", setup_type=setup_tag
                    ))
                    in_trade = False

            continue

        # If not in trade, search for MSNR + LIT Setup on bar i
        if trades_today >= max_trades_day:
            continue

        cur_htf_bias = htf_bias_aligned[i]
        ref_sh = swing_highs[i - 1]
        ref_sl = swing_lows[i - 1]

        if np.isnan(ref_sh) or np.isnan(ref_sl):
            continue

        candle_range = h - l
        if candle_range <= 0:
            continue

        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        # ----------------------------------------------------
        # SETUP 1: BULLISH TS (Target Sweep of Liquidity / Stop Hunt)
        # Condition:
        #   - HTF Bias allows Long (cur_htf_bias in [1, 999])
        #   - Liquidity Sweep: bar i low dips below prior swing low (l < ref_sl)
        #   - Rejection: bar i closes back above the swing low (c > ref_sl)
        #   - Confirmation:
        #       a) Pin bar: lower_wick >= 0.45 * candle_range and c > l + 0.3 * candle_range
        #       b) Bullish Engulfing: c > o and c > prev_c and o <= prev_l
        # ----------------------------------------------------
        long_ts = (l < ref_sl) and (c > ref_sl)
        is_bull_pin = (lower_wick >= 0.45 * candle_range) and (c > (l + 0.35 * candle_range))
        is_bull_engulf = (c > o) and (prev_c < prev_o) and (c >= prev_o)

        if (cur_htf_bias in [1, 999]) and long_ts and (is_bull_pin or is_bull_engulf):
            raw_entry = c + (spread / 2.0)

            if sl_mode == "fixed10":
                calc_sl = raw_entry - (10.0 * pip_size)
            else:
                # Structural: beyond sweep low + buffer (1 pip)
                calc_sl = l - (1.0 * pip_size)

            risk_dist = raw_entry - calc_sl
            min_sl_dist = 3.0 * pip_size
            max_sl_dist = 50.0 * pip_size if "USD" in symbol else raw_entry * 0.02

            if min_sl_dist <= risk_dist <= max_sl_dist:
                calc_tp = raw_entry + (risk_dist * rr_ratio)
                in_trade = True
                trade_dir = "LONG"
                entry_bar = i
                entry_price = raw_entry
                sl_price = calc_sl
                tp_price = calc_tp
                setup_tag = "TS_PINBAR" if is_bull_pin else "TS_ENGULFING"
                trades_today += 1
                continue

        # ----------------------------------------------------
        # SETUP 2: BEARISH TS (Target Sweep of Liquidity / Stop Hunt)
        # Condition:
        #   - HTF Bias allows Short (cur_htf_bias in [-1, 999])
        #   - Liquidity Sweep: bar i high spikes above prior swing high (h > ref_sh)
        #   - Rejection: bar i closes back below the swing high (c < ref_sh)
        #   - Confirmation:
        #       a) Pin bar / Shooting star: upper_wick >= 0.45 * candle_range and c < (h - 0.35 * candle_range)
        #       b) Bearish Engulfing: c < o and prev_c > prev_o and c <= prev_o
        # ----------------------------------------------------
        short_ts = (h > ref_sh) and (c < ref_sh)
        is_bear_pin = (upper_wick >= 0.45 * candle_range) and (c < (h - 0.35 * candle_range))
        is_bear_engulf = (c < o) and (prev_c > prev_o) and (c <= prev_o)

        if (cur_htf_bias in [-1, 999]) and short_ts and (is_bear_pin or is_bear_engulf):
            raw_entry = c - (spread / 2.0)

            if sl_mode == "fixed10":
                calc_sl = raw_entry + (10.0 * pip_size)
            else:
                # Structural: beyond sweep high + buffer (1 pip)
                calc_sl = h + (1.0 * pip_size)

            risk_dist = calc_sl - raw_entry
            min_sl_dist = 3.0 * pip_size
            max_sl_dist = 50.0 * pip_size if "USD" in symbol else raw_entry * 0.02

            if min_sl_dist <= risk_dist <= max_sl_dist:
                calc_tp = raw_entry - (risk_dist * rr_ratio)
                in_trade = True
                trade_dir = "SHORT"
                entry_bar = i
                entry_price = raw_entry
                sl_price = calc_sl
                tp_price = calc_tp
                setup_tag = "TS_PINBAR" if is_bear_pin else "TS_ENGULFING"
                trades_today += 1
                continue

    # 5. Summary Performance Metrics
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "symbol": symbol, "tf": tf, "htf": htf, "sl_mode": sl_mode,
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "expectancy_r": 0.0, "total_pnl_r": 0.0, "max_drawdown_r": 0.0,
            "trades": []
        }

    pnls = [t.pnl_r for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = (len(wins) / total_trades) * 100.0
    total_pnl_r = sum(pnls)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    expectancy_r = total_pnl_r / total_trades

    # Drawdown calculation
    equity_curve = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity_curve)
    drawdowns = peak - equity_curve
    max_dd_r = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    return {
        "symbol": symbol,
        "tf": tf,
        "htf": htf,
        "sl_mode": sl_mode,
        "bars_scanned": n_bars,
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_r": round(expectancy_r, 3),
        "total_pnl_r": round(total_pnl_r, 2),
        "max_drawdown_r": round(max_dd_r, 2),
        "exit_reasons": exit_reasons,
        "trades": trades,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alchemist MSNR Strategy Backtest")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol")
    parser.add_argument("--tf", type=str, default="M15", help="Execution timeframe")
    parser.add_argument("--htf", type=str, default="H1", help="HTF Bias: H4, H1, or NONE")
    parser.add_argument("--rr", type=float, default=2.0, help="Reward:Risk ratio")
    parser.add_argument("--swing-len", type=int, default=5, help="Swing lookback")
    parser.add_argument("--sl-mode", type=str, default="structural", choices=["structural", "fixed10"], help="SL Mode")
    args = parser.parse_args()

    print("=" * 70)
    print(f"RUNNING ALCHEMIST MSNR BACKTEST: {args.symbol} {args.tf} (HTF: {args.htf}, SL: {args.sl_mode}, RR: {args.rr})")
    print("=" * 70)
    res = run_alchemist_msnr_backtest(
        symbol=args.symbol, tf=args.tf, htf=args.htf,
        rr_ratio=args.rr, swing_len=args.swing_len, sl_mode=args.sl_mode
    )

    print(f"Bars Scanned:     {res['bars_scanned']}")
    print(f"Total Trades:     {res['total_trades']}")
    print(f"Win Rate:         {res['win_rate']}%")
    print(f"Profit Factor:    {res['profit_factor']}")
    print(f"Expectancy:       {res['expectancy_r']:+.3f} R")
    print(f"Net PnL:          {res['total_pnl_r']:+.2f} R")
    print(f"Max Drawdown:     {res['max_drawdown_r']:.2f} R")
    print(f"Exit Reasons:     {res['exit_reasons']}")
    print("=" * 70)
