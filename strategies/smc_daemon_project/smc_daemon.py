#!/usr/bin/env python3
"""
SMC Algorithmic Trading Daemon (smc_daemon.py) - Forex Edition
-------------------------------------------------------------------------
Author: Lead Systems Architect & Quant Developer
Description: A standalone, autonomous Forex/Metals intraday trading daemon.
             Trades majors and Gold (e.g., EURUSD, GBPUSD, XAUUSD) on MT5.
             Relies strictly on raw OHLCV and mathematical fractals (SMC).
             Completely avoids lagging indicators (RSI, MACD, MAs, etc.).
             Features 100% non-blocking MT5 operations wrapped in asyncio threads.
             Includes strict dynamic position sizing (2% risk limit of balance).
             Enforces defensive Weekend Lockouts and Temporal Session Filters.
-------------------------------------------------------------------------
"""

import os
import sys
import time
import asyncio
import logging
from datetime import datetime
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Try importing MetaTrader5 defensively (handles platforms/environments where not installed)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# Load environment variables
load_dotenv()

# Setup Terminal Color Logging
class ColorFormatter(logging.Formatter):
    """Custom log formatter with ANSI escape codes for professional styling."""
    GREY = "\033[90m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

    FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"

    def __init__(self):
        super().__init__()
        self.FORMATS = {
            logging.DEBUG: self.GREY + self.FORMAT + self.RESET,
            logging.INFO: self.CYAN + self.FORMAT + self.RESET,
            logging.WARNING: self.YELLOW + self.FORMAT + self.RESET,
            logging.ERROR: self.RED + self.FORMAT + self.RESET,
            logging.CRITICAL: self.RED + self.BOLD + self.FORMAT + self.RESET
        }

    def format(self, record):
        # Inject custom prefix styling based on specific SMC phases
        msg = record.getMessage()
        if "[WAITING]" in msg:
            record.msg = self.GREY + msg + self.RESET
        elif "[TRIGGER]" in msg or "[HTF_SWEEP]" in msg:
            record.msg = self.YELLOW + self.BOLD + msg + self.RESET
        elif "[BOS]" in msg:
            record.msg = self.MAGENTA + self.BOLD + msg + self.RESET
        elif "[INDUCEMENT]" in msg:
            record.msg = self.BLUE + self.BOLD + msg + self.RESET
        elif "[EXECUTION]" in msg:
            record.msg = self.GREEN + self.BOLD + msg + self.RESET
        elif "[TRADE_MANAGEMENT]" in msg or "[VOLUME_CALC]" in msg:
            record.msg = self.CYAN + self.BOLD + msg + self.RESET
        elif "[WEEKEND_LOCKOUT]" in msg or "[SESSION_FILTER]" in msg:
            record.msg = self.RED + self.BOLD + msg + self.RESET

        log_fmt = self.FORMATS.get(record.levelno, self.FORMAT)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

# Initialize logger
logger = logging.getLogger("SMCDaemon")
logger.setLevel(logging.DEBUG)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(ColorFormatter())
logger.addHandler(stdout_handler)

# File handler for permanent logs
os.makedirs("logs", exist_ok=True)
file_handler = logging.FileHandler(os.path.join("logs", "smc_daemon.log"), encoding="utf-8")
file_formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)


# -------------------------------------------------------------------------
# NON-BLOCKING METATRADER 5 THREAD WRAPPERS (asyncio.to_thread)
# -------------------------------------------------------------------------
class AsyncMT5:
    """Helper wrapper to ensure all MT5 synchronous C-calls are offloaded to threads."""

    @staticmethod
    async def initialize(path=None, login=0, password="", server="", timeout=5000):
        if not MT5_AVAILABLE:
            return False
        if login and password and server:
            if path is not None:
                return await asyncio.to_thread(
                    mt5.initialize, path=path, login=login, password=password, server=server, timeout=timeout
                )
            else:
                return await asyncio.to_thread(
                    mt5.initialize, login=login, password=password, server=server, timeout=timeout
                )
        else:
            if path is not None:
                return await asyncio.to_thread(mt5.initialize, path=path)
            else:
                return await asyncio.to_thread(mt5.initialize)

    @staticmethod
    async def login(login, password="", server=""):
        if not MT5_AVAILABLE:
            return False
        return await asyncio.to_thread(mt5.login, login, password=password, server=server)

    @staticmethod
    async def copy_rates_from_pos(symbol, timeframe, start_pos, count):
        if not MT5_AVAILABLE:
            return None
        return await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, timeframe, start_pos, count)

    @staticmethod
    async def symbol_info(symbol):
        if not MT5_AVAILABLE:
            return None
        return await asyncio.to_thread(mt5.symbol_info, symbol)

    @staticmethod
    async def symbol_info_tick(symbol):
        if not MT5_AVAILABLE:
            return None
        return await asyncio.to_thread(mt5.symbol_info_tick, symbol)

    @staticmethod
    async def order_send(request):
        if not MT5_AVAILABLE:
            return None
        return await asyncio.to_thread(mt5.order_send, request)

    @staticmethod
    async def orders_get(symbol=None, ticket=None):
        if not MT5_AVAILABLE:
            return []
        if ticket is not None:
            return await asyncio.to_thread(mt5.orders_get, ticket=ticket)
        elif symbol is not None:
            return await asyncio.to_thread(mt5.orders_get, symbol=symbol)
        return await asyncio.to_thread(mt5.orders_get)

    @staticmethod
    async def positions_get(symbol=None, ticket=None):
        if not MT5_AVAILABLE:
            return []
        if ticket is not None:
            return await asyncio.to_thread(mt5.positions_get, ticket=ticket)
        elif symbol is not None:
            return await asyncio.to_thread(mt5.positions_get, symbol=symbol)
        return await asyncio.to_thread(mt5.positions_get)

    @staticmethod
    async def shutdown():
        if not MT5_AVAILABLE:
            return
        await asyncio.to_thread(mt5.shutdown)


# -------------------------------------------------------------------------
# THE TRADING STATE MACHINE & SMC LOGIC CLASS
# -------------------------------------------------------------------------
class AssetTrader:
    """Manages the SMC 5-Phase State Machine and calculations for a single Forex asset."""

    # State Machine Constants
    STATE_SCANNING_HTF = "SCANNING_HTF"
    STATE_HUNTING_LTF_BOS = "HUNTING_LTF_BOS"
    STATE_WAITING_INDUCEMENT = "WAITING_INDUCEMENT"
    STATE_ORDER_PLACED = "ORDER_PLACED"
    STATE_POSITION_ACTIVE = "POSITION_ACTIVE"

    def __init__(self, symbol, config, is_mock=False):
        self.symbol = symbol
        self.config = config
        self.is_mock = is_mock

        # State Variables
        self.state = self.STATE_SCANNING_HTF
        self.directional_bias = None  # "BUY" or "SELL"

        # HTF Sweep Variables (Phase 2)
        self.htf_sweep_candle_time = None
        self.htf_sweep_level = 0.0
        self.htf_sweep_low_1H = 0.0
        self.htf_sweep_high_1H = 0.0
        self.htf_atr_1H = 0.0
        self.opposing_htf_liquidity = 0.0

        # LTF BOS Variables (Phase 3)
        self.bos_trigger_level = 0.0
        self.p_origin = 0.0
        self.p_break = 0.0
        self.ltf_sweep_extreme_time = None

        # Inducement Variables (Phase 4)
        self.inducement_level = 0.0
        self.inducement_swept = False

        # Execution & Risk (Phase 5)
        self.entry_price = 0.0
        self.sl_price = 0.0
        self.tp_price = 0.0
        self.limit_order_ticket = None
        self.active_position_ticket = None
        self.breakeven_triggered = False

        # Invalidation & Timeout limits
        self.max_bos_candles = 50  # Max LTF candles to wait for BOS after HTF sweep
        self.bos_candle_count = 0
        self.max_inducement_candles = 80  # Max LTF candles to wait for Inducement sweep
        self.inducement_candle_count = 0

        # Local cache trackers
        self.last_processed_h1_time = None
        self.last_processed_m5_time = None
        
        # Lockout caching trackers
        self._last_lockout_print_hour = None
        self._last_session_print_hour = None

        logger.info(f"Initialized AssetTrader for {self.symbol} in {'MOCK' if self.is_mock else 'MT5_LIVE'} mode.")

    # --- FOREX TEMPORAL FILTERS (WEEKEND & KILLZONES) ---
    async def check_weekend_lockout(self):
        """
        Forex Weekend Lockout filter: Friday 21:00 to Monday 01:00 broker time.
        Uses live MT5 tick time if available, else system time.
        Bypassed dynamically for 24/7/365 Synthetic Indices.
        """
        now = datetime.now()
        
        # Check if the symbol is a Synthetic Index (always trades 24/7/365)
        is_synthetic = any(x in self.symbol.lower() for x in ["index", "volatility", "step", "jump", "crash", "boom"])
        if is_synthetic:
            return False, now
            
        if self.is_mock:
            # Bypass lockout in mock mode to allow simulated execution loops to run on weekends
            return False, now
            
        if not self.is_mock and MT5_AVAILABLE:
            try:
                # Query broker time using latest tick of the current symbol
                tick = await AsyncMT5.symbol_info_tick(self.symbol)
                if tick is not None:
                    now = datetime.fromtimestamp(tick.time)
            except Exception:
                pass
        
        weekday = now.weekday()  # Monday = 0, ..., Sunday = 6
        hour = now.hour
        
        # Friday after 21:00
        if weekday == 4 and hour >= 21:
            return True, now
        # Saturday and Sunday
        if weekday in (5, 6):
            return True, now
        # Monday before 01:00
        if weekday == 0 and hour < 1:
            return True, now
            
        return False, now

    def is_high_volume_session(self, broker_time):
        """
        Forex high-volume session check: allows new setups to form
        only during active session hours (09:00 to 21:00 broker time, covering London/NY overlaps).
        Bypassed dynamically for 24/7/365 Synthetic Indices.
        """
        # Check if the symbol is a Synthetic Index (always trades 24/7/365)
        is_synthetic = any(x in self.symbol.lower() for x in ["index", "volatility", "step", "jump", "crash", "boom"])
        if is_synthetic:
            return True
            
        if self.is_mock:
            # Bypass session filter in mock mode to allow simulated setups at any hour
            return True
            
        hour = broker_time.hour
        return 9 <= hour <= 21

    # --- DYNAMIC POSITION SIZING (2% RISK MANAGEMENT) ---
    async def calculate_risk_volume(self, entry, sl):
        """
        Calculate dynamic Forex lot size risking 2% of the account balance.
        Formula: Lot_Size = (Risk_Amount * Tick_Size) / (SL_Distance * Tick_Value)
        """
        risk_percentage = 0.02
        default_balance = 100.0  # Fallback small balance
        
        try:
            # Query live account balance
            if not self.is_mock and MT5_AVAILABLE:
                acc_info = await asyncio.to_thread(mt5.account_info)
                if acc_info is not None:
                    balance = acc_info.balance
                else:
                    balance = default_balance
            else:
                balance = default_balance

            risk_amount = balance * risk_percentage

            if self.is_mock:
                # Simulated symbol parameters
                point = 0.00001 if "JPY" not in self.symbol and "XAU" not in self.symbol else 0.01
                tick_size = point
                tick_value = 1.0
                volume_min = 0.01
                volume_max = 500.0
                volume_step = 0.01
            else:
                # Live MT5 parameters
                sym_info = await AsyncMT5.symbol_info(self.symbol)
                if sym_info is None:
                    logger.error(f"[{self.symbol}] Could not fetch symbol info for volume calculation. Using default 0.01.")
                    return 0.01

                point = sym_info.point
                tick_size = sym_info.trade_tick_size
                tick_value = sym_info.trade_tick_value
                volume_min = sym_info.volume_min
                volume_max = sym_info.volume_max
                volume_step = sym_info.volume_step

            sl_distance = abs(entry - sl)
            if sl_distance == 0:
                return volume_min

            # Forex contract risk math
            # Lot_Size = (Risk_Amount * Tick_Size) / (SL_Distance * Tick_Value)
            raw_lot = (risk_amount * tick_size) / (sl_distance * tick_value)
            
            # Normalize to broker lot step constraints
            lot = round(raw_lot / volume_step) * volume_step
            # Clip within broker's minimum and maximum constraints
            lot = max(volume_min, min(volume_max, lot))
            
            logger.info(f"[VOLUME_CALC] [{self.symbol}] Account Balance: ${balance:.2f} | Risk Amount (2%): ${risk_amount:.2f}")
            logger.info(f"[VOLUME_CALC] [{self.symbol}] SL Distance: {sl_distance:.5f} | Tick Value: {tick_value:.5f} | Raw Lot: {raw_lot:.4f} -> Normalized Lot: {lot:.2f}")
            
            return round(lot, 2)

        except Exception as e:
            logger.error(f"[{self.symbol}] Exception during volume calculation: {e}")
            return 0.01

    # --- PHASE 1: Data Ingestion & Formatting ---
    def calculate_atr(self, df, period=14):
        """Calculate Average True Range (ATR) strictly mathematically."""
        high = df['high']
        low = df['low']
        close_prev = df['close'].shift(1)

        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        return atr

    async def update_market_data(self, mock_dfs=None):
        """Fetches OHLCV and builds DataFrames for 1H and 5M timeframes."""
        if self.is_mock:
            if mock_dfs:
                return mock_dfs['df_1H'], mock_dfs['df_5M']
            return None, None

        try:
            # 1H Timeframe fetching (50 lookback)
            rates_1h = await AsyncMT5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_H1, 0, 50)
            if rates_1h is None or len(rates_1h) < 30:
                logger.error(f"[{self.symbol}] Failed to fetch H1 rates.")
                return None, None

            df_1H = pd.DataFrame(rates_1h)
            df_1H['time'] = pd.to_datetime(df_1H['time'], unit='s')
            df_1H['atr'] = self.calculate_atr(df_1H, period=14)

            # 5M Timeframe fetching (200 lookback)
            rates_5m = await AsyncMT5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 0, 200)
            if rates_5m is None or len(rates_5m) < 50:
                logger.error(f"[{self.symbol}] Failed to fetch M5 rates.")
                return None, None

            df_5M = pd.DataFrame(rates_5m)
            df_5M['time'] = pd.to_datetime(df_5M['time'], unit='s')

            return df_1H, df_5M

        except Exception as e:
            logger.error(f"[{self.symbol}] Exception during data ingestion: {e}")
            return None, None

    # --- THE CORE STATE MACHINE ENGINE ---
    async def process_tick(self, mock_dfs=None):
        """State Machine tick processor called periodically."""
        # 1. Temporal Check: Weekend Lockout Friday 21:00 to Monday 01:00 broker time
        is_locked, broker_time = await self.check_weekend_lockout()
        if is_locked:
            if self.state != self.STATE_SCANNING_HTF or self.limit_order_ticket is not None:
                logger.warning(f"[WEEKEND_LOCKOUT] [{self.symbol}] Entering weekend lockout at broker time {broker_time}. "
                               f"Cancelling pending limit orders and resetting scanner.")
                await self.cancel_pending_order()
                self.reset_to_scanning()
            
            # Throttle lockout logging to once per hour to keep console readable
            current_hour = broker_time.strftime("%Y-%m-%d %H")
            if self._last_lockout_print_hour != current_hour:
                logger.warning(f"[WEEKEND_LOCKOUT] [{self.symbol}] Scanning paused. Dormant until Monday 01:00 broker time. (Current: {broker_time})")
                self._last_lockout_print_hour = current_hour
            return
        
        # Clear weekend prints tracker when weekend ends
        self._last_lockout_print_hour = None

        df_1H, df_5M = await self.update_market_data(mock_dfs)
        if df_1H is None or df_5M is None:
            return

        latest_h1_time = df_1H['time'].iloc[-1]
        latest_m5_time = df_5M['time'].iloc[-1]

        # 2. State Progression
        if self.state == self.STATE_SCANNING_HTF:
            await self._run_phase2_scanning(df_1H, latest_h1_time, broker_time)

        elif self.state == self.STATE_HUNTING_LTF_BOS:
            await self._run_phase3_bos_hunt(df_5M, df_1H, latest_m5_time, broker_time)

        elif self.state == self.STATE_WAITING_INDUCEMENT:
            await self._run_phase4_inducement_wait(df_5M, latest_m5_time)

        elif self.state == self.STATE_ORDER_PLACED:
            await self._run_phase5_order_monitoring(df_5M)

        elif self.state == self.STATE_POSITION_ACTIVE:
            await self._run_phase5_position_management(df_5M)

    # --- PHASE 2: HTF Sweep Detection ---
    async def _run_phase2_scanning(self, df_1H, latest_h1_time, broker_time):
        """Scans the last closed H1 candle for a liquidity sweep rejection."""
        # Temporal Check: Session Filter (restrict triggers to London & New York session overlaps)
        if not self.is_high_volume_session(broker_time):
            current_hour = broker_time.strftime("%Y-%m-%d %H")
            if self._last_session_print_hour != current_hour:
                logger.info(f"[SESSION_FILTER] [{self.symbol}] Outside high-volume session overlap hours (09:00 - 21:00). Waiting... (Current: {broker_time})")
                self._last_session_print_hour = current_hour
            return

        self._last_session_print_hour = None

        if self.last_processed_h1_time == latest_h1_time:
            return

        c = df_1H.iloc[-2]
        c_time = c['time']

        # Preceding 20-candle rolling range BEFORE the sweep candle
        idx_sweep = len(df_1H) - 2
        history = df_1H.iloc[idx_sweep-20:idx_sweep]
        
        rolling_high = history['high'].max()
        rolling_low = history['low'].min()

        logger.debug(f"[WAITING] Scanning 1H for {self.symbol}... Rolling High: {rolling_high:.5f}, Rolling Low: {rolling_low:.5f}. Last Closed (Open: {c['open']:.5f}, High: {c['high']:.5f}, Low: {c['low']:.5f}, Close: {c['close']:.5f})")

        # 1. Bearish Sweep (Swept HTF High)
        if c['high'] > rolling_high and c['close'] <= rolling_high and c['open'] <= rolling_high:
            self.directional_bias = "SELL"
            self.state = self.STATE_HUNTING_LTF_BOS
            self.htf_sweep_candle_time = c_time
            self.htf_sweep_level = rolling_high
            self.htf_sweep_high_1H = c['high']
            self.htf_atr_1H = c['atr']
            self.opposing_htf_liquidity = rolling_low  # Opposing low is target profit
            self.bos_candle_count = 0
            self.last_processed_h1_time = latest_h1_time

            logger.info(f"[TRIGGER] 1H Bearish Sweep Detected on {self.symbol} at {c_time}! "
                        f"Swept High: {rolling_high:.5f} (Wicked: {c['high']:.5f}, Body Close: {c['close']:.5f}). "
                        f"Locked Directional Bias = SELL. Hunting 5M BOS...")

        # 2. Bullish Sweep (Swept HTF Low)
        elif c['low'] < rolling_low and c['close'] >= rolling_low and c['open'] >= rolling_low:
            self.directional_bias = "BUY"
            self.state = self.STATE_HUNTING_LTF_BOS
            self.htf_sweep_candle_time = c_time
            self.htf_sweep_level = rolling_low
            self.htf_sweep_low_1H = c['low']
            self.htf_atr_1H = c['atr']
            self.opposing_htf_liquidity = rolling_high  # Opposing high is target profit
            self.bos_candle_count = 0
            self.last_processed_h1_time = latest_h1_time

            logger.info(f"[TRIGGER] 1H Bullish Sweep Detected on {self.symbol} at {c_time}! "
                        f"Swept Low: {rolling_low:.5f} (Wicked: {c['low']:.5f}, Body Close: {c['close']:.5f}). "
                        f"Locked Directional Bias = BUY. Hunting 5M BOS...")

    # --- PHASE 3: LTF Break of Structure (BOS) ---
    async def _run_phase3_bos_hunt(self, df_5M, df_1H, latest_m5_time, broker_time):
        """Scans the 5M timeframe for a Break of Structure in the direction of the bias."""
        if not self.is_high_volume_session(broker_time):
            # Do not allow new BOS confirmations outside session overlaps
            return

        if self.last_processed_m5_time == latest_m5_time:
            return
        self.last_processed_m5_time = latest_m5_time

        self.bos_candle_count += 1
        if self.bos_candle_count > self.max_bos_candles:
            logger.warning(f"[{self.symbol}] Hunting 5M BOS timed out (>{self.max_bos_candles} candles). Resetting to HTF scanning.")
            self.reset_to_scanning()
            return

        sweep_5m_candles = df_5M[df_5M['time'] >= pd.to_datetime(self.htf_sweep_candle_time)]
        if sweep_5m_candles.empty:
            return

        if self.directional_bias == "BUY":
            extreme_idx = sweep_5m_candles['low'].idxmin()
            extreme_price = sweep_5m_candles.loc[extreme_idx, 'low']
            extreme_time = sweep_5m_candles.loc[extreme_idx, 'time']

            # Find structural swing high on 5M before extreme_idx (only log once)
            if self.bos_trigger_level == 0.0:
                bos_trigger = None
                for idx in range(extreme_idx - 2, 2, -1):
                    h_prev2 = df_5M.loc[idx - 2, 'high']
                    h_prev1 = df_5M.loc[idx - 1, 'high']
                    h_curr = df_5M.loc[idx, 'high']
                    h_next1 = df_5M.loc[idx + 1, 'high']
                    h_next2 = df_5M.loc[idx + 2, 'high']

                    if h_curr > max(h_prev2, h_prev1, h_next1, h_next2):
                        bos_trigger = h_curr
                        logger.info(f"[{self.symbol}] Found recent structural 5M Swing High before sweep extremum: "
                                    f"{bos_trigger:.5f} at {df_5M.loc[idx, 'time']}")
                        break

                if bos_trigger is None:
                    bos_trigger = df_5M.loc[extreme_idx - 15:extreme_idx, 'high'].max()
                    logger.info(f"[{self.symbol}] Swing High not found by fractal. Fallback rolling High: {bos_trigger:.5f}")

                self.bos_trigger_level = bos_trigger
                self.ltf_sweep_extreme_time = extreme_time

            # Monitor for BOS: body closes aggressively ABOVE swing high
            c_m5 = df_5M.iloc[-2]
            if c_m5['close'] > self.bos_trigger_level and c_m5['time'] > self.ltf_sweep_extreme_time:
                self.p_origin = extreme_price
                self.p_break = df_5M.iloc[-20:]['high'].max()
                self.state = self.STATE_WAITING_INDUCEMENT
                self.inducement_candle_count = 0
                logger.info(f"[BOS] 5M Bullish BOS Confirmed on {self.symbol}! "
                            f"Body Close: {c_m5['close']:.5f} > Swing High: {self.bos_trigger_level:.5f}. "
                            f"Impulse Leg: P_origin={self.p_origin:.5f}, P_break={self.p_break:.5f}. Hunting Inducement...")

        elif self.directional_bias == "SELL":
            extreme_idx = sweep_5m_candles['high'].idxmax()
            extreme_price = sweep_5m_candles.loc[extreme_idx, 'high']
            extreme_time = sweep_5m_candles.loc[extreme_idx, 'time']

            # Find structural swing low on 5M before extreme_idx (only log once)
            if self.bos_trigger_level == 0.0:
                bos_trigger = None
                for idx in range(extreme_idx - 2, 2, -1):
                    l_prev2 = df_5M.loc[idx - 2, 'low']
                    l_prev1 = df_5M.loc[idx - 1, 'low']
                    l_curr = df_5M.loc[idx, 'low']
                    l_next1 = df_5M.loc[idx + 1, 'low']
                    l_next2 = df_5M.loc[idx + 2, 'low']

                    if l_curr < min(l_prev2, l_prev1, l_next1, l_next2):
                        bos_trigger = l_curr
                        logger.info(f"[{self.symbol}] Found recent structural 5M Swing Low before sweep extremum: "
                                    f"{bos_trigger:.5f} at {df_5M.loc[idx, 'time']}")
                        break

                if bos_trigger is None:
                    bos_trigger = df_5M.loc[extreme_idx - 15:extreme_idx, 'low'].min()
                    logger.info(f"[{self.symbol}] Swing Low not found by fractal. Fallback rolling Low: {bos_trigger:.5f}")

                self.bos_trigger_level = bos_trigger
                self.ltf_sweep_extreme_time = extreme_time

            # Monitor for BOS: body closes aggressively BELOW swing low
            c_m5 = df_5M.iloc[-2]
            if c_m5['close'] < self.bos_trigger_level and c_m5['time'] > self.ltf_sweep_extreme_time:
                self.p_origin = extreme_price
                self.p_break = df_5M.iloc[-20:]['low'].min()
                self.state = self.STATE_WAITING_INDUCEMENT
                self.inducement_candle_count = 0
                logger.info(f"[BOS] 5M Bearish BOS Confirmed on {self.symbol}! "
                            f"Body Close: {c_m5['close']:.5f} < Swing Low: {self.bos_trigger_level:.5f}. "
                            f"Impulse Leg: P_origin={self.p_origin:.5f}, P_break={self.p_break:.5f}. Hunting Inducement...")

    # --- PHASE 4: Internal Inducement & The Trap ---
    async def _run_phase4_inducement_wait(self, df_5M, latest_m5_time):
        """Identifies the internal fractal inducement and waits for it to be swept and rejected."""
        self.inducement_candle_count += 1
        if self.inducement_candle_count > self.max_inducement_candles:
            logger.warning(f"[{self.symbol}] Waiting for inducement timed out. Resetting to HTF scanning.")
            self.reset_to_scanning()
            return

        leg_df = df_5M[df_5M['time'] >= pd.to_datetime(self.ltf_sweep_extreme_time)]
        if len(leg_df) < 5:
            return

        if self.directional_bias == "BUY":
            # 5-Bar Fractal algorithm for internal swing low: L_i < min(L_i-2, L_i-1, L_i+1, L_i+2)
            fractals = []
            for idx in range(leg_df.index[2], leg_df.index[-2]):
                l_prev2 = df_5M.loc[idx - 2, 'low']
                l_prev1 = df_5M.loc[idx - 1, 'low']
                l_curr = df_5M.loc[idx, 'low']
                l_next1 = df_5M.loc[idx + 1, 'low']
                l_next2 = df_5M.loc[idx + 2, 'low']

                if l_curr < min(l_prev2, l_prev1, l_next1, l_next2):
                    if l_curr > self.p_origin:
                        fractals.append((l_curr, df_5M.loc[idx, 'time']))

            if not fractals:
                return

            self.inducement_level = fractals[-1][0]
            
            c = df_5M.iloc[-2]
            if c['low'] < self.inducement_level and c['close'] >= self.inducement_level:
                logger.info(f"[INDUCEMENT] Inducement Sweep & Rejection Confirmed on {self.symbol}! "
                            f"Candle Low: {c['low']:.5f} swept internal swing low Li: {self.inducement_level:.5f} "
                            f"and closed at {c['close']:.5f}. Entering Execution phase...")
                await self.execute_limit_order()

        elif self.directional_bias == "SELL":
            # 5-Bar Fractal algorithm for internal swing high: H_i > max(H_i-2, H_i-1, H_i+1, H_i+2)
            fractals = []
            for idx in range(leg_df.index[2], leg_df.index[-2]):
                h_prev2 = df_5M.loc[idx - 2, 'high']
                h_prev1 = df_5M.loc[idx - 1, 'high']
                h_curr = df_5M.loc[idx, 'high']
                h_next1 = df_5M.loc[idx + 1, 'high']
                h_next2 = df_5M.loc[idx + 2, 'high']

                if h_curr > max(h_prev2, h_prev1, h_next1, h_next2):
                    if h_curr < self.p_origin:
                        fractals.append((h_curr, df_5M.loc[idx, 'time']))

            if not fractals:
                return

            self.inducement_level = fractals[-1][0]

            c = df_5M.iloc[-2]
            if c['high'] > self.inducement_level and c['close'] <= self.inducement_level:
                logger.info(f"[INDUCEMENT] Inducement Sweep & Rejection Confirmed on {self.symbol}! "
                            f"Candle High: {c['high']:.5f} swept internal swing high Hi: {self.inducement_level:.5f} "
                            f"and closed at {c['close']:.5f}. Entering Execution phase...")
                await self.execute_limit_order()

    # --- PHASE 5: Precision Execution & Risk Management ---
    async def execute_limit_order(self):
        """Calculates entries, SL, TP, dynamic Forex lot sizes, and places an MT5 limit order."""
        # Entry: 0.25 level of impulse leg
        self.entry_price = self.p_origin + 0.25 * (self.p_break - self.p_origin)

        # Stop Loss: 2 ATR below/above absolute low/high of H1 sweep candle
        if self.directional_bias == "BUY":
            self.sl_price = self.htf_sweep_low_1H - 2 * self.htf_atr_1H
            self.tp_price = self.opposing_htf_liquidity
            order_type_str = "BUY_LIMIT"
        else:
            self.sl_price = self.htf_sweep_high_1H + 2 * self.htf_atr_1H
            self.tp_price = self.opposing_htf_liquidity
            order_type_str = "SELL_LIMIT"

        logger.info(f"[EXECUTION] Calculating precision execution for {self.symbol}:")
        logger.info(f"            - Bias: {self.directional_bias}")
        logger.info(f"            - Impulse Origin: {self.p_origin:.5f}, Impulse Break: {self.p_break:.5f}")
        logger.info(f"            - Entry (0.25 level): {self.entry_price:.5f}")
        logger.info(f"            - Stop Loss (2 ATR): {self.sl_price:.5f}")
        logger.info(f"            - Take Profit (Opposing Liquidity): {self.tp_price:.5f}")

        # Check for invalid setups
        if self.directional_bias == "BUY" and self.sl_price >= self.entry_price:
            logger.error(f"[{self.symbol}] Invalid Buy SL computed. Resetting state.")
            self.reset_to_scanning()
            return
        elif self.directional_bias == "SELL" and self.sl_price <= self.entry_price:
            logger.error(f"[{self.symbol}] Invalid Sell SL computed. Resetting state.")
            self.reset_to_scanning()
            return

        # Calculate dynamic Forex lot size based on 2% risk limit of account balance
        lot = await self.calculate_risk_volume(self.entry_price, self.sl_price)

        if self.is_mock:
            self.limit_order_ticket = 999999 + int(time.time()) % 100000
            self.state = self.STATE_ORDER_PLACED
            logger.info(f"[EXECUTION] [MOCK MODE] Placed {order_type_str} order #{self.limit_order_ticket} of {lot} lots at {self.entry_price:.5f}.")
            return

        # MT5 LIVE Execution
        try:
            sym_info = await AsyncMT5.symbol_info(self.symbol)
            if sym_info is None:
                logger.error(f"[{self.symbol}] Could not get symbol info from MT5.")
                self.reset_to_scanning()
                return

            digits = sym_info.digits

            # Format prices to match symbol digits
            entry_f = round(self.entry_price, digits)
            sl_f = round(self.sl_price, digits)
            tp_f = round(self.tp_price, digits)

            action = mt5.TRADE_ACTION_PENDING
            order_type = mt5.ORDER_TYPE_BUY_LIMIT if self.directional_bias == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

            request = {
                "action": action,
                "symbol": self.symbol,
                "volume": lot,
                "type": order_type,
                "price": entry_f,
                "sl": sl_f,
                "tp": tp_f,
                "type_time": mt5.ORDER_TIME_DAY,
                "comment": "SMC Forex Limit Order",
            }

            res = await AsyncMT5.order_send(request)
            if res is None:
                logger.error(f"[{self.symbol}] MT5 Order send returned None.")
                self.reset_to_scanning()
                return

            if res.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"[{self.symbol}] Failed to place limit order. Retcode: {res.retcode} | {res.comment}")
                self.reset_to_scanning()
            else:
                self.limit_order_ticket = res.order
                self.state = self.STATE_ORDER_PLACED
                logger.info(f"[EXECUTION] [MT5 LIVE] Successfully placed pending limit order #{self.limit_order_ticket} of {lot} lots at {entry_f}.")

        except Exception as e:
            logger.error(f"[{self.symbol}] Exception during limit order placement: {e}")
            self.reset_to_scanning()

    async def _run_phase5_order_monitoring(self, df_5M):
        """Monitors the limit order for fill or structural invalidation."""
        latest_candle = df_5M.iloc[-1]
        
        if self.directional_bias == "BUY" and latest_candle['low'] < self.p_origin:
            logger.warning(f"[TRADE_MANAGEMENT] Setup invalidated: Price fell below P_origin ({self.p_origin:.5f}). "
                           f"Cancelling order and resetting.")
            await self.cancel_pending_order()
            self.reset_to_scanning()
            return
        elif self.directional_bias == "SELL" and latest_candle['high'] > self.p_origin:
            logger.warning(f"[TRADE_MANAGEMENT] Setup invalidated: Price rose above P_origin ({self.p_origin:.5f}). "
                           f"Cancelling order and resetting.")
            await self.cancel_pending_order()
            self.reset_to_scanning()
            return

        if self.is_mock:
            # Simulation fill
            if np.random.rand() > 0.5:
                self.active_position_ticket = self.limit_order_ticket + 500
                self.state = self.STATE_POSITION_ACTIVE
                self.breakeven_triggered = False
                logger.info(f"[EXECUTION] [MOCK MODE] Limit Order #{self.limit_order_ticket} filled. Position active!")
            return

        # MT5 LIVE monitoring
        try:
            orders = await AsyncMT5.orders_get(ticket=self.limit_order_ticket)
            if not orders or len(orders) == 0:
                # Check active positions
                positions = await AsyncMT5.positions_get(symbol=self.symbol)
                for pos in positions:
                    if pos.comment == "SMC Forex Limit Order" or pos.identifier == self.limit_order_ticket:
                        self.active_position_ticket = pos.ticket
                        self.state = self.STATE_POSITION_ACTIVE
                        self.breakeven_triggered = False
                        logger.info(f"[EXECUTION] [MT5 LIVE] Limit Order #{self.limit_order_ticket} was filled. Active position: #{self.active_position_ticket}")
                        return
                
                logger.warning(f"[{self.symbol}] Order #{self.limit_order_ticket} disappeared. Resetting.")
                self.reset_to_scanning()

        except Exception as e:
            logger.error(f"[{self.symbol}] Exception during order monitoring: {e}")

    async def _run_phase5_position_management(self, df_5M):
        """Manages active trades. Moves SL to BE once 50% TP distance is covered."""
        if self.is_mock:
            sim_progress = getattr(self, "_sim_progress", 0) + 0.15
            self._sim_progress = sim_progress

            tp_distance = abs(self.tp_price - self.entry_price)
            if self.directional_bias == "BUY":
                current_price = self.entry_price + sim_progress * tp_distance
            else:
                current_price = self.entry_price - sim_progress * tp_distance

            logger.info(f"[TRADE_MANAGEMENT] [MOCK] Tracking position #{self.active_position_ticket}. Entry: {self.entry_price:.5f}, "
                        f"Current: {current_price:.5f}, Target: {self.tp_price:.5f}. Progress: {sim_progress*100:.1f}% to TP")

            # Break-even check (50% distance)
            if sim_progress >= 0.50 and not self.breakeven_triggered:
                self.sl_price = self.entry_price
                self.breakeven_triggered = True
                logger.info(f"[TRADE_MANAGEMENT] [MOCK] Price reached 50% distance to TP! "
                            f"Modified Stop Loss to Break-Even (SL: {self.sl_price:.5f})")

            # Take Profit / Stop Loss Simulation
            if sim_progress >= 1.0:
                logger.info(f"[TRADE_MANAGEMENT] [MOCK] Take Profit hit at {self.tp_price:.5f}! Trade successfully closed. Resetting daemon.")
                self.reset_to_scanning()
                self._sim_progress = 0
            elif current_price < self.sl_price if self.directional_bias == "BUY" else current_price > self.sl_price:
                logger.info(f"[TRADE_MANAGEMENT] [MOCK] Stop Loss hit at {self.sl_price:.5f}. Resetting daemon.")
                self.reset_to_scanning()
                self._sim_progress = 0
            return

        # MT5 LIVE Management
        try:
            positions = await AsyncMT5.positions_get(ticket=self.active_position_ticket)
            if not positions or len(positions) == 0:
                logger.info(f"[TRADE_MANAGEMENT] [MT5 LIVE] Position #{self.active_position_ticket} has closed. Resetting.")
                self.reset_to_scanning()
                return

            pos = positions[0]
            current_price = pos.price_current
            tp_distance = abs(self.tp_price - self.entry_price)
            halfway_point = 0.5 * tp_distance

            # Check for Break-even condition
            if not self.breakeven_triggered:
                if self.directional_bias == "BUY":
                    distance_covered = current_price - self.entry_price
                    if distance_covered >= halfway_point:
                        await self.modify_sl_to_breakeven(pos)
                elif self.directional_bias == "SELL":
                    distance_covered = self.entry_price - current_price
                    if distance_covered >= halfway_point:
                        await self.modify_sl_to_breakeven(pos)

        except Exception as e:
            logger.error(f"[{self.symbol}] Exception during active position tracking: {e}")

    async def modify_sl_to_breakeven(self, pos):
        """Modifies active position's SL to entry price."""
        try:
            sym_info = await AsyncMT5.symbol_info(self.symbol)
            digits = sym_info.digits
            entry_f = round(self.entry_price, digits)

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": self.symbol,
                "sl": entry_f,
                "tp": pos.tp,
            }

            res = await AsyncMT5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self.breakeven_triggered = True
                self.sl_price = self.entry_price
                logger.info(f"[TRADE_MANAGEMENT] [MT5 LIVE] Successfully moved SL to Break-Even ({entry_f}) for position #{pos.ticket}!")
            else:
                ret_code = res.retcode if res else "Unknown"
                comment = res.comment if res else "No response"
                logger.error(f"[TRADE_MANAGEMENT] Failed to modify SL. Retcode: {ret_code} | {comment}")

        except Exception as e:
            logger.error(f"[{self.symbol}] Exception modifying SL: {e}")

    async def cancel_pending_order(self):
        """Cancels placed pending limit order."""
        if self.is_mock or self.limit_order_ticket is None:
            return

        try:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": self.limit_order_ticket,
            }
            res = await AsyncMT5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"[TRADE_MANAGEMENT] Cancelled pending limit order #{self.limit_order_ticket}.")
            else:
                logger.error(f"[TRADE_MANAGEMENT] Failed to cancel order #{self.limit_order_ticket}.")
        except Exception as e:
            logger.error(f"[{self.symbol}] Exception cancelling order: {e}")

    def reset_to_scanning(self):
        """Cleanly resets state variables back to HTF scanning."""
        logger.info(f"[{self.symbol}] State transition: {self.state} -> {self.STATE_SCANNING_HTF}. Clearing buffers.")
        self.state = self.STATE_SCANNING_HTF
        self.directional_bias = None
        self.limit_order_ticket = None
        self.active_position_ticket = None
        self.breakeven_triggered = False
        if hasattr(self, "_sim_progress"):
            delattr(self, "_sim_progress")


# -------------------------------------------------------------------------
# HIGH-FIDELITY FOREX PRICE FEED SIMULATOR (MOCK DATA GENERATOR)
# -------------------------------------------------------------------------
class MarketSimulator:
    """Generates continuous realistic Forex price feeds (e.g. EURUSD, GBPUSD, XAUUSD)."""

    def __init__(self, symbol):
        self.symbol = symbol
        
        # Select realistic Forex/Gold structures based on symbols
        if "EURUSD" in symbol:
            self.base_price = 1.08000
            self.noise = 0.00010
            self.point = 0.00001
        elif "GBPUSD" in symbol:
            self.base_price = 1.25000
            self.noise = 0.00012
            self.point = 0.00001
        elif "XAUUSD" in symbol:
            self.base_price = 2300.00
            self.noise = 0.40
            self.point = 0.01
        else:
            self.base_price = 100.0
            self.noise = 0.1
            self.point = 0.01

        self.step = 0
        self.m5_times = []
        self.m5_closes = []
        self.generate_initial_data()

    def generate_initial_data(self):
        t_now = datetime.now()
        prices = [self.base_price]
        for _ in range(250):
            prices.append(prices[-1] + np.random.normal(0, self.noise * 1.5))

        self.m5_closes = prices
        self.m5_times = [t_now - pd.Timedelta(minutes=5 * (250 - i)) for i in range(251)]

    def get_market_data(self):
        """Generates realistic market price feeds to step the state machine."""
        self.step += 1
        t_now = datetime.now()

        self.m5_times.append(t_now)
        
        # Step sequence to guarantee a beautiful Forex SMC lifecycle
        if self.step < 6:
            next_close = self.m5_closes[-1] + np.random.normal(0, self.noise * 0.5)
        elif self.step == 6:
            # Sweep Low on H1
            next_close = self.base_price - self.noise * 20
        elif self.step == 7:
            next_close = self.base_price - self.noise * 5
        elif self.step == 8:
            next_close = self.base_price + self.noise * 5
        elif self.step == 9:
            # BOS confirmed
            next_close = self.base_price + self.noise * 10
        elif self.step == 10:
            next_close = self.base_price + self.noise * 20
        elif self.step == 11:
            next_close = self.base_price + self.noise * 16
        elif self.step == 12:
            next_close = self.base_price + self.noise * 18
        elif self.step == 13:
            # Create inducement fractal low
            next_close = self.base_price + self.noise * 12
        elif self.step == 14:
            next_close = self.base_price + self.noise * 15
        elif self.step == 15:
            next_close = self.base_price + self.noise * 17
        elif self.step == 16:
            # Sweep Inducement and reject
            next_close = self.base_price + self.noise * 13.5
        else:
            next_close = self.m5_closes[-1] + np.random.normal(self.noise * 0.5, self.noise * 0.5)

        self.m5_closes.append(next_close)

        df_5M = pd.DataFrame()
        df_5M['time'] = self.m5_times[-200:]
        
        closes = self.m5_closes[-200:]
        df_5M['close'] = closes
        df_5M['open'] = [closes[i-1] if i > 0 else closes[i] for i in range(len(closes))]
        
        highs = []
        lows = []
        for i in range(len(closes)):
            step_idx = self.step - (200 - i)
            o = df_5M['open'].iloc[i]
            c = df_5M['close'].iloc[i]
            
            h = max(o, c) + abs(np.random.normal(0, self.noise * 0.2))
            l = min(o, c) - abs(np.random.normal(0, self.noise * 0.2))
            
            if step_idx == 6:
                l = self.base_price - self.noise * 28
            elif step_idx == 16:
                l = self.base_price + self.noise * 9.5
                
            highs.append(h)
            lows.append(l)
            
        df_5M['high'] = highs
        df_5M['low'] = lows
        df_5M['tick_volume'] = [100 + int(np.random.rand()*50) for _ in range(200)]

        # Assemble H1 DataFrame
        df_1H = pd.DataFrame()
        h1_times = []
        h1_opens = []
        h1_highs = []
        h1_lows = []
        h1_closes = []
        
        t_base = t_now - pd.Timedelta(hours=60)
        for hour in range(50):
            h_time = t_base + pd.Timedelta(hours=hour)
            h1_times.append(h_time)
            
            if hour == 48:  # HTF sweep candle (index -2)
                h1_opens.append(self.base_price - self.noise * 10)
                h1_highs.append(self.base_price - self.noise * 2)
                h1_lows.append(self.base_price - self.noise * 28)
                h1_closes.append(self.base_price - self.noise * 5)
            else:
                prev_c = h1_closes[-1] if len(h1_closes) > 0 else self.base_price
                o = prev_c
                c = prev_c + np.random.normal(0, self.noise * 2.0)
                
                if hour < 48:
                    c = max(self.base_price - self.noise * 14, min(self.base_price + self.noise * 20, c))
                
                h = max(o, c) + abs(np.random.normal(0, self.noise * 1.0))
                l = min(o, c) - abs(np.random.normal(0, self.noise * 1.0))
                
                if hour == 30:
                    l = self.base_price - self.noise * 15
                    
                h1_opens.append(o)
                h1_highs.append(h)
                h1_lows.append(l)
                h1_closes.append(c)

        df_1H['time'] = h1_times
        df_1H['open'] = h1_opens
        df_1H['high'] = h1_highs
        df_1H['low'] = h1_lows
        df_1H['close'] = h1_closes
        df_1H['atr'] = self.calculate_atr(df_1H, period=14)

        return {'df_1H': df_1H, 'df_5M': df_5M}

    def calculate_atr(self, df, period=14):
        high = df['high']
        low = df['low']
        close_prev = df['close'].shift(1)
        tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean().fillna(self.noise * 4.5)


# -------------------------------------------------------------------------
# DAEMON INITIALIZATION & LOOP
# -------------------------------------------------------------------------
async def main():
    logger.info("=========================================================================")
    logger.info("       STARTING SMC FOREX ALGORITHMIC TRADING DAEMON IN WINDOWS MODE")
    logger.info("=========================================================================")

    # Configuration loading
    symbols_env = os.environ.get("SYMBOLS", "EURUSD,GBPUSD,XAUUSD")
    symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
    
    # Determine if mock mode is forced.
    # SAFETY: MOCK_MODE is now mandatory. Previously the default was live
    # trading ("False"), which silently placed real orders on any machine
    # with a running MT5 terminal.
    mock_env = os.environ.get("MOCK_MODE")
    if mock_env is None:
        logger.critical(
            "[SAFETY] MOCK_MODE is not set. Refusing to start. "
            "Set MOCK_MODE=true (simulation) or MOCK_MODE=false "
            "(live trading — explicit opt-in, at your own risk)."
        )
        sys.exit(1)
    mock_forced = mock_env.lower() in ("true", "1", "yes")
    
    # MT5 connection settings
    login_env = os.environ.get("MT5_LOGIN", "0")
    login = int(login_env) if login_env.strip() else 0
    password = os.environ.get("MT5_PASSWORD", "")
    server = os.environ.get("MT5_SERVER", "")
    poll_env = os.environ.get("POLL_INTERVAL_SECONDS", "5")
    poll_interval = int(poll_env) if poll_env.strip() else 5

    is_mock = mock_forced
    if not MT5_AVAILABLE:
        logger.warning("[WARNING] MetaTrader5 package is not installed. FORCING MOCK MODE.")
        is_mock = True

    if not is_mock:
        logger.info(f"[MT5] Attempting to connect to {server} broker...")
        # 1. First try attaching to already running terminal (extremely safe and fast)
        success = await AsyncMT5.initialize(login=login, password=password, server=server)
        if not success:
            # 2. If that fails, try launching terminal from the default installation path
            default_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
            logger.info(f"[MT5] Connection failed. Retrying by launching terminal from {default_path}...")
            success = await AsyncMT5.initialize(path=default_path, login=login, password=password, server=server)
            
        if not success:
            err = mt5.last_error() if MT5_AVAILABLE else "Unknown"
            logger.warning(f"[WARNING] MT5 connection failed. Error: {err}. (Verify terminal is running!)")
            logger.warning("          Falling back to Forex simulated MOCK MODE.")
            is_mock = True
        else:
            logger.info("[MT5] Connected successfully to the MT5 terminal!")
            terminal_info = await asyncio.to_thread(mt5.terminal_info)
            if terminal_info:
                logger.info(f"[MT5] Connected Company: {terminal_info.company} | Server: {terminal_info.server}")
            else:
                logger.warning("[MT5] Connected, but terminal details unavailable.")

    # Instantiate asset trackers
    traders = {}
    simulators = {}

    for sym in symbols:
        traders[sym] = AssetTrader(sym, config={}, is_mock=is_mock)
        if is_mock:
            simulators[sym] = MarketSimulator(sym)

    logger.info(f"SMC Forex Daemon active and scanning {len(symbols)} assets. Tick interval: {poll_interval}s.")
    logger.info("-------------------------------------------------------------------------")

    try:
        while True:
            tasks = []
            for sym in symbols:
                if is_mock:
                    mock_dfs = simulators[sym].get_market_data()
                    tasks.append(traders[sym].process_tick(mock_dfs=mock_dfs))
                else:
                    tasks.append(traders[sym].process_tick())

            # Concurrently gather all asset ticks without blocking the loop
            await asyncio.gather(*tasks)
            await asyncio.sleep(poll_interval)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Daemon termination signal received.")
    except Exception as e:
        logger.critical(f"Unhandled daemon loop exception: {e}")
    finally:
        if not is_mock and MT5_AVAILABLE:
            logger.info("[MT5] Shutting down MT5 API connection cleanly.")
            await AsyncMT5.shutdown()
        logger.info("SMC Forex Trading Daemon shutdown complete.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSMC Forex Trading Daemon stopped.")
