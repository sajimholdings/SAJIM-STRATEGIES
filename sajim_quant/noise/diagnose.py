"""
Diagnose a backtest by the noise state at each trade's entry.

Reads a SAJIM BacktestResult JSON (the universal schema), tags every trade's
custom_fields with np_* noise fields, and breaks performance down by:
  - regime          calm / normal / storm  (forecast vs its own past year)
  - session         quiet / normal / peak hour  (hour-of-day share of daily noise)
  - recent noise    quieter / as expected / louder  (last hour realized vs expected)

Causality: everything attached to a trade uses only bars that closed at or
before entry_time, and daily forecasts made before the entry's trading day.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import HONESTY_NOTE
from .data import BAR_SECONDS, load_m5, m5_returns, session_day
from .model import build

SYMBOL_ALIASES = {"BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD", "GOLD": "XAUUSD", "SPX500": "US500"}
RECENT_BARS = 12            # the hour before entry
MAX_HOLD_BARS = 288 * 10    # cap the holding-window walk at 10 days of M5 bars
SMALL_SAMPLE = 30


def resolve_symbol(symbol: str) -> str:
    s = symbol.upper()
    return SYMBOL_ALIASES.get(s, s)


class NoiseState:
    """Forecast table + profile for one symbol, queried causally by timestamp."""

    def __init__(self, symbol: str, data_root=None):
        self.symbol = symbol
        bars = load_m5(symbol, data_root)
        self.rets = m5_returns(bars)
        self.table, self.profile = build(self.rets)
        self.bars_per_hour = self.profile.attrs["bars_per_hour"]
        self.days = self.table["day"].to_numpy()
        self.ret_times = self.rets["time"].to_numpy()
        self.ret_vals = self.rets["ret"].to_numpy()
        self.ret_hours = self.rets["hour"].to_numpy()
        self.data_start, self.data_end = self.rets["time"].iloc[0], self.rets["time"].iloc[-1]

    def _row(self, t: pd.Timestamp) -> int | None:
        """Table row whose forecast was built from data strictly before t's trading day."""
        d = np.datetime64(session_day(pd.Series([t])).iloc[0])
        i = int(np.searchsorted(self.days, d, side="left"))
        if i >= len(self.days) or self.days[i] - d > np.timedelta64(4, "D"):
            return None  # entry beyond the data, or inside a long hole
        return i

    def _bar_var(self, i: int) -> np.ndarray | None:
        """Expected variance of one M5 bar in each UTC hour (len 24) for row i."""
        rv = self.table["rv_forecast"].iat[i]
        prof = self.profile.iloc[i].to_numpy()
        bph = self.bars_per_hour.iloc[i].to_numpy()
        if not np.isfinite(rv) or not np.all(np.isfinite(prof)):
            return None
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(bph > 0, rv * prof / bph, 0.0)

    def at_entry(self, entry: pd.Timestamp, exit_: pd.Timestamp | None,
                 direction: str, entry_price: float, exit_price: float | None) -> dict:
        i = self._row(entry)
        if i is None or entry < self.data_start:
            return {"np_status": "no_data"}
        bar_var = self._bar_var(i)
        if bar_var is None:
            return {"np_status": "warming_up"}
        row = self.table.iloc[i]
        prof = self.profile.iloc[i].to_numpy()

        out = {
            "np_status": "ok",
            "np_forecast_day": str(pd.Timestamp(self.days[i]).date()),
            "np_vol_daily_pct": round(float(row["vol_daily_pct"]), 4),
            "np_vol_ann_pct": round(float(row["vol_ann_pct"]), 2),
            "np_vol_pctile": None if pd.isna(row["vol_pctile"]) else round(float(row["vol_pctile"]), 1),
            "np_regime": row["regime"] if isinstance(row["regime"], str) else "unranked",
            "np_session_factor": round(float(prof[entry.hour] * 24), 3),
            "np_bar_sigma_pct": round(100 * float(np.sqrt(bar_var[entry.hour])), 5),
        }

        # realized noise over the hour before entry vs what the model expected for it
        cutoff = np.datetime64(entry - pd.Timedelta(seconds=BAR_SECONDS))  # bars closed by entry
        j = int(np.searchsorted(self.ret_times, cutoff, side="right"))
        k = max(0, j - RECENT_BARS)
        if j - k == RECENT_BARS and (cutoff - self.ret_times[k]) <= np.timedelta64(2, "h"):
            realized = float(np.sum(self.ret_vals[k:j] ** 2))
            expected = float(np.sum(bar_var[self.ret_hours[k:j]]))
            if expected > 0:
                out["np_recent_vs_expected"] = round(float(np.sqrt(realized / expected)), 3)

        # expected noise over the holding window, and the trade's move measured in it
        if exit_price == entry_price:
            out["np_move_note"] = "exit_price equals entry_price in the source result; move not measured"
        elif exit_ is not None and exit_price and exit_ > entry:
            n = int(min(MAX_HOLD_BARS, max(1, round((exit_ - entry).total_seconds() / BAR_SECONDS))))
            hours = ((entry.hour * 60 + entry.minute) // 5 + np.arange(n)) // 12 % 24
            hold_sigma = float(np.sqrt(np.sum(bar_var[hours])))
            if hold_sigma > 0:
                sign = 1.0 if direction.upper() in ("BUY", "LONG") else -1.0
                out["np_hold_sigma_pct"] = round(100 * hold_sigma, 4)
                out["np_move_sigmas"] = round(sign * np.log(exit_price / entry_price) / hold_sigma, 3)
        return out


# ------------------------------------------------------------------ breakdowns
def _bucket_stats(df: pd.DataFrame) -> dict:
    pnl = df["net_pnl"]
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    stats = {
        "trades": int(len(df)),
        "win_rate_pct": round(100 * float((pnl > 0).mean()), 1),
        "net_pnl": round(float(pnl.sum()), 2),
        "avg_net_pnl": round(float(pnl.mean()), 2),
        "profit_factor": round(float(wins / losses), 2) if losses > 0 else None,
    }
    if "np_move_sigmas" in df and df["np_move_sigmas"].notna().any():
        stats["avg_move_sigmas"] = round(float(df["np_move_sigmas"].mean()), 3)
    if len(df) < SMALL_SAMPLE:
        stats["small_sample"] = True
    return stats


def _breakdown(df: pd.DataFrame, col: str, order: list[str]) -> dict:
    return {k: _bucket_stats(df[df[col] == k]) for k in order if (df[col] == k).any()}


def diagnose_result(result: dict, symbol: str | None = None, data_root=None) -> tuple[dict, dict]:
    """Returns (enriched_result, summary)."""
    meta = result["metadata"]
    sym = resolve_symbol(symbol or meta["symbol"])
    state = NoiseState(sym, data_root)

    rows = []
    for t in result["trades"]:
        entry = _ts(t["entry_time"])
        exit_ = _ts(t.get("exit_time"))
        tags = state.at_entry(entry, exit_, t["direction"], t["entry_price"], t.get("exit_price"))
        t.setdefault("custom_fields", {}).update(tags)
        rows.append({"net_pnl": t["net_pnl"], **tags})

    df = pd.DataFrame(rows)
    ok = df[df["np_status"] == "ok"].copy()
    if ok.empty:
        ok = pd.DataFrame(columns=["net_pnl", "np_regime", "np_session_factor", "np_vol_pctile"])
    ok["session"] = pd.cut(ok["np_session_factor"].astype(float), [-np.inf, 0.75, 1.25, np.inf],
                           labels=["quiet_hours", "normal_hours", "peak_hours"]).astype(object)
    if "np_recent_vs_expected" in ok:
        ok["recent"] = pd.cut(ok["np_recent_vs_expected"], [-np.inf, 0.8, 1.25, np.inf],
                              labels=["quieter", "as_expected", "louder"]).astype(object)

    summary = {
        "strategy": meta["strategy_name"],
        "symbol": meta["symbol"],
        "noise_data_symbol": sym,
        "timeframe": meta["timeframe"],
        "trades_total": int(len(df)),
        "trades_diagnosed": int(len(ok)),
        "trades_skipped": df["np_status"].value_counts().drop("ok", errors="ignore").to_dict(),
        "noise_data_range": [str(state.data_start), str(state.data_end)],
        "overall": _bucket_stats(ok) if len(ok) else {},
        "by_regime": _breakdown(ok, "np_regime", ["calm", "normal", "storm", "unranked"]),
        "by_session": _breakdown(ok, "session", ["quiet_hours", "normal_hours", "peak_hours"]),
        "by_recent_noise": _breakdown(ok, "recent", ["quieter", "as_expected", "louder"]) if "recent" in ok else {},
        "pnl_vs_vol_pctile_spearman": _spearman(ok, "np_vol_pctile"),
        "note": HONESTY_NOTE,
    }
    summary["findings"] = _findings(summary)
    return result, summary


def _ts(v) -> pd.Timestamp | None:
    if v in (None, ""):
        return None
    t = pd.Timestamp(v)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t


def _spearman(df: pd.DataFrame, col: str) -> float | None:
    d = df[[col, "net_pnl"]].dropna()
    if len(d) < SMALL_SAMPLE:
        return None
    return round(float(d[col].corr(d["net_pnl"], method="spearman")), 3)


def _findings(s: dict) -> list[str]:
    out = []
    unranked = s["by_regime"].get("unranked", {}).get("trades", 0)
    if unranked:
        out.append(f"{unranked} diagnosed trades are 'unranked': a forecast exists but fewer than "
                   f"60 past forecasts to rank it against yet.")
    if s["trades_diagnosed"] < s["trades_total"]:
        out.append(f"{s['trades_total'] - s['trades_diagnosed']} trades not diagnosed "
                   f"({s['trades_skipped']}) — outside noise-data coverage or model warm-up.")
    for name, label in (("by_regime", "regime"), ("by_session", "session"), ("by_recent_noise", "recent noise")):
        b = {k: v for k, v in s[name].items() if not v.get("small_sample") and k != "unranked"}
        if len(b) < 2:
            continue
        best = max(b, key=lambda k: b[k]["avg_net_pnl"])
        worst = min(b, key=lambda k: b[k]["avg_net_pnl"])
        total = sum(v["net_pnl"] for v in s[name].values())
        line = (f"By {label}: best '{best}' (avg {b[best]['avg_net_pnl']}, PF {b[best]['profit_factor']}), "
                f"worst '{worst}' (avg {b[worst]['avg_net_pnl']}, PF {b[worst]['profit_factor']}).")
        if total > 0 and b[worst]["net_pnl"] < 0:
            line += f" '{worst}' gave back {round(-b[worst]['net_pnl'] / total * 100)}% of net profit."
        out.append(line)
    rho = s["pnl_vs_vol_pctile_spearman"]
    if rho is not None:
        strength = "no meaningful" if abs(rho) < 0.1 else ("weak" if abs(rho) < 0.3 else "clear")
        out.append(f"P&L vs forecast-vol percentile: Spearman {rho} — {strength} relationship.")
    ms = s["overall"].get("avg_move_sigmas")
    if ms is not None:
        if ms <= 0:
            out.append(f"Average trade moves {ms} expected-noise sigmas over its hold, i.e. against "
                       f"the position: the typical trade loses ground measured in noise units.")
        else:
            size = "small relative to" if ms < 0.3 else "clearly above"
            out.append(f"Average trade moves +{ms} expected-noise sigmas in its favour over its hold "
                       f"— {size} the noise.")
    return out


# ------------------------------------------------------------------- reporting
def render_markdown(summary: dict) -> str:
    lines = [f"# Noise diagnosis — {summary['strategy']} ({summary['symbol']} {summary['timeframe']})", "",
             f"Noise model: walk-forward HAR-RV on {summary['noise_data_symbol']} M5 "
             f"({summary['noise_data_range'][0][:10]} → {summary['noise_data_range'][1][:10]}). "
             f"Trades diagnosed: {summary['trades_diagnosed']} / {summary['trades_total']}.", "",
             "## Findings", ""]
    lines += [f"- {f}" for f in summary["findings"]] or ["- (none)"]
    cols = ["trades", "win_rate_pct", "net_pnl", "avg_net_pnl", "profit_factor", "avg_move_sigmas"]
    for key, title in (("overall", "Overall"), ("by_regime", "By volatility regime"),
                       ("by_session", "By session (hour-of-day noise)"),
                       ("by_recent_noise", "By noise in the hour before entry")):
        block = {"all": summary[key]} if key == "overall" else summary[key]
        if not block:
            continue
        lines += ["", f"## {title}", "", "| bucket | " + " | ".join(cols) + " |",
                  "|---|" + "---:|" * len(cols)]
        for name, st in block.items():
            flag = " ⚠ small" if st.get("small_sample") else ""
            lines.append(f"| {name}{flag} | " + " | ".join(str(st.get(c, "")) for c in cols) + " |")
    lines += ["", f"_{summary['note']}_", ""]
    return "\n".join(lines)


def diagnose_file(path: str | Path, out_dir: str | Path, symbol: str | None = None,
                  data_root=None) -> tuple[dict, Path, Path]:
    path = Path(path)
    result = json.loads(path.read_text(encoding="utf-8"))
    enriched, summary = diagnose_result(result, symbol=symbol, data_root=data_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem.removesuffix("_result")
    enriched_path = out_dir / f"{stem}_noise_enriched.json"
    report_path = out_dir / f"{stem}_noise_report.md"
    enriched["noise_summary"] = summary
    enriched_path.write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")
    report_path.write_text(render_markdown(summary), encoding="utf-8")
    return summary, enriched_path, report_path
