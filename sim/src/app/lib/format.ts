import type { Tier } from "./types";

export const money = (v: number, digits = 2) =>
  `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export const signedMoney = (v: number) => (v >= 0 ? "+" : "") + money(v);

export const pct = (v: number, digits = 1) => `${v.toFixed(digits)}%`;

export const num = (v: number, digits = 2) => v.toFixed(digits);

export function tierBadge(tier: Tier): string {
  if (tier.startsWith("TIER 1")) return "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
  if (tier === "TIER 2: VIABLE") return "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30";
  if (tier.startsWith("TIER 2")) return "bg-blue-500/20 text-blue-300 border border-blue-500/30";
  if (tier === "TIER 3: INSUFFICIENT DATA") return "bg-amber-500/20 text-amber-300 border border-amber-500/30";
  if (tier.startsWith("TIER 3")) return "bg-rose-500/20 text-rose-300 border border-rose-500/30";
  return "bg-slate-700/60 text-slate-300 border border-slate-600";
}

export const tierShort = (tier: Tier) => tier.split(":")[0];

export const sharpeClass = (s: number) => (s >= 2 ? "text-emerald-400" : s >= 1 ? "text-cyan-400" : s >= 0 ? "text-blue-300" : "text-rose-400");

export const pfClass = (pf: number) => (pf >= 1.5 ? "text-emerald-400" : pf >= 1.0 ? "text-blue-400" : "text-rose-400");

export const ddClass = (dd: number, warn = 20) => (dd > warn ? "text-rose-400 font-bold" : "text-slate-300");

export const edgeBadge: Record<string, string> = {
  ALPHA_LEADER: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  SOLID_VIABLE: "bg-cyan-500/10 text-cyan-400 border-cyan-500/30",
  CHOP_VULNERABLE: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  TOXIC_FAILURE: "bg-rose-500/10 text-rose-400 border-rose-500/30",
};

export function engineFamily(engine: string): "PYTHON" | "MT5" | "PINE" | "DERIV" | "OTHER" {
  const e = engine.toLowerCase();
  if (e.startsWith("python")) return "PYTHON";
  if (e.startsWith("mt5") || e.includes("mql")) return "MT5";
  if (e.startsWith("pine")) return "PINE";
  if (e.startsWith("deriv")) return "DERIV";
  return "OTHER";
}

export const shortDate = (iso: string) => (iso ? iso.slice(0, 10) : "");
