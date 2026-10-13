"use client";

import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { MonteCarlo } from "../lib/types";
import { money } from "../lib/format";

const BANDS: { key: "p95" | "p75" | "p50" | "p25" | "p5"; label: string; color: string }[] = [
  { key: "p95", label: "P95 (top 5%)", color: "#10b981" },
  { key: "p75", label: "P75 (optimistic)", color: "#34d399" },
  { key: "p50", label: "P50 (median)", color: "#06b6d4" },
  { key: "p25", label: "P25 (conservative)", color: "#fb7185" },
  { key: "p5", label: "P5 (worst 5%)", color: "#f43f5e" },
];

export default function MonteCarloFan({ mc, height = 260 }: { mc: MonteCarlo; height?: number }) {
  const data = mc.trade_steps.map((step, i) => ({
    step,
    p5: mc.p5_curve[i],
    p25: mc.p25_curve[i],
    p50: mc.p50_curve[i],
    p75: mc.p75_curve[i],
    p95: mc.p95_curve[i],
    // stacked band helpers
    outerBase: mc.p5_curve[i],
    outerSpan: mc.p95_curve[i] - mc.p5_curve[i],
    innerBase: mc.p25_curve[i],
    innerSpan: mc.p75_curve[i] - mc.p25_curve[i],
  }));
  return (
    <div>
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="step" stroke="#64748b" fontSize={11} tickLine={false} />
            <YAxis stroke="#64748b" fontSize={11} tickLine={false} width={60} domain={["auto", "auto"]} tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`} />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const row = payload[0].payload as Record<string, number>;
                return (
                  <div className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-[11px] text-slate-200 space-y-0.5">
                    <div className="text-slate-400">Trade step {label}</div>
                    {BANDS.map((b) => (
                      <div key={b.key} className="flex justify-between gap-4 font-mono">
                        <span style={{ color: b.color }}>{b.label}</span>
                        <span>{money(row[b.key])}</span>
                      </div>
                    ))}
                  </div>
                );
              }}
            />
            {/* P5–P95 envelope */}
            <Area type="monotone" dataKey="outerBase" stackId="outer" stroke="none" fill="transparent" isAnimationActive={false} />
            <Area type="monotone" dataKey="outerSpan" stackId="outer" stroke="none" fill="#06b6d4" fillOpacity={0.08} isAnimationActive={false} />
            {/* P25–P75 envelope */}
            <Area type="monotone" dataKey="innerBase" stackId="inner" stroke="none" fill="transparent" isAnimationActive={false} />
            <Area type="monotone" dataKey="innerSpan" stackId="inner" stroke="none" fill="#06b6d4" fillOpacity={0.18} isAnimationActive={false} />
            {BANDS.map((b) => (
              <Line key={b.key} type="monotone" dataKey={b.key} stroke={b.color} strokeWidth={b.key === "p50" ? 2.2 : 1.2} dot={false} isAnimationActive={false} strokeDasharray={b.key === "p25" || b.key === "p75" ? "4 3" : undefined} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400 mt-2 px-1">
        {BANDS.map((b) => (
          <span key={b.key} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: b.color }} />
            {b.label}
          </span>
        ))}
        <span className="ml-auto font-mono">{mc.num_simulations.toLocaleString()} paths · {mc.num_trades} trades · start {money(mc.initial_balance, 0)}</span>
      </div>
    </div>
  );
}
