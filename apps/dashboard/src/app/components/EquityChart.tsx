"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EquityPoint } from "../lib/types";
import { money } from "../lib/format";

export default function EquityChart({ series, positive, height = 160 }: { series: EquityPoint[]; positive: boolean; height?: number }) {
  const data = series.map((p, i) => ({ i, equity: p.equity, dd: p.drawdown_pct, time: p.time }));
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="i" hide />
          <YAxis domain={["auto", "auto"]} stroke="#64748b" fontSize={10} tickLine={false} width={56} tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as { i: number; equity: number; dd: number; time: string };
              return (
                <div className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-[11px] text-slate-200">
                  <div className="text-slate-400">Trade #{p.i} · {p.time?.slice(0, 16).replace("T", " ")}</div>
                  <div className="font-mono">Equity {money(p.equity)}</div>
                  <div className="font-mono text-rose-300">Drawdown {p.dd.toFixed(2)}%</div>
                </div>
              );
            }}
          />
          <Line type="monotone" dataKey="equity" stroke={positive ? "#10b981" : "#f43f5e"} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
