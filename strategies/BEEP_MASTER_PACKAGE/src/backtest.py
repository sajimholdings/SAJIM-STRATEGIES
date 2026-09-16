"""
BEEP Backtester
================
Compares trading performance on raw data vs BEEP-filtered data.

Strategy (simple for Phase 0 proof):
- Follow the trend direction
- RAW: Use raw candle direction to decide (buy if bullish, sell if bearish)
- BEEP: Use cleaned signal direction to decide (ignore noise candles)

We're not trying to build a perfect strategy here.
We're trying to answer ONE question:
    "Does filtering noise improve trading outcomes?"
"""

import numpy as np
import pandas as pd


class SimpleBacktest:
    """
    Simple trend-following backtest to compare raw vs filtered signals.
    """

    def __init__(self, initial_balance: float = 10000.0, risk_per_trade: float = 0.01):
        """
        Args:
            initial_balance: Starting balance in USD
            risk_per_trade: Risk per trade as fraction of balance (0.01 = 1%)
        """
        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade

    def run_raw(self, df: pd.DataFrame) -> dict:
        """
        Backtest using raw (unfiltered) signals.
        Trades every candle based on previous candle direction.
        """
        return self._run(df, use_filter=False)

    def run_filtered(self, df: pd.DataFrame) -> dict:
        """
        Backtest using BEEP-filtered signals.
        Skips trades when noise is detected.
        """
        return self._run(df, use_filter=True)

    def _run(self, df: pd.DataFrame, use_filter: bool) -> dict:
        """Internal backtest logic."""
        balance = self.initial_balance
        trades = []
        position = 0  # 1 = long, -1 = short, 0 = flat

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i - 1]

            # Decide: should we trade this candle?
            if use_filter and row.get("is_noise", False):
                # BEEP says this is noise — skip it
                continue

            # Simple signal: follow previous clean direction
            if use_filter:
                signal = np.sign(prev.get("signal_direction", 0))
            else:
                signal = prev.get("direction", 0)

            if signal == 0:
                continue

            # Calculate P&L for this candle
            price_change_pct = (row["Close"] - row["Open"]) / row["Open"]

            # If we're following the signal direction
            trade_pnl = price_change_pct * signal * balance * self.risk_per_trade

            balance += trade_pnl
            trades.append({
                "datetime": df.index[i],
                "direction": "LONG" if signal > 0 else "SHORT",
                "pnl": trade_pnl,
                "balance": balance,
                "was_noise": row.get("is_noise", False),
            })

        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

        # Calculate metrics
        total_pnl = balance - self.initial_balance
        num_trades = len(trades)
        win_trades = len([t for t in trades if t["pnl"] > 0]) if trades else 0
        loss_trades = len([t for t in trades if t["pnl"] < 0]) if trades else 0
        win_rate = (win_trades / num_trades * 100) if num_trades > 0 else 0

        return {
            "mode": "BEEP Filtered" if use_filter else "Raw (Unfiltered)",
            "initial_balance": self.initial_balance,
            "final_balance": round(balance, 2),
            "total_pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / self.initial_balance * 100, 2),
            "total_trades": num_trades,
            "winning_trades": win_trades,
            "losing_trades": loss_trades,
            "win_rate": round(win_rate, 1),
            "trades": trades_df,
        }

    def compare(self, df: pd.DataFrame) -> dict:
        """
        Run both raw and filtered backtests and compare.

        Returns:
            dict with 'raw', 'filtered', and 'comparison' keys
        """
        raw = self.run_raw(df)
        filtered = self.run_filtered(df)

        improvement = {
            "pnl_difference": round(filtered["total_pnl"] - raw["total_pnl"], 2),
            "return_difference": round(filtered["return_pct"] - raw["return_pct"], 2),
            "win_rate_difference": round(filtered["win_rate"] - raw["win_rate"], 1),
            "trades_avoided": raw["total_trades"] - filtered["total_trades"],
            "filter_helped": filtered["total_pnl"] > raw["total_pnl"],
        }

        return {
            "raw": raw,
            "filtered": filtered,
            "comparison": improvement,
        }


def print_comparison(results: dict):
    """Pretty-print the backtest comparison."""
    raw = results["raw"]
    filtered = results["filtered"]
    comp = results["comparison"]

    print("\n" + "=" * 70)
    print("  BEEP BACKTEST COMPARISON")
    print("=" * 70)

    print(f"\n  {'Metric':<25s} {'Raw':>15s} {'BEEP Filtered':>15s}")
    print(f"  {'-'*25} {'-'*15} {'-'*15}")
    print(f"  {'Initial Balance':<25s} {'$' + str(raw['initial_balance']):>15s} {'$' + str(filtered['initial_balance']):>15s}")
    print(f"  {'Final Balance':<25s} {'$' + str(raw['final_balance']):>15s} {'$' + str(filtered['final_balance']):>15s}")
    print(f"  {'Total P&L':<25s} {'$' + str(raw['total_pnl']):>15s} {'$' + str(filtered['total_pnl']):>15s}")
    print(f"  {'Return %':<25s} {str(raw['return_pct']) + '%':>15s} {str(filtered['return_pct']) + '%':>15s}")
    print(f"  {'Total Trades':<25s} {str(raw['total_trades']):>15s} {str(filtered['total_trades']):>15s}")
    print(f"  {'Win Rate':<25s} {str(raw['win_rate']) + '%':>15s} {str(filtered['win_rate']) + '%':>15s}")

    print(f"\n  {'─' * 55}")
    print(f"  Trades avoided by BEEP:  {comp['trades_avoided']}")
    print(f"  P&L improvement:         ${comp['pnl_difference']}")
    print(f"  Return improvement:      {comp['return_difference']}%")
    print(f"  Win rate improvement:    {comp['win_rate_difference']}%")

    verdict = "✅ BEEP FILTER IMPROVED RESULTS" if comp["filter_helped"] else "❌ FILTER DID NOT HELP (needs tuning)"
    print(f"\n  VERDICT: {verdict}")
    print("=" * 70)
