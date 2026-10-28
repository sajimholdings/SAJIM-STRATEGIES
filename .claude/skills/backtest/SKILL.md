---
name: backtest
description: Run a SAJIM strategy backtest on Modal cloud, run its noise (volatility-regime) diagnosis, and give a critical analysis. Use when the user asks to backtest, test, re-run or analyze a strategy from the catalog, e.g. "/backtest beep_vacuum_hunter XAUUSD M5" or "how does smc_daemon do on EURUSD H1".
argument-hint: "<strategy_id> [SYMBOL] [TF]"
---

# Backtest on Modal + analysis

Replaces the dashboard's AI chat. All heavy compute runs on the deployed Modal app `sajim-quant-cloud` (market data in volume `sajim-market-data`, outputs in `sajim-results`) through its HTTP API — the client below starts a detached job and polls it, so it survives a flaky connection. Never run `python -m sajim_quant test` locally unless the user explicitly asks for a local run.

If strategy code or `sajim_quant/` changed since the last deploy, redeploy first (mounts are snapshotted at deploy time): `python -X utf8 -m modal deploy sajim_quant/cloud/modal_runner.py`.

## 1. Resolve the strategy

Catalog: `sajim_quant/core/strategy_catalog.json` (`id`, `run_adapter`, `default_symbol`, `default_timeframe`, `supported_symbols`, `supported_timeframes`).

- Match the user's strategy to an `id` (case-insensitive, partial names OK; ask if ambiguous).
- Symbol / timeframe default to the entry's `default_symbol` / `default_timeframe`. Warn (don't block) if outside `supported_*`.
- `run_adapter == "mt5"` → cannot run on Modal (needs the Windows MetaTrader terminal). Say so and stop; offer the dashboard's Local engine.
- Available market data symbols are the folders in `data/parquet/`.

## 2. Run the backtest on Modal

From the repo root, in the background (1–4 minutes including container start):

```
python -X utf8 -m sajim_quant.cloud.client backtest --strategy <id> --symbol <SYMBOL> --tf <TF> [--balance 10000]
```

Success line: `[OK] Cloud Execution Response: success=True`. On `success=False`, report the `Error:` line and stop — never fall back to a local run. If the client times out it prints the job id; `python -X utf8 -m sajim_quant.cloud.client job <job_id> --save` fetches and stores the result once it finishes.
The result is written to `data/results/<id>_<SYMBOL>_<TF>_result.json` (and mirrored to `sim/public/test_results/`), so the dashboard picks it up.

## 3. Run the noise diagnosis on Modal

```
python -X utf8 -m sajim_quant.cloud.client noise --result data/results/<id>_<SYMBOL>_<TF>_result.json
```

Writes `data/noise/diagnose/<id>_<SYMBOL>_<TF>_noise_enriched.json` + `_noise_report.md` (the dashboard's Noise Regime panel reads these). A "No M5 data" error just means that symbol has no noise data — continue without it.

## 4. Summarize — never read the raw result JSON

```
python .claude/skills/backtest/summarize.py data/results/<id>_<SYMBOL>_<TF>_result.json
```

Prints metadata, engine metrics, monthly P&L, trade aggregates and the noise summary. Do **not** Read/cat the result or enriched JSON directly — they hold thousands of trades.

## 5. Analysis to give the user

Be a critical quant, not a cheerleader:

- Headline: net P&L, return %, trades, win rate, profit factor, Sharpe/Sortino, max drawdown, test window.
- Robustness: is profit concentrated (top-5 share, a few months)? Long vs short asymmetry? Max losing streak? Fewer than ~100 trades → say the sample is thin.
- Red flags: martingale/grid-like drawdowns, PF < 1.1 after costs, monthly P&L dominated by one month, win rate high but expectancy negative.
- Noise: which regime / session / pre-entry-noise bucket makes or loses money; flag `small_sample` and `unranked`/`warming_up` counts (the noise model needs ~6 months of history before it ranks regimes). Volatility forecasts measure size of moves, not direction.
- If the result differs from a previous run of the same file, say what changed (data window, trade count).
- End with 1–3 concrete next tests (e.g. a session filter, another symbol/timeframe) — don't run them unasked.

Remind the user the full tear sheet + Noise panel are on the dashboard (http://localhost:3001).
