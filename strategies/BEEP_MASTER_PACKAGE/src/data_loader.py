"""
BEEP Data Loader
================
Fetches historical forex price data for analysis.
Uses Yahoo Finance as the free data source.

Yahoo Finance forex tickers use the format: "EURUSD=X"
"""

import os
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


# Forex pairs we care about
FOREX_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "XAUUSD": "GC=F",       # Gold futures
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "BTCUSD": "BTC-USD",    # Bitcoin
    "ETHUSD": "ETH-USD",    # Ethereum
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "US30": "^DJI",
    "NDX100": "^NDX",
    "USOIL": "CL=F",
}

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)


def fetch_pair(
    pair: str,
    period: str = "6mo",
    interval: str = "1h",
) -> pd.DataFrame:
    """
    Fetch historical data for a forex pair.

    Args:
        pair: Short name like "EURUSD" (must be in FOREX_PAIRS)
        period: How far back to look ("1mo", "3mo", "6mo", "1y", "2y")
        interval: Candle size ("1m", "5m", "15m", "1h", "1d")

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume, plus
        a 'Pair' column identifying the instrument.
    """
    if pair not in FOREX_PAIRS:
        raise ValueError(
            f"Unknown pair '{pair}'. Available: {list(FOREX_PAIRS.keys())}"
        )

    ticker = FOREX_PAIRS[pair]
    print(f"[BEEP] Fetching {pair} ({ticker}) — period={period}, interval={interval}")

    df = yf.download(ticker, period=period, interval=interval, progress=False)

    if df.empty:
        raise RuntimeError(f"No data returned for {pair}. Check your internet connection.")

    # Flatten multi-level columns if present (yfinance sometimes returns these)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Clean up
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df["Pair"] = pair
    df.index.name = "Datetime"

    # Drop any rows with NaN prices
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

    print(f"[BEEP] Got {len(df)} candles for {pair}")
    return df


def save_data(df: pd.DataFrame, pair: str, interval: str):
    """Save fetched data to CSV for offline use."""
    ensure_data_dir()
    filename = f"{pair}_{interval}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath)
    print(f"[BEEP] Saved to {filepath}")
    return filepath


def load_data(pair: str, interval: str) -> pd.DataFrame:
    """Load previously saved data from CSV."""
    filename = f"{pair}_{interval}.csv"
    filepath = os.path.join(DATA_DIR, filename)

    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"No saved data for {pair} at {interval}. Run fetch_pair() first."
        )

    df = pd.read_csv(filepath, index_col="Datetime", parse_dates=True)
    print(f"[BEEP] Loaded {len(df)} candles from {filepath}")
    return df


def fetch_and_save(
    pair: str = "EURUSD",
    period: str = "6mo",
    interval: str = "1h",
) -> pd.DataFrame:
    """Fetch data and save it locally. Returns the DataFrame."""
    df = fetch_pair(pair, period, interval)
    save_data(df, pair, interval)
    return df


if __name__ == "__main__":
    # Quick test — fetch EURUSD 1h data for the last 6 months
    df = fetch_and_save("EURUSD", period="6mo", interval="1h")
    print(f"\nSample data:\n{df.head(10)}")
    print(f"\nShape: {df.shape}")
    print(f"Date range: {df.index.min()} to {df.index.max()}")
