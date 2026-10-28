"use client";

import { useState, useEffect } from "react";
import {
  Play,
  RotateCw,
  CheckCircle2,
  AlertTriangle,
  FileText,
  DollarSign,
  TrendingUp,
  Percent,
  Shield,
  Layers,
  Terminal,
  Database,
  Radio,
  BarChart2,
  Cloud
} from "lucide-react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import TradeTable from "./TradeTable";
import type { StrategyCatalogItem } from "../api/strategies/route";
import type { BacktestResultJSON, TradeRecord } from "../../lib/results";

export default function TestingCockpit() {
  const [strategies, setStrategies] = useState<StrategyCatalogItem[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string>("sunrise_ogle_xauusd");
  const [selectedSymbol, setSelectedSymbol] = useState<string>("XAUUSD");
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>("M5");
  const [initialBalance, setInitialBalance] = useState<number>(10000);
  const [computeProvider, setComputeProvider] = useState<"modal" | "local">("modal");

  const [loadingCatalog, setLoadingCatalog] = useState<boolean>(true);
  const [executing, setExecuting] = useState<boolean>(false);
  const [executionResult, setExecutionResult] = useState<BacktestResultJSON | null>(null);
  const [executionLogs, setExecutionLogs] = useState<string>("");
  const [executionDuration, setExecutionDuration] = useState<number | null>(null);
  const [showLogs, setShowLogs] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [runWarning, setRunWarning] = useState<string | null>(null);

  // Quick load existing cached result
  const loadExistingResult = async (stratId: string, symbol: string, tf: string) => {
    try {
      setErrorMsg(null);
      const res = await fetch("/api/run-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: stratId,
          symbol,
          timeframe: tf,
          mode: "load_existing"
        })
      });
      const data = await res.json();
      if (data.success && data.data) {
        setExecutionResult(data.data);
        setExecutionLogs(`[INFO] Loaded existing real result: ${data.file || "cached"}`);
      } else {
        setExecutionResult(null);
      }
    } catch (err) {
      console.warn("Auto-load cached result skipped:", err);
      setExecutionResult(null);
    }
  };

  // Load available strategies on mount
  useEffect(() => {
    async function loadCatalog() {
      try {
        setLoadingCatalog(true);
        const res = await fetch("/api/strategies");
        const data = await res.json();
        if (data.success && data.strategies?.length > 0) {
          setStrategies(data.strategies);
          const first = data.strategies[0];
          setSelectedStrategyId(first.id);
          setSelectedSymbol(first.default_symbol || "XAUUSD");
          setSelectedTimeframe(first.default_timeframe || "M5");

          // Auto-load existing result if available
          if (first.has_existing_result) {
            loadExistingResult(first.id, first.default_symbol, first.default_timeframe);
          }
        }
      } catch (err: unknown) {
        console.error("Failed to load strategy catalog:", err);
      } finally {
        setLoadingCatalog(false);
      }
    }
    loadCatalog();
  }, []);

  const selectedStrategy = strategies.find((s) => s.id === selectedStrategyId);

  // Update default symbol/timeframe when strategy changes
  const handleStrategyChange = (stratId: string) => {
    setSelectedStrategyId(stratId);
    setExecutionResult(null); // Clear previous strategy results immediately
    setErrorMsg(null);
    setExecutionLogs("");
    const strat = strategies.find((s) => s.id === stratId);
    if (strat) {
      if (strat.supported_symbols?.length > 0 && !strat.supported_symbols.includes(selectedSymbol)) {
        setSelectedSymbol(strat.default_symbol || strat.supported_symbols[0]);
      }
      if (strat.supported_timeframes?.length > 0 && !strat.supported_timeframes.includes(selectedTimeframe)) {
        setSelectedTimeframe(strat.default_timeframe || strat.supported_timeframes[0]);
      }
      // If has existing result, load it
      if (strat.has_existing_result) {
        loadExistingResult(stratId, strat.default_symbol, strat.default_timeframe);
      }
    }
  };

  // A Modal run that outlasts the route's wait comes back as 504 + job_id; it keeps
  // running in the cloud, so keep polling it here instead of reporting a failure.
  const waitForModalJob = async (jobId: string) => {
    const deadline = Date.now() + 15 * 60_000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 5_000));
      try {
        const r = await fetch(`/api/run-test?job=${encodeURIComponent(jobId)}`, { cache: "no-store" });
        const j = await r.json();
        if (j.success && j.data) return j;
        if (j.status === "failed" || j.status === "expired") return j;
        if (!j.success && r.status !== 502 && r.status !== 503) return j;
      } catch {
        // network blip — the job is unaffected; keep polling
      }
    }
    return { success: false, error: `Modal job ${jobId} is still running after 15 minutes; check back later.` };
  };

  // Run Real Execution Test
  const handleRunTest = async () => {
    try {
      setExecuting(true);
      setErrorMsg(null);
      setRunWarning(null);
      setExecutionLogs("");
      setExecutionResult(null); // Clear previous result so failure cannot display stale results
      const startT = Date.now();

      const res = await fetch("/api/run-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: selectedStrategyId,
          symbol: selectedSymbol,
          timeframe: selectedTimeframe,
          initial_balance: initialBalance,
          mode: "real_backtest",
          provider: computeProvider
        })
      });

      let data = await res.json();
      if (!data.success && data.job_id && res.status === 504) {
        setExecutionLogs(`[Modal] job ${data.job_id} is still running in the cloud — waiting for it…`);
        data = await waitForModalJob(data.job_id);
      }

      const elapsed = Date.now() - startT;
      setExecutionDuration(elapsed);

      if (data.success && data.data) {
        setExecutionResult(data.data);
        setExecutionLogs(data.logs || "Execution completed successfully.");
        if (data.warning) setRunWarning(data.warning);
      } else {
        setExecutionResult(null);
        setErrorMsg(data.error || "Strategy execution failed to return valid trades.");
        if (data.logs) setExecutionLogs(data.logs);
      }
    } catch (err: unknown) {
      setExecutionResult(null);
      setErrorMsg(
        err instanceof Error ? err.message : "Network error while triggering test execution."
      );
    } finally {
      setExecuting(false);
    }
  };

  // Metrics from result
  const metrics = executionResult?.metrics || {};
  const metadata = executionResult?.metadata || {};
  const trades: TradeRecord[] = executionResult?.trades || [];

  // Build cumulative equity curve series for chart
  const equitySeries = (() => {
    if (!trades || trades.length === 0) return [];
    let curBal = metadata.initial_balance || initialBalance;
    const series = [{ tradeNum: 0, equity: curBal, time: metadata.start_date || "Start" }];

    trades.forEach((t, idx) => {
      curBal += (t.net_pnl ?? 0);
      series.push({
        tradeNum: idx + 1,
        equity: Math.round(curBal * 100) / 100,
        time: t.exit_time || t.entry_time || `Trade ${idx + 1}`
      });
    });
    return series;
  })();

  const isProfitable = (metrics.net_profit ?? 0) >= 0;

  return (
    <div className="space-y-6">
      {/* Top Banner: Real Testing Mode */}
      <div className="bg-gradient-to-r from-blue-950/40 via-purple-950/20 to-neutral-900 border border-blue-800/40 rounded-xl p-5 shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="flex h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-xs uppercase tracking-wider font-semibold text-blue-400">Institutional Strategy Testing Cockpit</span>
            <span className="bg-emerald-500/20 text-emerald-300 text-[10px] px-2 py-0.5 rounded border border-emerald-500/30 font-medium">Real Dukascopy Parquet</span>
            <span className="bg-purple-500/20 text-purple-300 text-[10px] px-2 py-0.5 rounded border border-purple-500/30 font-medium">Universal Trade Schema</span>
          </div>
          <h2 className="text-xl font-bold text-white tracking-tight">Real Data & Direct Trade Execution</h2>
          <p className="text-xs text-neutral-400 mt-0.5">
            Select any trading strategy to test against real tick/bar Parquet feeds or forward dispatch real orders. No synthetic simulations.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Compute Engine Toggle: Modal Cloud vs Local */}
          <div className="flex bg-neutral-900/90 border border-neutral-800 p-1 rounded-lg text-xs">
            <button
              onClick={() => setComputeProvider("modal")}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-semibold transition ${
                computeProvider === "modal"
                  ? "bg-purple-600 text-white shadow-md shadow-purple-600/30"
                  : "text-neutral-400 hover:text-white"
              }`}
            >
              <Cloud className="w-3.5 h-3.5" />
              Modal Cloud
              <span className="text-[9px] bg-purple-900/60 text-purple-200 px-1 py-0.2 rounded border border-purple-400/30 font-mono">achwanyatours</span>
            </button>
            <button
              onClick={() => setComputeProvider("local")}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-semibold transition ${
                computeProvider === "local"
                  ? "bg-neutral-700 text-white shadow-sm"
                  : "text-neutral-400 hover:text-white"
              }`}
            >
              💻 Local Engine
            </button>
          </div>

          {/* Mode Toggle: Real Parquet vs Live */}
          <div className="flex bg-neutral-900/90 border border-neutral-800 p-1 rounded-lg text-xs">
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium bg-blue-600 text-white shadow-sm">
              <Database className="w-3.5 h-3.5" />
              Real Parquet Feed
            </div>
            <button
              disabled
              title="Live forward paper-execution daemon pipeline currently in development"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium text-neutral-500 opacity-60 cursor-not-allowed"
            >
              <Radio className="w-3.5 h-3.5" />
              Live Forward
              <span className="text-[9px] bg-neutral-800 text-neutral-400 px-1 py-0.5 rounded font-mono font-normal">SOON</span>
            </button>
          </div>
        </div>
      </div>

      {/* Control Panel: Selectors */}
      <div className="bg-neutral-900/80 border border-neutral-800 rounded-xl p-5 shadow-lg backdrop-blur">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          {/* Strategy Dropdown */}
          <div className="md:col-span-2">
            <label className="block text-xs font-semibold text-neutral-400 mb-1.5 flex items-center justify-between">
              <span>Select Strategy Package</span>
              {selectedStrategy && (
                <span className="text-[11px] text-blue-400 font-mono">
                  Engine: {selectedStrategy.engine}
                </span>
              )}
            </label>
            <select
              value={selectedStrategyId}
              onChange={(e) => handleStrategyChange(e.target.value)}
              disabled={loadingCatalog || executing}
              className="w-full bg-neutral-950 border border-neutral-700 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 font-medium"
            >
              {strategies.map((strat) => (
                <option key={strat.id} value={strat.id}>
                  {strat.name} ({strat.category})
                </option>
              ))}
            </select>
          </div>

          {/* Symbol Selector */}
          <div>
            <label className="block text-xs font-semibold text-neutral-400 mb-1.5">
              Market Symbol
            </label>
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              disabled={executing}
              className="w-full bg-neutral-950 border border-neutral-700 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 font-medium"
            >
              {(selectedStrategy?.supported_symbols || ["XAUUSD", "EURUSD", "GBPJPY", "BTCUSD", "US30"]).map((sym) => (
                <option key={sym} value={sym}>
                  {sym}
                </option>
              ))}
            </select>
          </div>

          {/* Timeframe Selector */}
          <div>
            <label className="block text-xs font-semibold text-neutral-400 mb-1.5">
              Timeframe
            </label>
            <select
              value={selectedTimeframe}
              onChange={(e) => setSelectedTimeframe(e.target.value)}
              disabled={executing}
              className="w-full bg-neutral-950 border border-neutral-700 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 font-medium"
            >
              {(selectedStrategy?.supported_timeframes || ["M1", "M5", "M15", "H1"]).map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Secondary Parameters & Launch Button */}
        <div className="mt-4 pt-4 border-t border-neutral-800/80 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-4 text-xs text-neutral-400 w-full md:w-auto">
            <div>
              <span className="text-neutral-500">Initial Capital:</span>{" "}
              <input
                type="number"
                value={initialBalance}
                onChange={(e) => setInitialBalance(Number(e.target.value))}
                className="w-24 bg-neutral-950 border border-neutral-700 text-white px-2 py-1 rounded text-xs ml-1"
              />
            </div>
            <div>
              <span className="text-neutral-500">Friction Model:</span>{" "}
              <span className="text-neutral-300 font-mono">Floating Spread + Commission</span>
            </div>
          </div>

          <div className="flex items-center gap-3 w-full md:w-auto justify-end">
            {selectedStrategy?.has_existing_result && (
              <button
                onClick={() => loadExistingResult(selectedStrategyId, selectedSymbol, selectedTimeframe)}
                disabled={executing}
                className="px-3.5 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-xs font-medium rounded-lg transition flex items-center gap-1.5"
              >
                <FileText className="w-3.5 h-3.5" />
                Load Cached Result
              </button>
            )}

            <button
              onClick={handleRunTest}
              disabled={executing}
              className={`px-5 py-2 text-white text-xs font-semibold rounded-lg shadow-lg transition flex items-center gap-2 ${
                executing
                  ? "bg-blue-600/60 cursor-not-allowed"
                  : "bg-blue-600 hover:bg-blue-500 active:scale-95 shadow-blue-500/20"
              }`}
            >
              {executing ? (
                <>
                  <RotateCw className="w-3.5 h-3.5 animate-spin" />
                  Running Strategy...
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5 fill-current" />
                  Execute Strategy Test
                </>
              )}
            </button>
          </div>
        </div>

        {/* Selected Strategy Details Snippet */}
        {selectedStrategy && (
          <div className="mt-3 pt-3 border-t border-neutral-800/40 text-[11px] text-neutral-400 flex flex-wrap items-center gap-x-6 gap-y-1">
            <span><strong className="text-neutral-300">Strategy Path:</strong> <code className="text-blue-400 font-mono">{selectedStrategy.source_file}</code></span>
            <span><strong className="text-neutral-300">Category:</strong> {selectedStrategy.category}</span>
            <span><strong className="text-neutral-300">Notes:</strong> {selectedStrategy.description}</span>
          </div>
        )}
      </div>

      {/* Error notification */}
      {errorMsg && (
        <div className="bg-rose-950/40 border border-rose-800 text-rose-300 text-xs p-3.5 rounded-lg flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 text-rose-400" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Result produced by an older cloud deploy than the working tree */}
      {runWarning && (
        <div className="bg-amber-950/40 border border-amber-700 text-amber-200 text-xs p-3.5 rounded-lg flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 text-amber-400" />
          <span>{runWarning}</span>
        </div>
      )}

      {/* Execution Results Section */}
      {executionResult && (
        <div className="space-y-6">
          {/* Header Status Bar */}
          <div className="flex flex-wrap items-center justify-between bg-neutral-900 border border-neutral-800 px-4 py-2.5 rounded-lg text-xs">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1.5 text-emerald-400 font-semibold">
                <CheckCircle2 className="w-4 h-4" />
                Execution Verified
              </span>
              <span className="text-neutral-500">|</span>
              <span className="text-neutral-300 font-medium">
                {metadata.strategy_name || selectedStrategy?.name} &bull; {metadata.symbol || selectedSymbol} &bull; {metadata.timeframe || selectedTimeframe}
              </span>
              {executionDuration && (
                <span className="text-neutral-400 font-mono text-[11px]">
                  ({(executionDuration / 1000).toFixed(2)}s)
                </span>
              )}
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={() => setShowLogs(!showLogs)}
                className="text-neutral-400 hover:text-white flex items-center gap-1 text-[11px] underline"
              >
                <Terminal className="w-3 h-3" />
                {showLogs ? "Hide Console Logs" : "View Console Logs"}
              </button>
            </div>
          </div>

          {/* Console Logs drawer */}
          {showLogs && (
            <div className="bg-black/90 border border-neutral-800 rounded-lg p-3.5 font-mono text-[11px] text-neutral-300 max-h-56 overflow-y-auto whitespace-pre-wrap">
              <div className="text-neutral-500 mb-1 border-b border-neutral-800 pb-1">Runner Console Telemetry</div>
              {executionLogs || "No console output recorded."}
            </div>
          )}

          {/* Key Metric Scorecards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="bg-neutral-900/90 border border-neutral-800 p-3.5 rounded-xl shadow-sm">
              <div className="text-[11px] font-semibold text-neutral-400 flex items-center justify-between">
                <span>Net Profit</span>
                <DollarSign className="w-3.5 h-3.5 text-neutral-500" />
              </div>
              <div className={`text-lg font-bold mt-1 ${isProfitable ? "text-emerald-400" : "text-rose-400"}`}>
                {metrics.net_profit != null ? `$${metrics.net_profit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">
                {metrics.return_pct != null ? `${metrics.return_pct.toFixed(2)}% return` : ""}
              </div>
            </div>

            <div className="bg-neutral-900/90 border border-neutral-800 p-3.5 rounded-xl shadow-sm">
              <div className="text-[11px] font-semibold text-neutral-400 flex items-center justify-between">
                <span>Win Rate</span>
                <Percent className="w-3.5 h-3.5 text-neutral-500" />
              </div>
              <div className="text-lg font-bold text-white mt-1">
                {metrics.win_rate_pct != null ? `${metrics.win_rate_pct.toFixed(1)}%` : "—"}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">
                {metrics.winning_trades ?? 0}W / {metrics.losing_trades ?? 0}L
              </div>
            </div>

            <div className="bg-neutral-900/90 border border-neutral-800 p-3.5 rounded-xl shadow-sm">
              <div className="text-[11px] font-semibold text-neutral-400 flex items-center justify-between">
                <span>Profit Factor</span>
                <TrendingUp className="w-3.5 h-3.5 text-neutral-500" />
              </div>
              <div className="text-lg font-bold text-white mt-1">
                {metrics.profit_factor != null ? (metrics.profit_factor >= 999 ? "∞" : metrics.profit_factor.toFixed(2)) : "—"}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">
                Exp: {metrics.expectancy != null ? `$${metrics.expectancy.toFixed(2)}` : "—"}
              </div>
            </div>

            <div className="bg-neutral-900/90 border border-neutral-800 p-3.5 rounded-xl shadow-sm">
              <div className="text-[11px] font-semibold text-neutral-400 flex items-center justify-between">
                <span>Sharpe Ratio</span>
                <BarChart2 className="w-3.5 h-3.5 text-neutral-500" />
              </div>
              <div className="text-lg font-bold text-white mt-1">
                {metrics.sharpe_ratio != null ? metrics.sharpe_ratio.toFixed(2) : "—"}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">
                Sortino: {metrics.sortino_ratio != null ? metrics.sortino_ratio.toFixed(2) : "—"}
              </div>
            </div>

            <div className="bg-neutral-900/90 border border-neutral-800 p-3.5 rounded-xl shadow-sm">
              <div className="text-[11px] font-semibold text-neutral-400 flex items-center justify-between">
                <span>Max Drawdown</span>
                <Shield className="w-3.5 h-3.5 text-neutral-500" />
              </div>
              <div className="text-lg font-bold text-rose-400 mt-1">
                {metrics.max_drawdown_pct != null ? `-${metrics.max_drawdown_pct.toFixed(2)}%` : "—"}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">
                {metrics.max_drawdown_amount != null ? `-$${metrics.max_drawdown_amount.toFixed(2)}` : ""}
              </div>
            </div>

            <div className="bg-neutral-900/90 border border-neutral-800 p-3.5 rounded-xl shadow-sm">
              <div className="text-[11px] font-semibold text-neutral-400 flex items-center justify-between">
                <span>Total Trades</span>
                <Layers className="w-3.5 h-3.5 text-neutral-500" />
              </div>
              <div className="text-lg font-bold text-white mt-1">
                {metrics.total_trades ?? trades.length}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">
                Avg dur: {metrics.avg_duration_minutes != null ? `${Math.round(metrics.avg_duration_minutes)}m` : "—"}
              </div>
            </div>
          </div>

          {/* Real Cumulative Equity Curve */}
          <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-5 shadow-lg">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-semibold text-white">Real Account Equity Curve</h3>
                <p className="text-[11px] text-neutral-400">Step-by-step account balance based on real completed fills</p>
              </div>
              <div className="text-right">
                <span className="text-xs text-neutral-400">Final Balance: </span>
                <span className={`text-sm font-bold font-mono ${isProfitable ? "text-emerald-400" : "text-rose-400"}`}>
                  ${metrics.final_balance != null ? metrics.final_balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}
                </span>
              </div>
            </div>

            <div className="h-64 w-full">
              {equitySeries.length > 1 ? (
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <AreaChart data={equitySeries} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                    <defs>
                      <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={isProfitable ? "#10b981" : "#f43f5e"} stopOpacity={0.4} />
                        <stop offset="95%" stopColor={isProfitable ? "#10b981" : "#f43f5e"} stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
                    <XAxis dataKey="tradeNum" stroke="#737373" tick={{ fontSize: 10 }} tickLine={false} />
                    <YAxis
                      stroke="#737373"
                      domain={['auto', 'auto']}
                      tick={{ fontSize: 10 }}
                      tickLine={false}
                      tickFormatter={(v) => `$${v.toLocaleString()}`}
                    />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#171717", borderColor: "#404040", borderRadius: 8, fontSize: 12 }}
                      formatter={(val) => [`$${Number(val ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, "Equity"]}
                      labelFormatter={(lbl) => `Trade #${lbl}`}
                    />
                    <Area
                      type="monotone"
                      dataKey="equity"
                      stroke={isProfitable ? "#10b981" : "#f43f5e"}
                      strokeWidth={2}
                      fillOpacity={1}
                      fill="url(#equityGrad)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-xs text-neutral-500">
                  Not enough trades to plot equity curve.
                </div>
              )}
            </div>
          </div>

          {/* Real Trades Execution Log */}
          <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-5 shadow-lg">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-semibold text-white">Real Trade Execution Log</h3>
                <p className="text-[11px] text-neutral-400">
                  Universal Trade Schema records for all {trades.length} completed orders
                </p>
              </div>
            </div>

            {trades.length > 0 ? (
              <TradeTable trades={trades} />
            ) : (
              <div className="p-8 text-center text-xs text-neutral-500 border border-dashed border-neutral-800 rounded-lg">
                No trades were triggered during this backtest window.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
