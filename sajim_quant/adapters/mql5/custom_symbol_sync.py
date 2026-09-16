"""
SAJIM Quant - MT5 Custom Symbol Synchronizer

Injects Dukascopy historical bars directly into MetaTrader 5 as Custom Symbols
using mt5.CustomSymbolCreate and mt5.CustomRatesUpdate / mt5.CustomRatesReplace.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from sajim_quant.data.loader import DataLoader

logger = logging.getLogger("sajim_quant.adapters.mql5.custom_symbol")


class CustomSymbolSync:
    """
    Synchronizes historical M1/H1 data from Parquet into MT5 Custom Symbols.
    """

    def __init__(self, data_loader: Optional[DataLoader] = None):
        self.loader = data_loader or DataLoader()

    def ensure_connected(self) -> bool:
        if not mt5.initialize():
            logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
            return False
        return True

    def sync_symbol(
        self,
        symbol: str,
        custom_name: Optional[str] = None,
        timeframe: str = "M1",
        spread_points: int = 15,
    ) -> bool:
        """
        Pushes cached Parquet bars for `symbol` into MT5 under `custom_name`.
        """
        if not self.ensure_connected():
            return False

        custom_name = custom_name or f"{symbol}_SAJIM"

        # 1. Load data from Parquet
        logger.info(f"Loading {symbol} {timeframe} data from Parquet...")
        df = self.loader.load(symbol=symbol, timeframe=timeframe, set_index=False)
        if df.empty:
            logger.error(f"No data available to sync for {symbol}")
            return False

        # 2. Check if custom symbol exists, if not create it
        sym_info = mt5.symbol_info(custom_name)
        if sym_info is None:
            logger.info(f"Creating Custom Symbol: {custom_name}")
            created = mt5.CustomSymbolCreate(custom_name, group="SAJIM", path=f"Custom\\SAJIM\\{custom_name}")
            if not created:
                logger.error(f"Failed to create custom symbol {custom_name}: {mt5.last_error()}")
                return False

            # Set essential trading properties
            is_gold = "XAU" in symbol
            is_crypto = "BTC" in symbol or "ETH" in symbol or "LTC" in symbol or "XRP" in symbol
            digits = 2 if (is_gold or is_crypto) else (3 if "JPY" in symbol else 5)
            point = 0.01 if digits == 2 else (0.001 if digits == 3 else 0.00001)
            contract_size = 100.0 if is_gold else (1.0 if is_crypto else 100000.0)

            mt5.CustomSymbolSetInteger(custom_name, mt5.CUSTOM_SYMBOL_DIGITS, digits)
            mt5.CustomSymbolSetDouble(custom_name, mt5.CUSTOM_SYMBOL_POINT, point)
            mt5.CustomSymbolSetDouble(custom_name, mt5.CUSTOM_SYMBOL_TRADE_CONTRACT_SIZE, contract_size)
            mt5.CustomSymbolSetString(custom_name, mt5.CUSTOM_SYMBOL_CURRENCY_BASE, "USD" if is_gold or is_crypto else symbol[:3])
            mt5.CustomSymbolSetString(custom_name, mt5.CUSTOM_SYMBOL_CURRENCY_PROFIT, "USD")
            mt5.CustomSymbolSetString(custom_name, mt5.CUSTOM_SYMBOL_CURRENCY_MARGIN, "USD")
            mt5.CustomSymbolSetInteger(custom_name, mt5.CUSTOM_SYMBOL_TRADE_CALC_MODE, mt5.SYMBOL_CALC_MODE_FOREX if not is_gold else mt5.SYMBOL_CALC_MODE_CFD)
            mt5.CustomSymbolSetInteger(custom_name, mt5.CUSTOM_SYMBOL_SPREAD, spread_points)
            mt5.CustomSymbolSetInteger(custom_name, mt5.CUSTOM_SYMBOL_TRADE_MODE, mt5.SYMBOL_TRADE_MODE_FULL)

        # 3. Format rates as numpy structured array expected by MT5
        # dtype = [('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8'), ('tick_volume', '<i8'), ('spread', '<i4'), ('real_volume', '<i8')]
        rates_dtype = np.dtype([
            ('time', '<i8'),
            ('open', '<f8'),
            ('high', '<f8'),
            ('low', '<f8'),
            ('close', '<f8'),
            ('tick_volume', '<i8'),
            ('spread', '<i4'),
            ('real_volume', '<i8'),
        ])

        rates = np.empty(len(df), dtype=rates_dtype)
        # Convert timestamp to epoch seconds (UTC)
        time_series = pd.to_datetime(df["time"]).astype("int64") // 10**9
        rates["time"] = time_series.values
        rates["open"] = df["open"].values
        rates["high"] = df["high"].values
        rates["low"] = df["low"].values
        rates["close"] = df["close"].values
        rates["tick_volume"] = (df["volume"] * 1000).astype("int64").values if "volume" in df else 1
        rates["spread"] = spread_points
        rates["real_volume"] = 0

        # 4. Replace rates in MT5
        t_from = int(rates["time"][0])
        t_to = int(rates["time"][-1])
        logger.info(f"Injecting {len(rates):,} bars into MT5 {custom_name} ({df['time'].iloc[0]} to {df['time'].iloc[-1]})...")

        updated = mt5.CustomRatesReplace(custom_name, t_from, t_to, rates)
        if updated < 0:
            logger.error(f"CustomRatesReplace failed for {custom_name}: {mt5.last_error()}")
            return False

        # Select symbol in MarketWatch
        mt5.symbol_select(custom_name, True)
        logger.info(f"Successfully synced {updated:,} rates into MT5 custom symbol '{custom_name}'.")
        return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync Parquet data to MT5 Custom Symbol")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Ticker, e.g. XAUUSD")
    args = parser.parse_args()

    syncer = CustomSymbolSync()
    syncer.sync_symbol(args.symbol, timeframe="H1")
    mt5.shutdown()
