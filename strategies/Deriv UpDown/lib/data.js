// lib/data.js — Fetch V100 candle history from Deriv WebSocket

const WebSocket = require('ws');

const PUBLIC_WS = 'wss://ws.derivws.com/websockets/v3?app_id=1';

/**
 * Fetch up to `count` candles ending at `endEpoch` (or 'latest').
 * Returns array of { epoch, open, high, low, close } sorted oldest→newest.
 */
function fetchCandles(symbol, granularity, count, endEpoch = 'latest') {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(PUBLIC_WS);
    const timeout = setTimeout(() => {
      ws.terminate();
      reject(new Error('Timeout fetching candles'));
    }, 30000);

    ws.on('open', () => {
      ws.send(JSON.stringify({
        ticks_history: symbol,
        end: endEpoch,
        count,
        style: 'candles',
        granularity,
        req_id: 1
      }));
    });

    ws.on('message', (raw) => {
      const data = JSON.parse(raw);
      if (data.req_id === 1) {
        clearTimeout(timeout);
        ws.close();
        if (data.error) return reject(new Error(data.error.message));
        const candles = (data.candles || []).map(c => ({
          epoch: c.epoch,
          open: parseFloat(c.open),
          high: parseFloat(c.high),
          low: parseFloat(c.low),
          close: parseFloat(c.close),
        }));
        resolve(candles);
      }
    });

    ws.on('error', (e) => { clearTimeout(timeout); reject(e); });
  });
}

/**
 * Fetch multiple batches to get a large candle dataset.
 * Paginates backwards from 'latest'.
 * @param {string} symbol e.g. '1HZ100V'
 * @param {number} granularity seconds e.g. 300
 * @param {number} batches number of batches (each batch = 5000 candles)
 */
async function fetchCandleHistory(symbol, granularity, batches = 2) {
  const BATCH_SIZE = 5000;
  let allCandles = [];
  let endEpoch = 'latest';

  process.stdout.write(`Fetching candle history (${batches} batch${batches > 1 ? 'es' : ''} of ${BATCH_SIZE})...\n`);

  for (let b = 0; b < batches; b++) {
    process.stdout.write(`  Batch ${b + 1}/${batches}... `);
    const candles = await fetchCandles(symbol, granularity, BATCH_SIZE, endEpoch);
    if (candles.length === 0) break;

    allCandles = [...candles, ...allCandles];
    endEpoch = candles[0].epoch - 1; // go further back
    process.stdout.write(`got ${candles.length} candles\n`);

    // Small delay to avoid hammering the API
    if (b < batches - 1) await new Promise(r => setTimeout(r, 500));
  }

  // Sort and deduplicate by epoch
  const seen = new Set();
  allCandles = allCandles
    .filter(c => { if (seen.has(c.epoch)) return false; seen.add(c.epoch); return true; })
    .sort((a, b) => a.epoch - b.epoch);

  return allCandles;
}

module.exports = { fetchCandles, fetchCandleHistory };
