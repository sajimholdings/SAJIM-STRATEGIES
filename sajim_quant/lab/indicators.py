"""
SAJIM Strategy Lab — causal indicators for generated strategies.

    from sajim_quant.lab import indicators as ind

Every function is CAUSAL: the value on row i depends only on rows <= i, so cutting
the frame after any row leaves every earlier value unchanged (tests/lab/test_indicators.py
proves it by truncation). Outputs are aligned to the input index.

Higher-timeframe views come only from `htf()` and `session_range()`, which expose a
higher-TF bar or a session only once it has CLOSED. Strategies may not call
`.resample()` themselves (the contract linter rejects it).

This file is vendored byte-for-byte into the untrusted sandbox, so it must stay
self-contained: numpy, pandas, math and typing only — no sajim_quant imports, no I/O.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

INDICATORS_VERSION = "1"

_HTF_RULES: Dict[str, int] = {"30min": 30, "1h": 60, "4h": 240, "1d": 1440}
_NS_PER_MIN = 60_000_000_000
_NS_PER_HOUR = 60 * _NS_PER_MIN
_NS_PER_DAY = 24 * _NS_PER_HOUR


def _period(n) -> int:
    """A window length: a positive integer (integral floats are accepted)."""
    if isinstance(n, bool):
        raise ValueError("window length must be a positive integer")
    try:
        f = float(n)
    except (TypeError, ValueError):
        raise ValueError(f"window length must be a positive integer, got {n!r}") from None
    if not math.isfinite(f) or f < 1 or f != math.floor(f):
        raise ValueError(f"window length must be a positive integer, got {n!r}")
    return int(f)


def _times_ns(index: pd.Index) -> np.ndarray:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("a DatetimeIndex is required")
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    return index.values.astype("datetime64[ns]").astype(np.int64)


def _wilder(values: np.ndarray, n: int, index: pd.Index, name: str) -> pd.Series:
    """
    Wilder smoothing of values[1:]: NaN for rows 0..n-1, the mean of values[1..n] on
    row n, then avg_t = (avg_{t-1} * (n - 1) + values_t) / n.
    """
    out = np.full(len(values), np.nan)
    if len(values) > n:
        seeded = values.astype(np.float64).copy()
        seeded[:n] = np.nan
        seeded[n] = np.nanmean(values[1:n + 1]) if np.isfinite(values[1:n + 1]).any() else np.nan
        out = np.array(pd.Series(seeded).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy(), copy=True)
        out[:n] = np.nan
    return pd.Series(out, index=index, name=name)


def sma(x: pd.Series, n: int) -> pd.Series:
    """Simple moving average of the last n rows (NaN until n rows exist)."""
    n = _period(n)
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    """Exponential moving average, span n, adjust=False, NaN for the first n-1 rows."""
    n = _period(n)
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's average true range. NaN for the first n bars; row n is the mean TR of rows 1..n."""
    n = _period(n)
    h = df["high"].to_numpy(dtype=np.float64)
    l = df["low"].to_numpy(dtype=np.float64)
    c = df["close"].to_numpy(dtype=np.float64)
    tr = np.full(len(c), np.nan)
    if len(c) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return _wilder(tr, n, df.index, "atr")


def rsi(x: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI (0..100). NaN for the first n rows; 100 with no losses, 50 when flat."""
    n = _period(n)
    v = x.to_numpy(dtype=np.float64)
    delta = np.full(len(v), np.nan)
    if len(v) > 1:
        delta[1:] = np.diff(v)
    gain = _wilder(np.where(np.isnan(delta), np.nan, np.clip(delta, 0, None)), n, x.index, "g").to_numpy()
    loss = _wilder(np.where(np.isnan(delta), np.nan, np.clip(-delta, 0, None)), n, x.index, "l").to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + gain / loss)
    out = np.where((loss == 0) & (gain > 0), 100.0, out)
    out = np.where((loss == 0) & (gain == 0), 50.0, out)
    return pd.Series(out, index=x.index, name="rsi")


def donchian(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """
    Highest high / lowest low of the last n bars INCLUDING the current one (columns
    upper, lower, mid). For a breakout of the PRIOR n bars use `.shift(1)`.
    """
    n = _period(n)
    upper = df["high"].rolling(n, min_periods=n).max()
    lower = df["low"].rolling(n, min_periods=n).min()
    return pd.DataFrame({"upper": upper, "lower": lower, "mid": (upper + lower) / 2.0}, index=df.index)


def zscore(x: pd.Series, n: int) -> pd.Series:
    """(x - rolling mean) / rolling std (ddof=1) over the last n rows; NaN where std is 0."""
    n = _period(n)
    m = x.rolling(n, min_periods=n).mean()
    s = x.rolling(n, min_periods=n).std(ddof=1)
    return ((x - m) / s.where(s > 0)).rename("zscore")


def _session_hours(start_h, end_h) -> Tuple[int, int, int]:
    """(start, end, duration_hours). end 24 means midnight; start > end wraps midnight."""
    for v in (start_h, end_h):
        if isinstance(v, bool) or not float(v).is_integer():
            raise ValueError("session hours must be whole UTC hours")
    s, e = int(start_h), int(end_h)
    if not (0 <= s <= 23 and 0 <= e <= 24):
        raise ValueError("session hours must satisfy 0 <= start_h <= 23 and 0 <= end_h <= 24")
    if s == e:
        raise ValueError("start_h and end_h must differ")
    return s, e, (e - s if e > s else e + 24 - s)


def session_mask(index: pd.DatetimeIndex, start_h: int, end_h: int) -> pd.Series:
    """True for bars whose open hour h (UTC) has start_h <= h < end_h; wraps midnight if start_h > end_h."""
    s, e, _ = _session_hours(start_h, end_h)
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("a DatetimeIndex is required")
    hour = np.asarray(index.hour)
    mask = (hour >= s) & (hour < e) if s < e else (hour >= s) | (hour < e)
    return pd.Series(mask, index=index, name="session")


def session_range(df: pd.DataFrame, start_h: int, end_h: int) -> pd.DataFrame:
    """
    High and low of the most recent COMPLETED session [start_h, end_h) UTC (columns
    high, low). A session belongs to the bars whose open time falls inside it and
    becomes visible on the first bar whose open time is >= the session end; NaN before.
    A session cut short by the start of the data counts with the bars it has.
    """
    s, _, dur = _session_hours(start_h, end_h)
    t = _times_ns(df.index)
    n = len(t)
    out_h = np.full(n, np.nan)
    out_l = np.full(n, np.nan)
    in_sess = session_mask(df.index, start_h, end_h).to_numpy()
    if n and in_sess.any():
        shifted = t - s * _NS_PER_HOUR
        anchor = (shifted // _NS_PER_DAY) * _NS_PER_DAY + s * _NS_PER_HOUR
        a = anchor[in_sess]
        hi = df["high"].to_numpy(dtype=np.float64)[in_sess]
        lo = df["low"].to_numpy(dtype=np.float64)[in_sess]
        starts = np.flatnonzero(np.r_[True, a[1:] != a[:-1]])
        sess_hi = np.maximum.reduceat(hi, starts)
        sess_lo = np.minimum.reduceat(lo, starts)
        sess_end = a[starts] + dur * _NS_PER_HOUR
        k = np.searchsorted(sess_end, t, side="right") - 1
        ok = k >= 0
        out_h[ok] = sess_hi[k[ok]]
        out_l[ok] = sess_lo[k[ok]]
    return pd.DataFrame({"high": out_h, "low": out_l}, index=df.index)


def htf(df: pd.DataFrame, rule: str, base_minutes: Optional[int] = None) -> pd.DataFrame:
    """
    open/high/low/close of the most recent CLOSED higher-timeframe bar, on every row.

    rule is one of "30min", "1h", "4h", "1D" (UTC-aligned buckets [T, T+R)). Bucket k
    becomes visible on the first row r whose bar END (open time + base) is >= T+R.

    Causal by construction, whatever the data looks like:
      * base is `base_minutes` when given; otherwise the smallest positive step among
        rows <= r (a running minimum, so later rows never change an earlier value; the
        first row, having no step yet, sees only buckets that ended before it opened);
      * the bucket's high/low/close are taken over its rows <= r only, so even a wrong
        base can never pull in a later row. On a regular frame this is the full bucket.
    NaN before the first closed bucket.
    """
    key = str(rule).strip().lower()
    if key not in _HTF_RULES:
        raise ValueError(f"rule must be one of {', '.join(['30min', '1h', '4h', '1D'])}")
    if not df.index.is_monotonic_increasing:
        raise ValueError("htf needs an ascending index")
    t = _times_ns(df.index)
    n = len(t)
    cols = ("open", "high", "low", "close")
    out = {c: np.full(n, np.nan) for c in cols}
    r_ns = _HTF_RULES[key] * _NS_PER_MIN
    if n == 0:
        return pd.DataFrame(out, index=df.index)
    if base_minutes is not None:
        base = np.full(n, _period(base_minutes) * _NS_PER_MIN, dtype=np.int64)
        if base[0] > r_ns:
            raise ValueError(f"rule {rule!r} is shorter than base_minutes={base_minutes}")
    else:
        steps = np.diff(t)
        big = np.iinfo(np.int64).max
        run = np.minimum.accumulate(np.where(steps > 0, steps, big)) if n > 1 else np.zeros(0, np.int64)
        base = np.r_[0, np.where(run == big, 0, run)].astype(np.int64)

    bucket = (t // r_ns) * r_ns
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    last_row = np.r_[starts[1:], n] - 1
    group = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, n]))
    o = df["open"].to_numpy(dtype=np.float64)
    c = df["close"].to_numpy(dtype=np.float64)
    run_high = pd.Series(df["high"].to_numpy(dtype=np.float64)).groupby(group).cummax().to_numpy()
    run_low = pd.Series(df["low"].to_numpy(dtype=np.float64)).groupby(group).cummin().to_numpy()
    closes_at = bucket[starts] + r_ns
    rows = np.arange(n)
    k = np.searchsorted(closes_at, t + base, side="right") - 1
    k = np.minimum(k, group)                              # never a bucket that starts after r
    ok = k >= 0
    kk = k[ok]
    used = np.minimum(last_row[kk], rows[ok])            # never a row after r
    out["open"][ok] = o[starts[kk]]
    out["high"][ok] = run_high[used]
    out["low"][ok] = run_low[used]
    out["close"][ok] = c[used]
    return pd.DataFrame(out, index=df.index)
