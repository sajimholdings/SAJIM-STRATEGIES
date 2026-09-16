// lib/stats.js — Proper statistical tools for time-series backtest evaluation

/**
 * Wilson score confidence interval for a proportion.
 * More accurate than naive p ± z*SE, especially for extreme win rates or small N.
 * @param {number} wins
 * @param {number} total
 * @param {number} z  z-score (1.96 = 95% CI, 1.645 = 90% CI)
 */
function wilsonCI(wins, total, z = 1.96) {
  if (total === 0) return { lower: 0, upper: 1, mid: 0, n: 0 };
  const p = wins / total;
  const z2 = z * z;
  const n  = total;
  const center = (p + z2 / (2 * n)) / (1 + z2 / n);
  const margin  = z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n);
  return {
    lower: Math.max(0, center - margin),
    upper: Math.min(1, center + margin),
    mid:   p,
    n:     total,
  };
}

/**
 * Block bootstrap confidence interval.
 * Preserves local autocorrelation in time-series data by resampling
 * contiguous blocks instead of individual observations.
 *
 * @param {boolean[]} outcomes  Array of win(true)/loss(false) per signal
 * @param {number}    blockSize  Recommended: sqrt(N). Null = auto.
 * @param {number}    iterations Number of bootstrap resamples
 * @returns {{ lower, upper, mean, se, blockSize }}
 */
function blockBootstrap(outcomes, blockSize = null, iterations = 2000) {
  const n = outcomes.length;
  if (n < 10) return { lower: null, upper: null, mean: null, se: null, blockSize: 1 };

  blockSize = blockSize || Math.max(3, Math.round(Math.sqrt(n)));
  const rates = [];

  for (let iter = 0; iter < iterations; iter++) {
    const sample = [];
    while (sample.length < n) {
      // Pick a random block start
      const start = Math.floor(Math.random() * (n - blockSize + 1));
      for (let j = 0; j < blockSize && sample.length < n; j++) {
        sample.push(outcomes[start + j]);
      }
    }
    rates.push(sample.filter(Boolean).length / sample.length);
  }

  rates.sort((a, b) => a - b);
  const mean = rates.reduce((s, r) => s + r, 0) / rates.length;
  const se   = Math.sqrt(rates.reduce((s, r) => s + (r - mean) ** 2, 0) / rates.length);

  return {
    lower:     rates[Math.floor(0.025 * iterations)],
    upper:     rates[Math.floor(0.975 * iterations)],
    mean,
    se,
    blockSize,
  };
}

/**
 * Non-overlapping signal filter.
 * For a given look-ahead window of `ahead` candles, ensures consecutive signals
 * do not share any candles in their evaluation windows.
 * This eliminates the inflated effective-N from overlapping windows.
 *
 * @param {Array}  signals  Sorted array of signal objects (must have `.i` property)
 * @param {number} ahead    Look-ahead in candles
 * @returns {Array}  Subset of signals with no overlapping windows
 */
function noOverlapSignals(signals, ahead) {
  const result = [];
  let nextAllowed = -Infinity;
  for (const s of signals) {
    if (s.i >= nextAllowed) {
      result.push(s);
      nextAllowed = s.i + ahead; // skip past the evaluation window
    }
  }
  return result;
}

/**
 * Reliability-weighted ranking score.
 * Penalises results from short data spans and rewards statistical depth.
 * Uses the Wilson CI lower bound — the "pessimistic" win rate estimate.
 *
 * Formula: score = wilsonCI.lower, where the CI accounts for both
 * win rate *and* sample size. A 56% rate over 200 signals will score
 * lower than a 56% rate over 1000 signals.
 *
 * @param {number} wins
 * @param {number} total
 * @returns {number}  Score in [0,1] — rank descending
 */
function reliabilityScore(wins, total) {
  const ci = wilsonCI(wins, total);
  return ci.lower;
}

/**
 * Format a CI for display.
 */
function fmtCI(ci, digits = 1) {
  if (ci.lower === null) return 'N/A';
  const pct = n => `${(n * 100).toFixed(digits)}%`;
  return `${pct(ci.lower)} – ${pct(ci.upper)}`;
}

module.exports = { wilsonCI, blockBootstrap, noOverlapSignals, reliabilityScore, fmtCI };
