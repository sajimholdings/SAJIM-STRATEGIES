"""
Walk-forward HAR-RV volatility model + causal intraday profile.

HAR-RV (Corsi 2009) on logs:
    log RV[d+1] = b0 + b1 log RV[d] + b2 log mean(RV[d-4..d]) + b3 log mean(RV[d-21..d]) + e

Zero-lookahead contract: the forecast for trading day D is built only from
days strictly before D (coefficients fitted on pairs whose target is < D).
The intraday profile used on day D is averaged over days strictly before D.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TRAIN_DAYS = 40      # HAR pairs before the first forecast (40 still beats naive out of sample)
PROFILE_LOOKBACK = 60    # days averaged for the hour-of-day profile
PROFILE_MIN_DAYS = 20
REGIME_LOOKBACK = 250    # trading days of past forecasts the percentile is ranked against
REGIME_MIN = 60
CALM_PCTILE, STORM_PCTILE = 33.0, 67.0
MIN_DAY_COVERAGE = 0.5   # a day needs >= 50% of the typical bar count to be used


def daily_realized(rets: pd.DataFrame) -> pd.DataFrame:
    """Daily realized variance; thin days (holidays, half sessions, holes) are dropped."""
    g = rets.groupby("day")["ret"]
    daily = pd.DataFrame({"n_bars": g.size(), "rv": g.apply(lambda x: float((x ** 2).sum()))})
    typical = daily["n_bars"].median()
    daily = daily[(daily["n_bars"] >= MIN_DAY_COVERAGE * typical) & (daily["rv"] > 0)]
    return daily.reset_index()


def _har_features(rv: np.ndarray) -> np.ndarray:
    """Row i holds features known at the close of day i (NaN until 22 days exist)."""
    s = pd.Series(rv)
    return np.column_stack([
        np.ones(len(rv)),
        np.log(s),
        np.log(s.rolling(5).mean()),
        np.log(s.rolling(22).mean()),
    ])


def har_walkforward(daily: pd.DataFrame, min_train: int = MIN_TRAIN_DAYS) -> pd.DataFrame:
    """
    Adds `rv_forecast`: forecast of day i's variance, made at the close of day i-1.
    Coefficients are refitted every day on an expanding window.
    """
    rv = daily["rv"].to_numpy(dtype=float)
    X = _har_features(rv)
    y = np.log(rv)
    n = len(rv)
    fc = np.full(n, np.nan)
    first_feat = 21  # first row with a full 22-day window

    for i in range(first_feat + 1 + min_train, n):
        # pairs (features at j-1 -> target j) for targets j < i
        Xtr, ytr = X[first_feat:i - 1], y[first_feat + 1:i]
        beta, *_ = np.linalg.lstsq(Xtr, ytr, rcond=None)
        resid = ytr - Xtr @ beta
        s2 = resid.var(ddof=X.shape[1])
        fc[i] = np.exp(X[i - 1] @ beta + 0.5 * s2)  # lognormal bias correction

    out = daily.copy()
    out["rv_forecast"] = fc
    out["vol_daily_pct"] = 100 * np.sqrt(fc)
    days_per_year = _trading_days_per_year(daily["day"])
    out["vol_ann_pct"] = out["vol_daily_pct"] * np.sqrt(days_per_year)

    # percentile of today's forecast vs the past REGIME_LOOKBACK forecasts (excludes today)
    f = out["rv_forecast"]
    out["vol_pctile"] = f.rolling(REGIME_LOOKBACK + 1, min_periods=REGIME_MIN + 1).apply(
        lambda w: 100.0 * (w[:-1] < w[-1]).mean(), raw=True)
    out["regime"] = pd.cut(out["vol_pctile"], bins=[-1, CALM_PCTILE, STORM_PCTILE, 101],
                           labels=["calm", "normal", "storm"]).astype(object)
    out.attrs["days_per_year"] = days_per_year
    return out


def _trading_days_per_year(days: pd.Series) -> float:
    span_years = (days.iloc[-1] - days.iloc[0]).days / 365.25
    return float(len(days) / span_years) if span_years > 0 else 252.0


def hourly_profile(rets: pd.DataFrame, daily: pd.DataFrame,
                   lookback: int = PROFILE_LOOKBACK) -> pd.DataFrame:
    """
    For each trading day D (row), the share of daily variance expected in each
    UTC hour (columns 0..23), averaged over the `lookback` valid days before D.
    Also returns the expected number of bars per hour via attrs["bars_per_hour"].
    """
    valid = rets[rets["day"].isin(daily["day"])]
    hv = valid.groupby(["day", "hour"])["ret"].apply(lambda x: float((x ** 2).sum())).unstack(fill_value=0.0)
    hv = hv.reindex(columns=range(24), fill_value=0.0).reindex(daily["day"])
    share = hv.div(hv.sum(axis=1), axis=0)
    prof = share.shift(1).rolling(lookback, min_periods=PROFILE_MIN_DAYS).mean()
    prof = prof.div(prof.sum(axis=1), axis=0)

    counts = valid.groupby(["day", "hour"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=range(24), fill_value=0).reindex(daily["day"])
    bars = counts.shift(1).rolling(lookback, min_periods=PROFILE_MIN_DAYS).mean()
    prof.attrs["bars_per_hour"] = bars
    return prof


def build(rets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily forecast table + hourly profile for one symbol."""
    daily = daily_realized(rets)
    table = har_walkforward(daily)
    prof = hourly_profile(rets, daily)
    return table, prof


def evaluate(table: pd.DataFrame) -> dict:
    """
    Out-of-sample forecast quality vs simple baselines (lower QLIKE / MSE is better).
    Baselines are also causal: yesterday's RV, and the trailing 22-day mean.
    """
    rv = table["rv"]
    cands = {
        "har_rv": table["rv_forecast"],
        "naive_yesterday": rv.shift(1),
        "mean_22d": rv.shift(1).rolling(22).mean(),
    }
    mask = np.logical_and.reduce([c.notna().to_numpy() for c in cands.values()])
    y = rv[mask].to_numpy()
    res = {"oos_days": int(mask.sum())}
    for name, f in cands.items():
        h = f[mask].to_numpy()
        qlike = float(np.mean(y / h - np.log(y / h) - 1))
        log_mse = float(np.mean((np.log(y) - np.log(h)) ** 2))
        corr = float(np.corrcoef(np.log(y), np.log(h))[0, 1])
        res[name] = {"qlike": round(qlike, 4), "log_mse": round(log_mse, 4),
                     "log_corr": round(corr, 3)}
    return res
