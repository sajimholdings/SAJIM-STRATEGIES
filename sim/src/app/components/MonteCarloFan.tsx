"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Info } from "lucide-react";
import { buildFanChart, type MonteCarloPayload } from "../../lib/results";

interface MonteCarloFanProps {
  mc: MonteCarloPayload | null | undefined;
  height?: number;
}

const tooltipStyle = {
  backgroundColor: "#020617",
  borderColor: "#334155",
  borderRadius: "0.5rem",
  fontSize: "11px",
} as const;

/**
 * Probabilistic equity fan from a REAL Monte Carlo payload. When the cell has
 * no stress-test data this renders an explicit "not stress-tested" state —
 * placeholder curves are never synthesized.
 */
export default function MonteCarloFan({ mc, height = 240 }: MonteCarloFanProps) {
  const fan = buildFanChart(mc);

  if (!fan || fan.length === 0) {
    return (
      <div
        style={{ height }}
        className="w-full flex flex-col items-center justify-center text-center gap-2 bg-slate-950/40 rounded-lg border border-dashed border-slate-800"
      >
        <Info size={18} className="text-amber-400" />
        <p className="text-xs text-slate-400 max-w-xs">
          Not stress-tested. Run the real matrix builder with Monte Carlo enabled
          to generate fan-chart data for this cell.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <AreaChart data={fan} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
            <defs>
              <linearGradient id="fanBullGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
              </linearGradient>
              <linearGradient id="fanMedianGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.35} />
                <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="fanBearGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#f43f5e" stopOpacity={0.0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="step"
              stroke="#64748b"
              fontSize={11}
              tickLine={false}
            />
            <YAxis
              stroke="#64748b"
              fontSize={11}
              tickFormatter={(val) => `$${(Number(val) / 1000).toFixed(1)}k`}
              tickLine={false}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(val, name) => [
                `$${Number(val ?? 0).toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`,
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
              fill="url(#fanBullGrad)"
            />
            {/* Median Expected P50 */}
            <Area
              type="monotone"
              dataKey="p50"
              stroke="#06b6d4"
              strokeWidth={2}
              fillOpacity={1}
              fill="url(#fanMedianGrad)"
            />
            {/* Bear Bound P5 */}
            <Area
              type="monotone"
              dataKey="p5"
              stroke="#f43f5e"
              strokeWidth={1.5}
              fillOpacity={1}
              fill="url(#fanBearGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="flex items-center justify-between text-[11px] text-slate-400 px-1">
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span>
          <span>P95 Bull: ${mc?.terminal_p95_balance?.toLocaleString() ?? "—"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 inline-block"></span>
          <span>P50 Median: ${mc?.terminal_median_balance?.toLocaleString() ?? "—"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-rose-500 inline-block"></span>
          <span>P5 Stress: ${mc?.terminal_p5_balance?.toLocaleString() ?? "—"}</span>
        </div>
      </div>
    </div>
  );
}
