import type { CorrelationExport, LeaderboardExport, RegistryExport, RunDetail, UniverseExport } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  const body = (await res.json()) as { success: boolean; error?: string } & Record<string, unknown>;
  if (!body.success) throw new Error(body.error || `${url} failed`);
  return body as unknown as T;
}

export const fetchLeaderboard = () => getJson<LeaderboardExport>("/api/leaderboard");
export const fetchRegistry = () => getJson<RegistryExport>("/api/registry");
export const fetchUniverse = () => getJson<UniverseExport>("/api/strategy-universe");
export const fetchCorrelation = () => getJson<CorrelationExport>("/api/correlation");
export const fetchRunDetail = (strategyId: string, runId: string) =>
  getJson<RunDetail>(`/api/registry/${encodeURIComponent(strategyId)}/${encodeURIComponent(runId)}`);
export const tradesCsvUrl = (strategyId: string, runId: string) =>
  `/api/registry/${encodeURIComponent(strategyId)}/${encodeURIComponent(runId)}/trades`;
