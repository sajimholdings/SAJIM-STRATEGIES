import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import type { UniverseNode, UniverseResponse, UniverseStrategy } from "../../../lib/universe";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const strategyId = searchParams.get("strategy_id");
    const symbol = searchParams.get("symbol");
    const timeframe = searchParams.get("timeframe");

    const possiblePaths = [
      path.join(process.cwd(), "data", "strategy_universe_matrix.json"),
      path.join(process.cwd(), "..", "data", "strategy_universe_matrix.json"),
      path.join(process.cwd(), "public", "strategy_universe_matrix.json"),
    ];

    let matrixData: Partial<UniverseResponse> | null = null;
    for (const p of possiblePaths) {
      if (fs.existsSync(p)) {
        try {
          const content = fs.readFileSync(p, "utf-8");
          matrixData = JSON.parse(content);
          break;
        } catch {
          // continue
        }
      }
    }

    if (!matrixData) {
      return NextResponse.json(
        { success: false, error: "Matrix data file not found" },
        { status: 404 }
      );
    }

    let strategies: UniverseStrategy[] = matrixData.strategies || [];

    // Filter by strategy_id if specified
    if (strategyId) {
      strategies = strategies.filter((s) => s.id === strategyId);
    }

    // Normalize each strategy so the client never guesses: peak combination,
    // universality %, and a symbol×timeframe heatmap are computed from the
    // REAL ranked_combinations when the file does not already provide them.
    strategies = strategies.map((s) => {
      const combos: UniverseNode[] = s.ranked_combinations || [];
      let peak = s.peak_combination;
      if (!peak && combos.length > 0) {
        peak = combos.reduce<UniverseNode | null>(
          (best, c) =>
            (c.sharpe_ratio ?? -Infinity) > (best?.sharpe_ratio ?? -Infinity) ? c : best,
          null
        );
      }
      let universality = s.universality_pct;
      if (typeof universality !== "number" && combos.length > 0) {
        const viable = combos.filter((c) => (c.sharpe_ratio ?? 0) > 0).length;
        universality = Math.round((viable / combos.length) * 100);
      }
      let heatmap = s.heatmap;
      if (!heatmap) {
        heatmap = {};
        for (const c of combos) {
          if (!c.symbol || !c.timeframe) continue;
          heatmap[c.symbol] = heatmap[c.symbol] ?? {};
          heatmap[c.symbol][c.timeframe] = c.sharpe_ratio ?? 0;
        }
      }
      return { ...s, peak_combination: peak, universality_pct: universality, heatmap };
    });

    // Filter combinations if symbol or timeframe specified
    if (symbol || timeframe) {
      strategies = strategies.map((s) => {
        let combos: UniverseNode[] = s.ranked_combinations || [];
        if (symbol) {
          combos = combos.filter((c) => c.symbol.toUpperCase() === symbol.toUpperCase());
        }
        if (timeframe) {
          combos = combos.filter((c) => c.timeframe.toUpperCase() === timeframe.toUpperCase());
        }
        return {
          ...s,
          ranked_combinations: combos,
        };
      });
    }

    return NextResponse.json({
      success: true,
      generated_at: matrixData.generated_at,
      build_mode: matrixData.build_mode ?? null,
      total_nodes: matrixData.total_nodes ?? null,
      completed_nodes: matrixData.completed_nodes ?? null,
      symbols: matrixData.symbols,
      timeframes: matrixData.timeframes,
      strategies,
    });
  } catch (error) {
    console.error("Strategy Universe API error:", error);
    return NextResponse.json(
      { success: false, error: "Internal Server Error" },
      { status: 500 }
    );
  }
}
