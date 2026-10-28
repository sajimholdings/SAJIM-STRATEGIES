# SAJIM STRATEGIES — Code Audit Final Report (Phase D)

**Date:** 2026-09-25
**Branch:** `sajim-strategies-update`
**Scope:** Full-system audit per `CLAUDE_AUDIT_PROMPT.md` — architecture/contract audit (A), test repair (B), trading-logic sanity (C), plus verification of the AI agent (DeepSeek) wiring in the `sim` dashboard.

---

## 1. What was broken, and how it was fixed

### 1.1 Test suite was red and uncollectable (Phase B)

| Symptom | Root cause | Fix |
|---|---|---|
| `test_gold_pullback_ai_replay.py` failed at **collection** (`ModuleNotFoundError: strategies.gold_pullback_ai`) | The strategy moved under `strategies/XAUUSD_TRADING_ASISTENT_AI/sovereign_sniper_bot/` but the test's `sys.path` still pointed at the old repo-root layout | Anchored `sys.path` to `strategies/XAUUSD_TRADING_ASISTENT_AI/sovereign_sniper_bot` via `__file__` |
| 3 tests failed on stale data paths (`BTC data collector`, `Deriv trading`, `GoldOrb`) | Folders moved under `strategies/` after the tests were written | All paths re-anchored with `Path(__file__).resolve().parent.parent / "strategies" / ...` |
| `PytestReturnNotNoneWarning` | Test returned a tuple as its return value | Removed the accidental return |
| Phase 3 test would **force-kill every `terminal64.exe`** with empty credentials during a normal pytest run | Adapter's launch path unconditionally ran `Stop-Process terminal64 -Force` | (a) test skipped unless `MT5_LOGIN`/`MT5_PASSWORD` are set; (b) the kill is now gated behind `SAJIM_MT5_KILL_TERMINAL=1` — without it the adapter only probes and logs a warning |

**Result: `7 collected — 6 passed, 1 skipped in 24s`, exit code 0.** The single skip is the MT5 headless end-to-end test, which by design requires live terminal credentials on this machine.

### 1.2 Universal Schema was silently corrupting data (Phase A)

| Bug | Fix |
|---|---|
| `Trade` rejected valid CSV directions (`LONG`/`SELL`/`B`) because the str-enums only knew `BUY`/`SHORT` | Added `_coerce_direction()` in `core/schema.py` — used both in `Trade.__post_init__` and `from_dict`; `load_trades_csv` now passes raw strings so coercion + `exit_reason` fallback to `UNKNOWN` applies |
| **Backtrader adapter recorded the exit fill as the entry price** (`trade.price` at close = last execution price) | `notify_trade` now reads `trade.history[0].event.price` for the true entry; exit-reason is derived from the closing order (`trail→TRAILING_STOP`, `stop→STOP_LOSS`, `limit→TAKE_PROFIT`, else `SIGNAL`); `return_pct` is direction-correct |
| **Deriv binary importer fabricated an exit price** (`entry + pnl` on an instrument that settles at entry) | `exit_price = entry_price` for binary options |
| Strategy discovery was flooded with **~169 MT5 stock-instrument EAs** from `sajim_holdings_sandbox` | Excluded the sandbox from the catalog scanner |
| CLI crashed on strategies whose engine spelling didn't match the adapter registry (`mt5:headless`, `python:breakout_system`, `python:custom`, …) | Added `ENGINE_ALIASES` mapping plus a `python_custom` dispatch (SMC / BEEP adapters selected by filename hints) |
| Output JSON failed to serialize dataclasses (or was skipped on 0-trade runs) | Result section always writes JSON via `metadata.to_dict()` / `t.to_dict()`; metrics included only when present |
| `.env` wasn't loaded for CLI runs (DeepSeek key invisible to Hermes etc.) | `python-dotenv` loaded at `__main__.py` entry from the workspace root |
| Headless MT5: missing terminal data dir / metaeditor hang → cryptic crash | `_detect_terminal_data_path()` with `SAJIM_MT5_DATA_DIR` override and sane fallbacks; compile + tester subprocesses wrapped with timeouts and friendly `RuntimeError`s; malformed trade rows are skipped, not fatal |

### 1.3 Trading-logic safety issues (Phase C)

| Issue | Fix |
|---|---|
| **`hermes_agent.py` shipped a hardcoded paid DeepSeek API key** in source (and git history — anyone with the repo can bill the owner) | Key removed from source; now environment-only (`DEEPSEEK_API_KEY`) with a placeholder guard. **Action for owner: revoke/rotate that legacy key.** |
| **`smc_daemon.py` started LIVE by default** (would place real orders with no explicit consent) | `MOCK_MODE` is now mandatory — daemon refuses to start (and exits 1) unless `MOCK_MODE=true` or `MOCK_MODE=false` is explicitly set |
| Sunrise strategy crashes writing its own reports on Windows (emoji in report text, cp1252 default codec) | Flagged as strategy-side bug (charmap `\U0001f3af`); not yet fixed — see debt list |

### 1.4 AI agent (DeepSeek) wiring — verified working

- **Provider connectivity verified end-to-end** via `sim/scripts/verify_ai.mjs`: HTTP 200, correct streamed reply, using the AI SDK against `https://api.deepseek.com/v1` with Chat Completions (`provider.chat()` — DeepSeek does not serve OpenAI's Responses API, which is the v4 SDK default).
- **Chat route fixed at the protocol layer** (`sim/src/app/api/chat/route.ts`):
  - `toTextStreamResponse()` → `toUIMessageStreamResponse()` (AI SDK v7 UI message protocol — the old route spoke a wire format the new client can't read).
  - Added `stopWhen: stepCountIs(10)` — v7 defaults to 1 step, so previously the stream died the moment `run_backtest`/`scan_strategies` executed, before the model could summarize.
  - **Shell-injection hardening:** `strategy_path`/`symbol`/`timeframe` from the model are now strictly whitelisted (`/^[A-Za-z0-9_.\-/ ]+$/`, no `..`, no absolute paths, resolved-path containment check) before being interpolated into the `exec` command. Previously a model prompt-injection could run arbitrary shell commands.
  - `any`-typed JSON reads replaced with real shapes; errors normalized.
- **Chat clients rewritten for AI SDK v7** (`components/AgentTab.tsx`, `chat/page.tsx`): removed `@ts-nocheck`; `useChat()` with the default `/api/chat` transport; `sendMessage({ text })` instead of the removed `append`; `status` instead of `isLoading`; parts-based rendering (`isTextUIPart` / `isToolUIPart`) instead of the removed `toolInvocations`, with tool progress/result/error cards and a compact backtest summary (trades, net PnL).
- `sim/.env.local` holds the test key (gitignored); `sim/.env.example` documents the three vars.
- **Verification state: `tsc --noEmit` clean, eslint clean on every touched file.** Runtime verification of the chat route needs the dev server restarted (see §3, item 10).
- **MonteCarloDrawer crash fixed**: clicking a dashboard cell with no `monte_carlo` payload used to blank the whole dashboard; now renders a graceful "no telemetry" panel, and the component is fully typed (lint-clean).

---

## 2. Health scores

| Phase | Score | Rationale |
|---|---|---|
| **Phase 1 — Schema & Data pipeline** | **8.5 / 10** | Frozen schema, coercion, and serialization now robust; CSV importers no longer fabricate prices; discovery sane. Parquet loader passes the speed test. Remaining: sunrise charmap bug (strategy-side), and no streaming validation of schema on *every* producer (only on load). |
| **Phase 2 — Python adapters & analytics** | **8.5 / 10** | Backtrader entry/exit capture now correct; all four replay runners verified on real data (gold_pullback 291 trades / $3,606.88; SMC 14 trades / PF 5.32; Deriv 2,628 trades; sunrise 13 trades). Analytics pass portfolio tests. Remaining: BEEP baseline adapter is thin; Sharpe annualization convention needs standardizing (see debt). |
| **Phase 3 — MT5 headless adapter** | **7 / 10** | Architecture is sound (compile-time injection, non-destructive, timeout-protected, kill-gated, tolerant harvest) and the unit test now skips safely offline — but the end-to-end run has **not been exercised on this machine** (needs terminal credentials + `SAJIM_MT5_KILL_TERMINAL=1` or a free terminal). Score reflects unverified-live, not broken. |

**Overall: the system is confirmed working** — Python-side end to end (scan → test → schema → analytics → JSON), plus AI-agent provider connectivity. The one component awaiting a live run is headless MT5.

---

## 3. Technical debt to clear before scaling the `sim` UI leaderboard

Ranked roughly by how directly each item blocks the UI work:

1. **Fabricated UI data (highest priority).** `sim/src/app/page.tsx` renders a `Math.random` equity curve and a hardcoded 20×6 grid / 120 nodes / correlation matrix / edge ratings. None of it comes from real backtests. Before scaling the leaderboard, the dashboard must consume `sim/public/test_results/*_result.json` and `sim/public/strategy_universe_matrix.json` (current shipped matrix is a *real* build — good — but the page still draws fake nodes on top).
2. **Monte Carlo coverage.** The fan chart silently falls back to flat `10000` placeholders when curves are missing; only cells that ran MC have real percentiles. Rebuild the matrix with MC enabled and render an explicit "not stress-tested" state (the drawer now handles the null case, the chart should too).
3. **Synthetic matrix builders.** `analytics/universe_matrix.py` and `timeframe_matrix.py` fabricate metrics and must be deprecated/deleted in favor of `analytics/real_matrix.py` (canonical) — otherwise a future `matrix` run can silently overwrite real results with synthetic ones.
4. **Sharpe annualization convention.** Backtrader `TimeReturn`-based Sharpe and the formula-based metric disagree; standardize one convention before the leaderboard compares strategies by Sharpe.
5. **Lint debt:** 24 remaining eslint errors (down from 39), concentrated in `page.tsx` and the fake-data components — these files get rewritten by item 1 anyway, so fix them *as part of* the real-data rebuild rather than separately.
6. **MT5 cells need an "unverified" badge.** Until a credentialed headless run happens on this machine, leaderboard rows backed only by the MT5 adapter should carry a clear `engine: MT5 (not yet live-verified)` marker.
7. **Sunrise strategy charmap crash** (`'charmap' codec can't encode '\U0001f3af'` on Windows) — strategy writes its own reports with emoji under cp1252. Fix with `encoding="utf-8"` in the strategy's report writer.
8. **Hermes key in git history.** Source is clean, but the old key is in history — rotate it at the provider. (User already plans to rotate the *test* key used for this audit.)
9. **No CI.** There is no pipeline running the now-green pytest suite; add one (GitHub Actions: `pytest tests/`, and `tsc --noEmit` + eslint for `sim/`) so this audit never has to be repeated from zero.
10. **Dev server must be restarted** on port 3001 to pick up `.env.local` and the rewritten chat route/client. Also note: `BACKTEST_TIMEOUT_MS` (240 s) sits at the edge of the route's `maxDuration` (300 s); slow strategies (~4 min, e.g. sunrise on XAUUSD M5) can trip it — consider streaming progress (tool output events) so the chat UX doesn't sit silent for minutes.
11. **`sim/public/test_results/` grows unbounded** — add retention/cleanup or a per-strategy "latest only" convention once backtests are run frequently through the agent.

---

## 4. Confirmation summary for the user

- ✅ pytest: **6 passed, 1 skipped** (skip = MT5 live-credentials gate, by design)
- ✅ All four strategy replay runners produce real trades through the frozen schema
- ✅ DeepSeek chat verified (HTTP 200, correct reply) through the AI SDK using the provided test key in the gitignored `sim/.env.local`
- ✅ Chat stack (route + both UIs) rewritten for AI SDK v7, type-safe, lint-clean
- ✅ Dashboard crash (Monte Carlo missing data) fixed
- 🔧 Remaining manual steps: restart the 3001 dev server; rotate the legacy Hermes key; (optional) run a credentialed MT5 headless test to lift Phase 3 to verified
