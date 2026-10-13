"use client";

import { Activity, Award, Clock, Layers, ListTree, RefreshCw, Sparkles } from "lucide-react";
import type { LeaderboardRow, RegistryExport } from "../lib/types";

export type Tab = "leaderboard" | "strategies" | "universe" | "correlations";

const TABS: { id: Tab; label: string; icon: React.ReactNode; color: string }[] = [
  { id: "leaderboard", label: "Leaderboard", icon: <Award className="w-4 h-4" />, color: "border-cyan-400 text-cyan-400 bg-cyan-500/5" },
  { id: "strategies", label: "Strategies", icon: <ListTree className="w-4 h-4" />, color: "border-emerald-400 text-emerald-400 bg-emerald-500/5" },
  { id: "universe", label: "Universe grid", icon: <Sparkles className="w-4 h-4" />, color: "border-purple-400 text-purple-400 bg-purple-500/10" },
  { id: "correlations", label: "Correlation", icon: <Activity className="w-4 h-4" />, color: "border-blue-400 text-blue-400 bg-blue-500/5" },
];

export default function Header({
  activeTab, onTab, onReload, reloading, leaderboard, registry, lastUpdated,
}: {
  activeTab: Tab; onTab: (t: Tab) => void; onReload: () => void; reloading: boolean;
  leaderboard: LeaderboardRow[]; registry: RegistryExport | null; lastUpdated: string | null;
}) {
  const verified = leaderboard.filter((s) => s.has_artifacts).length;
  const top = leaderboard.length ? leaderboard.reduce((a, b) => (b.sharpe_ratio > a.sharpe_ratio ? b : a)) : null;
  const viable = leaderboard.filter((s) => s.tier.startsWith("TIER 1") || s.tier === "TIER 2: VIABLE").length;
  const totalTrades = leaderboard.reduce((a, b) => a + b.trades, 0);
  const counts: Record<Tab, number> = {
    leaderboard: leaderboard.length,
    strategies: registry?.total_runs ?? 0,
    universe: registry?.total_strategies ?? 0,
    correlations: 0,
  };

  return (
    <header className="mb-6 border-b border-slate-800/80 pb-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
              SAJIM STRATEGY BENCHMARK
            </span>
            <span className="text-xs text-slate-500">Universal Trade Schema v1 · Results Registry</span>
          </div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
            Universal Quant Benchmark & Leaderboard
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Every strategy run (Python, MT5 headless, Pine import, Deriv) is normalized at the results layer and recorded in <code className="text-slate-300">results/</code>. Nothing here is simulated.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {lastUpdated && (
            <span className="flex items-center gap-1"><Clock className="w-3.5 h-3.5" /> exported {lastUpdated.replace("T", " ").slice(0, 16)}</span>
          )}
          <button
            onClick={onReload}
            disabled={reloading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-slate-900 border border-slate-800 rounded-lg hover:bg-slate-800 transition text-slate-300 disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${reloading ? "animate-spin" : ""}`} /> Reload data
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-6 border-b border-slate-800 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => onTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition whitespace-nowrap ${
              activeTab === t.id ? t.color : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.icon} {t.label}{counts[t.id] ? ` (${counts[t.id]})` : ""}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 md:gap-4 mt-6">
        <Kpi label="Strategies" icon={<Layers className="w-4 h-4 text-cyan-400" />} value={`${leaderboard.length}`} sub={`${registry?.total_runs ?? 0} runs · ${totalTrades.toLocaleString()} trades`} />
        <Kpi label="Top Sharpe" icon={<Award className="w-4 h-4 text-emerald-400" />} value={top ? top.sharpe_ratio.toFixed(2) : "–"} valueClass="text-emerald-400" sub={top ? `${top.name} (${top.symbol} ${top.timeframe})` : "no runs recorded"} />
        <Kpi label="Viable" icon={<Sparkles className="w-4 h-4 text-blue-400" />} value={`${viable} of ${leaderboard.length}`} sub="Tier 1 Elite or Tier 2 Viable" />
        <Kpi label="With artifacts" icon={<ListTree className="w-4 h-4 text-amber-400" />} value={`${verified}`} valueClass="text-amber-400" sub={`${leaderboard.length - verified} legacy rows without trade lists`} />
      </div>
    </header>
  );
}

function Kpi({ label, icon, value, sub, valueClass = "text-white" }: { label: string; icon: React.ReactNode; value: string; sub: string; valueClass?: string }) {
  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-4">
      <div className="flex items-center justify-between text-slate-400 mb-1">
        <span className="text-xs font-medium uppercase tracking-wider">{label}</span>
        {icon}
      </div>
      <div className={`text-2xl font-bold ${valueClass}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1 truncate" title={sub}>{sub}</div>
    </div>
  );
}
