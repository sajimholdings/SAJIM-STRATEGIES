"""indicators.py: every indicator is causal (truncation-proof), htf/session_range expose only CLOSED bars."""

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sajim_quant.lab import indicators as ind


def synth(n: int = 3000, freq: str = "5min", seed: int = 1, start: str = "2024-09-01 21:00",
          weekends: bool = True) -> pd.DataFrame:
    """Synthetic OHLCV with weekend gaps (no Saturday bars, Sunday before 21:00 removed)."""
    idx = pd.date_range(start, periods=int(n * 1.4), freq=freq, name="time")
    if weekends:
        keep = ~((idx.dayofweek == 5) | ((idx.dayofweek == 6) & (idx.hour < 21))
                 | ((idx.dayofweek == 4) & (idx.hour >= 22)))
        idx = idx[keep]
    idx = idx[:n]
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.2, len(idx)))
    o = np.r_[c[0], c[:-1]] + rng.normal(0, 0.02, len(idx))
    h = np.maximum(o, c) + rng.uniform(0, 0.15, len(idx))
    l = np.minimum(o, c) - rng.uniform(0, 0.15, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                         "volume": rng.uniform(1, 10, len(idx))}, index=idx)


def _as_frame(x) -> pd.DataFrame:
    return x.to_frame() if isinstance(x, pd.Series) else x


def assert_causal(fn, df: pd.DataFrame, cuts=(1, 2, 15, 97, 500, 1234, 2001, 2999)):
    """Values on rows < cut are identical whether or not the later rows exist (NaN-aware)."""
    full = _as_frame(fn(df)).astype(float)
    assert full.index.equals(df.index)
    for cut in cuts:
        if cut > len(df):
            continue
        part = _as_frame(fn(df.iloc[:cut])).astype(float)
        assert part.index.equals(df.index[:cut])
        a, b = full.iloc[:cut].to_numpy(), part.to_numpy()
        same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9)
        assert same.all(), f"{fn} changed rows before cut {cut} (first row {np.argwhere(~same)[0][0]})"


INDICATORS = {
    "sma": lambda d: ind.sma(d["close"], 20),
    "ema": lambda d: ind.ema(d["close"], 20),
    "atr": lambda d: ind.atr(d, 14),
    "rsi": lambda d: ind.rsi(d["close"], 14),
    "donchian": lambda d: ind.donchian(d, 20),
    "zscore": lambda d: ind.zscore(d["close"], 30),
    "session_mask": lambda d: ind.session_mask(d.index, 7, 16),
    "session_mask_wrap": lambda d: ind.session_mask(d.index, 22, 6),
    "session_range": lambda d: ind.session_range(d, 0, 6),
    "session_range_wrap": lambda d: ind.session_range(d, 21, 2),
    "htf_30min": lambda d: ind.htf(d, "30min"),
    "htf_1h": lambda d: ind.htf(d, "1h"),
    "htf_4h": lambda d: ind.htf(d, "4h"),
    "htf_1D": lambda d: ind.htf(d, "1D"),
}


@pytest.mark.parametrize("name", sorted(INDICATORS))
@pytest.mark.parametrize("freq", ["5min", "15min", "1h"])
def test_truncation_causality(name, freq):
    df = synth(freq=freq)
    assert_causal(INDICATORS[name], df)


def test_htf_causal_even_when_the_first_step_is_a_gap():
    """A frame that starts with a weekend gap overestimates the base early on; still causal."""
    df = synth(800, freq="15min", start="2024-09-06 21:45")
    assert (df.index[1] - df.index[0]) > pd.Timedelta("1h")
    for rule in ("30min", "1h", "4h", "1D"):
        assert_causal(lambda d, r=rule: ind.htf(d, r), df, cuts=range(1, 60))


@pytest.mark.parametrize("name", sorted(INDICATORS))
def test_future_changes_do_not_leak(name):
    """Rewriting every bar after row T leaves rows <= T unchanged."""
    df = synth()
    t = 1500
    alt = df.copy()
    alt.iloc[t + 1:, :4] = alt.iloc[t + 1:, :4].to_numpy()[::-1] * 1.7
    a = _as_frame(INDICATORS[name](df)).astype(float).iloc[:t + 1].to_numpy()
    b = _as_frame(INDICATORS[name](alt)).astype(float).iloc[:t + 1].to_numpy()
    assert ((np.isnan(a) & np.isnan(b)) | (a == b)).all()


def test_naive_resample_fails_the_truncation_test():
    """The test above has teeth: an unshifted resample (the canary) is caught by it."""
    def leaky(d):
        return d["close"].resample("1h").last().reindex(d.index, method="ffill")
    with pytest.raises(AssertionError):
        assert_causal(leaky, synth(freq="15min"))


# ── htf: only CLOSED higher-TF bars ─────────────────────────────────────────

def test_htf_values_are_the_last_closed_bucket():
    idx = pd.date_range("2024-09-02 00:00", periods=12, freq="15min")
    df = pd.DataFrame({"open": np.arange(12.0), "high": np.arange(12.0) + 10,
                       "low": np.arange(12.0) - 10, "close": np.arange(12.0) + 0.5}, index=idx)
    out = ind.htf(df, "1h")
    assert out.iloc[:3].isna().all().all()                     # 00:00..00:30: hour not closed yet
    # 00:45 bar ends at 01:00 -> the 00:00 hour is closed on that row
    for r in (3, 4, 5, 6):
        assert out.iloc[r].tolist() == [0.0, 13.0, -10.0, 3.5]
    for r in (7, 8, 9, 10):
        assert out.iloc[r].tolist() == [4.0, 17.0, -6.0, 7.5]
    assert out.iloc[11].tolist() == [8.0, 21.0, -2.0, 11.5]


def test_htf_with_gaps_and_explicit_base():
    idx = pd.DatetimeIndex(["2024-09-02 00:00", "2024-09-02 00:15", "2024-09-02 01:30", "2024-09-02 02:45"])
    df = pd.DataFrame({"open": [1.0, 2, 3, 4], "high": [5.0, 6, 7, 8], "low": [0.0, 1, 2, 3],
                       "close": [1.5, 2.5, 3.5, 4.5]}, index=idx)
    out = ind.htf(df, "1h", base_minutes=15)
    assert out.iloc[:2].isna().all().all()
    assert out.iloc[2].tolist() == [1.0, 6.0, 0.0, 2.5]          # 00:00 hour (only 2 bars) is closed
    assert out.iloc[3].tolist() == [4.0, 8.0, 3.0, 4.5]          # the 02:45 bar ends at 03:00: 02:00 hour closed
    # without an explicit base the 15-minute step is learnt from rows <= r (row 1)
    assert ind.htf(df, "1h").iloc[2:].equals(out.iloc[2:])
    one = ind.htf(df.iloc[:1], "1h")                             # base cannot be inferred from one bar
    assert one.isna().all().all()


def test_htf_rejects_bad_rules():
    df = synth(200, freq="1h")
    for rule in ("2h", "H4", "1W", ""):
        with pytest.raises(ValueError):
            ind.htf(df, rule)
    with pytest.raises(ValueError):
        ind.htf(df, "30min", base_minutes=60)                  # shorter than the stated base
    # inferred base: a bucket finer than the bar is simply the bar itself (still causal)
    assert ind.htf(df, "30min").iloc[5].tolist() == df.iloc[5][["open", "high", "low", "close"]].tolist()
    assert ind.htf(df, "1h").iloc[5].tolist() == df.iloc[5][["open", "high", "low", "close"]].tolist()


# ── sessions ────────────────────────────────────────────────────────────────

def test_session_range_visible_only_after_the_session_ends():
    idx = pd.date_range("2024-09-02 00:00", periods=96 * 2, freq="15min")
    rng = np.random.default_rng(3)
    c = 50 + rng.normal(0, 1, len(idx)).cumsum()
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c}, index=idx)
    out = ind.session_range(df, 0, 6)
    first_day_end = idx.get_loc(pd.Timestamp("2024-09-02 06:00"))
    assert out.iloc[:first_day_end].isna().all().all()            # 05:45 row: session not complete
    sess = df.loc["2024-09-02 00:00":"2024-09-02 05:45"]
    assert out.iloc[first_day_end].tolist() == [sess["high"].max(), sess["low"].min()]
    # stays on the 2 Sep session until 3 Sep 06:00
    r = idx.get_loc(pd.Timestamp("2024-09-03 05:45"))
    assert out.iloc[r].tolist() == [sess["high"].max(), sess["low"].min()]
    nxt = df.loc["2024-09-03 00:00":"2024-09-03 05:45"]
    r = idx.get_loc(pd.Timestamp("2024-09-03 06:00"))
    assert out.iloc[r].tolist() == [nxt["high"].max(), nxt["low"].min()]


def test_session_range_wraps_midnight():
    idx = pd.date_range("2024-09-02 20:00", periods=40, freq="30min")
    df = pd.DataFrame({"open": 1.0, "high": np.arange(40.0), "low": -np.arange(40.0), "close": 1.0}, index=idx)
    out = ind.session_range(df, 22, 2)
    end = idx.get_loc(pd.Timestamp("2024-09-03 02:00"))
    assert out.iloc[:end].isna().all().all()
    members = df.loc["2024-09-02 22:00":"2024-09-03 01:30"]
    assert out.iloc[end].tolist() == [members["high"].max(), members["low"].min()]


def test_session_mask():
    idx = pd.date_range("2024-09-02 00:00", periods=24, freq="1h")
    assert ind.session_mask(idx, 7, 16).to_numpy().nonzero()[0].tolist() == list(range(7, 16))
    assert ind.session_mask(idx, 22, 3).to_numpy().nonzero()[0].tolist() == [0, 1, 2, 22, 23]
    assert ind.session_mask(idx, 0, 24).all()
    assert ind.session_mask(idx, 7, 16).index.equals(idx)
    for bad in ((5, 5), (-1, 4), (3, 25), (2.5, 4)):
        with pytest.raises(ValueError):
            ind.session_mask(idx, *bad)


# ── values ──────────────────────────────────────────────────────────────────

def test_atr_wilder_hand_computed():
    c = np.array([10.0, 11, 12, 11, 13])
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c},
                      index=pd.date_range("2024-09-02", periods=5, freq="1h"))
    a = ind.atr(df, 3)
    assert a.iloc[:3].isna().all()                     # NaN for the first n bars
    assert a.iloc[3] == pytest.approx(2.0)             # mean TR of rows 1..3
    assert a.iloc[4] == pytest.approx((2.0 * 2 + 3.0) / 3)


def test_rsi_wilder_hand_computed():
    x = pd.Series([1.0, 2, 1, 2, 1, 2], index=pd.date_range("2024-09-02", periods=6, freq="1h"))
    r = ind.rsi(x, 2)
    assert r.iloc[:2].isna().all()
    assert r.iloc[2] == pytest.approx(50.0)
    assert r.iloc[3] == pytest.approx(75.0)
    up = pd.Series(np.arange(30.0), index=pd.date_range("2024-09-02", periods=30, freq="1h"))
    assert (ind.rsi(up, 14).dropna() == 100.0).all()
    flat = pd.Series(np.ones(30), index=up.index)
    assert (ind.rsi(flat, 14).dropna() == 50.0).all()


def test_sma_ema_donchian_zscore_values():
    df = synth(300)
    x = df["close"]
    pd.testing.assert_series_equal(ind.sma(x, 10), x.rolling(10).mean())
    pd.testing.assert_series_equal(ind.ema(x, 10), x.ewm(span=10, adjust=False, min_periods=10).mean())
    dc = ind.donchian(df, 5)
    assert list(dc.columns) == ["upper", "lower", "mid"]
    assert dc["upper"].iloc[10] == df["high"].iloc[6:11].max()          # includes the current bar
    assert dc["mid"].iloc[10] == pytest.approx((dc["upper"].iloc[10] + dc["lower"].iloc[10]) / 2)
    z = ind.zscore(x, 20)
    w = x.iloc[0:20]
    assert z.iloc[19] == pytest.approx((x.iloc[19] - w.mean()) / w.std(ddof=1))
    flat = pd.Series(np.ones(30), index=df.index[:30])
    assert ind.zscore(flat, 5).isna().all()                              # std 0 -> NaN, not inf


@pytest.mark.parametrize("n", [0, -3, 2.5, "x", None, True])
def test_bad_window_lengths(n):
    x = synth(50)["close"]
    with pytest.raises(ValueError):
        ind.sma(x, n)


def test_short_inputs_are_all_nan():
    df = synth(10)
    assert ind.atr(df, 14).isna().all() and ind.rsi(df["close"], 14).isna().all()
    assert len(ind.atr(df.iloc[:0], 14)) == 0


def test_indicators_is_self_contained():
    """Vendored into the sandbox: numpy / pandas / math / typing (and __future__) only."""
    tree = ast.parse(Path(ind.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            mods.add(node.module.split(".")[0])
    assert mods <= {"numpy", "pandas", "math", "typing", "__future__"}
    assert ind.INDICATORS_VERSION == "1"
