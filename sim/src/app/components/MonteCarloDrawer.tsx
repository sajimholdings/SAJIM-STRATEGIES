"use client";

import React from "react";
import {
  X,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  AlertTriangle,
  CheckCircle2,
  Info,
  Clock,
  Coins,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

interface MonteCarloDrawerProps {
  node: any;
  strategyName: string;
  onClose: () => void;
}

export default function MonteCarloDrawer({
  node,
  strategyName,
  onClose,
}: MonteCarloDrawerProps) {
  if (!node) return null;

  const mc = node.monte_carlo;
  const diag = node.diagnosis;

  // Prepare data for the Monte Carlo Fan Chart
  const fanChartData = (mc.trade_steps || []).map((step: number, idx: number) => ({
    step: `T+${step}`,
    tradeNum: step,
    p5: mc.p5_curve ? mc.p5_curve[idx] : 10000,
    p25: mc.p25_curve ? mc.p25_curve[idx] : 10000,
    p50: mc.p50_curve ? mc.p50_curve[idx] : 10000,
    p75: mc.p75_curve ? mc.p75_curve[idx] : 10000,
    p95: mc.p95_curve ? mc.p95_curve[idx] : 10000,
  }));

  const edgeBadgeStyles: Record<string, string> = {
    ALPHA_LEADER: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
    SOLID_VIABLE: "bg-cyan-500/10 text-cyan-400 border-cyan-500/30",
    CHOP_VULNERABLE: "bg-amber-500/10 text-amber-400 border-amber-500/30",
    TOXIC_FAILURE: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  };

  const badgeClass =
    edgeBadgeStyles[diag?.edge_rating] ||
    "bg-slate-800 text-slate-400 border-slate-700";

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/75 backdrop-blur-sm transition-opacity animate-in fade-in duration-200">
      <div
        className="w-full max-w-2xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 flex flex-col justify-between shadow-2xl animate-in slide-in-from-right duration-300"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="space-y-6">
          {/* Header */}
          <div className="flex items-start justify-between pb-4 border-b border-slate-800">
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-xs font-mono font-semibold px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/30">
                  {strategyName}
                </span>
                <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-slate-800 text-cyan-300">
                  {node.symbol}
                </span>
                <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-amber-300">
                  {node.timeframe}
                </span>
              </div>
              <h2 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
                Monte Carlo Stress Analysis & Telemetry
              </h2>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
              aria-label="Close drawer"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Behavioral Diagnostic Card (Why it behaves this way) */}
          <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-purple-400" />
                <h3 className="text-sm font-semibold text-white">
                  Why It Behaves This Way
                </h3>
              </div>
              <span
                className={`text-[11px] font-bold px-2 py-0.5 rounded border uppercase tracking-wider ${badgeClass}`}
              >
                {diag?.edge_rating?.replace("_", " ") || "ANALYZED"}
              </span>
            </div>

            <div className="text-sm font-medium text-purple-300 font-mono">
              "{diag?.headline || "Systematic Edge Analysis"}"
            </div>

            <div className="text-xs text-slate-300 leading-relaxed space-y-2">
              <p>
                <strong className="text-slate-200">Structural Cause: </strong>
                {diag?.structural_cause}
              </p>
              <p>
                <strong className="text-slate-200">Execution Mechanics: </strong>
                {diag?.mechanics_detail}
              </p>
              <p className="p-2.5 rounded-lg bg-slate-950/80 border border-slate-800/80 text-amber-200/90 text-xs">
                <strong className="text-amber-300">Tactical Recommendation: </strong>
                {diag?.recommendation}
              </p>
            </div>
          </div>

          {/* Monte Carlo Risk & Stress KPIs */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
            <div className="bg-slate-900/70 border border-slate-800/80 rounded-xl p-3">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Historical DD
              </div>
              <div className="text-lg font-bold text-white mt-0.5">
                {node.max_drawdown_pct}%
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">Observed in sample</div>
            </div>

            <div className="bg-slate-900/70 border border-slate-800/80 rounded-xl p-3">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Worst-Case DD (95%)
              </div>
              <div
                className={`text-lg font-bold mt-0.5 ${
                  mc.worst_drawdown_95_pct > 20
                    ? "text-rose-400"
                    : mc.worst_drawdown_95_pct > 10
                    ? "text-amber-400"
                    : "text-emerald-400"
                }`}
              >
                {mc.worst_drawdown_95_pct}%
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                99% Bound: {mc.worst_drawdown_99_pct}%
              </div>
            </div>

            <div className="bg-slate-900/70 border border-slate-800/80 rounded-xl p-3">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Risk of Ruin (&gt;20% DD)
              </div>
              <div
                className={`text-lg font-bold mt-0.5 ${
                  mc.risk_of_ruin_20_pct > 10
                    ? "text-rose-400"
                    : mc.risk_of_ruin_20_pct > 0
                    ? "text-amber-400"
                    : "text-emerald-400"
                }`}
              >
                {mc.risk_of_ruin_20_pct}%
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                &gt;10% DD: {mc.risk_of_ruin_10_pct}%
              </div>
            </div>

            <div className="bg-slate-900/70 border border-slate-800/80 rounded-xl p-3">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Fragility Index
              </div>
              <div
                className={`text-lg font-bold mt-0.5 ${
                  mc.is_robust ? "text-emerald-400" : "text-amber-400"
                }`}
              >
                {mc.fragility_index}
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                {mc.is_robust ? "Robust Edge" : "Curve-Fit Alert"}
              </div>
            </div>
          </div>

          {/* Monte Carlo Fan Chart */}
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
                  <TrendingUp className="w-4 h-4 text-cyan-400" />
                  Probabilistic Equity Fan (1,000 Resampled Paths)
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Shuffles and resamples trades with replacement across 1,000 simulations.
                </p>
              </div>
              <div className="text-xs font-mono text-slate-400">
                Starting Capital: $10,000
              </div>
            </div>

            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={fanChartData}
                  margin={{ top: 10, right: 10, left: -10, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="mcBullGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                    </linearGradient>
                    <linearGradient id="mcMedianGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.05} />
                    </linearGradient>
                    <linearGradient id="mcBearGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#f43f5e" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis
                    dataKey="tradeNum"
                    stroke="#64748b"
                    fontSize={11}
                    tickLine={false}
                  />
                  <YAxis
                    stroke="#64748b"
                    fontSize={11}
                    tickFormatter={(val) => `$${(val / 1000).toFixed(1)}k`}
                    tickLine={false}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#020617",
                      borderColor: "#334155",
                      borderRadius: "0.5rem",
                      fontSize: "11px",
                    }}
                    formatter={(val: any, name: string | undefined) => [
                      `$${Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                      name === "p95"
                        ? "P95 (Top 5% Bull)"
                        : name === "p75"
                        ? "P75 (Optimistic)"
                        : name === "p50"
                        ? "P50 (Median Expected)"
                        : name === "p25"
                        ? "P25 (Conservative)"
                        : "P5 (Worst 5% Stress)",
                    ]}
                    labelFormatter={(lbl) => `Trade Step: ${lbl}`}
                  />
                  {/* Outer Bull Bound P95 */}
                  <Area
                    type="monotone"
                    dataKey="p95"
                    stroke="#10b981"
                    strokeWidth={1.5}
                    fillOpacity={1}
                    fill="url(#mcBullGrad)"
                  />
                  {/* Median Expected P50 */}
                  <Area
                    type="monotone"
                    dataKey="p50"
                    stroke="#06b6d4"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#mcMedianGrad)"
                  />
                  {/* Bear Bound P5 */}
                  <Area
                    type="monotone"
                    dataKey="p5"
                    stroke="#f43f5e"
                    strokeWidth={1.5}
                    fillOpacity={1}
                    fill="url(#mcBearGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            <div className="flex items-center justify-between text-[11px] text-slate-400 mt-2 px-1">
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span>
                <span>P95 Bull: ${mc.terminal_p95_balance?.toLocaleString()}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 inline-block"></span>
                <span>P50 Median: ${mc.terminal_median_balance?.toLocaleString()}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-rose-500 inline-block"></span>
                <span>P5 Stress: ${mc.terminal_p5_balance?.toLocaleString()}</span>
              </div>
            </div>
          </div>

          {/* Quick Metrics Summary Footer */}
          <div className="p-3 bg-slate-900/50 border border-slate-800 rounded-xl flex items-center justify-between text-xs">
            <div className="text-slate-400">
              Total Trades: <span className="font-mono text-white font-bold">{node.trades}</span>
            </div>
            <div className="text-slate-400">
              Win Rate: <span className="font-mono text-emerald-400 font-bold">{node.win_rate_pct}%</span>
            </div>
            <div className="text-slate-400">
              Profit Factor: <span className="font-mono text-cyan-400 font-bold">{node.profit_factor}</span>
            </div>
            <div className="text-slate-400">
              Sharpe Ratio: <span className="font-mono text-purple-400 font-bold">{node.sharpe_ratio}</span>
            </div>
          </div>
        </div>

        <div className="pt-4 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-white rounded-lg transition"
          >
            Close Telemetry
          </button>
        </div>
      </div>
    </div>
  );
}
