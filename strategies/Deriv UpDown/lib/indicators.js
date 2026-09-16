// lib/indicators.js — EMA, RSI, local extremes

/**
 * Compute EMA aligned to the closes array.
 * Returns array of same length — null for warmup period.
 */
function computeEMA(closes, period) {
  const result = new Array(closes.length).fill(null);
  const k = 2 / (period + 1);
  let sum = 0;
  for (let i = 0; i < period; i++) sum += closes[i];
  result[period - 1] = sum / period;
  for (let i = period; i < closes.length; i++) {
    result[i] = closes[i] * k + result[i - 1] * (1 - k);
  }
  return result;
}

/**
 * Compute RSI using Wilder's smoothing.
 * Returns array of same length — null for warmup period.
 */
function computeRSI(closes, period = 14) {
  const result = new Array(closes.length).fill(null);
  let avgGain = 0, avgLoss = 0;

  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) avgGain += diff;
    else avgLoss -= diff;
  }
  avgGain /= period;
  avgLoss /= period;
  result[period] = 100 - 100 / (1 + avgGain / (avgLoss || 1e-10));

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result[i] = 100 - 100 / (1 + avgGain / (avgLoss || 1e-10));
  }
  return result;
}

/**
 * For candle at index i, check if it made a new high (or low)
 * compared to the previous `lookback` candles.
 */
function isNewLocalHigh(candles, i, lookback = 5) {
  if (i < lookback) return false;
  const currentHigh = candles[i].high;
  for (let j = i - lookback; j < i; j++) {
    if (candles[j].high >= currentHigh) return false;
  }
  return true;
}

function isNewLocalLow(candles, i, lookback = 5) {
  if (i < lookback) return false;
  const currentLow = candles[i].low;
  for (let j = i - lookback; j < i; j++) {
    if (candles[j].low <= currentLow) return false;
  }
  return true;
}

module.exports = { computeEMA, computeRSI, isNewLocalHigh, isNewLocalLow };
