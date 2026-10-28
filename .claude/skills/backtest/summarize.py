"""
Compact summary of a SAJIM BacktestResult JSON (and its noise diagnosis, if present).

Never prints the raw trades array — only metadata, engine metrics, monthly P&L,
trade-level aggregates and the noise summary — so the analysis stays small.

    python .claude/skills/backtest/summarize.py data/results/<file>_result.json
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main(path_arg: str) -> None:
    path = Path(path_arg)
    if not path.is_absolute():
        path = ROOT / path
    r = json.loads(path.read_text(encoding="utf-8"))
    trades = r.get("trades", [])
    pnl = [float(t.get("net_pnl") or 0) for t in trades]

    out = {
        "file": path.name,
        "metadata": r.get("metadata", {}),
        "metrics": {k: v for k, v in (r.get("metrics") or {}).items() if not isinstance(v, (list, dict))},
        "trades": len(trades),
    }
    if trades:
        months = defaultdict(float)
        for t, p in zip(trades, pnl):
            months[str(t.get("exit_time") or t.get("entry_time"))[:7]] += p
        streak = worst = 0
        for p in pnl:
            streak = streak + 1 if p < 0 else 0
            worst = max(worst, streak)
        by_dir = defaultdict(list)
        for t, p in zip(trades, pnl):
            by_dir[str(t.get("direction", "?")).upper()].append(p)
        out["trade_stats"] = {
            "largest_win": round(max(pnl), 2),
            "largest_loss": round(min(pnl), 2),
            "max_consecutive_losses": worst,
            "by_direction": {d: {"n": len(v), "net": round(sum(v), 2)} for d, v in by_dir.items()},
            "top5_pnl_share_pct": round(100 * sum(sorted(pnl, reverse=True)[:5]) / sum(pnl), 1) if sum(pnl) > 0 else None,
        }
        out["monthly_pnl"] = {m: round(v, 2) for m, v in sorted(months.items())}

    noise = ROOT / "data" / "noise" / "diagnose" / f"{path.stem.removesuffix('_result')}_noise_enriched.json"
    if noise.exists() and noise.stat().st_mtime >= path.stat().st_mtime:
        out["noise_summary"] = json.loads(noise.read_text(encoding="utf-8")).get("noise_summary")
    else:
        out["noise_summary"] = None

    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        sys.exit("usage: summarize.py <result.json>")
    main(sys.argv[1])
