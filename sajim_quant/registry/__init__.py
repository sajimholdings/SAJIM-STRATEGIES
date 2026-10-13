"""Results registry: persistent record of every backtest run plus dashboard export."""

from sajim_quant.registry.export import (
    build_correlation,
    build_leaderboard,
    build_registry_export,
    build_universe,
    export_dashboard,
)
from sajim_quant.registry.store import ResultsRegistry, RunRecord, default_results_dir, slugify
from sajim_quant.registry.tiers import classify_tier, tier_rank

__all__ = [
    "ResultsRegistry", "RunRecord", "default_results_dir", "slugify",
    "classify_tier", "tier_rank", "record_run",
    "export_dashboard", "build_leaderboard", "build_registry_export", "build_correlation", "build_universe",
]


def record_run(result, **kwargs):
    """Convenience wrapper: `record_run(result, strategy_id=..., folder=..., description=...)`."""
    return ResultsRegistry().record_run(result, **kwargs)
