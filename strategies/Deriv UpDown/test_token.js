// test_token.js — Comprehensive Deriv API token probe
const fs = require('fs');
const https = require('https');
const WebSocket = require('ws');

// ── Read .env ──────────────────────────────────────────────
const env = Object.fromEntries(
  fs.readFileSync('.env', 'utf8')
    .split('\n')
    .filter(l => l && !l.startsWith('#') && l.includes('='))
    .map(l => {
      const idx = l.indexOf('=');
      return [l.slice(0, idx).trim(), l.slice(idx + 1).trim()];
    })
);

const TOKEN = env.DERIV_API_TOKEN;
const APP_ID = env.DERIV_APP_ID || '';
if (!TOKEN) { console.error('❌ No DERIV_API_TOKEN found in .env'); process.exit(1); }
console.log('🔑 Token:', TOKEN.slice(0, 14) + '...' + TOKEN.slice(-6));
console.log('🆔 App ID:', APP_ID || '(not set)');
console.log('');

// ── HTTPS helper ───────────────────────────────────────────
function httpReq(method, url, headers = {}, body = null) {
  return new Promise((resolve) => {
    const u = new URL(url);
    const opts = { method, hostname: u.hostname, path: u.pathname + u.search, headers };
    const req = https.request(opts, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, body: data.trim() }); }
      });
    });
    req.on('error', e => resolve({ status: 0, body: e.message }));
    req.setTimeout(8000, () => { req.destroy(); resolve({ status: 0, body: 'Timeout' }); });
    if (body) req.write(body);
    req.end();
  });
}

// ── REST Tests ─────────────────────────────────────────────
async function restTests() {
  const BASE = 'https://api.derivws.com';

  if (!APP_ID || APP_ID === '1') {
    console.log('⚠️  DERIV_APP_ID not set in .env — skipping REST tests');
    console.log('   Go to developers.deriv.com → Registered apps → copy your App ID\n');
    return;
  }

  const approaches = [
    { label: `Bearer + Deriv-App-ID (${APP_ID})`, headers: { 'Authorization': `Bearer ${TOKEN}`, 'Deriv-App-ID': APP_ID, 'Content-Type': 'application/json' } },
  ];

  console.log('═══════════════════════════════════════════════');
  console.log('  REST API — /trading/v1/options/accounts');
  console.log('═══════════════════════════════════════════════');
  for (const a of approaches) {
    const r = await httpReq('GET', `${BASE}/trading/v1/options/accounts`, a.headers);
    const ok = r.status === 200 ? '✅' : '❌';
    console.log(`${ok} [${a.label}] → ${r.status}`);
    if (r.status === 200) console.log('   DATA:', JSON.stringify(r.body, null, 2));
    else console.log(`   ${JSON.stringify(r.body).slice(0, 120)}`);
  }
  console.log('');
}

// ── WebSocket test helper ──────────────────────────────────
function wsTest(label, wsUrl, messages, onMessage, timeoutMs = 12000) {
  return new Promise((resolve) => {
    console.log(`── ${label}`);
    console.log(`   URL: ${wsUrl}`);
    const ws = new WebSocket(wsUrl);
    const results = [];

    ws.on('open', () => {
      console.log('   ✅ Connected');
      messages.forEach(m => ws.send(JSON.stringify(m)));
    });

    ws.on('message', raw => {
      const data = JSON.parse(raw);
      const done = onMessage(data, results, ws);
      if (done) { ws.close(); }
    });

    ws.on('error', e => { console.log(`   ❌ Error: ${e.message}`); resolve(results); });
    ws.on('close', () => { console.log('   Connection closed\n'); resolve(results); });
    setTimeout(() => { ws.close(); }, timeoutMs);
  });
}

// ── WebSocket Tests ────────────────────────────────────────
async function wsTests() {
  console.log('═══════════════════════════════════════════════');
  console.log('  WebSocket Tests');
  console.log('═══════════════════════════════════════════════\n');

  // Test A: New public WS — authorize with PAT
  await wsTest(
    'New API — Public WS + authorize (PAT)',
    'wss://api.derivws.com/trading/v1/options/ws/public',
    [
      { authorize: TOKEN, req_id: 1 },
      { time: 1, req_id: 2 }
    ],
    (data, results, ws) => {
      console.log(`   MSG [req_id=${data.req_id}] type=${data.msg_type}`);
      if (data.error) console.log(`   ⚠️  error: ${data.error.code} — ${data.error.message}`);
      if (data.msg_type === 'authorize') {
        console.log('   🔐 Authorize result:', JSON.stringify(data.authorize || data.error, null, 2).split('\n').map(l => '   ' + l).join('\n'));
        results.push({ authorized: !data.error, data: data.authorize });
      }
      if (data.msg_type === 'time') {
        console.log(`   ⏰ Server time: ${new Date(data.time * 1000).toISOString()}`);
        return true; // done
      }
      return false;
    }
  );

  // Test B: Old WebSocket API — classic authorize
  await wsTest(
    'Old API — wss://ws.derivws.com + authorize',
    'wss://ws.derivws.com/websockets/v3?app_id=1',
    [
      { authorize: TOKEN, req_id: 1 },
    ],
    (data, results, ws) => {
      console.log(`   MSG [req_id=${data.req_id}] type=${data.msg_type}`);
      if (data.error) console.log(`   ⚠️  error: ${data.error.code} — ${data.error.message}`);
      if (data.msg_type === 'authorize') {
        console.log('   🔐 Authorize result:');
        const info = data.authorize;
        if (info) {
          console.log(`      loginid:      ${info.loginid}`);
          console.log(`      email:        ${info.email}`);
          console.log(`      currency:     ${info.currency}`);
          console.log(`      balance:      ${info.balance}`);
          console.log(`      account_type: ${info.account_type}`);
          console.log(`      scopes:       ${JSON.stringify(info.scopes)}`);
          console.log(`      country:      ${info.country}`);
        }
        results.push({ authorized: !data.error, data: info });
        return true; // done
      }
      return false;
    }
  );

  // Test C: Old API — get account list after authorize
  await wsTest(
    'Old API — balance after authorize',
    'wss://ws.derivws.com/websockets/v3?app_id=1',
    [
      { authorize: TOKEN, req_id: 1 },
    ],
    (data, results, ws) => {
      if (data.msg_type === 'authorize' && !data.error) {
        console.log(`   ✅ Authorized as: ${data.authorize?.loginid}`);
        // Now get balance + portfolio
        ws.send(JSON.stringify({ balance: 1, req_id: 2 }));
        ws.send(JSON.stringify({ portfolio: 1, req_id: 3 }));
        ws.send(JSON.stringify({ active_symbols: 'brief', contract_type: ['CALL','PUT'], req_id: 4 }));
      }
      if (data.msg_type === 'authorize' && data.error) {
        console.log(`   ❌ Auth failed: ${data.error.message}`);
        return true;
      }
      if (data.msg_type === 'balance') {
        console.log(`   💰 Balance: ${data.balance?.balance} ${data.balance?.currency}`);
        results.push({ balance: data.balance?.balance, currency: data.balance?.currency });
      }
      if (data.msg_type === 'portfolio') {
        const contracts = data.portfolio?.contracts || [];
        console.log(`   📋 Open contracts: ${contracts.length}`);
      }
      if (data.req_id === 4) {
        const syms = data.active_symbols || [];
        const v100 = syms.find(s => s.symbol === '1HZ100V' || s.underlying_symbol === '1HZ100V');
        console.log(`   📊 V100 available for CALL/PUT: ${v100 ? '✅ YES' : '❌ not in filtered list'}`);
        console.log(`   📊 Total active symbols: ${syms.length}`);
        return true;
      }
      return false;
    }
  );

  // Test D: ticks_history — no auth (confirm this still works)
  await wsTest(
    'Market Data — ticks_history V100 1m candles (no auth)',
    'wss://ws.derivws.com/websockets/v3?app_id=1',
    [
      { ticks_history: '1HZ100V', end: 'latest', count: 5, style: 'candles', granularity: 60, req_id: 1 }
    ],
    (data, results, ws) => {
      if (data.msg_type === 'candles') {
        console.log(`   📈 Got ${data.candles.length} candles:`);
        data.candles.forEach(c =>
          console.log(`      ${new Date(c.epoch * 1000).toISOString().slice(11,19)}  O:${c.open} H:${c.high} L:${c.low} C:${c.close}`)
        );
        return true;
      }
      if (data.error) { console.log(`   ❌ ${data.error.message}`); return true; }
      return false;
    }
  );
}

// ── Run ────────────────────────────────────────────────────
(async () => {
  await restTests();
  await wsTests();
  console.log('✅ All tests complete.');
})();
