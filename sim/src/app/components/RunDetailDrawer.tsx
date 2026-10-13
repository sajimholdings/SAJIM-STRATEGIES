"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Download, FileText, ShieldAlert, Sparkles, TrendingUp, X } from "lucide-react";
import { fetchRunDetail, tradesCsvUrl } from "../lib/api";
import { edgeBadge, money, num, pct, shortDate, signedMoney } from "../lib/format";
import type { RunDetail, RunRef } from "../lib/types";
import { EngineChip, SourceBadge, TierBadge } from "./Badges";
import EquityChart from "./EquityChart";
import MonteCarloFan from "./MonteCarloFan";

export default function RunDetailDrawer({ target, onClose }: { target: RunRef; onClose: () => void }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<"overview" | "montecarlo" | "tearsheet">("overview");

  // The parent remounts this drawer (key = strategy/run) so state starts fresh per target.
  useEffect(() => {
    let alive = true;
    fetchRunDetail(target.strategyId, target.runId)
      .then((d) => alive && setDetail(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [target.strategyId, target.runId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const run = detail?.run;
  const m = detail?.metrics ?? null;
  const mc = detail?.monte_carlo ?? null;
  const diag = detail?.diagnosis ?? null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/75 backdrop-blur-sm" onClick={onClose} role="dialog" aria-modal="true">
      <div className="w-full max-w-3xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between pb-4 border-b border-slate-800">
          <div className="min-w-0">
            {run ? (
              <>
                <div className="flex flex-wrap items-center gap-2 mb-1.5">
                  <EngineChip engine={run.engine} />
                  <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-slate-800 text-cyan-300">{run.symbol}</span>
                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-amber-300">{run.timeframe}</span>
                  <TierBadge tier={run.tier} full />
                  <SourceBadge source={run.source} tags={run.tags} />
                </div>
                <h2 className="text-xl font-bold text-white tracking-tight truncate">{run.name}</h2>
                <div className="text-[11px] text-slate-500 font-mono mt-0.5 truncate">
                  {run.strategy_id} / {run.run_id}
                  {run.start_date && <> · {shortDate(run.start_date)} → {shortDate(run.end_date)}</>}
                  {run.folder && <> · {run.folder}</>}
                </div>
              </>
            ) : (
              <h2 className="text-xl font-bold text-white">Loading run…</h2>
            )}
          </div>
          <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && <div className="mt-4 p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs">{error}</div>}

        {run && (
          <>
            <div className="flex items-center gap-2 mt-4 border-b border-slate-800">
              {(["overview", "montecarlo", "tearsheet"] as const).map((s) => (
                <button key={s} onClick={() => setSection(s)} className={`px-3 py-2 text-xs font-semibold border-b-2 transition ${section === s ? "border-cyan-400 text-cyan-300" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
                  {s === "overview" ? "Overview" : s === "montecarlo" ? "Monte Carlo" : "Tear sheet"}
                </button>
              ))}
              {run.artifacts["trades.csv"] && (
                <a href={tradesCsvUrl(run.strategy_id, run.run_id)} className="ml-auto flex items-center gap-1 px-3 py-1.5 text-xs text-slate-300 hover:text-white" download>
                  <Download className="w-3.5 h-3.5" /> trades.csv
                </a>
              )}
            </div>

            {run.description && <p className="text-xs text-slate-400 mt-4 leading-relaxed">{run.description}</p>}

            {section === "overview" && (
              <div className="space-y-5 mt-4">
                <div className="border border-slate-800/80 rounded-xl p-3 bg-slate-950/60">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5"><TrendingUp className="w-3.5 h-3.5 text-cyan-400" /> Equity curve (realized, trade by trade)</span>
                    <span className={`text-xs font-mono font-bold ${run.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{signedMoney(run.net_pnl)}</span>
                  </div>
                  {m && m.equity_series.length > 1 ? (
                    <EquityChart series={m.equity_series} positive={run.net_pnl >= 0} height={190} />
                  ) : (
                    <NoArtifacts source={run.source} what="an equity curve" />
                  )}
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                  <Stat label="Trades" value={run.trades.toLocaleString()} />
                  <Stat label="Win rate" value={pct(run.win_rate_pct)} />
                  <Stat label="Profit factor" value={num(run.profit_factor)} />
                  <Stat label="Sharpe" value={num(run.sharpe_ratio)} />
                  <Stat label="Sortino" value={num(run.sortino_ratio)} />
                  <Stat label="Max drawdown" value={pct(run.max_drawdown_pct, 2)} valueClass="text-rose-400" />
                  <Stat label="Return" value={pct(run.return_pct)} />
                  <Stat label="Start balance" value={run.initial_balance ? money(run.initial_balance, 0) : "–"} />
                  {m && (
                    <>
                      <Stat label="CAGR" value={pct(m.cagr_pct)} />
                      <Stat label="Expectancy / trade" value={money(m.expectancy)} />
                      <Stat label="Avg win / loss" value={`${money(m.avg_win, 0)} / ${money(m.avg_loss, 0)}`} />
                      <Stat label="Max streak W / L" value={`${m.max_consecutive_wins} / ${m.max_consecutive_losses}`} />
                      <Stat label="Calmar" value={num(m.calmar_ratio)} />
                      <Stat label="Recovery factor" value={num(m.recovery_factor)} />
                      <Stat label="Avg duration" value={`${Math.round(m.avg_duration_minutes)} min`} />
                      <Stat label="Fees paid" value={money(m.total_commission + m.total_swap)} />
                    </>
                  )}
                </div>

                {diag && (
                  <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-semibold text-white flex items-center gap-2"><Sparkles className="w-4 h-4 text-purple-400" /> Why it behaves this way</h3>
                      <span className={`text-[11px] font-bold px-2 py-0.5 rounded border uppercase tracking-wider ${edgeBadge[diag.edge_rating] || "bg-slate-800 text-slate-400 border-slate-700"}`}>{diag.edge_rating.replace("_", " ")}</span>
                    </div>
                    <div className="text-sm font-medium text-purple-300 font-mono">&ldquo;{diag.headline}&rdquo;</div>
                    <p className="text-xs text-slate-300"><strong className="text-slate-200">Structural cause: </strong>{diag.structural_cause}</p>
                    <p className="text-xs text-slate-300"><strong className="text-slate-200">Mechanics: </strong>{diag.mechanics_detail}</p>
                    <p className="p-2.5 rounded-lg bg-slate-950/80 border border-slate-800/80 text-amber-200/90 text-xs"><strong className="text-amber-300">Recommendation: </strong>{diag.recommendation}</p>
                  </div>
                )}

                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4">
                  <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">Run assumptions</h3>
                  <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-[11px]">
                    {Object.entries(run.assumptions).filter(([, v]) => v !== null && v !== "").map(([k, v]) => (
                      <div key={k} className="flex flex-col">
                        <dt className="text-slate-500">{k.replace(/_/g, " ")}</dt>
                        <dd className="font-mono text-slate-200 break-words">{String(v)}</dd>
                      </div>
                    ))}
                    {run.tags.length > 0 && (
                      <div className="flex flex-col"><dt className="text-slate-500">tags</dt><dd className="font-mono text-slate-200">{run.tags.join(", ")}</dd></div>
                    )}
                    <div className="flex flex-col"><dt className="text-slate-500">recorded</dt><dd className="font-mono text-slate-200">{run.created_at.replace("T", " ").slice(0, 16)}</dd></div>
                  </dl>
                </div>
              </div>
            )}

            {section === "montecarlo" && (
              <div className="space-y-4 mt-4">
                {mc ? (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                      <Stat label="Historical DD" value={pct(mc.historical_max_dd_pct, 2)} sub="observed in sample" />
                      <Stat label="Worst DD (95%)" value={pct(mc.worst_drawdown_95_pct, 2)} valueClass={mc.worst_drawdown_95_pct > 20 ? "text-rose-400" : mc.worst_drawdown_95_pct > 10 ? "text-amber-400" : "text-emerald-400"} sub={`99%: ${pct(mc.worst_drawdown_99_pct, 2)} · median ${pct(mc.expected_max_dd_median_pct, 2)}`} />
                      <Stat label="Risk of ruin (>20% DD)" value={pct(mc.risk_of_ruin_20_pct)} valueClass={mc.risk_of_ruin_20_pct > 10 ? "text-rose-400" : mc.risk_of_ruin_20_pct > 0 ? "text-amber-400" : "text-emerald-400"} sub={`>10%: ${pct(mc.risk_of_ruin_10_pct)} · >30%: ${pct(mc.risk_of_ruin_30_pct)} · >50%: ${pct(mc.risk_of_ruin_50_pct)}`} />
                      <Stat label="Fragility index" value={num(mc.fragility_index)} valueClass={mc.is_robust ? "text-emerald-400" : "text-amber-400"} sub={mc.is_robust ? "robust edge" : "curve-fit alert (hist. Sharpe ≫ MC median)"} />
                    </div>
                    <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4">
                      <h3 className="text-sm font-semibold text-white flex items-center gap-1.5 mb-1"><TrendingUp className="w-4 h-4 text-cyan-400" /> Probabilistic equity fan</h3>
                      <p className="text-xs text-slate-400 mb-3">Trades resampled with replacement {mc.num_simulations.toLocaleString()} times. Bands are percentiles of the simulated equity at each trade step.</p>
                      <MonteCarloFan mc={mc} />
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <Stat label="Terminal P95" value={money(mc.terminal_p95_balance, 0)} valueClass="text-emerald-400" />
                      <Stat label="Terminal median" value={money(mc.terminal_median_balance, 0)} valueClass="text-cyan-400" />
                      <Stat label="Terminal P5" value={money(mc.terminal_p5_balance, 0)} valueClass="text-rose-400" />
                    </div>
                  </>
                ) : (
                  <NoArtifacts source={run.source} what="a Monte Carlo simulation" />
                )}
              </div>
            )}

            {section === "tearsheet" && (
              <div className="mt-4">
                {detail?.tear_sheet_md ? (
                  <article className="prose prose-invert prose-sm max-w-none prose-table:text-[11px] prose-th:px-2 prose-td:px-2 prose-headings:text-slate-100">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.tear_sheet_md}</ReactMarkdown>
                  </article>
                ) : (
                  <NoArtifacts source={run.source} what="a tear sheet" />
                )}
              </div>
            )}
          </>
        )}

        <div className="pt-4 mt-6 border-t border-slate-800 flex justify-end">
          <button onClick={onClose} className="px-4 py-2 text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-white rounded-lg transition">Close</button>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, valueClass = "text-white" }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="bg-slate-900/70 border border-slate-800/80 rounded-xl p-3">
      <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider">{label}</div>
      <div className={`text-base font-bold font-mono mt-0.5 ${valueClass}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function NoArtifacts({ source, what }: { source: string; what: string }) {
  return (
    <div className="flex items-start gap-2 p-3 rounded-lg bg-slate-900/60 border border-slate-800 text-xs text-slate-400">
      {source === "legacy_manual" ? <FileText className="w-4 h-4 text-slate-500 shrink-0" /> : <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0" />}
      <span>
        {source === "legacy_manual"
          ? `This row was transcribed from the old hand-written leaderboard. It has no trade list, so ${what} cannot be produced. Re-run the strategy through an adapter with record_run() to get full artifacts.`
          : `No artifact found for ${what}. The run folder may have been deleted; re-record the run.`}
      </span>
    </div>
  );
}
