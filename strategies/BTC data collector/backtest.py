#!/usr/bin/env python3
"""
BTC Mean-Reversion + Vol-Regime Backtester
============================================
Tests the strategy that Phase 0 data told us to build:
  - Fade overextended candles during high-volatility regimes
  - TP on reversion to moving average
  - SL on further extension
  - Stand down in low-vol regimes

Usage:
    python backtest.py                    # Run with defaults on BTCUSDT_1H
    python backtest.py --sweep            # Run parameter sweep to find optimal settings
    python backtest.py --file data/ETHUSDT_1H.csv   # Test on different instrument

Reads CSV from collect_btc_data.py format:
    epoch, datetime_utc, open, high, low, close, [volume, ...]
"""

import os
import sys
import csv
import math
import argparse
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

# Force UTF-8 output so Unicode symbols print on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class Candle:
    epoch: int
    dt: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    size_usd: float
    pnl_usd: float
    pnl_r: float            # P&L in R-multiples
    exit_reason: str         # "TP", "SL", "TIMEOUT"
    regime_atr: float        # ATR at entry
    candle_body: float       # Body size of trigger candle
    bars_held: int


@dataclass
class StrategyParams:
    # Volatility measurement
    atr_period: int = 20           # ATR lookback
    
    # Regime detection
    regime_ma_period: int = 50     # Slow MA of ATR for regime threshold
    regime_threshold: float = 1.0  # Trade when ATR > threshold * ATR_MA (1.0 = above average)
    
    # Mean target
    mean_ma_period: int = 20       # MA period for reversion target
    
    # Entry trigger
    body_atr_multiple: float = 1.5 # Candle body must exceed this * ATR to trigger
    
    # Exit logic
    tp_mean_fraction: float = 0.7  # TP at this fraction of distance to MA (1.0 = full reversion)
    sl_atr_multiple: float = 1.5   # SL at this * ATR beyond the candle extreme
    
    # Trade management
    max_bars_held: int = 12        # Force exit after N bars (prevent stuck trades)
    
    # Risk management
    risk_per_trade: float = 0.01   # 1% risk per trade
    starting_balance: float = 1000.0
    max_open_trades: int = 1       # One trade at a time
    
    # Fees
    fee_pct: float = 0.001         # 0.1% per side (Binance maker/taker)


# ─── Indicators ──────────────────────────────────────────────────────────────

def compute_atr(candles: list[Candle], period: int) -> list[float]:
    """Average True Range."""
    atr = [0.0] * len(candles)
    
    for i in range(1, len(candles)):
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        if i < period:
            # Simple average for warmup
            atr[i] = sum(
                max(
                    candles[j].high - candles[j].low,
                    abs(candles[j].high - candles[j - 1].close) if j > 0 else candles[j].high - candles[j].low,
                    abs(candles[j].low - candles[j - 1].close) if j > 0 else candles[j].high - candles[j].low,
                )
                for j in range(1, i + 1)
            ) / i if i > 0 else 0
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr) / period
    
    return atr


def compute_sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    sma = [0.0] * len(values)
    for i in range(len(values)):
        if i < period - 1:
            sma[i] = sum(values[:i + 1]) / (i + 1) if i > 0 else values[0]
        else:
            sma[i] = sum(values[i - period + 1:i + 1]) / period
    return sma


def compute_ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average — used for mean reversion target."""
    ema = [0.0] * len(values)
    multiplier = 2 / (period + 1)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = (values[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


# ─── Core Backtester ─────────────────────────────────────────────────────────

def load_candles(filepath: str) -> list[Candle]:
    """Load candle data from CSV."""
    candles = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                candles.append(Candle(
                    epoch=int(row["epoch"]),
                    dt=row["datetime_utc"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                ))
            except (ValueError, KeyError):
                continue
    return candles


def run_backtest(candles: list[Candle], params: StrategyParams) -> dict:
    """
    Run the mean-reversion + vol-regime backtest.
    
    Logic:
    1. Compute ATR and its MA (regime filter)
    2. Compute price EMA (mean reversion target)
    3. On each candle:
       a. Check if we're in a high-vol regime (ATR > threshold * ATR_MA)
       b. Check if candle body > body_atr_multiple * ATR (overextension)
       c. If bullish overextension → SHORT (fade it)
       d. If bearish overextension → LONG (fade it)
       e. TP = fraction of distance back to EMA
       f. SL = further extension by sl_atr_multiple * ATR
    4. Manage open trades: check SL/TP on each bar using high/low
    """
    
    n = len(candles)
    if n < params.regime_ma_period + 10:
        return {"error": "Not enough data"}
    
    # Compute indicators
    closes = [c.close for c in candles]
    atr = compute_atr(candles, params.atr_period)
    atr_ma = compute_sma(atr, params.regime_ma_period)
    price_ema = compute_ema(closes, params.mean_ma_period)
    
    # State
    balance = params.starting_balance
    peak_balance = balance
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    
    trades: list[Trade] = []
    open_trade: Optional[dict] = None
    warmup = max(params.regime_ma_period, params.atr_period, params.mean_ma_period) + 5
    
    # Equity curve (sampled at each candle)
    equity_curve = []
    
    # Monthly tracking
    monthly_returns = {}
    month_start_balance = balance
    current_month = None
    
    for i in range(warmup, n):
        candle = candles[i]
        
        # Track monthly returns
        month_key = candle.dt[:7]  # "YYYY-MM"
        if current_month is None:
            current_month = month_key
            month_start_balance = balance
        elif month_key != current_month:
            if month_start_balance > 0:
                monthly_returns[current_month] = (balance - month_start_balance) / month_start_balance
            current_month = month_key
            month_start_balance = balance
        
        # ── Manage open trade ──
        if open_trade is not None:
            open_trade["bars_held"] += 1
            hit_tp = False
            hit_sl = False
            exit_price = 0
            exit_reason = ""
            
            if open_trade["direction"] == "LONG":
                # Check SL first (worst case), then TP
                if candle.low <= open_trade["sl"]:
                    hit_sl = True
                    exit_price = open_trade["sl"]
                    exit_reason = "SL"
                elif candle.high >= open_trade["tp"]:
                    hit_tp = True
                    exit_price = open_trade["tp"]
                    exit_reason = "TP"
            else:  # SHORT
                if candle.high >= open_trade["sl"]:
                    hit_sl = True
                    exit_price = open_trade["sl"]
                    exit_reason = "SL"
                elif candle.low <= open_trade["tp"]:
                    hit_tp = True
                    exit_price = open_trade["tp"]
                    exit_reason = "TP"
            
            # Timeout check
            if not hit_tp and not hit_sl and open_trade["bars_held"] >= params.max_bars_held:
                exit_price = candle.close
                exit_reason = "TIMEOUT"
                hit_sl = True  # Treat as exit
            
            if hit_tp or hit_sl or exit_reason == "TIMEOUT":
                # Calculate P&L
                if open_trade["direction"] == "LONG":
                    raw_pnl_pct = (exit_price - open_trade["entry"]) / open_trade["entry"]
                else:
                    raw_pnl_pct = (open_trade["entry"] - exit_price) / open_trade["entry"]
                
                # Apply fees (entry + exit)
                net_pnl_pct = raw_pnl_pct - (2 * params.fee_pct)
                pnl_usd = open_trade["size_usd"] * net_pnl_pct
                
                # R-multiple: risk was distance to SL
                risk_distance = abs(open_trade["entry"] - open_trade["sl"]) / open_trade["entry"]
                pnl_r = net_pnl_pct / risk_distance if risk_distance > 0 else 0
                
                balance += pnl_usd
                
                trades.append(Trade(
                    entry_time=open_trade["entry_time"],
                    exit_time=candle.dt,
                    direction=open_trade["direction"],
                    entry_price=open_trade["entry"],
                    exit_price=exit_price,
                    sl_price=open_trade["sl"],
                    tp_price=open_trade["tp"],
                    size_usd=open_trade["size_usd"],
                    pnl_usd=round(pnl_usd, 2),
                    pnl_r=round(pnl_r, 3),
                    exit_reason=exit_reason,
                    regime_atr=open_trade["atr"],
                    candle_body=open_trade["trigger_body"],
                    bars_held=open_trade["bars_held"],
                ))
                
                open_trade = None
                
                # Drawdown tracking
                if balance > peak_balance:
                    peak_balance = balance
                dd = peak_balance - balance
                dd_pct = dd / peak_balance if peak_balance > 0 else 0
                max_drawdown = max(max_drawdown, dd)
                max_drawdown_pct = max(max_drawdown_pct, dd_pct)
        
        equity_curve.append({"dt": candle.dt, "balance": round(balance, 2)})
        
        # ── Look for new entry ──
        if open_trade is not None:
            continue  # Already in a trade
        
        if balance <= 0:
            continue  # Blown
        
        current_atr = atr[i]
        current_atr_ma = atr_ma[i]
        current_ema = price_ema[i]
        
        if current_atr <= 0 or current_atr_ma <= 0:
            continue
        
        # Regime filter: only trade in high-vol environment
        if current_atr < params.regime_threshold * current_atr_ma:
            continue
        
        # Candle body
        body = candle.close - candle.open  # Positive = bullish, negative = bearish
        body_size = abs(body)
        
        # Entry trigger: body must exceed threshold
        if body_size < params.body_atr_multiple * current_atr:
            continue
        
        # Determine direction (FADE the candle)
        if body > 0:
            # Bullish candle → SHORT (fade it)
            direction = "SHORT"
            entry_price = candle.close
            sl_price = candle.high + (params.sl_atr_multiple * current_atr)
            
            # TP: reversion toward EMA
            distance_to_mean = entry_price - current_ema
            if distance_to_mean <= 0:
                continue  # Price already below mean, don't short
            tp_price = entry_price - (distance_to_mean * params.tp_mean_fraction)
            
        else:
            # Bearish candle → LONG (fade it)
            direction = "LONG"
            entry_price = candle.close
            sl_price = candle.low - (params.sl_atr_multiple * current_atr)
            
            # TP: reversion toward EMA
            distance_to_mean = current_ema - entry_price
            if distance_to_mean <= 0:
                continue  # Price already above mean, don't long
            tp_price = entry_price + (distance_to_mean * params.tp_mean_fraction)
        
        if sl_price <= 0 or tp_price <= 0:
            continue
        
        # Validate RR ratio (minimum 1:1.5)
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)
        if risk <= 0 or reward / risk < 1.5:
            continue
        
        # Position sizing (risk-based)
        risk_pct = risk / entry_price
        size_usd = (balance * params.risk_per_trade) / risk_pct
        size_usd = min(size_usd, balance * 0.5)  # Never use more than 50% of balance
        
        open_trade = {
            "direction": direction,
            "entry": entry_price,
            "sl": sl_price,
            "tp": tp_price,
            "entry_time": candle.dt,
            "size_usd": size_usd,
            "atr": current_atr,
            "trigger_body": body_size,
            "bars_held": 0,
        }
    
    # Close final month
    if current_month and month_start_balance > 0:
        monthly_returns[current_month] = (balance - month_start_balance) / month_start_balance
    
    # ── Compute statistics ──
    return compute_stats(trades, equity_curve, monthly_returns, params, balance, max_drawdown, max_drawdown_pct)


def compute_stats(trades, equity_curve, monthly_returns, params, final_balance, max_dd, max_dd_pct) -> dict:
    """Calculate comprehensive backtest statistics."""
    
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "n_trades": 0,
            "error": "No trades generated. Try loosening parameters.",
            "final_balance": final_balance,
        }
    
    winners = [t for t in trades if t.pnl_usd > 0]
    losers = [t for t in trades if t.pnl_usd <= 0]
    
    tp_exits = [t for t in trades if t.exit_reason == "TP"]
    sl_exits = [t for t in trades if t.exit_reason == "SL"]
    timeout_exits = [t for t in trades if t.exit_reason == "TIMEOUT"]
    
    longs = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]
    
    total_pnl = sum(t.pnl_usd for t in trades)
    gross_profit = sum(t.pnl_usd for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl_usd for t in losers)) if losers else 0.01
    
    win_rate = len(winners) / n_trades
    
    avg_win = sum(t.pnl_r for t in winners) / len(winners) if winners else 0
    avg_loss = abs(sum(t.pnl_r for t in losers) / len(losers)) if losers else 0
    
    # Expectancy in R
    expectancy_r = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    
    # Profit factor
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    
    # Consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    current_streak = 0
    streak_type = None
    
    for t in trades:
        if t.pnl_usd > 0:
            if streak_type == "win":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "win"
            max_consec_wins = max(max_consec_wins, current_streak)
        else:
            if streak_type == "loss":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "loss"
            max_consec_losses = max(max_consec_losses, current_streak)
    
    # Average bars held
    avg_bars = sum(t.bars_held for t in trades) / n_trades
    
    # Monthly stats
    positive_months = sum(1 for r in monthly_returns.values() if r > 0)
    total_months = len(monthly_returns)
    avg_monthly = sum(monthly_returns.values()) / total_months if total_months > 0 else 0
    
    # Trades per month
    trades_per_month = n_trades / total_months if total_months > 0 else n_trades
    
    return {
        "n_trades": n_trades,
        "final_balance": round(final_balance, 2),
        "total_return_pct": round((final_balance - params.starting_balance) / params.starting_balance * 100, 2),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3),
        "expectancy_r": round(expectancy_r, 4),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "max_drawdown_usd": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct * 100, 2),
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "avg_bars_held": round(avg_bars, 1),
        "trades_per_month": round(trades_per_month, 1),
        "n_longs": len(longs),
        "n_shorts": len(shorts),
        "n_tp": len(tp_exits),
        "n_sl": len(sl_exits),
        "n_timeout": len(timeout_exits),
        "positive_months": positive_months,
        "total_months": total_months,
        "avg_monthly_return": round(avg_monthly * 100, 2),
        "monthly_returns": monthly_returns,
        "trades": trades,
        "equity_curve": equity_curve,
    }


# ─── Parameter Sweep ─────────────────────────────────────────────────────────

def run_sweep(candles: list[Candle], base_params: StrategyParams) -> list[dict]:
    """Test multiple parameter combinations to find the optimal setup."""
    
    sweep_configs = []
    
    # Key parameters to sweep
    body_multiples = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
    sl_multiples = [1.0, 1.5, 2.0, 2.5]
    tp_fractions = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    regime_thresholds = [0.8, 1.0, 1.2]
    mean_periods = [14, 20, 30]
    
    total = len(body_multiples) * len(sl_multiples) * len(tp_fractions) * len(regime_thresholds) * len(mean_periods)
    print(f"  Running {total} parameter combinations...")
    
    count = 0
    results = []
    
    for body_m in body_multiples:
        for sl_m in sl_multiples:
            for tp_f in tp_fractions:
                for regime_t in regime_thresholds:
                    for mean_p in mean_periods:
                        count += 1
                        if count % 100 == 0:
                            print(f"    Progress: {count}/{total} ({count/total*100:.0f}%)")
                        
                        p = StrategyParams(
                            atr_period=base_params.atr_period,
                            regime_ma_period=base_params.regime_ma_period,
                            regime_threshold=regime_t,
                            mean_ma_period=mean_p,
                            body_atr_multiple=body_m,
                            tp_mean_fraction=tp_f,
                            sl_atr_multiple=sl_m,
                            max_bars_held=base_params.max_bars_held,
                            risk_per_trade=base_params.risk_per_trade,
                            starting_balance=base_params.starting_balance,
                            fee_pct=base_params.fee_pct,
                        )
                        
                        result = run_backtest(candles, p)
                        
                        if result.get("n_trades", 0) >= 30:  # Minimum sample size
                            results.append({
                                "body_atr": body_m,
                                "sl_atr": sl_m,
                                "tp_frac": tp_f,
                                "regime": regime_t,
                                "mean_period": mean_p,
                                "n_trades": result["n_trades"],
                                "win_rate": result["win_rate"],
                                "profit_factor": result["profit_factor"],
                                "expectancy_r": result["expectancy_r"],
                                "total_return": result["total_return_pct"],
                                "max_dd": result["max_drawdown_pct"],
                                "avg_monthly": result["avg_monthly_return"],
                                "trades_per_month": result["trades_per_month"],
                            })
    
    # Sort by expectancy * sqrt(trades_per_month) — balances edge quality with frequency
    for r in results:
        r["score"] = r["expectancy_r"] * math.sqrt(r["trades_per_month"]) if r["expectancy_r"] > 0 else -1
    
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ─── Output ──────────────────────────────────────────────────────────────────

def print_report(stats: dict, params: StrategyParams, filepath: str):
    """Print comprehensive backtest report."""
    
    print(f"\n{'═' * 70}")
    print(f"  BACKTEST REPORT — Mean Reversion + Vol Regime")
    print(f"{'═' * 70}")
    print(f"  Data: {filepath}")
    print(f"  Trades: {stats['n_trades']}  |  Period: {stats.get('total_months', '?')} months")
    
    # Parameters
    print(f"\n  ── Parameters ──")
    print(f"  ATR period:        {params.atr_period}")
    print(f"  Regime threshold:  ATR > {params.regime_threshold}x ATR_MA({params.regime_ma_period})")
    print(f"  Entry trigger:     body > {params.body_atr_multiple}x ATR")
    print(f"  TP:                {params.tp_mean_fraction}x distance to EMA({params.mean_ma_period})")
    print(f"  SL:                {params.sl_atr_multiple}x ATR beyond candle extreme")
    print(f"  Max hold:          {params.max_bars_held} bars")
    print(f"  Risk/trade:        {params.risk_per_trade * 100}%")
    print(f"  Fee/side:          {params.fee_pct * 100}%")
    
    if stats["n_trades"] == 0:
        print(f"\n  ⚠ No trades generated.")
        return
    
    # Core metrics
    print(f"\n  ── Core Metrics ──")
    print(f"  Starting balance:  ${params.starting_balance:,.2f}")
    print(f"  Final balance:     ${stats['final_balance']:,.2f}")
    print(f"  Total return:      {stats['total_return_pct']:+.2f}%")
    print(f"  Avg monthly:       {stats['avg_monthly_return']:+.2f}%")
    print(f"  Profit factor:     {stats['profit_factor']:.3f}")
    print(f"  Expectancy (R):    {stats['expectancy_r']:+.4f}")
    
    # Win/Loss
    print(f"\n  ── Win/Loss ──")
    print(f"  Win rate:          {stats['win_rate'] * 100:.1f}%")
    print(f"  Avg winner:        {stats['avg_win_r']:+.3f}R")
    print(f"  Avg loser:         {stats['avg_loss_r']:-.3f}R")
    print(f"  Max consec wins:   {stats['max_consec_wins']}")
    print(f"  Max consec losses: {stats['max_consec_losses']}")
    
    # Risk
    print(f"\n  ── Risk ──")
    print(f"  Max drawdown:      {stats['max_drawdown_pct']:.2f}%  (${stats['max_drawdown_usd']:,.2f})")
    print(f"  Positive months:   {stats['positive_months']}/{stats['total_months']}")
    
    # Activity
    print(f"\n  ── Activity ──")
    print(f"  Trades/month:      {stats['trades_per_month']:.1f}")
    print(f"  Avg bars held:     {stats['avg_bars_held']:.1f}")
    print(f"  Longs/Shorts:      {stats['n_longs']} / {stats['n_shorts']}")
    print(f"  TP/SL/Timeout:     {stats['n_tp']} / {stats['n_sl']} / {stats['n_timeout']}")
    
    # Monthly breakdown
    print(f"\n  ── Monthly Returns ──")
    monthly = stats.get("monthly_returns", {})
    for month, ret in sorted(monthly.items()):
        bar_len = int(abs(ret) * 200)
        bar = "█" * min(bar_len, 40)
        sign = "+" if ret >= 0 else ""
        color_indicator = "🟢" if ret > 0 else "🔴" if ret < 0 else "⚪"
        print(f"  {month}  {color_indicator} {sign}{ret * 100:6.2f}%  {bar}")
    
    # Verdict
    print(f"\n  ── VERDICT ──")
    
    pf = stats["profit_factor"]
    wr = stats["win_rate"]
    exp = stats["expectancy_r"]
    dd = stats["max_drawdown_pct"]
    n = stats["n_trades"]
    
    issues = []
    strengths = []
    
    if exp > 0.2:
        strengths.append(f"Strong expectancy ({exp:.3f}R)")
    elif exp > 0.05:
        strengths.append(f"Positive expectancy ({exp:.3f}R)")
    else:
        issues.append(f"Weak expectancy ({exp:.3f}R)")
    
    if pf > 1.5:
        strengths.append(f"Healthy profit factor ({pf:.2f})")
    elif pf > 1.1:
        strengths.append(f"Marginal profit factor ({pf:.2f})")
    else:
        issues.append(f"Poor profit factor ({pf:.2f})")
    
    if dd > 30:
        issues.append(f"Excessive drawdown ({dd:.1f}%)")
    elif dd > 20:
        issues.append(f"High drawdown ({dd:.1f}%)")
    else:
        strengths.append(f"Controlled drawdown ({dd:.1f}%)")
    
    if n < 50:
        issues.append(f"Low sample size ({n} trades)")
    else:
        strengths.append(f"Adequate sample ({n} trades)")
    
    if stats["positive_months"] / max(stats["total_months"], 1) > 0.55:
        strengths.append(f"Majority positive months ({stats['positive_months']}/{stats['total_months']})")
    
    for s in strengths:
        print(f"    ✅ {s}")
    for issue in issues:
        print(f"    ⚠️  {issue}")
    
    if exp > 0.1 and pf > 1.3 and dd < 25 and n >= 50:
        print(f"\n  🟢 VIABLE — proceed to forward testing")
    elif exp > 0 and pf > 1.0:
        print(f"\n  🟡 MARGINAL — optimize parameters before forward testing")
    else:
        print(f"\n  🔴 NOT VIABLE — strategy needs redesign")


def print_sweep_results(results: list[dict]):
    """Print parameter sweep results."""
    
    print(f"\n{'═' * 70}")
    print(f"  PARAMETER SWEEP RESULTS")
    print(f"{'═' * 70}")
    print(f"  Viable combinations (30+ trades): {len(results)}\n")
    
    if not results:
        print("  No viable parameter combinations found.")
        return
    
    # Top 20
    print(f"  {'Body':>5} {'SL':>5} {'TP':>5} {'Rgme':>5} {'MA':>4} | {'Trades':>6} {'WR%':>6} {'PF':>6} {'Exp(R)':>7} {'Ret%':>7} {'DD%':>6} | {'Score':>6}")
    print(f"  {'─' * 80}")
    
    for r in results[:20]:
        print(f"  {r['body_atr']:5.2f} {r['sl_atr']:5.1f} {r['tp_frac']:5.2f} {r['regime']:5.1f} {r['mean_period']:4d}"
              f" | {r['n_trades']:6d} {r['win_rate']*100:5.1f}% {r['profit_factor']:6.2f} {r['expectancy_r']:+7.4f}"
              f" {r['total_return']:+6.1f}% {r['max_dd']:5.1f}%"
              f" | {r['score']:6.3f}")
    
    # Best overall
    best = results[0]
    print(f"\n  ── BEST CONFIGURATION ──")
    print(f"  Body trigger:     {best['body_atr']}x ATR")
    print(f"  SL distance:      {best['sl_atr']}x ATR")
    print(f"  TP fraction:      {best['tp_frac']}x to mean")
    print(f"  Regime threshold: {best['regime']}x ATR_MA")
    print(f"  Mean MA period:   {best['mean_period']}")
    print(f"  → {best['n_trades']} trades | {best['win_rate']*100:.1f}% WR | {best['profit_factor']:.2f} PF | {best['expectancy_r']:+.4f} Exp(R)")
    
    return best


def save_trades_csv(trades: list[Trade], filepath: str):
    """Save trade log to CSV for external analysis."""
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "entry_time", "exit_time", "direction", "entry_price", "exit_price",
            "sl_price", "tp_price", "size_usd", "pnl_usd", "pnl_r",
            "exit_reason", "bars_held"
        ])
        for t in trades:
            writer.writerow([
                t.entry_time, t.exit_time, t.direction, t.entry_price, t.exit_price,
                t.sl_price, t.tp_price, t.size_usd, t.pnl_usd, t.pnl_r,
                t.exit_reason, t.bars_held,
            ])


def save_equity_csv(equity: list[dict], filepath: str):
    """Save equity curve to CSV."""
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["datetime", "balance"])
        for point in equity:
            writer.writerow([point["dt"], point["balance"]])


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BTC Mean-Reversion Backtester")
    parser.add_argument("--file", default="data/BTCUSDT_1H.csv", help="Path to candle CSV")
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep")
    parser.add_argument("--balance", type=float, default=1000, help="Starting balance")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (0.01 = 1%%)")
    parser.add_argument("--fee", type=float, default=0.001, help="Fee per side (0.001 = 0.1%%)")
    args = parser.parse_args()
    
    if not os.path.exists(args.file):
        print(f"  ✗ File not found: {args.file}")
        print(f"  Run collect_btc_data.py first, or specify --file path/to/data.csv")
        return
    
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║    BTC Mean-Reversion + Vol-Regime Backtester                    ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    
    candles = load_candles(args.file)
    print(f"\n  Loaded {len(candles):,} candles from {args.file}")
    print(f"  Range: {candles[0].dt} → {candles[-1].dt}")
    
    base_params = StrategyParams(
        starting_balance=args.balance,
        risk_per_trade=args.risk,
        fee_pct=args.fee,
    )
    
    if args.sweep:
        print(f"\n  ── PARAMETER SWEEP MODE ──")
        results = run_sweep(candles, base_params)
        best = print_sweep_results(results)
        
        if best:
            # Run full report with best params
            print(f"\n\n  Running full report with best parameters...")
            optimal_params = StrategyParams(
                body_atr_multiple=best["body_atr"],
                sl_atr_multiple=best["sl_atr"],
                tp_mean_fraction=best["tp_frac"],
                regime_threshold=best["regime"],
                mean_ma_period=best["mean_period"],
                starting_balance=args.balance,
                risk_per_trade=args.risk,
                fee_pct=args.fee,
            )
            stats = run_backtest(candles, optimal_params)
            print_report(stats, optimal_params, args.file)
            
            # Save outputs
            base_name = os.path.splitext(os.path.basename(args.file))[0]
            if stats.get("trades"):
                save_trades_csv(stats["trades"], f"{base_name}_trades.csv")
                save_equity_csv(stats["equity_curve"], f"{base_name}_equity.csv")
                print(f"\n  Saved: {base_name}_trades.csv, {base_name}_equity.csv")
    else:
        # Single run with defaults
        stats = run_backtest(candles, base_params)
        print_report(stats, base_params, args.file)
        
        if stats.get("trades"):
            base_name = os.path.splitext(os.path.basename(args.file))[0]
            save_trades_csv(stats["trades"], f"{base_name}_trades.csv")
            save_equity_csv(stats["equity_curve"], f"{base_name}_equity.csv")
            print(f"\n  Saved: {base_name}_trades.csv, {base_name}_equity.csv")
    
    print()


if __name__ == "__main__":
    main()
