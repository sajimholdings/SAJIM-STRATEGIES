# How to Get Your Deriv API Access

> **Goal:** Get everything needed to backtest V100 and execute Rise/Fall trades programmatically.

---

## What You Actually Need (and When)

| Purpose | What's Required |
|---------|----------------|
| Backtesting (`ticks_history`) | App ID only — no login needed |
| Demo trading (paper money) | App ID + OAuth2 `client_id` + demo account |
| Live trading (real money) | App ID + OAuth2 `client_id` + real account |

Start with **Step 1–2** if you just want to backtest. Add **Step 3** when you want to trade.

---

## Step 1 — Create a Deriv Account

If you don't have one already:

1. Go to 👉 https://deriv.com
2. Sign up (email, or social login)
3. You'll automatically get a **virtual/demo account** (no real money needed to start)

> ✅ You need a logged-in Deriv account to access the developer dashboard in Step 2.

---

## Step 2 — Register Your App → Get `App ID` + `client_id`

This is the most important step. All API calls require an App ID.

1. Go to 👉 https://api.deriv.com/dashboard  
   *(Log in with your Deriv account when prompted)*

2. Click **"Register Application"** (or "Create new app")

3. Fill in the form:

   | Field | What to put |
   |-------|------------|
   | **App Name** | e.g. `V100 Momentum Bot` |
   | **Redirect URI** | `http://localhost:3000/callback` (for local dev) or your actual app URL |
   | **Scopes** | Tick ✅ `trade` (to buy/sell) + ✅ `read` (to read account) |
   | **App Homepage** | Optional — can be blank or `http://localhost:3000` |

4. Click **Create** — you'll get:
   - **`App ID`** — a number (e.g. `12345`) — used in every API call as `Deriv-App-ID` header
   - **`client_id`** — used in the OAuth2 authorization URL

> 💾 **Save both values immediately.** You'll put them in your `.env` file.

---

## Step 3 — OAuth2 Login Flow (for trading)

> Skip this if you're only backtesting with `ticks_history` (public data, no auth needed).

### 3a. Build the Login URL

Paste this into a browser (replace the placeholders):

```
https://auth.deriv.com/oauth2/auth?
  response_type=code
  &client_id=YOUR_CLIENT_ID
  &redirect_uri=http://localhost:3000/callback
  &scope=trade
  &state=RANDOM_STRING_FOR_CSRF
  &code_challenge=YOUR_CODE_CHALLENGE
  &code_challenge_method=S256
```

### 3b. Generate PKCE (run this once in the browser console or Node)

```javascript
// Run in browser console or Node.js
const array = crypto.getRandomValues(new Uint8Array(64));
const codeVerifier = Array.from(array)
  .map(v => 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'[v % 66])
  .join('');

const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(codeVerifier));
const codeChallenge = btoa(String.fromCharCode(...new Uint8Array(hash)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

console.log('code_verifier:', codeVerifier);  // SAVE THIS
console.log('code_challenge:', codeChallenge); // USE IN URL
```

### 3c. Exchange the Code for a Token (backend)

After login, Deriv redirects to:
```
http://localhost:3000/callback?code=AUTH_CODE&state=YOUR_STATE
```

Then call:
```bash
curl -X POST https://auth.deriv.com/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "code=AUTH_CODE" \
  -d "code_verifier=YOUR_CODE_VERIFIER" \
  -d "redirect_uri=http://localhost:3000/callback"
```

You get back:
```json
{
  "access_token": "ory_at_...",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

> ⏱️ Token expires in **1 hour**. You'll need to re-login or implement token refresh.

---

## Step 4 — Get a WebSocket OTP (for live trading)

```bash
curl -X POST "https://api.derivws.com/trading/v1/options/accounts/YOUR_ACCOUNT_ID/otp" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Deriv-App-ID: YOUR_APP_ID"
```

Response:
```json
{
  "data": {
    "url": "wss://api.derivws.com/trading/v1/options/ws/demo?otp=abc123..."
  }
}
```

Connect to that URL directly — no further auth needed on the WebSocket.

---

## Step 5 — Test It (Backtesting, No Auth)

You can test immediately with just the App ID — no OAuth needed for historical data:

```javascript
const ws = new WebSocket('wss://api.derivws.com/trading/v1/options/ws/public');

ws.onopen = () => {
  ws.send(JSON.stringify({
    ticks_history: '1HZ100V',  // V100
    end: 'latest',
    count: 1000,
    style: 'candles',
    granularity: 60,           // 1-minute candles
    req_id: 1
  }));
};

ws.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.msg_type === 'candles') {
    console.log('Got', data.candles.length, 'candles');
    // Each candle: { epoch, open, high, low, close }
    // Now compute your 10 EMA, 30 EMA, RSI here
  }
};
```

> ✅ No App ID needed even for this — the public endpoint is fully open.

---

## Your `.env` File (once you have credentials)

```env
DERIV_APP_ID=12345
DERIV_CLIENT_ID=your_oauth_client_id
DERIV_REDIRECT_URI=http://localhost:3000/callback
```

---

## Summary — The Minimum Path

```
1. deriv.com → Create account (free)
        ↓
2. api.deriv.com/dashboard → Register app → get App ID + client_id
        ↓
3. Test ticks_history on public WS (no auth, works immediately)
        ↓
4. When ready to trade → OAuth2 login → access token → OTP → trade WS
```

---

## Key Links

| Resource | URL |
|----------|-----|
| Create Deriv account | https://deriv.com |
| App registration dashboard | https://api.deriv.com/dashboard |
| OAuth2 auth endpoint | https://auth.deriv.com/oauth2/auth |
| API docs | https://developers.deriv.com/docs/ |
| API playground (test calls live) | https://developers.deriv.com/playground |
