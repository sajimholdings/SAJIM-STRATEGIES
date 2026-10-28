"""
REMOVED — Synthetic Universe Matrix Builder (do not restore)
===========================================================

This module fabricated performance metrics and wrote them to the same paths the
dashboard reads as real results. It is retained only as a tombstone so the
hazard is documented rather than silently reintroduced; importing or running it
raises.

What it used to do
------------------
* Carried a hardcoded `base_sharpe` / `base_pf` / `base_win_rate` / `base_pnl`
  per strategy, then scaled them by asset-class and timeframe fudge factors to
  manufacture a 20 x 6 grid of 120 "evaluated" nodes per strategy.
* `generate_synthetic_trade_sequence()` reverse-engineered a plausible PnL stream
  from a target win rate and profit factor, with Gaussian dispersion, so the
  fabricated nodes would survive a Monte Carlo pass and look empirical.
* Wrote the result to `data/strategy_universe_matrix.json` and copied it over
  `sim/data/` and `sim/public/` — the exact files `/api/strategy-universe`
  serves. One invocation replaced a real 1,753-second backtest build with
  fiction, distinguishable only by a `build_mode` key the API treated as
  optional.

What to use instead
-------------------
`sajim_quant.analytics.real_matrix.RealMatrixBuilder` — it executes actual
backtests, caches each node against the strategy *and* pricing source hashes,
and reports `NO_DATA` / `PENDING_ADAPTER` / `ERROR` honestly instead of filling
the gap with a number.

    python -m sajim_quant matrix --workers 4

`timeframe_matrix.py` was the same pattern for the timeframe sweep and is
tombstoned alongside this file.
"""

from __future__ import annotations

_MESSAGE = (
    "sajim_quant.analytics.universe_matrix fabricated metrics and overwrote the "
    "real matrix JSON that the dashboard serves. It has been removed. Use "
    "sajim_quant.analytics.real_matrix.RealMatrixBuilder (python -m sajim_quant matrix), "
    "which runs actual backtests and marks gaps as NO_DATA/PENDING_ADAPTER instead "
    "of inventing numbers."
)


def __getattr__(name: str):
    """Any attribute access fails loudly, including the old entry points."""
    raise RuntimeError(f"{_MESSAGE} (attempted to access '{name}')")


if __name__ == "__main__":
    raise SystemExit(_MESSAGE)
