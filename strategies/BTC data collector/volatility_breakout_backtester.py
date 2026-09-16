import pandas as pd
import numpy as np
import os
import argparse
import sys
from datetime import datetime

# Force UTF-8 output so Unicode symbols print on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

class VolatilityBreakoutBacktester:
    def __init__(self, risk_per_trade=0.01, initial_balance=10000):
        self.risk_per_trade = risk_per_trade
        self.initial_balance = initial_balance
        self.results = {}

    def calculate_indicators(self, df):
        # ATR calculation
        high_low = df["High"] - df["Low"]
        high_close = np.abs(df["High"] - df["Close"].shift())
        low_close = np.abs(df["Low"] - df["Close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df["ATR"] = true_range.rolling(14).mean()
        
        # Regime filter (ATR > ATR_MA)
        df["ATR_MA_50"] = df["ATR"].rolling(50).mean()
        
        # Trailing Stop levels pre-calculated for convenience
        df["TrailingStop_Long"] = df["Close"] - (df["ATR"] * 3)
        df["TrailingStop_Short"] = df["Close"] + (df["ATR"] * 3)
        
        return df

    def run_backtest(self, file_path, body_atr_multiplier=1.0, trailing_stop_atr_multiplier=3.0, fee=0.001, df=None):
        if df is None:
            df = pd.read_csv(file_path)
            # Map our column names to what the script expects
            df = df.rename(columns={
                "datetime_utc": "Datetime",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close"
            })
            df["Datetime"] = pd.to_datetime(df["Datetime"])
            df = df.set_index("Datetime")
            # Calculate indicators before dropping NAs
            df = self.calculate_indicators(df)
            df = df.dropna()
        
        balance = self.initial_balance
        peak_balance = balance
        max_dd = 0.0
        
        trades = []
        in_position = False
        position_type = None
        entry_price = 0
        position_size = 0
        current_trailing_stop = 0
        risk_amount_at_entry = 0
        
        for i in range(len(df)):
            row = df.iloc[i]
            
            # Calculate candle body size
            candle_body = abs(row["Open"] - row["Close"])
            
            if not in_position:
                # Entry Logic: Volatility Breakout
                if row["ATR"] > row["ATR_MA_50"] and candle_body > (row["ATR"] * body_atr_multiplier):
                    if row["Close"] > row["Open"]: # Bullish candle, go LONG
                        position_type = "LONG"
                        entry_price = row["Close"]
                        current_trailing_stop = row["Close"] - (row["ATR"] * trailing_stop_atr_multiplier)
                        
                        risk_amount_at_entry = balance * self.risk_per_trade
                        position_size = risk_amount_at_entry / (row["ATR"] * trailing_stop_atr_multiplier)
                        
                        trades.append({
                            "type": position_type,
                            "entry": entry_price,
                            "size": position_size,
                            "entry_time": row.name,
                            "trailing_stop": current_trailing_stop,
                            "risk_usd": risk_amount_at_entry
                        })
                        in_position = True
                        
                    elif row["Close"] < row["Open"]: # Bearish candle, go SHORT
                        position_type = "SHORT"
                        entry_price = row["Close"]
                        current_trailing_stop = row["Close"] + (row["ATR"] * trailing_stop_atr_multiplier)
                        
                        risk_amount_at_entry = balance * self.risk_per_trade
                        position_size = risk_amount_at_entry / (row["ATR"] * trailing_stop_atr_multiplier)
                        
                        trades.append({
                            "type": position_type,
                            "entry": entry_price,
                            "size": position_size,
                            "entry_time": row.name,
                            "trailing_stop": current_trailing_stop,
                            "risk_usd": risk_amount_at_entry
                        })
                        in_position = True
            
            else:
                # Update Trailing Stop
                if position_type == "LONG":
                    current_trailing_stop = max(current_trailing_stop, row["Close"] - (row["ATR"] * trailing_stop_atr_multiplier))
                    if row["Low"] <= current_trailing_stop:
                        pnl_gross = (current_trailing_stop - entry_price) * position_size
                        pnl_net = pnl_gross - (entry_price * position_size * fee)
                        balance += pnl_net
                        trades[-1].update({"exit_price": current_trailing_stop, "pnl": pnl_net, "exit_time": row.name})
                        in_position = False
                        
                elif position_type == "SHORT":
                    current_trailing_stop = min(current_trailing_stop, row["Close"] + (row["ATR"] * trailing_stop_atr_multiplier))
                    if row["High"] >= current_trailing_stop:
                        pnl_gross = (entry_price - current_trailing_stop) * position_size
                        pnl_net = pnl_gross - (entry_price * position_size * fee)
                        balance += pnl_net
                        trades[-1].update({"exit_price": current_trailing_stop, "pnl": pnl_net, "exit_time": row.name})
                        in_position = False

                if not in_position:
                    position_type = None
                    entry_price = 0
                    position_size = 0
                    current_trailing_stop = 0
                    
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                    if dd > max_dd:
                        max_dd = dd
        
        # Close out any remaining position at the end of the data
        if in_position:
            last_row = df.iloc[-1]
            if position_type == "LONG":
                pnl_gross = (last_row["Close"] - entry_price) * position_size
            else:
                pnl_gross = (entry_price - last_row["Close"]) * position_size
            pnl_net = pnl_gross - (entry_price * position_size * fee)
            balance += pnl_net
            trades[-1].update({"exit_price": last_row["Close"], "pnl": pnl_net, "exit_time": last_row.name})
            
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
            if dd > max_dd:
                max_dd = dd
            
        winners = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0]
        losers = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0]
        win_rate = len(winners) / len(trades) if trades else 0
        profit_factor = sum(winners) / abs(sum(losers)) if abs(sum(losers)) > 0 else np.inf
        
        avg_win = sum(winners) / len(winners) if winners else 0
        avg_loss = abs(sum(losers) / len(losers)) if losers else 0
        
        # Expectancy in R-multiples: pnl / risk_amount
        r_multiples = [t.get("pnl", 0) / t["risk_usd"] for t in trades if "risk_usd" in t and t["risk_usd"] > 0]
        expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
        
        instrument_name = os.path.basename(file_path)
        self.results[instrument_name] = {
            "final_balance": balance,
            "total_trades": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "expectancy_r": expectancy_r,
            "max_drawdown": max_dd
        }
        return self.results[instrument_name]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Volatility Breakout Backtester")
    parser.add_argument("--file", type=str, required=True, help="Path to the CSV data file")
    parser.add_argument("--body_atr_multiplier", type=float, default=1.0, help="Multiplier for ATR to determine candle body trigger")
    parser.add_argument("--trailing_stop_atr_multiplier", type=float, default=3.0, help="Multiplier for ATR to determine trailing stop distance")
    parser.add_argument("--fee", type=float, default=0.001, help="Transaction fee as a percentage (e.g., 0.001 for 0.1%%)")
    
    args = parser.parse_args()
    
    tester = VolatilityBreakoutBacktester()
    results = tester.run_backtest(
        args.file,
        body_atr_multiplier=args.body_atr_multiplier,
        trailing_stop_atr_multiplier=args.trailing_stop_atr_multiplier,
        fee=args.fee
    )
    
    print(f"Backtest Results for {os.path.basename(args.file)}:")
    for metric, value in results.items():
        if isinstance(value, float):
            if "balance" in metric.lower():
                print(f"  {metric.replace('_', ' ').title()}: ${value:.2f}")
            elif "rate" in metric.lower() or "drawdown" in metric.lower():
                print(f"  {metric.replace('_', ' ').title()}: {value*100:.2f}%")
            else:
                print(f"  {metric.replace('_', ' ').title()}: {value:.3f}")
        else:
            print(f"  {metric.replace('_', ' ').title()}: {value}")
