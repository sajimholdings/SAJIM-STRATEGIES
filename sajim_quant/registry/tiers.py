"""
Single source of truth for strategy tier classification.

The leaderboard, the registry and the universe grid all call `classify_tier` so a run can never
be "TIER 1" in one view and "TIER 2" in another. Thresholds follow the ones that were already
used (informally) in universe_matrix.py and the tear-sheet generator, plus one hard rule that
was missing: a run with fewer than MIN_TRADES_FOR_TIER1 trades cannot be Tier 1, no matter how
good its ratios look, because the sample is too small to trust.
"""

from __future__ import annotations

from typing import Any, Mapping

MIN_TRADES_FOR_TIER1 = 30
MIN_TRADES_FOR_TIER2 = 10

TIER_1_ELITE = "TIER 1: ELITE"
TIER_2_VIABLE = "TIER 2: VIABLE"
TIER_2_MARGINAL = "TIER 2: MARGINAL"
TIER_3_UNPROFITABLE = "TIER 3: UNPROFITABLE"
TIER_3_TOXIC_DD = "TIER 3: TOXIC DD"
TIER_3_INSUFFICIENT = "TIER 3: INSUFFICIENT DATA"
TIER_UNTESTED = "UNTESTED"


def classify_tier(m: Mapping[str, Any] | Any) -> str:
    """
    Accepts a PerformanceMetrics instance or any mapping with the keys
    total_trades, net_profit, sharpe_ratio, profit_factor, max_drawdown_pct.
    """
    g = (lambda k, d=0.0: m.get(k, d)) if isinstance(m, Mapping) else (lambda k, d=0.0: getattr(m, k, d))
    trades = int(g("total_trades", g("trades", 0)) or 0)
    net = float(g("net_profit", g("net_pnl", 0.0)) or 0.0)
    sharpe = float(g("sharpe_ratio", 0.0) or 0.0)
    pf = float(g("profit_factor", 0.0) or 0.0)
    dd = float(g("max_drawdown_pct", 0.0) or 0.0)

    if trades == 0:
        return TIER_UNTESTED
    if dd >= 25.0:
        return TIER_3_TOXIC_DD
    if trades < MIN_TRADES_FOR_TIER2:
        return TIER_3_INSUFFICIENT
    if net <= 0 or pf < 1.0 or sharpe < 0:
        return TIER_3_UNPROFITABLE if sharpe < -0.5 or pf < 0.9 else TIER_2_MARGINAL
    if sharpe >= 1.8 and pf >= 1.5 and dd <= 10.0 and trades >= MIN_TRADES_FOR_TIER1:
        return TIER_1_ELITE
    if sharpe >= 1.0 and pf >= 1.1:
        return TIER_2_VIABLE
    return TIER_2_MARGINAL


def tier_rank(tier: str) -> int:
    """Lower is better; used for sorting."""
    order = [TIER_1_ELITE, TIER_2_VIABLE, TIER_2_MARGINAL, TIER_3_INSUFFICIENT,
             TIER_3_UNPROFITABLE, TIER_3_TOXIC_DD, TIER_UNTESTED]
    return order.index(tier) if tier in order else len(order)
