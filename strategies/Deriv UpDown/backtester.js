#!/usr/bin/env node
// backtester.js — V100 Momentum Continuation Strategy Backtester
//
// Strategy: Short-term momentum continuation
//   CALL: 10 EMA > 30 EMA, RSI 50–70, new local high in last 5 candles
//   PUT:  10 EMA < 30 EMA, RSI 30–50, new local low  in last 5 candles
//
// Usage:
//   node backtester.js                     (defaults: 5min candles, 2 batches, +1 candle ahead)
//   node backtester.js --gran 60           (1-min candles)
//   node backtester.js --batches 3         (3x5000 candles ≈ 52 days at 5min)
//   node backtester.js --ahead 1,3,5,10    (test multiple look-ahead durations)

const fs = require('fs');
const path = require('path');
const { fetchCandleHistory } = require('./lib/data');
const { computeEMA, computeRSI, isNewLocalHigh, isNewLocalLow } = require('./lib/indicators');

// ── Load .env ──────────────────────────────────────────────────────────────────
const env = Object.fromEntries(
  fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
    .split('\n')
    .filter(l => l && !l.startsWith('#') && l.includes('='))
    .map(l => { const i = l.indexOf('='); return [l.slice(0,i).trim(), l.slice(i+1).trim()]; })
);

// ── Parse CLI args ─────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const getArg = (name, def) => {
  const i = args.indexOf(name);
  return i !== -1 ? args[i + 1] : def;
};

const SYMBOL      = env.SYMBOL      || '1HZ100V';
const GRAN        = parseInt(getArg('--gran',    env.CANDLE_GRANULARITY || '300'));
const BATCHES     = parseInt(getArg('--batches', '2'));
const AHEAD_STR   = getArg('--ahead', '1,3,5');
const AHEAD_LIST  = AHEAD_STR.split(',').map(Number);

const EMA_FAST    = parseInt(env.EMA_FAST   || '10');
const EMA_SLOW    = parseInt(env.EMA_SLOW   || '30');
const RSI_PERIOD  = parseInt(env.RSI_PERIOD || '14');
const RSI_MIN_L   = parseFloat(env.RSI_MIN_LONG  || '50');
const RSI_MAX_L   = parseFloat(env.RSI_MAX_LONG  || '70');
const RSI_MIN_S   = parseFloat(env.RSI_MIN_SHORT || '30');
const RSI_MAX_S   = parseFloat(env.RSI_MAX_SHORT || '50');
const LOOKBACK    = 5; // local high/low lookback

const GRANULARITY_LABEL = { 60: '1m', 120: '2m', 300: '5m', 900: '15m', 3600: '1h' };
const gran_label = GRANULARITY_LABEL[GRAN] || `${GRAN}s`;

// ── Helpers ────────────────────────────────────────────────────────────────────
const fmt = (n, d = 2) => n.toFixed(d);
const pct = (n) => `${(n * 100).toFixed(1)}%`;
const ts  = (epoch) => new Date(epoch * 1000).toISOString().replace('T', ' ').slice(0, 16);

function printSeparator(char = '─', len = 60) { console.log(char.repeat(len)); }

// ── Main ───────────────────────────────────────────────────────────────────────
(async () => {
  console.log('');
  console.log('╔══════════════════════════════════════════════════════════╗');
  console.log('║       V100 Momentum Continuation — Backtester            ║');
  console.log('╚══════════════════════════════════════════════════════════╝');
  console.log('');
  console.log(`  Symbol:      ${SYMBOL}`);
  console.log(`  Timeframe:   ${gran_label} candles (granularity ${GRAN}s)`);
  console.log(`  Batches:     ${BATCHES} × 5000 = up to ${BATCHES * 5000} candles`);
  console.log(`  EMA fast:    ${EMA_FAST}   EMA slow: ${EMA_SLOW}`);
  console.log(`  RSI period:  ${RSI_PERIOD}  Long zone: ${RSI_MIN_L}–${RSI_MAX_L}  Short zone: ${RSI_MIN_S}–${RSI_MAX_S}`);
  console.log(`  Look-ahead:  ${AHEAD_LIST.map(a => `${a} candle${a > 1 ? 's' : ''}`).join(', ')}`);
  console.log('');

  // ── 1. Fetch data ────────────────────────────────────────────────────────────
  let candles;
  try {
    candles = await fetchCandleHistory(SYMBOL, GRAN, BATCHES);
  } catch (e) {
    console.error('❌ Failed to fetch candles:', e.message);
    process.exit(1);
  }

  const N = candles.length;
  if (N < 100) { console.error('❌ Not enough candles for analysis'); process.exit(1); }

  const dateFrom = ts(candles[0].epoch);
  const dateTo   = ts(candles[N - 1].epoch);
  const durationDays = ((candles[N-1].epoch - candles[0].epoch) / 86400).toFixed(1);

  console.log(`\n✅ ${N.toLocaleString()} candles loaded`);
  console.log(`   From: ${dateFrom}`);
  console.log(`   To:   ${dateTo}`);
  console.log(`   Span: ~${durationDays} days`);

  // ── 2. Compute indicators ────────────────────────────────────────────────────
  const closes = candles.map(c => c.close);
  const ema10  = computeEMA(closes, EMA_FAST);
  const ema30  = computeEMA(closes, EMA_SLOW);
  const rsi14  = computeRSI(closes, RSI_PERIOD);

  // First valid index (all 3 indicators available)
  const warmup = Math.max(EMA_SLOW - 1, RSI_PERIOD);

  // ── 3. Detect signals ────────────────────────────────────────────────────────
  const signals = [];
  for (let i = warmup; i < N - Math.max(...AHEAD_LIST); i++) {
    const e10 = ema10[i], e30 = ema30[i], r = rsi14[i];
    if (e10 === null || e30 === null || r === null) continue;

    let type = null;
    if (e10 > e30 && r >= RSI_MIN_L && r <= RSI_MAX_L && isNewLocalHigh(candles, i, LOOKBACK)) {
      type = 'CALL';
    } else if (e10 < e30 && r >= RSI_MIN_S && r <= RSI_MAX_S && isNewLocalLow(candles, i, LOOKBACK)) {
      type = 'PUT';
    }

    if (type) {
      signals.push({
        i, type,
        epoch: candles[i].epoch,
        entry: candles[i].close,
        ema10: e10, ema30: e30, rsi: r,
      });
    }
  }

  console.log(`\n📊 Signals detected: ${signals.length}`);
  console.log(`   CALL: ${signals.filter(s => s.type === 'CALL').length}`);
  console.log(`   PUT:  ${signals.filter(s => s.type === 'PUT').length}`);

  if (signals.length === 0) {
    console.log('\n⚠️  No signals found. Try adjusting RSI ranges or checking timeframe.');
    process.exit(0);
  }

  // ── 4. Evaluate each look-ahead duration ─────────────────────────────────────
  console.log('');
  printSeparator('═');
  console.log('  BACKTEST RESULTS');
  printSeparator('═');

  const BREAKEVEN = 0.52;
  const resultsForCSV = [];

  for (const ahead of AHEAD_LIST) {
    const label = `+${ahead} candle${ahead > 1 ? 's' : ''} (${gran_label})`;

    const evaluated = signals.filter(s => s.i + ahead < N).map(s => {
      const exitPrice = candles[s.i + ahead].close;
      const win = s.type === 'CALL' ? exitPrice > s.entry : exitPrice < s.entry;
      return { ...s, exitPrice, win };
    });

    if (evaluated.length === 0) continue;

    const total  = evaluated.length;
    const wins   = evaluated.filter(e => e.win).length;
    const losses = total - wins;
    const wr     = wins / total;

    const calls      = evaluated.filter(e => e.type === 'CALL');
    const callWins   = calls.filter(e => e.win).length;
    const puts       = evaluated.filter(e => e.type === 'PUT');
    const putWins    = puts.filter(e => e.win).length;

    // Max consecutive losses
    let maxConsecLoss = 0, streak = 0;
    for (const e of evaluated) {
      if (!e.win) { streak++; maxConsecLoss = Math.max(maxConsecLoss, streak); }
      else streak = 0;
    }

    const beatingBreakeven = wr > BREAKEVEN;

    console.log('');
    console.log(`  Duration: ${label}`);
    printSeparator('─');
    console.log(`  Total signals :  ${total}`);
    console.log(`  Wins          :  ${wins}  (${pct(wr)})`);
    console.log(`  Losses        :  ${losses}`);
    console.log(`  CALL win rate :  ${calls.length > 0 ? pct(callWins / calls.length) : 'N/A'}  (${callWins}/${calls.length})`);
    console.log(`  PUT  win rate :  ${puts.length > 0 ? pct(putWins / puts.length) : 'N/A'}  (${putWins}/${puts.length})`);
    console.log(`  Max consec.   :  ${maxConsecLoss} losses in a row`);
    console.log(`  Breakeven     :  ${pct(BREAKEVEN)}  →  ${beatingBreakeven ? '✅ STRATEGY IS PROFITABLE' : '❌ BELOW BREAKEVEN'}`);

    // Store for CSV
    evaluated.forEach(e => {
      resultsForCSV.push({
        epoch: e.epoch,
        date: ts(e.epoch),
        type: e.type,
        entry: e.entry,
        exitPrice: e.exitPrice,
        win: e.win ? 1 : 0,
        ema10: fmt(e.ema10),
        ema30: fmt(e.ema30),
        rsi: fmt(e.rsi),
        ahead,
      });
    });
  }

  // ── 5. Sample signals ────────────────────────────────────────────────────────
  console.log('');
  printSeparator('═');
  console.log('  SAMPLE SIGNALS (first 10)');
  printSeparator('─');
  console.log('  Date (UTC)       | Type | Entry    | EMA10   | EMA30   | RSI   ');
  printSeparator('─');
  signals.slice(0, 10).forEach(s => {
    console.log(
      `  ${ts(s.epoch)}  | ${s.type.padEnd(4)} | ${fmt(s.entry, 2).padStart(8)} | ${fmt(s.ema10, 2).padStart(7)} | ${fmt(s.ema30, 2).padStart(7)} | ${fmt(s.rsi, 1).padStart(5)}`
    );
  });

  // ── 6. Save CSV ───────────────────────────────────────────────────────────────
  const csvPath = path.join(__dirname, 'backtest_results.csv');
  const headers = ['epoch','date','type','entry','exitPrice','win','ema10','ema30','rsi','ahead'];
  const csvLines = [
    headers.join(','),
    ...resultsForCSV.map(r => headers.map(h => r[h]).join(','))
  ];
  fs.writeFileSync(csvPath, csvLines.join('\n'));

  console.log('');
  printSeparator('═');
  console.log(`\n💾 Full results saved → backtest_results.csv  (${resultsForCSV.length} rows)`);
  console.log('\n✅ Backtest complete.\n');
})();
