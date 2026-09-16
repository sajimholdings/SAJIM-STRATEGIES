#!/usr/bin/env node
// validate_r25_put.js
//
// Out-of-sample validation of the R_25 PUT bias.
//
// Methodology:
//   1. Fetch data from BEFORE the original backtest window (true out-of-sample)
//   2. Use non-overlapping signal sampling to prevent evaluation window overlap
//   3. Report block bootstrap 95% CI (handles autocorrelation in time series)
//   4. Test across 5m, 15m, 1h timeframes — checking cross-timeframe consistency
//   5. PUT-only signals only (testing the directional hypothesis)
//
// Original in-sample data:
//   5m  : 2026-06-06 → 2026-06-24  (17 days)
//   15m : 2026-05-03 → 2026-06-24  (52 days)
//   1h  : 2025-11-27 → 2026-06-24  (208 days)
//
// Out-of-sample target:
//   5m  : fetch 5000 candles ending before 2026-06-06 (~17 days, May 19–Jun 5)
//   15m : fetch 5000 candles ending before 2026-05-03 (~52 days, Mar 12–May 3)
//   1h  : fetch 5000 candles ending before 2025-11-27 (~208 days, May–Nov 2025)

const fs   = require('fs');
const path = require('path');
const { fetchCandles }   = require('./lib/data');
const { computeEMA, computeRSI, isNewLocalLow } = require('./lib/indicators');
const { wilsonCI, blockBootstrap, noOverlapSignals, fmtCI } = require('./lib/stats');

// ── Strategy constants ─────────────────────────────────────────────────────────
const SYMBOL    = 'R_25';
const EMA_FAST  = 10;
const EMA_SLOW  = 30;
const RSI_MIN   = 30;
const RSI_MAX   = 50;
const LOOKBACK  = 5;
const BREAKEVEN = 0.52;
const AHEAD_LIST = [3, 5, 10]; // candles — matches in-sample best performers

// In-sample boundary epochs (start of original data for each timeframe)
// Used as the `end` epoch for fetching out-of-sample data
const OUT_OF_SAMPLE_END = {
  300:  Math.floor(new Date('2026-06-06T00:00:00Z').getTime() / 1000),  // 5m
  900:  Math.floor(new Date('2026-05-03T00:00:00Z').getTime() / 1000),  // 15m
  3600: Math.floor(new Date('2025-11-27T00:00:00Z').getTime() / 1000),  // 1h
};

const TIMEFRAMES = [
  { gran: 300,  label: '5m',  aheadLabel: '+10 candles = 50 min' },
  { gran: 900,  label: '15m', aheadLabel: '+10 candles = 2.5 hr' },
  { gran: 3600, label: '1h',  aheadLabel: '+5  candles = 5 hr'   },
];

// ── Helpers ────────────────────────────────────────────────────────────────────
const pct  = n => `${(n * 100).toFixed(1)}%`;
const ts   = e => new Date(e * 1000).toISOString().replace('T', ' ').slice(0, 16);
const sep  = (c, n) => c.repeat(n);
const fmt  = (n, d = 2) => typeof n === 'number' ? n.toFixed(d) : 'N/A';

// ── Core backtest (PUT-only, no overlap) ───────────────────────────────────────
function runPutBacktest(candles, ahead) {
  const N      = candles.length;
  const closes = candles.map(c => c.close);
  const ema10  = computeEMA(closes, EMA_FAST);
  const ema30  = computeEMA(closes, EMA_SLOW);
  const rsi14  = computeRSI(closes, 14);
  const warmup = Math.max(EMA_SLOW - 1, 14);

  // Collect ALL raw signals first
  const rawSignals = [];
  for (let i = warmup; i < N - ahead; i++) {
    const e10 = ema10[i], e30 = ema30[i], r = rsi14[i];
    if (e10 === null || e30 === null || r === null) continue;
    if (e10 < e30 && r >= RSI_MIN && r <= RSI_MAX && isNewLocalLow(candles, i, LOOKBACK)) {
      rawSignals.push({ i, epoch: candles[i].epoch, entry: candles[i].close, ema10: e10, ema30: e30, rsi: r });
    }
  }

  // Apply non-overlapping filter
  const signals = noOverlapSignals(rawSignals, ahead);

  // Evaluate
  const outcomes = signals.map(s => {
    const exitPrice = candles[s.i + ahead].close;
    return exitPrice < s.entry; // PUT wins if price goes down
  });

  const wins  = outcomes.filter(Boolean).length;
  const total = outcomes.length;

  return {
    rawSignalCount: rawSignals.length,
    noOverlapCount: total,
    wins,
    wr: total > 0 ? wins / total : null,
    ci: wilsonCI(wins, total),
    bootstrap: total >= 20 ? blockBootstrap(outcomes) : null,
    overlap_inflation: rawSignals.length > 0 ? (rawSignals.length / Math.max(1, total)).toFixed(1) : '—',
    signals,
    outcomes,
  };
}

// ── Main ───────────────────────────────────────────────────────────────────────
(async () => {
  console.log('');
  console.log(sep('═', 68));
  console.log('  R_25 PUT BIAS — OUT-OF-SAMPLE VALIDATION');
  console.log(sep('═', 68));
  console.log('  Hypothesis: R_25 has a structural PUT-side edge');
  console.log('  Method:     Non-overlapping signals + block bootstrap CI');
  console.log('  Data:       Fresh slices BEFORE original backtest window');
  console.log(sep('═', 68));
  console.log('');

  const allResults = [];

  for (const tf of TIMEFRAMES) {
    const endEpoch = OUT_OF_SAMPLE_END[tf.gran];
    console.log(`── Timeframe: ${tf.label} (out-of-sample end: ${ts(endEpoch)})`);
    console.log(`   Fetching 5000 candles before ${ts(endEpoch)}...`);

    let candles;
    try {
      candles = await fetchCandles(SYMBOL, tf.gran, 5000, endEpoch);
    } catch (e) {
      console.log(`   ❌ Fetch failed: ${e.message}\n`);
      continue;
    }

    const N = candles.length;
    const spanDays = ((candles[N-1].epoch - candles[0].epoch) / 86400).toFixed(1);
    console.log(`   ✅ Got ${N} candles | ${ts(candles[0].epoch)} → ${ts(candles[N-1].epoch)} (${spanDays} days)`);
    console.log('');

    for (const ahead of AHEAD_LIST) {
      const r = runPutBacktest(candles, ahead);

      const verdict = r.wr === null ? '—'
        : r.wr >= BREAKEVEN && (r.ci.lower >= BREAKEVEN - 0.03) ? '✅ HOLDS'
        : r.wr >= BREAKEVEN ? '〰️ Marginal'
        : '❌ Fails';

      const bsStr = r.bootstrap
        ? `boot CI: [${pct(r.bootstrap.lower)} – ${pct(r.bootstrap.upper)}]`
        : 'boot CI: too few signals';

      console.log(`   +${ahead} candles (${tf.aheadLabel.trim()}):`);
      console.log(`     Raw signals:     ${r.rawSignalCount}  →  non-overlap: ${r.noOverlapCount}  (overlap factor: ${r.overlap_inflation}×)`);
      console.log(`     Win rate:        ${r.wr !== null ? pct(r.wr) : 'N/A'}  (${r.wins}/${r.noOverlapCount})`);
      console.log(`     Wilson 95% CI:   [${fmtCI(r.ci)}]`);
      console.log(`     ${bsStr}`);
      console.log(`     Verdict:         ${verdict}`);
      console.log('');

      allResults.push({
        tf: tf.label, gran: tf.gran, ahead, spanDays,
        candles: N,
        rawSignals: r.rawSignalCount,
        noOverlapSignals: r.noOverlapCount,
        overlapFactor: r.overlap_inflation,
        wins: r.wins,
        wr: r.wr,
        wilsonLower: r.ci.lower,
        wilsonUpper: r.ci.upper,
        bootLower: r.bootstrap?.lower ?? null,
        bootUpper: r.bootstrap?.upper ?? null,
        verdict,
      });
    }
  }

  // ── Cross-timeframe summary ────────────────────────────────────────────────
  console.log(sep('═', 68));
  console.log('  CROSS-TIMEFRAME CONSISTENCY CHECK');
  console.log(sep('═', 68));
  console.log('');
  console.log('  TF   | Ahead | OOS Win Rate | Wilson CI          | Boot CI            | Verdict');
  console.log('  -----|-------|-------------|--------------------|--------------------|--------');
  allResults.forEach(r => {
    const wrStr     = r.wr !== null ? pct(r.wr).padEnd(6) : 'N/A   ';
    const wilsonStr = r.wilsonLower !== null ? `[${pct(r.wilsonLower)} – ${pct(r.wilsonUpper)}]`.padEnd(20) : 'N/A                 ';
    const bootStr   = r.bootLower   !== null ? `[${pct(r.bootLower)} – ${pct(r.bootUpper)}]`.padEnd(20) : 'too few signals     ';
    console.log(`  ${r.tf.padEnd(4)} | +${r.ahead.toString().padEnd(4)} | ${wrStr}       | ${wilsonStr} | ${bootStr} | ${r.verdict}`);
  });

  // ── Final assessment ───────────────────────────────────────────────────────
  const holdCount = allResults.filter(r => r.verdict.includes('HOLDS') || r.verdict.includes('Marginal')).length;
  const failCount = allResults.filter(r => r.verdict.includes('Fails')).length;

  console.log('');
  console.log(sep('═', 68));
  console.log('  ASSESSMENT');
  console.log(sep('═', 68));

  if (holdCount >= allResults.length * 0.6) {
    console.log('');
    console.log('  ✅ The R_25 PUT bias HOLDS out-of-sample.');
    console.log('     Cross-timeframe consistency confirmed on fresh data.');
    console.log('     This is a credible structural edge worth proceeding with.');
    console.log('');
    console.log('  Recommended next step: paper trade PUT-only on R_25 (5m)');
    console.log('  Contract duration: +10 candles = 50 minutes per trade');
  } else if (failCount >= allResults.length * 0.6) {
    console.log('');
    console.log('  ❌ The R_25 PUT bias does NOT hold out-of-sample.');
    console.log('     The in-sample results were likely statistical noise.');
    console.log('     Do not trade this signal without further investigation.');
  } else {
    console.log('');
    console.log('  〰️ Mixed result — some timeframes hold, others do not.');
    console.log('     Proceed only on timeframes where OOS result also holds.');
    console.log('     Favour longer spans (15m, 1h) for lower signal noise.');
  }

  // ── Save results ───────────────────────────────────────────────────────────
  console.log('');
  const csvHeaders = ['tf','gran','ahead','spanDays','candles','rawSignals','noOverlapSignals','overlapFactor','wins','wr','wilsonLower','wilsonUpper','bootLower','bootUpper','verdict'];
  const csvLines = [
    csvHeaders.join(','),
    ...allResults.map(r => csvHeaders.map(h => r[h] !== null && r[h] !== undefined ? r[h] : '').join(','))
  ];
  const csvPath = path.join(__dirname, 'r25_put_oos_results.csv');
  fs.writeFileSync(csvPath, csvLines.join('\n'));
  console.log(`  💾 Results saved → r25_put_oos_results.csv`);
  console.log('');
})();
