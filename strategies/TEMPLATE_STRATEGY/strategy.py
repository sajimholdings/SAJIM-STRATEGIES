"""
SAJIM Strategy Template - Backtrader / Python Reference
Drop your custom trading logic here.
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
            ("atr_mult", 2.0),
        )

        def __init__(self):
            self.fast_ma = bt.indicators.EMA(self.data.close, period=self.params.fast_period)
            self.slow_ma = bt.indicators.EMA(self.data.close, period=self.params.slow_period)
            self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
            self.atr = bt.indicators.ATR(self.data, period=14)

        def next(self):
            if not self.position:
                if self.crossover > 0:
                    sl = self.data.close[0] - (self.atr[0] * self.params.atr_mult)
                    tp = self.data.close[0] + (self.atr[0] * self.params.atr_mult * 2.0)
                    self.buy()
            else:
                if self.crossover < 0:
                    self.close()
