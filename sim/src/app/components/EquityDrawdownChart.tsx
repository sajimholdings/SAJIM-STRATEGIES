"use client";

import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { DrawdownPoint, EquityPoint } from "../../lib/results";

interface EquityDrawdownChartProps {
  equity: EquityPoint[];
  drawdown: DrawdownPoint[];
  /** Total chart stack height in px (equity takes ~60%, drawdown ~40%). */
  height?: number;
}

const tooltipStyle = {
  backgroundColor: "#0f172a",
  borderColor: "#334155",
  borderRadius: "8px",
  fontSize: "11px",
} as const;

/**
 * Quant-standard dual panel: equity curve on top, drawdown gutter below.
 * Both series come from real backtest trades (lib/results.ts) — never simulated.
 */
export default function EquityDrawdownChart({
  equity,
  drawdown,
  height = 320,
}: EquityDrawdownChartProps) {
  const equityH = Math.round(height * 0.6);
  const ddH = height - equityH;

  return (
    <div className="space-y-1">
      <div style={{ height: equityH }} className="w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <LineChart data={equity} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="index" hide />
            <YAxis
              stroke="#475569"
              fontSize={10}
              tickLine={false}
              width={52}
              tickFormatter={(v) => `$${(Number(v) / 1000).toFixed(1)}k`}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(val) => [
                `$${Number(val).toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`,
                "Equity",
              ]}
              labelFormatter={(lbl) => `Trade #${lbl}`}
            />
            <Line
              type="monotone"
              dataKey="equity"
              stroke="#10b981"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div style={{ height: ddH }} className="w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <AreaChart data={drawdown} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f43f5e" stopOpacity={0.45} />
                <stop offset="100%" stopColor="#f43f5e" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <XAxis dataKey="index" hide />
            <YAxis
              stroke="#475569"
              fontSize={10}
              tickLine={false}
              width={52}
              tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(val) => [`${Number(val).toFixed(2)}%`, "Drawdown"]}
              labelFormatter={(lbl) => `Trade #${lbl}`}
            />
            <Area
              type="monotone"
              dataKey="ddPct"
              stroke="#f43f5e"
              strokeWidth={1.5}
              fill="url(#ddGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
