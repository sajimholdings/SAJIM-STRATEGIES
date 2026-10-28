"""
REMOVED — Synthetic Timeframe Matrix Engine (do not restore)
===========================================================

Companion to the tombstoned `universe_matrix.py`. This module produced a
strategy x timeframe performance sweep without running the strategies, and
wrote it to `data/timeframe_matrix.json`, which `/api/leaderboard` used to
serve to the dashboard's "Timeframes" tab as measured results.

The leaderboard API no longer reads that file; it derives every row from the
real result JSONs in `data/results/`. For a genuine timeframe sweep use:

    python -m sajim_quant matrix --timeframes M5,M15,H1 --workers 4

which calls `sajim_quant.analytics.real_matrix.RealMatrixBuilder` and executes
one actual backtest per node.

Importing or running this module raises.
"""

from __future__ import annotations

_MESSAGE = (
    "sajim_quant.analytics.timeframe_matrix produced a timeframe sweep without "
    "running any backtests and published it as measured data. It has been removed. "
    "Use 'python -m sajim_quant matrix --timeframes ...' (real_matrix.RealMatrixBuilder)."
)


def __getattr__(name: str):
    """Any attribute access fails loudly, including the old entry points."""
    raise RuntimeError(f"{_MESSAGE} (attempted to access '{name}')")


if __name__ == "__main__":
    raise SystemExit(_MESSAGE)
