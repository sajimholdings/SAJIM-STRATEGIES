import sys
import os
import time
import math
import csv
import json
import datetime
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

sys.path.insert(0, os.path.dirname(__file__))
from beep_filter import BeepFilter

# --- UNIVERSAL SETTINGS ---
SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "USDJPY", "AUDUSD", "USDCAD", "GBPJPY", "USOIL", "US30", "NDX100"]
MAGIC_NUMBER = 777012 

# --- DEATH AVOIDANCE RISK SCALER (Defaults, hot-reloaded from dna_config.json) ---
DEATH_AVOIDANCE_RISK = {
    "BTCUSD": 0.010,
    "XAUUSD": 0.010,
    "US30":   0.010,
    "NDX100": 0.010,
    "USOIL":  0.015,
    "GBPUSD": 0.025,
    "EURUSD": 0.020,
    "GBPJPY": 0.020,
}

# --- THE DNA MATRIX (Defaults, hot-reloaded from dna_config.json) ---
DNA_THRESHOLDS = {
    "EURUSD": (2.5, 1.5),
    "GBPUSD": (4.0, 2.5),
    "USDJPY": (2.0, 1.2),
    "AUDUSD": (2.5, 1.5),
    "USDCAD": (4.0, 2.5),
    "BTCUSD": (2.0, 1.2),
    "ETHUSD": (2.0, 1.2),
    "XAUUSD": (2.5, 1.5),
    "US30": (4.0, 2.5),
    "NDX100": (3.0, 2.0),
    "USOIL": (2.0, 1.2),
    "GBPJPY": (4.0, 2.5),
}

PARTIALS_TAKEN = set()
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
LOG_FILE = os.path.join(ROOT_DIR, "playbook_data.csv")
CONFIG_FILE = os.path.join(ROOT_DIR, "dna_config.json")

LAST_CONFIG_MTIME = 0
CURRENT_CONFIG_VERSION = 0
MIN_MASS_FOREX = 15.0
MIN_MASS_INDICES_METALS = 40.0

# --- OVERLORD PATCH (Aug 19): Data-derived hourly gates (LOCAL server time) ---
# Sources: live playbook_data.csv + historical logs. Winners: 11h (+49.75, 56% WR),
# 15h (+39.68, 80% WR), 20h (+294.33, 100% WR), 23h (+15.31), 19h/21h (marginal).
# Killers: 00-08h dead zone, 12-14h chop, 16-18h bleed window (today -214 in 16-18h).
TRADING_HOURS = [11, 15, 19, 20, 21, 23]

# --- OVERLORD PATCH (Aug 19): Anti-chop velocity floor (|Vel_M5| minimum) ---
MIN_VELOCITY_FLOOR = {
    "EURUSD": 0.00001, "GBPUSD": 0.00001, "USDJPY": 0.001,
    "AUDUSD": 0.00001, "USDCAD": 0.00001, "GBPJPY": 0.001,
    "XAUUSD": 0.01, "BTCUSD": 1.0, "US30": 0.5,
    "NDX100": 0.5, "USOIL": 0.005,
}

# --- OVERLORD PATCH (Aug 19): Hard lot cap + min SL distance (risk containment) ---
MAX_LOT_SIZE = 0.10
MIN_SL_TICKS = 50  # 5 pips minimum SL distance so lot sizing can't explode on tight SL

COOLDOWN_UNTIL = {}       # symbol -> datetime (15-min post-close cooldown)
WAS_IN_TRADE = {}         # symbol -> bool (close detection)
ENTRY_TIMES = []          # (symbol, local_hour) entries this hour for burst cap
MAX_ENTRIES_PER_HOUR = 2  # hard stop on machine-gun entries

def load_dynamic_config():
    global DEATH_AVOIDANCE_RISK, DNA_THRESHOLDS, MIN_MASS_FOREX, MIN_MASS_INDICES_METALS, LAST_CONFIG_MTIME, CURRENT_CONFIG_VERSION, TRADING_HOURS, MIN_VELOCITY_FLOOR
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
        if mtime != LAST_CONFIG_MTIME:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            
            if "death_avoidance_risk" in cfg:
                for k, v in cfg["death_avoidance_risk"].items():
                    DEATH_AVOIDANCE_RISK[k] = float(v)
                    
            if "dna_thresholds" in cfg:
                for k, v in cfg["dna_thresholds"].items():
                    DNA_THRESHOLDS[k] = (float(v[0]), float(v[1]))
                    
            MIN_MASS_FOREX = float(cfg.get("min_mass_forex", 15.0))
            MIN_MASS_INDICES_METALS = float(cfg.get("min_mass_indices_metals", 40.0))
            
            if "trading_hours" in cfg and isinstance(cfg["trading_hours"], list) and len(cfg["trading_hours"]) > 0:
                TRADING_HOURS = [int(h) for h in cfg["trading_hours"]]
            if "min_velocity_floor" in cfg and isinstance(cfg["min_velocity_floor"], dict):
                for k, v in cfg["min_velocity_floor"].items():
                    MIN_VELOCITY_FLOOR[k] = float(v)
            new_v = cfg.get("version", CURRENT_CONFIG_VERSION + 1)
            
            if LAST_CONFIG_MTIME != 0 and new_v != CURRENT_CONFIG_VERSION:
                print(f"\n[HERMES HOT-RELOAD] Evolved DNA Config Loaded! (Version {new_v})")
                print(f"   Min Mass Forex: {MIN_MASS_FOREX} | Min Mass Indices/Metals: {MIN_MASS_INDICES_METALS}")
                print(f"   Notes: {cfg.get('hermes_notes', 'N/A')}\n")
                
            CURRENT_CONFIG_VERSION = new_v
            LAST_CONFIG_MTIME = mtime
    except Exception as e:
        print(f"[CONFIG WARNING] Error reloading dna_config.json: {e}")

def initialize_mt5():
    for attempt in range(5):
        if mt5.initialize():
            account = mt5.account_info()
            if account:
                print(f"\n[MT5] Connected to Account {account.login} ({account.server}). Balance: ${account.balance:.2f}")
                for sym in SYMBOLS:
                    mt5.symbol_select(sym, True)
                return True
        time.sleep(2)
    print("initialize() failed, error code =", mt5.last_error())
    return False
        
    # Initialize logger
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Symbol", "Action", "Mass", "Vel_M1", "Vel_M5", "Vel_M15", "Z_Score", "Profit"])

def log_playbook_data(symbol, action, mass, vel_m1, vel_m5, vel_m15, z_score, profit=0.0):
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([time.strftime('%Y-%m-%d %H:%M:%S'), symbol, action, f"{mass:.2f}", f"{vel_m1:.4f}", f"{vel_m5:.4f}", f"{vel_m15:.4f}", f"{z_score:.2f}", f"{profit:.2f}"])

def get_live_candles(symbol, timeframe, n=300):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "tick_volume": "Volume"}, inplace=True)
    return df

def get_asymptotic_baseline(df):
    current_df = df.copy()
    total = len(df)
    for p in range(1, 10):
        beep = BeepFilter(lookback=20, z_threshold=2.0)
        filtered = beep.apply(current_df)
        noise_idx = filtered[filtered["is_noise"]].index
        if len(noise_idx) / total < 0.01 and p > 1: break
        clean_close = current_df["Close"].copy()
        for idx in noise_idx:
            loc = current_df.index.get_loc(idx)
            clean_close.iloc[loc] = clean_close.iloc[max(0, loc - 1)]
        current_df["Close"] = clean_close
    return current_df["Close"].rolling(window=15, min_periods=1).mean()

def calculate_dynamic_lot_size(symbol, account_balance, margin_free, entry_price, sl_price, direction):
    info = mt5.symbol_info(symbol)
    if not info: return None
        
    # Fetch dynamic risk parameter, default to 2.0% for stable pairs not explicitly defined
    risk_pct = DEATH_AVOIDANCE_RISK.get(symbol, 0.020)
    risk_amount = account_balance * risk_pct
    tick_value = info.trade_tick_value 
    tick_size = info.trade_tick_size
    
    if tick_value == 0 or tick_size == 0:
        return info.volume_min
        
    sl_distance = abs(entry_price - sl_price)
    ticks_at_risk = sl_distance / tick_size
    if ticks_at_risk == 0: return info.volume_min
        
    # OVERLORD PATCH: enforce minimum SL distance (5 pips) so tight baselines
    # can't explode lot size into account-killing positions (see -653 USDCAD, -330 US30)
    if ticks_at_risk < MIN_SL_TICKS:
        ticks_at_risk = MIN_SL_TICKS
        sl_distance = MIN_SL_TICKS * tick_size
        
    loss_per_lot = ticks_at_risk * tick_value
    raw_lot = risk_amount / loss_per_lot
    
    lot_step = info.volume_step
    lot = round(raw_lot / lot_step) * lot_step
    lot = max(info.volume_min, min(lot, info.volume_max))
    
    # OVERLORD PATCH: hard cap on lot size regardless of margin math
    lot = min(lot, MAX_LOT_SIZE)
    
    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL
    
    while lot >= info.volume_min:
        margin_required = mt5.order_calc_margin(order_type, symbol, lot, entry_price)
        if margin_required is None:
            lot = info.volume_min
            break
            
        # Physical reality check: Can we actually afford this trade with our FREE MARGIN?
        if margin_required < (margin_free * 0.90): # Leave a 10% free margin buffer
            break 
        else:
            lot -= lot_step
            if lot < info.volume_min:
                return None # Mathematically cannot afford the minimum lot size
            lot = round(lot / lot_step) * lot_step
            
    return float(lot)

def get_filling_mode(symbol):
    info = mt5.symbol_info(symbol)
    if not info:
        return mt5.ORDER_FILLING_IOC
    modes = info.filling_mode
    # Bit 1 = FOK (1), Bit 2 = IOC (2)
    if (modes & 2) != 0:
        return mt5.ORDER_FILLING_IOC
    elif (modes & 1) != 0:
        return mt5.ORDER_FILLING_FOK
    else:
        return mt5.ORDER_FILLING_RETURN

def send_market_order(symbol, direction, entry_price, sl_price, lot_size):
    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return None
    price = tick.ask if direction == 1 else tick.bid
    filling = get_filling_mode(symbol)
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": sl_price, 
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "Matrix MTF",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        ret_code = result.retcode if result else mt5.last_error()
        print(f"[{symbol}] Order failed: {ret_code}")
        return None
    print(f"[{symbol}] ORDER FILLED: {'BUY' if direction == 1 else 'SELL'} @ {price:.5f} | SL: {sl_price:.5f} | LOTS: {lot_size}")
    return result

def take_partial_close(symbol, pos, z_score):
    """Dynamically calculates exit percentage based on the physical pressure (Z-Score)"""
    info = mt5.symbol_info(symbol)
    if not info:
        return False
    
    # Get DNA thresholds, default to (3.0, 2.0) if not explicitly mapped yet
    t1, t2 = DNA_THRESHOLDS.get(symbol, (3.0, 2.0))
    
    abs_z = abs(z_score)
    if abs_z > t1:
        partial_pct = 0.80 # Extreme pressure: Close 80%
    elif abs_z > t2:
        partial_pct = 0.50 # Strong pressure: Close 50%
    else:
        partial_pct = 0.25 # Weak flutter: Close 25%
        
    close_volume = pos.volume * partial_pct
    close_volume = round(close_volume / info.volume_step) * info.volume_step
    
    if close_volume < info.volume_min:
        return False
        
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return False
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    filling = get_filling_mode(symbol)
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": close_volume,
        "type": order_type,
        "position": pos.ticket,
        "price": price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": f"Partial {int(partial_pct*100)}%",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"\n[{symbol}] {int(partial_pct*100)}% PARTIAL SECURED. DNA Triggered (Z={abs_z:.2f})")
        PARTIALS_TAKEN.add(pos.identifier)
        return True
    return False

def trail_stop_loss(symbol, pos, m5_baseline_price):
    info = mt5.symbol_info(symbol)
    if not info:
        return
    new_sl = round(m5_baseline_price, info.digits)
    
    improve = (pos.type == mt5.ORDER_TYPE_BUY and new_sl > pos.sl) or \
              (pos.type == mt5.ORDER_TYPE_SELL and (pos.sl == 0.0 or new_sl < pos.sl))
              
    if improve:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": pos.ticket,
            "sl": new_sl,
            "tp": pos.tp
        }
        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[{symbol}] M5 SURFACE TRAIL UPDATED -> {new_sl}")

def initialize_mt5():
    for attempt in range(5):
        if mt5.initialize():
            account = mt5.account_info()
            if account:
                print(f"\n[MT5] Connected to Account {account.login} ({account.server}). Balance: ${account.balance:.2f}")
                for sym in SYMBOLS:
                    mt5.symbol_select(sym, True)
                return True
        time.sleep(2)
    print("initialize() failed, error code =", mt5.last_error())
    return False

def run_bot():
    print(f"\n{'='*70}")
    print("  THE PURE DYNAMIC MTF MATRIX (PHASE 8)")
    print("  Features: DNA Dynamic Partials, Playbook Logger, M5 Trailing")
    print(f"{'='*70}\n")
    
    while not initialize_mt5():
        print("[MT5 RETRY] Reconnecting to MetaTrader 5 terminal in 5s...")
        time.sleep(5)
    
    while True:
        try:
            load_dynamic_config()
            account = mt5.account_info()
            if not account:
                if not mt5.initialize():
                    time.sleep(5)
                    continue
                account = mt5.account_info()
                if not account:
                    time.sleep(5)
                    continue
            balance = account.balance
            
            for symbol in SYMBOLS:
                try:
                    m1_df = get_live_candles(symbol, mt5.TIMEFRAME_M1, 300)
                    m5_df = get_live_candles(symbol, mt5.TIMEFRAME_M5, 100)
                    m15_df = get_live_candles(symbol, mt5.TIMEFRAME_M15, 100)
                    
                    if m1_df is None or m5_df is None or m15_df is None: continue
                        
                    base_m1 = get_asymptotic_baseline(m1_df)
                    base_m5 = get_asymptotic_baseline(m5_df)
                    base_m15 = get_asymptotic_baseline(m15_df)
                    
                    vel_m1 = base_m1.diff().iloc[-1]
                    vel_m5 = base_m5.diff().iloc[-1]
                    vel_m15 = base_m15.diff().iloc[-1]
                    
                    dev = m1_df["Close"] - base_m1
                    rolling_std = dev.rolling(window=100).std()
                    z_score = dev.iloc[-1] / rolling_std.iloc[-1] if rolling_std.iloc[-1] > 0 else 0
                    
                    recent_z = dev.iloc[-10:] / rolling_std.iloc[-1]
                    mass = 0
                    if abs(z_score) > 1.5:
                        dir_sign = np.sign(z_score)
                        for z in recent_z:
                            if pd.notna(z) and np.sign(z) == dir_sign and abs(z) > 1.5:
                                mass += (abs(z) * 10)
                                
                    current_price = m1_df["Close"].iloc[-1]
                    m15_sl_price = base_m15.iloc[-1]
                    
                    print(f"[{symbol}] Mass: {mass:5.2f} | Vel(M1/M5): {vel_m1:6.4f} / {vel_m5:6.4f} | Z: {z_score:4.2f}")
                    
                    positions = mt5.positions_get(symbol=symbol)
                    in_trade = False
                    
                    if positions:
                        for pos in positions:
                            if pos.magic == MAGIC_NUMBER:
                                in_trade = True
                                macro_dead = (pos.type == mt5.ORDER_TYPE_BUY and vel_m15 < 0) or \
                                             (pos.type == mt5.ORDER_TYPE_SELL and vel_m15 > 0)
                                             
                                m5_exhausted = (pos.type == mt5.ORDER_TYPE_BUY and vel_m5 < 0) or \
                                               (pos.type == mt5.ORDER_TYPE_SELL and vel_m5 > 0)
                                
                                # 1. Macro Exit (The Cause is Dead)
                                if macro_dead:
                                    print(f"\n[{symbol}] MACRO TREND DEAD. EXHAUSTION EXIT.")
                                    log_playbook_data(symbol, "MACRO_EXIT", mass, vel_m1, vel_m5, vel_m15, z_score, pos.profit)
                                    tick = mt5.symbol_info_tick(symbol)
                                    if tick:
                                        req = {
                                            "action": mt5.TRADE_ACTION_DEAL,
                                            "symbol": symbol,
                                            "volume": pos.volume,
                                            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                                            "position": pos.ticket,
                                            "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
                                            "deviation": 20,
                                            "magic": MAGIC_NUMBER,
                                            "comment": "Macro Dead",
                                            "type_time": mt5.ORDER_TIME_GTC,
                                            "type_filling": get_filling_mode(symbol),
                                        }
                                        mt5.order_send(req)
                                    
                                # 2. Secure Dynamic Partials on M5 Exhaustion (The DNA Cut)
                                elif pos.profit > 0 and m5_exhausted and pos.identifier not in PARTIALS_TAKEN:
                                    if take_partial_close(symbol, pos, z_score):
                                        log_playbook_data(symbol, "PARTIAL_CLOSE", mass, vel_m1, vel_m5, vel_m15, z_score, pos.profit)
                                    
                                # 3. Trail Stop Loss (M5 Breathing Room)
                                elif pos.profit > 0:
                                    trail_stop_loss(symbol, pos, base_m5.iloc[-1])
                                    
                    required_mass = MIN_MASS_FOREX if symbol in ["GBPUSD", "EURUSD", "USDJPY", "AUDUSD", "USDCAD", "GBPJPY"] else MIN_MASS_INDICES_METALS
                    
                    # OVERLORD PATCH: post-close cooldown detection (15 min per symbol)
                    was_in = WAS_IN_TRADE.get(symbol, False)
                    if was_in and not in_trade:
                        COOLDOWN_UNTIL[symbol] = datetime.datetime.now() + datetime.timedelta(minutes=15)
                        print(f"\n[{symbol}] TRADE CLOSED. 15-MIN COOLDOWN UNTIL {COOLDOWN_UNTIL[symbol].strftime('%H:%M:%S')}")
                    WAS_IN_TRADE[symbol] = in_trade
                    
                    # OVERLORD PATCH: entry gates
                    now_local = datetime.datetime.now()
                    local_hour = now_local.hour
                    in_cooldown = COOLDOWN_UNTIL.get(symbol) and now_local < COOLDOWN_UNTIL[symbol]
                    hour_allowed = local_hour in TRADING_HOURS
                    min_vel = MIN_VELOCITY_FLOOR.get(symbol, 0.00001)
                    velocity_ok = abs(vel_m5) >= min_vel
                    # burst cap: count entries for this symbol in the current local hour
                    hour_entries = sum(1 for (s, h) in ENTRY_TIMES if s == symbol and h == local_hour)
                    burst_ok = hour_entries < MAX_ENTRIES_PER_HOUR
                    
                    if not in_trade and mass >= required_mass:
                        direction = np.sign(z_score)
                        
                        gate_open = False
                        if symbol == "BTCUSD":
                            # Bypass M15 Macro Gate for Crypto
                            if direction == 1 and vel_m5 > 0: gate_open = True
                            if direction == -1 and vel_m5 < 0: gate_open = True
                        else:
                            # Standard M15 Gate
                            if direction == 1 and vel_m5 > 0 and vel_m15 > 0: gate_open = True
                            if direction == -1 and vel_m5 < 0 and vel_m15 < 0: gate_open = True
                        
                        if gate_open and hour_allowed and velocity_ok and burst_ok and not in_cooldown:
                            print(f"\n[{symbol}] !!! GATES OPEN. ENTERING THE VOID. !!!")
                            dynamic_lot = calculate_dynamic_lot_size(symbol, balance, account.margin_free, current_price, m15_sl_price, direction)
                            if dynamic_lot and dynamic_lot > 0:
                                res = send_market_order(symbol, direction, current_price, m15_sl_price, dynamic_lot)
                                if res:
                                    log_playbook_data(symbol, "ENTRY", mass, vel_m1, vel_m5, vel_m15, z_score, 0.0)
                                    ENTRY_TIMES.append((symbol, local_hour))
                                    # prune old entries from ENTRY_TIMES
                                    ENTRY_TIMES[:] = [(s, h) for (s, h) in ENTRY_TIMES if h == local_hour]
                except Exception as e:
                    print(f"[{symbol} WARNING] Execution cycle notice: {e}")
                time.sleep(0.05)
                            
            time.sleep(30)
            
        except KeyboardInterrupt:
            print("\nShutting down Pure Dynamic Engine...")
            mt5.shutdown()
            break
        except SystemExit:
            break
        except BaseException as e:
            print(f"[ENGINE RECOVERY] Cycle exception: {e}")
            time.sleep(10)

if __name__ == "__main__":
    while True:
        try:
            run_bot()
        except KeyboardInterrupt:
            print("\n[BEEP] Shutting down on user command.")
            break
        except SystemExit:
            break
        except BaseException as e:
            print(f"\n[SUPERVISOR WATCHDOG] Bot auto-restarting after: {e}")
            time.sleep(5)
