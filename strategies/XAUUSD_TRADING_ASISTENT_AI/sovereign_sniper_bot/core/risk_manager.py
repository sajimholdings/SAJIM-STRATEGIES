import datetime
import json
import os
import MetaTrader5 as mt5

class RiskManager:
    def __init__(self):
        self.log_file = "ui/telemetry_log.json"
        if not os.path.exists("ui"):
            os.makedirs("ui")
        if not os.path.exists(self.log_file):
            with open(self.log_file, "w") as f:
                json.dump([], f)

    def get_account_state(self) -> dict:
        account_info = mt5.account_info()
        if account_info is None:
            return {
                "equity": 0.0,
                "balance": 0.0,
                "drawdown_percent": 0.0,
                "consecutive_losses": 0
            }
        
        balance = account_info.balance
        equity = account_info.equity
        drawdown_percent = 0.0
        if balance > 0 and equity < balance:
            drawdown_percent = ((balance - equity) / balance) * 100.0
            
        open_positions = mt5.positions_total() or 0
            
        return {
            "equity": equity,
            "balance": balance,
            "drawdown_percent": drawdown_percent,
            "open_trades": open_positions,
            "consecutive_losses": 0  # Placeholder, requires trade history parsing
        }

    def calculate_safe_lot(self, symbol: str, risk_percent: float, sl_pips: float) -> float:
        # Simplistic placeholder logic for calculating lot size based on risk percent
        return 0.01

    def log_system_state(self, phase: str, action: str, reason: str, market_data: dict = None, risk_metrics: dict = None):
        entry = {
            "Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Phase": phase,
            "Action": action,
            "Reason": reason,
            "MarketData": market_data or {},
            "RiskMetrics": risk_metrics or {}
        }
        print(f"[{entry['Timestamp']}] {action}: {reason}")
        
        try:
            with open(self.log_file, "r") as f:
                logs = json.load(f)
        except:
            logs = []
            
        logs.insert(0, entry)
        logs = logs[:100]  # Keep last 100
        
        with open(self.log_file, "w") as f:
            json.dump(logs, f, indent=2)
