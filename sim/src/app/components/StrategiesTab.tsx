"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { ddClass, engineFamily, pfClass, sharpeClass, shortDate, signedMoney } from "../lib/format";
import type { RegistryExport, RegistryRun, RunRef } from "../lib/types";
import { EngineChip, SourceBadge, TierBadge } from "./Badges";
import { SortHeader, useSort } from "./useSort";

type Mode = "grouped" | "flat";

export default function StrategiesTab({ registry, onOpen }: { registry: RegistryExport | null; onOpen: (r: RunRef) => void }) {
  const [q, setQ] = useState("");
  const [engine, setEngine] = useState("ALL");
  const [tier, setTier] = useState("ALL");
  const [symbol, setSymbol] = useState("ALL");
  const [tf, setTf] = useState("ALL");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [mode, setMode] = useState<Mode>("grouped");
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const allRuns = useMemo(() => registry?.strategies.flatMap((s) => s.runs) ?? [], [registry]);
  const symbols = useMemo(() => Array.from(new Set(allRuns.map((r) => r.symbol))).sort(), [allRuns]);
  const tfs = useMemo(() => Array.from(new Set(allRuns.map((r) => r.timeframe))).sort(), [allRuns]);

  const keep = (r: RegistryRun) => {
    const text = `${r.name} ${r.symbol} ${r.engine} ${r.folder} ${r.tags.join(" ")} ${r.run_id}`.toLowerCase();
    return (
      (!q || text.includes(q.toLowerCase())) &&
      (engine === "ALL" || engineFamily(r.engine) === engine) &&
      (tier === "ALL" || r.tier.startsWith(tier)) &&
      (symbol === "ALL" || r.symbol === symbol) &&
      (tf === "ALL" || r.timeframe === tf) &&
      (!verifiedOnly || r.source === "registry")
    );
  };

  const flat = useMemo(() => allRuns.filter(keep), [allRuns, q, engine, tier, symbol, tf, verifiedOnly]); // eslint-disable-line react-hooks/exhaustive-deps
  const { sorted, key, dir, toggle } = useSort<RegistryRun>(flat, "sharpe_ratio", "desc");

  const groups = useMemo(() => {
    if (!registry) return [];
    return registry.strategies
      .map((s) => ({ ...s, runs: s.runs.filter(keep) }))
      .filter((s) => s.runs.length > 0);
  }, [registry, q, engine, tier, symbol, tf, verifiedOnly]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleGroup = (id: string) => setOpen((o) => ({ ...o, [id]: !o[id] }));

  const Row = ({ r, indent = false }: { r: RegistryRun; indent?: boolean }) => (
    <tr onClick={() => onOpen({ strategyId: r.strategy_id, runId: r.run_id })} className={`cursor-pointer transition hover:bg-slate-800/50 ${r.primary && indent ? "bg-cyan-500/5" : ""}`} title="Open run details">
      <td className={`py-2.5 px-3 ${indent ? "pl-9" : ""}`}>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-slate-300">{r.run_id}</span>
          {r.primary && <span className="text-[9px] uppercase tracking-wider text-cyan-300 border border-cyan-500/30 rounded px-1">primary</span>}
        </div>
        {!indent && <div className="text-[11px] text-slate-500">{r.name}</div>}
        {r.start_date && <div className="text-[10px] text-slate-600 font-mono">{shortDate(r.start_date)} → {shortDate(r.end_date)}</div>}
      </td>
      <td className="py-2.5 px-3"><EngineChip engine={r.engine} /></td>
      <td className="py-2.5 px-3 text-center"><span className="font-mono text-slate-200">{r.symbol}</span> <span className="text-[10px] text-slate-400">({r.timeframe})</span></td>
      <td className="py-2.5 px-3 text-right font-mono text-slate-300">{r.trades.toLocaleString()}</td>
      <td className={`py-2.5 px-3 text-right font-mono ${r.win_rate_pct >= 50 ? "text-emerald-400" : "text-slate-400"}`}>{r.win_rate_pct.toFixed(1)}%</td>
      <td className={`py-2.5 px-3 text-right font-mono font-bold ${pfClass(r.profit_factor)}`}>{r.profit_factor.toFixed(2)}</td>
      <td className={`py-2.5 px-3 text-right font-mono font-bold ${r.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{signedMoney(r.net_pnl)}</td>
      <td className={`py-2.5 px-3 text-right font-mono ${ddClass(r.max_drawdown_pct)}`}>{r.max_drawdown_pct.toFixed(2)}%</td>
      <td className={`py-2.5 px-3 text-right font-mono font-bold ${sharpeClass(r.sharpe_ratio)}`}>{r.sharpe_ratio.toFixed(2)}</td>
      <td className="py-2.5 px-3 text-right font-mono text-slate-400">{r.monte_carlo_summary?.worst_drawdown_95_pct != null ? `${r.monte_carlo_summary.worst_drawdown_95_pct.toFixed(1)}%` : "–"}</td>
      <td className="py-2.5 px-3 text-center"><TierBadge tier={r.tier} /></td>
      <td className="py-2.5 px-3 text-center"><SourceBadge source={r.source} tags={r.tags} /></td>
    </tr>
  );

  const headers = (
    <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 text-[11px]">
      <SortHeader<RegistryRun> label={mode === "grouped" ? "Strategy / run" : "Run"} col="name" active={key} dir={dir} onClick={toggle} align="left" />
      <th className="py-3 px-3 uppercase tracking-wider font-semibold">Runtime</th>
      <SortHeader<RegistryRun> label="Asset / TF" col="symbol" active={key} dir={dir} onClick={toggle} align="center" />
      <SortHeader<RegistryRun> label="Trades" col="trades" active={key} dir={dir} onClick={toggle} />
      <SortHeader<RegistryRun> label="Win %" col="win_rate_pct" active={key} dir={dir} onClick={toggle} />
      <SortHeader<RegistryRun> label="PF" col="profit_factor" active={key} dir={dir} onClick={toggle} />
      <SortHeader<RegistryRun> label="Net PnL" col="net_pnl" active={key} dir={dir} onClick={toggle} />
      <SortHeader<RegistryRun> label="Max DD" col="max_drawdown_pct" active={key} dir={dir} onClick={toggle} />
      <SortHeader<RegistryRun> label="Sharpe" col="sharpe_ratio" active={key} dir={dir} onClick={toggle} />
      <th className="py-3 px-3 text-right uppercase tracking-wider font-semibold" title="Monte Carlo worst-case drawdown at 95% confidence">MC DD95</th>
      <SortHeader<RegistryRun> label="Tier" col="tier" active={key} dir={dir} onClick={toggle} align="center" />
      <th className="py-3 px-3 text-center uppercase tracking-wider font-semibold">Source</th>
    </tr>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-col lg:flex-row gap-2.5 items-stretch lg:items-center justify-between bg-slate-900/50 p-2.5 rounded-xl border border-slate-800/80">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, run id, symbol, folder, tag…" className="w-full bg-slate-950/80 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500 transition" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={engine} onChange={setEngine} options={[["ALL", "All runtimes"], ["PYTHON", "Python"], ["MT5", "MT5"], ["PINE", "Pine"], ["DERIV", "Deriv"], ["OTHER", "Other"]]} />
          <Select value={tier} onChange={setTier} options={[["ALL", "All tiers"], ["TIER 1", "Tier 1"], ["TIER 2", "Tier 2"], ["TIER 3", "Tier 3"], ["UNTESTED", "Untested"]]} />
          <Select value={symbol} onChange={setSymbol} options={[["ALL", "All symbols"], ...symbols.map((s) => [s, s] as [string, string])]} />
          <Select value={tf} onChange={setTf} options={[["ALL", "All timeframes"], ...tfs.map((s) => [s, s] as [string, string])]} />
          <label className="flex items-center gap-1.5 text-xs text-slate-400 whitespace-nowrap"><input type="checkbox" checked={verifiedOnly} onChange={(e) => setVerifiedOnly(e.target.checked)} className="accent-emerald-500" /> verified only</label>
          <div className="flex rounded-lg overflow-hidden border border-slate-800 text-xs">
            {(["grouped", "flat"] as Mode[]).map((m) => (
              <button key={m} onClick={() => setMode(m)} className={`px-2.5 py-1 ${mode === m ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-950 text-slate-400 hover:text-slate-200"}`}>{m === "grouped" ? "By strategy" : "All runs"}</button>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>{headers}</thead>
            <tbody className="divide-y divide-slate-800/50">
              {mode === "flat" && sorted.map((r) => <Row key={`${r.strategy_id}/${r.run_id}`} r={r} />)}
              {mode === "grouped" &&
                groups.map((g) => {
                  const primary = g.runs.find((r) => r.primary) ?? g.runs[0];
                  const expanded = open[g.strategy_id] ?? false;
                  return (
                    <GroupRows key={g.strategy_id} name={g.name} folder={g.folder} description={g.description} runs={g.runs} primary={primary} expanded={expanded} onToggle={() => toggleGroup(g.strategy_id)} onOpen={onOpen} Row={Row} />
                  );
                })}
              {((mode === "flat" && sorted.length === 0) || (mode === "grouped" && groups.length === 0)) && (
                <tr><td colSpan={12} className="py-8 text-center text-slate-500">{registry ? "No runs match the current filters." : "registry.json not found. Run `sajim export` from the repo root."}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      <p className="text-[11px] text-slate-500 px-1">
        Every run recorded in <code>results/registry.json</code>. A strategy tested on several symbols, timeframes or parameter sets shows one row per run; the primary run is the one the leaderboard uses. Record a run with <code>sajim_quant.registry.record_run(result)</code>, then <code>sajim export</code>.
      </p>
    </div>
  );
}

function GroupRows({ name, folder, description, runs, primary, expanded, onToggle, onOpen, Row }: {
  name: string; folder: string; description: string; runs: RegistryRun[]; primary: RegistryRun; expanded: boolean;
  onToggle: () => void; onOpen: (r: RunRef) => void; Row: (p: { r: RegistryRun; indent?: boolean }) => React.ReactElement;
}) {
  const best = Math.max(...runs.map((r) => r.sharpe_ratio));
  const verified = runs.filter((r) => r.source === "registry").length;
  return (
    <>
      <tr className="bg-slate-950/40 hover:bg-slate-800/40 transition">
        <td className="py-2.5 px-3" colSpan={2}>
          <button onClick={onToggle} className="flex items-start gap-2 text-left w-full">
            {expanded ? <ChevronDown className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" />}
            <span>
              <span className="font-semibold text-white">{name}</span>
              <span className="block text-[11px] text-slate-500 truncate max-w-[360px]" title={description || folder}>{folder || description || "—"}</span>
            </span>
          </button>
        </td>
        <td className="py-2.5 px-3 text-center text-[11px] text-slate-400">{runs.length} run{runs.length === 1 ? "" : "s"} · {verified} verified</td>
        <td className="py-2.5 px-3 text-right text-[11px] text-slate-500" colSpan={5}>best Sharpe <span className={`font-mono font-bold ${sharpeClass(best)}`}>{best.toFixed(2)}</span> · primary {primary.symbol} {primary.timeframe}</td>
        <td className="py-2.5 px-3 text-right">
          <button onClick={() => onOpen({ strategyId: primary.strategy_id, runId: primary.run_id })} className="text-[11px] text-cyan-300 hover:text-cyan-200 whitespace-nowrap">open primary →</button>
        </td>
        <td className="py-2.5 px-3 text-center"><TierBadge tier={primary.tier} /></td>
        <td className="py-2.5 px-3" colSpan={2}></td>
      </tr>
      {expanded && runs.map((r) => <Row key={r.run_id} r={r} indent />)}
    </>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-emerald-500">
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );
}
