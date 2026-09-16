import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

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

    let matrixData: any = null;
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

    let strategies = matrixData.strategies || [];

    // Filter by strategy_id if specified
    if (strategyId) {
      strategies = strategies.filter((s: any) => s.id === strategyId);
    }

    // Filter combinations if symbol or timeframe specified
    if (symbol || timeframe) {
      strategies = strategies.map((s: any) => {
        let combos = s.ranked_combinations || [];
        if (symbol) {
          combos = combos.filter((c: any) => c.symbol.toUpperCase() === symbol.toUpperCase());
        }
        if (timeframe) {
          combos = combos.filter((c: any) => c.timeframe.toUpperCase() === timeframe.toUpperCase());
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
