"""
sajim_quant.noise — volatility ("noise") forecasting for the SAJIM strategy tester.

Answers "how violent is the market likely to be" — never "which way".

Pipeline (all causal, zero lookahead):
  M5 bars (Dukascopy parquet)
    -> daily realized variance (sum of squared M5 log returns)
    -> walk-forward HAR-RV forecast of the next trading day's variance
    -> intraday hour-of-day profile (how that variance is spread across the day)
    -> expected noise for any bar / holding window
    -> trade diagnosis: tag every trade with the noise state at entry
"""

HONESTY_NOTE = ("Volatility forecasts measure the expected size of moves (noise), "
                "not their direction.")
