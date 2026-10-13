"""
SAJIM Quant - Strategy Universe Matrix (symbol x timeframe grid)
================================================================
Builds `strategy_universe_matrix.json` for the dashboard from the results registry.

History: the previous version of this module did not run any backtest. It took six hardcoded
Sharpe values, scaled them with fixed per-timeframe and per-asset multipliers, synthesized a
trade list from the scaled numbers and ran Monte Carlo on that. Every number it produced was
fabricated. That generator was deleted; the grid is now populated only by runs recorded through
`sajim_quant.registry.record_run`, and cells that were never tested are `null`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sajim_quant.registry.export import (  # noqa: F401  (re-exported for backwards compatibility)
    UNIVERSE_SYMBOLS as ALL_SYMBOLS,
    UNIVERSE_TIMEFRAMES as ALL_TIMEFRAMES,
    build_universe,
    default_dashboard_public_dir,
)
from sajim_quant.registry.store import ResultsRegistry


def build_strategy_universe_matrix(results_dir: Optional[Path] = None, public_dir: Optional[Path] = None) -> Dict[str, Any]:
    import json

    reg = ResultsRegistry(results_dir)
    matrix = build_universe(reg)
    out = (Path(public_dir) if public_dir else default_dashboard_public_dir()) / "strategy_universe_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(matrix, f, indent=2)
    tested = sum(s["total_combinations_tested"] for s in matrix["strategies"])
    print(f"[universe] {len(matrix['strategies'])} strategies, {tested} tested cells -> {out}")
    return matrix


if __name__ == "__main__":
    build_strategy_universe_matrix()
