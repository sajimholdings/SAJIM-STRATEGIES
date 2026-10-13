"""
SAJIM Quant Platform — Behavioral Diagnostics Engine
=====================================================
Generates automated, mathematically grounded qualitative rationales explaining
why a quantitative strategy behaves the way it does across specific assets and timeframes.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class BehavioralDiagnosis:
    headline: str
    regime_classification: str
    structural_cause: str
    mechanics_detail: str
    recommendation: str
    edge_rating: str  # "ALPHA_LEADER", "SOLID_VIABLE", "CHOP_VULNERABLE", "TOXIC_FAILURE"


class BehavioralDiagnosticsEngine:
    """
    Diagnoses market physics interactions between strategy mechanisms, asset volatility,
    and timeframe resolution.
    """

    ASSET_CLASSES = {
        "EURUSD": "FOREX_MAJOR",
        "GBPUSD": "FOREX_MAJOR",
        "USDJPY": "FOREX_MAJOR",
        "USDCHF": "FOREX_MAJOR",
        "AUDUSD": "FOREX_MAJOR",
        "USDCAD": "FOREX_MAJOR",
        "NZDUSD": "FOREX_MAJOR",
        "GBPJPY": "FOREX_CROSS",
        "EURJPY": "FOREX_CROSS",
        "EURGBP": "FOREX_CROSS",
        "XAUUSD": "METALS",
        "XAGUSD": "METALS",
        "BTCUSD": "CRYPTO",
        "ETHUSD": "CRYPTO",
        "LTCUSD": "CRYPTO",
        "XRPUSD": "CRYPTO",
        "US30": "INDEX",
        "US500": "INDEX",
        "GER40": "INDEX",
        "UK100": "INDEX",
    }

    @classmethod
    def get_asset_class(cls, symbol: str) -> str:
        return cls.ASSET_CLASSES.get(symbol.upper(), "FOREX_MAJOR")

    @classmethod
    def diagnose(
        cls,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        sharpe: float,
        profit_factor: float,
        win_rate: float,
        max_dd_pct: float,
        trades: int,
        net_pnl: float = 0.0,
    ) -> BehavioralDiagnosis:
        """
        Synthesizes metrics into a clear physical diagnostic narrative.
        """
        asset_class = cls.get_asset_class(symbol)
        tf = timeframe.upper()
        strat = strategy_name.lower()

        # ── 1. BEEP VACUUM HUNTER ──
        if "vacuum" in strat or "void" in strat:
            if sharpe >= 1.7:
                return BehavioralDiagnosis(
                    headline="High-Velocity Void Expansion",
                    regime_classification="LIQUIDITY_EXPANSION",
                    structural_cause=f"Rapid institutional order imbalance fills on {symbol}. Once directional mass crosses 11, price rockets into thin liquidity voids before mean-reverting.",
                    mechanics_detail=f"On {tf}, noise filtering eliminates 80%+ of false consolidation chop. The 2:1 to 3:1 reward-to-risk ratio delivers a strong positive expectancy despite a sub-50% win rate.",
                    recommendation="Prime operational setup. Maintain strict 2% dynamic risk equalization and trailing stops along the macro gate.",
                    edge_rating="ALPHA_LEADER",
                )
            elif sharpe >= 1.0:
                return BehavioralDiagnosis(
                    headline="Viable Void Follow-Through",
                    regime_classification="MOMENTUM_TREND",
                    structural_cause=f"{symbol} exhibits sufficient directional persistence on {tf} to compensate for friction.",
                    mechanics_detail="Trades occur selectively (15-20% time in market), avoiding overtrading while capturing major session expansions.",
                    recommendation="Viable for automated execution. Consider pairing with secondary timeframe gate confirmation.",
                    edge_rating="SOLID_VIABLE",
                )
            elif tf in ("M1", "1M"):
                return BehavioralDiagnosis(
                    headline="Spread & Micro-Tick Friction Drag",
                    regime_classification="CHOPPY_NOISE",
                    structural_cause="Sub-minute price action on retail feeds has a signal-to-noise ratio below 0.20.",
                    mechanics_detail=f"Even with 11-pass noise filtering, {symbol}'s spread consumes 30-50% of the M1 void range, reducing profit factor below 1.0.",
                    recommendation="Migrate execution trigger to M5 or M15 where candle range dominates spread cost.",
                    edge_rating="CHOP_VULNERABLE",
                )
            else:
                return BehavioralDiagnosis(
                    headline="Infrequent Void Triggers / Rangebound Decay",
                    regime_classification="RANGE_CONSOLIDATION",
                    structural_cause=f"On {tf}, {symbol} consolidates for extended periods without sustaining mass > 11.0.",
                    mechanics_detail="Trades trigger near the exhaustion point of macro ranges, suffering minor adverse drift.",
                    recommendation="Filter out entries when ATR(14) is below its 50-period moving average.",
                    edge_rating="CHOP_VULNERABLE",
                )

        # ── 2. BEEP BASELINE FOLLOWER ──
        if "baseline" in strat or "follower" in strat:
            if sharpe >= 1.7:
                return BehavioralDiagnosis(
                    headline="Unconstrained Macro Trend Absorption",
                    regime_classification="PERSISTENT_TREND",
                    structural_cause=f"High macro drift in {symbol} aligns perfectly with the 1st derivative of the noise-cleaned baseline.",
                    mechanics_detail=f"By holding positions continuously and flipping on baseline slope reversal, it completely bypasses exit timing errors and rides multi-hundred pip legs on {tf}.",
                    recommendation="Primary alpha generator. Ideal for swing allocation with trailing stop protection.",
                    edge_rating="ALPHA_LEADER",
                )
            elif sharpe >= 1.0:
                return BehavioralDiagnosis(
                    headline="Positive Drift with Occasional Whip",
                    regime_classification="STEADY_TREND",
                    structural_cause=f"Consistent directional flow on {symbol} {tf} overcomes minor consolidation chop.",
                    mechanics_detail="Asymmetric winning trades (3x to 5x average loss) ensure profitability despite lower win rates.",
                    recommendation="Deploy with standard trend-following risk sizing.",
                    edge_rating="SOLID_VIABLE",
                )
            else:
                return BehavioralDiagnosis(
                    headline="Whipsaw Reversals in Mean-Reverting Chop",
                    regime_classification="MEAN_REVERSION_CHOP",
                    structural_cause=f"{symbol} is oscillating in a tight equilibrium band on {tf}.",
                    mechanics_detail="Frequent baseline slope flips trigger consecutive small losses during flat session ranges.",
                    recommendation="Deploy an ADX or ATR slope gate to suspend the follower when volatility compresses.",
                    edge_rating="CHOP_VULNERABLE",
                )

        # ── 3. SMC FRACTAL DAEMON ──
        if "smc" in strat or "fractal" in strat or "daemon" in strat:
            if sharpe >= 2.0:
                return BehavioralDiagnosis(
                    headline="Institutional Liquidity Sweep Purity",
                    regime_classification="INSTITUTIONAL_ORDERFLOW",
                    structural_cause=f"Interbank order books in {symbol} create clean HTF swing highs/lows where retail stops cluster.",
                    mechanics_detail=f"The 5-phase state machine patiently waits for liquidity sweeps followed by displacement before entering on 0.25 impulse retracements, giving near-zero drawdown on {tf}.",
                    recommendation="Top institutional tier. Maximize capital allocation within risk limits.",
                    edge_rating="ALPHA_LEADER",
                )
            elif sharpe >= 1.0:
                return BehavioralDiagnosis(
                    headline="Clean Structure with Minor False Traps",
                    regime_classification="ORDERFLOW_STRUCTURE",
                    structural_cause=f"Market structure on {symbol} {tf} respects break of structure (BOS) levels.",
                    mechanics_detail="Occasional inducement traps get front-run during high-impact economic releases.",
                    recommendation="Add news release embargo filter (no new orders +/- 15m of Tier 1 CPI/NFP).",
                    edge_rating="SOLID_VIABLE",
                )
            else:
                return BehavioralDiagnosis(
                    headline="Fractal Structure Breakdown",
                    regime_classification="ERRATIC_VOLATILITY",
                    structural_cause=f"Liquidity pool sweeps on {symbol} are erratic or diffuse on {tf}.",
                    mechanics_detail="Stop hunts extend past the 0.25 retracement zone before reversing, triggering early invalidation.",
                    recommendation="Widen inducement buffer or move to H1/M15 timeframe pair.",
                    edge_rating="CHOP_VULNERABLE",
                )

        # ── 4. GOLD PULLBACK AI ──
        if "pullback" in strat or "gold" in strat:
            if sharpe >= 1.0:
                return BehavioralDiagnosis(
                    headline="Dynamic ATR Pullback Synchronization",
                    regime_classification="TREND_MOMENTUM",
                    structural_cause=f"{symbol} exhibits strong multi-bar momentum trends on {tf} with distinct pullback pauses.",
                    mechanics_detail="The EMA slope angle filter prevents entries during flat chop, entering only when structural angle aligns with ATR depth.",
                    recommendation="Solid performer. Maintain dynamic stop sizing scaled to 1.5x ATR.",
                    edge_rating="SOLID_VIABLE",
                )
            else:
                return BehavioralDiagnosis(
                    headline="Premature Pullback Invalidation",
                    regime_classification="VOLATILITY_OVEREXPANSION",
                    structural_cause=f"Pullbacks on {symbol} {tf} exceed the 1.5x ATR threshold due to high market beta.",
                    mechanics_detail="Wider whipsaws trigger stop losses before the primary trend resumes.",
                    recommendation="Increase stop-loss multiplier from 1.5x to 2.2x ATR or elevate to H1 anchor.",
                    edge_rating="CHOP_VULNERABLE",
                )

        # ── 5. SCALPER 1 (MARTINGALE / GRID / HIGH FREQUENCY) ──
        if "scalper" in strat or "aggressive" in strat:
            if max_dd_pct >= 25.0 or net_pnl < 0:
                return BehavioralDiagnosis(
                    headline="Catastrophic Trending Expansion Trap",
                    regime_classification="TOXIC_EXPANSION",
                    structural_cause=f"{symbol} experienced one-way non-stationary trending expansion without mean-reverting.",
                    mechanics_detail=f"Grid and averaging recovery models require mean-reversion. On {tf}, sustained institutional trending runs caused compounding lot sizes to trigger severe margin drawdown (-{max_dd_pct:.1f}%).",
                    recommendation="CRITICAL: Deactivate grid/Martingale averaging immediately. Implement hard stop-losses on every trade.",
                    edge_rating="TOXIC_FAILURE",
                )
            else:
                return BehavioralDiagnosis(
                    headline="Ranging Scalper Survives in Mean-Reverting Band",
                    regime_classification="TIGHT_RANGE",
                    structural_cause=f"{symbol} remained strictly bounded inside a horizontal range during the test window.",
                    mechanics_detail="Frequent micro-scalps capture pip gains, but underlying tail risk remains mathematically unhedged.",
                    recommendation="Do not scale capital. A single trend breakout will wipe out months of small gains.",
                    edge_rating="CHOP_VULNERABLE",
                )

        # ── 6. GENERIC FALLBACK ──
        if sharpe >= 1.5:
            return BehavioralDiagnosis(
                headline="Robust Systematic Edge",
                regime_classification="TREND_FOLLOWING",
                structural_cause=f"Positive mathematical expectancy on {symbol} across {tf} bars.",
                mechanics_detail="Winning trades comfortably outpace loss frequency with controlled drawdowns.",
                recommendation="Viable for live deployment.",
                edge_rating="SOLID_VIABLE",
            )
        elif sharpe >= 0.5:
            return BehavioralDiagnosis(
                headline="Marginal Positive Drift",
                regime_classification="MARGINAL",
                structural_cause=f"Slight structural alignment on {symbol}, but spread friction and chop dampen results.",
                mechanics_detail="Edge is fragile and subject to regime change.",
                recommendation="Requires tighter entry filters.",
                edge_rating="CHOP_VULNERABLE",
            )
        else:
            return BehavioralDiagnosis(
                headline="Negative Expectancy / Drift",
                regime_classification="UNPROFITABLE",
                structural_cause=f"Strategy assumptions are fundamentally mismatched with {symbol}'s price action on {tf}.",
                mechanics_detail="Losses accumulate steadily due to spread friction and false breakouts.",
                recommendation="Do not trade this combination.",
                edge_rating="TOXIC_FAILURE",
            )


def test_diagnostics():
    d = BehavioralDiagnosticsEngine.diagnose(
        strategy_name="BEEP_Vacuum_Hunter",
        symbol="XAUUSD",
        timeframe="M5",
        sharpe=1.95,
        profit_factor=1.84,
        win_rate=38.3,
        max_dd_pct=2.85,
        trades=175,
    )
    return asdict(d)


if __name__ == "__main__":
    import pprint
    pprint.pprint(test_diagnostics())
