/**
 * Phase 3 verification — result-file catalog against the real filesystem.
 *
 * Exercises the server-side enumerator (listResultEntries / resolveResultFile)
 * and the correlation pipeline exactly as /api/results and /api/leaderboard
 * use them. Run from the sim/ directory:
 *
 *   npx tsx scripts/verify_catalog.ts
 */

import fs from "fs";
import { listResultEntries, resolveResultFile } from "../src/lib/resultFiles";
import {
  buildMonthlyPnl,
  computePairwiseCorrelation,
  monthlyPnlMap,
  parseResultJSON,
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

console.log("\n=== Result file catalog (real filesystem) ===");
const { entries, skipped } = listResultEntries();
assert(entries.length >= 1, "catalog finds at least one real result file", `${entries.length} entries`);
assert(entries.every((e) => e.total_trades > 0), "every entry summarizes a non-empty trade list");
assert(entries.every((e) => e.file && e.source), "every entry carries file + source");
assert(Array.isArray(skipped), "skipped list is an array (may be empty)");

const sunrise = entries.find((e) => e.strategy_name === "sunrise_ogle_xauusd");
assert(sunrise !== undefined, "catalog contains the sunrise_ogle result", sunrise ? `${sunrise.source}/${sunrise.file}` : "not found");
if (sunrise) {
  assert(sunrise.total_trades === 23, "catalog trade count matches engine output", `${sunrise.total_trades}`);
  assert(sunrise.engine === "python:backtrader", "catalog engine matches result metadata", sunrise.engine);
  assert(sunrise.symbol === "XAUUSD" && sunrise.timeframe === "M5", "catalog symbol/timeframe match", `${sunrise.symbol} ${sunrise.timeframe}`);
  const full = resolveResultFile(sunrise);
  assert(full !== null && fs.existsSync(full), "resolveResultFile yields a readable path", full ?? "null");
  if (full) {
    const raw = JSON.parse(fs.readFileSync(full, "utf-8"));
    const parsed = parseResultJSON(raw);
    assert(parsed !== null && parsed.trades.length === 23, "resolved file re-parses to the same 23 trades", `${parsed?.trades.length}`);
  }
}

console.log("\n=== Correlation pipeline (as /api/leaderboard uses it) ===");
// Replicate realCorrelation(): series of monthly PnL per entry.
const series = entries
  .map((e) => {
    const full = resolveResultFile(e);
    if (!full) return null;
    try {
      const raw = JSON.parse(fs.readFileSync(full, "utf-8"));
      if (!Array.isArray(raw?.trades) || raw.trades.length === 0) return null;
      return { name: e.strategy_name || e.file, months: monthlyPnlMap(buildMonthlyPnl(raw.trades)) };
    } catch {
      return null;
    }
  })
  .filter((s): s is { name: string; months: Map<string, number> } => s !== null);

assert(series.length >= 2, "pipeline produces at least two real series", `${series.length} series`);

// Duplicate copies of the same strategy (data/results + test_results) must
// correlate perfectly with each other — they are the same trades.
const groups = new Map<string, typeof series>();
for (const s of series) {
  groups.set(s.name, [...(groups.get(s.name) ?? []), s]);
}
const dupes = [...groups.values()].filter((g) => g.length > 1);
assert(dupes.length >= 1, "same strategy appears under both sources (expected)", `${dupes.length} duplicate groups`);
let dupesPerfect = true;
for (const g of dupes) {
  const [a, b] = g;
  const shared = [...a.months.entries()].filter(([k]) => b.months.has(k));
  const r = computePairwiseCorrelation(
    shared.map(([, v]) => v),
    shared.map(([k]) => b.months.get(k) ?? 0)
  );
  if (r === null || Math.abs(r - 1) > 1e-9) dupesPerfect = false;
}
assert(dupesPerfect, "duplicate source copies correlate at r = 1", dupesPerfect ? "" : "mismatch found");

console.log(`\n=== RESULT: ${checks - failures}/${checks} checks passed ===`);
if (failures > 0) {
  console.log(`${failures} FAILURE(S) — catalog NOT confirmed`);
  process.exit(1);
} else {
  console.log("CATALOG CONFIRMED: real result files enumerate, resolve, and correlate correctly");
}
