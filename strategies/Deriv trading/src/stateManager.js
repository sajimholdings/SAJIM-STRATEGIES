/**
 * stateManager.js — V3: Per-asset state manager with barrier calculations.
 *
 * Replaces pure velocity/acceleration breakout logic with:
 *   - 100-tick rolling buffer with multi-timeframe variance
 *   - Barrier breach probability (normal CDF + regime adjustment)
 *   - Expected Value gating (minimum +2% EV required)
 *   - AI regime integration (HMM state + GARCH forecast)
 *   - Dynamic direction selection (mean-reversion at extremes)
 *   - Consecutive loss cooldown (3-loss → 60s hard lock)
 */

'use strict';

const {
  COOLDOWN_MS,
  MAX_CONSECUTIVE_LOSSES,
  LOSS_COOLDOWN_MS,
  MIN_CONFIDENCE,
  ASSET_CONFIG,
} = require('./config');
const { info, warn } = require('./logger');
const { broadcast }  = require('./broadcaster');

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────
const MIN_TICKS_FOR_METRICS = 10;
const MIN_EV_THRESHOLD      = 0.02;   // +2% minimum expected value

// CDF approximation coefficients (Abramowitz & Stegun)
const CDF_A1 =  0.254829592;
const CDF_A2 = -0.284496736;
const CDF_A3 =  1.421413741;
const CDF_A4 = -1.453152027;
const CDF_A5 =  1.061405429;
const CDF_P  =  0.3275911;

// Regime multipliers for breach probability adjustment
const REGIME_MULTIPLIERS = {
  TREND:       1.1,
  CHOP:        1.0,
  COMPRESSION: 0.7,
  SPIKE:       1.8,
  FAKEOUT:     1.5,
  NEUTRAL:     1.0,
};

const PIP_SIZE = {
  '1HZ10V': 0.01,
  '1HZ50V': 10.0,
  '1HZ75V': 0.1
};

class StateManager {
  /**
   * @param {string} symbol - e.g. "1HZ75V"
   */
  constructor(symbol) {
    this.symbol = symbol;

    /** @type {Array<{price: number, timestamp: number}>} */
    this.ticks = [];

    // ── Execution gating ──────────────────────────────────────────────────
    this.lastEntryTs       = 0;
    this.executing         = false;
    this.consecutiveLosses = 0;
    this.lossCooldownUntil = 0;

    // ── AI Watchdog Parameters (set via Express webhook) ──────────────────
    this.barrierParams = null;   // Full barrier payload from Tier 2
    this.ai_status     = 'ACTIVE';
    this.confidence    = 0;
    this.multiplier    = 0.20;   // Default Kelly fraction

    // ── Open trade tracking ───────────────────────────────────────────────
    this.openTrades = new Map();
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // TICK INGESTION
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Ingest a new tick and evaluate whether a trade should fire.
   * @param {number} price
   * @returns {{ signal: 'CALL'|'PUT'|null, price: number, stake?: number, meta?: object }}
   */
  onTick(price) {
    const ts = Date.now();

    // Push into rolling buffer
    const bufferLimit = ASSET_CONFIG[this.symbol]?.TICK_BUFFER_SIZE || 100;
    this.ticks.push({ price, timestamp: ts });
    if (this.ticks.length > bufferLimit) {
      this.ticks.shift();
    }

    // Broadcast raw tick to dashboard (reuse fastSMA/slowSMA fields for V/A)
    const velocity     = this.ticks.length >= 2
      ? price - this.ticks[this.ticks.length - 2].price
      : 0;
    const prevVelocity = this.ticks.length >= 3
      ? this.ticks[this.ticks.length - 2].price - this.ticks[this.ticks.length - 3].price
      : 0;
    const acceleration = velocity - prevVelocity;

    broadcast({
      type: 'TICK',
      asset: this.symbol,
      price,
      fastSMA: velocity,
      slowSMA: acceleration,
      timestamp: new Date().toISOString(),
    });

    // ── Gate 1: Need enough ticks ──────────────────────────────────────────
    if (this.ticks.length < MIN_TICKS_FOR_METRICS) {
      return { signal: null, price };
    }

    // ── Gate 2: Cooldowns ──────────────────────────────────────────────────
    if (ts < this.lossCooldownUntil) {
      return { signal: null, price };
    }
    if (ts - this.lastEntryTs < COOLDOWN_MS || this.executing) {
      return { signal: null, price };
    }

    // ── Calculate metrics ──────────────────────────────────────────────────
    const metrics = this.calculateMetrics();
    if (!metrics) {
      return { signal: null, price };
    }

    // ── Evaluate trade signal ──────────────────────────────────────────────
    const signal = this.evaluateTradeSignal(metrics);

    if (signal.shouldTrade) {
      const baseStake    = require('./config').CONTRACT_STAKE || 1.00;
      const dynamicStake = Math.max(baseStake * signal.kellyMultiplier, 0.35);

      const msg = `[SIGNAL] ${signal.direction} | EV: ${(signal.expectedValue * 100).toFixed(2)}% | WinP: ${(signal.winProb * 100).toFixed(1)}% | Barrier: ${signal.barrierDistance.toFixed(3)} | Stake: $${dynamicStake.toFixed(2)}`;
      info(this.symbol, msg);
      broadcast({ type: 'INFO', asset: this.symbol, message: msg, timestamp: new Date().toISOString() });

      this.lastEntryTs = ts;
      this.executing   = true;

      return {
        signal: signal.direction,
        price,
        stake: dynamicStake,
        meta: signal,
      };
    }

    // Log why we didn't trade (throttled — only on non-trivial rejections)
    if (signal.reason && this.barrierParams && !signal.reason.includes('Insufficient data')) {
      const msg = `[SKIP] ${signal.reason}`;
      broadcast({ type: 'INFO', asset: this.symbol, message: msg, timestamp: new Date().toISOString() });
    }

    return { signal: null, price };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // METRICS CALCULATION
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Core metrics: multi-timeframe variance, support/resistance, velocity.
   * @returns {object|null}
   */
  calculateMetrics() {
    const prices = this.ticks.map(t => t.price);
    if (prices.length < MIN_TICKS_FOR_METRICS) return null;

    // Short-term volatility (10 ticks)
    const recentVol = this._calculateVariance(prices.slice(-10));

    // Medium-term reference (dynamic buffer size or whatever we have)
    const bufferLimit = ASSET_CONFIG[this.symbol]?.TICK_BUFFER_SIZE || 50;
    const mediumWindow = prices.slice(-Math.min(bufferLimit, prices.length));
    const historicalVol = this._calculateVariance(mediumWindow);

    // Support / Resistance over the full buffer
    const high = Math.max(...prices);
    const low  = Math.min(...prices);

    const currentPrice  = prices[prices.length - 1];
    const prevPrice     = prices.length >= 2 ? prices[prices.length - 2] : currentPrice;
    const velocity      = currentPrice - prevPrice;
    const distanceToHigh = high - currentPrice;
    const distanceToLow  = currentPrice - low;

    return {
      recentVol,
      historicalVol,
      volRatio: historicalVol > 0 ? recentVol / historicalVol : 1,
      high,
      low,
      currentPrice,
      prevPrice,
      velocity,
      distanceToHigh,
      distanceToLow,
    };
  }

  /**
   * Calculate variance of returns (not raw prices).
   * @param {number[]} prices
   * @returns {number}
   */
  _calculateVariance(prices) {
    if (prices.length < 2) return 0;

    // Convert to log-returns
    const returns = [];
    for (let i = 1; i < prices.length; i++) {
      if (prices[i - 1] === 0) continue;
      returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
    }
    if (returns.length === 0) return 0;

    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;

    return variance;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // BARRIER BREACH PROBABILITY (Normal CDF Approximation)
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Estimate the probability that price will breach the barrier
   * within the contract duration.
   *
   * @param {number} barrierDistancePips
   * @param {number} recentVolatility - return variance
   * @param {string} hmmState         - regime label from Tier 2
   * @param {number} currentPrice     - current asset price for scaling
   * @param {number} [ticksUntilExpiry=5]
   * @returns {number} probability in [0.01, 0.99]
   */
  calculateBarrierBreachProbability(barrierDistancePips, recentVolatility, hmmState, currentPrice, ticksUntilExpiry = 5) {
    // 1. Get pip-adjusted absolute barrier offset
    const pipScale = PIP_SIZE[this.symbol] || 1.0;
    const absoluteOffset = barrierDistancePips * pipScale;

    // 2. Convert absolute offset into standard return percentage relative to current price
    const barrierPercentage = absoluteOffset / (currentPrice || 1.0);

    // 3. Convert variance to standard deviation units
    const vol = Math.sqrt(Math.max(recentVolatility, 1e-12));
    const stdDevs = barrierPercentage / vol;

    // Base breach probability via Φ(x) approximation
    let breachProb = 1 - this._normCDF(stdDevs);

    // HMM regime adjustment
    const multiplier = REGIME_MULTIPLIERS[hmmState] || 1.0;
    breachProb *= multiplier;

    // Clamp to valid probability range
    return Math.min(0.99, Math.max(0.01, breachProb));
  }

  /**
   * Cumulative Normal Distribution via Abramowitz & Stegun approximation.
   * @param {number} x
   * @returns {number}
   */
  _normCDF(x) {
    const sign = x < 0 ? -1 : 1;
    const absX = Math.abs(x);
    const t = 1 / (1 + CDF_P * absX);
    const y = 1 - (((((CDF_A5 * t + CDF_A4) * t + CDF_A3) * t + CDF_A2) * t + CDF_A1) * t * Math.exp(-absX * absX));
    return 0.5 * (1 + sign * y);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // TRADE SIGNAL EVALUATION
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Determine if a trade should be executed.
   * Requires barrier params from the AI Watchdog.
   *
   * @param {object} metrics - from calculateMetrics()
   * @returns {object}
   */
  evaluateTradeSignal(metrics) {
    if (!metrics || !this.barrierParams) {
      return { shouldTrade: false, reason: 'Insufficient data or no AI params' };
    }

    const {
      barrier_distance_pips,
      hmm_state,
      confidence,
      garch_forecast,
      kelly_multiplier,
    } = this.barrierParams;

    // ── Barrier breach probability ─────────────────────────────────────────
    const breachProb = this.calculateBarrierBreachProbability(
      barrier_distance_pips,
      metrics.recentVol,
      hmm_state,
      metrics.currentPrice
    );
    const winProb = 1 - breachProb;

    // ── Payout odds estimation ─────────────────────────────────────────────
    const payoutOdds = this._estimatePayoutOdds(barrier_distance_pips);

    // ── Expected Value calculation ─────────────────────────────────────────
    // EV = (P(win) × payout_ratio) - (P(loss) × 1.0)
    const expectedValue = (winProb * (payoutOdds / 100)) - (breachProb * 1.0);

    // ── Gate checks ────────────────────────────────────────────────────────
    if (expectedValue < MIN_EV_THRESHOLD) {
      return {
        shouldTrade: false,
        reason: `EV too low: ${(expectedValue * 100).toFixed(2)}% (need >${MIN_EV_THRESHOLD * 100}%)`,
      };
    }

    if (confidence < MIN_CONFIDENCE) {
      return {
        shouldTrade: false,
        reason: `AI confidence too low: ${(confidence * 100).toFixed(0)}% (need >${MIN_CONFIDENCE * 100}%)`,
      };
    }

    if (this.consecutiveLosses >= MAX_CONSECUTIVE_LOSSES) {
      return {
        shouldTrade: false,
        reason: `Consecutive loss cooldown (${this.consecutiveLosses} losses)`,
      };
    }

    // Determine direction
    const direction = this._selectDirection(metrics, barrier_distance_pips, hmm_state);
    const pipScale = PIP_SIZE[this.symbol] || 1.0;
    const absoluteBarrierDistance = barrier_distance_pips * pipScale;

    return {
      shouldTrade: true,
      direction,
      barrierDistance: absoluteBarrierDistance,
      breachProb,
      winProb,
      expectedValue,
      hmmState: hmm_state,
      payoutOdds,
      kellyMultiplier: kelly_multiplier || this.multiplier || 0.20,
    };
  }

  /**
   * Decide CALL or PUT using HMM-regime-aware trend alignment or micro-reversion.
   *
   * - TREND Regime → Trend-Following: Go CALL if moving up, PUT if moving down.
   * - Otherwise → Mean-Reversion: Fades price extremes or snapbacks.
   *
   * @param {object} metrics
   * @param {number} barrierDistance
   * @param {string} hmmState
   * @returns {'CALL'|'PUT'}
   */
  _selectDirection(metrics, barrierDistance, hmmState = 'NEUTRAL') {
    const { velocity, distanceToHigh, distanceToLow } = metrics;

    if (hmmState === 'TREND') {
      // Trend-following logic: follow the momentum direction in a trending market!
      return velocity > 0 ? 'CALL' : 'PUT';
    }

    // Otherwise, fade price exhaustion at extremes
    if (velocity > 0 && distanceToHigh < barrierDistance * 0.5) {
      return 'PUT';   // Fading upside exhaustion
    }
    if (velocity < 0 && distanceToLow < barrierDistance * 0.5) {
      return 'CALL';  // Fading downside exhaustion
    }

    // Default: mean-reversion against the last move
    return velocity > 0 ? 'PUT' : 'CALL';
  }

  /**
   * Estimate payout odds for a given barrier distance.
   * In production, these would be fetched from the Deriv proposal response.
   *
   * @param {number} barrierDistancePips
   * @returns {number} payout percentage (e.g. 85 = 85%)
   */
  _estimatePayoutOdds(barrierDistancePips) {
    if (barrierDistancePips < 1.5) return 90;
    if (barrierDistancePips < 2.0) return 85;
    if (barrierDistancePips < 2.5) return 82;
    if (barrierDistancePips < 3.0) return 80;
    if (barrierDistancePips < 4.0) return 75;
    if (barrierDistancePips < 5.0) return 70;
    return 65;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // AI WATCHDOG INTERFACE
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Called by the Express webhook when Tier 2 pushes new regime data.
   * @param {object} params
   */
  setBarrierParams(params) {
    this.barrierParams = params;
    this.ai_status     = params.hmm_state === 'CHOP' || params.hmm_state === 'SPIKE'
                           ? 'PAUSED'
                           : 'ACTIVE';
    this.confidence    = params.confidence || 0;
    this.multiplier    = params.kelly_multiplier || 0.20;

    const msg = `[AI] Regime: ${params.hmm_state} | Conf: ${(this.confidence * 100).toFixed(0)}% | Barrier: ${params.barrier_distance_pips}p | Kelly: ${this.multiplier.toFixed(2)}`;
    info(this.symbol, msg);
    broadcast({ type: 'INFO', asset: this.symbol, message: msg, timestamp: new Date().toISOString() });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // PnL FEEDBACK LOOP
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Called by connection.js when a contract expires.
   * @param {number} profit
   */
  onTradeResult(profit) {
    if (profit < 0) {
      this.consecutiveLosses += 1;
      if (this.consecutiveLosses >= MAX_CONSECUTIVE_LOSSES) {
        warn(this.symbol, `[COOLDOWN] ${MAX_CONSECUTIVE_LOSSES} consecutive losses. 60s lockout.`);
        broadcast({ type: 'INFO', asset: this.symbol, message: `[COOLDOWN] ${MAX_CONSECUTIVE_LOSSES} losses → 60s lockout`, timestamp: new Date().toISOString() });
        this.lossCooldownUntil = Date.now() + LOSS_COOLDOWN_MS;
        this.consecutiveLosses = 0;
      }
    } else {
      this.consecutiveLosses = 0;
    }
  }

  /**
   * Release the execution lock (called in executor.js finally block).
   */
  releaseExecution() {
    this.executing = false;
  }
}

module.exports = StateManager;
