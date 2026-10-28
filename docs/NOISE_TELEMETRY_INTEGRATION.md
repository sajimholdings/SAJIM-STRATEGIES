# Noise Telemetry — what it is and how we integrate it into the Strategy Tester

_Status as of 2026-10-12: Python engine copied in and verified. Dashboard work not started._

## 1. What this is

**Noise prediction** forecasts how violent the market is likely to be: the expected *size* of price moves, never their direction. The tester uses it in two ways:

1. **Market telemetry:** for every symbol, the current volatility forecast, how it ranks against the past year (calm / normal / storm), and how noise is distributed across the trading day.
2. **Strategy diagnosis:** for any backtest result, the noise state at each trade's entry, with P&L broken down by it. This answers "does this strategy only make money in calm markets?" and "is its edge bigger than the noise it trades in?"

It was built standalone in `NoisePrediction/` (which started as a copy of the third-party GARCH Method skill, MIT licence). It now lives in the tester as **`sajim_quant/noise/`**. **From here on, `sajim_quant/noise/` is the canonical copy.** `NoisePrediction/` is a sandbox; don't let the two drift apart.

## 2. The model

| Step | What happens | Where |
|---|---|---|
| Data | Dukascopy **M5** parquet, `data/parquet/<SYMBOL>/M5.parquet`, naive UTC (same clock as the tester's trades) | `data.py` |
| Returns | Close-to-close log returns. Returns spanning gaps over 30 min (weekends, holes) are dropped. Trading day = UTC + 3h, so the Sunday-evening open counts as Monday. | `data.py` |
| Daily realized variance | Sum of squared M5 returns per trading day. Days with under 50% of the usual bar count are dropped. | `model.py` |
| Forecast | **HAR-RV** on logs (yesterday, 5-day and 22-day means), refitted **every day** on an expanding window, with lognormal bias correction. 40-day warm-up. | `model.py` |
| Regime | Percentile of today's forecast against the previous 250 forecasts: calm under 33, storm over 67. Needs 60 prior forecasts, otherwise `unranked`. | `model.py` |
| Intraday profile | Share of daily variance in each UTC hour, averaged over the **previous** 60 days. Turns the daily forecast into an expected noise level per M5 bar. | `model.py` |
| Trade tags | `np_*` fields written into `Trade.custom_fields`. **The `Trade` schema is not changed.** | `diagnose.py` |

**No lookahead:** a trade at time T only sees bars that closed by T, plus forecasts made before T's trading day. `tests/test_noise.py` checks this by changing future prices and asserting that past forecasts and trade tags stay identical.

**Out-of-sample quality** (QLIKE, lower is better):

| | HAR-RV | same as yesterday | 22-day mean |
|---|---|---|---|
| XAUUSD | **0.160** | 0.182 | 0.294 |
| EURUSD | **0.140** | 0.179 | 0.181 |

### Per-trade fields (`custom_fields`)
| Field | Meaning |
|---|---|
| `np_status` | `ok` / `warming_up` / `no_data` |
| `np_forecast_day` | Trading day the forecast applies to |
| `np_vol_daily_pct`, `np_vol_ann_pct` | Forecast volatility for that day |
| `np_vol_pctile`, `np_regime` | Rank against the past year → calm / normal / storm / unranked |
| `np_session_factor` | How noisy the entry hour usually is (1.0 = average hour) |
| `np_bar_sigma_pct` | Expected size of one M5 move at entry |
| `np_recent_vs_expected` | Realized noise in the hour before entry ÷ expected (above 1 = louder than usual) |
| `np_hold_sigma_pct` | Expected noise over the trade's holding window |
| `np_move_sigmas` | Trade's move in its own direction ÷ that noise. This is the edge-vs-noise measure. |

## 3. What already works

```
python -m sajim_quant.noise evaluate --symbol XAUUSD          # forecast quality vs baselines
python -m sajim_quant.noise forecast --symbol XAUUSD --save   # latest forecast + 24h profile (JSON); --save writes parquet
python -m sajim_quant.noise diagnose data/results/<X>_result.json [--json]
python -m pytest tests/test_noise.py
```

- Output goes to `data/noise/` (`forecasts/`, `diagnose/`). This is deliberately **not** `data/results/`, so the dashboard's result catalog and leaderboard are unaffected.
- `diagnose` writes `<stem>_noise_enriched.json` (full result plus tags plus `noise_summary`) and `<stem>_noise_report.md`.
- Runs in about 4–5 seconds per symbol. Python dependencies are only pandas, numpy and pyarrow, all already in `requirements.txt`.

## 4. The telemetry dashboard (to build)

A new **"Noise"** tab in `sim/src/app/page.tsx`, next to testing / universe / leaderboard / correlations / agent, plus small hooks into the Testing Cockpit.

### 4a. Noise tab: market telemetry
- **Symbol grid:** one card per symbol with the forecast annualized vol, percentile, regime chip (calm / normal / storm colours), and the current hour's session factor.
- **Selected symbol:**
  - Forecast vs realized daily vol (line chart, last ~250 days) with storm periods shaded.
  - Intraday profile: 24 bars, one per UTC hour, with the current hour highlighted.
  - Forecast-quality strip from `evaluate`, so the user can see the model beats the baselines.

### 4b. Noise tab: strategy diagnosis
- A result picker reusing `/api/results`.
- A regime / session / recent-noise breakdown: trades, win %, net P&L, profit factor, average move in noise units, with a small-sample flag under 30 trades.
- A scatter of P&L against volatility percentile.
- The findings list (plain-English lines from `noise_summary.findings`).

### 4c. Testing Cockpit hooks
- **Equity chart:** shade calm/storm regimes behind `EquityDrawdownChart` (by date ranges).
- **TradeTable:** a regime chip column, plus `np_*` fields in the expanded row.
- **After a run:** a "Noise diagnosis" card (regime table plus findings), filled automatically once `/api/run-test` succeeds.

## 5. Data flow

```
data/parquet/<SYM>/M5.parquet
        │  python -m sajim_quant.noise …   (all model work stays in Python)
        ▼
data/noise/forecasts/<SYM>.json          ← compact: last 250 daily rows + today's profile
data/noise/diagnose/<stem>_noise_summary.json   ← summary only (no trades)
data/noise/diagnose/<stem>_noise_enriched.json  ← full tagged trades (on demand only)
        │  Next.js route handlers read files; spawn Python only if missing/stale
        ▼
GET /api/noise/market?symbol=XAUUSD
GET /api/noise/diagnose?file=<result file>&source=data/results
```

Rules:
- **No modelling in TypeScript.** The dashboard only reads JSON the Python side produced. This matches the existing "never fabricate numbers" rule in `lib/results.ts`.
- **Spawn Python the way `api/run-test/route.ts` does:** `PYTHON_BIN`, `execFile`, a fixed argument list, file names validated against the result catalog (`lib/resultFiles.ts`). No paths from the client.
- **Freshness:** regenerate when the parquet file's mtime is newer than the JSON's.
- **AI agent:** add a `noise_diagnose` tool to `api/chat/route.ts` that returns **`noise_summary` only**, never the tagged trades (CLAUDE.md truncation rule).
- `sim/AGENTS.md`: this Next.js version has breaking changes. Read `sim/node_modules/next/dist/docs/` before writing the new routes.

## 6. Implementation phases

- [ ] **P1. Python output for the dashboard**
  - `forecast --dashboard-json` writes `data/noise/forecasts/<SYM>.json`.
  - A `market` command runs it for all 20 symbols.
  - `diagnose` also writes `_noise_summary.json`.
  - Add a test for the JSON shapes.
- [ ] **P2. API routes:** `api/noise/market`, `api/noise/diagnose`, plus types in `sim/src/lib/noise.ts`.
- [ ] **P3. Noise tab, telemetry half (4a).**
- [ ] **P4. Noise tab, diagnosis half (4b).**
- [ ] **P5. Cockpit hooks (4c):** regime shading, trade-table chip, auto-diagnosis after a run (optional flag in `run-test`).
- [ ] **P6. Agent tool** `noise_diagnose` (summary only).
- [ ] **Later:**
  - What-if filter: re-score a strategy keeping only chosen regimes, judged out of sample so it isn't fitted to the backtest.
  - Volatility-targeted sizing comparison.
  - Longer daily history from MT5 to shorten the warm-up.
  - A GARCH challenger model.
  - A live MT5 feed for real-time telemetry.

## 7. Known issues and limits

- **Two years of data** (Sep 2024 – Sep 2026). Trades before about late Nov 2024 aren't diagnosed (warm-up), and regime ranking starts around Feb 2025. Trades in between are `unranked`.
- **The hour profile ignores weekday and daylight saving.** It adapts over its 60-day window but lags for a few weeks after clock changes. For holds over a weekend, expected noise is overstated.
- **Deriv synthetics** (`1HZ100V`, `SYNTHETICS`) have no M5 parquet, so they can't be diagnosed. The CLI exits with a clear error.
- **`BTCUSDT` maps to `BTCUSD`** through the aliases in `diagnose.py`. BTCUSD M1 ends in Apr 2025, so check M5 coverage before trusting BTC diagnoses.
- **Tester bugs found along the way (not fixed):**
  - The SunriseOgle (backtrader) results have `exit_price == entry_price` on every trade while `net_pnl` is about ±$1,000. The diagnosis skips the move measure for these trades and adds an `np_move_note`.
  - `BEEP_Gold_Vacuum` = `beep_vacuum_hunter`, and the two GoldPullbackAI files are identical. These look like duplicate runs that double-count on the leaderboard.

## 8. Decisions to make when we meet

1. A separate **Noise tab** (proposed), or everything inside the Testing Cockpit?
2. Should diagnosis run **automatically after every test**, or only on demand?
3. **"Telemetry" refresh:** the parquet store is static today. Refreshing when the data downloader updates it is enough for now. Do we want a live MT5 feed later?
4. Should the `np_*` tags stay in separate files under `data/noise/` (proposed, keeps results immutable), or be written back into `data/results`?
