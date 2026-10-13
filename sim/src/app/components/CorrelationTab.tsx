"use client";

import { Activity } from "lucide-react";
import type { CorrelationExport } from "../lib/types";

export default function CorrelationTab({ corr }: { corr: CorrelationExport | null }) {
  const names = corr?.strategies ?? [];
  return (
    <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-5 shadow-2xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-4">
        <div>
          <h3 className="text-sm font-semibold text-white flex items-center gap-1.5"><Activity className="w-4 h-4 text-cyan-400" /> Cross-strategy daily PnL correlation</h3>
          <p className="text-xs text-slate-400 mt-0.5">{corr?.note ?? "correlation.json not found. Run `sajim export`."}</p>
        </div>
        {corr?.diversification_ratio != null && (
          <div className="text-xs text-slate-400">
            Diversification ratio <span className="font-mono font-bold text-emerald-400">{corr.diversification_ratio.toFixed(2)}</span>
            {corr.portfolio_sharpe != null && <> · equal-weight portfolio Sharpe <span className="font-mono text-slate-200">{corr.portfolio_sharpe.toFixed(2)}</span></>}
            {corr.portfolio_max_drawdown_pct != null && <> · max DD <span className="font-mono text-slate-200">{corr.portfolio_max_drawdown_pct.toFixed(2)}%</span></>}
          </div>
        )}
      </div>
      {names.length < 2 ? (
        <div className="p-6 text-center text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
          Correlation is computed from the trade lists of primary runs. Record at least two strategies with <code>record_run()</code> and re-export to populate this matrix. Legacy rows have no trades and are excluded.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-center text-xs border-collapse">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 font-mono">
                <th className="py-2 px-3 text-left">Strategy</th>
                {names.map((n) => <th key={n} className="py-2 px-3 font-semibold truncate max-w-[120px]" title={n}>{n}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {names.map((row) => (
                <tr key={row} className="hover:bg-slate-800/30 transition">
                  <td className="py-2 px-3 text-left font-semibold text-white font-mono truncate max-w-[160px]" title={row}>{row}</td>
                  {names.map((col) => {
                    const val = corr?.matrix[row]?.[col] ?? 0;
                    const cls = row === col ? "bg-slate-800/80 text-white font-bold" : val > 0.2 ? "bg-amber-500/10 text-amber-300 font-bold" : val < -0.1 ? "bg-blue-500/10 text-blue-300 font-bold" : "text-slate-400";
                    return <td key={col} className={`py-2 px-3 font-mono ${cls}`}>{val.toFixed(2)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
