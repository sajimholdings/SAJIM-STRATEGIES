"use client";

import { Fragment, useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, ChevronDown, ChevronRight } from "lucide-react";
import type { TradeRecord } from "../../lib/results";

interface TradeTableProps {
  trades: TradeRecord[];
}

type SortKey = "entry_time" | "net_pnl" | "duration_seconds";

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 16).replace("T", " ");
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * Sortable trade list built from the real Trade[] of a backtest result.
 * Rows expand to show full execution detail (prices, MAE/MFE, costs).
 */
export default function TradeTable({ trades }: TradeTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("entry_time");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [openRow, setOpenRow] = useState<number | null>(null);

  const sorted = useMemo(() => {
    const arr = trades.map((t, i) => ({ t, i }));
    arr.sort((a, b) => {
      let cmp = 0;
      if (sortKey === "entry_time") {
        cmp = (a.t.entry_time ?? "").localeCompare(b.t.entry_time ?? "");
      } else if (sortKey === "net_pnl") {
        cmp = (a.t.net_pnl ?? 0) - (b.t.net_pnl ?? 0);
      } else {
        cmp = (a.t.duration_seconds ?? 0) - (b.t.duration_seconds ?? 0);
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [trades, sortKey, sortDir]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "entry_time" ? "asc" : "desc");
    }
  };

  const arrow = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-slate-800 text-slate-400 font-mono text-left">
            <th className="py-2 px-2 w-6"></th>
            <th
              className="py-2 px-2 cursor-pointer select-none hover:text-white"
              onClick={() => onSort("entry_time")}
            >
              Entry{arrow("entry_time")}
            </th>
            <th className="py-2 px-2">Dir</th>
            <th className="py-2 px-2 text-right">Entry Px</th>
            <th className="py-2 px-2 text-right">Exit Px</th>
            <th
              className="py-2 px-2 text-right cursor-pointer select-none hover:text-white"
              onClick={() => onSort("net_pnl")}
            >
              Net PnL{arrow("net_pnl")}
            </th>
            <th className="py-2 px-2 text-right">Return %</th>
            <th
              className="py-2 px-2 text-right cursor-pointer select-none hover:text-white"
              onClick={() => onSort("duration_seconds")}
            >
              Duration{arrow("duration_seconds")}
            </th>
            <th className="py-2 px-2">Exit Reason</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60">
          {sorted.map(({ t, i }) => {
            const isOpen = openRow === i;
            const isBuy = (t.direction ?? "").toUpperCase() === "BUY";
            const pnl = t.net_pnl ?? 0;
            const mins = (t.duration_seconds ?? 0) / 60;
            const dur =
              mins >= 60
                ? `${(mins / 60).toFixed(1)}h`
                : `${mins.toFixed(0)}m`;
            return (
              <Fragment key={`group-${i}`}>
                <tr
                  onClick={() => setOpenRow(isOpen ? null : i)}
                  className="hover:bg-slate-800/40 cursor-pointer transition"
                >
                  <td className="py-2 px-2 text-slate-500">
                    {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  </td>
                  <td className="py-2 px-2 font-mono text-slate-300">{fmtDate(t.entry_time)}</td>
                  <td className="py-2 px-2">
                    <span
                      className={`inline-flex items-center gap-1 font-mono font-bold ${
                        isBuy ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {isBuy ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                      {isBuy ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-slate-300">
                    {t.entry_price?.toFixed(2) ?? "—"}
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-slate-300">
                    {t.exit_price?.toFixed(2) ?? "—"}
                  </td>
                  <td
                    className={`py-2 px-2 text-right font-mono font-bold ${
                      pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {fmtMoney(pnl)}
                  </td>
                  <td
                    className={`py-2 px-2 text-right font-mono ${
                      (t.return_pct ?? 0) >= 0 ? "text-emerald-400/80" : "text-rose-400/80"
                    }`}
                  >
                    {t.return_pct != null ? `${t.return_pct.toFixed(2)}%` : "—"}
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-slate-400">{dur}</td>
                  <td className="py-2 px-2">
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[10px]">
                      {t.exit_reason ?? "—"}
                    </span>
                  </td>
                </tr>
                {isOpen && (
                  <tr className="bg-slate-950/60">
                    <td colSpan={9} className="py-3 px-4">
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px] font-mono text-slate-400">
                        <div>
                          <span className="text-slate-500 block">Size</span>
                          {t.position_size ?? "—"}
                        </div>
                        <div>
                          <span className="text-slate-500 block">Gross PnL</span>
                          {fmtMoney(t.gross_pnl)}
                        </div>
                        <div>
                          <span className="text-slate-500 block">Commission</span>
                          {fmtMoney(t.commission)}
                        </div>
                        <div>
                          <span className="text-slate-500 block">Swap</span>
                          {fmtMoney(t.swap_cost)}
                        </div>
                        <div>
                          <span className="text-slate-500 block">MAE</span>
                          {t.mae != null ? fmtMoney(t.mae) : "—"}
                        </div>
                        <div>
                          <span className="text-slate-500 block">MFE</span>
                          {t.mfe != null ? fmtMoney(t.mfe) : "—"}
                        </div>
                        <div>
                          <span className="text-slate-500 block">Exit Time</span>
                          {fmtDate(t.exit_time)}
                        </div>
                        <div>
                          <span className="text-slate-500 block">Trade ID</span>
                          {t.trade_id ?? "—"}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
