# indicators.py
import numpy as np

def compute_atr(candles: list[dict], period: int = 14) -> np.ndarray:
    """Wilder-smoothed Average True Range. Returns array same length as candles."""
    n = len(candles)
    if n < 2:
        return np.zeros(n, dtype=np.float64)

    highs = np.array([c['high'] for c in candles], dtype=np.float64)
    lows = np.array([c['low'] for c in candles], dtype=np.float64)
    closes = np.array([c['close'] for c in candles], dtype=np.float64)

    prev_close = np.empty(n, dtype=np.float64)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]

    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close))
    )

    atr = np.zeros(n, dtype=np.float64)
    if n < period:
        return atr

    atr[period - 1] = tr[:period].mean()
    k = 1.0 / period
    for i in range(period, n):
        atr[i] = atr[i - 1] * (1.0 - k) + tr[i] * k

    return atr

def compute_ema(closes: list[float], period: int) -> np.ndarray:
    """Exponential Moving Average."""
    arr = np.array(closes, dtype=np.float64)
    n = len(arr)
    ema = np.zeros(n, dtype=np.float64)
    if n < period:
        return ema

    ema[period - 1] = arr[:period].mean()
    k = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = arr[i] * k + ema[i - 1] * (1.0 - k)
    return ema

def compute_sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    n = len(values)
    cum = np.concatenate([[0.0], np.cumsum(values)])
    sma = np.zeros(n, dtype=np.float64)
    if n >= period:
        sma[period - 1:] = (cum[period:] - cum[:n - period + 1]) / period
    return sma

def get_candle_body(candle: dict) -> float:
    """Returns close - open. Positive = bullish, negative = bearish."""
    return candle['close'] - candle['open']

def get_latest_indicators(candles: list[dict], atr_period: int, atr_ma_period: int, ema_period: int = 14) -> dict:
    """
    Convenience function. Computes all indicators and returns a dict for the latest fully closed candle.
    Assumes candles[-1] is forming, so it calculates based on candles[-2].
    """
    if len(candles) < 2:
        return {}

    closed_candle = candles[-2]
    
    # Calculate indicators up to the closed candle
    # We pass all candles and just take index -2
    closes = [c['close'] for c in candles]
    
    atr_array = compute_atr(candles, atr_period)
    atr_ma_array = compute_sma(atr_array, atr_ma_period)
    ema_array = compute_ema(closes, ema_period)
    
    return {
        'atr': atr_array[-2],
        'atr_ma': atr_ma_array[-2],
        'ema': ema_array[-2],
        'candle_body': get_candle_body(closed_candle),
        'close': closed_candle['close'],
        'high': closed_candle['high'],
        'low': closed_candle['low'],
        'open': closed_candle['open']
    }
