import { NextResponse } from "next/server";
import { readExport } from "../../lib/server";
import type { UniverseExport } from "../../lib/types";

export async function GET(request: Request) {
  const data = readExport<UniverseExport>("strategy_universe_matrix.json");
  if (!data) {
    return NextResponse.json({ success: false, error: "strategy_universe_matrix.json not found. Run `sajim export`." }, { status: 404 });
  }
  const { searchParams } = new URL(request.url);
  const strategyId = searchParams.get("strategy_id");
  const symbol = searchParams.get("symbol")?.toUpperCase();
  const timeframe = searchParams.get("timeframe")?.toUpperCase();
  let strategies = data.strategies;
  if (strategyId) strategies = strategies.filter((s) => s.id === strategyId);
  if (symbol || timeframe) {
    strategies = strategies.map((s) => ({
      ...s,
      ranked_combinations: s.ranked_combinations.filter(
        (c) => (!symbol || c.symbol.toUpperCase() === symbol) && (!timeframe || c.timeframe.toUpperCase() === timeframe),
      ),
    }));
  }
  return NextResponse.json({ success: true, ...data, strategies });
}
