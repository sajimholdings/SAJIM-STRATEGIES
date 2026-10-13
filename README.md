# SAJIM Strategies

Research and benchmarking platform for SAJIM's trading strategies. Every strategy, whatever runtime it
runs on (Python, MetaTrader 5 EA, TradingView Pine export, Deriv bot), is normalized at the **results
layer** into one trade schema, recorded in a **results registry**, and ranked on one dashboard.

```
sajim_quant/     the engine        schema · data loader · adapters · analytics · registry · CLI
strategies/      strategy code     one folder per strategy (Python, MQL5, Pine, Deriv)
scripts/         runner scripts    run a strategy through an adapter and record the run
results/         the registry      registry.json + per-run artifacts (committed, small)
apps/dashboard/  the cockpit       Next.js leaderboard / strategies / universe / correlation
apps/*           live products     sajim_traders, sajim_holdings_sandbox (not benchmark inputs)
tools/           data tooling      Dukascopy downloader, Deriv edge detector
tests/           pytest suite      runs offline on synthetic fixtures
docs/            design docs       ARCHITECTURE.md is the current map
data/            market data       gitignored: data/dukascopy/*.csv, data/parquet/*
archive/         retired code      kept for reference, not imported anywhere
```

## Quick start

```bash
pip install -e ".[dev,backtrader]"        # engine + test deps
pytest                                     # offline unit + end-to-end tests
python scripts/run_demo_synthetic.py      # records a demo run (synthetic data) and exports the dashboard JSON
cd apps/dashboard && npm ci && npm run dev # http://localhost:3000
```

With real market data (`python tools/data_downloader/download_all.py`, then
`python -c "from sajim_quant.data import DataLoader; DataLoader().build_cache()"`):

```bash
sajim run-all        # every scripts/run_*.py -> results/ -> apps/dashboard/public/*.json
sajim list           # registry table
sajim export         # regenerate dashboard JSON only
```

## Recording a run

```python
from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
from sajim_quant.registry import record_run

result = BacktraderAdapter(MyStrategy, strategy_name="MyStrategy").run(symbol="XAUUSD", timeframe="H1")
record_run(result, strategy_id="my_strategy", folder="strategies/MyStrategy", description="what it does")
```

`record_run` computes metrics, a 1,000-path Monte Carlo on the real trade PnLs, the behavioural
diagnosis and a markdown tear sheet, writes them under `results/<strategy_id>/<run_id>/`, updates
`results/registry.json` and the run shows up in the dashboard after `sajim export`.

## Tiers

One rule, `sajim_quant/registry/tiers.py`, classifies every run. Tier 1 needs Sharpe ≥ 1.8, profit
factor ≥ 1.5, drawdown ≤ 10% **and at least 30 trades**; fewer than 10 trades is "insufficient data".

## Secrets

Nothing in the repo holds credentials. Set `SAJIM_MT5_LOGIN`, `SAJIM_MT5_PASSWORD`, `SAJIM_MT5_SERVER`
for headless MT5 runs and `DEEPSEEK_API_KEY` for the BEEP Hermes agent. The values that were
previously committed must be considered leaked and rotated.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full map and the reasoning behind it.
