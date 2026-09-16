'use strict';

const WebSocket = require('ws');
const { BROADCAST_PORT } = require('./config');
const { info, error } = require('./logger');

let wss;
const clients = new Set();

function startBroadcaster() {
  wss = new WebSocket.Server({ port: BROADCAST_PORT }, () => {
    info('BROADCASTER', `WebSocket server running on ws://localhost:${BROADCAST_PORT}`);
  });

  wss.on('connection', (ws) => {
    info('BROADCASTER', 'Dashboard connected');
    clients.add(ws);
    ws.on('close', () => clients.delete(ws));
    ws.on('error', (err) => error('BROADCASTER', 'Client error:', err));
  });

  wss.on('error', (err) => {
    error('BROADCASTER', 'Server error:', err);
  });
}

function broadcast(payload) {
  if (!wss) return;
  const msg = JSON.stringify(payload);
  for (const client of clients) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(msg);
    }
  }
}

module.exports = { startBroadcaster, broadcast };
