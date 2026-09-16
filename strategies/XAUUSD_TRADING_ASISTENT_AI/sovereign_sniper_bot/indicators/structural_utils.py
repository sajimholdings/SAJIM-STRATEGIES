import pandas as pd
import numpy as np

def calculate_slope_angle(series: pd.Series, period: int = 5) -> float:
    if len(series) < period:
        return 0.0
    y1 = series.iloc[-period]
    y2 = series.iloc[-1]
    # Simple unscaled angle approx (in real terms needs normalization, but for placeholder):
    dy = y2 - y1
    dx = period
    angle_rad = np.arctan2(dy, dx)
    return np.degrees(angle_rad)
