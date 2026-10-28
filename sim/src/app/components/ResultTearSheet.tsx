"use client";

import { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { X, TrendingUp, TrendingDown, Activity } from "lucide-react";
import type { analyzeResult } from "../../lib/results";
import { buildPnlHistogram } from "../../lib/results";
import EquityDrawdownChart from "./EquityDrawdownChart";
import TradeTable from "./TradeTable";
import NoisePanel from "./NoisePanel";

interface ResultTearSheetProps {
  strategyName: string;
  engine: string;
  symbol: string;
  timeframe: string;
  analysis: NonNullable<ReturnType<typeof analyzeResult>>;
  /** Catalog reference of the loaded result file; enables the noise diagnosis. */
  resultRef?: { file: string; source: string } | null;
  onClose: () => void;
}

const tooltipStyle = {
  backgroundColor: "#0f172a",
  borderColor: "#334155",
  borderRadius: "8px",
  fontSize: "11px",
} as const;

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" | "neutral" }) {
  const color =
    tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-rose-400" : "text-white";
  return (
    <div className="bg-slate-950/40 p-3 rounded-lg border border-slate-800/60">
      <span className="text-slate-500 block text-[10px] uppercase tracking-wider">{label}</span>
      <span className={`font-mono font-bold text-sm ${color}`}>{value}</span>
    </div>
  );
}

/**
 * Full tear sheet for one backtest result: every number and every series is
 * derived from the real result JSON (lib/results.ts). Nothing is synthesized.
 */
export default function ResultTearSheet({
  strategyName,
  engine,
  symbol,
  timeframe,
  analysis,
  resultRef,
  onClose,
}: ResultTearSheetProps) {
  const { result, equity, drawdown, monthly } = analysis;
  const m = result.metrics;

  const histogram = useMemo(() => buildPnlHistogram(result.trades), [result.trades]);
  const maxDdPct = useMemo(
    () => Math.max(...drawdown.map((d) => d.ddPct), 0),
    [drawdown]
  );

  const pnl = m.net_profit ?? 0;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm overflow-y-auto"
      onClick={onClose}
    >
      <div
        className="min-h-full w-full max-w-6xl mx-auto bg-slate-950 border-x border-slate-800 p-6 space-y-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="px-2 py-0.5 rounded text-[11px] font-mono text-cyan-400 bg-cyan-500/10 border border-cyan-500/20">
                {engine}
              </span>
              <span className="px-2 py-0.5 rounded text-[11px] font-mono font-bold text-white bg-slate-800">
                {symbol}
              </span>
              <span className="px-2 py-0.5 rounded text-[11px] font-mono text-amber-300 bg-slate-800">
                {timeframe}
              </span>
              {(result.metadata.start_date || result.metadata.end_date) && (
                <span className="text-[11px] font-mono text-slate-500">
                  {(result.metadata.start_date ?? "?").slice(0, 10)} →{" "}
                  {(result.metadata.end_date ?? "?").slice(0, 10)}
                </span>
              )}
            </div>
            <h2 className="text-2xl font-bold text-white tracking-tight">
              {strategyName} — Tear Sheet
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              {result.trades.length} real trades · engine-computed metrics + series rebuilt
              from raw trade data
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
            aria-label="Close tear sheet"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* KPI strip */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          <Kpi
            label="Net Profit"
            value={`$${pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
            tone={pnl >= 0 ? "pos" : "neg"}
          />
          <Kpi
            label="Return"
            value={`${(m.return_pct ?? 0).toFixed(2)}%`}
            tone={(m.return_pct ?? 0) >= 0 ? "pos" : "neg"}
          />
          <Kpi label="Sharpe" value={(m.sharpe_ratio ?? 0).toFixed(2)} />
          <Kpi label="Sortino" value={(m.sortino_ratio ?? 0).toFixed(2)} />
          <Kpi label="Profit Factor" value={(m.profit_factor ?? 0).toFixed(2)} />
          <Kpi
            label="Win Rate"
            value={`${(m.win_rate_pct ?? 0).toFixed(1)}%`}
            tone={(m.win_rate_pct ?? 0) >= 50 ? "pos" : "neg"}
          />
          <Kpi label="Max DD (recomputed)" value={`${maxDdPct.toFixed(2)}%`} tone="neg" />
          <Kpi
            label="Max DD (engine)"
            value={`${(m.max_drawdown_pct ?? 0).toFixed(2)}%`}
            tone="neg"
          />
          <Kpi label="Recovery Factor" value={(m.recovery_factor ?? 0).toFixed(2)} />
          <Kpi
            label="Expectancy / Trade"
            value={`$${(m.expectancy ?? 0).toFixed(2)}`}
            tone={(m.expectancy ?? 0) >= 0 ? "pos" : "neg"}
          />
        </div>

        {/* Equity + drawdown */}
        <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-1.5">
            <TrendingUp className="w-4 h-4 text-emerald-400" /> Equity Curve & Drawdown
          </h3>
          <EquityDrawdownChart equity={equity} drawdown={drawdown} height={380} />
        </div>

        {/* Monthly PnL */}
        <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-1.5">
            <Activity className="w-4 h-4 text-cyan-400" /> Monthly PnL
          </h3>
          <div className="h-48 w-full">
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <BarChart data={monthly} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="month" stroke="#475569" fontSize={10} tickLine={false} />
                <YAxis
                  stroke="#475569"
                  fontSize={10}
                  tickLine={false}
                  width={56}
                  tickFormatter={(v) => `$${(Number(v) / 1000).toFixed(0)}k`}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(val) => [
                    `$${Number(val).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}`,
                    "PnL",
                  ]}
                  labelFormatter={(lbl) => `Month: ${lbl}`}
                />
                <Bar dataKey="pnl" radius={[2, 2, 0, 0]}>
                  {monthly.map((b, i) => (
                    <Cell key={i} fill={b.pnl >= 0 ? "#10b981" : "#f43f5e"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* PnL histogram */}
        <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-1.5">
            <TrendingDown className="w-4 h-4 text-amber-400" /> Per-Trade PnL Distribution
          </h3>
          <div className="h-40 w-full">
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <BarChart data={histogram} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="range" stroke="#475569" fontSize={9} tickLine={false} interval={1} />
                <YAxis stroke="#475569" fontSize={10} tickLine={false} width={30} allowDecimals={false} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(val, name) => [Number(val), name === "count" ? "Trades" : name]}
                  labelFormatter={(lbl) => `PnL range: ${lbl}`}
                />
                <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                  {histogram.map((b, i) => (
                    <Cell key={i} fill={b.pnl >= 0 ? "#10b981" : "#f43f5e"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Noise (volatility) regime diagnosis */}
        {resultRef && <NoisePanel file={resultRef.file} source={resultRef.source} />}

        {/* Trade table */}
        <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white mb-3">Trade List</h3>
          <TradeTable trades={result.trades} />
        </div>

        <div className="pt-4 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-white rounded-lg transition"
          >
            Close Tear Sheet
          </button>
        </div>
      </div>
    </div>
  );
}
