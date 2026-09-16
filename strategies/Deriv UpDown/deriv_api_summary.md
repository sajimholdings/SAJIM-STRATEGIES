# Deriv API — Developer Summary

> **Source:** https://developers.deriv.com/llms.txt  
> **API Version:** Current production API  
> **Base REST URL:** `https://api.derivws.com`  
> **Base WebSocket URL:** `wss://api.derivws.com/trading/v1/options/ws/{endpoint}`

---

## 1. Architecture Overview

Deriv's API is **WebSocket-first** with a supporting REST layer. The typical pattern is:

1. Use **REST** to authenticate (OAuth2) and get a one-time password (OTP)
2. Use the **OTP to open an authenticated WebSocket** for all real-time trading
3. Use the **public WebSocket** (no auth) for market data only

```
OAuth2 (REST) → Access Token
Access Token + REST POST /otp → WebSocket URL with OTP
WebSocket (OTP URL) → Trade, subscribe, manage contracts
```

---

## 2. Authentication (OAuth 2.0 + PKCE)

**Endpoints:**
- Auth: `https://auth.deriv.com/oauth2/auth`
- Token: `https://auth.deriv.com/oauth2/token`

### Flow
1. Generate a `code_verifier` (random 43–128 char string) + `code_challenge` = `BASE64URL(SHA256(verifier))`
2. Redirect user to auth URL with `response_type=code`, `scope=trade` or `admin`, `code_challenge`, `state`
3. Deriv redirects back with `?code=AUTH_CODE&state=...`
4. **Backend** POSTs code + verifier to token endpoint → gets `access_token`
5. Use `Authorization: Bearer YOUR_ACCESS_TOKEN` on all REST calls

### Scopes
| Scope | Use |
|-------|-----|
| `trade` | Execute trades, read account |
| `admin` | Create accounts |
| `account_manage` | Create/manage accounts |

### Sign Up (vs Login)
Add `prompt=registration` to the auth URL. Optionally add UTM params (`utm_source`, `utm_medium=affiliate`, `sidc`) for partner tracking.

### Common Auth Errors
| Error | Cause |
|-------|-------|
| State mismatch | Store `state` in `sessionStorage` before redirect |
| `invalid_grant` | Use original `code_verifier`, exchange immediately |
| Redirect URI mismatch | Must exactly match registration — check trailing slashes |
| `invalid_client` | Wrong `client_id` |

---

## 3. WebSocket Connection

### Public (no auth)
```javascript
const ws = new WebSocket('wss://api.derivws.com/trading/v1/options/ws/public');
```

### Authenticated (via OTP)
```javascript
// Step 1: Get OTP from REST
const res = await fetch('https://api.derivws.com/trading/v1/options/accounts/YOUR_ACCOUNT_ID/otp', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer TOKEN', 'Deriv-App-ID': 'YOUR_APP_ID' }
});
const { data } = await res.json();

// Step 2: Connect with returned URL
const ws = new WebSocket(data.url); // Already authenticated — no authorize step needed
```

---

## 4. WebSocket API — Endpoint Reference

### 4.1 Account Management (auth required)

| Endpoint | What it does |
|----------|-------------|
| `balance` | Current balance; subscribe for real-time updates |
| `portfolio` | List of all open contracts |
| `profit_table` | Profit/loss summary for completed trades |
| `statement` | Full transaction history |
| `transaction` | Real-time transaction notifications (subscribe only) |

### 4.2 Market Data (no auth required)

| Endpoint | What it does |
|----------|-------------|
| `active_symbols` | All tradable symbols; filter by contract type |
| `contracts_for` | Contract types available for a specific symbol |
| `contracts_list` | All contract categories on the platform |
| `ticks` | Real-time price stream for a symbol |
| `ticks_history` | Historical OHLC or tick data |

#### `ticks_history` — key for backtesting
```json
{
  "ticks_history": "1HZ100V",
  "end": "latest",
  "count": 5000,
  "style": "candles",
  "granularity": 60
}
```
Allowed `granularity` values (seconds): `60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400`

### 4.3 Trading Operations (auth required)

| Endpoint | What it does |
|----------|-------------|
| `proposal` | Get a price quote before buying; subscribe for live price updates |
| `buy` | Purchase a contract using a `proposal.id` |
| `sell` | Sell an open contract before expiry |
| `proposal_open_contract` | Real-time status of an open contract |
| `contract_update` | Modify stop loss / take profit on open contract |
| `contract_update_history` | History of updates on a contract |
| `cancel` | Cancel a contract (where available) |

#### The minimal trade loop
```
proposal → buy (using proposal.id) → proposal_open_contract (monitor) → sell (optional)
```

### 4.4 Subscription Management (no auth required)

| Endpoint | What it does |
|----------|-------------|
| `forget` | Cancel one subscription by ID |
| `forget_all` | Cancel all subscriptions of a type (e.g. `"ticks"`, `"proposal"`) |

### 4.5 System Endpoints (no auth)

| Endpoint | What it does |
|----------|-------------|
| `ping` | Check connectivity → returns `"pong"` |
| `time` | Get current server time (epoch) |
| `trading_times` | Trading hours for all symbols on a given date |

---

## 5. REST API Endpoints

Base URL: `https://api.derivws.com`

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/trading/v1/options/accounts` | List all options accounts | Bearer token |
| POST | `/trading/v1/options/accounts` | Create a new options account | Bearer token |
| POST | `/trading/v1/options/accounts/{id}/reset-demo-balance` | Reset demo balance | Bearer token |
| POST | `/trading/v1/options/accounts/{id}/otp` | Get OTP → returns WebSocket URL | Bearer token |
| GET | `/trading/v1/options/ws/public` | Public WebSocket (no OTP) | None |
| GET | `/v1/health` | Health check | None |

---

## 6. Contract Types — V100 Relevant

> Always confirm availability with `contracts_for` — types change over time.

| Code | Category | Win Condition |
|------|----------|--------------|
| `CALL` | Rise/Fall | Exit spot > entry spot |
| `PUT` | Rise/Fall | Exit spot < entry spot |
| `HIGHER` | Rise/Fall | Exit spot > barrier |
| `LOWER` | Rise/Fall | Exit spot < barrier |
| `DIGITEVEN` | Digits | Last digit is even |
| `DIGITODD` | Digits | Last digit is odd |
| `ONETOUCH` | Touch | Market touches barrier |
| `NOTOUCH` | Touch | Market never touches barrier |
| `MULTUP` / `MULTDOWN` | Multipliers | Direction × multiplier |
| `ACCU` | Accumulators | Price stays within band |

**Symbol for V100:** `1HZ100V`

---

## 7. Error Handling

All errors return the same structure:
```json
{
  "error": {
    "code": "AuthorizationRequired",
    "message": "Please log in."
  },
  "msg_type": "authorize"
}
```

| Code | Meaning |
|------|---------|
| `AuthorizationRequired` | Need to log in first |
| `InvalidToken` | Token expired or invalid |
| `RateLimit` | Too many requests |
| `InputValidationFailed` | Bad request parameters |
| `ContractNotFound` | Contract doesn't exist |
| `InsufficientBalance` | Not enough funds |
| `InternalError` | Server-side error |

---

## 8. Rate Limits

| Limit | Value |
|-------|-------|
| WebSocket requests | 100 req/sec per connection |
| REST API | 60 req/min per token |
| Active subscriptions | 100 per connection |
| Concurrent WebSocket connections | 5 per user |

---

## 9. WebSocket Best Practices

1. **Ping every 30 seconds** to keep the connection alive
2. **Use `req_id`** on every message to match responses to requests
3. **Exponential backoff** on reconnects
4. **Clean up subscriptions** with `forget`/`forget_all` when done
5. **Always check for `error`** in responses before processing `data`

---

## 10. Full Trade Execution Flow (Code)

```javascript
// 1. Get OTP
const otp = await fetch('https://api.derivws.com/trading/v1/options/accounts/ACCOUNT_ID/otp', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer TOKEN', 'Deriv-App-ID': 'APP_ID' }
}).then(r => r.json());

// 2. Open authenticated WebSocket
const ws = new WebSocket(otp.data.url);

ws.onopen = () => {
  // 3. Get proposal
  ws.send(JSON.stringify({
    proposal: 1, amount: 10, basis: "stake",
    contract_type: "CALL", currency: "USD",
    duration: 5, duration_unit: "t",
    underlying_symbol: "1HZ100V", req_id: 1
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  // 4. Buy on proposal
  if (data.msg_type === 'proposal') {
    ws.send(JSON.stringify({ buy: data.proposal.id, price: data.proposal.ask_price, req_id: 2 }));
  }

  // 5. Monitor contract
  if (data.msg_type === 'buy') {
    ws.send(JSON.stringify({
      proposal_open_contract: 1, contract_id: data.buy.contract_id, subscribe: 1, req_id: 3
    }));
  }

  // 6. Sell early if profitable
  if (data.msg_type === 'proposal_open_contract' && data.proposal_open_contract.profit > 5) {
    ws.send(JSON.stringify({
      sell: data.proposal_open_contract.contract_id,
      price: data.proposal_open_contract.bid_price, req_id: 4
    }));
  }
};
```

---

## 11. Quick Reference

| Layer | Count | Key Calls |
|-------|-------|-----------|
| OAuth | 2 | `/oauth2/auth`, `/oauth2/token` |
| REST | 6 | `/accounts`, `/otp`, `/health` |
| WebSocket | 22 | `proposal`, `buy`, `sell`, `ticks_history`, `balance` |

**Docs:** https://developers.deriv.com/docs/  
**Playground:** https://developers.deriv.com/playground  
**Support:** api-support@deriv.com
