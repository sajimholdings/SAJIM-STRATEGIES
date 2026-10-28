"""Causality and sanity tests on synthetic M5 data with volatility clustering."""

import numpy as np
import pandas as pd
import pytest

from sajim_quant.noise.data import m5_returns
from sajim_quant.noise.diagnose import NoiseState
from sajim_quant.noise.model import build, evaluate


def _synthetic_bars(n_days=260, seed=7):
    """24/5 M5 bars: GARCH-style daily vol, a London/NY intraday hump, weekend gaps."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2024-01-01 00:00", periods=n_days * 288, freq="5min")
    times = times[times.dayofweek < 5]
    day = times.normalize()
    udays = day.unique()
    var = np.empty(len(udays))
    v, eps2 = 1e-4, 1e-4
    for i in range(len(udays)):
        v = 2e-6 + 0.15 * eps2 + 0.83 * v
        eps2 = v * rng.standard_t(6) ** 2 * 4 / 6
        var[i] = v
    daily_var = pd.Series(var, index=udays).reindex(day).to_numpy()
    hour = times.hour.to_numpy()
    shape = 1 + 1.5 * np.exp(-((hour - 13.5) ** 2) / 6)
    shape = shape / shape.mean()
    r = rng.standard_normal(len(times)) * np.sqrt(daily_var * shape / 288)
    close = 100 * np.exp(np.cumsum(r))
    return pd.DataFrame({"time": times, "close": close})


def _write(tmp_path, bars, symbol="SYN"):
    d = tmp_path / symbol
    d.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(d / "M5.parquet", index=False)
    return tmp_path


def test_forecasts_ignore_future_data():
    bars = _synthetic_bars()
    t_full, p_full = build(m5_returns(bars))
    cut = bars["time"].iloc[len(bars) * 3 // 4]
    future = bars["time"] > cut
    shocked = bars.copy()
    shocked.loc[future, "close"] = shocked.loc[future, "close"] * np.exp(
        np.random.default_rng(1).standard_normal(future.sum()) * 0.05)
    t_shock, p_shock = build(m5_returns(shocked))

    cut_day = (cut + pd.Timedelta(hours=3)).floor("D")
    past = t_full["day"] <= cut_day  # forecast for day D uses days < D only
    pd.testing.assert_series_equal(t_full.loc[past, "rv_forecast"], t_shock.loc[past, "rv_forecast"])
    pd.testing.assert_frame_equal(p_full[past.to_numpy()], p_shock[past.to_numpy()])


def test_entry_tags_ignore_future_bars(tmp_path):
    bars = _synthetic_bars()
    entry = pd.Timestamp("2024-09-10 14:00")
    root_a = _write(tmp_path / "a", bars)
    shocked = bars.copy()
    later = shocked["time"] >= entry
    shocked.loc[later, "close"] *= 1.5
    root_b = _write(tmp_path / "b", shocked)

    tag = lambda root: NoiseState("SYN", root).at_entry(entry, None, "BUY", 100.0, None)
    a, b = tag(root_a), tag(root_b)
    assert a["np_status"] == "ok"
    assert a == b


def test_har_beats_naive_on_clustered_vol():
    res = evaluate(build(m5_returns(_synthetic_bars(n_days=400)))[0])
    assert res["har_rv"]["qlike"] < res["naive_yesterday"]["qlike"]


def test_profile_finds_the_intraday_hump():
    _, prof = build(m5_returns(_synthetic_bars()))
    last = prof.dropna().iloc[-1]
    assert last.idxmax() in (12, 13, 14, 15)
    assert last.sum() == pytest.approx(1.0)
