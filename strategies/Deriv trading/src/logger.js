/**
 * logger.js — Dual-channel logging: console (structured) + file (trade log).
 */

'use strict';

const fs   = require('fs');
const { LOG_FILE } = require('./config');

// ---------------------------------------------------------------------------
// Console helpers (prefixed, colour-coded)
// ---------------------------------------------------------------------------
const RESET  = '\x1b[0m';
const CYAN   = '\x1b[36m';
const GREEN  = '\x1b[32m';
const YELLOW = '\x1b[33m';
const RED    = '\x1b[31m';
const DIM    = '\x1b[2m';

function ts() {
  return new Date().toISOString();
}

function info(tag, ...args) {
  console.log(`${DIM}${ts()}${RESET} ${CYAN}[${tag}]${RESET}`, ...args);
}

function success(tag, ...args) {
  console.log(`${DIM}${ts()}${RESET} ${GREEN}[${tag}]${RESET}`, ...args);
}

function warn(tag, ...args) {
  console.warn(`${DIM}${ts()}${RESET} ${YELLOW}[${tag}]${RESET}`, ...args);
}

function error(tag, ...args) {
  console.error(`${DIM}${ts()}${RESET} ${RED}[${tag}]${RESET}`, ...args);
}

// ---------------------------------------------------------------------------
// File logger — append-only trade log (non-blocking)
// ---------------------------------------------------------------------------
const logStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });

/**
 * Append a structured trade record to trades.log.
 * @param {{ timestamp: string, asset: string, entryPrice: number, orderId: string }} record
 */
function logTrade(record) {
  const line = `${record.timestamp} | Asset: ${record.asset} | Entry: ${record.entryPrice} | OrderID: ${record.orderId}\n`;
  logStream.write(line);
  success('TRADE-LOG', line.trim());
}

module.exports = { info, success, warn, error, logTrade };
