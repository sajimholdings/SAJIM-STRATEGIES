"use client";

import React from "react";
import {
  X,
  Sparkles,
  TrendingUp,
  Info,
} from "lucide-react";
import MonteCarloFan from "./MonteCarloFan";
import type { MonteCarloPayload } from "../../lib/results";

type DiagnosisPayload = {
  edge_rating?: string;
  headline?: string;
  structural_cause?: string;
  mechanics_detail?: string;
  recommendation?: string;
};

/**
 * One matrix cell. Mirrors `UniverseNode` in lib/universe.ts; `profit_factor`
 * is nullable because a run with no losing trades has no profit factor, and a
 * node with status NO_DATA/PENDING_ADAPTER/ERROR has no metrics at all.
 */
type MatrixNode = {
  symbol?: string;
  timeframe?: string;
  trades?: number;
  win_rate_pct?: number;
  profit_factor?: number | null;
  sharpe_ratio?: number;
  max_drawdown_pct?: number;
  monte_carlo?: MonteCarloPayload | null;
  diagnosis?: DiagnosisPayload | null;
};

interface MonteCarloDrawerProps {
  node: MatrixNode;
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

  // Guard: cells without Monte Carlo payloads (e.g. imported/derived nodes or
  // strategies the matrix never stress-tested) previously crashed the whole
  // dashboard by dereferencing mc.trade_steps on undefined.
  if (!mc) {
    return (
      <div className="fixed inset-0 z-50 flex justify-end bg-black/75 backdrop-blur-sm">
        <div className="w-full max-w-2xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 flex flex-col justify-between">
          <div className="space-y-6">
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
                <h2 className="text-xl font-bold text-white tracking-tight">
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

            <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 flex flex-col items-center text-center space-y-3">
              <Info className="w-8 h-8 text-amber-400" />
              <h3 className="text-sm font-semibold text-white">
                No Monte Carlo telemetry for this cell
              </h3>
              <p className="text-xs text-slate-400 leading-relaxed max-w-sm">
                This strategy was catalogued without a stress-test payload. Run the
                real matrix builder (<span className="font-mono text-cyan-300">python -m sajim_quant matrix</span>)
                with Monte Carlo enabled to populate fan-chart and drawdown data.
              </p>
              <div className="flex flex-wrap justify-center gap-2 text-[11px] font-mono text-slate-400">
                <span className="px-2 py-1 rounded bg-slate-800/80 border border-slate-700">
                  trades: {node.trades ?? "—"}
                </span>
                <span className="px-2 py-1 rounded bg-slate-800/80 border border-slate-700">
                  win rate: {node.win_rate_pct ?? "—"}%
                </span>
                <span className="px-2 py-1 rounded bg-slate-800/80 border border-slate-700">
                  PF: {node.profit_factor ?? "—"}
                </span>
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

  const edgeBadgeStyles: Record<string, string> = {
    ALPHA_LEADER: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
    SOLID_VIABLE: "bg-cyan-500/10 text-cyan-400 border-cyan-500/30",
    CHOP_VULNERABLE: "bg-amber-500/10 text-amber-400 border-amber-500/30",
    TOXIC_FAILURE: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  };

  const badgeClass =
    edgeBadgeStyles[diag?.edge_rating ?? ""] ||
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
              &quot;{diag?.headline || "Systematic Edge Analysis"}&quot;
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
                  (mc.worst_drawdown_95_pct ?? 0) > 20
                    ? "text-rose-400"
                    : (mc.worst_drawdown_95_pct ?? 0) > 10
                    ? "text-amber-400"
                    : "text-emerald-400"
                }`}
              >
                {mc.worst_drawdown_95_pct ?? 0}%
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                99% Bound: {mc.worst_drawdown_99_pct ?? 0}%
              </div>
            </div>

            <div className="bg-slate-900/70 border border-slate-800/80 rounded-xl p-3">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Risk of Ruin (&gt;20% DD)
              </div>
              <div
                className={`text-lg font-bold mt-0.5 ${
                  (mc.risk_of_ruin_20_pct ?? 0) > 10
                    ? "text-rose-400"
                    : (mc.risk_of_ruin_20_pct ?? 0) > 0
                    ? "text-amber-400"
                    : "text-emerald-400"
                }`}
              >
                {mc.risk_of_ruin_20_pct ?? 0}%
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                &gt;10% DD: {mc.risk_of_ruin_10_pct ?? 0}%
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
                  Probabilistic Equity Fan
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Resampled equity paths from the real Monte Carlo simulation for this cell.
                </p>
              </div>
            </div>

            <MonteCarloFan mc={mc} height={256} />
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
              Profit Factor: <span className="font-mono text-cyan-400 font-bold">{node.profit_factor ?? "n/a"}</span>
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
