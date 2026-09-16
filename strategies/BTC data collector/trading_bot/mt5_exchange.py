# mt5_exchange.py
"""
MetaTrader5 execution engine for Headway broker.
Replaces exchange.py (Binance REST) as the order execution backend.

This module manages the MT5 connection lifecycle and exposes the same
interface used by bot_btc_fade.py and bot_eth_momentum.py, so the
strategy files need no changes.

MT5 must be installed and the Headway terminal must be running (or the
path must be provided in config.MT5_PATH) before calling initialize().
"""
import math
import logging
import time
import config

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logging.warning(
        "MetaTrader5 Python package not installed. "
        "Run: pip install MetaTrader5\n"
        "MT5 functions will raise RuntimeError until installed."
    )


# ─── Timeframe mapping ────────────────────────────────────────────────────────

_TF_MAP = {
    "1m":  "TIMEFRAME_M1",
    "5m":  "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h":  "TIMEFRAME_H1",
    "4h":  "TIMEFRAME_H4",
    "1d":  "TIMEFRAME_D1",
    "1w":  "TIMEFRAME_W1",
}

def _get_tf(timeframe: str):
    """Convert string timeframe (e.g. '1h') to mt5.TIMEFRAME_* constant."""
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 package not installed.")
    key = _TF_MAP.get(timeframe)
    if key is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}. Choose from: {list(_TF_MAP.keys())}")
    return getattr(mt5, key)


# ─── Connection management ────────────────────────────────────────────────────

def initialize() -> bool:
    """
    Connect to the Headway MT5 terminal.
    Must be called once at the start of each bot run (in run_btc.py / run_eth.py).
    Returns True on success.
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 package not installed. Run: pip install MetaTrader5")

    kwargs = {}
    if config.MT5_PATH:
        kwargs["path"] = config.MT5_PATH
    if config.MT5_LOGIN:
        kwargs["login"]    = config.MT5_LOGIN
        kwargs["password"] = config.MT5_PASSWORD
        kwargs["server"]   = config.MT5_SERVER

    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        logging.error(f"MT5 initialize() failed: {err}")
        return False

    # Log connection info
    info = mt5.terminal_info()
    account = mt5.account_info()
    if account:
        mode = "DEMO" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "LIVE"
        logging.info(
            f"MT5 connected | Account: {account.login} | {mode} | "
            f"Balance: {account.balance:.2f} {account.currency} | "
            f"Server: {account.server}"
        )
    return True


def shutdown():
    """Disconnect from MT5 terminal. Call in finally block of run_*.py."""
    if MT5_AVAILABLE:
        mt5.shutdown()
        logging.info("MT5 disconnected.")


# ─── Market Data ─────────────────────────────────────────────────────────────

def get_klines(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    """
    Fetch OHLCV candles from MT5 via copy_rates_from_pos().
    Returns list of candle dicts: {open_time, open, high, low, close, volume, close_time}.

    NOTE: MT5 returns candles in ascending time order.
    candles[-1] is the currently forming (incomplete) candle.
    candles[-2] is the last fully closed candle.
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 not installed.")

    tf = _get_tf(interval)

    # Ensure the symbol is visible in Market Watch
    if not mt5.symbol_select(symbol, True):
        raise ValueError(f"Symbol {symbol} not found in MT5 Market Watch. "
                         f"Check config.py BTC_SYMBOL / ETH_SYMBOL matches Headway's exact symbol name.")

    # Retry up to 3 times (MT5 occasionally returns None on first call after connect)
    rates = None
    for attempt in range(1, 4):
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, limit)
        if rates is not None and len(rates) > 0:
            break
        logging.warning(f"MT5 copy_rates_from_pos() returned None (attempt {attempt}/3). Retrying in 2s...")
        time.sleep(2)

    if rates is None or len(rates) == 0:
        err = mt5.last_error()
        raise RuntimeError(f"Failed to fetch klines for {symbol} {interval}: {err}")

    candles = []
    for r in rates:
        candles.append({
            "open_time":  int(r["time"]),
            "open":       float(r["open"]),
            "high":       float(r["high"]),
            "low":        float(r["low"]),
            "close":      float(r["close"]),
            "volume":     float(r["tick_volume"]),
            "close_time": int(r["time"]) + _interval_seconds(interval) - 1,
        })
    return candles


def _interval_seconds(interval: str) -> int:
    """Convert interval string to number of seconds (for close_time approximation)."""
    mapping = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
    return mapping.get(interval, 3600)


# ─── Account ─────────────────────────────────────────────────────────────────

def get_account_balance(asset: str = "USD") -> float:
    """
    Returns the current account EQUITY (not just balance) from MT5.
    Equity = Balance +/- open position floating PnL.
    Using equity for position sizing is safer — it reflects real current value.
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 not installed.")
    account = mt5.account_info()
    if account is None:
        err = mt5.last_error()
        raise RuntimeError(f"mt5.account_info() returned None: {err}")
    return float(account.equity)


# ─── Symbol Info ─────────────────────────────────────────────────────────────

def get_symbol_info(symbol: str) -> dict:
    """
    Returns trading constraints for the symbol from MT5:
    {
        step_size:     volume_step (lot increment),
        min_qty:       volume_min (minimum lots),
        max_qty:       volume_max (maximum lots),
        tick_size:     point (minimum price movement),
        tick_value:    trade_tick_value (USD per tick per 1 lot),
        contract_size: contract_size (units per lot, e.g. 1 for crypto CFD),
        digits:        price decimal places,
    }
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 not installed.")

    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"Symbol {symbol} not found in MT5.")

    return {
        "step_size":     info.volume_step,
        "min_qty":       info.volume_min,
        "max_qty":       info.volume_max,
        "tick_size":     info.point,
        "tick_value":    info.trade_tick_value,
        "contract_size": info.trade_contract_size,
        "digits":        info.digits,
    }


# ─── Orders ──────────────────────────────────────────────────────────────────

def place_market_order(
    symbol: str,
    side: str,
    quantity: float,   # In lots (e.g. 0.01)
    current_price: float = None,
    magic: int = None,
    comment: str = "BotEntry",
) -> dict:
    """
    Place a market order via MT5.
    side = "BUY" or "SELL"
    quantity = lot size (not units of base asset)
    Returns {fill_price: float, executed_qty: float, ticket: int}

    current_price is accepted for API compatibility but not used in live mode
    (MT5 always fills at market).
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 not installed.")

    if magic is None:
        magic = config.MT5_MAGIC_BTC  # Fallback; callers should pass the right magic

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Could not get tick for {symbol}: {mt5.last_error()}")

    price = tick.ask if side == "BUY" else tick.bid

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      symbol,
        "volume":      quantity,
        "type":        order_type,
        "price":       price,
        "deviation":   config.MT5_DEVIATION,
        "magic":       magic,
        "comment":     comment,
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result is None:
        err = mt5.last_error()
        raise RuntimeError(f"MT5 order_send() returned None for {side} {quantity} {symbol}: {err}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"MT5 order rejected | retcode: {result.retcode} | comment: {result.comment} | "
            f"Symbol: {symbol} | Side: {side} | Lots: {quantity}"
        )

    fill_price = result.price if result.price > 0 else price
    logging.info(
        f"[MT5] {side} {quantity} lots {symbol} @ {fill_price:.5f} | "
        f"Ticket: {result.order} | Retcode: {result.retcode}"
    )
    return {
        "fill_price":   fill_price,
        "executed_qty": quantity,
        "ticket":       result.order,
    }


def close_position_by_ticket(
    ticket: int,
    symbol: str,
    lots: float,
    original_side: str,
    magic: int = None,
    comment: str = "BotClose",
) -> dict:
    """
    Close an open MT5 position by its ticket number.
    This is more reliable than closing by symbol because it targets a specific position.
    original_side = the side the position was opened with ("BUY" or "SELL")
    Returns {fill_price: float, executed_qty: float}
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 not installed.")

    if magic is None:
        magic = config.MT5_MAGIC_BTC

    # To close a BUY position, we SELL. To close a SELL position, we BUY.
    close_type = mt5.ORDER_TYPE_SELL if original_side == "BUY" else mt5.ORDER_TYPE_BUY

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Could not get tick for {symbol}: {mt5.last_error()}")

    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      symbol,
        "volume":      lots,
        "type":        close_type,
        "position":    ticket,       # <-- links this order to the specific open position
        "price":       price,
        "deviation":   config.MT5_DEVIATION,
        "magic":       magic,
        "comment":     comment,
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result is None:
        err = mt5.last_error()
        raise RuntimeError(f"MT5 close_position_by_ticket() returned None for ticket {ticket}: {err}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"MT5 close failed | ticket: {ticket} | retcode: {result.retcode} | "
            f"comment: {result.comment}"
        )

    fill_price = result.price if result.price > 0 else price
    logging.info(
        f"[MT5] Closed ticket {ticket} | {symbol} {lots} lots @ {fill_price:.5f} | "
        f"Retcode: {result.retcode}"
    )
    return {
        "fill_price":   fill_price,
        "executed_qty": lots,
    }


# ─── Open Positions ───────────────────────────────────────────────────────────

def get_open_positions(symbol: str) -> dict | None:
    """
    Check if there is an open MT5 position for this symbol (tagged with bot's magic number).
    Returns {quantity, avg_entry_price, ticket, direction} or None.
    """
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 not installed.")

    positions = mt5.positions_get(symbol=symbol)
    if positions is None or len(positions) == 0:
        return None

    # Filter by magic numbers belonging to our bots
    bot_magics = {config.MT5_MAGIC_BTC, config.MT5_MAGIC_ETH}
    bot_positions = [p for p in positions if p.magic in bot_magics]

    if not bot_positions:
        return None

    # Return the first bot position (we never hold more than one per symbol)
    pos = bot_positions[0]
    direction = "LONG" if pos.type == mt5.POSITION_TYPE_BUY else "SHORT"
    return {
        "quantity":         pos.volume,
        "avg_entry_price":  pos.price_open,
        "ticket":           pos.ticket,
        "direction":        direction,
    }
