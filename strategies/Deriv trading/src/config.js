/**
 * config.js — Centralised configuration constants.
 * All tunable parameters live here for easy adjustment.
 */

'use strict';

// ---------------------------------------------------------------------------
// Environment — load .env file manually (no dotenv dependency)
// ---------------------------------------------------------------------------
const fs = require('fs');
const path = require('path');

const envPath = path.resolve(__dirname, '..', '.env');
if (fs.existsSync(envPath)) {
  const envContent = fs.readFileSync(envPath, 'utf-8');
  for (const line of envContent.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const val = trimmed.slice(eqIdx + 1).trim();
    if (!process.env[key]) process.env[key] = val;
  }
}

// ---------------------------------------------------------------------------
// Deriv API
// ---------------------------------------------------------------------------
const DERIV_APP_ID   = process.env.DERIV_APP_ID;
const DERIV_API_TOKEN = process.env.DERIV_API_TOKEN;

if (!DERIV_APP_ID || !DERIV_API_TOKEN) {
  console.error('[FATAL] DERIV_APP_ID and DERIV_API_TOKEN must be set in .env or environment.');
  process.exit(1);
}

const WS_URL = `wss://ws.derivws.com/websockets/v3?app_id=${DERIV_APP_ID}`;

// ---------------------------------------------------------------------------
// Trading assets
// ---------------------------------------------------------------------------
const ASSETS = ['1HZ50V', '1HZ75V'];

// ---------------------------------------------------------------------------
// Strategy parameters
// ---------------------------------------------------------------------------
const COOLDOWN_MS      = 30_000; // standard 30-second cooldown per asset
const MIN_CONFIDENCE   = 0.65;

// ---------------------------------------------------------------------------
// Asset-specific configurations (Tuned to individual PRNG characteristics)
// ---------------------------------------------------------------------------
const ASSET_CONFIG = {
  '1HZ10V': {
    TICK_BUFFER_SIZE: 150,           // Increased from 120 for lookback buffer safety
    VELOCITY_THRESHOLD: 1.8,         // Lower threshold (1.5-2.0σ)
    MAX_VARIANCE: 2.0,               // Tight (low variance expected)
    ACCELERATION_DECAY_GAMMA: 1.2,   // Looser fakeout detection
    GARCH_SCALE: 0.7,                // Vol changes slowly
    HMM_LOOKBACK_SAMPLES: 100,       // Longer window for regime detection
  },
  '1HZ50V': {
    TICK_BUFFER_SIZE: 100,           // Increased from 65
    VELOCITY_THRESHOLD: 2.3,         // Medium threshold (2.0-2.5σ)
    MAX_VARIANCE: 5000,              // Moderate (high nominal price)
    ACCELERATION_DECAY_GAMMA: 0.9,   // Moderate fakeout detection
    GARCH_SCALE: 1.0,                // Normal vol changes
    HMM_LOOKBACK_SAMPLES: 60,        // Medium window
  },
  '1HZ75V': {
    TICK_BUFFER_SIZE: 80,            // Increased from 32
    VELOCITY_THRESHOLD: 2.8,         // Higher threshold (2.5-3.0σ)
    MAX_VARIANCE: 80,                // Loose (high volatility expected)
    ACCELERATION_DECAY_GAMMA: 0.75,  // Loosened from 0.6 to prevent blocking valid breakout trades
    GARCH_SCALE: 1.3,               // Vol changes quickly
    HMM_LOOKBACK_SAMPLES: 35,        // Shorter window for rapid adaptation
  }
};

const MAX_CONSECUTIVE_LOSSES = 3;
const LOSS_COOLDOWN_MS       = 60_000;

// ---------------------------------------------------------------------------
// Contract parameters
// ---------------------------------------------------------------------------
const CONTRACT_DURATION = 5;          // ticks
const CONTRACT_DURATION_UNIT = 't';   // t = ticks
const CONTRACT_STAKE    = 1;          // USD
const CONTRACT_CURRENCY = 'USD';
const CONTRACT_BASIS    = 'stake';

// ---------------------------------------------------------------------------
// Connection parameters
// ---------------------------------------------------------------------------
const HEARTBEAT_INTERVAL_MS  = 30_000; // ping every 30 s
const RECONNECT_BASE_DELAY   = 1_000;  // initial reconnect delay
const RECONNECT_MAX_DELAY    = 30_000; // cap exponential backoff at 30 s
const RECONNECT_MAX_ATTEMPTS = Infinity; // never give up
const BROADCAST_PORT         = 8080;

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
const LOG_FILE = path.resolve(__dirname, '..', 'trades_v3_no_vol10.log');

module.exports = {
  DERIV_APP_ID,
  DERIV_API_TOKEN,
  WS_URL,
  ASSETS,
  ASSET_CONFIG,
  COOLDOWN_MS,
  MAX_CONSECUTIVE_LOSSES,
  LOSS_COOLDOWN_MS,
  MIN_CONFIDENCE,
  CONTRACT_DURATION,
  CONTRACT_DURATION_UNIT,
  CONTRACT_STAKE,
  CONTRACT_CURRENCY,
  CONTRACT_BASIS,
  HEARTBEAT_INTERVAL_MS,
  RECONNECT_BASE_DELAY,
  RECONNECT_MAX_DELAY,
  RECONNECT_MAX_ATTEMPTS,
  BROADCAST_PORT,
  LOG_FILE,
};
