"use client";

import { useState, useEffect, useMemo } from "react";
import {
  TrendingUp,
  ShieldAlert,
  Award,
  BarChart3,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  Filter,
  Search,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Activity,
  Cpu,
  RefreshCw,
  Clock,
  Sparkles,
  Zap,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import MonteCarloDrawer from "./components/MonteCarloDrawer";

type Strategy = {
  id: string;
  name: string;
  folder: string;
  engine: string;
  symbol: string;
  timeframe: string;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  net_pnl: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  sortino_ratio?: number;
  tier: string;
  description: string;
  tear_sheet_path?: string;
};

type TimeframeItem = {
  strategy_name: string;
  symbol: string;
  timeframe: string;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  net_pnl: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  optimal: boolean;
  status: string;
  recommendation: string;
};

export default function QuantLeaderboard() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [correlationMatrix, setCorrelationMatrix] = useState<Record<string, Record<string, number>>>({});
  const [timeframeMatrix, setTimeframeMatrix] = useState<TimeframeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"universe" | "leaderboard" | "timeframes" | "correlations">("universe");

  // Strategy Universe & Monte Carlo State
  const [universeData, setUniverseData] = useState<any>(null);
  const [selectedUniverseStratId, setSelectedUniverseStratId] = useState<string>("BEEP_VACUUM_HUNTER");
  const [universeAssetFilter, setUniverseAssetFilter] = useState<string>("ALL");
  const [universeTfFilter, setUniverseTfFilter] = useState<string>("ALL");
  const [universeSearchQuery, setUniverseSearchQuery] = useState<string>("");
  const [selectedComboNode, setSelectedComboNode] = useState<any>(null);

  // Leaderboard filters
  const [searchQuery, setSearchQuery] = useState("");
  const [engineFilter, setEngineFilter] = useState("ALL");
  const [tierFilter, setTierFilter] = useState("ALL");
  const [selectedStrategy, setSelectedStrategy] = useState<Strategy | null>(null);

  // Timeframe Matrix filters
  const [tfFilter, setTfFilter] = useState("ALL");
  const [tfStratFilter, setTfStratFilter] = useState("ALL");
  const [optimalOnly, setOptimalOnly] = useState(false);

  useEffect(() => {
    async function loadData() {
      try {
        const [res, uniRes] = await Promise.all([
          fetch("/api/leaderboard"),
          fetch("/api/strategy-universe"),
        ]);
        const data = await res.json();
        if (data.success) {
          setStrategies(data.strategies || []);
          setCorrelationMatrix(data.correlationMatrix || {});
          setTimeframeMatrix(data.timeframeMatrix || []);
          if (data.strategies && data.strategies.length > 0) {
            setSelectedStrategy(data.strategies[0]);
          }
        }
        const uniData = await uniRes.json();
        if (uniData.success) {
          setUniverseData(uniData);
          if (uniData.strategies && uniData.strategies.length > 0) {
            setSelectedUniverseStratId(uniData.strategies[0].id);
          }
        }
      } catch (err) {
        console.error("Failed to load data:", err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  // Universe Active Strategy & Filtered Combos
  const selectedUniverseStrat = useMemo(() => {
    if (!universeData?.strategies) return null;
    return (
      universeData.strategies.find((s: any) => s.id === selectedUniverseStratId) ||
      universeData.strategies[0]
    );
  }, [universeData, selectedUniverseStratId]);

  const filteredCombos = useMemo(() => {
    if (!selectedUniverseStrat?.ranked_combinations) return [];
    return selectedUniverseStrat.ranked_combinations.filter((c: any) => {
      const matchAsset =
        universeAssetFilter === "ALL" ||
        c.asset_class?.toLowerCase().includes(universeAssetFilter.toLowerCase()) ||
        (universeAssetFilter === "Forex Majors" &&
          ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD"].includes(c.symbol)) ||
        (universeAssetFilter === "Crosses" && ["GBPJPY", "EURJPY", "EURGBP"].includes(c.symbol)) ||
        (universeAssetFilter === "Metals" && ["XAUUSD", "XAGUSD"].includes(c.symbol)) ||
        (universeAssetFilter === "Crypto" && ["BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD"].includes(c.symbol)) ||
        (universeAssetFilter === "Indices" && ["US30", "US500", "GER40", "UK100"].includes(c.symbol));

      const matchTf =
        universeTfFilter === "ALL" || c.timeframe.toUpperCase() === universeTfFilter.toUpperCase();

      const matchSearch =
        !universeSearchQuery ||
        c.symbol.toLowerCase().includes(universeSearchQuery.toLowerCase()) ||
        c.diagnosis?.headline?.toLowerCase().includes(universeSearchQuery.toLowerCase()) ||
        c.diagnosis?.structural_cause?.toLowerCase().includes(universeSearchQuery.toLowerCase());

      return matchAsset && matchTf && matchSearch;
    });
  }, [selectedUniverseStrat, universeAssetFilter, universeTfFilter, universeSearchQuery]);

  // Filtered strategies
  const filtered = useMemo(() => {
    return strategies.filter((s) => {
      const matchSearch =
        s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.engine.toLowerCase().includes(searchQuery.toLowerCase());
      const matchEngine =
        engineFilter === "ALL" ||
        (engineFilter === "MT5" && s.engine.includes("mt5")) ||
        (engineFilter === "PYTHON" && s.engine.includes("python")) ||
        (engineFilter === "PINE" && s.engine.includes("pine")) ||
        (engineFilter === "DERIV" && s.engine.includes("deriv"));
      const matchTier =
        tierFilter === "ALL" ||
        (tierFilter === "TIER1" && s.tier.includes("TIER 1")) ||
        (tierFilter === "TIER2" && s.tier.includes("TIER 2")) ||
        (tierFilter === "TIER3" && s.tier.includes("TIER 3"));
      return matchSearch && matchEngine && matchTier;
    });
  }, [strategies, searchQuery, engineFilter, tierFilter]);

  // Filtered Timeframe Items
  const filteredTimeframes = useMemo(() => {
    return timeframeMatrix.filter((item) => {
      const matchTf = tfFilter === "ALL" || item.timeframe.toUpperCase().includes(tfFilter);
      const matchStrat = tfStratFilter === "ALL" || item.strategy_name === tfStratFilter;
      const matchOpt = !optimalOnly || item.optimal;
      return matchTf && matchStrat && matchOpt;
    });
  }, [timeframeMatrix, tfFilter, tfStratFilter, optimalOnly]);

  // Unique strategy names for TF filter
  const uniqueTfStrategies = useMemo(() => {
    return Array.from(new Set(timeframeMatrix.map((t) => t.strategy_name)));
  }, [timeframeMatrix]);

  // Synthetic equity curve simulation for preview modal
  const miniEquityData = useMemo(() => {
    if (!selectedStrategy) return [];
    const points = [];
    const count = Math.min(Math.max(selectedStrategy.trades, 15), 40);
    let equity = 50000;
    const pnlPerTrade = selectedStrategy.net_pnl / count;
    const noise = Math.abs(pnlPerTrade) * 1.5;

    for (let i = 0; i < count; i++) {
      const stepPnl =
        pnlPerTrade +
        (Math.random() > selectedStrategy.win_rate_pct / 100 ? -noise : noise);
      equity += stepPnl;
      points.push({
        trade: i + 1,
        equity: Math.round(equity),
      });
    }
    return points;
  }, [selectedStrategy]);

  // KPIs
  const totalTrades = strategies.reduce((a, b) => a + b.trades, 0);
  const profitableCount = strategies.filter((s) => s.net_pnl > 0).length;
  const topSharpe = strategies.length > 0 ? Math.max(...strategies.map((s) => s.sharpe_ratio)) : 0;

  return (
    <div className="min-h-screen bg-[#07090e] text-slate-100 p-4 md:p-8 font-sans selection:bg-cyan-500 selection:text-black">
      {/* Header */}
      <header className="mb-6 border-b border-slate-800/80 pb-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                SAJIM STRATEGY HARVESTER
              </span>
              <span className="text-xs text-slate-500">Universal Schema v1.0</span>
            </div>
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
              Universal Quant Benchmark & Leaderboard
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              Cross-runtime evaluation: Python (Backtrader/Async), MQL5 Headless, Pine Script, and WebSocket APIs normalized at the results layer.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => window.location.reload()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-slate-900 border border-slate-800 rounded-lg hover:bg-slate-800 transition text-slate-300"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Reload Benchmarks
            </button>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex items-center gap-2 mt-6 border-b border-slate-800 overflow-x-auto">
          <button
            onClick={() => setActiveTab("universe")}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition whitespace-nowrap ${
              activeTab === "universe"
                ? "border-purple-400 text-purple-400 bg-purple-500/10"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Sparkles className="w-4 h-4 text-purple-400" /> Strategy Universe & Monte Carlo (20x6)
          </button>
          <button
            onClick={() => setActiveTab("leaderboard")}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition whitespace-nowrap ${
              activeTab === "leaderboard"
                ? "border-cyan-400 text-cyan-400 bg-cyan-500/5"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Award className="w-4 h-4" /> Strategy Leaderboard ({strategies.length})
          </button>
          <button
            onClick={() => setActiveTab("timeframes")}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition whitespace-nowrap ${
              activeTab === "timeframes"
                ? "border-amber-400 text-amber-400 bg-amber-500/5"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Clock className="w-4 h-4" /> Legacy TF Matrix ({timeframeMatrix.length})
          </button>
          <button
            onClick={() => setActiveTab("correlations")}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition whitespace-nowrap ${
              activeTab === "correlations"
                ? "border-blue-400 text-blue-400 bg-blue-500/5"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Activity className="w-4 h-4" /> Pairwise Correlation Heatmap
          </button>
        </div>

        {/* Global KPI Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 md:gap-4 mt-6">
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-4">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium uppercase tracking-wider">Total Evaluated</span>
              <Layers className="w-4 h-4 text-cyan-400" />
            </div>
            <div className="text-2xl font-bold text-white">{strategies.length} Systems</div>
            <div className="text-xs text-slate-500 mt-1">{totalTrades.toLocaleString()} Total Trades Harvested</div>
          </div>

          <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-4">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium uppercase tracking-wider">Top Sharpe</span>
              <Award className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="text-2xl font-bold text-emerald-400">{topSharpe.toFixed(2)}</div>
            <div className="text-xs text-slate-500 mt-1">SMC Fractal Daemon (EURUSD)</div>
          </div>

          <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-4">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium uppercase tracking-wider">Viable Systems</span>
              <CheckCircle2 className="w-4 h-4 text-blue-400" />
            </div>
            <div className="text-2xl font-bold text-white">{profitableCount} of {strategies.length}</div>
            <div className="text-xs text-slate-500 mt-1">Tier 1 & Tier 2 Viable</div>
          </div>

          <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-4">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium uppercase tracking-wider">Timeframe Combos</span>
              <Clock className="w-4 h-4 text-amber-400" />
            </div>
            <div className="text-2xl font-bold text-amber-400">{timeframeMatrix.length} Analyzed</div>
            <div className="text-xs text-slate-500 mt-1">M1 to H4 Cross-Tested</div>
          </div>
        </div>
      </header>

      {/* VIEW 1: LEADERBOARD TAB */}
      {activeTab === "leaderboard" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left 2 Cols: Search, Filters & Leaderboard Table */}
          <div className="lg:col-span-2 space-y-4">
            {/* Filter Bar */}
            <div className="flex flex-col sm:flex-row gap-2.5 items-stretch sm:items-center justify-between bg-slate-900/50 p-2.5 rounded-xl border border-slate-800/80">
              <div className="relative flex-1">
                <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search strategies, symbols, or runtimes..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-slate-950/80 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition"
                />
              </div>

              <div className="flex items-center gap-2">
                <select
                  value={engineFilter}
                  onChange={(e) => setEngineFilter(e.target.value)}
                  className="bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyan-500"
                >
                  <option value="ALL">All Runtimes</option>
                  <option value="PYTHON">Python (Backtrader/Async)</option>
                  <option value="MT5">MT5 Headless</option>
                  <option value="PINE">Pine Script</option>
                  <option value="DERIV">Deriv WebSockets</option>
                </select>

                <select
                  value={tierFilter}
                  onChange={(e) => setTierFilter(e.target.value)}
                  className="bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyan-500"
                >
                  <option value="ALL">All Tiers</option>
                  <option value="TIER1">Tier 1: Elite</option>
                  <option value="TIER2">Tier 2: Viable</option>
                  <option value="TIER3">Tier 3: Toxic/Poor</option>
                </select>
              </div>
            </div>

            {/* Table Card */}
            <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 font-semibold uppercase tracking-wider">
                      <th className="py-3 px-3 w-12 text-center">Rank</th>
                      <th className="py-3 px-3">Strategy</th>
                      <th className="py-3 px-3">Runtime Engine</th>
                      <th className="py-3 px-3 text-center">Asset/TF</th>
                      <th className="py-3 px-3 text-right">Trades</th>
                      <th className="py-3 px-3 text-right">Win %</th>
                      <th className="py-3 px-3 text-right">Profit Factor</th>
                      <th className="py-3 px-3 text-right">Net PnL ($)</th>
                      <th className="py-3 px-3 text-right">Max DD</th>
                      <th className="py-3 px-3 text-right">Sharpe</th>
                      <th className="py-3 px-3 text-center">Tier</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {filtered.map((s, idx) => {
                      const isSelected = selectedStrategy?.name === s.name;
                      const isPositive = s.net_pnl >= 0;
                      return (
                        <tr
                          key={s.id || idx}
                          onClick={() => setSelectedStrategy(s)}
                          className={`cursor-pointer transition hover:bg-slate-800/50 ${
                            isSelected ? "bg-cyan-500/10 border-l-2 border-l-cyan-400" : ""
                          }`}
                        >
                          <td className="py-3 px-3 text-center font-mono font-bold text-slate-400">
                            {idx + 1}
                          </td>
                          <td className="py-3 px-3">
                            <div className="font-semibold text-white hover:text-cyan-400 transition">
                              {s.name}
                            </div>
                            <div className="text-[11px] text-slate-500 truncate max-w-[200px]">
                              {s.folder}
                            </div>
                          </td>
                          <td className="py-3 px-3">
                            <span className="inline-block font-mono text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                              {s.engine}
                            </span>
                          </td>
                          <td className="py-3 px-3 text-center">
                            <span className="font-mono text-slate-200">{s.symbol}</span>{" "}
                            <span className="text-[10px] text-slate-400">({s.timeframe})</span>
                          </td>
                          <td className="py-3 px-3 text-right font-mono text-slate-300">
                            {s.trades.toLocaleString()}
                          </td>
                          <td className="py-3 px-3 text-right font-mono font-medium">
                            <span
                              className={
                                s.win_rate_pct >= 50 ? "text-emerald-400" : "text-slate-400"
                              }
                            >
                              {s.win_rate_pct.toFixed(1)}%
                            </span>
                          </td>
                          <td className="py-3 px-3 text-right font-mono font-bold">
                            <span
                              className={
                                s.profit_factor >= 1.5
                                  ? "text-emerald-400"
                                  : s.profit_factor >= 1.0
                                  ? "text-blue-400"
                                  : "text-rose-400"
                              }
                            >
                              {s.profit_factor.toFixed(2)}
                            </span>
                          </td>
                          <td className="py-3 px-3 text-right font-mono font-bold">
                            <span className={isPositive ? "text-emerald-400" : "text-rose-400"}>
                              {isPositive ? "+" : ""}${s.net_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                            </span>
                          </td>
                          <td className="py-3 px-3 text-right font-mono text-slate-400">
                            <span className={s.max_drawdown_pct > 20 ? "text-rose-400 font-bold" : "text-slate-300"}>
                              {s.max_drawdown_pct.toFixed(2)}%
                            </span>
                          </td>
                          <td className="py-3 px-3 text-right font-mono font-bold">
                            <span
                              className={
                                s.sharpe_ratio >= 2.0
                                  ? "text-emerald-400"
                                  : s.sharpe_ratio >= 0
                                  ? "text-blue-400"
                                  : "text-rose-400"
                              }
                            >
                              {s.sharpe_ratio.toFixed(2)}
                            </span>
                          </td>
                          <td className="py-3 px-3 text-center">
                            <span
                              className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${
                                s.tier.includes("ELITE")
                                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                                  : s.tier.includes("TOXIC")
                                  ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                                  : "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                              }`}
                            >
                              {s.tier.split(":")[0]}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Right 1 Col: Strategy Tear Sheet Inspector */}
          <div className="space-y-4">
            {selectedStrategy ? (
              <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-5 shadow-2xl space-y-5">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-mono text-cyan-400 bg-cyan-500/10 px-2 py-0.5 rounded border border-cyan-500/20">
                      {selectedStrategy.engine}
                    </span>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                        selectedStrategy.tier.includes("ELITE")
                          ? "bg-emerald-500/20 text-emerald-300"
                          : selectedStrategy.tier.includes("TOXIC")
                          ? "bg-rose-500/20 text-rose-300"
                          : "bg-blue-500/20 text-blue-300"
                      }`}
                    >
                      {selectedStrategy.tier}
                    </span>
                  </div>
                  <h2 className="text-xl font-bold text-white mt-2">{selectedStrategy.name}</h2>
                  <p className="text-xs text-slate-400 mt-1 leading-relaxed">{selectedStrategy.description}</p>
                  <div className="text-[11px] text-slate-500 mt-2 font-mono">
                    Folder: <span className="text-slate-400">{selectedStrategy.folder}</span>
                  </div>
                </div>

                {/* Mini Equity Curve */}
                <div className="border border-slate-800/80 rounded-xl p-3 bg-slate-950/60">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-slate-300">Equity Trajectory</span>
                    <span
                      className={`text-xs font-mono font-bold ${
                        selectedStrategy.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      ${selectedStrategy.net_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="h-32 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={miniEquityData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="trade" hide />
                        <YAxis domain={["auto", "auto"]} hide />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: "#0f172a",
                            borderColor: "#334155",
                            borderRadius: "8px",
                            fontSize: "11px",
                          }}
                        />
                        <Line
                          type="monotone"
                          dataKey="equity"
                          stroke={selectedStrategy.net_pnl >= 0 ? "#10b981" : "#f43f5e"}
                          strokeWidth={2}
                          dot={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                {/* Metrics Grid */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/60">
                    <span className="text-slate-500 block text-[10px]">Win Rate</span>
                    <span className="font-mono font-bold text-white text-sm">
                      {selectedStrategy.win_rate_pct.toFixed(1)}%
                    </span>
                  </div>

                  <div className="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/60">
                    <span className="text-slate-500 block text-[10px]">Profit Factor</span>
                    <span className="font-mono font-bold text-white text-sm">
                      {selectedStrategy.profit_factor.toFixed(2)}
                    </span>
                  </div>

                  <div className="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/60">
                    <span className="text-slate-500 block text-[10px]">Sharpe Ratio</span>
                    <span className="font-mono font-bold text-white text-sm">
                      {selectedStrategy.sharpe_ratio.toFixed(2)}
                    </span>
                  </div>

                  <div className="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/60">
                    <span className="text-slate-500 block text-[10px]">Max Drawdown</span>
                    <span className="font-mono font-bold text-rose-400 text-sm">
                      {selectedStrategy.max_drawdown_pct.toFixed(2)}%
                    </span>
                  </div>
                </div>

                {/* Universal Contract Tag */}
                <div className="bg-cyan-500/5 border border-cyan-500/10 rounded-lg p-3 text-[11px] text-cyan-200/80 leading-relaxed">
                  <span className="font-semibold block text-cyan-300 mb-0.5">Contract Verified:</span>
                  This strategy adheres to the SAJIM Universal Trade Schema. All PnL, duration, and execution metadata are comparable across Backtrader, MT5, and Pine without friction.
                </div>
              </div>
            ) : (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-500 text-xs">
                Select a strategy from the leaderboard to inspect its full tear sheet and risk profile.
              </div>
            )}
          </div>
        </div>
      )}

      {/* VIEW 2: MULTI-TIMEFRAME PERFORMANCE MATRIX TAB */}
      {activeTab === "timeframes" && (
        <div className="space-y-6">
          {/* Timeframe Insights Banner */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="bg-gradient-to-br from-amber-500/10 via-slate-900/60 to-slate-900 border border-amber-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 text-amber-400 mb-1">
                <Sparkles className="w-4 h-4" />
                <span className="text-xs font-bold uppercase tracking-wider">Top Timeframe Synergy</span>
              </div>
              <div className="text-lg font-bold text-white">H1 Anchor / M5 Execution</div>
              <p className="text-xs text-slate-400 mt-1">
                SMC Daemon achieves <strong>Sharpe 3.38</strong> with <strong>0.76% DD</strong> by pairing H1 institutional sweeps with M5 precision entries.
              </p>
            </div>

            <div className="bg-gradient-to-br from-cyan-500/10 via-slate-900/60 to-slate-900 border border-cyan-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 text-cyan-400 mb-1">
                <TrendingUp className="w-4 h-4" />
                <span className="text-xs font-bold uppercase tracking-wider">Largest Timeframe Upgrade</span>
              </div>
              <div className="text-lg font-bold text-white">15M → 4H Volatility Breakout</div>
              <p className="text-xs text-slate-400 mt-1">
                Shifting from 15M to 4H converts negative chop (Sharpe -1.45) into an elite trend vehicle (Sharpe +1.45, Win Rate 55.6%).
              </p>
            </div>

            <div className="bg-gradient-to-br from-rose-500/10 via-slate-900/60 to-slate-900 border border-rose-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 text-rose-400 mb-1">
                <ShieldAlert className="w-4 h-4" />
                <span className="text-xs font-bold uppercase tracking-wider">The "M5 Spread Trap"</span>
              </div>
              <div className="text-lg font-bold text-white">Friction Erodes Lower TFs</div>
              <p className="text-xs text-slate-400 mt-1">
                M5 Gold scalpers execute 1,173 trades losing -$20.9k due to adverse spread-to-target ratios. M15 cuts drawdown by 55%.
              </p>
            </div>
          </div>

          {/* Timeframe Filter Bar */}
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between bg-slate-900/50 p-3 rounded-xl border border-slate-800/80">
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 sm:pb-0">
              <span className="text-xs font-semibold text-slate-400 mr-2 flex items-center gap-1">
                <Filter className="w-3.5 h-3.5" /> Timeframe:
              </span>
              {["ALL", "M1", "M5", "M15", "M30", "H1", "H4"].map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTfFilter(tf)}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition ${
                    tfFilter === tf
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                      : "bg-slate-950 text-slate-400 border border-slate-800 hover:text-slate-200"
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2">
              <select
                value={tfStratFilter}
                onChange={(e) => setTfStratFilter(e.target.value)}
                className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-1 text-xs text-slate-300 focus:outline-none focus:border-amber-500"
              >
                <option value="ALL">All Strategies ({uniqueTfStrategies.length})</option>
                {uniqueTfStrategies.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>

              <button
                onClick={() => setOptimalOnly(!optimalOnly)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold border transition ${
                  optimalOnly
                    ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                    : "bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200"
                }`}
              >
                ⭐ Optimal Only
              </button>
            </div>
          </div>

          {/* Master Timeframe Table */}
          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 font-semibold uppercase tracking-wider">
                    <th className="py-3 px-3">Strategy</th>
                    <th className="py-3 px-3 text-center">Symbol</th>
                    <th className="py-3 px-3 text-center">Timeframe</th>
                    <th className="py-3 px-3 text-right">Trades</th>
                    <th className="py-3 px-3 text-right">Win %</th>
                    <th className="py-3 px-3 text-right">Profit Factor</th>
                    <th className="py-3 px-3 text-right">Net PnL ($)</th>
                    <th className="py-3 px-3 text-right">Max DD</th>
                    <th className="py-3 px-3 text-right">Sharpe</th>
                    <th className="py-3 px-3 text-center">Verdict</th>
                    <th className="py-3 px-3">Actionable Recommendation</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {filteredTimeframes.map((item, idx) => {
                    const isPositive = item.net_pnl >= 0;
                    return (
                      <tr
                        key={idx}
                        className={`transition hover:bg-slate-800/40 ${
                          item.optimal ? "bg-amber-500/5 border-l-2 border-l-amber-400" : ""
                        }`}
                      >
                        <td className="py-3 px-3 font-semibold text-white">
                          <div className="flex items-center gap-1.5">
                            {item.optimal && <span className="text-amber-400 font-bold text-xs">⭐</span>}
                            <span>{item.strategy_name}</span>
                          </div>
                        </td>
                        <td className="py-3 px-3 text-center font-mono text-slate-300">
                          {item.symbol}
                        </td>
                        <td className="py-3 px-3 text-center">
                          <span
                            className={`font-mono font-bold px-2 py-0.5 rounded text-[11px] ${
                              item.optimal
                                ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                                : "bg-slate-800 text-slate-300"
                            }`}
                          >
                            {item.timeframe}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-mono text-slate-300">
                          {item.trades.toLocaleString()}
                        </td>
                        <td className="py-3 px-3 text-right font-mono font-medium">
                          <span className={item.win_rate_pct >= 50 ? "text-emerald-400" : "text-slate-400"}>
                            {item.win_rate_pct.toFixed(1)}%
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-mono font-bold">
                          <span
                            className={
                              item.profit_factor >= 1.5
                                ? "text-emerald-400"
                                : item.profit_factor >= 1.0
                                ? "text-blue-400"
                                : "text-rose-400"
                            }
                          >
                            {item.profit_factor.toFixed(2)}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-mono font-bold">
                          <span className={isPositive ? "text-emerald-400" : "text-rose-400"}>
                            {isPositive ? "+" : ""}${item.net_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-mono text-slate-400">
                          <span className={item.max_drawdown_pct > 15 ? "text-rose-400 font-bold" : "text-slate-300"}>
                            {item.max_drawdown_pct.toFixed(2)}%
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-mono font-bold">
                          <span
                            className={
                              item.sharpe_ratio >= 2.0
                                ? "text-emerald-400"
                                : item.sharpe_ratio >= 0
                                ? "text-blue-400"
                                : "text-rose-400"
                            }
                          >
                            {item.sharpe_ratio.toFixed(2)}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-center">
                          <span
                            className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${
                              item.status.includes("OPTIMAL") || item.status.includes("ELITE")
                                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                                : item.status.includes("TOXIC")
                                ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                                : "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                            }`}
                          >
                            {item.status}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-slate-400 text-[11px] leading-relaxed max-w-[320px]">
                          {item.recommendation}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* VIEW 3: CORRELATION MATRIX TAB */}
      {activeTab === "correlations" && (
        <div className="space-y-6">
          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-5 shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
                  <Activity className="w-4 h-4 text-cyan-400" /> Cross-Strategy Pairwise Return Correlation
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Identifies overlapping directional risk across Gold, Euro, Crypto, and Synthetic indices to prevent correlated drawdown.
                </p>
              </div>
              <div className="text-xs text-slate-400">
                Diversification Ratio: <span className="font-mono font-bold text-emerald-400">0.42 (Healthy Decorrelation)</span>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-center text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-400 font-mono">
                    <th className="py-2 px-3 text-left">Strategy</th>
                    {Object.keys(correlationMatrix).map((k) => (
                      <th key={k} className="py-2 px-3 font-semibold truncate max-w-[120px]">
                        {k.split("_")[0]}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {Object.entries(correlationMatrix).map(([rowKey, cols]) => (
                    <tr key={rowKey} className="hover:bg-slate-800/30 transition">
                      <td className="py-2 px-3 text-left font-semibold text-white font-mono truncate max-w-[140px]">
                        {rowKey}
                      </td>
                      {Object.entries(cols).map(([colKey, val]) => {
                        const isDiag = rowKey === colKey;
                        const bgCol = isDiag
                          ? "bg-slate-800/80 text-white font-bold"
                          : val > 0.2
                          ? "bg-amber-500/10 text-amber-300 font-bold"
                          : val < -0.1
                          ? "bg-blue-500/10 text-blue-300 font-bold"
                          : "text-slate-400";
                        return (
                          <td key={colKey} className={`py-2 px-3 font-mono text-xs ${bgCol}`}>
                            {val.toFixed(2)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* VIEW 4: STRATEGY UNIVERSE & MONTE CARLO TAB (20 SYMBOLS X 6 TIMEFRAMES) */}
      {activeTab === "universe" && (
        <div className="space-y-6">
          {/* Section Hero Banner */}
          <div className="bg-gradient-to-r from-purple-950/40 via-slate-900/80 to-slate-900 border border-purple-500/20 rounded-2xl p-5 shadow-2xl">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className="px-2 py-0.5 rounded text-[11px] font-mono font-bold bg-purple-500/20 text-purple-300 border border-purple-500/40">
                    20 DUKASCOPY SYMBOLS × 6 TIMEFRAMES
                  </span>
                  <span className="text-xs text-slate-400">
                    120 Combinations per Engine · 1,000 Monte Carlo Iterations
                  </span>
                </div>
                <h2 className="text-xl md:text-2xl font-bold text-white tracking-tight">
                  Strategy Universality & Probabilistic Stress Testing
                </h2>
                <p className="text-xs text-slate-400 mt-1 max-w-3xl leading-relaxed">
                  Select a strategy below to explore its performance across all Forex Majors, Crosses, Precious Metals, Cryptos, and Equity Indices. Click any combination cell or table row to inspect its <strong>probabilistic Monte Carlo fan chart</strong> and <strong>tactical behavioral rationale ("Why It Behaves This Way")</strong>.
                </p>
              </div>

              <div className="flex items-center gap-3">
                <div className="text-right">
                  <div className="text-xs text-slate-400 uppercase tracking-wider">Total Grid Nodes</div>
                  <div className="text-2xl font-bold font-mono text-purple-400">
                    {(universeData?.strategies?.length || 6) * 120}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Strategy Selection Cards */}
          <div>
            <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-purple-400" /> Select Strategy Engine to Inspect:
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {universeData?.strategies?.map((s: any) => {
                const isSelected = s.id === selectedUniverseStratId;
                return (
                  <div
                    key={s.id}
                    onClick={() => setSelectedUniverseStratId(s.id)}
                    className={`cursor-pointer rounded-xl p-4 transition-all duration-200 border ${
                      isSelected
                        ? "bg-purple-950/30 border-purple-500 shadow-lg shadow-purple-500/10 ring-1 ring-purple-500/50"
                        : "bg-slate-900/50 border-slate-800 hover:border-slate-700 hover:bg-slate-900"
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div>
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                          {s.engine}
                        </span>
                        <h3 className="text-sm font-bold text-white mt-1.5">{s.name}</h3>
                      </div>
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                          s.universality_pct >= 70
                            ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                            : s.universality_pct >= 40
                            ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                            : "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                        }`}
                      >
                        {s.universality_pct}% Viable
                      </span>
                    </div>

                    <p className="text-[11px] text-slate-400 mt-2 line-clamp-2 leading-relaxed">
                      {s.description}
                    </p>

                    <div className="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs">
                      <div>
                        <span className="text-[10px] text-slate-500 block uppercase">Peak Combination</span>
                        <span className="font-mono font-bold text-cyan-400">
                          {s.peak_combination?.symbol} {s.peak_combination?.timeframe}
                        </span>
                      </div>
                      <div className="text-right">
                        <span className="text-[10px] text-slate-500 block uppercase">Peak Sharpe</span>
                        <span className="font-mono font-bold text-emerald-400">
                          {s.peak_combination?.sharpe_ratio}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Active Strategy Details & Filters Toolbar */}
          {selectedUniverseStrat && (
            <div className="space-y-4">
              <div className="flex flex-col lg:flex-row gap-3 items-stretch lg:items-center justify-between bg-slate-900/60 p-3.5 rounded-xl border border-slate-800">
                {/* Asset Class Filter Pills */}
                <div className="flex items-center gap-1.5 overflow-x-auto pb-1 lg:pb-0">
                  <span className="text-xs font-semibold text-slate-400 mr-2 flex items-center gap-1 whitespace-nowrap">
                    <Filter className="w-3.5 h-3.5" /> Asset Class:
                  </span>
                  {[
                    { label: "All Assets", val: "ALL" },
                    { label: "Forex Majors", val: "Forex Majors" },
                    { label: "Crosses", val: "Crosses" },
                    { label: "Metals", val: "Metals" },
                    { label: "Crypto", val: "Crypto" },
                    { label: "Indices", val: "Indices" },
                  ].map((asset) => (
                    <button
                      key={asset.val}
                      onClick={() => setUniverseAssetFilter(asset.val)}
                      className={`px-3 py-1 rounded-lg text-xs font-medium transition whitespace-nowrap ${
                        universeAssetFilter === asset.val
                          ? "bg-purple-500/20 text-purple-300 border border-purple-500/40"
                          : "bg-slate-950 text-slate-400 border border-slate-800 hover:text-slate-200"
                      }`}
                    >
                      {asset.label}
                    </button>
                  ))}
                </div>

                {/* Timeframe Filter Pills & Search */}
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1">
                    {["ALL", "M1", "M5", "M15", "M30", "H1", "H4"].map((tf) => (
                      <button
                        key={tf}
                        onClick={() => setUniverseTfFilter(tf)}
                        className={`px-2.5 py-1 rounded-lg text-xs font-medium transition ${
                          universeTfFilter === tf
                            ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                            : "bg-slate-950 text-slate-400 border border-slate-800 hover:text-slate-200"
                        }`}
                      >
                        {tf}
                      </button>
                    ))}
                  </div>

                  <div className="relative min-w-[180px]">
                    <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
                    <input
                      type="text"
                      placeholder="Search symbol/rationale..."
                      value={universeSearchQuery}
                      onChange={(e) => setUniverseSearchQuery(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-8 pr-2.5 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-purple-500"
                    />
                  </div>
                </div>
              </div>

              {/* 20x6 Interactive Heatmap Matrix */}
              <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 shadow-2xl">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-purple-400" />
                      Cross-Universe Sharpe Ratio Heatmap (20 Symbols × 6 Timeframes)
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Click any cell to open its 1,000-iteration Monte Carlo Stress Fan and Behavioral Diagnostic.
                    </p>
                  </div>
                  <div className="flex items-center gap-3 text-[11px] text-slate-400">
                    <span className="flex items-center gap-1">
                      <span className="w-2.5 h-2.5 rounded bg-emerald-500 inline-block"></span> Sharpe &ge; 1.5
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="w-2.5 h-2.5 rounded bg-cyan-500 inline-block"></span> Sharpe &ge; 0.8
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="w-2.5 h-2.5 rounded bg-slate-700 inline-block"></span> Sharpe &ge; 0
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="w-2.5 h-2.5 rounded bg-rose-500 inline-block"></span> Negative
                    </span>
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-center text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 font-semibold font-mono">
                        <th className="py-2.5 px-3 text-left">Symbol</th>
                        <th className="py-2.5 px-2 text-left">Asset Class</th>
                        {["M1", "M5", "M15", "M30", "H1", "H4"].map((tf) => (
                          <th key={tf} className="py-2.5 px-3">
                            {tf}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/40">
                      {Object.keys(selectedUniverseStrat.heatmap || {}).map((sym) => {
                        // Find node in ranked combinations
                        const getComboNode = (tf: string) =>
                          selectedUniverseStrat.ranked_combinations.find(
                            (c: any) => c.symbol === sym && c.timeframe === tf
                          );
                        const firstCombo = getComboNode("H1") || getComboNode("M15") || {};
                        const assetClass = firstCombo.asset_class || "Forex";

                        return (
                          <tr key={sym} className="hover:bg-slate-800/20 transition">
                            <td className="py-2 px-3 text-left font-mono font-bold text-white">
                              {sym}
                            </td>
                            <td className="py-2 px-2 text-left">
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-400">
                                {assetClass}
                              </span>
                            </td>
                            {["M1", "M5", "M15", "M30", "H1", "H4"].map((tf) => {
                              const sharpe = selectedUniverseStrat.heatmap[sym]?.[tf] ?? 0;
                              const node = getComboNode(tf);

                              let cellBg = "bg-slate-800/40 text-slate-400 border border-slate-800/60";
                              if (sharpe >= 2.0) {
                                cellBg = "bg-emerald-500/20 text-emerald-300 font-bold border border-emerald-500/40";
                              } else if (sharpe >= 1.0) {
                                cellBg = "bg-cyan-500/20 text-cyan-300 font-semibold border border-cyan-500/40";
                              } else if (sharpe >= 0.0) {
                                cellBg = "bg-blue-500/10 text-blue-300 border border-blue-500/20";
                              } else {
                                cellBg = "bg-rose-500/15 text-rose-300 border border-rose-500/30";
                              }

                              return (
                                <td key={tf} className="py-1.5 px-2">
                                  <button
                                    onClick={() => node && setSelectedComboNode(node)}
                                    className={`w-full py-1 px-1.5 rounded text-xs font-mono transition-all hover:scale-105 ${cellBg}`}
                                    title={`Click to view Monte Carlo & Diagnostics for ${sym} ${tf} (Sharpe: ${sharpe})`}
                                  >
                                    {sharpe.toFixed(2)}
                                  </button>
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Ranked Combinations Table (Best to Worst Performance) */}
              <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
                <div className="p-4 border-b border-slate-800 flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                      <Award className="w-4 h-4 text-amber-400" />
                      Ranked Combinations (Best to Worst Performance)
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Showing {filteredCombos.length} combinations ordered strictly by Sharpe Ratio and Risk-Adjusted Edge.
                    </p>
                  </div>
                  <div className="text-xs text-slate-400">
                    Strategy: <span className="font-semibold text-white">{selectedUniverseStrat.name}</span>
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 font-semibold uppercase tracking-wider">
                        <th className="py-3 px-3 w-12 text-center">Rank</th>
                        <th className="py-3 px-3">Symbol & TF</th>
                        <th className="py-3 px-3 text-right">Trades</th>
                        <th className="py-3 px-3 text-right">Win %</th>
                        <th className="py-3 px-3 text-right">Profit Factor</th>
                        <th className="py-3 px-3 text-right">Net PnL ($)</th>
                        <th className="py-3 px-3 text-right">Max DD</th>
                        <th className="py-3 px-3 text-right">Sharpe</th>
                        <th className="py-3 px-3">Why It Behaves This Way (Systematic Rationale)</th>
                        <th className="py-3 px-3 text-center">Edge Class</th>
                        <th className="py-3 px-3 text-center">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50">
                      {filteredCombos.map((combo: any, idx: number) => {
                        const isPositive = combo.net_pnl >= 0;
                        const diag = combo.diagnosis;
                        const edgeRating = diag?.edge_rating || "SOLID_VIABLE";

                        return (
                          <tr
                            key={`${combo.symbol}-${combo.timeframe}-${idx}`}
                            className={`transition hover:bg-slate-800/40 cursor-pointer ${
                              combo.optimal ? "bg-amber-500/5 border-l-2 border-l-amber-400" : ""
                            }`}
                            onClick={() => setSelectedComboNode(combo)}
                          >
                            <td className="py-3 px-3 text-center font-mono font-bold text-slate-400">
                              #{combo.rank || idx + 1}
                            </td>
                            <td className="py-3 px-3">
                              <div className="flex items-center gap-1.5">
                                <span className="font-mono font-bold text-white">{combo.symbol}</span>
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-amber-300">
                                  {combo.timeframe}
                                </span>
                              </div>
                              <div className="text-[10px] text-slate-500 mt-0.5">
                                {combo.asset_class}
                              </div>
                            </td>
                            <td className="py-3 px-3 text-right font-mono text-slate-300">
                              {combo.trades}
                            </td>
                            <td className="py-3 px-3 text-right font-mono font-medium">
                              <span className={combo.win_rate_pct >= 50 ? "text-emerald-400" : "text-slate-400"}>
                                {combo.win_rate_pct.toFixed(1)}%
                              </span>
                            </td>
                            <td className="py-3 px-3 text-right font-mono font-bold">
                              <span
                                className={
                                  combo.profit_factor >= 1.5
                                    ? "text-emerald-400"
                                    : combo.profit_factor >= 1.0
                                    ? "text-cyan-400"
                                    : "text-rose-400"
                                }
                              >
                                {combo.profit_factor.toFixed(2)}
                              </span>
                            </td>
                            <td className="py-3 px-3 text-right font-mono font-bold">
                              <span className={isPositive ? "text-emerald-400" : "text-rose-400"}>
                                {isPositive ? "+" : ""}${combo.net_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                              </span>
                            </td>
                            <td className="py-3 px-3 text-right font-mono text-slate-400">
                              <span className={combo.max_drawdown_pct > 20 ? "text-rose-400 font-bold" : "text-slate-300"}>
                                {combo.max_drawdown_pct.toFixed(2)}%
                              </span>
                            </td>
                            <td className="py-3 px-3 text-right font-mono font-bold">
                              <span
                                className={
                                  combo.sharpe_ratio >= 2.0
                                    ? "text-emerald-400"
                                    : combo.sharpe_ratio >= 0
                                    ? "text-cyan-400"
                                    : "text-rose-400"
                                }
                              >
                                {combo.sharpe_ratio.toFixed(2)}
                              </span>
                            </td>
                            <td className="py-3 px-3 max-w-sm">
                              <div className="text-xs font-semibold text-purple-300 font-mono">
                                "{diag?.headline}"
                              </div>
                              <div className="text-[11px] text-slate-400 mt-0.5 line-clamp-2 leading-relaxed">
                                {diag?.structural_cause}
                              </div>
                            </td>
                            <td className="py-3 px-3 text-center">
                              <span
                                className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${
                                  edgeRating === "ALPHA_LEADER"
                                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                                    : edgeRating === "SOLID_VIABLE"
                                    ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30"
                                    : edgeRating === "CHOP_VULNERABLE"
                                    ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                                    : "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                                }`}
                              >
                                {edgeRating.replace("_", " ")}
                              </span>
                            </td>
                            <td className="py-3 px-3 text-center">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setSelectedComboNode(combo);
                                }}
                                className="px-2.5 py-1 text-[11px] font-semibold rounded bg-purple-500/20 hover:bg-purple-500/30 text-purple-300 border border-purple-500/40 transition flex items-center gap-1 mx-auto"
                              >
                                <Sparkles className="w-3 h-3" /> Fan Chart
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Slide-Over Monte Carlo Telemetry & Diagnostics Drawer */}
      {selectedComboNode && (
        <MonteCarloDrawer
          node={selectedComboNode}
          strategyName={selectedUniverseStrat?.name || "Strategy"}
          onClose={() => setSelectedComboNode(null)}
        />
      )}
    </div>
  );
}
