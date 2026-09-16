# exchange.py
"""
Execution engine shim.

All calls are forwarded to mt5_exchange.py (Headway MetaTrader5).
This shim preserves the original API surface so bot_btc_fade.py and
bot_eth_momentum.py require no changes.

If you ever want to switch back to Binance, replace this file with the
original Binance implementation and update config.py accordingly.
"""
from mt5_exchange import (
    initialize,
    shutdown,
    get_klines,
    get_account_balance,
    get_symbol_info,
    place_market_order,
    close_position_by_ticket,
    get_open_positions,
)

__all__ = [
    "initialize",
    "shutdown",
    "get_klines",
    "get_account_balance",
    "get_symbol_info",
    "place_market_order",
    "close_position_by_ticket",
    "get_open_positions",
]
