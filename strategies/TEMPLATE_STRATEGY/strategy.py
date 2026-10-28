"""
SAJIM Strategy Template — Backtrader / Python Reference

Drop your custom trading logic here. This file is the pattern every new Python
strategy is copied from, so it deliberately demonstrates the things the
benchmarking platform expects:

  * a stop-loss and a take-profit attached to EVERY entry, via a bracket order
    (the previous version of this template computed `sl` and `tp` from ATR and
    then called a bare `self.buy()`, discarding both — it taught entering with
    no stop);
  * ATR-scaled levels rather than fixed pip distances, so the strategy ports
    across instruments;
  * risk-based position sizing, so results are comparable to the other engines;
  * params exposed through `params`, so the runner can sweep them.

Register it in `sajim_quant/core/strategy_catalog.json` with
`"run_adapter": "backtrader"`, then:

    python -m sajim_quant test --strategy strategies/YOUR_STRATEGY/strategy.py \
        --symbol XAUUSD --tf M15
"""

try:
    import backtrader as bt
except ImportError:
    bt = None


if bt:

    class TemplateStrategy(bt.Strategy):
        params = (
            ("fast_period", 14),
            ("slow_period", 50),
            ("atr_period", 14),
            # Stop at 2x ATR, target at 2x the stop distance (a 2:1 reward:risk).
            ("atr_mult", 2.0),
            ("reward_risk", 2.0),
            # Fraction of equity risked if the stop is hit.
            ("risk_pct", 0.01),
        )

        def __init__(self):
            self.fast_ma = bt.indicators.EMA(self.data.close, period=self.p.fast_period)
            self.slow_ma = bt.indicators.EMA(self.data.close, period=self.p.slow_period)
            self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
            self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
            #: Set while a bracket is live so we don't stack entries.
            self.entry_order = None

        def _size_for(self, stop_distance: float) -> float:
            """
            Fixed-fractional size: lose `risk_pct` of equity if the stop is hit.

            Returns 0.0 for an unusable stop distance so the caller skips the
            trade rather than dividing by zero or sizing off a stop inside the
            spread.
            """
            if stop_distance <= 0:
                return 0.0
            risk_amount = self.broker.getvalue() * self.p.risk_pct
            return risk_amount / stop_distance

        def next(self):
            # One bracket at a time: an open position or a pending entry means wait.
            if self.position or self.entry_order:
                return

            atr = float(self.atr[0])
            if atr <= 0:
                return

            stop_distance = atr * self.p.atr_mult
            size = self._size_for(stop_distance)
            if size <= 0:
                return

            price = float(self.data.close[0])

            if self.crossover > 0:
                # buy_bracket submits the entry together with its stop and limit,
                # so the protective orders cannot be forgotten or lost.
                self.entry_order = self.buy_bracket(
                    size=size,
                    price=price,
                    stopprice=price - stop_distance,
                    limitprice=price + stop_distance * self.p.reward_risk,
                )
            elif self.crossover < 0:
                self.entry_order = self.sell_bracket(
                    size=size,
                    price=price,
                    stopprice=price + stop_distance,
                    limitprice=price - stop_distance * self.p.reward_risk,
                )

        def notify_order(self, order):
            # Clear the guard once the bracket is no longer pending, so the next
            # signal can be taken.
            if order.status in (order.Completed, order.Canceled, order.Rejected, order.Margin):
                self.entry_order = None
