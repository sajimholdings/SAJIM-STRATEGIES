# SAJIM dashboard (`apps/dashboard`)

Next.js cockpit that renders the results registry. It never computes a metric itself: everything it
shows comes from the JSON files written by `sajim export` into `public/` plus the per-run artifacts
under `results/` at the repository root.

```bash
# from the repo root, after recording runs
sajim export                 # writes public/{leaderboard,registry,correlation,strategy_universe_matrix}.json
cd apps/dashboard
npm ci
npm run dev                  # http://localhost:3000
```

Tabs

- **Leaderboard** – one row per strategy (its primary run), sortable, filter by runtime / tier, "verified only".
- **Strategies** – every recorded run, grouped by strategy or flat. Click a run for the detail drawer.
- **Universe grid** – symbol × timeframe Sharpe heatmap per strategy. Only cells with a real run are filled.
- **Correlation** – daily-PnL correlation across primary runs that have trade lists.

The detail drawer shows the realized equity curve, 1,000-path Monte Carlo fan (P5/P25/P50/P75/P95),
risk-of-ruin, fragility, the behavioural diagnosis, run assumptions, the markdown tear sheet and a
`trades.csv` download. Rows tagged **LEGACY** were transcribed from the old hand-written leaderboard
and have no trade list, so those panels explain what is missing instead of inventing a chart.

API routes (all read-only): `/api/leaderboard`, `/api/registry`, `/api/registry/[strategyId]/[runId]`,
`/api/registry/[strategyId]/[runId]/trades`, `/api/strategy-universe`, `/api/correlation`.
Set `SAJIM_RESULTS_DIR` to point the detail routes at a results folder outside the repo.

Checks: `npm run lint`, `npx tsc --noEmit`, `npm run build`.
