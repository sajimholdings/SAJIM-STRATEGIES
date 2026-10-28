/**
 * Phase 1 verification — real-data series builders vs the real backtest JSON.
 *
 * Asserts that the lib reproduces the engine's own published metrics
 * (final balance, max drawdown) from raw trades, and checks edge cases.
 *
 * Run:  npx tsx scripts/verify_results.ts
 */

import fs from "fs";
import path from "path";
import {
  analyzeResult,
  bestResultMatch,
  buildDrawdownSeries,
  buildEquityCurve,
  buildFanChart,
  buildMonthlyPnl,
  buildPnlHistogram,
  computeDiversificationRatio,
  computePairwiseCorrelation,
  monthlyPnlMap,
  normalizeKey,
  parseResultJSON,
  scoreResultMatch,
  summarizeResultEntry,
  type MonteCarloPayload,
  type ResultEntry,
} from "../src/lib/results";

let failures = 0;
let checks = 0;

function assert(cond: boolean, label: string, detail?: string) {
  checks++;
  if (cond) {
    console.log(`  PASS  ${label}`);
  } else {
    failures++;
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

function approx(a: number, b: number, tol: number) {
  return Math.abs(a - b) <= tol;
}

console.log("\n=== 1. Real backtest JSON (sunrise_ogle_xauusd XAUUSD M5) ===");
const jsonPath = path.resolve(
  __dirname,
  "../public/test_results/sunrise_ogle_xauusd_XAUUSD_M5_result.json"
);
const raw = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));

const analysis = analyzeResult(raw);
assert(analysis !== null, "parseResultJSON accepts the real result JSON");
if (!analysis) {
  console.log("\nFATAL: cannot proceed without a parsed result");
  process.exit(1);
}

const { result, equity, drawdown, monthly } = analysis;
const metrics = result.metrics;
const trades = result.trades;

assert(trades.length === 23, "trade count matches engine output", `${trades.length}`);

const initial = metrics.initial_balance ?? 0;
assert(
  approx(equity[0].equity, initial, 1e-6),
  "equity curve starts at initial balance",
  `${equity[0].equity} vs ${initial}`
);
assert(equity.length === trades.length + 1, "equity curve has one point per trade + start", `${equity.length}`);

const sumPnl = trades.reduce((s, t) => s + (t.net_pnl ?? 0), 0);
const finalEquity = equity[equity.length - 1].equity;
assert(
  approx(finalEquity, initial + sumPnl, 1e-6),
  "final equity = initial + sum(net_pnl)",
  `${finalEquity} vs ${initial + sumPnl}`
);
assert(
  approx(finalEquity, metrics.final_balance ?? NaN, 1e-3),
  "final equity matches the engine's final_balance",
  `${finalEquity} vs ${metrics.final_balance}`
);

// Equity curve must be strictly increasing in time order
let monotone = true;
for (let i = 1; i < equity.length; i++) {
  const a = equity[i - 1].time ? Date.parse(equity[i - 1].time!) : 0;
  const b = equity[i].time ? Date.parse(equity[i].time!) : Infinity;
  if (a > b) monotone = false;
}
assert(monotone, "equity curve is sorted by entry time");

// Drawdown series
const maxDd = Math.max(...drawdown.map((d) => d.ddPct));
assert(
  approx(maxDd, metrics.max_drawdown_pct ?? NaN, 1.0),
  "max drawdown recomputed from trades matches engine metric (±1pt)",
  `${maxDd.toFixed(2)}% vs ${metrics.max_drawdown_pct}%`
);
assert(drawdown.every((d) => d.ddPct >= 0 && d.ddAbs >= 0), "drawdown values are non-negative");
assert(drawdown.length === equity.length, "drawdown series aligns with equity curve");

// Monthly buckets
const monthlySum = monthly.reduce((s, m) => s + m.pnl, 0);
assert(
  approx(monthlySum, sumPnl, 1e-6),
  "monthly PnL buckets sum to total net PnL",
  `${monthlySum} vs ${sumPnl}`
);
assert(monthly.length > 0, "monthly buckets produced", `${monthly.length} months`);
let sorted = true;
for (let i = 1; i < monthly.length; i++) {
  if (monthly[i - 1].month > monthly[i].month) sorted = false;
}
assert(sorted, "monthly buckets are chronologically sorted");

console.log("\n=== 2. Diversification ratio (synthetic matrices) ===");
const identity = { A: { A: 1, B: 0, C: 0 }, B: { A: 0, B: 1, C: 0 }, C: { A: 0, B: 0, C: 1 } };
const drIdentity = computeDiversificationRatio(identity);
assert(
  drIdentity !== null && approx(drIdentity, Math.sqrt(3), 1e-9),
  "orthogonal 3x3 matrix -> sqrt(3)",
  `${drIdentity}`
);
const ones = { A: { A: 1, B: 1 }, B: { A: 1, B: 1 } };
const drOnes = computeDiversificationRatio(ones);
assert(
  drOnes !== null && approx(drOnes, 1, 1e-9),
  "identical 2x2 matrix -> 1 (no diversification)",
  `${drOnes}`
);
assert(
  computeDiversificationRatio({}) === null,
  "empty matrix -> null"
);
const bad = { A: { A: "x" as unknown as number } };
assert(
  computeDiversificationRatio(bad) === null,
  "malformed matrix -> null"
);

console.log("\n=== 3. Edge cases ===");
const emptyEquity = buildEquityCurve([], 10000);
assert(emptyEquity.length === 1 && emptyEquity[0].equity === 10000, "empty trades -> single start point");
const emptyDd = buildDrawdownSeries(emptyEquity);
assert(emptyDd.length === 1 && emptyDd[0].ddPct === 0, "empty drawdown -> zero at start");
assert(buildMonthlyPnl([]).length === 0, "empty monthly buckets");

// Unsortable timestamps must not throw
const messy = parseResultJSON({
  metadata: { initial_balance: 10000 },
  metrics: {},
  trades: [
    { net_pnl: 10, entry_time: "not-a-date" },
    { net_pnl: -5, entry_time: "2024-01-01T00:00:00Z" },
  ],
});
assert(messy !== null && messy.trades.length === 2, "malformed timestamps tolerated");
const messyCurve = buildEquityCurve(messy?.trades ?? [], 10000);
assert(messyCurve.length === 3, "equity curve builds despite bad timestamps");

// Malformed JSON input rejected
assert(parseResultJSON(null) === null, "null input -> null");
assert(parseResultJSON({ metadata: {}, metrics: {}, trades: [] }) === null, "no usable trades -> null");

console.log("\n=== 4. PnL histogram (Phase 2) ===");
const hist = buildPnlHistogram(trades);
assert(hist.length === 12, "histogram has 12 buckets for varied real trades", `${hist.length}`);
const histCountSum = hist.reduce((s, b) => s + b.count, 0);
assert(histCountSum === trades.length, "histogram counts cover every trade", `${histCountSum}`);
const histPnlSum = hist.reduce((s, b) => s + b.pnl, 0);
assert(
  approx(histPnlSum, sumPnl, 1e-6),
  "histogram bucket PnL sums to total net PnL",
  `${histPnlSum} vs ${sumPnl}`
);
assert(hist.every((b) => b.range.length > 0), "histogram buckets carry range labels");
assert(buildPnlHistogram([]).length === 0, "histogram of no trades -> empty");
const flat = buildPnlHistogram([
  { net_pnl: 5 },
  { net_pnl: 5 },
  { net_pnl: 5 },
] as unknown as Parameters<typeof buildPnlHistogram>[0]);
assert(flat.length === 1 && flat[0].count === 3, "constant PnL collapses to one bucket", `${flat.length} buckets`);

console.log("\n=== 5. Monte Carlo fan chart (Phase 2) ===");
assert(buildFanChart(null) === null, "null MC payload -> null fan");
assert(buildFanChart(undefined) === null, "undefined MC payload -> null fan");
assert(
  buildFanChart({} as MonteCarloPayload) === null,
  "MC payload without trade_steps -> null fan (never fabricated)"
);
const mc: MonteCarloPayload = {
  trade_steps: [1, 2, 3, 4, 5],
  p5_curve: [9500, 9400, 9300, 9200, 9100],
  p25_curve: [9900, 9850, 9800, 9750, 9700],
  p50_curve: [10000, 10100, 10200, 10300, 10400],
  p75_curve: [10100, 10300, 10500, 10700, 10900],
  p95_curve: [10200, 10600, 11000, 11400, 11800],
};
const fan = buildFanChart(mc);
assert(fan !== null && fan.length === 5, "fan has one point per trade step", `${fan?.length}`);
const ordered = (fan ?? []).every(
  (p) =>
    (p.p5 ?? -Infinity) <= (p.p25 ?? Infinity) &&
    (p.p25 ?? -Infinity) <= (p.p50 ?? Infinity) &&
    (p.p50 ?? -Infinity) <= (p.p75 ?? Infinity) &&
    (p.p75 ?? -Infinity) <= (p.p95 ?? Infinity)
);
assert(ordered, "percentiles stay ordered (P5 <= P25 <= P50 <= P75 <= P95)");
assert(
  (fan ?? []).every((p, i) => p.label === `T+${mc.trade_steps![i]}`),
  "fan points are labeled by trade step"
);
const partialMc: MonteCarloPayload = {
  trade_steps: [1, 2],
  p50_curve: [10000, 10100],
};
const partialFan = buildFanChart(partialMc);
assert(
  partialFan !== null &&
    partialFan.length === 2 &&
    partialFan[0].p5 === undefined &&
    partialFan[1].p50 === 10100,
  "partial percentile curves tolerated (missing keys -> undefined)"
);

console.log("\n=== 6. Result catalog matching + correlation (Phase 3) ===");
// normalizeKey: punctuation/case tolerant
assert(normalizeKey("SunriseOgle_XAUUSD") === normalizeKey("sunrise_ogle_xauusd"), "normalizeKey strips case/punctuation");
assert(normalizeKey(null) === "", "normalizeKey(null) -> empty string");

// summarizeResultEntry: compact metadata, no trade arrays
const summary = summarizeResultEntry(raw);
assert(summary !== null, "summarizeResultEntry accepts the real result JSON");
assert(
  summary !== null &&
    summary.strategy_name === "sunrise_ogle_xauusd" &&
    summary.engine === "python:backtrader" &&
    summary.symbol === "XAUUSD" &&
    summary.timeframe === "M5" &&
    summary.total_trades === 23,
  "summary carries engine/symbol/timeframe/trade-count metadata",
  JSON.stringify({
    strategy_name: summary?.strategy_name,
    engine: summary?.engine,
    symbol: summary?.symbol,
    timeframe: summary?.timeframe,
    total_trades: summary?.total_trades,
  })
);
assert(
  summary !== null && !("trades" in (summary as unknown as Record<string, unknown>)),
  "summary contains no trade payload"
);

// scoreResultMatch: leaderboard row -> catalog entry
const catalogEntry: ResultEntry = {
  ...(summary as unknown as ResultEntry),
  file: "sunrise_ogle_xauusd_XAUUSD_M5_result.json",
  source: "data/results",
};
const exactScore = scoreResultMatch(catalogEntry, {
  name: "SunriseOgle_XAUUSD",
  symbol: "XAUUSD",
  timeframe: "M5",
  engine: "python:backtrader",
});
assert(exactScore !== null && exactScore >= 0.9, "leaderboard row matches its catalog entry", `${exactScore}`);
assert(
  scoreResultMatch(catalogEntry, { name: "SMC_Fractal_Daemon", symbol: "EURUSD", timeframe: "H1" }) === null,
  "unrelated strategy does not match the entry"
);
const multiTf = scoreResultMatch(catalogEntry, { name: "SunriseOgle_XAUUSD", symbol: "XAUUSD", timeframe: "H1/M5" });
assert(multiTf !== null && multiTf >= 0.9, "multi-timeframe rows match when one piece agrees", `${multiTf}`);
const best = bestResultMatch([catalogEntry], { name: "SunriseOgle_XAUUSD", symbol: "XAUUSD", timeframe: "M5" });
assert(best !== null && best.entry.file === "sunrise_ogle_xauusd_XAUUSD_M5_result.json", "bestResultMatch picks the right entry");
assert(bestResultMatch([], { name: "X", symbol: "Y", timeframe: "Z" }) === null, "empty catalog -> no match");

// computePairwiseCorrelation: statistical sanity
const up = [1, 2, 3, 4, 5, 6, 7, 8];
const down = up.map((v) => -v);
const corrPos = computePairwiseCorrelation(up, up);
const corrNeg = computePairwiseCorrelation(up, down);
assert(corrPos !== null && approx(corrPos, 1, 1e-9), "identical series -> r = 1", `${corrPos}`);
assert(corrNeg !== null && approx(corrNeg, -1, 1e-9), "inverted series -> r = -1", `${corrNeg}`);
assert(computePairwiseCorrelation([1, 2], [3, 4]) === null, "fewer than 3 points -> null");
assert(computePairwiseCorrelation([5, 5, 5, 5], [1, 2, 3, 4]) === null, "zero-variance series -> null");

// monthlyPnlMap aligns real monthly buckets
const realMap = monthlyPnlMap(monthly);
assert(realMap.size === monthly.length, "monthlyPnlMap keys every month", `${realMap.size}`);
const pnlFromMap = [...realMap.values()].reduce((s, v) => s + v, 0);
assert(approx(pnlFromMap, sumPnl, 1e-6), "monthlyPnlMap preserves total PnL", `${pnlFromMap} vs ${sumPnl}`);

console.log(`\n=== RESULT: ${checks - failures}/${checks} checks passed ===`);
if (failures > 0) {
  console.log(`${failures} FAILURE(S) — phases NOT confirmed`);
  process.exit(1);
} else {
  console.log("PHASES 1+2+3 CONFIRMED: series builders, fan charts, and catalog matching verified");
}
