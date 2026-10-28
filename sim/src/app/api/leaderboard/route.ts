import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { listResultEntries, resolveResultFile } from "../../../lib/resultFiles";
import {
  buildMonthlyPnl,
  computePairwiseCorrelation,
  monthlyPnlMap,
  parseResultJSON,
  type ResultEntry,
} from "../../../lib/results";

/**
 * Leaderboard, derived entirely from the real backtest result JSONs on disk.
 *
 * This route used to serve `data/leaderboard.json` — a hand-maintained file last
 * edited 2026-09-15 — with ~200 lines of hardcoded metrics as a fallback. It
 * disagreed with the engine's own output (SMC: $4,209/14 trades on the board vs
 * $15,181/69 trades in the result file) and carried three rows (GoldOrb_ORB and
 * both Scalper1 EAs) that had no result file at all, because the headless MT5
 * adapter has never completed a run. Those numbers had no provenance.
 *
 * Now every row traces to a file: `result_file` names it and `source` says which
 * directory it came from. A strategy with no result does not appear — the UI
 * shows what has actually been measured, and the catalog (/api/strategies) is
 * where you see what *could* be measured.
 */

type LeaderboardRow = {
  id: string;
  name: string;
  folder: string;
  engine: string;
  symbol: string;
  timeframe: string;
  trades: number;
  win_rate_pct: number;
  profit_factor: number | null;
  net_pnl: number;
  max_drawdown_pct: number;
  max_drawdown_pct_closed: number | null;
  drawdown_includes_intratrade: boolean;
  sharpe_ratio: number;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  cagr_pct: number | null;
  span_days: number | null;
  tier: string;
  description: string;
  /** Execution assumptions the run was priced under (RunMetadata.notes). */
  assumptions: string;
  result_file: string;
  source: ResultEntry["source"];
};

/** Catalog lookup so rows can carry a folder and description. */
function loadCatalog(): Record<string, { folder?: string; description?: string; name?: string }> {
  const candidates = [
    path.resolve(process.cwd(), "..", "sajim_quant", "core", "strategy_catalog.json"),
    path.resolve(process.cwd(), "sajim_quant", "core", "strategy_catalog.json"),
  ];
  for (const p of candidates) {
    if (!fs.existsSync(p)) continue;
    try {
      const items = JSON.parse(fs.readFileSync(p, "utf-8")) as {
        id: string;
        name?: string;
        folder?: string;
        description?: string;
      }[];
      const byKey: Record<string, { folder?: string; description?: string; name?: string }> = {};
      for (const it of items) {
        byKey[it.id.toLowerCase()] = { folder: it.folder, description: it.description, name: it.name };
        if (it.name) byKey[it.name.toLowerCase()] = byKey[it.id.toLowerCase()];
      }
      return byKey;
    } catch (err) {
      console.error("Failed to parse strategy_catalog.json:", err);
    }
  }
  return {};
}

/**
 * Tier from measured risk-adjusted performance.
 *
 * Drawdown is tested first so a profitable strategy with a punishing drawdown is
 * labelled for the drawdown, not for the profit — mirroring
 * `_classify_status()` in analytics/real_matrix.py. A null profit factor (no
 * losing trades) counts as not clearing any profit-factor bar rather than as
 * infinitely good.
 */
function classifyTier(
  sharpe: number,
  pf: number | null,
  dd: number,
  pnl: number
): string {
  if (dd >= 25) return "TIER 3: TOXIC DD";
  const pfVal = pf ?? 0;
  if (sharpe >= 1.8 && pfVal >= 1.5 && dd <= 5 && pnl > 0) return "TIER 1: ELITE";
  if (sharpe >= 1.0 && pfVal >= 1.1 && pnl > 0) return "TIER 2: VIABLE";
  if (sharpe >= 0 && pnl >= 0) return "TIER 2: MARGINAL";
  return "TIER 3: DEGRADED";
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** One leaderboard row per readable result file. */
function buildRows(): { rows: LeaderboardRow[]; skipped: string[] } {
  const catalog = loadCatalog();
  const { entries, skipped } = listResultEntries();
  const rows: LeaderboardRow[] = [];

  for (const e of entries) {
    const full = resolveResultFile(e);
    if (!full) continue;
    let raw: unknown;
    try {
      raw = JSON.parse(fs.readFileSync(full, "utf-8"));
    } catch {
      skipped.push(e.file);
      continue;
    }
    const parsed = parseResultJSON(raw);
    if (!parsed) {
      skipped.push(e.file);
      continue;
    }

    const mx = parsed.metrics;
    const md = parsed.metadata;
    const name = md.strategy_name || e.strategy_name || e.file.replace(/_result\.json$/i, "");
    const meta = catalog[name.toLowerCase()] ?? {};

    const netPnl = num(mx.net_profit, parsed.trades.reduce((a, t) => a + (t.net_pnl ?? 0), 0));
    const sharpe = num(mx.sharpe_ratio);
    const pf = numOrNull(mx.profit_factor);
    const dd = num(mx.max_drawdown_pct);

    rows.push({
      // One row per file, so key on the file: two files can share strategy/symbol/timeframe
      // (e.g. GoldPullbackAI_XAUUSD_H1 and Sovereign_Sniper_GoldPullbackAI_XAUUSD_H1).
      id: e.file.replace(/_result\.json$/i, "").replace(/[^A-Za-z0-9_]/g, "_"),
      name,
      folder: meta.folder ?? "",
      engine: md.engine ?? e.engine ?? "",
      symbol: md.symbol ?? e.symbol ?? "",
      timeframe: md.timeframe ?? e.timeframe ?? "",
      trades: num(mx.total_trades, parsed.trades.length),
      win_rate_pct: num(mx.win_rate_pct),
      profit_factor: pf,
      net_pnl: netPnl,
      max_drawdown_pct: dd,
      max_drawdown_pct_closed: numOrNull(mx.max_drawdown_pct_closed),
      drawdown_includes_intratrade: mx.drawdown_includes_intratrade === true,
      sharpe_ratio: sharpe,
      sortino_ratio: numOrNull(mx.sortino_ratio),
      calmar_ratio: numOrNull(mx.calmar_ratio),
      cagr_pct: numOrNull(mx.cagr_pct),
      span_days: numOrNull(mx.span_days),
      tier: classifyTier(sharpe, pf, dd, netPnl),
      description: meta.description ?? (md.notes ? String(md.notes) : ""),
      assumptions: md.notes ? String(md.notes) : "",
      result_file: e.file,
      source: e.source,
    });
  }

  // Best risk-adjusted first, then by net PnL.
  rows.sort((a, b) => b.sharpe_ratio - a.sharpe_ratio || b.net_pnl - a.net_pnl);
  return { rows, skipped };
}

/**
 * Cross-strategy return correlation from real monthly PnL buckets, aligned by
 * calendar month. Pearson r is computed only over months both strategies traded,
 * and only when at least three overlap. Null means "not enough shared history",
 * never zero.
 */
function realCorrelation(): {
  matrix: Record<string, Record<string, number | null>>;
  seriesCount: number;
} | null {
  const { entries } = listResultEntries();
  const series: { name: string; months: Map<string, number> }[] = [];
  for (const e of entries) {
    const full = resolveResultFile(e);
    if (!full) continue;
    try {
      const raw = JSON.parse(fs.readFileSync(full, "utf-8"));
      if (!Array.isArray(raw?.trades) || raw.trades.length === 0) continue;
      series.push({
        name: e.strategy_name || e.file,
        months: monthlyPnlMap(buildMonthlyPnl(raw.trades)),
      });
    } catch {
      continue;
    }
  }
  if (series.length < 2) return null;

  const matrix: Record<string, Record<string, number | null>> = {};
  for (const a of series) {
    matrix[a.name] = {};
    for (const b of series) {
      if (a === b) {
        matrix[a.name][b.name] = 1;
        continue;
      }
      const shared = [...a.months.entries()].filter(([k]) => b.months.has(k));
      matrix[a.name][b.name] =
        shared.length >= 3
          ? computePairwiseCorrelation(
              shared.map(([, v]) => v),
              shared.map(([k]) => b.months.get(k) ?? 0)
            )
          : null;
    }
  }
  return { matrix, seriesCount: series.length };
}

export async function GET() {
  try {
    const { rows, skipped } = buildRows();
    const real = realCorrelation();

    const sharpes = rows.map((r) => r.sharpe_ratio);
    const pfs = rows.map((r) => r.profit_factor).filter((v): v is number => v !== null);

    return NextResponse.json(
      {
        success: true,
        // Every row is traceable to a file on disk; nothing here is seeded or
        // hand-maintained. The UI shows this as its provenance badge.
        source: "data/results",
        last_updated: new Date().toISOString(),
        result_count: rows.length,
        skipped_files: skipped,
        strategies: rows,
        correlationMatrix: real ? real.matrix : {},
        correlationSource: real ? "real" : "insufficient",
        correlationResultCount: real ? real.seriesCount : 0,
        portfolioSummary: {
          totalStrategies: rows.length,
          profitableStrategies: rows.filter((r) => r.net_pnl > 0).length,
          topSharpe: sharpes.length > 0 ? Math.max(...sharpes) : null,
          topProfitFactor: pfs.length > 0 ? Math.max(...pfs) : null,
          totalVolumeTrades: rows.reduce((acc, r) => acc + r.trades, 0),
        },
      },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (error) {
    console.error("Leaderboard API error:", error);
    return NextResponse.json({ success: false, error: "Internal Server Error" }, { status: 500 });
  }
}
