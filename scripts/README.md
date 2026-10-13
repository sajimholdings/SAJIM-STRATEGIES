# Runner scripts

Each `run_*.py` executes one strategy (or one importer) through a SAJIM adapter and ends with
`record_run(...)`, which writes the run into `results/` and updates `results/registry.json`.
Run `sajim export` afterwards (or `sajim run-all`, which does both) to refresh the dashboard JSON.

| Script | Engine | Needs |
|---|---|---|
| `run_demo_synthetic.py` | Backtrader on synthetic bars | nothing (offline demo) |
| `run_phase1_schema_check.py` | schema round-trip + loader timing | `data/parquet` optional |
| `run_phase2_python_adapter.py` | Backtrader reference SMA cross | `data/` XAUUSD H1 |
| `run_backtrader_sunrise.py` | SunriseOgle (Backtrader) | `data/` XAUUSD M5 |
| `run_smc_daemon_replay.py` | SMC daemon replay | `data/` EURUSD H1+M5 |
| `run_gold_pullback_ai_replay.py` | GoldPullbackAI replay | `data/` XAUUSD H1 |
| `run_external_systems_ingest.py` | BTC/EURUSD breakout CSVs + Deriv v3 log | files inside `strategies/BTC data collector` and `strategies/Deriv trading` (gitignored `*.csv`/`*.log`) |
| `run_deriv_updown.py` | Deriv UpDown CSV | `strategies/Deriv UpDown/backtest_results.csv` |
| `run_pine_importer.py` | TradingView export importer (3-trade sample) | nothing |
| `run_portfolio_analytics.py` | portfolio correlation over imported logs | same files as external ingest |
| `run_phase3_mt5_adapter.py`, `run_scalper1_*.py`, `run_all_mql5_scalpers.py` | MT5 headless | Windows, MetaTrader 5, `SAJIM_MT5_LOGIN/PASSWORD/SERVER` |
| `run_all_suite_tests.py` | runs the scripts above as subprocesses | |

`strategy_id` values match the slugs of the legacy leaderboard rows, so a fresh run replaces the
LEGACY row of the same strategy in the dashboard.
