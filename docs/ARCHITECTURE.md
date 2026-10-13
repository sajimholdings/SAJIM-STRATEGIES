# Architecture

## The one idea

Runtimes differ (Backtrader bar loop, MT5 tick tester, TradingView fills, Deriv ticks). We do not try
to make them execute identically. Each adapter produces trades in the **Universal Trade Schema**
(`sajim_quant/core/schema.py`: `Trade`, `RunMetadata`, `BacktestResult`) and everything downstream
(metrics, Monte Carlo, diagnosis, tiering, dashboard) works on that schema only.

```
strategies/<X>  --adapter-->  BacktestResult  --record_run-->  results/<id>/<run>/  --sajim export-->  apps/dashboard/public/*.json
```

## Layout

| Path | Role | Rules |
|---|---|---|
| `sajim_quant/core` | schema, discovery | frozen contract; additive changes only |
| `sajim_quant/data` | `DataLoader` (Parquet with CSV fallback), synthetic fixture generator | reads `data/`; `SAJIM_DATA_DIR` overrides |
| `sajim_quant/adapters` | `python/backtrader_adapter.py`, `mql5/*` (Windows only), `pine/`, `csv_importer.py` | return `BacktestResult`; never write files |
| `sajim_quant/analytics` | `metrics.py`, `monte_carlo.py`, `behavioral_diagnostics.py`, `portfolio.py`, `report.py`, `universe_matrix.py` | pure functions over the schema; no I/O except `universe_matrix` |
| `sajim_quant/registry` | `store.py` (record/list/detail), `tiers.py` (one tier rule), `export.py` (dashboard JSON), `legacy.py` | the only code that writes `results/` |
| `sajim_quant/cli.py` | `sajim list / export / import-legacy / show / remove / run-all` | |
| `scripts/run_*.py` | one runner per strategy/engine; ends with `record_run(...)` | need market data; not collected by pytest |
| `tests/` | pytest, offline, synthetic fixtures (`conftest.make_result`, `data/synthetic.py`) | must stay green in CI |
| `results/` | `registry.json` + `<strategy_id>/<run_id>/{result.json,trades.csv,metrics.json,monte_carlo.json,diagnosis.json,tear_sheet.md}` | committed; small; regenerate rather than hand-edit |
| `apps/dashboard` | Next.js cockpit; reads `public/*.json` and `results/` through its API routes | never computes metrics; `SAJIM_RESULTS_DIR` overrides |
| `apps/sajim_traders`, `apps/sajim_holdings_sandbox` | live-trading products (bots, Flask/Next front ends, Supabase, Telegram) | independent apps; the sandbox is a drifted fork of SajimTraders with newer config. `MT5_Sajim/` (a whole MetaTrader install) is untracked; keep it locally |
| `strategies/` | strategy source per folder; add a `sajim_strategy.yaml` (see `TEMPLATE_STRATEGY`) so discovery can register it | large external payloads (Scalper1 agent tooling, Deriv study material) are gitignored |
| `tools/` | `data_downloader` (Dukascopy → `data/dukascopy`), `deriv_edge_detector` | |
| `archive/legacy_generators` | `timeframe_matrix.py`, `update_tf_matrix.py`, `generate_all_folders_leaderboard.py`: produced hardcoded numbers; superseded by the registry | reference only |
| `docs/` | this file, the original design narrative, the problem statement, the audit prompt | |

## Data flow in detail

1. **Data** – `tools/data_downloader/download_all.py` writes `data/dukascopy/{SYMBOL}/{TF}.csv`;
   `DataLoader().build_cache()` converts to `data/parquet/{SYMBOL}/{TF}.parquet`.
2. **Run** – a script in `scripts/` builds an adapter, calls `.run(symbol, timeframe, ...)` and gets a
   `BacktestResult` with `RunMetadata` (dates, balance, spread/commission/slippage assumptions).
3. **Record** – `record_run(result, strategy_id=..., folder=..., description=..., tags=[...])`
   computes `calculate_metrics`, `MonteCarloSimulator.simulate` on the real PnL list,
   `BehavioralDiagnosticsEngine.diagnose`, `classify_tier`, the tear sheet, and writes the run folder.
   `registry.json` keeps one row per run and marks one **primary** run per strategy (explicit
   `primary=True`, else best tier then Sharpe).
4. **Export** – `sajim export` writes `leaderboard.json` (primary runs ranked), `registry.json`
   (all runs grouped), `correlation.json` (daily PnL correlation of primary runs with trades) and
   `strategy_universe_matrix.json` (symbol × timeframe grid; untested cells are `null`).
5. **View** – the dashboard reads those files; the detail drawer reads the run folder via
   `/api/registry/<strategy_id>/<run_id>`.

## Legacy rows

The leaderboard that existed before the registry was typed by hand (11 rows + 23 timeframe rows).
They were imported once with `sajim import-legacy` as runs with `source: legacy_manual`: metrics and,
where one existed, the old tear sheet, but no trade list, so no equity curve or Monte Carlo. They are
shown with a LEGACY badge until the strategy is re-run through an adapter, which replaces them.

## What was removed and why

- `universe_matrix.py` synthetic generator: produced 720 "tested" cells from six hardcoded Sharpe
  values and fixed multipliers. Fabricated data; deleted.
- Hardcoded fallback leaderboard and correlation matrix in the dashboard API routes.
- `Math.random()` equity curve in the leaderboard inspector.
- The "Legacy TF Matrix" tab (rows now live in the Strategies tab as legacy runs).
- ~960 MB of tracked vendor payload (MT5 terminal, Deriv media, Scalper1 tooling, PDFs). These files
  are still in git history; a history rewrite (`git filter-repo`) is a separate, deliberate step.

## Conventions

- Repo root is found by walking up to `pyproject.toml` (`sajim_quant.data.loader.find_repo_root`);
  never hardcode absolute paths.
- Scripts in `scripts/` are runnable from any cwd and record into the registry; they do not write
  tear sheets anywhere else.
- Dashboard: `npm run lint`, `npx tsc --noEmit`, `npm run build` must pass. Engine: `pytest`.
- Credentials only via environment variables (`SAJIM_MT5_*`, `DEEPSEEK_API_KEY`, the apps' own `.env`).
