/**
 * index.js — Entry point for the Deriv Deterministic Engine.
 *
 * Boots the connection, prints a startup banner, and wires
 * SIGINT / SIGTERM for graceful shutdown.
 */

'use strict';

// Load config first (parses .env)
const config     = require('./config');
const connection = require('./connection');
const { info }   = require('./logger');
const { startBroadcaster, broadcast } = require('./broadcaster');
const express    = require('express');
const cors       = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

// The V3 AI Regime Webhook Receiver
app.post('/api/regime-update', (req, res) => {
    const {
        asset,
        regime,
        confidence,
        barrier_distance_pips,
        kelly_multiplier,
        garch_forecast,
        timestamp
    } = req.body;

    const manager = connection.managers.get(asset);
    if (!manager) {
        return res.status(404).json({ error: 'Symbol not currently tracked' });
    }

    // Pass the full V3 payload to the StateManager
    manager.setBarrierParams({
        hmm_state: regime,
        confidence,
        barrier_distance_pips,
        kelly_multiplier,
        garch_forecast
    });

    // Also broadcast the update to the dashboard
    broadcast({
        type: 'AI_UPDATE',
        asset,
        status: regime === 'CHOP' || regime === 'SPIKE' ? 'PAUSED' : 'ACTIVE',
        confidence: confidence || 0,
        multiplier: kelly_multiplier || 0,
        timestamp: timestamp || new Date().toISOString()
    });

    res.status(200).json({ message: 'Regime updated successfully' });
});

// Endpoint for the Python Watchdog to retrieve dynamic live tick histories
app.get('/api/ticks', (req, res) => {
    const { asset, count } = req.query;
    if (!asset) {
        return res.status(400).json({ error: 'Asset parameter required' });
    }
    const manager = connection.managers.get(asset);
    if (!manager) {
        return res.status(404).json({ error: 'Symbol not currently tracked' });
    }
    const limit = parseInt(count) || manager.ticks.length;
    const ticksData = manager.ticks.slice(-limit).map(t => t.price);
    res.status(200).json({ asset, ticks: ticksData });
});

app.listen(3001, () => {
    console.log('[EXPRESS] AI Webhook Bridge live on port 3001. Listening for Watchdog...');
});

// ---------------------------------------------------------------------------
// Banner
// ---------------------------------------------------------------------------
console.log(`
╔════════════════════════════════════════════════════════════╗
║         DERIV  DETERMINISTIC  ENGINE  v1.0.0              ║
║  Micro-Momentum Crossover  •  Synthetic Indices Pipeline  ║
╠════════════════════════════════════════════════════════════╣
║  Assets : ${config.ASSETS.join(', ').padEnd(46)}║
║  Fast SMA : ${String(config.FAST_SMA_PERIOD).padEnd(44)}║
║  Slow SMA : ${String(config.SLOW_SMA_PERIOD).padEnd(44)}║
║  Buffer   : ${String(config.TICK_BUFFER_SIZE + ' ticks').padEnd(44)}║
║  Cooldown : ${String(config.COOLDOWN_MS / 1000 + 's').padEnd(44)}║
║  Stake    : $${String(config.CONTRACT_STAKE).padEnd(43)}║
╚════════════════════════════════════════════════════════════╝
`);

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------
function shutdown(signal) {
  info('ENGINE', `Received ${signal}. Shutting down...`);
  connection.stop();
  process.exit(0);
}

process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

// Catch unhandled errors so the engine doesn't silently die
process.on('uncaughtException', (err) => {
  console.error('[FATAL] Uncaught exception:', err);
  connection.stop();
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[FATAL] Unhandled rejection:', reason);
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
info('ENGINE', 'Starting Deterministic Engine...');
startBroadcaster();
connection.start();
