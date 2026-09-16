const test = require('node:test');
const assert = require('node:assert');
const StateManager = require('../src/stateManager');

test('StateManager - Barrier Breach Probability Calculation', () => {
  const manager = new StateManager('1HZ75V');

  // Test case 1: Low volatility, tight barrier in compression
  const breachProb1 = manager.calculateBarrierBreachProbability(
    1.5,              // 1.5 pip barrier
    0.00005,          // Very low volatility
    'COMPRESSION',
    4300.0,            // realistic currentPrice for 1HZ75V
    5                 // 5 ticks
  );
  // With 4300.0 price, barrierPercentage = 0.15 / 4300 = 0.0000348
  // vol = sqrt(0.00005) = 0.00707
  // stdDevs = 0.0000348 / 0.00707 = 0.0049 (highly likely to breach)
  // breachProb = (1 - CDF(0.0049)) * 0.7 = 0.5 * 0.7 = 0.35 (which is correct for COMPRESSION)
  assert.ok(breachProb1 < 0.40, `Tight barrier in compression should have <40% breach prob (got ${breachProb1})`);

  // Test case 2: High volatility, wide barrier in spike
  const breachProb2 = manager.calculateBarrierBreachProbability(
    5.0,              // 5 pip barrier
    0.0003,           // High volatility
    'SPIKE',
    4300.0,            // realistic currentPrice
    5
  );
  // vol = sqrt(0.0003) = 0.0173
  // barrierPercentage = 0.5 / 4300 = 0.000116
  // stdDevs = 0.000116 / 0.0173 = 0.0067
  // breachProb = (1 - CDF(0.0067)) * 1.8 = 0.5 * 1.8 = 0.90 (highly volatile spike will breach)
  assert.ok(breachProb2 > 0.80, `Wide barrier in spike should have >80% breach prob (got ${breachProb2})`);

  // Test case 3: Trend regime adjustment check
  const breachProb3 = manager.calculateBarrierBreachProbability(
    2.5,
    0.0001,
    'TREND',
    4300.0,            // realistic currentPrice
    5
  );
  assert.ok(breachProb3 > 0, 'Should return valid probability');
});

test('StateManager - Trade Signal Evaluation (Rejection & Acceptance)', () => {
  const manager = new StateManager('1HZ75V');

  // Mock enough ticks to bypass the minimum tick gate
  for (let i = 0; i < 20; i++) {
    manager.onTick(100 + i);
  }

  // Inject AI barrier params manually
  manager.setBarrierParams({
    barrier_distance_pips: 2.5,
    hmm_state: 'TREND',
    confidence: 0.80,
    garch_forecast: 'MODERATE',
    kelly_multiplier: 0.25
  });

  const metrics = manager.calculateMetrics();
  
  // Verify acceptance
  const signal = manager.evaluateTradeSignal(metrics);
  assert.strictEqual(signal.shouldTrade, true, 'Should trade under optimal trend conditions');
  assert.ok(signal.expectedValue > 0, 'EV should be positive');
  assert.ok(['CALL', 'PUT'].includes(signal.direction), 'Direction should be CALL or PUT');

  // Verify rejection due to low AI confidence
  manager.setBarrierParams({
    barrier_distance_pips: 2.5,
    hmm_state: 'TREND',
    confidence: 0.40, // Below 0.70 threshold
    garch_forecast: 'MODERATE',
    kelly_multiplier: 0.10
  });
  const rejectedSignal1 = manager.evaluateTradeSignal(metrics);
  assert.strictEqual(rejectedSignal1.shouldTrade, false, 'Should reject if AI confidence is low');

  // Verify rejection due to Cooldown
  manager.setBarrierParams({
    barrier_distance_pips: 2.5,
    hmm_state: 'TREND',
    confidence: 0.80,
    garch_forecast: 'MODERATE',
    kelly_multiplier: 0.25
  });
  manager.consecutiveLosses = 3; // Trigger cooldown limit
  const rejectedSignal2 = manager.evaluateTradeSignal(metrics);
  assert.strictEqual(rejectedSignal2.shouldTrade, false, 'Should reject if max consecutive losses reached');
});

test('StateManager - Direction Selection Logic', () => {
  const manager = new StateManager('1HZ75V');
  
  // Test fading upside
  const fadeUpMetrics = {
    velocity: 2,
    distanceToHigh: 0.5, // Near high
    distanceToLow: 10
  };
  assert.strictEqual(manager._selectDirection(fadeUpMetrics, 2.0), 'PUT', 'Should fade upside when near high');

  // Test fading downside
  const fadeDownMetrics = {
    velocity: -2,
    distanceToHigh: 10,
    distanceToLow: 0.5 // Near low
  };
  assert.strictEqual(manager._selectDirection(fadeDownMetrics, 2.0), 'CALL', 'Should fade downside when near low');

  // Test default mean-reversion
  const midMetrics = {
    velocity: 2,
    distanceToHigh: 10, // Not near extremes
    distanceToLow: 10
  };
  assert.strictEqual(manager._selectDirection(midMetrics, 2.0), 'PUT', 'Should default to mean reversion against velocity');

  // Test trend-following under TREND regime
  assert.strictEqual(manager._selectDirection(midMetrics, 2.0, 'TREND'), 'CALL', 'Should follow the trend under TREND regime');
});
