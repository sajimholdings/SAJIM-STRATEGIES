"use client";

import { useCallback, useEffect, useState } from "react";
import CorrelationTab from "./components/CorrelationTab";
import Header, { type Tab } from "./components/Header";
import LeaderboardTab from "./components/LeaderboardTab";
import RunDetailDrawer from "./components/RunDetailDrawer";
import StrategiesTab from "./components/StrategiesTab";
import UniverseTab from "./components/UniverseTab";
import { fetchCorrelation, fetchLeaderboard, fetchRegistry, fetchUniverse } from "./lib/api";
import type { CorrelationExport, LeaderboardExport, RegistryExport, RunRef, UniverseExport } from "./lib/types";

export default function Dashboard() {
  const [tab, setTab] = useState<Tab>("leaderboard");
  const [leaderboard, setLeaderboard] = useState<LeaderboardExport | null>(null);
  const [registry, setRegistry] = useState<RegistryExport | null>(null);
  const [universe, setUniverse] = useState<UniverseExport | null>(null);
  const [correlation, setCorrelation] = useState<CorrelationExport | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<RunRef | null>(null);

  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const errs: string[] = [];
    const settle = async <T,>(p: Promise<T>, set: (v: T) => void) => {
      try {
        const v = await p;
        if (!cancelled) set(v);
      } catch (e) {
        errs.push((e as Error).message);
      }
    };
    Promise.all([
      settle(fetchLeaderboard(), setLeaderboard),
      settle(fetchRegistry(), setRegistry),
      settle(fetchUniverse(), setUniverse),
      settle(fetchCorrelation(), setCorrelation),
    ]).then(() => {
      if (cancelled) return;
      setErrors(errs);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  const load = useCallback(() => {
    setLoading(true);
    setReloadToken((t) => t + 1);
  }, []);

  const openRun = useCallback((r: RunRef) => setOpen(r), []);
  const closeRun = useCallback(() => setOpen(null), []);

  return (
    <div className="min-h-screen bg-[#07090e] text-slate-100 p-4 md:p-8 font-sans selection:bg-cyan-500 selection:text-black">
      <Header activeTab={tab} onTab={setTab} onReload={load} reloading={loading} leaderboard={leaderboard?.strategies ?? []} registry={registry} lastUpdated={leaderboard?.last_updated ?? null} />

      {errors.length > 0 && (
        <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs space-y-0.5">
          {errors.map((e) => <div key={e}>{e}</div>)}
        </div>
      )}

      {tab === "leaderboard" && <LeaderboardTab rows={leaderboard?.strategies ?? []} onOpen={openRun} />}
      {tab === "strategies" && <StrategiesTab registry={registry} onOpen={openRun} />}
      {tab === "universe" && <UniverseTab universe={universe} onOpen={openRun} />}
      {tab === "correlations" && <CorrelationTab corr={correlation} />}

      {open && <RunDetailDrawer key={`${open.strategyId}/${open.runId}`} target={open} onClose={closeRun} />}
    </div>
  );
}
