/**
 * executor.js — V3: Trade execution with dynamic barrier support.
 *
 * Pipeline: proposal (with barrier) → buy → subscribe to open contract.
 * Logs full signal metadata (EV, winProb, barrier, regime) for analysis.
 */

'use strict';

const {
  CONTRACT_DURATION,
  CONTRACT_DURATION_UNIT,
  CONTRACT_CURRENCY,
  CONTRACT_BASIS,
} = require('./config');
const { info, error, logTrade } = require('./logger');
const { broadcast } = require('./broadcaster');

/** Auto-incrementing request ID for multiplexed WS responses */
let reqSeq = 1000;

/**
 * Execute the full proposal→buy pipeline for a given symbol.
 *
 * @param {import('ws').WebSocket} ws          - live WS connection
 * @param {import('./stateManager')} manager   - the asset's state manager
 * @param {number} entryPrice                  - tick price that triggered the signal
 * @param {'CALL'|'PUT'} contractType          - direction
 * @param {number} dynamicStake                - Kelly-adjusted stake
 * @param {object} [meta]                      - V3 signal metadata (EV, barrier, etc.)
 * @returns {Promise<void>}
 */
async function executeTradeSequence(ws, manager, entryPrice, contractType, dynamicStake, meta) {
  const symbol = manager.symbol;

  try {
    // ------------------------------------------------------------------
    // Step 1: Send proposal request (with optional barrier)
    // ------------------------------------------------------------------
    const proposal = await sendProposal(ws, symbol, contractType, dynamicStake, meta);
    info(symbol, `Proposal received: ${proposal.id} (Ask: ${proposal.ask_price})`);

    // ------------------------------------------------------------------
    // Step 2: Buy with the proposal_id
    // ------------------------------------------------------------------
    const buyResult = await sendBuy(ws, proposal.id, proposal.ask_price);
    const orderId = buyResult.buy.contract_id || buyResult.buy.transaction_id || 'unknown';

    // ------------------------------------------------------------------
    // Step 3: Log the trade with V3 metadata
    // ------------------------------------------------------------------
    const ts = new Date().toISOString();
    logTrade({
      timestamp:  ts,
      asset:      symbol,
      entryPrice,
      orderId:    String(orderId),
    });

    // Build rich broadcast payload
    const tradePayload = {
      type: 'TRADE',
      asset: symbol,
      action: contractType,
      price: entryPrice,
      stake: dynamicStake,
      timestamp: ts,
    };
    if (meta) {
      tradePayload.expectedValue = meta.expectedValue;
      tradePayload.winProb       = meta.winProb;
      tradePayload.barrier       = meta.barrierDistance;
      tradePayload.hmmState      = meta.hmmState;
    }
    broadcast(tradePayload);

    // ------------------------------------------------------------------
    // Step 4: Subscribe to contract outcome
    // ------------------------------------------------------------------
    if (buyResult.buy.contract_id) {
      ws.send(JSON.stringify({
        proposal_open_contract: 1,
        contract_id: buyResult.buy.contract_id,
        subscribe: 1,
      }));
    }

  } catch (err) {
    error(symbol, 'Trade execution failed:', err.message || err);
  } finally {
    manager.releaseExecution();
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Send a proposal request and resolve with the proposal_id.
 * @returns {Promise<{id: string, ask_price: number}>}
 */
function sendProposal(ws, symbol, contractType, dynamicStake, meta) {
  return new Promise((resolve, reject) => {
    const rid = ++reqSeq;

    const payload = {
      proposal:      1,
      amount:        dynamicStake,
      basis:         CONTRACT_BASIS,
      contract_type: contractType,
      currency:      CONTRACT_CURRENCY,
      duration:      CONTRACT_DURATION,
      duration_unit: CONTRACT_DURATION_UNIT,
      symbol,
      req_id:        rid,
    };

    // V3: Attach barrier if provided by the AI (limited to 2 decimal places by Deriv constraint)
    if (meta && meta.barrierDistance) {
      const formattedBarrier = Number(meta.barrierDistance).toFixed(2);
      payload.barrier = `+${formattedBarrier}`;
    }

    const handler = (raw) => {
      try {
        const msg = JSON.parse(raw.toString());
        if (msg.req_id !== rid) return; // not ours
        ws.removeListener('message', handler);

        if (msg.error) {
          return reject(new Error(`Proposal error: ${msg.error.message} (${msg.error.code})`));
        }
        if (!msg.proposal?.id) {
          return reject(new Error('Proposal response missing proposal.id'));
        }

        resolve({ id: msg.proposal.id, ask_price: msg.proposal.ask_price });
      } catch (e) {
        ws.removeListener('message', handler);
        reject(e);
      }
    };

    ws.on('message', handler);
    ws.send(JSON.stringify(payload));

    // Timeout guard — 10 seconds
    setTimeout(() => {
      ws.removeListener('message', handler);
      reject(new Error('Proposal request timed out (10 s)'));
    }, 10_000);
  });
}

/**
 * Send a buy request for the given proposal_id and resolve with the buy response.
 * @returns {Promise<object>}
 */
function sendBuy(ws, proposalId, askPrice) {
  return new Promise((resolve, reject) => {
    const rid = ++reqSeq;

    const payload = {
      buy:    proposalId,
      price:  askPrice,
      req_id: rid,
    };

    const handler = (raw) => {
      try {
        const msg = JSON.parse(raw.toString());
        if (msg.req_id !== rid) return;
        ws.removeListener('message', handler);

        if (msg.error) {
          return reject(new Error(`Buy error: ${msg.error.message} (${msg.error.code})`));
        }
        if (!msg.buy) {
          return reject(new Error('Buy response missing buy payload'));
        }
        resolve(msg);
      } catch (e) {
        ws.removeListener('message', handler);
        reject(e);
      }
    };

    ws.on('message', handler);
    ws.send(JSON.stringify(payload));

    // Timeout guard — 10 seconds
    setTimeout(() => {
      ws.removeListener('message', handler);
      reject(new Error('Buy request timed out (10 s)'));
    }, 10_000);
  });
}

module.exports = { executeTradeSequence };
