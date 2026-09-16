#!/usr/bin/env python3
"""
Deriv Synthetic Indices — Statistical Edge Detector
=====================================================
Reads CSV data from collect_data.py and runs a battery of statistical tests
to determine whether each synthetic index has exploitable structure.

Tests performed:
  1. Hurst Exponent (R/S method) — trending vs mean-reverting vs random walk
  2. Autocorrelation Analysis — do past returns predict future returns?
  3. Runs Test — is the sequence of up/down moves random?
  4. Return Distribution — fat tails, skewness (asymmetric opportunity?)
  5. Volatility Clustering — does high vol predict high vol? (GARCH effect)
  6. Spike Analysis (Crash/Boom only) — is spike timing predictable?

Usage:
    pip install numpy scipy statsmodels pandas
    python analyze_data.py

Reads from ./data/ directory. Run collect_data.py first.
"""

import os
import sys
import csv
import math
import warnings
from collections import defaultdict

import numpy as np
from scipy import stats as scipy_stats
from statsmodels.tsa.stattools import acf, adfuller
# Runs test is implemented manually below (wald_wolfowitz_runs_test)

# Force UTF-8 output so Unicode symbols print on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

DATA_DIR = "data"

# ─── Utility Functions ───────────────────────────────────────────────────────

def load_csv(filepath: str) -> dict:
    """Load a candle CSV into arrays."""
    epochs, opens, highs, lows, closes = [], [], [], [], []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            epochs.append(int(row["epoch"]))
            opens.append(float(row["open"]))
            highs.append(float(row["high"]))
            lows.append(float(row["low"]))
            closes.append(float(row["close"]))
    
    return {
        "epochs": np.array(epochs),
        "open": np.array(opens),
        "high": np.array(highs),
        "low": np.array(lows),
        "close": np.array(closes),
    }


def log_returns(prices: np.ndarray) -> np.ndarray:
    """Calculate log returns from price series."""
    return np.diff(np.log(prices))


# ─── Test 1: Hurst Exponent (Rescaled Range) ────────────────────────────────

def hurst_exponent(series: np.ndarray, min_window: int = 20, max_divisions: int = 20) -> float:
    """
    Estimate Hurst exponent using the R/S (Rescaled Range) method.
    
    Interpretation:
      H < 0.5  → Mean-reverting (anti-persistent). Strategy: fade moves.
      H = 0.5  → Random walk. No exploitable memory. 
      H > 0.5  → Trending (persistent). Strategy: trend-following.
      
    H > 0.6 or H < 0.4 is where exploitable edge begins.
    """
    N = len(series)
    if N < 100:
        return float("nan")
    
    # Generate logarithmically-spaced window sizes
    max_k = N // 2
    sizes = np.unique(np.logspace(
        np.log10(min_window), 
        np.log10(max_k), 
        num=max_divisions
    ).astype(int))
    sizes = sizes[sizes >= min_window]
    
    rs_values = []
    size_values = []
    
    for size in sizes:
        n_windows = N // size
        if n_windows < 1:
            continue
        
        rs_list = []
        for i in range(n_windows):
            window = series[i * size:(i + 1) * size]
            mean = np.mean(window)
            deviations = np.cumsum(window - mean)
            R = np.max(deviations) - np.min(deviations)
            S = np.std(window, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        
        if rs_list:
            rs_values.append(np.mean(rs_list))
            size_values.append(size)
    
    if len(rs_values) < 3:
        return float("nan")
    
    # Linear regression in log-log space
    log_sizes = np.log(size_values)
    log_rs = np.log(rs_values)
    slope, _, _, _, _ = scipy_stats.linregress(log_sizes, log_rs)
    
    return slope


# ─── Test 2: Autocorrelation Analysis ───────────────────────────────────────

def autocorrelation_analysis(returns: np.ndarray, max_lags: int = 20) -> dict:
    """
    Check if past returns predict future returns.
    
    Significant autocorrelation at lag k means return[t] is correlated 
    with return[t-k]. In a pure random walk, all autocorrelations ≈ 0.
    
    Returns dict with lag values and significance flags.
    """
    n = len(returns)
    acf_values, confint = acf(returns, nlags=max_lags, alpha=0.05)
    
    results = []
    significant_count = 0
    for lag in range(1, max_lags + 1):
        lower = confint[lag][0] - acf_values[lag]
        upper = confint[lag][1] - acf_values[lag]
        is_significant = acf_values[lag] < confint[lag][0] or acf_values[lag] > confint[lag][1]
        if is_significant:
            significant_count += 1
        results.append({
            "lag": lag,
            "acf": acf_values[lag],
            "significant": is_significant,
        })
    
    # Also check autocorrelation of absolute returns (volatility clustering)
    abs_acf, abs_confint = acf(np.abs(returns), nlags=max_lags, alpha=0.05)
    abs_significant = sum(
        1 for lag in range(1, max_lags + 1)
        if abs_acf[lag] < abs_confint[lag][0] or abs_acf[lag] > abs_confint[lag][1]
    )
    
    return {
        "return_acf": results,
        "significant_lags": significant_count,
        "total_lags": max_lags,
        "abs_return_significant_lags": abs_significant,
        "abs_acf_lag1": abs_acf[1] if len(abs_acf) > 1 else 0,
    }


# ─── Test 3: Runs Test (Wald-Wolfowitz) ─────────────────────────────────────

def wald_wolfowitz_runs_test(returns: np.ndarray) -> dict:
    """
    Tests whether the sequence of positive/negative returns is random.
    
    Too few runs → trending behavior (momentum).
    Too many runs → mean-reverting behavior.
    Expected runs for random series ≈ N/2.
    
    p-value < 0.05 means the series is NOT random.
    """
    # Convert to binary: 1 = positive return, 0 = negative
    binary = (returns > 0).astype(int)
    
    # Count runs
    runs = 1
    for i in range(1, len(binary)):
        if binary[i] != binary[i - 1]:
            runs += 1
    
    n1 = np.sum(binary)  # positive returns
    n0 = len(binary) - n1  # negative returns
    N = len(binary)
    
    if n1 == 0 or n0 == 0:
        return {"runs": runs, "expected": N, "z_stat": 0, "p_value": 1.0, "interpretation": "all same sign"}
    
    # Expected runs and variance under null hypothesis
    expected_runs = (2 * n1 * n0) / N + 1
    variance = (2 * n1 * n0 * (2 * n1 * n0 - N)) / (N * N * (N - 1))
    
    if variance <= 0:
        return {"runs": runs, "expected": expected_runs, "z_stat": 0, "p_value": 1.0, "interpretation": "degenerate"}
    
    z = (runs - expected_runs) / math.sqrt(variance)
    p_value = 2 * (1 - scipy_stats.norm.cdf(abs(z)))
    
    if p_value < 0.05:
        if runs < expected_runs:
            interpretation = "TRENDING (fewer runs than random → momentum)"
        else:
            interpretation = "MEAN-REVERTING (more runs than random → fade moves)"
    else:
        interpretation = "Random (cannot reject null hypothesis)"
    
    return {
        "runs": runs,
        "expected": round(expected_runs, 1),
        "z_stat": round(z, 3),
        "p_value": round(p_value, 4),
        "interpretation": interpretation,
    }


# ─── Test 4: Return Distribution Analysis ───────────────────────────────────

def distribution_analysis(returns: np.ndarray) -> dict:
    """
    Analyze the shape of the return distribution.
    
    Normal distribution → no distributional edge.
    Fat tails (high kurtosis) → extreme moves happen more than expected.
    Skewness → asymmetric opportunity (negative skew = big drops; positive = big jumps).
    
    For Crash/Boom, we expect extreme skewness — that's the designed feature.
    """
    skew = scipy_stats.skew(returns)
    kurt = scipy_stats.kurtosis(returns)  # Excess kurtosis (0 = normal)
    
    # Jarque-Bera test for normality
    jb_stat, jb_p = scipy_stats.jarque_bera(returns)
    
    # Shapiro-Wilk on subsample (max 5000 for the test)
    sample = returns[:5000] if len(returns) > 5000 else returns
    sw_stat, sw_p = scipy_stats.shapiro(sample)
    
    # Tail analysis: what % of returns are beyond 2σ and 3σ?
    std = np.std(returns)
    beyond_2sigma = np.sum(np.abs(returns) > 2 * std) / len(returns) * 100
    beyond_3sigma = np.sum(np.abs(returns) > 3 * std) / len(returns) * 100
    # For normal distribution: 2σ ≈ 4.55%, 3σ ≈ 0.27%
    
    return {
        "mean": round(float(np.mean(returns)), 8),
        "std": round(float(std), 8),
        "skewness": round(float(skew), 4),
        "excess_kurtosis": round(float(kurt), 4),
        "jarque_bera_p": round(float(jb_p), 6),
        "shapiro_wilk_p": round(float(sw_p), 6),
        "pct_beyond_2sigma": round(beyond_2sigma, 2),
        "pct_beyond_3sigma": round(beyond_3sigma, 2),
        "normal_2sigma": 4.55,
        "normal_3sigma": 0.27,
        "is_normal": jb_p > 0.05,
    }


# ─── Test 5: Volatility Clustering (Simplified ARCH test) ───────────────────

def volatility_clustering_test(returns: np.ndarray, lags: int = 10) -> dict:
    """
    Test whether volatility clusters — i.e., big moves predict big moves.
    
    This is the ARCH effect. If present, it means:
    - Volatility is predictable even if direction isn't
    - Position sizing can be optimized (reduce size in high-vol regimes)
    - Options/timing strategies may have edge
    
    Uses Engle's ARCH test (Lagrange Multiplier).
    """
    from statsmodels.stats.diagnostic import het_arch
    
    try:
        lm_stat, lm_pvalue, f_stat, f_pvalue = het_arch(returns, nlags=lags)
        has_clustering = lm_pvalue < 0.05
        
        return {
            "lm_statistic": round(float(lm_stat), 4),
            "lm_p_value": round(float(lm_pvalue), 6),
            "f_statistic": round(float(f_stat), 4),
            "f_p_value": round(float(f_pvalue), 6),
            "has_vol_clustering": has_clustering,
            "interpretation": "VOLATILITY CLUSTERS (vol is predictable)" if has_clustering else "No clustering (vol is random)",
        }
    except Exception as e:
        return {"error": str(e)}


# ─── Test 6: Spike Analysis (Crash/Boom only) ───────────────────────────────

def spike_analysis(closes: np.ndarray, symbol: str) -> dict:
    """
    For Crash/Boom indices: analyze whether spike timing is predictable.
    
    If spikes follow a Poisson process (memoryless), intervals between
    spikes should be exponentially distributed. Deviation from exponential
    means timing has structure → potentially exploitable.
    
    A spike is defined as a candle return > 3σ (for Boom) or < -3σ (for Crash).
    """
    returns = log_returns(closes)
    std = np.std(returns)
    mean = np.mean(returns)
    
    is_crash = "CRASH" in symbol.upper()
    is_boom = "BOOM" in symbol.upper()
    
    if not (is_crash or is_boom):
        return {"applicable": False, "reason": "Not a Crash/Boom index"}
    
    # Detect spikes
    threshold = 3 * std
    if is_crash:
        spike_mask = returns < (mean - threshold)
    else:
        spike_mask = returns > (mean + threshold)
    
    spike_indices = np.where(spike_mask)[0]
    n_spikes = len(spike_indices)
    
    if n_spikes < 10:
        return {
            "applicable": True,
            "n_spikes": n_spikes,
            "reason": "Too few spikes for meaningful analysis (need >10)",
        }
    
    # Inter-spike intervals
    intervals = np.diff(spike_indices)
    
    # Test if intervals are exponentially distributed (Poisson process)
    # Kolmogorov-Smirnov test against exponential distribution
    lambda_est = 1 / np.mean(intervals)
    ks_stat, ks_p = scipy_stats.kstest(intervals, "expon", args=(0, 1/lambda_est))
    
    # Coefficient of variation: for exponential, CV = 1.0
    cv = np.std(intervals) / np.mean(intervals) if np.mean(intervals) > 0 else 0
    
    # Autocorrelation of intervals — if >0, recent spike spacing predicts next spacing
    if len(intervals) > 20:
        interval_acf = acf(intervals, nlags=10, alpha=0.05)
        interval_acf_lag1 = interval_acf[0][1]
    else:
        interval_acf_lag1 = 0
    
    # Mean interval in candles
    mean_interval = np.mean(intervals)
    std_interval = np.std(intervals)
    
    is_memoryless = ks_p > 0.05
    
    return {
        "applicable": True,
        "n_spikes": n_spikes,
        "spike_rate": round(n_spikes / len(returns) * 100, 3),  # % of candles that are spikes
        "mean_interval_candles": round(float(mean_interval), 1),
        "std_interval_candles": round(float(std_interval), 1),
        "cv_of_intervals": round(float(cv), 3),  # 1.0 = perfectly exponential
        "ks_test_p_value": round(float(ks_p), 4),
        "interval_autocorr_lag1": round(float(interval_acf_lag1), 4),
        "is_memoryless": is_memoryless,
        "interpretation": (
            "Spike timing appears RANDOM (Poisson-like, no timing edge)"
            if is_memoryless else
            "Spike timing has STRUCTURE (deviates from Poisson → potentially exploitable)"
        ),
    }


# ─── Stationarity Test ──────────────────────────────────────────────────────

def stationarity_test(returns: np.ndarray) -> dict:
    """Augmented Dickey-Fuller test. Stationary returns = consistent behavior over time."""
    try:
        adf_stat, p_value, used_lag, nobs, crit_values, icbest = adfuller(returns, maxlag=20)
        return {
            "adf_statistic": round(float(adf_stat), 4),
            "p_value": round(float(p_value), 6),
            "is_stationary": p_value < 0.05,
            "interpretation": "Stationary (behavior is stable over time)" if p_value < 0.05 else "NON-stationary (behavior shifts — dangerous for automation)",
        }
    except Exception as e:
        return {"error": str(e)}


# ─── Report Generator ───────────────────────────────────────────────────────

def print_section(title: str, width: int = 60):
    print(f"\n  {'─' * width}")
    print(f"  {title}")
    print(f"  {'─' * width}")


def analyze_file(filepath: str, symbol: str, granularity: str):
    """Run full analysis suite on one data file."""
    data = load_csv(filepath)
    closes = data["close"]
    n = len(closes)
    
    if n < 200:
        print(f"  ⚠ Only {n} candles — skipping (need 200+)")
        return None
    
    returns = log_returns(closes)
    
    print(f"\n{'═' * 70}")
    print(f"  {symbol} — {granularity} — {n:,} candles")
    print(f"  Price range: {closes.min():.4f} → {closes.max():.4f}")
    print(f"{'═' * 70}")
    
    results = {"symbol": symbol, "granularity": granularity, "n_candles": n}
    
    # ── Test 1: Hurst Exponent ──
    print_section("TEST 1: Hurst Exponent (Memory / Trend Detection)")
    H = hurst_exponent(returns)
    results["hurst"] = round(H, 4) if not math.isnan(H) else None
    
    if not math.isnan(H):
        if H < 0.4:
            verdict = "STRONG MEAN-REVERSION ← exploitable (fade moves)"
        elif H < 0.45:
            verdict = "Mild mean-reversion (weak signal)"
        elif H < 0.55:
            verdict = "Random walk (no memory → no edge)"
        elif H < 0.6:
            verdict = "Mild trending (weak signal)"
        else:
            verdict = "STRONG TRENDING ← exploitable (follow momentum)"
        
        print(f"  H = {H:.4f}   |   {verdict}")
        print(f"  (H=0.5 = random walk | H>0.6 = trending | H<0.4 = mean-reverting)")
    
    # ── Test 2: Autocorrelation ──
    print_section("TEST 2: Autocorrelation (Past returns → future returns?)")
    acf_result = autocorrelation_analysis(returns)
    results["acf_significant_lags"] = acf_result["significant_lags"]
    results["abs_acf_significant_lags"] = acf_result["abs_return_significant_lags"]
    
    sig = acf_result["significant_lags"]
    total = acf_result["total_lags"]
    expected_false_positives = total * 0.05  # ~1 out of 20 at 5% significance
    
    print(f"  Return autocorrelation: {sig}/{total} lags significant")
    if sig > expected_false_positives * 2:
        print(f"  → MORE than random chance — returns have memory")
    else:
        print(f"  → Consistent with random (expected ~{expected_false_positives:.0f} false positives)")
    
    abs_sig = acf_result["abs_return_significant_lags"]
    print(f"  |Return| autocorrelation: {abs_sig}/{total} lags significant (volatility memory)")
    print(f"  |Return| ACF at lag 1: {acf_result['abs_acf_lag1']:.4f}")
    
    # ── Test 3: Runs Test ──
    print_section("TEST 3: Runs Test (Is the up/down sequence random?)")
    runs = wald_wolfowitz_runs_test(returns)
    results["runs_p_value"] = runs["p_value"]
    results["runs_interpretation"] = runs["interpretation"]
    
    print(f"  Observed runs: {runs['runs']}  |  Expected: {runs['expected']}")
    print(f"  Z-statistic: {runs['z_stat']}  |  p-value: {runs['p_value']}")
    print(f"  → {runs['interpretation']}")
    
    # ── Test 4: Distribution ──
    print_section("TEST 4: Return Distribution (Shape of randomness)")
    dist = distribution_analysis(returns)
    results["skewness"] = dist["skewness"]
    results["excess_kurtosis"] = dist["excess_kurtosis"]
    results["is_normal"] = dist["is_normal"]
    
    print(f"  Skewness:  {dist['skewness']:+.4f}  (0 = symmetric)")
    print(f"  Ex. Kurt:  {dist['excess_kurtosis']:+.4f}  (0 = normal, >3 = very fat tails)")
    print(f"  % beyond 2σ: {dist['pct_beyond_2sigma']:.2f}%  (normal: {dist['normal_2sigma']}%)")
    print(f"  % beyond 3σ: {dist['pct_beyond_3sigma']:.2f}%  (normal: {dist['normal_3sigma']}%)")
    print(f"  Normal distribution? {'YES' if dist['is_normal'] else 'NO (Jarque-Bera p=' + str(dist['jarque_bera_p']) + ')'}")
    
    # ── Test 5: Volatility Clustering ──
    print_section("TEST 5: Volatility Clustering (ARCH effect)")
    vol = volatility_clustering_test(returns)
    results["has_vol_clustering"] = vol.get("has_vol_clustering", None)
    
    if "error" not in vol:
        print(f"  ARCH LM stat: {vol['lm_statistic']}  |  p-value: {vol['lm_p_value']}")
        print(f"  → {vol['interpretation']}")
    else:
        print(f"  Error: {vol['error']}")
    
    # ── Test 6: Spike Analysis ──
    if "CRASH" in symbol.upper() or "BOOM" in symbol.upper():
        print_section("TEST 6: Spike Timing Analysis (Crash/Boom specific)")
        spikes = spike_analysis(closes, symbol)
        results["spike_analysis"] = spikes
        
        if spikes.get("n_spikes", 0) >= 10:
            print(f"  Spikes detected: {spikes['n_spikes']} ({spikes['spike_rate']}% of candles)")
            print(f"  Mean interval: {spikes['mean_interval_candles']} candles (±{spikes['std_interval_candles']})")
            print(f"  CV of intervals: {spikes['cv_of_intervals']} (1.0 = perfect Poisson)")
            print(f"  KS test p-value: {spikes['ks_test_p_value']} (>0.05 = memoryless)")
            print(f"  Interval autocorr (lag 1): {spikes['interval_autocorr_lag1']}")
            print(f"  → {spikes['interpretation']}")
        else:
            print(f"  {spikes.get('reason', 'N/A')}")
    
    # ── Test 7: Stationarity ──
    print_section("TEST 7: Stationarity (Does behavior stay consistent?)")
    station = stationarity_test(returns)
    results["is_stationary"] = station.get("is_stationary", None)
    
    if "error" not in station:
        print(f"  ADF statistic: {station['adf_statistic']}  |  p-value: {station['p_value']}")
        print(f"  → {station['interpretation']}")
    
    # ── Summary Verdict ──
    print_section("VERDICT", 60)
    edge_signals = 0
    edge_details = []
    
    if results.get("hurst") and (results["hurst"] > 0.6 or results["hurst"] < 0.4):
        edge_signals += 1
        edge_details.append(f"Hurst = {results['hurst']} ({'trending' if results['hurst'] > 0.5 else 'mean-reverting'})")
    
    if results.get("acf_significant_lags", 0) > 3:
        edge_signals += 1
        edge_details.append(f"{results['acf_significant_lags']} significant ACF lags")
    
    if results.get("runs_p_value", 1) < 0.05:
        edge_signals += 1
        edge_details.append(f"Non-random runs (p={results['runs_p_value']})")
    
    if results.get("has_vol_clustering"):
        edge_signals += 1
        edge_details.append("Volatility clusters")
    
    if results.get("spike_analysis", {}).get("applicable") and not results.get("spike_analysis", {}).get("is_memoryless", True):
        edge_signals += 1
        edge_details.append("Non-Poisson spike timing")
    
    if results.get("is_stationary"):
        edge_signals += 1
        edge_details.append("Stationary (behavior consistent over time)")
    
    if edge_signals >= 3:
        print(f"  🟢 STRONG SIGNAL — {edge_signals} indicators suggest exploitable structure")
    elif edge_signals >= 2:
        print(f"  🟡 MODERATE SIGNAL — {edge_signals} indicators, worth deeper investigation")
    elif edge_signals >= 1:
        print(f"  🟠 WEAK SIGNAL — only {edge_signals} indicator(s), proceed with caution")
    else:
        print(f"  🔴 NO SIGNAL — appears to be a random walk. No detectable edge.")
    
    for detail in edge_details:
        print(f"    • {detail}")
    
    return results


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║    Deriv Synthetic Indices — Statistical Edge Detector           ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    
    if not os.path.exists(DATA_DIR):
        print(f"\n  ✗ No '{DATA_DIR}/' directory found.")
        print(f"  Run collect_data.py first to download historical data.")
        sys.exit(1)
    
    csv_files = sorted([f for f in os.listdir(DATA_DIR) if f.endswith(".csv")])
    
    if not csv_files:
        print(f"\n  ✗ No CSV files in '{DATA_DIR}/'.")
        print(f"  Run collect_data.py first.")
        sys.exit(1)
    
    print(f"\n  Found {len(csv_files)} data files:\n")
    for f in csv_files:
        print(f"    • {f}")
    
    all_results = []
    
    for filename in csv_files:
        filepath = os.path.join(DATA_DIR, filename)
        
        # Parse symbol and granularity from filename: R_75_1H.csv
        parts = filename.replace(".csv", "").rsplit("_", 1)
        if len(parts) == 2:
            symbol, granularity = parts
        else:
            symbol = filename.replace(".csv", "")
            granularity = "unknown"
        
        result = analyze_file(filepath, symbol, granularity)
        if result:
            all_results.append(result)
    
    # ── Comparative Summary ──
    if len(all_results) > 1:
        print(f"\n\n{'═' * 70}")
        print(f"  COMPARATIVE RANKING — Best candidates for automation")
        print(f"{'═' * 70}\n")
        
        for r in sorted(all_results, key=lambda x: (
            abs(x.get("hurst", 0.5) - 0.5) if x.get("hurst") else 0
        ), reverse=True):
            h = r.get("hurst", "N/A")
            h_str = f"{h:.3f}" if isinstance(h, float) else h
            acf_str = f"{r.get('acf_significant_lags', '?')}/{20} ACF"
            vol_str = "vol-cluster" if r.get("has_vol_clustering") else "no-cluster"
            stat_str = "stationary" if r.get("is_stationary") else "non-stat"
            
            print(f"  {r['symbol']:12s} {r['granularity']:4s} | H={h_str:6s} | {acf_str:9s} | {vol_str:12s} | {stat_str}")
    
    print(f"\n{'═' * 70}")
    print(f"  Analysis complete. {len(all_results)} instruments tested.")
    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    main()
