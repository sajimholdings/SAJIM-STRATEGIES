"use client";

import { useMemo, useState } from "react";
import { Award, BarChart3, Filter, Search, Sparkles } from "lucide-react";
import { ddClass, edgeBadge, pfClass, sharpeClass, signedMoney } from "../lib/format";
import type { RunRef, UniverseExport } from "../lib/types";
import { EngineChip, SourceBadge, TierBadge } from "./Badges";

const ASSET_GROUPS: Record<string, string[]> = {
  "Forex Majors": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD"],
  Crosses: ["GBPJPY", "EURJPY", "EURGBP"],
  Metals: ["XAUUSD", "XAGUSD"],
  Crypto: ["BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD"],
  Indices: ["US30", "US500", "GER40", "UK100"],
};

function cellClass(sharpe: number | null): string {
  if (sharpe === null) return "bg-slate-900/40 text-slate-600 border border-dashed border-slate-800";
  if (sharpe >= 2.0) return "bg-emerald-500/20 text-emerald-300 font-bold border border-emerald-500/40";
  if (sharpe >= 1.0) return "bg-cyan-500/20 text-cyan-300 font-semibold border border-cyan-500/40";
  if (sharpe >= 0.0) return "bg-blue-500/10 text-blue-300 border border-blue-500/20";
  return "bg-rose-500/15 text-rose-300 border border-rose-500/30";
}

export default function UniverseTab({ universe, onOpen }: { universe: UniverseExport | null; onOpen: (r: RunRef) => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [asset, setAsset] = useState("ALL");
  const [tf, setTf] = useState("ALL");
  const [q, setQ] = useState("");
  const [testedOnly, setTestedOnly] = useState(true);

  const strat = useMemo(() => {
    if (!universe?.strategies.length) return null;
    return universe.strategies.find((s) => s.id === selected) ?? universe.strategies[0];
  }, [universe, selected]);

  const combos = useMemo(() => {
    if (!strat) return [];
    return strat.ranked_combinations.filter((c) => {
      const matchAsset = asset === "ALL" || (ASSET_GROUPS[asset] ?? []).includes(c.symbol);
      const matchTf = tf === "ALL" || c.timeframe === tf;
      const text = `${c.symbol} ${c.diagnosis?.headline ?? ""} ${c.diagnosis?.structural_cause ?? ""}`.toLowerCase();
      return matchAsset && matchTf && (!q || text.includes(q.toLowerCase()));
    });
  }, [strat, asset, tf, q]);

  if (!universe) {
    return <div className="p-8 text-center text-slate-500 text-xs">strategy_universe_matrix.json not found. Run <code>sajim export</code>.</div>;
  }
  const timeframes = universe.timeframes;
  const symbols = universe.symbols.filter((sym) => asset === "ALL" || (ASSET_GROUPS[asset] ?? universe.symbols).includes(sym));
  const visibleSymbols = testedOnly && strat ? symbols.filter((sym) => timeframes.some((t) => strat.heatmap[sym]?.[t] != null)) : symbols;
  const totalTested = universe.strategies.reduce((a, s) => a + s.total_combinations_tested, 0);

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-r from-purple-950/40 via-slate-900/80 to-slate-900 border border-purple-500/20 rounded-2xl p-5 shadow-2xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="px-2 py-0.5 rounded text-[11px] font-mono font-bold bg-purple-500/20 text-purple-300 border border-purple-500/40">{universe.symbols.length} SYMBOLS × {timeframes.length} TIMEFRAMES</span>
              <span className="text-xs text-slate-400">only cells with a recorded run are filled</span>
            </div>
            <h2 className="text-xl md:text-2xl font-bold text-white tracking-tight">Symbol × timeframe coverage per strategy</h2>
            <p className="text-xs text-slate-400 mt-1 max-w-3xl leading-relaxed">
              Each filled cell is the Sharpe ratio of the best recorded run for that symbol and timeframe. Grey dashed cells were never tested. Click a cell or a row to open the run (equity, Monte Carlo, diagnosis).
            </p>
          </div>
          <div className="text-right">
            <div className="text-xs text-slate-400 uppercase tracking-wider">Tested cells</div>
            <div className="text-2xl font-bold font-mono text-purple-400">{totalTested}</div>
            <div className="text-[10px] text-slate-500">of {universe.strategies.length * universe.symbols.length * timeframes.length} possible</div>
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2.5 flex items-center gap-1.5"><Sparkles className="w-3.5 h-3.5 text-purple-400" /> Strategy</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {universe.strategies.map((s) => {
            const isSel = s.id === strat?.id;
            return (
              <button key={s.id} onClick={() => setSelected(s.id)} className={`text-left rounded-xl p-3.5 transition-all border ${isSel ? "bg-purple-950/30 border-purple-500 ring-1 ring-purple-500/50" : "bg-slate-900/50 border-slate-800 hover:border-slate-700"}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <EngineChip engine={s.engine} />
                    <h3 className="text-sm font-bold text-white mt-1.5 truncate">{s.name}</h3>
                  </div>
                  <TierBadge tier={s.tier} />
                </div>
                <div className="mt-2.5 pt-2.5 border-t border-slate-800/80 grid grid-cols-3 gap-1 text-[10px] text-slate-500 uppercase">
                  <div>Tested<span className="block text-xs font-mono text-slate-200 normal-case">{s.total_combinations_tested} ({s.coverage_pct}%)</span></div>
                  <div>Profitable<span className="block text-xs font-mono text-slate-200 normal-case">{s.profitable_combinations} ({s.universality_pct}%)</span></div>
                  <div>Peak<span className="block text-xs font-mono text-emerald-400 normal-case">{s.peak_combination ? `${s.peak_combination.symbol} ${s.peak_combination.timeframe} · ${s.peak_combination.sharpe_ratio}` : "–"}</span></div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {strat && (
        <div className="space-y-4">
          <div className="flex flex-col lg:flex-row gap-3 items-stretch lg:items-center justify-between bg-slate-900/60 p-3.5 rounded-xl border border-slate-800">
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 lg:pb-0">
              <span className="text-xs font-semibold text-slate-400 mr-2 flex items-center gap-1 whitespace-nowrap"><Filter className="w-3.5 h-3.5" /> Asset class:</span>
              {["ALL", ...Object.keys(ASSET_GROUPS)].map((a) => (
                <button key={a} onClick={() => setAsset(a)} className={`px-3 py-1 rounded-lg text-xs font-medium transition whitespace-nowrap ${asset === a ? "bg-purple-500/20 text-purple-300 border border-purple-500/40" : "bg-slate-950 text-slate-400 border border-slate-800 hover:text-slate-200"}`}>{a === "ALL" ? "All assets" : a}</button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {["ALL", ...timeframes].map((t) => (
                <button key={t} onClick={() => setTf(t)} className={`px-2.5 py-1 rounded-lg text-xs font-medium transition ${tf === t ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40" : "bg-slate-950 text-slate-400 border border-slate-800 hover:text-slate-200"}`}>{t}</button>
              ))}
              <label className="flex items-center gap-1.5 text-xs text-slate-400 whitespace-nowrap"><input type="checkbox" checked={testedOnly} onChange={(e) => setTestedOnly(e.target.checked)} className="accent-purple-500" /> hide untested rows</label>
              <div className="relative min-w-[180px]">
                <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbol / rationale…" className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-8 pr-2.5 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-purple-500" />
              </div>
            </div>
          </div>

          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 shadow-2xl">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2"><BarChart3 className="w-4 h-4 text-purple-400" /> Sharpe heatmap · {strat.name}</h3>
              <div className="flex items-center gap-3 text-[11px] text-slate-400">
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-emerald-500 inline-block" /> ≥ 2.0</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-cyan-500 inline-block" /> ≥ 1.0</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-blue-500/60 inline-block" /> ≥ 0</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-rose-500 inline-block" /> negative</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-dashed border-slate-600 inline-block" /> not tested</span>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-center text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 font-semibold font-mono">
                    <th className="py-2.5 px-3 text-left">Symbol</th>
                    {timeframes.map((t) => <th key={t} className="py-2.5 px-3">{t}</th>)}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/40">
                  {visibleSymbols.map((sym) => (
                    <tr key={sym} className="hover:bg-slate-800/20 transition">
                      <td className="py-2 px-3 text-left font-mono font-bold text-white">{sym}</td>
                      {timeframes.map((t) => {
                        const sharpe = strat.heatmap[sym]?.[t] ?? null;
                        const node = strat.ranked_combinations.find((c) => c.symbol === sym && c.timeframe === t);
                        return (
                          <td key={t} className="py-1.5 px-2">
                            <button disabled={!node} onClick={() => node && onOpen({ strategyId: node.strategy_id, runId: node.run_id })} className={`w-full py-1 px-1.5 rounded text-xs font-mono transition-all ${node ? "hover:scale-105 cursor-pointer" : "cursor-default"} ${cellClass(sharpe)}`} title={node ? `${sym} ${t}: Sharpe ${sharpe} · ${node.trades} trades · ${node.tier}` : `${sym} ${t}: not tested`}>
                              {sharpe === null ? "·" : sharpe.toFixed(2)}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  {visibleSymbols.length === 0 && <tr><td colSpan={timeframes.length + 1} className="py-6 text-slate-500">No tested symbols for this filter.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
            <div className="p-4 border-b border-slate-800 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2"><Award className="w-4 h-4 text-amber-400" /> Ranked combinations ({combos.length})</h3>
              <span className="text-xs text-slate-400">ordered by tier, then Sharpe</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 font-semibold uppercase tracking-wider text-[11px]">
                    <th className="py-3 px-3 w-12 text-center">#</th>
                    <th className="py-3 px-3">Symbol / TF</th>
                    <th className="py-3 px-3 text-right">Trades</th>
                    <th className="py-3 px-3 text-right">Win %</th>
                    <th className="py-3 px-3 text-right">PF</th>
                    <th className="py-3 px-3 text-right">Net PnL</th>
                    <th className="py-3 px-3 text-right">Max DD</th>
                    <th className="py-3 px-3 text-right">Sharpe</th>
                    <th className="py-3 px-3">Why it behaves this way</th>
                    <th className="py-3 px-3 text-center">Edge</th>
                    <th className="py-3 px-3 text-center">Tier</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {combos.map((c) => (
                    <tr key={`${c.symbol}-${c.timeframe}`} onClick={() => onOpen({ strategyId: c.strategy_id, runId: c.run_id })} className={`transition hover:bg-slate-800/40 cursor-pointer ${c.optimal ? "bg-amber-500/5 border-l-2 border-l-amber-400" : ""}`}>
                      <td className="py-3 px-3 text-center font-mono font-bold text-slate-400">#{c.rank}</td>
                      <td className="py-3 px-3">
                        <div className="flex items-center gap-1.5"><span className="font-mono font-bold text-white">{c.symbol}</span><span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-amber-300">{c.timeframe}</span><SourceBadge source={c.source} /></div>
                        <div className="text-[10px] text-slate-500 mt-0.5">{c.asset_class}</div>
                      </td>
                      <td className="py-3 px-3 text-right font-mono text-slate-300">{c.trades}</td>
                      <td className={`py-3 px-3 text-right font-mono ${c.win_rate_pct >= 50 ? "text-emerald-400" : "text-slate-400"}`}>{c.win_rate_pct.toFixed(1)}%</td>
                      <td className={`py-3 px-3 text-right font-mono font-bold ${pfClass(c.profit_factor)}`}>{c.profit_factor.toFixed(2)}</td>
                      <td className={`py-3 px-3 text-right font-mono font-bold ${c.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{signedMoney(c.net_pnl)}</td>
                      <td className={`py-3 px-3 text-right font-mono ${ddClass(c.max_drawdown_pct)}`}>{c.max_drawdown_pct.toFixed(2)}%</td>
                      <td className={`py-3 px-3 text-right font-mono font-bold ${sharpeClass(c.sharpe_ratio)}`}>{c.sharpe_ratio.toFixed(2)}</td>
                      <td className="py-3 px-3 max-w-sm">
                        {c.diagnosis ? (
                          <>
                            <div className="text-xs font-semibold text-purple-300 font-mono">&ldquo;{c.diagnosis.headline}&rdquo;</div>
                            <div className="text-[11px] text-slate-400 mt-0.5 line-clamp-2 leading-relaxed">{c.diagnosis.structural_cause}</div>
                          </>
                        ) : (
                          <span className="text-[11px] text-slate-600">no diagnosis (legacy row)</span>
                        )}
                      </td>
                      <td className="py-3 px-3 text-center">
                        {c.diagnosis ? <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold border ${edgeBadge[c.diagnosis.edge_rating] || "bg-slate-800 text-slate-400 border-slate-700"}`}>{c.diagnosis.edge_rating.replace("_", " ")}</span> : <span className="text-slate-600">–</span>}
                      </td>
                      <td className="py-3 px-3 text-center"><TierBadge tier={c.tier} /></td>
                    </tr>
                  ))}
                  {combos.length === 0 && <tr><td colSpan={11} className="py-8 text-center text-slate-500">No tested combinations match the filters.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
