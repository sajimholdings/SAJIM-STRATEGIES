import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export interface StrategyCatalogItem {
  id: string;
  name: string;
  folder: string;
  engine: "python:backtrader" | "python:custom" | "mt5:headless" | "deriv:bot" | "pine:csv";
  source_file: string;
  category: "Gold / Metals" | "SMC / ICT" | "Physics & Voids" | "Scalping" | "Crypto" | "Deriv Volatility" | "Trend & Momentum";
  description: string;
  default_symbol: string;
  default_timeframe: string;
  supported_symbols: string[];
  supported_timeframes: string[];
  run_adapter?: string;
  has_existing_result: boolean;
  latest_result_file?: string;
  latest_metrics?: {
    net_pnl: number;
    win_rate_pct: number;
    profit_factor: number;
    sharpe_ratio: number;
    max_drawdown_pct: number;
    trades: number;
  };
}

function loadCatalogFromDisk(): Omit<StrategyCatalogItem, "has_existing_result" | "latest_result_file" | "latest_metrics">[] {
  const candidatePaths = [
    path.resolve(process.cwd(), "..", "sajim_quant", "core", "strategy_catalog.json"),
    path.resolve(process.cwd(), "sajim_quant", "core", "strategy_catalog.json"),
  ];

  for (const p of candidatePaths) {
    if (fs.existsSync(p)) {
      try {
        return JSON.parse(fs.readFileSync(p, "utf-8"));
      } catch (err) {
        console.error("Failed to parse strategy_catalog.json:", err);
      }
    }
  }
  return [];
}

export async function GET() {
  try {
    const rawCatalog = loadCatalogFromDisk();
    const resultsDirs = [
      path.resolve(process.cwd(), "..", "data", "results"),
      path.resolve(process.cwd(), "data", "results"),
      path.resolve(process.cwd(), "public", "test_results"),
    ];

    // Read all existing result JSONs
    const resultFiles: { filename: string; fullPath: string }[] = [];
    for (const dir of resultsDirs) {
      if (fs.existsSync(dir)) {
        const files = fs.readdirSync(dir).filter(f => f.endsWith(".json"));
        for (const f of files) {
          resultFiles.push({ filename: f, fullPath: path.join(dir, f) });
        }
      }
    }

    const items: StrategyCatalogItem[] = rawCatalog.map((strat) => {
      // Find matching result file
      const matchingFile = resultFiles.find(rf => 
        rf.filename.toLowerCase().includes(strat.id.toLowerCase()) ||
        rf.filename.toLowerCase().includes(strat.name.toLowerCase().replace(/[^a-z0-9]/g, "_"))
      );

      let hasResult = false;
      let latestMetrics: StrategyCatalogItem["latest_metrics"] = undefined;
      let latestFile: string | undefined = undefined;

      if (matchingFile) {
        try {
          const raw = JSON.parse(fs.readFileSync(matchingFile.fullPath, "utf-8"));
          hasResult = true;
          latestFile = matchingFile.filename;
          
          if (raw.metrics) {
            latestMetrics = {
              net_pnl: Number(raw.metrics.net_profit ?? raw.metrics.net_pnl ?? 0),
              win_rate_pct: Number(raw.metrics.win_rate_pct ?? raw.metrics.win_rate ?? 0),
              profit_factor: Number(raw.metrics.profit_factor ?? 0),
              sharpe_ratio: Number(raw.metrics.sharpe_ratio ?? 0),
              max_drawdown_pct: Number(raw.metrics.max_drawdown_pct ?? 0),
              trades: Number(raw.metrics.total_trades ?? raw.trades?.length ?? 0)
            };
          }
        } catch {
          // If parse fails, leave as no result
        }
      }

      return {
        ...strat,
        has_existing_result: hasResult,
        latest_result_file: latestFile,
        latest_metrics: latestMetrics
      };
    });

    return NextResponse.json({
      success: true,
      count: items.length,
      strategies: items
    });
  } catch (error: unknown) {
    console.error("Strategies API error:", error);
    const message = error instanceof Error ? error.message : "Internal Server Error";
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
