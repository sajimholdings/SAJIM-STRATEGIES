import pandas as pd
import pytest

from sajim_quant.data import DataLoader


@pytest.fixture
def loader(tmp_path):
    return DataLoader(parquet_root=tmp_path / "parquet", csv_root=tmp_path / "dukascopy")


def _frame(n=48):
    t = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({"time": t, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 10})


def test_round_trip_and_filtering(loader):
    loader.save_frame(_frame(), "eurusd", "h1")
    assert loader.list_symbols() == ["EURUSD"] and loader.list_timeframes("EURUSD") == ["H1"]
    df = loader.load("EURUSD", "H1", start_date="2024-01-01 10:00", end_date="2024-01-02", set_index=True)
    assert df.index.name == "time" and list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 15


def test_csv_fallback_and_cache_build(loader):
    p = loader.csv_path("GBPUSD", "M5")
    p.parent.mkdir(parents=True)
    f = _frame(10).rename(columns={"time": "Gmt time", "volume": "Volume"})
    f.to_csv(p, index=False)
    df = loader.load("GBPUSD", "M5")
    assert len(df) == 10 and "volume" in df.columns
    written = loader.build_cache()
    assert written == [loader.parquet_path("GBPUSD", "M5")]


def test_missing_data_raises(loader):
    with pytest.raises(FileNotFoundError):
        loader.load("XAUUSD", "H1")
