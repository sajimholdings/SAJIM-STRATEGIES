"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { ddClass, engineFamily, pfClass, sharpeClass, signedMoney } from "../lib/format";
import type { LeaderboardRow, RunRef } from "../lib/types";
import { EngineChip, SourceBadge, TierBadge } from "./Badges";
import { SortHeader, useSort } from "./useSort";

export default function LeaderboardTab({ rows, onOpen }: { rows: LeaderboardRow[]; onOpen: (r: RunRef) => void }) {
  const [q, setQ] = useState("");
  const [engine, setEngine] = useState("ALL");
  const [tier, setTier] = useState("ALL");
  const [hideLegacy, setHideLegacy] = useState(false);

  const filtered = useMemo(
    () =>
      rows.filter((s) => {
        const text = `${s.name} ${s.symbol} ${s.engine} ${s.folder}`.toLowerCase();
        const matchQ = !q || text.includes(q.toLowerCase());
        const matchEngine = engine === "ALL" || engineFamily(s.engine) === engine;
        const matchTier = tier === "ALL" || s.tier.startsWith(tier);
        const matchSrc = !hideLegacy || s.has_artifacts;
        return matchQ && matchEngine && matchTier && matchSrc;
      }),
    [rows, q, engine, tier, hideLegacy],
  );
  const { sorted, key, dir, toggle } = useSort<LeaderboardRow>(filtered, "rank", "asc");

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row gap-2.5 items-stretch sm:items-center justify-between bg-slate-900/50 p-2.5 rounded-xl border border-slate-800/80">
        <div className="relative flex-1">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search strategies, symbols, runtimes, folders…" className="w-full bg-slate-950/80 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition" />
        </div>
        <div className="flex items-center gap-2">
          <select value={engine} onChange={(e) => setEngine(e.target.value)} className="bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyan-500">
            <option value="ALL">All runtimes</option>
            <option value="PYTHON">Python</option>
            <option value="MT5">MT5 headless</option>
            <option value="PINE">Pine import</option>
            <option value="DERIV">Deriv</option>
            <option value="OTHER">Other / unknown</option>
          </select>
          <select value={tier} onChange={(e) => setTier(e.target.value)} className="bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyan-500">
            <option value="ALL">All tiers</option>
            <option value="TIER 1">Tier 1: Elite</option>
            <option value="TIER 2">Tier 2: Viable / Marginal</option>
            <option value="TIER 3">Tier 3: Poor / Toxic / Insufficient</option>
          </select>
          <label className="flex items-center gap-1.5 text-xs text-slate-400 whitespace-nowrap">
            <input type="checkbox" checked={hideLegacy} onChange={(e) => setHideLegacy(e.target.checked)} className="accent-cyan-500" /> verified only
          </label>
        </div>
      </div>

      <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 text-[11px]">
                <SortHeader<LeaderboardRow> label="#" col="rank" active={key} dir={dir} onClick={toggle} align="center" className="w-12" />
                <SortHeader<LeaderboardRow> label="Strategy" col="name" active={key} dir={dir} onClick={toggle} align="left" />
                <th className="py-3 px-3 uppercase tracking-wider font-semibold">Runtime</th>
                <SortHeader<LeaderboardRow> label="Asset / TF" col="symbol" active={key} dir={dir} onClick={toggle} align="center" />
                <SortHeader<LeaderboardRow> label="Trades" col="trades" active={key} dir={dir} onClick={toggle} />
                <SortHeader<LeaderboardRow> label="Win %" col="win_rate_pct" active={key} dir={dir} onClick={toggle} />
                <SortHeader<LeaderboardRow> label="PF" col="profit_factor" active={key} dir={dir} onClick={toggle} />
                <SortHeader<LeaderboardRow> label="Net PnL" col="net_pnl" active={key} dir={dir} onClick={toggle} />
                <SortHeader<LeaderboardRow> label="Max DD" col="max_drawdown_pct" active={key} dir={dir} onClick={toggle} />
                <SortHeader<LeaderboardRow> label="Sharpe" col="sharpe_ratio" active={key} dir={dir} onClick={toggle} />
                <SortHeader<LeaderboardRow> label="Tier" col="tier" active={key} dir={dir} onClick={toggle} align="center" />
                <th className="py-3 px-3 text-center uppercase tracking-wider font-semibold">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {sorted.map((s) => (
                <tr key={s.id} onClick={() => onOpen({ strategyId: s.strategy_id, runId: s.run_id })} className="cursor-pointer transition hover:bg-slate-800/50" title="Open run details">
                  <td className="py-3 px-3 text-center font-mono font-bold text-slate-400">{s.rank}</td>
                  <td className="py-3 px-3">
                    <div className="font-semibold text-white">{s.name}</div>
                    <div className="text-[11px] text-slate-500 truncate max-w-[220px]" title={s.folder}>{s.folder || "—"}</div>
                  </td>
                  <td className="py-3 px-3"><EngineChip engine={s.engine} /></td>
                  <td className="py-3 px-3 text-center"><span className="font-mono text-slate-200">{s.symbol}</span> <span className="text-[10px] text-slate-400">({s.timeframe})</span></td>
                  <td className="py-3 px-3 text-right font-mono text-slate-300">{s.trades.toLocaleString()}</td>
                  <td className={`py-3 px-3 text-right font-mono ${s.win_rate_pct >= 50 ? "text-emerald-400" : "text-slate-400"}`}>{s.win_rate_pct.toFixed(1)}%</td>
                  <td className={`py-3 px-3 text-right font-mono font-bold ${pfClass(s.profit_factor)}`}>{s.profit_factor.toFixed(2)}</td>
                  <td className={`py-3 px-3 text-right font-mono font-bold ${s.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{signedMoney(s.net_pnl)}</td>
                  <td className={`py-3 px-3 text-right font-mono ${ddClass(s.max_drawdown_pct)}`}>{s.max_drawdown_pct.toFixed(2)}%</td>
                  <td className={`py-3 px-3 text-right font-mono font-bold ${sharpeClass(s.sharpe_ratio)}`}>{s.sharpe_ratio.toFixed(2)}</td>
                  <td className="py-3 px-3 text-center"><TierBadge tier={s.tier} /></td>
                  <td className="py-3 px-3 text-center"><SourceBadge source={s.source} tags={s.source === "legacy_manual" ? [] : s.strategy_id.startsWith("demo_") ? ["demo"] : []} /></td>
                </tr>
              ))}
              {sorted.length === 0 && (
                <tr><td colSpan={12} className="py-8 text-center text-slate-500">No strategies match the current filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      <p className="text-[11px] text-slate-500 px-1">
        One row per strategy (its primary run). Rank is by tier, then Sharpe. Click a row for the equity curve, Monte Carlo fan, diagnosis, assumptions and tear sheet. Tier 1 requires at least 30 trades; smaller samples cap at Tier 2 or &ldquo;insufficient data&rdquo;.
      </p>
    </div>
  );
}
