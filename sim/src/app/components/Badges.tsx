import { tierBadge, tierShort } from "../lib/format";
import type { Tier } from "../lib/types";

export function TierBadge({ tier, full = false }: { tier: Tier; full?: boolean }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap ${tierBadge(tier)}`} title={tier}>
      {full ? tier : tierShort(tier)}
    </span>
  );
}

export function SourceBadge({ source, tags = [] }: { source: string; tags?: string[] }) {
  if (tags.includes("demo")) {
    return <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-fuchsia-500/20 text-fuchsia-300 border border-fuchsia-500/30" title="Demo run on synthetic data. Not a market result.">DEMO</span>;
  }
  if (source === "legacy_manual") {
    return <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-700/70 text-slate-300 border border-slate-600" title="Transcribed from the pre-registry leaderboard. No trade list, so no Monte Carlo or equity curve.">LEGACY</span>;
  }
  return <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/20" title="Recorded by the registry with full artifacts.">VERIFIED</span>;
}

export function EngineChip({ engine }: { engine: string }) {
  return <span className="inline-block font-mono text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300">{engine}</span>;
}
