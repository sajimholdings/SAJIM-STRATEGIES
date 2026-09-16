import time
import requests
import pandas as pd
import numpy as np
import logging
import json
from datetime import datetime
from database import DatabaseManager
import sys

# Force UTF-8 output so Unicode symbols print on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ForwardBot")

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def fetch_data(config):
    symbol = config["instrument"]
    interval = config["timeframe"]
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=100"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        df = pd.DataFrame(data, columns=[
            "timestamp", "Open", "High", "Low", "Close", "Volume",
            "close_time", "quote_av", "trades", "tb_base_av", "tb_quote_av", "ignore"
        ])
        df["Datetime"] = pd.to_datetime(df["timestamp"], unit='ms')
        df = df[["Datetime", "Open", "High", "Low", "Close"]].astype({
            "Open": float, "High": float, "Low": float, "Close": float
        })
        return df
    except Exception as e:
        logger.error(f"Error fetching data: {e}")
        return None

def calculate_indicators(df):
    high_low = df["High"] - df["Low"]
    high_close = np.abs(df["High"] - df["Close"].shift())
    low_close = np.abs(df["Low"] - df["Close"].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df["ATR"] = true_range.rolling(14).mean()
    df["ATR_MA_50"] = df["ATR"].rolling(50).mean()
    return df

def run_cycle():
    config = load_config()
    db = DatabaseManager(config.get("db_name", "trading_data.db"))
    
    logger.info("Running evaluation cycle...")
    df = fetch_data(config)
    if df is None or len(df) < 55:
        logger.warning("Not enough data fetched.")
        return
        
    df = calculate_indicators(df)
    
    # We evaluate on the last closed candle.
    last_closed_candle = df.iloc[-2]
    
    status = db.get_system_status()
    balance = status["current_balance"]
    peak_balance = status["peak_balance"]
    open_trade = db.get_open_trade()
    
    # 1. Update Trailing Stop for Open Trade
    if open_trade:
        trade_id = open_trade["id"]
        pos_type = open_trade["direction"]
        entry_price = open_trade["entry_price"]
        pos_size = open_trade["position_size"]
        fee = config["fee"]
        ts_mult = config["trailing_stop_atr_multiplier"]
        
        entry_time = pd.to_datetime(open_trade["entry_time"])
        trade_df = df[df["Datetime"] > entry_time]
        
        if len(trade_df) > 0:
            if pos_type == "LONG":
                current_ts = entry_price - (df[df["Datetime"] == entry_time]["ATR"].iloc[0] * ts_mult) if len(df[df["Datetime"] == entry_time]) else entry_price
                exit_triggered = False
                exit_price = 0
                exit_time_str = ""
                
                for idx, row in trade_df.iterrows():
                    current_ts = max(current_ts, row["Close"] - (row["ATR"] * ts_mult))
                    if row["Low"] <= current_ts:
                        exit_triggered = True
                        exit_price = current_ts
                        exit_time_str = row["Datetime"].isoformat()
                        break
                        
                if exit_triggered:
                    logger.info(f"Trailing stop hit for LONG trade {trade_id} at {exit_price}")
                    pnl_gross = (exit_price - entry_price) * pos_size
                    fees_paid = (entry_price * pos_size * fee) + (exit_price * pos_size * fee)
                    pnl_net = pnl_gross - fees_paid
                    balance += pnl_net
                    if balance > peak_balance:
                        peak_balance = balance
                    db.update_trade_exit(trade_id, exit_time_str, exit_price, pnl_net, fees_paid)
                    db.record_equity(balance, (peak_balance - balance) / peak_balance if peak_balance > 0 else 0)
                    open_trade = None
                    
            elif pos_type == "SHORT":
                current_ts = entry_price + (df[df["Datetime"] == entry_time]["ATR"].iloc[0] * ts_mult) if len(df[df["Datetime"] == entry_time]) else entry_price
                exit_triggered = False
                exit_price = 0
                exit_time_str = ""
                
                for idx, row in trade_df.iterrows():
                    current_ts = min(current_ts, row["Close"] + (row["ATR"] * ts_mult))
                    if row["High"] >= current_ts:
                        exit_triggered = True
                        exit_price = current_ts
                        exit_time_str = row["Datetime"].isoformat()
                        break
                        
                if exit_triggered:
                    logger.info(f"Trailing stop hit for SHORT trade {trade_id} at {exit_price}")
                    pnl_gross = (entry_price - exit_price) * pos_size
                    fees_paid = (entry_price * pos_size * fee) + (exit_price * pos_size * fee)
                    pnl_net = pnl_gross - fees_paid
                    balance += pnl_net
                    if balance > peak_balance:
                        peak_balance = balance
                    db.update_trade_exit(trade_id, exit_time_str, exit_price, pnl_net, fees_paid)
                    db.record_equity(balance, (peak_balance - balance) / peak_balance if peak_balance > 0 else 0)
                    open_trade = None

    # 2. Check for New Entry Signal
    if not open_trade:
        row = last_closed_candle
        candle_body = abs(row["Open"] - row["Close"])
        body_mult = config["body_atr_multiplier"]
        ts_mult = config["trailing_stop_atr_multiplier"]
        
        if pd.notna(row["ATR"]) and pd.notna(row["ATR_MA_50"]) and row["ATR"] > row["ATR_MA_50"] and candle_body > (row["ATR"] * body_mult):
            direction = "LONG" if row["Close"] > row["Open"] else "SHORT"
            entry_price = row["Close"]
            
            risk_amount = balance * config["risk_per_trade"]
            pos_size = risk_amount / (row["ATR"] * ts_mult)
            
            logger.info(f"ENTRY SIGNAL: {direction} at {entry_price} (Size: {pos_size})")
            trade_id = db.record_trade(config["instrument"], direction, row["Datetime"].isoformat(), entry_price, pos_size)
            open_trade = {"id": trade_id}
            
    # Update Status
    db.update_system_status(balance, peak_balance, open_trade["id"] if open_trade else None)
    logger.info(f"Cycle complete. Balance: ${balance:.2f} | Open Trade: {'Yes' if open_trade else 'No'}")

if __name__ == "__main__":
    logger.info("Starting Forward Testing Bot...")
    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            
        # Sleep until the start of the next hour
        now = datetime.now()
        # Calculate seconds until next top of the hour
        sleep_seconds = 3600 - (now.minute * 60 + now.second)
        
        # If it's very close to the hour, wait an extra hour to not double-trigger instantly
        if sleep_seconds < 10:
            sleep_seconds += 3600
            
        logger.info(f"Sleeping for {sleep_seconds} seconds until next hourly candle...")
        time.sleep(sleep_seconds)
