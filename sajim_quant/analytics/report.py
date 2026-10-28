"""
SAJIM Quant - Institutional Tear Sheet Generator

Generates executive-level markdown and ASCII performance tear sheets from PerformanceMetrics.

Several metrics are Optional: a ratio whose denominator is zero (profit factor
with no losing trades, Calmar on a run too short to annualize) is None rather
than a sentinel. Every formatter here routes through `_f`/`_grade` so those
render as "n/a" instead of raising on a None format spec.
"""

from __future__ import annotations

from typing import Optional

from sajim_quant.analytics.metrics import MIN_CAGR_DAYS, PerformanceMetrics

NA = "n/a"


def _f(value: Optional[float], spec: str = ".2f", prefix: str = "", suffix: str = "") -> str:
    """Format a possibly-None number, falling back to 'n/a'."""
    if value is None:
        return NA
    return f"{prefix}{value:{spec}}{suffix}"


def _grade(value: Optional[float], thresholds, labels, unavailable: str = "⚪ NOT COMPUTABLE") -> str:
    """
    Pick a label by comparing `value` against ascending `thresholds`.

    len(labels) must be len(thresholds) + 1. None yields `unavailable` so a
    missing metric never silently grades as the worst bucket.
    """
    if value is None:
        return unavailable
    for i, t in enumerate(thresholds):
        if value < t:
            return labels[i]
    return labels[-1]


def generate_text_tear_sheet(m: PerformanceMetrics) -> str:
    lines = [
        "=" * 70,
        f"  SAJIM QUANT PERFORMANCE TEAR SHEET: {m.strategy_name.upper()}",
        f"  Engine: {m.engine} | Symbol: {m.symbol} | Timeframe: {m.timeframe}",
        "=" * 70,
        f"  Initial Balance:      ${m.initial_balance:>12,.2f}   Total Trades:       {m.total_trades:>8}",
        f"  Final Balance:        ${m.final_balance:>12,.2f}   Win Rate:           {m.win_rate_pct:>7.1f}%",
        f"  Net Profit:           ${m.net_profit:>12,.2f}   Profit Factor:      {_f(m.profit_factor, '>8.2f'):>8}",
        f"  Total Return:          {m.return_pct:>12.2f}%   Expectancy:         ${m.expectancy:>7.2f}",
        f"  CAGR:                  {_f(m.cagr_pct, '>12.2f'):>12}{'%' if m.cagr_pct is not None else ' '}   Win/Loss Ratio:     {_f(m.win_loss_ratio, '>8.2f'):>8}",
        "-" * 70,
        "  RISK & DRAWDOWN METRICS",
        "-" * 70,
        f"  Max Drawdown ($):     ${m.max_drawdown_amount:>12,.2f}   Sharpe Ratio:       {m.sharpe_ratio:>8.2f}",
        f"  Max Drawdown (%):      {m.max_drawdown_pct:>12.2f}%   Sortino Ratio:      {_f(m.sortino_ratio, '>8.2f'):>8}",
        f"  Recovery Factor:       {_f(m.recovery_factor, '>12.2f'):>12}    Calmar Ratio:       {_f(m.calmar_ratio, '>8.2f'):>8}",
        "-" * 70,
        "  TRADE PROFILE & EXECUTION",
        "-" * 70,
        f"  Winning Trades:        {m.winning_trades:>12}    Avg Win:            ${m.avg_win:>7.2f}",
        f"  Losing Trades:         {m.losing_trades:>12}    Avg Loss:           ${m.avg_loss:>7.2f}",
        f"  Max Cons. Wins:        {m.max_consecutive_wins:>12}    Max Cons. Losses:   {m.max_consecutive_losses:>8}",
        f"  Avg Duration (min):    {m.avg_duration_minutes:>12.1f}    Total Commission:   ${m.total_commission:>7.2f}",
    ]
    if m.avg_mae is not None and m.avg_mfe is not None:
        lines.append(f"  Avg MAE (Adverse):    ${m.avg_mae:>12.2f}    Avg MFE (Favorable):${m.avg_mfe:>7.2f}")

    # State the measurement basis rather than letting the reader assume.
    if m.drawdown_includes_intratrade:
        lines.append(
            f"  Drawdown basis: intra-trade excursion included "
            f"(closed-trade only would read {m.max_drawdown_pct_closed:.2f}%)"
        )
    else:
        lines.append("  Drawdown basis: trade closes only — no MAE on these trades, so this is a FLOOR")

    if m.cagr_pct is None and m.span_days:
        lines.append(f"  CAGR/Calmar suppressed: run spans {m.span_days}d (< {MIN_CAGR_DAYS}d minimum)")

    lines.append("=" * 70)
    return "\n".join(lines)


def generate_markdown_tear_sheet(m: PerformanceMetrics, assumptions: Optional[dict] = None) -> str:
    assump_str = ""
    if assumptions:
        assump_str = " | ".join([f"**{k}**: `{v}`" for k, v in assumptions.items()])

    pf_status = _grade(m.profit_factor, [1.5], ["🟡 MARGINAL", "🟢 PASS"])
    cagr_status = _grade(
        m.cagr_pct, [15.0], ["🟡 WATCH", "🟢 PASS"],
        unavailable=f"⚪ SPAN < {MIN_CAGR_DAYS}d",
    )
    dd_status = _grade(
        m.max_drawdown_pct, [10.0, 20.0],
        ["🟢 LOW (<10%)", "🟡 MODERATE (<20%)", "🔴 HIGH (>20%)"],
    )
    sharpe_status = _grade(
        m.sharpe_ratio, [1.0, 2.0],
        ["🔴 POOR (<1.0)", "🟡 GOOD (>1.0)", "🟢 EXCELLENT (>2.0)"],
    )

    dd_basis = (
        f"Includes intra-trade excursion (closed-trade only: {m.max_drawdown_pct_closed:.2f}%)"
        if m.drawdown_includes_intratrade
        else "**Trade closes only — a floor, not the true peak-to-trough**"
    )

    md = f"""# SAJIM Benchmark Tear Sheet: {m.strategy_name}

> **Engine**: `{m.engine}` | **Symbol**: `{m.symbol}` | **Timeframe**: `{m.timeframe}`
> {assump_str}

---

## 1. Executive Performance Summary

| Metric | Strategy Value | Benchmark Target | Status |
| :--- | :--- | :--- | :---: |
| **Total Net Profit** | **${m.net_profit:,.2f}** | > $0.00 | {'🟢 PASS' if m.net_profit > 0 else '🔴 FAIL'} |
| **Total Return** | **{m.return_pct:.2f}%** | > 20.0% | {'🟢 PASS' if m.return_pct >= 20 else '🟡 WATCH'} |
| **CAGR** | **{_f(m.cagr_pct, '.2f', suffix='%')}** | > 15.0% | {cagr_status} |
| **Profit Factor** | **{_f(m.profit_factor)}** | > 1.50 | {pf_status} |
| **Win Rate** | **{m.win_rate_pct:.1f}%** | > 50.0% | {'🟢 PASS' if m.win_rate_pct >= 50 else '⚪ ACCEPTABLE'} |
| **Expectancy per Trade** | **${m.expectancy:.2f}** | > $0.00 | {'🟢 PASS' if m.expectancy > 0 else '🔴 NEGATIVE'} |

---

## 2. Risk & Drawdown Profile

| Risk Metric | Value | Risk Rating |
| :--- | :--- | :--- |
| **Max Drawdown ($)** | ${m.max_drawdown_amount:,.2f} | — |
| **Max Drawdown (%)** | **{m.max_drawdown_pct:.2f}%** | {dd_status} |
| **Sharpe Ratio (Ann.)** | **{m.sharpe_ratio:.2f}** | {sharpe_status} |
| **Sortino Ratio** | **{_f(m.sortino_ratio)}** | Downside deviation risk-adjusted return |
| **Calmar Ratio** | **{_f(m.calmar_ratio)}** | CAGR / Max DD |
| **Recovery Factor** | **{_f(m.recovery_factor)}** | Net Profit / Max DD Amount |

**Drawdown basis:** {dd_basis}

---

## 3. Trade Execution Statistics

| Dimension | Value | Notes |
| :--- | :--- | :--- |
| **Total Trades** | {m.total_trades} | Statistically {'Valid (>100)' if m.total_trades >= 100 else 'Small (<100)'} |
| **Winning Trades / Losing Trades** | {m.winning_trades} / {m.losing_trades} | Ratio: {_f(m.win_loss_ratio)} |
| **Average Win / Average Loss** | ${m.avg_win:.2f} / ${m.avg_loss:.2f} | Reward-to-Risk profile |
| **Max Consecutive Wins / Losses** | {m.max_consecutive_wins} / {m.max_consecutive_losses} | Run clustering |
| **Average Trade Duration** | {m.avg_duration_minutes:.1f} minutes | Intraday / Swing classification |
| **Total Commissions & Fees** | ${m.total_commission + m.total_swap:.2f} | Execution friction cost |
| **Run Span** | {m.span_days} days | {'CAGR suppressed below ' + str(MIN_CAGR_DAYS) + 'd' if m.cagr_pct is None else 'Long enough to annualize'} |

---
*Report Generated by SAJIM Quant Benchmarking Platform*
"""
    return md
