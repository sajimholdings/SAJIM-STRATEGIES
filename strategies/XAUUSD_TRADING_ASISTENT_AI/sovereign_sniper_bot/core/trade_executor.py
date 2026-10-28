from core.groq_gatekeeper import GroqGatekeeper
from core.risk_manager import RiskManager

class TradeExecutor:
    def __init__(self, mt5_connector):
        self.mt5 = mt5_connector
        self.gatekeeper = GroqGatekeeper()
        self.risk_mgr = RiskManager()

    def process_market_cycle(self, symbol: str, raw_market_data: dict):
        # 1. Gather real-time risk metrics from account first so telemetry always has balance
        risk_metrics = self.risk_mgr.get_account_state()

        # 0. Check for open positions to prevent rapid-fire execution
        positions = self.mt5.get_positions(symbol)
        if positions is not None and len(positions) > 0:
            self.risk_mgr.log_system_state("IN_TRADE", "HOLD", f"Already have {len(positions)} open positions. Waiting for resolution.", raw_market_data, risk_metrics)
            return {"action": "HOLD", "target_phase": "IN_TRADE", "reason": "Trade already active"}
        
        # 2. Get AI decision
        verdict = self.gatekeeper.evaluate_setup(raw_market_data, risk_metrics)
        
        # Update current phase of the strategy if needed
        action = verdict.get("action", "HOLD")
        
        # 3. Handle decision tree
        if action in ["EXECUTE_LONG", "EXECUTE_SHORT"]:
            # Dynamic SL/TP with strict limits to protect account balance
            sl_points = 1.5 * raw_market_data.get("atr_value", 1.0)
            
            # HARD CAP: Maximum SL is 15.0 points ($15 risk on 0.01 micro lot)
            if sl_points > 15.0:
                sl_points = 15.0
                
            tp_points = 1.5 * raw_market_data.get("atr_value", 1.0)
            
            base_risk = self.risk_mgr.calculate_safe_lot(
                symbol,
                risk_percent=1.0 * verdict.get("risk_multiplier", 1.0),
                # sl_points is an ATR-derived PRICE distance, not pips.
                sl_price_distance=sl_points
            )

            # A size of 0.0 means the risk manager could not size this trade
            # safely (terminal unreachable, stop too tight for the broker
            # minimum, equity too small). Submitting anyway would either send a
            # 0-lot order or fall back to a default that ignores the risk
            # budget, so stand down instead.
            if base_risk <= 0:
                reason = (
                    f"Risk manager declined to size {symbol}: a {sl_points:.2f}-point stop "
                    f"at {1.0 * verdict.get('risk_multiplier', 1.0):.2f}% risk is not tradable "
                    f"on this account. No order sent."
                )
                self.risk_mgr.log_system_state(
                    "RISK_BLOCKED", "HOLD", reason, raw_market_data, risk_metrics
                )
                return {"action": "HOLD", "target_phase": "SCANNING", "reason": reason}

            if action == "EXECUTE_LONG":
                self.mt5.submit_order(
                    symbol=symbol,
                    order_type="BUY",
                    lots=base_risk,
                    sl=raw_market_data["last_price"] - sl_points,
                    tp=raw_market_data["last_price"] + tp_points
                )
            elif action == "EXECUTE_SHORT":
                self.mt5.submit_order(
                    symbol=symbol,
                    order_type="SELL",
                    lots=base_risk,
                    sl=raw_market_data["last_price"] + sl_points,
                    tp=raw_market_data["last_price"] - tp_points
                )
            
            self.risk_mgr.log_system_state(verdict.get("target_phase", "EXECUTION"), action, verdict.get("reason", "Trade triggered by Gatekeeper"), raw_market_data, risk_metrics)
        else:
            # Log the hold/reset reasoning directly to the system logs
            self.risk_mgr.log_system_state(verdict.get("target_phase", "SCANNING"), action, verdict.get("reason", "Holding position based on gatekeeper logic"), raw_market_data, risk_metrics)
            
        return verdict
