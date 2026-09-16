/**
 * connection.js — WebSocket lifecycle: connect, authenticate, subscribe,
 *                 heartbeat, and exponential-backoff reconnection.
 */

'use strict';

const WebSocket = require('ws');
const {
  WS_URL,
  DERIV_API_TOKEN,
  ASSETS,
  HEARTBEAT_INTERVAL_MS,
  RECONNECT_BASE_DELAY,
  RECONNECT_MAX_DELAY,
  RECONNECT_MAX_ATTEMPTS,
} = require('./config');
const { info, success, warn, error } = require('./logger');
const StateManager                   = require('./stateManager');
const { executeTradeSequence }       = require('./executor');
const fs                             = require('fs');
const { LOG_FILE }                   = require('./config');
const { broadcast }                  = require('./broadcaster');

/** @type {Map<string, StateManager>} symbol → state manager */
const managers = new Map();
ASSETS.forEach((sym) => managers.set(sym, new StateManager(sym)));

let ws              = null;
let heartbeatTimer  = null;
let reconnectDelay  = RECONNECT_BASE_DELAY;
let reconnectCount  = 0;
let intentionalClose = false;
let sessionPnL       = 0;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

function start() {
  intentionalClose = false;
  connect();
}

function stop() {
  intentionalClose = true;
  cleanup();
  if (ws) {
    ws.close();
    ws = null;
  }
  info('ENGINE', 'Stopped gracefully.');
}

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------

function connect() {
  info('WS', `Connecting to ${WS_URL.replace(/app_id=.*/, 'app_id=***')}...`);

  ws = new WebSocket(WS_URL);

  ws.on('open', onOpen);
  ws.on('message', onMessage);
  ws.on('error', onError);
  ws.on('close', onClose);
}

async function onOpen() {
  success('WS', 'Connection established.');
  reconnectDelay = RECONNECT_BASE_DELAY;
  reconnectCount = 0;

  try {
    await authenticate();
    startHeartbeat();
    subscribeAll();
  } catch (err) {
    error('WS', 'Post-open sequence failed:', err.message);
    ws.close();
  }
}

function onMessage(raw) {
  let msg;
  try {
    msg = JSON.parse(raw.toString());
  } catch {
    warn('WS', 'Received non-JSON payload');
    return;
  }

  // ---- Tick routing (hot path) ----
  if (msg.msg_type === 'tick' && msg.tick) {
    const { symbol, quote } = msg.tick;
    const manager = managers.get(symbol);
    if (!manager) return;

    const { signal, price, stake, meta } = manager.onTick(quote);
    if (signal === 'CALL' || signal === 'PUT') {
      // Fire-and-forget — the executor manages its own error handling
      executeTradeSequence(ws, manager, price, signal, stake, meta);
    }
    return;
  }

  // ---- Open Contract updates ----
  if (msg.msg_type === 'proposal_open_contract' && msg.proposal_open_contract) {
    const poc = msg.proposal_open_contract;
    if (poc.is_sold === 1) {
      const profit = poc.profit;
      sessionPnL += profit;
      const status = profit >= 0 ? 'WIN' : 'LOSS';
      const logLine = `[CLOSED] Asset: ${poc.underlying} | Entry -> Exit | PnL: $${profit.toFixed(2)} | Session PnL: $${sessionPnL.toFixed(2)}`;
      
      fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} | ${logLine}\n`);
      info('RESULT', logLine);

      const manager = managers.get(poc.underlying);
      if (manager) manager.onTradeResult(profit);

      broadcast({
        type: 'TRADE_RESULT',
        contract_id: poc.contract_id,
        asset: poc.underlying,
        profit: profit,
        status: status,
        timestamp: new Date().toISOString()
      });
      
      if (poc.id) {
        ws.send(JSON.stringify({ forget: poc.id }));
      }
    }
    return;
  }

  // ---- Heartbeat pong ----
  if (msg.msg_type === 'ping') return; // handled silently

  // ---- Auth response ----
  if (msg.msg_type === 'authorize') {
    if (msg.error) {
      error('AUTH', `Authorization failed: ${msg.error.message}`);
    }
    return;
  }

  // ---- Errors ----
  if (msg.error) {
    warn('WS', `API error [${msg.error.code}]: ${msg.error.message}`);
  }
}

function onError(err) {
  error('WS', 'Socket error:', err.message);
}

function onClose(code, reason) {
  cleanup();
  warn('WS', `Connection closed (code=${code}, reason=${reason || 'none'}).`);

  if (intentionalClose) return;

  if (reconnectCount >= RECONNECT_MAX_ATTEMPTS) {
    error('WS', 'Max reconnect attempts reached. Giving up.');
    return;
  }

  // Exponential backoff with jitter
  const jitter = Math.random() * 500;
  const delay  = Math.min(reconnectDelay + jitter, RECONNECT_MAX_DELAY);
  reconnectCount++;

  info('WS', `Reconnecting in ${(delay / 1000).toFixed(1)}s (attempt ${reconnectCount})...`);
  setTimeout(connect, delay);

  reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_DELAY);
}

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

function authenticate() {
  return new Promise((resolve, reject) => {
    const handler = (raw) => {
      try {
        const msg = JSON.parse(raw.toString());
        if (msg.msg_type !== 'authorize') return;
        ws.removeListener('message', handler);

        if (msg.error) {
          return reject(new Error(`Auth: ${msg.error.message}`));
        }
        success('AUTH', `Authenticated as ${msg.authorize?.loginid || 'unknown'} (balance: ${msg.authorize?.balance} ${msg.authorize?.currency})`);
        resolve();
      } catch (e) {
        ws.removeListener('message', handler);
        reject(e);
      }
    };

    ws.on('message', handler);
    ws.send(JSON.stringify({ authorize: DERIV_API_TOKEN }));

    setTimeout(() => {
      ws.removeListener('message', handler);
      reject(new Error('Authorization timed out (10 s)'));
    }, 10_000);
  });
}

// ---------------------------------------------------------------------------
// Tick subscriptions
// ---------------------------------------------------------------------------

function subscribeAll() {
  for (const symbol of ASSETS) {
    ws.send(JSON.stringify({ ticks: symbol, subscribe: 1 }));
    info('SUB', `Subscribed to ${symbol} tick stream.`);
  }
}

// ---------------------------------------------------------------------------
// Heartbeat
// ---------------------------------------------------------------------------

function startHeartbeat() {
  heartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ ping: 1 }));
    }
  }, HEARTBEAT_INTERVAL_MS);
}

function cleanup() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

module.exports = { start, stop, managers };
