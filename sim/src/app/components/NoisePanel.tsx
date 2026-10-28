"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from "recharts";
import { CloudLightning, Loader2, RefreshCw, AlertTriangle } from "lucide-react";

/** Shape of noise_summary written by sajim_quant.noise.diagnose. */
type BucketStats = {
  trades: number;
  win_rate_pct: number;
  net_pnl: number;
  avg_net_pnl: number;
  profit_factor: number | null;
  avg_move_sigmas?: number;
  small_sample?: boolean;
};

type NoiseSummary = {
  strategy: string;
  symbol: string;
  noise_data_symbol: string;
  timeframe: string;
  trades_total: number;
  trades_diagnosed: number;
  trades_skipped: Record<string, number>;
  noise_data_range: [string, string];
  overall: Partial<BucketStats>;
  by_regime: Record<string, BucketStats>;
  by_session: Record<string, BucketStats>;
  by_recent_noise: Record<string, BucketStats>;
  pnl_vs_vol_pctile_spearman: number | null;
  findings: string[];
  note: string;
};

const tooltipStyle = {
  backgroundColor: "#0f172a",
  borderColor: "#334155",
  borderRadius: "8px",
  fontSize: "11px",
} as const;

const GROUPS: { key: "by_regime" | "by_session" | "by_recent_noise"; title: string; hint: string }[] = [
  { key: "by_regime", title: "Volatility regime", hint: "Day's forecast vol vs its past year" },
  { key: "by_session", title: "Session", hint: "Hour-of-day share of daily noise" },
  { key: "by_recent_noise", title: "Hour before entry", hint: "Realized vs expected noise" },
];

const fmtMoney = (v: number) =>
  `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

function BreakdownCard({ title, hint, buckets }: { title: string; hint: string; buckets: Record<string, BucketStats> }) {
  const rows = Object.entries(buckets).map(([name, s]) => ({ name: name.replace(/_/g, " "), ...s }));
  if (!rows.length) return null;
  return (
    <div className="bg-slate-950/40 border border-slate-800/60 rounded-lg p-3 space-y-2 min-w-0">
      <div>
        <h4 className="text-xs font-semibold text-white">{title}</h4>
        <p className="text-[10px] text-slate-500">{hint} · bars = avg net P&L / trade</p>
      </div>
      <div className="h-28 w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart data={rows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="name" stroke="#475569" fontSize={9} tickLine={false} />
            <YAxis stroke="#475569" fontSize={9} tickLine={false} width={44} />
            <ReferenceLine y={0} stroke="#475569" />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(val) => [fmtMoney(Number(val)), "Avg P&L / trade"]}
            />
            <Bar dataKey="avg_net_pnl" radius={[2, 2, 0, 0]}>
              {rows.map((r, i) => (
                <Cell
                  key={i}
                  fill={r.avg_net_pnl >= 0 ? "#10b981" : "#f43f5e"}
                  fillOpacity={r.small_sample ? 0.4 : 1}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <table className="w-full text-[10px] font-mono">
        <thead>
          <tr className="text-slate-500 text-right">
            <th className="text-left font-normal">bucket</th>
            <th className="font-normal">n</th>
            <th className="font-normal">win%</th>
            <th className="font-normal">net</th>
            <th className="font-normal">PF</th>
            <th className="font-normal" title="Average move over the hold, in expected-noise sigmas">σ move</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="text-right text-slate-300">
              <td className="text-left">
                {r.name}
                {r.small_sample && (
                  <span className="text-amber-400" title="Fewer than 30 trades"> ⚠</span>
                )}
              </td>
              <td>{r.trades}</td>
              <td>{r.win_rate_pct.toFixed(1)}</td>
              <td className={r.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>{fmtMoney(r.net_pnl)}</td>
              <td>{r.profit_factor ?? "—"}</td>
              <td>{r.avg_move_sigmas ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Noise (volatility) diagnosis of one backtest: walk-forward HAR-RV on the
 * symbol's M5 data, every trade tagged causally at entry. Loads the cached
 * summary, or runs the diagnosis on first open.
 */
export default function NoisePanel({ file, source }: { file: string; source: string }) {
  const [summary, setSummary] = useState<NoiseSummary | null>(null);
  const [state, setState] = useState<"loading" | "running" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setState("running");
    setError(null);
    try {
      const res = await fetch("/api/noise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file, source }),
      });
      const json = await res.json();
      if (!json.success && json.job_id && res.status === 504) {
        // Still running on Modal: poll the job (GET ?job= saves it locally when done), then reload.
        const deadline = Date.now() + 15 * 60_000;
        for (;;) {
          if (Date.now() > deadline) throw new Error(`Modal job ${json.job_id} is still running; try again later.`);
          await new Promise((r) => setTimeout(r, 5_000));
          let j: { success?: boolean; status?: string; error?: string } = {};
          try {
            j = await (await fetch(`/api/run-test?job=${encodeURIComponent(json.job_id)}`, { cache: "no-store" })).json();
          } catch {
            continue; // network blip — keep polling
          }
          if (j.status === "done" && j.success) break;
          if (j.status === "failed" || j.status === "expired") throw new Error(j.error || `Modal job ${j.status}`);
        }
        const cached = await (await fetch(
          `/api/noise?file=${encodeURIComponent(file)}&source=${encodeURIComponent(source)}`,
          { cache: "no-store" }
        )).json();
        if (!cached.success || !cached.summary) throw new Error(cached.error || "Diagnosis finished but no summary was saved");
        setSummary(cached.summary);
        setState("ready");
        return;
      }
      if (!json.success) throw new Error(json.error || `HTTP ${res.status}`);
      setSummary(json.summary);
      setState("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, [file, source]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setSummary(null);
      setState("loading");
      try {
        const res = await fetch(
          `/api/noise?file=${encodeURIComponent(file)}&source=${encodeURIComponent(source)}`,
          { cache: "no-store" }
        );
        const json = await res.json();
        if (cancelled) return;
        if (json.success && json.cached) {
          setSummary(json.summary);
          setState("ready");
        } else if (json.success) {
          run();
        } else {
          setError(json.error);
          setState("error");
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setState("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [file, source, run]);

  const o = summary?.overall;

  return (
    <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
            <CloudLightning className="w-4 h-4 text-violet-400" /> Noise Regime Diagnosis
          </h3>
          {summary && (
            <p className="text-[11px] text-slate-500 mt-0.5">
              Walk-forward HAR-RV on {summary.noise_data_symbol} M5 (
              {summary.noise_data_range[0].slice(0, 10)} → {summary.noise_data_range[1].slice(0, 10)}) ·{" "}
              {summary.trades_diagnosed} / {summary.trades_total} trades diagnosed
              {summary.pnl_vs_vol_pctile_spearman !== null &&
                ` · P&L vs vol-pctile Spearman ${summary.pnl_vs_vol_pctile_spearman}`}
            </p>
          )}
        </div>
        {(state === "ready" || state === "error") && (
          <button
            onClick={run}
            className="flex items-center gap-1 px-2 py-1 text-[11px] text-slate-400 hover:text-white rounded border border-slate-700 hover:bg-slate-800 transition shrink-0"
          >
            <RefreshCw className="w-3 h-3" /> Re-run
          </button>
        )}
      </div>

      {(state === "loading" || state === "running") && (
        <div className="flex items-center gap-2 text-xs text-slate-400 py-6 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" />
          {state === "running" ? "Fitting noise model and tagging trades…" : "Loading…"}
        </div>
      )}

      {state === "error" && (
        <div className="flex items-start gap-2 text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-lg p-3">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span className="whitespace-pre-wrap font-mono">{error}</span>
        </div>
      )}

      {state === "ready" && summary && (
        <>
          {o && o.trades ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
              {[
                ["Diagnosed P&L", fmtMoney(o.net_pnl ?? 0), (o.net_pnl ?? 0) >= 0],
                ["Win rate", `${(o.win_rate_pct ?? 0).toFixed(1)}%`, null],
                ["Profit factor", String(o.profit_factor ?? "—"), (o.profit_factor ?? 0) >= 1],
                ["Avg σ move / trade", String(o.avg_move_sigmas ?? "—"), (o.avg_move_sigmas ?? 0) > 0],
              ].map(([label, value, good]) => (
                <div key={label as string} className="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/60">
                  <span className="text-slate-500 block text-[10px] uppercase tracking-wider">{label}</span>
                  <span
                    className={`font-mono font-bold text-sm ${
                      good === null ? "text-white" : good ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {value}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          {summary.findings.length > 0 && (
            <ul className="space-y-1 text-xs text-slate-300 list-disc pl-4">
              {summary.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            {GROUPS.map((g) => (
              <BreakdownCard key={g.key} title={g.title} hint={g.hint} buckets={summary[g.key]} />
            ))}
          </div>

          <p className="text-[10px] italic text-slate-500">{summary.note}</p>
        </>
      )}
    </div>
  );
}
