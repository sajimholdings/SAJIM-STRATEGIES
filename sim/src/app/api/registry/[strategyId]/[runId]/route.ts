import { NextResponse } from "next/server";
import path from "path";
import { readExport, readJson, readText, runDir } from "../../../../lib/server";
import type { Diagnosis, Metrics, MonteCarlo, RegistryExport } from "../../../../lib/types";

export async function GET(_req: Request, ctx: { params: Promise<{ strategyId: string; runId: string }> }) {
  const { strategyId, runId } = await ctx.params;
  const registry = readExport<RegistryExport>("registry.json");
  const strategy = registry?.strategies.find((s) => s.strategy_id === strategyId);
  const run = strategy?.runs.find((r) => r.run_id === runId);
  if (!run) {
    return NextResponse.json({ success: false, error: "Run not found in registry.json" }, { status: 404 });
  }
  const dir = runDir(strategyId, runId);
  const metrics = dir ? readJson<Metrics>(path.join(dir, "metrics.json")) : null;
  const monte_carlo = dir ? readJson<MonteCarlo>(path.join(dir, "monte_carlo.json")) : null;
  const diagnosis = dir ? readJson<Diagnosis>(path.join(dir, "diagnosis.json")) : null;
  const tear_sheet_md = dir ? readText(path.join(dir, "tear_sheet.md")) : null;
  return NextResponse.json({ success: true, run, metrics, monte_carlo, diagnosis, tear_sheet_md });
}
