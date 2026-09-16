"""
========================================================================================
FULL 20-SYMBOL CANONICAL BEEP UNIVERSE SWEEP & MONTE CARLO MATRIX GENERATOR (FAST)
========================================================================================
Optimized with pre-computed B(t) and M(t) rolling arrays for blazingly fast sweeps.
Runs all 20 historical parquet assets:
  - OriginalRawBeep (10-bar accumulation, sign autocorrelation, |Z| <= 2.0 pruning)
  - Multi-Scale Gamma Gate (H4 trend alignment)
  - False-Truth Wick Trap Fader (upper/lower wick <= 45%, Gold <= 30%)
  - Selective Gating on RARE (|M| >= 55) and DIAMOND (|M| >= 75)
  - Hummingbird Two-Stage Ratchet (+1.5R BE lock, +2.2R lock +1.0R, full runner TP)
  - Streaming Health Euthanasia H(t) (narrative scratch & stagnation capital recycle)
  - 1,000-Path Monte Carlo Simulation (p5 to p95 equity percentiles & risk of ruin)
  - Seamless output to data/canonical_beep_edge_matrix.json and Sim Cockpit matrix.
========================================================================================
"""

import os
import sys
import json
import math
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple, Any
import numpy as np
import pandas as pd

# Setup paths
SANDBOX_ROOT = Path(__file__).parent.parent
LAB_ROOT = SANDBOX_ROOT.parent
PARQUET_DIR = LAB_ROOT / "data" / "parquet"
VAULT_DIR = SANDBOX_ROOT / "vault" / "vault_original_beep"

sys.path.insert(0, str(SANDBOX_ROOT))
sys.path.insert(0, str(VAULT_DIR))

from raw_beep_equations import OriginalRawBeep

# Asset specifications
ALL_SYMBOLS = [
    "USDCAD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD",
    "EURJPY", "GBPJPY", "EURGBP", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD",
    "LTCUSD", "XRPUSD", "US30", "US500", "GER40", "UK100"
]

ASSET_CLASS_MAP = {
    "USDCAD": "FOREX_MAJOR", "EURUSD": "FOREX_MAJOR", "GBPUSD": "FOREX_MAJOR",
    "USDJPY": "FOREX_MAJOR", "USDCHF": "FOREX_MAJOR", "AUDUSD": "FOREX_MAJOR",
    "NZDUSD": "FOREX_MAJOR", "EURJPY": "FOREX_CROSS", "GBPJPY": "FOREX_CROSS",
    "EURGBP": "FOREX_CROSS", "XAUUSD": "COMMODITY_METALS", "XAGUSD": "COMMODITY_METALS",
    "BTCUSD": "CRYPTO_MAJOR", "ETHUSD": "CRYPTO_MAJOR", "LTCUSD": "CRYPTO_ALT",
    "XRPUSD": "CRYPTO_ALT", "US30": "INDEX_EQUITY", "US500": "INDEX_EQUITY",
    "GER40": "INDEX_EQUITY", "UK100": "INDEX_EQUITY"
}

SPREAD_MAP = {
    "USDCAD": 0.00012, "EURUSD": 0.00010, "GBPUSD": 0.00014, "USDJPY": 0.012,
    "USDCHF": 0.00013, "AUDUSD": 0.00012, "NZDUSD": 0.00015, "EURJPY": 0.015,
    "GBPJPY": 0.020,   "EURGBP": 0.00014, "XAUUSD": 0.35,    "XAGUSD": 0.020,
    "BTCUSD": 15.0,    "ETHUSD": 0.80,    "LTCUSD": 0.05,    "XRPUSD": 0.0003,
    "US30": 2.5,       "US500": 0.5,      "GER40": 1.5,      "UK100": 1.0,
}

PIP_MAP = {
    "USDCAD": 0.0001, "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01,
    "USDCHF": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001, "EURJPY": 0.01,
    "GBPJPY": 0.01,   "EURGBP": 0.0001, "XAUUSD": 0.1,     "XAGUSD": 0.01,
    "BTCUSD": 1.0,    "ETHUSD": 0.1,     "LTCUSD": 0.01,    "XRPUSD": 0.0001,
    "US30": 1.0,      "US500": 0.1,      "GER40": 1.0,      "UK100": 1.0,
}


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.zeros(n)
    if n < period:
        return atr
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def run_monte_carlo(trades_r: List[float], initial_balance: float = 10000.0, risk_pct: float = 0.015, num_sims: int = 1000) -> Dict[str, Any]:
    if not trades_r:
        return {}

    n_trades = len(trades_r)
    trades_arr = np.array(trades_r)
    sims = np.zeros((num_sims, n_trades + 1))
    sims[:, 0] = initial_balance

    indices = np.random.randint(0, n_trades, size=(num_sims, n_trades))
    sampled_r = trades_arr[indices]

    for t in range(n_trades):
        r_step = sampled_r[:, t]
        dollar_change = sims[:, t] * risk_pct * r_step
        sims[:, t + 1] = np.maximum(1.0, sims[:, t] + dollar_change)

    step_indices = np.linspace(0, n_trades, 30, dtype=int)
    p5 = np.percentile(sims[:, step_indices], 5, axis=0).round(2).tolist()
    p25 = np.percentile(sims[:, step_indices], 25, axis=0).round(2).tolist()
    p50 = np.percentile(sims[:, step_indices], 50, axis=0).round(2).tolist()
    p75 = np.percentile(sims[:, step_indices], 75, axis=0).round(2).tolist()
    p95 = np.percentile(sims[:, step_indices], 95, axis=0).round(2).tolist()

    running_peaks = np.maximum.accumulate(sims, axis=1)
    dd_matrix = (running_peaks - sims) / running_peaks
    max_dds = np.max(dd_matrix, axis=1) * 100.0

    return {
        "num_simulations": num_sims,
        "num_trades": n_trades,
        "initial_balance": initial_balance,
        "trade_steps": step_indices.tolist(),
        "p5_curve": p5,
        "p25_curve": p25,
        "p50_curve": p50,
        "p75_curve": p75,
        "p95_curve": p95,
        "expected_max_dd_median_pct": round(float(np.median(max_dds)), 2),
        "worst_drawdown_95_pct": round(float(np.percentile(max_dds, 95)), 2),
        "worst_drawdown_99_pct": round(float(np.percentile(max_dds, 99)), 2),
        "risk_of_ruin_10_pct": round(float(np.mean(max_dds >= 10.0) * 100.0), 2),
        "risk_of_ruin_20_pct": round(float(np.mean(max_dds >= 20.0) * 100.0), 2),
        "risk_of_ruin_30_pct": round(float(np.mean(max_dds >= 30.0) * 100.0), 2),
        "risk_of_ruin_50_pct": round(float(np.mean(max_dds >= 50.0) * 100.0), 2),
        "terminal_median_balance": round(float(np.median(sims[:, -1])), 2),
        "terminal_p5_balance": round(float(np.percentile(sims[:, -1], 5)), 2),
        "terminal_p95_balance": round(float(np.percentile(sims[:, -1], 95)), 2),
        "is_robust": bool(np.percentile(max_dds, 95) <= 25.0 and np.median(sims[:, -1]) > initial_balance),
    }


def evaluate_symbol(symbol: str, tf: str = "M15") -> Optional[Dict[str, Any]]:
    m15_path = PARQUET_DIR / symbol / f"{tf}.parquet"
    if not m15_path.exists():
        m15_path = PARQUET_DIR / f"{symbol} {tf}.parquet"
    if not m15_path.exists():
        return None

    df_m15 = pd.read_parquet(m15_path)
    df_m15.columns = [c.lower() for c in df_m15.columns]
    df_m15["time"] = pd.to_datetime(df_m15["time"])
    df_m15.sort_values("time", inplace=True)
    df_m15.reset_index(drop=True, inplace=True)

    h4_path = PARQUET_DIR / symbol / "H4.parquet"
    if not h4_path.exists():
        h4_path = PARQUET_DIR / f"{symbol} H4.parquet"
    h4_df = None
    if h4_path.exists():
        h4_df = pd.read_parquet(h4_path)
        h4_df.columns = [c.lower() for c in h4_df.columns]
        h4_df["time"] = pd.to_datetime(h4_df["time"])
        h4_df.sort_values("time", inplace=True)
        h4_df.reset_index(drop=True, inplace=True)

    raw_beep = OriginalRawBeep()
    pip_size = PIP_MAP.get(symbol, 0.0001)
    spread_cost = SPREAD_MAP.get(symbol, 0.00012)

    opens = df_m15["open"].values
    highs = df_m15["high"].values
    lows = df_m15["low"].values
    closes = df_m15["close"].values
    times = df_m15["time"].values
    n_bars = len(closes)

    atrs = compute_atr(highs, lows, closes, period=14)

    # 1. Pre-compute H4 trends
    h4_trends = np.zeros(n_bars, dtype=int)
    if h4_df is not None and len(h4_df) > 15:
        h4_times = h4_df["time"].values
        h4_closes = h4_df["close"].values
        h4_idx = 0
        n_h4 = len(h4_times)
        for i in range(n_bars):
            t = times[i]
            while h4_idx + 1 < n_h4 and h4_times[h4_idx + 1] <= t:
                h4_idx += 1
            if h4_idx >= 12:
                window_h4 = list(h4_closes[max(0, h4_idx - 11) : h4_idx + 1])
                b_h4 = raw_beep.calculate_raw_baseline_B(window_h4)
                h4_trends[i] = 1 if window_h4[-1] > b_h4 else -1

    # 2. Pre-compute B(t) and M(t) once for all bars (High-speed optimization)
    eff_lookback = 20
    start_bar = max(eff_lookback + 5, 20)
    all_b_t = np.zeros(n_bars)
    all_m_mag = np.zeros(n_bars)

    for k in range(start_bar, n_bars):
        win_k = list(closes[k - eff_lookback : k + 1])
        b_val = raw_beep.calculate_raw_baseline_B(win_k)
        all_b_t[k] = b_val
        all_m_mag[k] = raw_beep.calculate_raw_mass_M(win_k, b_val)

    # 3. Simulate Trade Lifecycle
    trades_pnl_r: List[float] = []
    in_trade_until = -1

    for i in range(start_bar, n_bars - 1):
        if i < in_trade_until:
            continue

        cur_p = closes[i]
        b_t = all_b_t[i]
        m_mag = all_m_mag[i]

        # Check Active Hunt with M >= 55 (RARE & DIAMOND)
        if m_mag >= 55.0:
            pot_action = "BUY" if cur_p > b_t else "SELL"
            pot_layer = "DIAMOND" if m_mag >= 75.0 else "RARE"
            pot_rr = 4.0 if m_mag >= 75.0 else 3.5

            bar_o = opens[i]
            bar_h = highs[i]
            bar_l = lows[i]
            bar_rng = max(1e-6, bar_h - bar_l)
            upper_wick = (bar_h - max(bar_o, cur_p)) / bar_rng
            lower_wick = (min(bar_o, cur_p) - bar_l) / bar_rng

            wick_limit = 0.30 if "XAU" in symbol else 0.45
            if pot_action == "BUY" and upper_wick > wick_limit:
                continue
            if pot_action == "SELL" and lower_wick > wick_limit:
                continue

            if h4_trends[i] != 0:
                if pot_action == "BUY" and h4_trends[i] != 1:
                    continue
                if pot_action == "SELL" and h4_trends[i] != -1:
                    continue

            min_stop = max(15.0 * pip_size, spread_cost * 4.0)
            risk_dist = max(min_stop, abs(cur_p - b_t) + (min_stop * 0.30))
            if risk_dist <= 1e-6:
                continue

            entry_price = cur_p
            entry_bar = i
            is_buy = (pot_action == "BUY")

            if is_buy:
                sl = cur_p - risk_dist
                tp = cur_p + (risk_dist * pot_rr)
            else:
                sl = cur_p + risk_dist
                tp = cur_p - (risk_dist * pot_rr)

            current_sl = sl
            target_tp = tp
            ratchet_be_triggered = False
            ratchet_lock_triggered = False
            exit_bar = entry_bar
            pnl_r = 0.0

            for j in range(i + 1, min(n_bars, i + 1 + 120)):
                bar_h_j = highs[j]
                bar_l_j = lows[j]
                cur_close_j = closes[j]

                if is_buy:
                    favorable_r = (bar_h_j - entry_price) / risk_dist
                    floating_r = (cur_close_j - entry_price) / risk_dist
                    adverse_hit = (bar_l_j <= current_sl)
                    tp_hit = (bar_h_j >= target_tp)
                else:
                    favorable_r = (entry_price - bar_l_j) / risk_dist
                    floating_r = (entry_price - cur_close_j) / risk_dist
                    adverse_hit = (bar_h_j >= current_sl)
                    tp_hit = (bar_l_j <= target_tp)

                if adverse_hit:
                    exit_bar = j
                    pnl_r = 1.0 if ratchet_lock_triggered else (0.0 if ratchet_be_triggered else -1.0)
                    break

                if tp_hit:
                    exit_bar = j
                    pnl_r = pot_rr
                    break

                # Hummingbird Two-Stage Ratchet
                if favorable_r >= 1.50 and not ratchet_be_triggered:
                    ratchet_be_triggered = True
                    current_sl = entry_price + (spread_cost if is_buy else -spread_cost)

                if favorable_r >= 2.20 and not ratchet_lock_triggered:
                    ratchet_lock_triggered = True
                    current_sl = entry_price + (risk_dist * 1.0 if is_buy else -risk_dist * 1.0)

                # Streaming Health Euthanasia H(t) using precomputed values
                b_t_j = all_b_t[j]
                m_mag_j = all_m_mag[j]
                m_t_j = m_mag_j if cur_close_j > b_t_j else (-m_mag_j if cur_close_j < b_t_j else 0.0)

                mass_score = max(0.0, min(1.0, (m_t_j + 50.0) / 100.0)) if is_buy else max(0.0, min(1.0, (50.0 - m_t_j) / 100.0))
                baseline_score = 1.0 if (cur_close_j >= b_t_j if is_buy else cur_close_j <= b_t_j) else max(0.0, 1.0 - (abs(cur_close_j - b_t_j) / risk_dist))
                
                bar_rng_j = max(1e-6, bar_h_j - bar_l_j)
                wick_trap_j = (bar_h_j - max(opens[j], cur_close_j)) / bar_rng_j if is_buy else (min(opens[j], cur_close_j) - bar_l_j) / bar_rng_j
                wick_safety = max(0.0, 1.0 - (wick_trap_j * 1.5))
                expansion_score = max(0.0, min(1.0, (floating_r + 1.0) / 3.0))

                h_t = (0.35 * mass_score) + (0.25 * baseline_score) + (0.20 * wick_safety) + (0.20 * expansion_score)

                if (h_t < 0.30 and -0.45 <= floating_r <= -0.10) or (h_t < 0.25 and floating_r < -0.45):
                    exit_bar = j
                    pnl_r = floating_r
                    break

                pos_age_bars = j - entry_bar
                if pos_age_bars >= 4 and (-0.25 <= floating_r <= 0.30) and h_t < 0.45:
                    exit_bar = j
                    pnl_r = floating_r
                    break
            else:
                exit_bar = min(n_bars - 1, i + 120)
                exit_price = closes[exit_bar]
                pnl_r = ((exit_price - entry_price) if is_buy else (entry_price - exit_price)) / risk_dist

            friction_r = spread_cost / risk_dist
            net_pnl_r = round(pnl_r - friction_r, 3)
            trades_pnl_r.append(net_pnl_r)
            in_trade_until = exit_bar

    total_trades = len(trades_pnl_r)
    if total_trades == 0:
        return None

    wins = [r for r in trades_pnl_r if r > 0]
    losses = [abs(r) for r in trades_pnl_r if r < 0]
    win_rate = (len(wins) / total_trades) * 100.0
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    expectancy_r = float(np.mean(trades_pnl_r))
    total_pnl_r = float(sum(trades_pnl_r))

    cum_pnl = np.cumsum(trades_pnl_r)
    peak = np.maximum.accumulate(cum_pnl)
    drawdowns = peak - cum_pnl
    max_dd_r = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    if profit_factor >= 1.30 and expectancy_r >= 0.10:
        status = "ELITE"
        edge_rating = "ALPHA_LEADER"
    elif profit_factor >= 1.10 and expectancy_r >= 0.03:
        status = "VIABLE"
        edge_rating = "POSITIVE_DRIFT"
    else:
        status = "MARGINAL"
        edge_rating = "NOISE_EXPOSED"

    mc_result = run_monte_carlo(trades_pnl_r, initial_balance=10000.0, risk_pct=0.015, num_sims=1000)

    diagnosis = {
        "headline": f"Canonical BEEP {symbol} M15",
        "regime_classification": "ACTIVE_HUNT_GAMMA_ALIGNED",
        "structural_cause": f"M(t) directional conviction aligned with H4 institutional baseline on {symbol}.",
        "mechanics_detail": f"Two-stage Hummingbird ratchet and streaming H(t) euthanasia maintain {profit_factor:.2f} PF over {n_bars:,} bars.",
        "recommendation": f"{'Deploy on fleet with priority allocation.' if status == 'ELITE' else 'Operate with standard risk controls.'}",
        "edge_rating": edge_rating,
    }

    return {
        "symbol": symbol,
        "asset_class": ASSET_CLASS_MAP.get(symbol, "FOREX_MAJOR"),
        "timeframe": tf,
        "bars_evaluated": n_bars,
        "trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_r": round(expectancy_r, 3),
        "net_pnl": round(total_pnl_r * 150.0, 1),
        "net_pnl_r": round(total_pnl_r, 2),
        "max_drawdown_pct": round((max_dd_r * 1.5), 2),
        "max_drawdown_r": round(max_dd_r, 2),
        "sharpe_ratio": round(expectancy_r / (np.std(trades_pnl_r) or 1.0) * math.sqrt(252 * 4), 2),
        "optimal": (status == "ELITE"),
        "status": status,
        "diagnosis": diagnosis,
        "monte_carlo": mc_result,
    }


import concurrent.futures

def main():
    print("=" * 80)
    print("      LAUNCHING FULL 20-SYMBOL CANONICAL BEEP UNIVERSE SWEEP (PARALLEL)")
    print("================================================================================")
    print(f"Total Universe Symbols: {len(ALL_SYMBOLS)}")
    print(f"Timeframe: M15 (Entry) | Macro Gate: H4 | Euthanasia: ACTIVE | Cores: 4")
    print("-" * 80)

    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(evaluate_symbol, sym, "M15"): sym for sym in ALL_SYMBOLS}
        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            sym = futures[future]
            completed_count += 1
            try:
                res = future.result()
                if res:
                    results.append(res)
                    print(f"[{completed_count:>2}/{len(ALL_SYMBOLS)}] {sym:<7} Trades: {res['trades']:>4} | WR: {res['win_rate_pct']:>5.1f}% | PF: {res['profit_factor']:>4.2f} | Exp: {res['expectancy_r']:>+5.3f}R | Net: {res['net_pnl_r']:>+6.1f}R | [{res['status']}]", flush=True)
                else:
                    print(f"[{completed_count:>2}/{len(ALL_SYMBOLS)}] {sym:<7} [SKIPPED - Incomplete Data]", flush=True)
            except Exception as exc:
                print(f"[{completed_count:>2}/{len(ALL_SYMBOLS)}] {sym:<7} [ERROR: {exc}]", flush=True)

    results.sort(key=lambda x: (x["profit_factor"], x["expectancy_r"]), reverse=True)
    for rank_idx, item in enumerate(results, 1):
        item["rank"] = rank_idx

    print("=" * 80)
    print("                         SWEEP SUMMARY LEADERBOARD")
    print("=" * 80)
    print(f"{'Rank':<5} {'Symbol':<8} {'Asset Class':<16} {'Trades':<8} {'Win Rate':<10} {'PF':<7} {'Expectancy':<12} {'Net PnL (R)':<12} {'Status'}")
    print("-" * 80)
    for r in results:
        print(f"#{r['rank']:<4} {r['symbol']:<8} {r['asset_class']:<16} {r['trades']:<8} {r['win_rate_pct']:>5.1f}%     {r['profit_factor']:>5.2f}   {r['expectancy_r']:>+6.3f} R    {r['net_pnl_r']:>+7.1f} R     {r['status']}")
    print("=" * 80)

    output_path = LAB_ROOT / "data" / "canonical_beep_edge_matrix.json"
    master_dict = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_symbols_evaluated": len(results),
        "engine": "Canonical BEEP OriginalRawBeep + Gamma Gate + Wick Fader + Streaming Health Euthanasia",
        "combinations": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(master_dict, f, indent=2)
    print(f"\n[OK] Master canonical matrix saved to: {output_path}")

    cockpit_matrix_path = LAB_ROOT / "data" / "strategy_universe_matrix.json"
    if cockpit_matrix_path.exists():
        try:
            with open(cockpit_matrix_path, "r", encoding="utf-8") as f:
                cockpit_data = json.load(f)

            beep_found = False
            for strat in cockpit_data.get("strategies", []):
                if strat.get("id") in ("strat_beep_baseline", "strat_beep_vacuum"):
                    strat["ranked_combinations"] = results
                    strat["total_combinations"] = len(results)
                    strat["elite_combinations"] = sum(1 for r in results if r["status"] == "ELITE")
                    strat["viable_combinations"] = sum(1 for r in results if r["status"] in ("ELITE", "VIABLE"))
                    beep_found = True

            if beep_found:
                with open(cockpit_matrix_path, "w", encoding="utf-8") as f:
                    json.dump(cockpit_data, f, indent=2)
                print(f"[OK] Successfully synchronized Sim Cockpit matrix at: {cockpit_matrix_path}")
        except Exception as err:
            print(f"[WARNING] Failed to sync cockpit matrix: {err}")


if __name__ == "__main__":
    main()
