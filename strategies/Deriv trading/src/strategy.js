/**
 * strategy.js — Pure math: SMA calculation and crossover detection.
 * Stateless functions — all state is held in StateManager.
 */

'use strict';

const { FAST_SMA_PERIOD, SLOW_SMA_PERIOD } = require('./config');

/**
 * Compute the Simple Moving Average for the last `period` values.
 * Returns null if insufficient data.
 * @param {number[]} ticks - price buffer (newest last)
 * @param {number}   period
 * @returns {number|null}
 */
function sma(ticks, period) {
  const len = ticks.length;
  if (len < period) return null;

  let sum = 0;
  // Sum only the last `period` elements — avoids slice allocation
  for (let i = len - period; i < len; i++) {
    sum += ticks[i];
  }
  return sum / period;
}

/**
 * Evaluate the micro-momentum crossover strategy.
 * @param {number[]} ticks           - price buffer (newest last)
 * @param {number|null} prevFastSMA  - previous fast SMA value
 * @param {number|null} prevSlowSMA  - previous slow SMA value
 * @returns {{ signal: 'BUY'|null, fastSMA: number|null, slowSMA: number|null }}
 */
function evaluate(ticks, prevFastSMA, prevSlowSMA) {
  const fastSMA = sma(ticks, FAST_SMA_PERIOD);
  const slowSMA = sma(ticks, SLOW_SMA_PERIOD);

  let signal = null;

  // Crossover detection: fast crosses above slow
  if (
    fastSMA !== null && slowSMA !== null &&
    prevFastSMA !== null && prevSlowSMA !== null &&
    prevFastSMA <= prevSlowSMA &&  // was below or equal
    fastSMA > slowSMA              // now above
  ) {
    signal = 'BUY';
  }

  return { signal, fastSMA, slowSMA };
}

module.exports = { sma, evaluate };
