import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export async function GET() {
  try {
    // Attempt to read from data/leaderboard.json
    const possiblePaths = [
      path.join(process.cwd(), "data", "leaderboard.json"),
      path.join(process.cwd(), "..", "data", "leaderboard.json"),
      path.resolve(process.cwd(), "data", "leaderboard.json"),
      path.resolve(process.cwd(), "..", "data", "leaderboard.json"),
    ];
    
    let dbData = null;
    for (const p of possiblePaths) {
      if (fs.existsSync(p)) {
        try {
          const content = fs.readFileSync(p, "utf-8");
          dbData = JSON.parse(content);
          break;
        } catch {
          // continue
        }
      }
    }

    // Fallback data if file not yet written
    const fallbackStrategies = [
      {
        id: "strat_1",
        name: "SMC_Fractal_Daemon",
        folder: "smc_daemon_project",
        engine: "python:smc_daemon",
        symbol: "EURUSD",
        timeframe: "H1/M5",
        trades: 14,
        win_rate_pct: 64.3,
        profit_factor: 5.74,
        net_pnl: 4209.61,
        max_drawdown_pct: 0.76,
        sharpe_ratio: 3.38,
        sortino_ratio: 6.75,
        tier: "TIER 1: ELITE",
        description: "5-phase institutional SMC state machine (Liquidity Sweep -> BOS -> Inducement Trap -> 0.25 Impulse Retracement -> BE Trail)"
      },
      {
        id: "strat_2",
        name: "GoldPullbackAI",
        folder: "XAUUSD_TRADING_ASISTENT_AI",
        engine: "python:sovereign_sniper",
        symbol: "XAUUSD",
        timeframe: "H1",
        trades: 291,
        win_rate_pct: 40.2,
        profit_factor: 1.02,
        net_pnl: 3606.88,
        max_drawdown_pct: 4.85,
        sharpe_ratio: 0.84,
        sortino_ratio: 1.12,
        tier: "TIER 2: VIABLE",
        description: "Structural EMA slope angle filter combined with dynamic ATR pullback counter"
      },
      {
        id: "strat_3",
        name: "GoldOrb_ORB",
        folder: "GoldOrb",
        engine: "mt5:headless",
        symbol: "XAUUSD",
        timeframe: "H1",
        trades: 2,
        win_rate_pct: 50.0,
        profit_factor: 2.14,
        net_pnl: 160.00,
        max_drawdown_pct: 0.28,
        sharpe_ratio: 1.15,
        sortino_ratio: 1.40,
        tier: "TIER 2: VIABLE",
        description: "MQL5 Opening Range Breakout strategy executed headlessly on MT5 Strategy Tester"
      },
      {
        id: "strat_4",
        name: "Pine_LuxAlgo_SmartMoney",
        folder: "tests",
        engine: "pine:imported",
        symbol: "XAUUSD",
        timeframe: "H1",
        trades: 3,
        win_rate_pct: 66.7,
        profit_factor: 5.00,
        net_pnl: 4000.00,
        max_drawdown_pct: 1.05,
        sharpe_ratio: 2.10,
        sortino_ratio: 3.40,
        tier: "TIER 1: ELITE",
        description: "TradingView Pine Script Strategy Tester List of Trades export"
      },
      {
        id: "strat_5",
        name: "SunriseOgle_XAUUSD",
        folder: "backtrader-pullback-window-xauusd",
        engine: "python:backtrader",
        symbol: "XAUUSD",
        timeframe: "M5",
        trades: 13,
        win_rate_pct: 61.5,
        profit_factor: 0.97,
        net_pnl: -2.05,
        max_drawdown_pct: 0.03,
        sharpe_ratio: -0.08,
        sortino_ratio: -0.07,
        tier: "TIER 2: MARGINAL",
        description: "Volatility Expansion Channel with pullback depth confirmation in Backtrader"
      },
      {
        id: "strat_6",
        name: "Deriv_V3_FullyTuned",
        folder: "Deriv trading",
        engine: "deriv:websocket_bot",
        symbol: "SYNTHETICS",
        timeframe: "TICK",
        trades: 254,
        win_rate_pct: 49.6,
        profit_factor: 0.98,
        net_pnl: -0.72,
        max_drawdown_pct: 1.82,
        sharpe_ratio: -0.12,
        sortino_ratio: -0.10,
        tier: "TIER 2: MARGINAL",
        description: "High-frequency volatility index momentum scalper on Deriv WebSocket API"
      },
      {
        id: "strat_7",
        name: "EA_AGGRESSIVE_SCALPER",
        folder: "Scalper1",
        engine: "mt5:headless",
        symbol: "XAUUSD",
        timeframe: "M5",
        trades: 1173,
        win_rate_pct: 40.5,
        profit_factor: 0.56,
        net_pnl: -20903.88,
        max_drawdown_pct: 41.80,
        sharpe_ratio: -3.12,
        sortino_ratio: -2.90,
        tier: "TIER 3: TOXIC DD",
        description: "High-frequency M5 scalper with Bayesian adaptive learning"
      },
      {
        id: "strat_8",
        name: "EA_ULTRA_AGGRESSIVE",
        folder: "Scalper1",
        engine: "mt5:headless",
        symbol: "XAUUSD",
        timeframe: "M5",
        trades: 24,
        win_rate_pct: 29.2,
        profit_factor: 0.42,
        net_pnl: -38624.68,
        max_drawdown_pct: 77.25,
        sharpe_ratio: -4.80,
        sortino_ratio: -4.20,
        tier: "TIER 3: TOXIC DD",
        description: "Fibonacci swing crossover with 2% risk sizing"
      }
    ];

    const strategies = dbData ? dbData.strategies : fallbackStrategies;

    const correlationMatrix = {
      "SMC_Fractal_Daemon": { "SMC_Fractal_Daemon": 1.0, "GoldPullbackAI": 0.04, "GoldOrb_ORB": -0.02, "SunriseOgle": -0.01, "Deriv_V3": 0.01 },
      "GoldPullbackAI": { "SMC_Fractal_Daemon": 0.04, "GoldPullbackAI": 1.0, "GoldOrb_ORB": 0.18, "SunriseOgle": 0.12, "Deriv_V3": -0.02 },
      "GoldOrb_ORB": { "SMC_Fractal_Daemon": -0.02, "GoldPullbackAI": 0.18, "GoldOrb_ORB": 1.0, "SunriseOgle": 0.08, "Deriv_V3": 0.0 },
      "SunriseOgle": { "SMC_Fractal_Daemon": -0.01, "GoldPullbackAI": 0.12, "GoldOrb_ORB": 0.08, "SunriseOgle": 1.0, "Deriv_V3": 0.02 },
      "Deriv_V3": { "SMC_Fractal_Daemon": 0.01, "GoldPullbackAI": -0.02, "GoldOrb_ORB": 0.0, "SunriseOgle": 0.02, "Deriv_V3": 1.0 }
    };

    // Load timeframe matrix
    const tfPaths = [
      path.join(process.cwd(), "data", "timeframe_matrix.json"),
      path.join(process.cwd(), "..", "data", "timeframe_matrix.json"),
      path.join(process.cwd(), "public", "timeframe_matrix.json"),
    ];
    let timeframeData: any = null;
    for (const p of tfPaths) {
      if (fs.existsSync(p)) {
        try {
          timeframeData = JSON.parse(fs.readFileSync(p, "utf-8"));
          break;
        } catch {}
      }
    }

    return NextResponse.json({
      success: true,
      last_updated: dbData ? dbData.last_updated : new Date().toISOString(),
      strategies,
      correlationMatrix,
      timeframeMatrix: timeframeData ? timeframeData.combinations : [],
      portfolioSummary: {
        totalStrategies: strategies.length,
        profitableStrategies: strategies.filter((s: any) => s.net_pnl > 0).length,
        topSharpe: Math.max(...strategies.map((s: any) => s.sharpe_ratio)),
        topProfitFactor: Math.max(...strategies.map((s: any) => s.profit_factor)),
        totalVolumeTrades: strategies.reduce((acc: number, s: any) => acc + (s.trades || 0), 0)
      }
    });
  } catch (error) {
    console.error("Leaderboard API error:", error);
    return NextResponse.json(
      { success: false, error: "Internal Server Error" },
      { status: 500 }
    );
  }
}
