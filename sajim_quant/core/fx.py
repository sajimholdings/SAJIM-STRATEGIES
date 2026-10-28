"""
SAJIM Quant — Quote-Currency Conversion

A price move on EURUSD produces USD directly, but a move on GBPJPY produces JPY
and a move on EURGBP produces GBP. Reporting those raw would overstate a JPY
pair's PnL by roughly 150x against a USD-quoted one — and the whole point of the
universal schema is that a leaderboard can compare them.

This module resolves the quote currency of a symbol to USD using the same local
Parquet cache the backtests read from, so no network call and no extra data are
needed. When the conversion pair is unavailable the caller is told, rather than
being handed a silent 1.0.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger("sajim_quant.core.fx")

#: quote currency -> (cache symbol, invert?). invert=True means the cached pair
#: is quoted USD/XXX so the XXX->USD rate is 1/price.
_ROUTES: Dict[str, Tuple[str, bool]] = {
    "USD": ("", False),
    "JPY": ("USDJPY", True),
    "CHF": ("USDCHF", True),
    "CAD": ("USDCAD", True),
    "GBP": ("GBPUSD", False),
    "EUR": ("EURUSD", False),
    "AUD": ("AUDUSD", False),
    "NZD": ("NZDUSD", False),
}


def quote_rate_series(
    quote_currency: str,
    timeframe: str,
    index: pd.DatetimeIndex,
    loader=None,
) -> Tuple[pd.Series, str]:
    """
    A quote-currency -> USD rate for every timestamp in `index`.

    Returns (series, description). The description is written into RunMetadata so
    a run always states how its PnL was converted. Falls back to a flat 1.0 with
    an explicit warning in the description when the conversion pair is missing,
    so a leaderboard row can be discounted rather than quietly trusted.
    """
    ccy = (quote_currency or "USD").upper()
    if ccy == "USD":
        return pd.Series(1.0, index=index), "quote currency USD (no conversion)"

    route = _ROUTES.get(ccy)
    if route is None:
        logger.warning("No USD conversion route for quote currency %s; PnL left unconverted.", ccy)
        return pd.Series(1.0, index=index), f"UNCONVERTED: no route for quote currency {ccy}"

    pair, invert = route
    if loader is None:
        from sajim_quant.data.loader import DataLoader
        loader = DataLoader()

    try:
        fx = loader.load(pair, timeframe, set_index=True)
    except FileNotFoundError:
        logger.warning(
            "Conversion pair %s %s is not cached; %s PnL left unconverted.", pair, timeframe, ccy
        )
        return pd.Series(1.0, index=index), f"UNCONVERTED: {pair} {timeframe} not cached"

    if fx.empty:
        return pd.Series(1.0, index=index), f"UNCONVERTED: {pair} {timeframe} empty"

    # Align to the strategy's bars, carrying the last known rate forward.
    aligned = fx["close"].reindex(fx.index.union(index)).ffill().reindex(index)
    aligned = aligned.bfill()
    rate = (1.0 / aligned) if invert else aligned

    rate = rate.replace([float("inf"), float("-inf")], pd.NA).astype("float64")
    if rate.isna().all():
        return pd.Series(1.0, index=index), f"UNCONVERTED: {pair} produced no usable rate"
    rate = rate.ffill().bfill()

    desc = f"{ccy}->USD via {'1/' if invert else ''}{pair} {timeframe} close"
    return rate, desc


def constant_quote_rate(quote_currency: str, timeframe: str = "H1", loader=None) -> Tuple[float, str]:
    """Single mid-period rate, for callers that cannot carry a per-bar series."""
    ccy = (quote_currency or "USD").upper()
    if ccy == "USD":
        return 1.0, "quote currency USD (no conversion)"

    route = _ROUTES.get(ccy)
    if route is None:
        return 1.0, f"UNCONVERTED: no route for quote currency {ccy}"

    pair, invert = route
    if loader is None:
        from sajim_quant.data.loader import DataLoader
        loader = DataLoader()
    try:
        fx = loader.load(pair, timeframe, set_index=True)
    except FileNotFoundError:
        return 1.0, f"UNCONVERTED: {pair} {timeframe} not cached"
    if fx.empty:
        return 1.0, f"UNCONVERTED: {pair} {timeframe} empty"

    median = float(fx["close"].median())
    if median <= 0:
        return 1.0, f"UNCONVERTED: {pair} median non-positive"
    rate = (1.0 / median) if invert else median
    return rate, f"{ccy}->USD at constant {rate:.6f} (median {pair} {timeframe})"
