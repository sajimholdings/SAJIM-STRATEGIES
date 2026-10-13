"""
sajim - command line entry point.

    sajim list                         print the registry table
    sajim export                       regenerate the dashboard JSON files from results/
    sajim import-legacy <leaderboard.json> [--tf-matrix timeframe_matrix.json] [--tear-sheets DIR]
    sajim show <strategy_id> <run_id>  dump one run's metrics / Monte Carlo summary
    sajim remove <strategy_id> <run_id> [--delete-artifacts]
    sajim run-all                      execute every runner script under scripts/ (needs market data)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sajim_quant.data.loader import find_repo_root
from sajim_quant.registry import ResultsRegistry, export_dashboard


def _print_table(rows, cols):
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in rows)) if rows else len(c) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


def cmd_list(args):
    reg = ResultsRegistry(args.results_dir)
    rows = [r.to_dict() for r in reg.runs()]
    rows.sort(key=lambda r: (r["strategy_id"], not r["primary"]))
    if not rows:
        print(f"Registry is empty ({reg.index_path}).")
        return 0
    for r in rows:
        r["primary"] = "*" if r["primary"] else ""
    _print_table(rows, ["primary", "strategy_id", "run_id", "engine", "symbol", "timeframe", "trades",
                        "win_rate_pct", "profit_factor", "net_pnl", "max_drawdown_pct", "sharpe_ratio", "tier", "source"])
    return 0


def cmd_export(args):
    reg = ResultsRegistry(args.results_dir)
    written = export_dashboard(reg, Path(args.public_dir) if args.public_dir else None)
    for name, p in written.items():
        print(f"[export] {name:32s} -> {p}")
    return 0


def cmd_import_legacy(args):
    from sajim_quant.registry.legacy import import_legacy_leaderboard, import_legacy_timeframe_matrix
    reg = ResultsRegistry(args.results_dir)
    recs = import_legacy_leaderboard(reg, Path(args.leaderboard), Path(args.tear_sheets) if args.tear_sheets else None)
    print(f"[legacy] imported {len(recs)} leaderboard rows")
    if args.tf_matrix:
        more = import_legacy_timeframe_matrix(reg, Path(args.tf_matrix))
        print(f"[legacy] imported {len(more)} timeframe-matrix rows")
    return 0


def cmd_show(args):
    reg = ResultsRegistry(args.results_dir)
    detail = reg.run_detail(args.strategy_id, args.run_id)
    if detail is None:
        print("run not found", file=sys.stderr)
        return 1
    detail.pop("tear_sheet_md", None)
    if detail.get("metrics"):
        detail["metrics"].pop("equity_series", None)
    print(json.dumps(detail, indent=2))
    return 0


def cmd_remove(args):
    reg = ResultsRegistry(args.results_dir)
    ok = reg.remove_run(args.strategy_id, args.run_id, delete_artifacts=args.delete_artifacts)
    print("removed" if ok else "run not found")
    return 0 if ok else 1


def cmd_run_all(args):
    root = find_repo_root()
    scripts = sorted((root / "scripts").glob("run_*.py"))
    if not scripts:
        print("no scripts/run_*.py found")
        return 1
    failed = []
    for s in scripts:
        print(f"\n[run-all] {s.relative_to(root)}", flush=True)
        rc = subprocess.call([sys.executable, str(s)], cwd=str(root))
        if rc != 0:
            failed.append((s.name, rc))
    if failed:
        print("\nFAILED:", ", ".join(f"{n} (rc={rc})" for n, rc in failed))
        return 1
    if not args.no_export:
        export_dashboard(ResultsRegistry(args.results_dir))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sajim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results-dir", default=None, help="override results/ location (or SAJIM_RESULTS_DIR)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    e = sub.add_parser("export"); e.add_argument("--public-dir", default=None); e.set_defaults(fn=cmd_export)
    il = sub.add_parser("import-legacy"); il.add_argument("leaderboard"); il.add_argument("--tf-matrix", default=None)
    il.add_argument("--tear-sheets", default=None); il.set_defaults(fn=cmd_import_legacy)
    s = sub.add_parser("show"); s.add_argument("strategy_id"); s.add_argument("run_id"); s.set_defaults(fn=cmd_show)
    r = sub.add_parser("remove"); r.add_argument("strategy_id"); r.add_argument("run_id")
    r.add_argument("--delete-artifacts", action="store_true"); r.set_defaults(fn=cmd_remove)
    ra = sub.add_parser("run-all"); ra.add_argument("--no-export", action="store_true"); ra.set_defaults(fn=cmd_run_all)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
