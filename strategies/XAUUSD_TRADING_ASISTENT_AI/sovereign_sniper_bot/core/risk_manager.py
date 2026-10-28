"""
Sovereign Sniper — Risk Manager

Account state, position sizing and telemetry for the live XAUUSD bot.

Both of the numbers this module exists to produce used to be stubs:

  * `calculate_safe_lot()` returned a constant 0.01 regardless of risk percent
    or stop distance, so the "risk-based sizing" in `trade_executor` sized every
    trade identically whatever the ATR said;
  * `get_account_state()` reported `consecutive_losses: 0` as a hardcoded
    placeholder — and `groq_gatekeeper`'s prompt instructs an immediate HALT
    when `consecutive_losses >= 3`, so that circuit breaker could never fire.

Both are now computed from the live MT5 account. Sizing returns 0.0 when it
cannot be computed safely; callers must treat that as "do not trade" rather than
substituting a default.
"""

import datetime
import json
import logging
import os

import MetaTrader5 as mt5

logger = logging.getLogger("RiskManager")

#: Deals older than this are not considered when counting a losing streak.
STREAK_LOOKBACK_DAYS = 14


class RiskManager:
    def __init__(self):
        self.log_file = "ui/telemetry_log.json"
        if not os.path.exists("ui"):
            os.makedirs("ui")
        if not os.path.exists(self.log_file):
            with open(self.log_file, "w") as f:
                json.dump([], f)

    # ── account state ───────────────────────────────────────────────────────
    def get_account_state(self) -> dict:
        account_info = mt5.account_info()
        if account_info is None:
            logger.warning("mt5.account_info() returned None — reporting a halted account state.")
            return {
                "equity": 0.0,
                "balance": 0.0,
                "drawdown_percent": 0.0,
                "open_trades": 0,
                # Unknown, not zero: a gatekeeper that halts on >= 3 must not be
                # told "0 losses" when the account is unreachable.
                "consecutive_losses": None,
                "account_available": False,
            }

        balance = account_info.balance
        equity = account_info.equity
        drawdown_percent = 0.0
        if balance > 0 and equity < balance:
            drawdown_percent = ((balance - equity) / balance) * 100.0

        return {
            "equity": equity,
            "balance": balance,
            "drawdown_percent": drawdown_percent,
            "open_trades": mt5.positions_total() or 0,
            "consecutive_losses": self.count_consecutive_losses(),
            "account_available": True,
        }

    def count_consecutive_losses(self, lookback_days: int = STREAK_LOOKBACK_DAYS) -> int:
        """
        Number of losing trades at the tail of the closed-deal history.

        Walks closing deals newest-first and counts until the first
        non-negative result. Deals with zero profit (a breakeven exit, a
        balance operation) end the streak rather than extending it.
        """
        now = datetime.datetime.now()
        try:
            deals = mt5.history_deals_get(now - datetime.timedelta(days=lookback_days), now)
        except Exception as e:
            logger.warning("Could not read deal history for the loss streak: %s", e)
            return 0

        if not deals:
            return 0

        closing = [
            d for d in deals
            if getattr(d, "entry", None) in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)
        ]
        closing.sort(key=lambda d: d.time, reverse=True)

        streak = 0
        for d in closing:
            net = float(d.profit) + float(getattr(d, "commission", 0.0)) + float(getattr(d, "swap", 0.0))
            if net < 0:
                streak += 1
            else:
                break
        return streak

    # ── position sizing ─────────────────────────────────────────────────────
    def calculate_safe_lot(
        self,
        symbol: str,
        risk_percent: float,
        sl_price_distance: float,
    ) -> float:
        """
        Lot size that loses `risk_percent` of equity if the stop is hit.

        Args:
            symbol: instrument to size for.
            risk_percent: percent of equity to risk (1.0 means 1%).
            sl_price_distance: stop distance in PRICE units, not pips. The
                caller passes an ATR-derived figure (`1.5 * atr`), which is a
                price distance; the old parameter name `sl_pips` misdescribed it.

        Returns:
            A broker-normalized lot size, or 0.0 if it cannot be computed —
            unreachable terminal, missing symbol, non-positive stop, or a result
            below the broker's minimum volume. 0.0 means DO NOT TRADE.
        """
        if risk_percent <= 0 or sl_price_distance <= 0:
            logger.warning(
                "Refusing to size: risk_percent=%s sl_price_distance=%s",
                risk_percent, sl_price_distance,
            )
            return 0.0

        account_info = mt5.account_info()
        info = mt5.symbol_info(symbol)
        if account_info is None or info is None:
            logger.warning("Refusing to size %s: account or symbol info unavailable.", symbol)
            return 0.0

        equity = float(account_info.equity)
        if equity <= 0:
            logger.warning("Refusing to size %s: equity is %.2f.", symbol, equity)
            return 0.0

        tick_size = float(getattr(info, "trade_tick_size", 0.0) or getattr(info, "point", 0.0) or 0.0)
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
        if tick_size <= 0 or tick_value <= 0:
            logger.warning(
                "Refusing to size %s: tick_size=%s tick_value=%s.", symbol, tick_size, tick_value
            )
            return 0.0

        # Loss of a 1.0-lot position if the stop is hit.
        loss_per_lot = (sl_price_distance / tick_size) * tick_value
        if loss_per_lot <= 0:
            logger.warning("Refusing to size %s: computed loss per lot is %.4f.", symbol, loss_per_lot)
            return 0.0

        risk_amount = equity * (risk_percent / 100.0)
        raw_lots = risk_amount / loss_per_lot

        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        vol_min = float(getattr(info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(info, "volume_max", 100.0) or 100.0)

        # Round DOWN to the step so rounding never increases risk above the budget.
        lots = (int(raw_lots / step) * step) if step > 0 else raw_lots
        lots = round(lots, 2)

        if lots < vol_min:
            logger.warning(
                "Refusing to size %s: risk budget $%.2f over a %.5f stop allows %.4f lots, "
                "below the broker minimum of %.2f. Widen the stop or fund the account.",
                symbol, risk_amount, sl_price_distance, raw_lots, vol_min,
            )
            return 0.0

        lots = min(lots, vol_max)
        logger.info(
            "[SIZING] %s equity=$%.2f risk=%.2f%% ($%.2f) stop=%.5f "
            "loss/lot=$%.2f -> %.2f lots",
            symbol, equity, risk_percent, risk_amount, sl_price_distance, loss_per_lot, lots,
        )
        return lots

    # ── telemetry ───────────────────────────────────────────────────────────
    def log_system_state(self, phase: str, action: str, reason: str, market_data: dict = None, risk_metrics: dict = None):
        entry = {
            "Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Phase": phase,
            "Action": action,
            "Reason": reason,
            "MarketData": market_data or {},
            "RiskMetrics": risk_metrics or {},
        }
        print(f"[{entry['Timestamp']}] {action}: {reason}")

        try:
            with open(self.log_file, "r") as f:
                logs = json.load(f)
        except (OSError, ValueError):
            # Missing or corrupt telemetry must not take the bot down.
            logs = []

        logs.insert(0, entry)
        logs = logs[:100]  # Keep last 100

        with open(self.log_file, "w") as f:
            json.dump(logs, f, indent=2)
