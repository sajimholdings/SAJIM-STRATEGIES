import pandas as pd

class StrategyBase:
    def initialize(self, config: dict):
        pass

    def analyze(self, symbol: str, df: pd.DataFrame) -> dict:
        raise NotImplementedError("analyze() must be implemented by subclass")
