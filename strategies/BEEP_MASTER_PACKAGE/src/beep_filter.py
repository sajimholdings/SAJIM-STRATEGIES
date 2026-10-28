"""
BEEP Filter — The Core Algorithm
=================================
Multi-layer anomaly detection for forex price data.

The filter works in layers, each catching a different type of noise:

Layer 1: Statistical Anomaly Detection
    - Identifies candles with abnormal size (body or wick) relative to recent history
    - Uses rolling z-scores to flag outliers

Layer 2: Volume Anomaly Detection
    - Flags moves that happen on abnormally low or high volume
    - Low volume moves = unreliable (drift noise)
    - Spike volume moves = potential stop hunts or news artifacts

Layer 3: Structural Anomaly Detection
    - Identifies fakeouts: moves that break a level then immediately reverse
    - Detects wick rejections: candles that test a level but close far from it
    - These are the classic "traps" that catch retail traders

Layer 4: Temporal Pattern Detection
    - Flags noise during known low-liquidity periods
    - Asian session drift, pre-news consolidation, weekend gaps

The output is the original data with anomaly scores. Higher score = more noise.
"""

import numpy as np
import pandas as pd
from scipy import stats


class BeepFilter:
    """
    The BEEP anomaly detection filter.

    Takes raw OHLCV data and scores each candle for "noise level".
    Higher noise score = less trustworthy signal.
    """

    def __init__(
        self,
        lookback: int = 20,
        z_threshold: float = 2.0,
        volume_z_threshold: float = 2.0,
        fakeout_window: int = 3,
        wick_ratio_threshold: float = 0.7,
        allow_lookahead: bool = False,
    ):
        """
        Args:
            lookback: Rolling window for calculating statistics (default 20 candles)
            z_threshold: Z-score threshold for flagging price anomalies
            volume_z_threshold: Z-score threshold for flagging volume anomalies
            fakeout_window: Number of candles over which a reversal must complete
            wick_ratio_threshold: Wick-to-body ratio above which a candle is suspicious
            allow_lookahead: RESEARCH ONLY. When True, the fakeout layer attributes
                a confirmed reversal back to the impulse candle, which means a
                candle's noise flag depends on candles that had not printed yet.
                Any backtest run with this enabled is contaminated. Default False.
        """
        self.lookback = lookback
        self.z_threshold = z_threshold
        self.volume_z_threshold = volume_z_threshold
        self.fakeout_window = fakeout_window
        self.wick_ratio_threshold = wick_ratio_threshold
        self.allow_lookahead = allow_lookahead

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply the full BEEP filter pipeline to price data.

        Args:
            df: DataFrame with Open, High, Low, Close, Volume columns

        Returns:
            DataFrame with added columns:
            - candle_body: absolute body size
            - candle_range: high - low
            - upper_wick / lower_wick: wick sizes
            - noise_statistical: Layer 1 score (0 or 1)
            - noise_volume: Layer 2 score (0 or 1)
            - noise_structural: Layer 3 score (0 or 1)
            - noise_temporal: Layer 4 score (0 or 1)
            - noise_score: Combined score (0 = clean, 4 = max noise)
            - is_noise: Boolean — True if noise_score >= 2
            - signal_direction: Cleaned trend direction
        """
        result = df.copy()

        # Pre-compute candle metrics
        result = self._compute_candle_metrics(result)

        # Apply each filter layer
        result = self._layer_statistical(result)
        result = self._layer_volume(result)
        result = self._layer_structural(result)
        result = self._layer_temporal(result)

        # Combine scores
        noise_cols = [
            "noise_statistical",
            "noise_volume",
            "noise_structural",
            "noise_temporal",
        ]
        result["noise_score"] = result[noise_cols].sum(axis=1)
        result["is_noise"] = result["noise_score"] >= 2

        # Generate cleaned signal direction
        result = self._compute_signal_direction(result)

        return result

    def _compute_candle_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate basic candle measurements."""
        df["candle_body"] = abs(df["Close"] - df["Open"])
        df["candle_range"] = df["High"] - df["Low"]

        # Wick calculations
        df["upper_wick"] = df["High"] - df[["Open", "Close"]].max(axis=1)
        df["lower_wick"] = df[["Open", "Close"]].min(axis=1) - df["Low"]

        # Wick-to-range ratio (how much of the candle is wick vs body)
        df["wick_ratio"] = np.where(
            df["candle_range"] > 0,
            (df["upper_wick"] + df["lower_wick"]) / df["candle_range"],
            0,
        )

        # Price change (close to close)
        df["price_change"] = df["Close"].pct_change()

        # Direction: 1 = bullish, -1 = bearish, 0 = doji
        df["direction"] = np.sign(df["Close"] - df["Open"])

        return df

    def _layer_statistical(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Layer 1: Statistical Anomaly Detection
        Flags candles whose body size or range is abnormally large relative
        to the recent rolling average.
        """
        # Rolling mean and std of candle body
        rolling_mean = df["candle_body"].rolling(self.lookback).mean()
        rolling_std = df["candle_body"].rolling(self.lookback).std()

        # Z-score of current candle body vs recent history
        df["body_zscore"] = np.where(
            rolling_std > 0,
            (df["candle_body"] - rolling_mean) / rolling_std,
            0,
        )

        # Also check range z-score
        range_mean = df["candle_range"].rolling(self.lookback).mean()
        range_std = df["candle_range"].rolling(self.lookback).std()
        df["range_zscore"] = np.where(
            range_std > 0,
            (df["candle_range"] - range_mean) / range_std,
            0,
        )

        # Flag as noise if either z-score exceeds threshold
        df["noise_statistical"] = (
            (abs(df["body_zscore"]) > self.z_threshold)
            | (abs(df["range_zscore"]) > self.z_threshold)
        ).astype(int)

        return df

    def _layer_volume(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Layer 2: Volume Anomaly Detection
        Flags moves on abnormally low volume (unreliable drift)
        or abnormally high volume (potential stop hunts / news spikes).
        """
        if "Volume" not in df.columns or df["Volume"].sum() == 0:
            # No volume data available — skip this layer
            df["volume_zscore"] = 0
            df["noise_volume"] = 0
            return df

        vol_mean = df["Volume"].rolling(self.lookback).mean()
        vol_std = df["Volume"].rolling(self.lookback).std()

        df["volume_zscore"] = np.where(
            vol_std > 0,
            (df["Volume"] - vol_mean) / vol_std,
            0,
        )

        # Noise if volume is extremely high (spike) or extremely low (drift)
        # combined with a significant price move
        significant_move = abs(df["body_zscore"]) > 1.0

        df["noise_volume"] = (
            (abs(df["volume_zscore"]) > self.volume_z_threshold) & significant_move
        ).astype(int)

        return df

    def _layer_structural(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Layer 3: Structural Anomaly Detection
        Detects fakeouts and wick rejections — the classic retail traps.

        Causality note: a fakeout is only knowable once the reversal has printed,
        so the flag is attached to the candle that COMPLETES the pattern, not to
        the impulse candle that started it. Attributing it backwards to the
        impulse — which earlier versions of this filter did — makes candle i's
        noise flag depend on candles i+1..i+fakeout_window, i.e. on the future.
        Measured on XAUUSD H1 that moved the flag on ~3% of candles and flipped
        the sign of the downstream baseline slope (the traded signal) on roughly
        1 candle in 24. Set allow_lookahead=True to restore the old behaviour for
        research comparisons only.
        """
        # Wick rejection: candles where most of the range is wick (not body)
        # These are "test and reject" candles — price went there but didn't stay
        df["noise_wick_rejection"] = (
            df["wick_ratio"] > self.wick_ratio_threshold
        ).astype(int)

        # Fakeout detection: a strong move reversed over the following N candles
        df["noise_fakeout"] = 0
        directions = df["direction"].values
        body_zscores = df["body_zscore"].values
        flags = np.zeros(len(df), dtype=int)
        window = self.fakeout_window
        min_reversals = window * 0.6

        # Walk impulse candidates; `window` candles later the pattern is settled.
        for impulse in range(self.lookback, len(df) - window):
            if abs(body_zscores[impulse]) <= 1.5 or directions[impulse] == 0:
                continue

            move_dir = directions[impulse]
            reversed_count = 0
            for j in range(1, window + 1):
                if directions[impulse + j] == -move_dir:
                    reversed_count += 1

            if reversed_count >= min_reversals:
                # The reversal is confirmed at impulse+window. Flag the candle
                # where that becomes true, so no flag depends on future data.
                flags[impulse if self.allow_lookahead else impulse + window] = 1

        df["noise_fakeout"] = flags

        # Combine structural signals
        df["noise_structural"] = (
            (df["noise_wick_rejection"] == 1) | (df["noise_fakeout"] == 1)
        ).astype(int)

        return df

    def _layer_temporal(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Layer 4: Temporal Pattern Detection
        Flags noise during known low-liquidity periods.
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            df["noise_temporal"] = 0
            return df

        hour = df.index.hour

        # Low liquidity windows (UTC):
        # - Late Asian / early pre-London: 21:00 - 00:00 UTC
        # - Post-NY close: 20:00 - 22:00 UTC
        # These windows have thin order books = price can drift randomly
        low_liquidity = ((hour >= 21) | (hour <= 0))

        # Only flag as temporal noise if a significant move happens during
        # low liquidity (real moves during low liquidity are rare)
        significant_move = abs(df["body_zscore"]) > 1.0

        df["noise_temporal"] = (low_liquidity & significant_move).astype(int)

        return df

    def _compute_signal_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute cleaned signal direction by ignoring noise candles.
        The signal direction is a smoothed trend based only on clean candles.
        """
        clean_direction = df["direction"].copy()
        clean_direction[df["is_noise"]] = 0  # Neutralize noisy candles

        # Smooth: rolling sum of clean directions
        # Positive = bullish bias, Negative = bearish bias
        df["signal_direction"] = (
            clean_direction.rolling(self.lookback, min_periods=1).sum()
        )

        return df

    def get_summary(self, df: pd.DataFrame) -> dict:
        """
        Get a summary of the filter results.

        Args:
            df: DataFrame that has been processed by apply()

        Returns:
            dict with filter statistics
        """
        total = len(df)
        noise_count = df["is_noise"].sum()

        return {
            "total_candles": total,
            "noise_candles": int(noise_count),
            "clean_candles": int(total - noise_count),
            "noise_percentage": round(noise_count / total * 100, 1),
            "by_layer": {
                "statistical": int(df["noise_statistical"].sum()),
                "volume": int(df["noise_volume"].sum()),
                "structural": int(df["noise_structural"].sum()),
                "temporal": int(df["noise_temporal"].sum()),
            },
            "avg_noise_score": round(df["noise_score"].mean(), 2),
        }


if __name__ == "__main__":
    # Quick test with sample data
    from data_loader import fetch_pair

    print("=" * 60)
    print("BEEP FILTER — TEST RUN")
    print("=" * 60)

    df = fetch_pair("EURUSD", period="3mo", interval="1h")
    beep = BeepFilter()
    result = beep.apply(df)
    summary = beep.get_summary(result)

    print(f"\n📊 Results:")
    print(f"   Total candles:  {summary['total_candles']}")
    print(f"   Noise candles:  {summary['noise_candles']} ({summary['noise_percentage']}%)")
    print(f"   Clean candles:  {summary['clean_candles']}")
    print(f"\n   By layer:")
    for layer, count in summary["by_layer"].items():
        print(f"     {layer:15s}: {count}")
    print(f"\n   Avg noise score: {summary['avg_noise_score']}")
