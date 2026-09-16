import pandas as pd
from core.strategy_base import StrategyBase
from indicators.common import calculate_atr, calculate_ema
from indicators.structural_utils import calculate_slope_angle

class GoldPullbackAI(StrategyBase):
    def initialize(self, config: dict):
        self.name = "Gold Pullback AI"
        self.timeframe = config.get("timeframe", "H1")
        self.current_phase = "SCANNING"
        self.pullback_counter = 0

    def analyze(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Compiles structural market facts without making an internal trade decision.
        """
        atr = calculate_atr(df, period=14)
        ema_fast = calculate_ema(df, period=1)
        ema_slow_basket = [calculate_ema(df, period=p) for p in [14, 18, 24]]
        slope = calculate_slope_angle(ema_fast)
        
        # Simple local tracking of counter-trend candles
        last_close = df.iloc[-1]['close']
        prev_close = df.iloc[-2]['close']
        
        # basic pullback count logic
        if self.current_phase == "ARMED":
            if (self.market_structure == "BULLISH" and last_close < prev_close) or (self.market_structure == "BEARISH" and last_close > prev_close):
                self.pullback_counter += 1
            else:
                self.pullback_counter = 0
                
        self.market_structure = "BULLISH" if last_close > ema_slow_basket[0].iloc[-1] else "BEARISH"
        
        return {
            "symbol": symbol,
            "current_phase": self.current_phase,
            "ema_slope_degrees": slope,
            "pullback_count": self.pullback_counter,
            "last_price": last_close,
            "atr_value": atr,
            "market_structure": self.market_structure
        }
