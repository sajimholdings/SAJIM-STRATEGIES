# Trading Bot — BTC Fade + ETH Momentum

Production Python trading bot system connecting to Binance. Two independent strategies sharing common infrastructure, each run via cron once per hour.

---

## Architecture

```
trading_bot/
├── config.py           # API keys, Telegram, risk params, strategy params
├── exchange.py         # Binance REST API wrapper with retry logic
├── indicators.py       # ATR (Wilder), EMA, SMA — pure functions
├── risk.py             # Position sizing, step-size rounding
├── notifications.py    # Telegram Bot API sender
├── state.py            # JSON-based persistent state (atomic writes)
├── bot_btc_fade.py     # Strategy 1: Mean-reversion (fade big candles)
├── bot_eth_momentum.py # Strategy 2: Trend-following (trailing stop)
├── run_btc.py          # Entry point for BTC bot (called by cron)
├── run_eth.py          # Entry point for ETH bot (called by cron)
├── daily_summary.py    # Daily PnL summary (called by cron at 00:05 UTC)
├── hourly_summary.py   # On-demand hourly summary
├── validate.py         # Self-test suite (run before going live)
├── requirements.txt    # Python dependencies
├── state/
│   ├── btc_state.json  # BTC bot persistent state (auto-created)
│   └── eth_state.json  # ETH bot persistent state (auto-created)
└── logs/
    ├── btc.log         # BTC bot log (auto-created)
    ├── eth.log         # ETH bot log (auto-created)
    └── summary.log     # Daily summary log (auto-created)
```

---

## Quick Start (Paper Trading)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure `config.py`
- Leave `PAPER_MODE = True`
- Leave `BINANCE_API_KEY` and `BINANCE_API_SECRET` blank (paper mode uses real market data but no real orders)
- Optionally fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for alerts

### 3. Run validation tests
```bash
cd trading_bot
python validate.py
```
All tests should pass before running live.

### 4. Test a single bot run
```bash
python run_btc.py
python run_eth.py
```
Check `logs/btc.log` and `logs/eth.log`. State files will be created in `state/`.

### 5. Run the daily summary manually
```bash
python daily_summary.py
```

---

## VPS Cron Setup

SSH into your VPS and add these cron jobs:

```bash
crontab -e
```

Paste (adjust the path):
```
# BTC Fade bot — every hour at :02
2 * * * * cd /path/to/trading_bot && /usr/bin/python3 run_btc.py >> logs/btc_cron.log 2>&1

# ETH Momentum bot — every hour at :03
3 * * * * cd /path/to/trading_bot && /usr/bin/python3 run_eth.py >> logs/eth_cron.log 2>&1

# Daily summary — 00:05 UTC
5 0 * * * cd /path/to/trading_bot && /usr/bin/python3 daily_summary.py >> logs/summary.log 2>&1
```

**Why :02 and :03?** Binance sometimes closes the hourly candle a few seconds late. Running at minute 2/3 guarantees `candles[-2]` is fully settled.

### Auto-start on reboot
```bash
# Add to crontab:
@reboot sleep 30 && cd /path/to/trading_bot && /usr/bin/python3 run_btc.py >> logs/btc_startup.log 2>&1
```

---

## Telegram Setup

1. Message `@BotFather` on Telegram → `/newbot` → copy the token
2. Start a conversation with your bot, then visit:  
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Copy your `chat_id` from the JSON response
4. Add both to `config.py`

If `TELEGRAM_BOT_TOKEN` is blank, notifications are silently skipped (logged locally instead).

---

## Going Live (Paper → Live Transition)

**Pre-flight checklist:**
- [ ] Paper traded for 4+ weeks with no bugs
- [ ] At least 20 paper trades completed per strategy
- [ ] Paper PnL is positive and matches backtest characteristics
- [ ] All Telegram alerts tested and working
- [ ] State recovery tested: kill bot mid-run → next run continues correctly
- [ ] `validate.py` passes all tests

**Switching to live:**
1. Open `config.py`
2. Set `PAPER_MODE = False`
3. Fill in `BINANCE_API_KEY` and `BINANCE_API_SECRET`
4. **API key permissions**: Enable "Read" + "Trade" only. **Never enable Withdrawals.**
5. Run `python run_btc.py` once manually and verify it places a real order
6. Set up cron jobs on VPS

---

## Monitoring

### Reading state
```bash
cat state/btc_state.json | python -m json.tool
cat state/eth_state.json | python -m json.tool
```

### Tailing logs
```bash
tail -f logs/btc.log
tail -f logs/eth.log
```

### Manual summary
```bash
python daily_summary.py
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Binance API timeout | Retries 3× with 5s delay |
| HTTP 429 (rate limit) | Waits 60s, retries once |
| Binance 5xx error | Retries 3× with 5s delay |
| Order placed but bot crashes | `pending_order=True` flag warns on next run |
| State file corrupted | Raises `ValueError`, halts bot, alerts via Telegram |
| Insufficient balance | Logs warning, skips order |
| Order below min notional | Logs warning, skips order |

### `pending_order` flag
If the bot crashes between placing an order and updating state, `pending_order` will be `True` on the next run. **You must manually check Binance and reconcile `state/*.json`** before restarting. The bot logs a warning but does not auto-reconcile (to avoid double-orders).

---

## Strategy Parameters

### BTC Fade
| Parameter | Value | Meaning |
|---|---|---|
| `BTC_ATR_PERIOD` | 14 | ATR calculation period |
| `BTC_ATR_MA_PERIOD` | 50 | ATR smoothing period for vol filter |
| `BTC_VOL_THRESHOLD` | 1.0 | ATR must be > 1.0× its SMA |
| `BTC_BODY_ATR_ENTRY` | 1.75 | Candle body must exceed 1.75× ATR |
| `BTC_EMA_PERIOD` | 14 | Mean-reversion target |
| `BTC_TP_MEAN_FRACTION` | 0.7 | TP at 70% of way back to EMA |
| `BTC_SL_ATR_MULTIPLE` | 1.5 | SL at 1.5× ATR beyond candle extreme |
| `BTC_MAX_BARS` | 12 | Force-close after 12 hours |

### ETH Momentum
| Parameter | Value | Meaning |
|---|---|---|
| `ETH_BODY_ATR_ENTRY` | 2.0 | Candle body must exceed 2.0× ATR |
| `ETH_TRAIL_ATR_MULTIPLE` | 4.0 | Trailing stop at 4× ATR from peak/trough |
| `ETH_MAX_BARS` | 48 | Force-close after 48 hours |
| `ETH_PARTIAL_TP_ATR` | 2.0 | Take partial at 2× ATR profit |
| `ETH_PARTIAL_PCT` | 0.4 | Close 40% at partial TP |

---

## Important Notes

### SHORT trades on Binance Spot
Both bots can take SHORT positions. On Binance **spot**, shorting without holding the base asset is not supported. In `PAPER_MODE`, SHORT simulation works as pure accounting. For live SHORT trades, you have two options:
1. **Accept spot-only**: modify `bot_btc_fade.py` and `bot_eth_momentum.py` to skip `direction == "SHORT"` entries
2. **Switch to Binance Futures**: change `BINANCE_BASE_URL` to `https://fapi.binance.com` and update `exchange.py` to use the Futures API endpoints

### Starting balance
Set `PAPER_STARTING_BALANCE` in `config.py`. Each bot gets its own independent balance starting at this value. In paper mode, the balance reflects simulated trade outcomes. In live mode, balance is read from Binance.
