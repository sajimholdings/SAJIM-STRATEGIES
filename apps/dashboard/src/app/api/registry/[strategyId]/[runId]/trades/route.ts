import { NextResponse } from "next/server";
import path from "path";
import { readText, runDir } from "../../../../../lib/server";

export async function GET(_req: Request, ctx: { params: Promise<{ strategyId: string; runId: string }> }) {
  const { strategyId, runId } = await ctx.params;
  const dir = runDir(strategyId, runId);
  const csv = dir ? readText(path.join(dir, "trades.csv")) : null;
  if (csv === null) {
    return NextResponse.json({ success: false, error: "trades.csv not available for this run" }, { status: 404 });
  }
  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${strategyId}_${runId}_trades.csv"`,
    },
  });
}
