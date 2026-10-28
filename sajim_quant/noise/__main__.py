"""
CLI:
  python -m sajim_quant.noise forecast --symbol XAUUSD          latest forecast (+ --save writes the series)
  python -m sajim_quant.noise evaluate --symbol XAUUSD          out-of-sample quality vs baselines
  python -m sajim_quant.noise diagnose path/to/X_result.json    tag trades + regime breakdown
"""

import argparse
import json
import sys
from pathlib import Path

from . import HONESTY_NOTE
from .data import load_m5, m5_returns
from .diagnose import diagnose_file, render_markdown
from .model import build, evaluate

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "noise"


def _state(symbol, data_root):
    rets = m5_returns(load_m5(symbol, data_root))
    return build(rets)


def cmd_forecast(a):
    table, prof = _state(a.symbol, a.data_root)
    last = table.dropna(subset=["rv_forecast"]).iloc[-1]
    payload = {
        "symbol": a.symbol.upper(),
        "forecast_for_day": str(last["day"].date()),
        "vol_daily_pct": round(float(last["vol_daily_pct"]), 3),
        "vol_ann_pct": round(float(last["vol_ann_pct"]), 1),
        "vol_pctile": None if last["vol_pctile"] != last["vol_pctile"] else round(float(last["vol_pctile"]), 1),
        "regime": last["regime"],
        "session_factor_by_utc_hour": [round(float(x) * 24, 2) for x in prof.iloc[-1].fillna(0)],
        "note": HONESTY_NOTE,
    }
    if a.save:
        d = OUTPUT_DIR / "forecasts"
        d.mkdir(parents=True, exist_ok=True)
        table.to_parquet(d / f"{a.symbol.upper()}_daily.parquet", index=False)
        prof.reset_index().to_parquet(d / f"{a.symbol.upper()}_profile.parquet", index=False)
        payload["saved_to"] = str(d)
    print(json.dumps(payload, indent=2, default=str))


def cmd_evaluate(a):
    table, _ = _state(a.symbol, a.data_root)
    print(json.dumps({"symbol": a.symbol.upper(), **evaluate(table)}, indent=2))


def cmd_diagnose(a):
    out_dir = Path(a.out_dir) if a.out_dir else OUTPUT_DIR / "diagnose"
    summary, enriched, report = diagnose_file(a.result, out_dir, symbol=a.symbol, data_root=a.data_root)
    if a.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(render_markdown(summary))
    print(f"\nenriched: {enriched}\nreport:   {report}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    ap = argparse.ArgumentParser(prog="sajim_quant.noise")
    ap.add_argument("--data-root", help="parquet root (default: ../data/parquet)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("forecast")
    f.add_argument("--symbol", required=True)
    f.add_argument("--save", action="store_true")
    f.set_defaults(fn=cmd_forecast)

    e = sub.add_parser("evaluate")
    e.add_argument("--symbol", required=True)
    e.set_defaults(fn=cmd_evaluate)

    d = sub.add_parser("diagnose")
    d.add_argument("result", help="BacktestResult JSON from the strategy tester")
    d.add_argument("--symbol", help="override the noise-data symbol")
    d.add_argument("--out-dir")
    d.add_argument("--json", action="store_true", help="print the summary as JSON")
    d.set_defaults(fn=cmd_diagnose)

    a = ap.parse_args()
    try:
        a.fn(a)
    except FileNotFoundError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
