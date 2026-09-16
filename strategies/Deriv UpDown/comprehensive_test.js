#!/usr/bin/env node
// comprehensive_test.js
// Tests all Deriv volatility indices × all timeframes with the momentum strategy
// Outputs: market_research_report.md + full_results.csv

const fs   = require('fs');
const path = require('path');
const { fetchCandleHistory } = require('./lib/data');
const { computeEMA, computeRSI, isNewLocalHigh, isNewLocalLow } = require('./lib/indicators');

// ── Strategy config (fixed) ────────────────────────────────────────────────────
const EMA_FAST   = 10;
const EMA_SLOW   = 30;
const RSI_PERIOD = 14;
const RSI_MIN_L  = 50; RSI_MAX_L = 70;
const RSI_MIN_S  = 30; RSI_MAX_S = 50;
const LOOKBACK   = 5;
const AHEAD_LIST = [1, 3, 5, 10];
const BATCHES    = 1;   // 1×5000 per symbol/timeframe to keep runtime manageable
const BREAKEVEN  = 0.52;

// ── Test matrix ────────────────────────────────────────────────────────────────
const SYMBOLS = [
  { symbol: 'R_10',    name: 'Volatility 10 Index',      volatility: 10 },
  { symbol: 'R_25',    name: 'Volatility 25 Index',      volatility: 25 },
  { symbol: 'R_50',    name: 'Volatility 50 Index',      volatility: 50 },
  { symbol: 'R_75',    name: 'Volatility 75 Index',      volatility: 75 },
  { symbol: 'R_100',   name: 'Volatility 100 Index',     volatility: 100 },
  { symbol: '1HZ10V',  name: 'Volatility 10 (1s) Index', volatility: 10, fast: true },
  { symbol: '1HZ25V',  name: 'Volatility 25 (1s) Index', volatility: 25, fast: true },
  { symbol: '1HZ50V',  name: 'Volatility 50 (1s) Index', volatility: 50, fast: true },
  { symbol: '1HZ75V',  name: 'Volatility 75 (1s) Index', volatility: 75, fast: true },
  { symbol: '1HZ100V', name: 'Volatility 100 (1s) Index',volatility: 100,fast: true },
];

const TIMEFRAMES = [
  { gran: 60,   label: '1m'  },
  { gran: 300,  label: '5m'  },
  { gran: 900,  label: '15m' },
  { gran: 3600, label: '1h'  },
];

// ── Helpers ────────────────────────────────────────────────────────────────────
const fmt2   = n => typeof n === 'number' ? n.toFixed(2) : 'N/A';
const fmt1   = n => typeof n === 'number' ? n.toFixed(1) : 'N/A';
const pct    = n => typeof n === 'number' ? `${(n * 100).toFixed(1)}%` : 'N/A';
const ts     = e => new Date(e * 1000).toISOString().replace('T', ' ').slice(0, 16);
const pad    = (s, n) => String(s).padEnd(n);
const padL   = (s, n) => String(s).padStart(n);
const sep    = (c, n) => c.repeat(n);

// ── Backtest one combination ───────────────────────────────────────────────────
async function runBacktest(symbol, gran) {
  let candles;
  try {
    candles = await fetchCandleHistory(symbol, gran, BATCHES);
  } catch (e) {
    return { error: e.message };
  }

  const N = candles.length;
  if (N < 100) return { error: `Only ${N} candles — insufficient` };

  const closes = candles.map(c => c.close);
  const ema10  = computeEMA(closes, EMA_FAST);
  const ema30  = computeEMA(closes, EMA_SLOW);
  const rsi14  = computeRSI(closes, RSI_PERIOD);
  const warmup = Math.max(EMA_SLOW - 1, RSI_PERIOD);

  const signals = [];
  for (let i = warmup; i < N - Math.max(...AHEAD_LIST); i++) {
    const e10 = ema10[i], e30 = ema30[i], r = rsi14[i];
    if (e10 === null || e30 === null || r === null) continue;
    let type = null;
    if (e10 > e30 && r >= RSI_MIN_L && r <= RSI_MAX_L && isNewLocalHigh(candles, i, LOOKBACK)) type = 'CALL';
    else if (e10 < e30 && r >= RSI_MIN_S && r <= RSI_MAX_S && isNewLocalLow(candles, i, LOOKBACK)) type = 'PUT';
    if (type) signals.push({ i, type, epoch: candles[i].epoch, entry: candles[i].close, ema10: e10, ema30: e30, rsi: r });
  }

  const spanDays = (candles[N-1].epoch - candles[0].epoch) / 86400;
  const signalsPerDay = signals.length / spanDays;

  const aheadResults = {};
  for (const ahead of AHEAD_LIST) {
    const ev = signals.filter(s => s.i + ahead < N).map(s => {
      const exitPrice = candles[s.i + ahead].close;
      const win = s.type === 'CALL' ? exitPrice > s.entry : exitPrice < s.entry;
      return { ...s, exitPrice, win };
    });

    const total    = ev.length;
    const wins     = ev.filter(e => e.win).length;
    const calls    = ev.filter(e => e.type === 'CALL');
    const puts     = ev.filter(e => e.type === 'PUT');
    const callWr   = calls.length > 0 ? calls.filter(e => e.win).length / calls.length : null;
    const putWr    = puts.length  > 0 ? puts.filter(e => e.win).length  / puts.length  : null;
    const wr       = total > 0 ? wins / total : null;
    let maxLoss = 0, streak = 0;
    ev.forEach(e => { if (!e.win) { streak++; maxLoss = Math.max(maxLoss, streak); } else streak = 0; });

    aheadResults[ahead] = { total, wins, wr, callWr, putWr, maxLoss };
  }

  // Best ahead by win rate
  const best = AHEAD_LIST
    .filter(a => aheadResults[a].wr !== null)
    .sort((a, b) => (aheadResults[b].wr || 0) - (aheadResults[a].wr || 0))[0];

  return {
    candles: N,
    spanDays: parseFloat(spanDays.toFixed(1)),
    dateFrom: ts(candles[0].epoch),
    dateTo:   ts(candles[N-1].epoch),
    totalSignals: signals.length,
    signalsPerDay: parseFloat(signalsPerDay.toFixed(1)),
    callSignals: signals.filter(s => s.type === 'CALL').length,
    putSignals:  signals.filter(s => s.type === 'PUT').length,
    aheadResults,
    bestAhead: best,
    bestWr:    best !== undefined ? aheadResults[best].wr : null,
  };
}

// ── Main ───────────────────────────────────────────────────────────────────────
(async () => {
  const startTime = Date.now();
  console.log('\n' + sep('═', 65));
  console.log('  COMPREHENSIVE VOLATILITY INDEX MARKET RESEARCH');
  console.log(sep('═', 65));
  console.log(`  Testing ${SYMBOLS.length} symbols × ${TIMEFRAMES.length} timeframes = ${SYMBOLS.length * TIMEFRAMES.length} combinations`);
  console.log(`  Strategy: EMA(${EMA_FAST}/${EMA_SLOW}) + RSI(${RSI_PERIOD}) momentum continuation`);
  console.log(`  Look-ahead durations: ${AHEAD_LIST.map(a => `+${a}`).join(', ')} candles`);
  console.log(sep('═', 65) + '\n');

  const allResults = [];
  let done = 0;
  const total = SYMBOLS.length * TIMEFRAMES.length;

  for (const sym of SYMBOLS) {
    for (const tf of TIMEFRAMES) {
      done++;
      process.stdout.write(`  [${String(done).padStart(2)}/${total}] ${pad(sym.symbol, 9)} ${pad(tf.label, 4)}... `);
      const result = await runBacktest(sym.symbol, tf.gran);
      if (result.error) {
        console.log(`⚠️  ${result.error}`);
      } else {
        const wr = result.bestWr;
        const flag = wr !== null && wr >= BREAKEVEN ? '✅' : wr !== null && wr >= 0.50 ? '〰️' : '❌';
        console.log(`${flag}  ${result.totalSignals} signals | best WR: ${pct(wr)} at +${result.bestAhead} candles`);
      }
      allResults.push({ symbol: sym.symbol, name: sym.name, volatility: sym.volatility, fast: !!sym.fast, gran: tf.gran, label: tf.label, ...result });
      await new Promise(r => setTimeout(r, 300)); // gentle rate limiting
    }
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
  console.log(`\n  Finished in ${elapsed}s. Generating report...\n`);

  // ── Filter valid results ───────────────────────────────────────────────────
  const valid = allResults.filter(r => !r.error && r.totalSignals >= 30 && r.bestWr !== null);
  valid.sort((a, b) => (b.bestWr || 0) - (a.bestWr || 0));

  const profitable = valid.filter(r => r.bestWr >= BREAKEVEN);
  const near       = valid.filter(r => r.bestWr >= 0.50 && r.bestWr < BREAKEVEN);
  const losing     = valid.filter(r => r.bestWr < 0.50);

  // ── Generate Report ────────────────────────────────────────────────────────
  const now = new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  let md = '';

  md += `# Deriv Volatility Index — Market Research Report\n`;
  md += `\n> **Generated:** ${now}  \n`;
  md += `> **Strategy:** EMA(${EMA_FAST}/${EMA_SLOW}) + RSI(${RSI_PERIOD}) Momentum Continuation  \n`;
  md += `> **Breakeven Win Rate:** ${pct(BREAKEVEN)} (based on ~95% payout)  \n`;
  md += `> **Data:** ~${Math.round(BATCHES * 5000 * 300 / 86400)} days per symbol at 5m equivalent  \n`;
  md += `> **Combinations tested:** ${SYMBOLS.length} symbols × ${TIMEFRAMES.length} timeframes = ${SYMBOLS.length * TIMEFRAMES.length}\n\n`;
  md += `---\n\n`;

  // ── 1. Executive Summary ───────────────────────────────────────────────────
  md += `## 1. Executive Summary\n\n`;
  md += `| Category | Count |\n|----------|-------|\n`;
  md += `| ✅ Profitable combinations (WR ≥ 52%) | **${profitable.length}** |\n`;
  md += `| 〰️ Near breakeven (50–52%) | ${near.length} |\n`;
  md += `| ❌ Below 50% | ${losing.length} |\n`;
  md += `| ⚠️ Skipped (error / insufficient data) | ${allResults.length - valid.length} |\n\n`;

  if (profitable.length > 0) {
    const top = profitable[0];
    md += `> **Top performer:** \`${top.symbol}\` on \`${top.label}\` candles — **${pct(top.bestWr)}** win rate at +${top.bestAhead} candle look-ahead (${top.totalSignals} signals over ${top.spanDays} days)\n\n`;
  } else if (near.length > 0) {
    const top = near[0];
    md += `> **Best near-breakeven:** \`${top.symbol}\` on \`${top.label}\` candles — **${pct(top.bestWr)}** win rate — just below breakeven, consider parameter tuning.\n\n`;
  } else {
    md += `> ⚠️ No combination beat the ${pct(BREAKEVEN)} breakeven threshold with the current parameters. Parameter tuning recommended.\n\n`;
  }

  md += `---\n\n`;

  // ── 2. Full Rankings Table ─────────────────────────────────────────────────
  md += `## 2. Full Rankings — All Combinations\n\n`;
  md += `Sorted by best win rate across all look-ahead durations.\n\n`;
  md += `| Rank | Symbol | Name | TF | Signals | Sig/Day | Best WR | @Ahead | CALL WR | PUT WR | Max Loss Streak | Status |\n`;
  md += `|------|--------|------|----|---------|---------|---------|--------|---------|--------|-----------------|--------|\n`;

  valid.forEach((r, idx) => {
    const ar = r.aheadResults[r.bestAhead];
    const status = r.bestWr >= BREAKEVEN ? '✅ Profitable' : r.bestWr >= 0.50 ? '〰️ Near' : '❌ Below';
    md += `| ${idx+1} | \`${r.symbol}\` | ${r.name} | ${r.label} | ${r.totalSignals} | ${r.signalsPerDay} | **${pct(r.bestWr)}** | +${r.bestAhead} | ${pct(ar.callWr)} | ${pct(ar.putWr)} | ${ar.maxLoss} | ${status} |\n`;
  });

  md += `\n---\n\n`;

  // ── 3. Results by Symbol ───────────────────────────────────────────────────
  md += `## 3. Results by Volatility Index\n\n`;
  for (const sym of SYMBOLS) {
    const symResults = valid.filter(r => r.symbol === sym.symbol).sort((a,b) => (b.bestWr||0)-(a.bestWr||0));
    if (symResults.length === 0) { continue; }
    const bestForSym = symResults[0];
    md += `### ${sym.name} (\`${sym.symbol}\`)\n\n`;
    md += `**Best timeframe:** \`${bestForSym.label}\` — ${pct(bestForSym.bestWr)} win rate at +${bestForSym.bestAhead} candles\n\n`;
    md += `| Timeframe | Signals | Sig/Day | +1 WR | +3 WR | +5 WR | +10 WR | Best |\n`;
    md += `|-----------|---------|---------|-------|-------|-------|--------|------|\n`;
    TIMEFRAMES.forEach(tf => {
      const r = valid.find(x => x.symbol === sym.symbol && x.gran === tf.gran);
      if (!r) { md += `| ${tf.label} | — | — | — | — | — | — | — |\n`; return; }
      const wrs = AHEAD_LIST.map(a => r.aheadResults[a] ? pct(r.aheadResults[a].wr) : '—');
      const bestMark = `**${pct(r.bestWr)}** (+${r.bestAhead})`;
      md += `| ${tf.label} | ${r.totalSignals} | ${r.signalsPerDay} | ${wrs[0]} | ${wrs[1]} | ${wrs[2]} | ${wrs[3]} | ${bestMark} |\n`;
    });
    md += '\n';

    // Detail on best timeframe
    const ar = bestForSym.aheadResults[bestForSym.bestAhead];
    md += `**Best look-ahead detail (+${bestForSym.bestAhead} candles on ${bestForSym.label}):**\n`;
    md += `- CALL win rate: ${pct(ar.callWr)} (${bestForSym.callSignals} CALL signals)\n`;
    md += `- PUT win rate: ${pct(ar.putWr)} (${bestForSym.putSignals} PUT signals)\n`;
    md += `- Max consecutive losses: ${ar.maxLoss}\n`;
    md += `- Data: ${bestForSym.dateFrom} → ${bestForSym.dateTo} (${bestForSym.spanDays} days)\n\n`;
  }

  md += `---\n\n`;

  // ── 4. Results by Timeframe ────────────────────────────────────────────────
  md += `## 4. Results by Timeframe\n\n`;
  md += `Average win rate across all symbols for each timeframe:\n\n`;
  md += `| Timeframe | Avg Win Rate | Profitable Symbols | Best Symbol | Best WR |\n`;
  md += `|-----------|-------------|-------------------|-------------|--------|\n`;
  for (const tf of TIMEFRAMES) {
    const tfRes = valid.filter(r => r.gran === tf.gran);
    if (tfRes.length === 0) { md += `| ${tf.label} | — | — | — | — |\n`; continue; }
    const avgWr = tfRes.reduce((s, r) => s + (r.bestWr || 0), 0) / tfRes.length;
    const profCount = tfRes.filter(r => r.bestWr >= BREAKEVEN).length;
    const best = tfRes.sort((a,b) => (b.bestWr||0)-(a.bestWr||0))[0];
    md += `| **${tf.label}** | ${pct(avgWr)} | ${profCount}/${tfRes.length} | \`${best.symbol}\` | ${pct(best.bestWr)} |\n`;
  }
  md += '\n---\n\n';

  // ── 5. CALL vs PUT Analysis ────────────────────────────────────────────────
  md += `## 5. CALL vs PUT Signal Analysis\n\n`;
  md += `Which direction wins more consistently?\n\n`;
  md += `| Symbol | TF | Best CALL WR | Best PUT WR | Dominant |\n`;
  md += `|--------|----|-------------|------------|----------|\n`;
  valid.forEach(r => {
    const callWrs = AHEAD_LIST.map(a => r.aheadResults[a]?.callWr || 0);
    const putWrs  = AHEAD_LIST.map(a => r.aheadResults[a]?.putWr  || 0);
    const bestCall = Math.max(...callWrs);
    const bestPut  = Math.max(...putWrs);
    const dom = bestCall > bestPut ? '📈 CALL' : bestPut > bestCall ? '📉 PUT' : '↔️ Equal';
    md += `| \`${r.symbol}\` | ${r.label} | ${pct(bestCall)} | ${pct(bestPut)} | ${dom} |\n`;
  });
  md += '\n---\n\n';

  // ── 6. Top 5 Detailed Breakdown ────────────────────────────────────────────
  md += `## 6. Top 5 Combinations — Detailed Analysis\n\n`;
  const top5 = valid.slice(0, 5);
  top5.forEach((r, idx) => {
    md += `### ${idx+1}. ${r.name} — ${r.label} candles\n\n`;
    md += `| Metric | Value |\n|--------|-------|\n`;
    md += `| Symbol | \`${r.symbol}\` |\n`;
    md += `| Timeframe | ${r.label} candles (${r.gran}s granularity) |\n`;
    md += `| Data range | ${r.dateFrom} → ${r.dateTo} |\n`;
    md += `| Span | ${r.spanDays} days |\n`;
    md += `| Total signals | ${r.totalSignals} |\n`;
    md += `| Signal frequency | ${r.signalsPerDay}/day |\n`;
    md += `| CALL signals | ${r.callSignals} |\n`;
    md += `| PUT signals | ${r.putSignals} |\n\n`;
    md += `**Win rates by look-ahead duration:**\n\n`;
    md += `| Look-Ahead | Total | Win Rate | CALL WR | PUT WR | Max Consec. Loss |\n`;
    md += `|-----------|-------|----------|---------|--------|------------------|\n`;
    AHEAD_LIST.forEach(a => {
      const ar = r.aheadResults[a];
      if (!ar) return;
      const flag = ar.wr >= BREAKEVEN ? '✅' : ar.wr >= 0.50 ? '〰️' : '❌';
      md += `| +${a} candle${a>1?'s':''} | ${ar.total} | ${flag} **${pct(ar.wr)}** | ${pct(ar.callWr)} | ${pct(ar.putWr)} | ${ar.maxLoss} |\n`;
    });
    md += '\n';
  });

  md += `---\n\n`;

  // ── 7. Bottom 5 — What to Avoid ───────────────────────────────────────────
  md += `## 7. Worst Performers — What to Avoid\n\n`;
  const bottom5 = [...valid].reverse().slice(0, 5);
  md += `| Rank | Symbol | TF | Best WR | Signals | Issue |\n`;
  md += `|------|--------|----|---------|---------|-------|\n`;
  bottom5.forEach((r, i) => {
    const issue = r.bestWr < 0.48 ? 'Win rate too low' : r.totalSignals < 50 ? 'Too few signals' : 'Below breakeven';
    md += `| ${valid.length - i} | \`${r.symbol}\` | ${r.label} | ${pct(r.bestWr)} | ${r.totalSignals} | ${issue} |\n`;
  });
  md += '\n---\n\n';

  // ── 8. Recommendation ─────────────────────────────────────────────────────
  md += `## 8. Recommendation — Target Market\n\n`;

  if (profitable.length > 0) {
    const rec = profitable[0];
    const ar  = rec.aheadResults[rec.bestAhead];
    md += `### ✅ Primary Recommendation\n\n`;
    md += `**${rec.name} (\`${rec.symbol}\`) on ${rec.label} candles**\n\n`;
    md += `- **Win rate:** ${pct(rec.bestWr)} at +${rec.bestAhead} candle duration\n`;
    md += `- **CALL win rate:** ${pct(ar.callWr)}\n`;
    md += `- **PUT win rate:** ${pct(ar.putWr)}\n`;
    md += `- **Signal frequency:** ${rec.signalsPerDay} signals/day\n`;
    md += `- **Max consecutive losses:** ${ar.maxLoss} — budget at least ${ar.maxLoss + 3} units per session\n\n`;
    md += `**Configuration for .env:**\n\`\`\`\nSYMBOL=${rec.symbol}\nCANDLE_GRANULARITY=${rec.gran}\n\`\`\`\n\n`;

    if (profitable.length > 1) {
      md += `### 🥈 Alternative Recommendation\n\n`;
      const alt = profitable[1];
      const altAr = alt.aheadResults[alt.bestAhead];
      md += `**${alt.name} (\`${alt.symbol}\`) on ${alt.label} candles** — ${pct(alt.bestWr)} win rate  \n`;
      md += `CALL: ${pct(altAr.callWr)} | PUT: ${pct(altAr.putWr)} | ${alt.signalsPerDay} signals/day\n\n`;
    }
  } else if (near.length > 0) {
    const rec = near[0];
    md += `### 〰️ Best Available (Near Breakeven)\n\n`;
    md += `No combination cleared ${pct(BREAKEVEN)} with the current parameters, but \`${rec.symbol}\` on ${rec.label} candles came closest at **${pct(rec.bestWr)}**.\n\n`;
    md += `**Suggested next steps:**\n`;
    md += `- Tighten local high/low lookback from 5 → 10 candles (fewer but higher quality signals)\n`;
    md += `- Test PUT-only on the best performing combinations (PUT tends to outperform CALL)\n`;
    md += `- Add an ATR volatility filter (only trade when market is moving)\n`;
    md += `- Test with 3 batches of data to reduce statistical noise\n\n`;
  } else {
    md += `### ❌ No Profitable Combination Found\n\n`;
    md += `The strategy in its current form does not beat the ${pct(BREAKEVEN)} breakeven on any combination tested. Recommended actions:\n\n`;
    md += `1. Tighten entry filters (stricter RSI ranges, longer local high/low lookback)\n`;
    md += `2. Consider direction bias (PUT-only or CALL-only)\n`;
    md += `3. Add trend strength filter (require EMA spread > threshold)\n`;
    md += `4. Re-evaluate with more data (3+ batches)\n\n`;
  }

  // ── 9. Risk Summary ───────────────────────────────────────────────────────
  md += `---\n\n## 9. Risk Metrics Summary\n\n`;
  md += `| Symbol | TF | Max Consec. Loss (best) | Signals/Day | Min Bankroll (×stake) |\n`;
  md += `|--------|----|------------------------|-------------|----------------------|\n`;
  const topByRisk = [...valid].sort((a,b) => (a.aheadResults[a.bestAhead]?.maxLoss||99) - (b.aheadResults[b.bestAhead]?.maxLoss||99)).slice(0, 10);
  topByRisk.forEach(r => {
    const ar = r.aheadResults[r.bestAhead];
    const minBankroll = (ar.maxLoss + 5) * 1;
    md += `| \`${r.symbol}\` | ${r.label} | ${ar.maxLoss} | ${r.signalsPerDay} | ${minBankroll}× |\n`;
  });

  md += `\n> **Bankroll rule:** Always have at least (max consecutive losses + 5) × stake available per session.\n\n`;
  md += `---\n\n`;
  md += `*Report generated by V100 Momentum Continuation Backtester — ${now}*\n`;

  // ── Save report ────────────────────────────────────────────────────────────
  const reportPath = path.join(__dirname, 'market_research_report.md');
  fs.writeFileSync(reportPath, md);

  // ── Save CSV ───────────────────────────────────────────────────────────────
  const csvRows = [];
  csvRows.push(['symbol','name','volatility','fast','timeframe','gran','candles','spanDays','totalSignals','signalsPerDay','callSignals','putSignals','ahead1_wr','ahead3_wr','ahead5_wr','ahead10_wr','bestAhead','bestWr'].join(','));
  valid.forEach(r => {
    const wrs = AHEAD_LIST.map(a => r.aheadResults[a]?.wr != null ? r.aheadResults[a].wr.toFixed(4) : '');
    csvRows.push([r.symbol, `"${r.name}"`, r.volatility, r.fast?1:0, r.label, r.gran, r.candles, r.spanDays, r.totalSignals, r.signalsPerDay, r.callSignals, r.putSignals, ...wrs, r.bestAhead, r.bestWr?.toFixed(4)].join(','));
  });
  fs.writeFileSync(path.join(__dirname, 'full_results.csv'), csvRows.join('\n'));

  // ── Terminal summary ───────────────────────────────────────────────────────
  console.log(sep('═', 65));
  console.log('  RESEARCH COMPLETE');
  console.log(sep('═', 65));
  console.log(`  Profitable combinations: ${profitable.length}`);
  if (profitable.length > 0) {
    console.log(`  Top pick: ${profitable[0].name} (${profitable[0].label}) — ${pct(profitable[0].bestWr)}`);
  }
  console.log(`\n  📄 Report saved → market_research_report.md`);
  console.log(`  📊 Data saved   → full_results.csv`);
  console.log(sep('═', 65) + '\n');
})();
