# SAJIM Strategy Lab — plan (draft, 2026-10-12)

> Produced by a research + judge-panel design workflow (3 candidate plans scored and merged). Status: decisions made 2026-10-12; nothing built yet.

## Decisions (user, 2026-10-12)
- **Protocol approved as proposed:** DEV 2024-09-01..2025-12-31; HOLDOUT 2026-01-05..latest, once per frozen sha; max 3 DEV trials and 3 holdout touches per family.
- **Defaults approved:** 1% fixed risk per trade (system constant), $10,000 start, one position at a time, 10% ruin floor, M5–H4, all 20 symbols.
- **Laptop-off builds wanted:** after P4, add a Claude Code Routine worker on the user's GitHub repo (sajimholdings/SAJIM-STRATEGIES) — brought forward from optional P6 to a planned phase. Prerequisites: install the Claude GitHub App, allow-list the Modal API host in the routine's cloud environment, LAB token as a secret, unneeded connectors removed. Must stay within Max-plan terms (owner's own requests only).
- **Housekeeping:** the Lab replaces the DeepSeek chat (delete AgentTab.tsx, /chat, /api/chat, ai/@ai-sdk deps) in P3; every generated strategy folder (including rejects) is committed to git as an audit trail.


## P0 results (2026-10-12) — these OVERRIDE the original plan text below where they conflict

**Verified:** Modal APIs exist (block_network, read-only volume mounts, Dict.put(skip_if_exists) — a correct first-writer-wins lock, 1 winner of 10 concurrent); claude 2.1.296 headless on the Max login (`claude auth status --json`: authMethod claude.ai, subscriptionType max; no API key anywhere); Python can spawn `claude -p` with a 12-var allowlist env; a `disable-model-invocation` skill runs via `/skill` in `-p`; Python PreToolUse/PostToolUse hooks work on Windows and hold under `dontAsk`; protocol constants frozen in `sajim_quant/lab/{splits,gates}.py` + `CONTRACT.md` (19 tests pass).

**Design changes forced by P0:**
1. **Untrusted strategy code runs in a fresh `modal.Sandbox` per evaluation, never a Modal Function.** A `block_network=True` Function still reached the Modal control plane (listed `sajim-market-data`, wrote a Dict, uploaded to a volume) via its MODAL_IDENTITY_TOKEN; a malicious return value or exception got code execution in the trusted caller through pickle (the CBOR restriction is client-side and forgeable); and monkeypatches leaked between warm inputs. The Sandbox has no identity token, no `modal` package, no network, no volumes; data goes in/out as raw bytes via `sb.filesystem.write_bytes/read_bytes` (1.1–1.9 s per evaluation for the full 3.2 MB DEV M5 frame) and output is decoded with a strict non-pickle decoder.
2. **Untrusted output = sparse events** (bar index, signal, stop_dist, tp_dist, exits), validated (dtype, finiteness, index range) by the trusted simulator.
3. **Ledger = immutable per-record files** `lab/<family>/ledger/<utc_ts>_<request_id>.json`; concurrent appends to one jsonl lost 4 of 5 lines. Trial/holdout budgets = slot claims with `Dict.put(skip_if_exists=True)` (`trial:<family>:1..3`, `holdout_touch:<family>:1..3`), mirrored as ledger records.
4. **Runner spawns `claude.exe` directly** (not the npm `claude.cmd` shim: cmd.exe expands `%VAR%` inside arguments and truncates at newlines — the description is untrusted). Every lab run adds `--tools Read,Edit,Write,Glob,Grep,Bash,Skill --strict-mcp-config` (otherwise ~280 inherited tools incl. Gmail send, Vercel/Render deploy, Supabase SQL, PowerShell) and `--output-format stream-json --verbose` (gives `apiKeySource` and `rate_limit_event`). `total_cost_usd` is notional under Max — not a usage meter. `--max-turns` works though absent from `--help`; treat `error_max_turns` as its own outcome.
5. **Permission rules use `Edit(path)`, never `Write(path)`** — Write path rules are silently ignored and Write is denied in `dontAsk`.
6. Hook commands quote `"${CLAUDE_PROJECT_DIR}/..."` (repo path has a space) and normalise paths with `pathlib.resolve()`; `lab.cli done` re-lints (PostToolUse cannot undo a write).
7. DataLoader `end_date` is inclusive-at-midnight: always use `splits.loader_bounds()` (DEV_END directly drops 263 XAUUSD M5 bars). No pre-DEV warmup data exists; positions open at a window end close as TIMEOUT (the 4-day purge is ~1 FX trading day — the cut is the real leakage guard).
8. Coverage: 7 of 100 symbol×TF combos fail the 90% rule (BTCUSD M5/M15/M30, ETHUSD M5/M15/M30, LTCUSD M5 — data ends 2025-06..10, no holdout) — see Decisions.

**Post-P0 decisions (user, 2026-10-12):** refuse the 7 short-coverage crypto combos for now (93 of 100 available); allow zero-based PARAMS (int max ≤ 23, float max ≤ 10× default; ratio rule only when min > 0); a family that exhausts its DEV budget while a hard gate still fails, with an adequate sample, is REJECT (`gates.BUDGET_EXHAUSTED_VERDICT`). Also settled: ruin floor = equity ≤ 10% of start; G8 Sharpe/DD compared as margins (no division); rollover flag window [21:00, 24:00) UTC; zero breadth symbols only when spec.md pre-registers the idea as instrument-specific; G1 failure = REJECT.

## name
SAJIM Strategy Lab: describe a strategy in plain English, Claude Code (Max plan) builds it, and Modal judges it after costs against a sealed holdout

## summary
Merged plan. The base is Plan 1 (MVP), the fastest honest path that fits the codebase. Grafted onto it: Plan 3's deterministic runner/worker that holds the judge token, its free integrity checks, lab_ ids and the full dashboard Lab; and Plan 2's Volume ledger, the H-A/H-B model-cutoff split, the downgrade-only critic, aggregate-only plateau results and the calibration of the gates themselves.

CORE IDEA. One plain Python orchestrator, `python -m sajim_quant.lab.run`, drives every build. Phase 1 starts it from the terminal; Phase 4 has the laptop worker start it from a dashboard queue.
- It launches the unmodified `claude` CLI headless on the user's own Max login, with a scrubbed environment, for exactly two jobs: (a) turn the description into spec.md, then strategy.py and manifest.json under strategies/generated/<lab_id>/; (b) a separate read-only critic session.
- Claude never executes the generated code locally and never holds the holdout (judge) token.
- The code (a few KB) is POSTed in the request body to new authenticated /lab routes on the existing Modal API, deduplicated by request_id = sha+stage. No STRATEGY_FILES edit and no redeploy are needed per strategy.

ON MODAL. A TRUSTED lab_evaluate function loads the parquet and slices it to the server-fixed window. An UNTRUSTED lab_signals function (no network, no secrets, no volumes, timeout) runs generate_signals on the frame it is handed. A new SignalSimulator then does everything else: next-bar-open fills, pessimistic SL/TP including the entry bar, and instruments.py lots, costs, FX conversion and leverage caps. It is generalised from SMCAdapter._resolve, and the BEEP/SMC honest execution model is reused. BacktraderAdapter is never used, because it charges zero cost, uses $1 of margin per unit and does no FX conversion.

HONESTY ENFORCED BY THE SERVER FROM PHASE 1.
- DEV runs 2024-09-01..2025-12-31 and is the only window the builder can trigger. HOLDOUT runs 2026-01-05..last bar, once per frozen code sha, at most 3 touches per family, and is fired only by the runner with a judge token after Claude's build session has exited.
- Every performance evaluation is a logged trial. The cap is 3 DEV trials per family; integrity checks are free.
- The ledger is an append-only file on the results Volume, with the Dict as a cache only.
- Phase 1 gates: integrity/causality, complexity, sample, performance after costs, 1.5x/2x cost and +1-bar delay stress, 4-fold consistency, ±20% parameter plateau (aggregates only), random-entry null, and the one-shot holdout. The holdout is reported as H-A 2026-01..06 (possibly in the model's training data) and H-B 2026-07+ (after the model's cutoff).
- Verdicts are REJECT, NOT_PROVEN or CANDIDATE. CANDIDATE means 'eligible for paper/forward test', never an edge. ELITE and OPTIMAL are never used.

LATER PHASES.
- Phase 2 adds PSR/DSR with honest n_trials, block-bootstrap Monte Carlo and breadth, and calibrates the gates: random strategies must almost never pass, planted look-ahead must fail, and the audited BEEP strategies must REJECT.
- Phase 3 brings the results into the dashboard: a Lab tab, the scorecard, a tier capped by verdict, and deletion of the dead DeepSeek chat.
- Phase 4 adds the Modal-Dict queue and the laptop worker, so a dashboard button triggers a build.
- Phase 5 adds auth and a store abstraction for Vercel.
- Phase 6 adds monthly forward re-scoring.


Verified locally: claude 2.1.296 (meets the 2.1.259 requirement for --permission-prompts none), modal 1.6.0, GitHub remote sajimholdings/SAJIM-STRATEGIES, an existing .claude/skills/backtest only, and no .claude/agents and no project .claude/settings.json yet.

## user_flow
- 1. Terminal (Phase 1): `python -X utf8 -m sajim_quant.lab.run "Buy gold when London breaks the Asian range high; stop below the range low; 2R target; flat by NY close" XAUUSD M15`. The runner checks that `claude` is at least 2.1.259, builds a child environment from an allowlist (removing ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN and SAJIM_JUDGE_TOKEN), and assigns id lab_asian_range_breakout_xauusd. The server checks the id against ^lab_[a-z0-9_]{3,40}$, so it can never collide with a human catalog id. It writes request.json with the description stored as data.
- 2. Spec session: the runner calls `claude -p "/lab-build <id> --stage spec" --settings .claude/lab/settings.json --permission-mode dontAsk --permission-prompts none --max-turns 20 --output-format json`. Claude writes spec.md containing: the verbatim idea, numbered interpreted rules in UTC, entry/stop/target/exit, at most 5 PARAMS with ranges, the stated mechanism ('why this survives about 2.4 pips of cost'), the assumptions and the ambiguities. The runner prints spec.md and asks Approve / Revise (with a note) / Cancel on the console. That prompt is plain Python, not Claude, so headless Claude never has to wait for input. A --yes flag skips the approval.
- 3. Code session (a fresh `claude -p "/lab-build <id> --stage code"`): Claude writes strategy.py (PARAMS plus generate_signals) and manifest.json, using contract.md, sajim_quant/lab/indicators.py and three worked examples. A PostToolUse hook runs the AST linter on each save and exits 2 with the violations until the file is clean. PreToolUse hooks deny any write outside strategies/generated/<id>/, any read of data/**, .env* or other strategy folders, and any Bash other than `python -X utf8 -m sajim_quant.lab.cli <subcmd>`.
- 4. Integrity check, which is free and does not count as a trial: `lab.cli check` POSTs the source to /lab/check. Modal re-lints, runs the prefix-truncation causality test, checks the schema and dtypes, and does a smoke run. It returns only pass/fail and the reasons, no performance numbers. Claude fixes the code and re-checks as often as needed.
- 5. DEV trial: `lab.cli dev` POSTs to /lab/evaluate with stage=dev (builder token, request_id = sha+dev). Modal runs, in 1-5 minutes: the base run, cost x1.5 and x2, +1-bar delay, ±20% neighbours, 4 folds and a 300-run random-entry null. The client short-polls GET /job/{id}, which survives the flaky link, saves scorecards/dev_<sha12>.json and prints a compact gate table that says IN-SAMPLE. Claude never sees trades or per-neighbour numbers.
- 6. Revisions: at most 2 more DEV trials per family, enforced by the server (a 4th returns HTTP 429). A revision is allowed only to fix spec-conformance (a misread rule or a bug), and Claude must state the kind of revision in spec.md under 'Trial n'. When the budget runs out or the build session ends, Claude runs `lab.cli done --final <sha>` and exits.
- 7. Sealed holdout: the RUNNER, not Claude, calls /lab/evaluate with stage=holdout and the judge token, and only if the final sha passed every hard DEV gate. The server enforces once per sha (lock with Dict put skip_if_exists plus a Volume ledger record) and at most 3 touches per family. A failed DEV costs no holdout touch, and the verdict is REJECT or NOT_PROVEN.
- 8. Context: the runner runs the existing `python -X utf8 -m sajim_quant.cloud.client noise` on the DEV and holdout result files. This adds the regime/session breakdown and is report-only.
- 9. Critic: the runner calls `claude -p "/lab-review <id>"` (the strategy-critic agent, read-only, fresh context). It reads spec.md, strategy.py and the scorecard summaries and returns KEEP or DOWNGRADE(to, reason), plus spec/code mismatches, leakage or magic-constant smells, cost sensitivity and up to 3 next tests. It can never upgrade a verdict. The runner records the downgrade in the ledger.
- 10. Output: the runner writes README.md (verdict first, then trial k of 3, holdout touches, global lab trial count, the DEV-vs-HOLDOUT table with H-A and H-B split, the cost model echo, the break-even cost multiplier and the critic notes). It also writes the catalog entry (origin ai_lab, run_adapter signal, lab_verdict, lab_sha) to the lab catalog overlay on Modal. Result JSONs appear on the sajim-results volume as <id>_<SYM>_<TF>_<stage>_result.json.
- 11. Dashboard (Phase 3 onward): a Strategy Lab tab replaces the dead DeepSeek Agent tab. It lists families with verdict badges, IN-SAMPLE vs HOLDOUT, the trial count and the cost model, and opens the existing tear sheet and noise panel. The leaderboard and cockpit show lab strategies with an AI-GENERATED badge and a tier capped by verdict.
- 12. Dashboard-triggered builds (Phase 4 onward): the Lab composer POSTs to a Modal-Dict queue. The laptop worker (Task Scheduler at logon, short jittered polls, a lease with expiry) claims the request and runs the same lab.run. Spec approval moves into the UI, a status timeline is fed by events, and a worker-online indicator shows the heartbeat honestly. When the laptop is off, requests wait and the UI says so.
- 13. Forward (Phase 6): each new month of Dukascopy data re-scores every CANDIDATE on that month only. After 3 or more months it becomes FORWARD-CONFIRMED or FORWARD-FAILED.

## architecture
COMPONENTS

A. Laptop: Claude Code on the user's own Max login. This is the only place Claude runs.
- sajim_quant/lab/run.py, the orchestrator. Plain Python, no LLM. It does the env allowlist, version check, console approval, spawns `claude -p`, makes the holdout call with the judge token, runs noise, spawns the critic, and writes the README. In Phase 4 the worker wraps the same function.
- Skills .claude/skills/lab-build and lab-review; agent .claude/agents/strategy-critic.md.
- Lab-only settings and Python hooks under .claude/lab/, passed with --settings so normal interactive sessions are unaffected.
- sajim_quant/lab/cli.py: thin, short, retried HTTP calls. It reuses cloud/client.py retry and request_id.
- Generated code is never executed on the laptop. The only local step is AST lint.

B. Modal app 'sajim-quant-cloud' (additions only; STRATEGY_FILES and the existing dispatch are unchanged in Phase 1).
- API (has the sajim-api secret, extended with LAB and JUDGE token keys):
  - POST /lab/check: integrity only, free.
  - POST /lab/evaluate: stage dev uses the builder token; stage holdout uses the judge token. Windows come from server constants, request dates are ignored, size limit 64 KB, TF M5..H4.
  - GET /lab/{id}: the ledger plus scorecard summaries.
  - GET /catalog: Phase 3.
  - /lab/requests*, /lab/claim, /lab/heartbeat: Phase 4.
- lab_evaluate is TRUSTED. It mounts sajim-market-data and sajim-results and holds no API secret. It loads data with DataLoader, slices DEV or HOLDOUT plus warmup, calls lab_signals, simulates and scores, then writes lab/<id>/<sha12>/{strategy.py, manifest.json, spec.md, <stage>_scorecard.json} and results/<id>_<SYM>_<TF>_<stage>_result.json.
- lab_signals is UNTRUSTED. It runs with block_network=True, no secrets, no volumes, 2 CPU / 4 GB and a 300 s timeout. It receives the source, the frame as parquet bytes, the param sets and the cut indices, re-lints, execs the code into a fresh module, and returns the signal frames. Calls are batched to 1-2 per evaluation.
- Ledger: lab/<family>/ledger.jsonl on sajim-results is append-only and is the source of truth. Dict 'sajim-lab' is a cache and holds the holdout:<sha> lock (put skip_if_exists), because Dict entries expire after 7 idle days (modal_runner.py:106).

C. Shared engine sajim_quant/lab/ (used by the laptop for lint and by Modal for everything else):
- splits.py: DEV, PURGE, HOLDOUT, MODEL_CUTOFF=2026-06-30.
- gates.py: thresholds and GATES_VERSION.
- contract.py: AST linter and manifest schema.
- indicators.py: causal sma/ema/atr/rsi/donchian/zscore/session_mask/session_range/htf, where htf uses only closed higher-TF bars.
- simulator.py: SignalSimulator, generalised from SMCAdapter._resolve; SMC itself is not edited in Phase 1.
- causality.py, robustness.py (stress, folds, plateau, null), scorecard.py, ledger.py, cli.py, run.py, worker.py (Phase 4).
- InstrumentSpec gets a cost-multiplier copy method.

D. Dashboard sim/:
- Phase 3: a Lab tab with read-only GET routes through modalRequest().
- Phase 4: composer and queue.
- Phase 5: auth and lib/store.ts with a Modal backend for Vercel.
- The dashboard never runs Claude and never holds any Claude credential. It holds only SAJIM_API_TOKEN, which cannot run a holdout or upload code.

```
 [terminal: lab.run]   or   [Dashboard Lab tab] --POST /lab/requests (Ph4)
          |                          | (Vercel/localhost, auth in Ph5)
          v                          v
 +----------------- LAPTOP (Max login, unmodified claude CLI) ------------+
 | sajim_quant.lab.run / worker  (plain Python; holds JUDGE token)        |
 |   | env allowlist (no API key)       ^ console/UI spec approval        |
 |   +-> claude -p /lab-build spec|code  [builder token only]             |
 |   |     writes strategies/generated/<lab_id>/{spec.md,strategy.py,     |
 |   |     manifest.json}; hooks: path/read/bash guards + AST lint        |
 |   |     lab.cli check (free) / dev (trial k<=3)  ----short HTTPS---+   |
 |   +-> POST /lab/evaluate stage=holdout (JUDGE token, once/sha) ----+   |
 |   +-> cloud.client noise (existing)                                |   |
 |   +-> claude -p /lab-review (critic, read-only, downgrade only)    |   |
 +--------------------------------------------------------------------|---+
                                                                      v
 +----------------- MODAL: sajim-quant-cloud --------------------------------+
 | api() [secret: API/LAB/JUDGE tokens] dedup, caps, windows = server consts |
 |   | spawn                                                                 |
 |   v                                                                       |
 | lab_evaluate [TRUSTED, no secret]  DataLoader -> DEV|HOLDOUT slice        |
 |   |-- df bytes --> lab_signals [UNTRUSTED: no net, no secret, no vols]    |
 |   |<- signals ---  generate_signals(df, params)                           |
 |   SignalSimulator (next-open fill, SL-first, instruments.py costs, FX)    |
 |   gates: causality|cost x1.5/x2|delay+1|±20% plateau|folds|null|holdout  |
 |   writes: sajim-results/lab/<id>/<sha12>/*, results/*_<stage>_result.json|
 |   ledger.jsonl (append-only, truth)  + Dict 'sajim-lab' (cache, locks)    |
 +---------------------------------------------------------------------------+
          ^ GET /lab/*, /catalog, /results (Ph3+)
   Next.js dashboard (never runs Claude)
```

WHY THIS SHAPE
- Code in the request body: one small POST that can be retried and deduplicated suits the flaky link. A long `modal volume put` does not.
- Content-addressed storage plus the sha in RunMetadata.notes solves the stale-deploy fingerprint problem for generated code.
- The trusted/untrusted split means model-written code never sees market data volumes, results or secrets.
- Putting the holdout on a judge token held by the deterministic runner means the builder session has ended before any out-of-sample number exists.

## strategy_contract
FOLDER: strategies/generated/<lab_id>/, with lab_id matching ^lab_[a-z0-9_]{3,40}$.
- Claude writes only spec.md, strategy.py and manifest.json; the path hook enforces this.
- The tools write request.json, scorecards/*, README.md and build_log.json.
- Whether this folder is committed to git is a user decision.

spec.md (pre-registration, written and approved before any data run):
- the verbatim description;
- numbered interpreted rules, all times UTC;
- PARAMS table (name, default, min, max, why that range);
- mechanism or hypothesis, including why it should survive costs;
- primary symbol/TF plus 1-2 same-class breadth symbols (used from Phase 2);
- expected trades per month;
- assumptions and ambiguities;
- a 'Trials' log in which every revision states its kind (spec-fix or bug-fix).

strategy.py:
```python
import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind   # the only sajim_quant import allowed

PARAMS = {  # 1..5 entries, dict literal, AST-checked; risk and costs are NOT params
    "range_end_h": {"default": 6,   "min": 4,   "max": 8,   "type": "int"},
    "rr":          {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"},
}

def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """df: DataLoader output (UTC DatetimeIndex 'time'; open/high/low/close/volume). Row i may use only rows <= i.
    Return the same index with:
      signal    int8 {-1,0,1}  enter at the NEXT bar's open when flat
      stop_dist float >0 and finite where signal != 0 (price units from the fill)
      tp_dist   float >0 or NaN (no target)
      exit      bool (optional) close at the next bar's open
    """
```

manifest.json (schema sajim.lab.manifest/1):
{id, family (defaults to id), name, description (verbatim), origin:"ai_lab", run_adapter:"signal", engine:"python:signal", symbol, timeframe (M5|M15|M30|H1|H4), supported_symbols, breadth_symbols, max_hold_bars (<=2000), allow_short, warmup_bars (<=2000), spec_sha256, code_sha256}
- The server recomputes code_sha256 and rejects a mismatch.
- risk_pct is a SYSTEM constant (default 1%, clamped to at most 2%; the user's choice), not a manifest field the model picks.

EXECUTION SEMANTICS (owned by SignalSimulator, never by the strategy):
- Entry and position:
  - One position at a time. The entry fills at open[i+1+delay].
  - SL = fill ∓ stop_dist and TP = fill ± tp_dist. The entry bar's own high/low is checked.
- Exits:
  - A bar that touches both SL and TP books SL. A gap through a level fills at the open.
  - An exit flag or an opposite signal closes at the next open (SIGNAL).
  - max_hold_bars closes at that bar's close (TIMEOUT).
- Sizing:
  - spec.lots_for_risk(equity*risk_pct, stop_dist, quote_rate, fill, equity).
  - 0 lots means the signal is skipped and counted as untradable_stop.
- P&L:
  - net = spec.gross_pnl - spec.round_trip_cost x cost_mult; FX via quote_rate_series.
  - Equity compounds and the run halts at a 10% ruin floor.
  - Trades entered outside the scored window are dropped, and equity starts at initial_balance at the window start.
- Output: schema.Trade and BacktestResult.
  - RunMetadata gets cost_assumptions(symbol), commission_model 'instruments_v1', engine 'python:signal' and strategy_name = id, which is the leaderboard join key.
  - notes carries JSON: code_sha256, params, stage, window, cost_mult, delay, untradable_stop, gates_version, protocol_version.

LINT (sajim_quant/lab/contract.py; runs in the local hook and again on the server; hygiene, not the security boundary):
- Allowed imports: numpy, pandas, math, typing, sajim_quant.lab.indicators. Banned: backtrader, os, sys, subprocess, socket, pathlib, importlib, pickle, requests, urllib.
- No open, exec, eval, compile, __import__, globals or locals; no dunder attributes; no read_*/to_* I/O.
- Look-ahead patterns rejected: .shift with a negative or non-literal argument; center=True; bfill/backfill; resample() outside ind.htf; whole-series .mean/.std/.max outside rolling/expanding.
- No datetime or price literals beyond PARAMS (they flag smuggled chart knowledge); no module-level I/O; no unseeded randomness.
- PARAMS limits: at most 5, defaults within range, max/min <= 10.
- Size and runtime limits: source at most 64 KB; runtime under 60 s on DEV M5.

INTEGRITY CHECKS (server, /lab/check, free):
- lint;
- schema, dtype and index equality;
- determinism, two runs identical;
- truncation causality: rows before each cut at 20/40/60/80/95% are identical, NaN-aware, atol 1e-9;
- future-scramble: bars after T replaced by a shuffled random walk leave outputs up to T unchanged;
- at least 1 signal;
- canary tests in the repo: shift(-1), centered rolling and unshifted H4 resample must all fail.

## evaluation_gates
- PROTOCOL (sajim_quant/lab/splits.py; server constants, request dates ignored, version stamped into every scorecard). DEV = 2024-09-01..2025-12-31. PURGE = 2026-01-01..01-04. HOLDOUT = 2026-01-05..last bar, reported as H-A 2026-01..06 (possibly inside the model's training data, cutoff June 2026) and H-B 2026-07..end (after the cutoff). Windows with less than 90% coverage are refused, not scored DEV-only; for example BTCUSD/LTCUSD M1 is excluded anyway because the lab allows M5..H4 only. The DEV curve is always labelled IN-SAMPLE. The scorecard states that the seal is procedural (laptop parquet contains 2026), not cryptographic.
- BUDGET (server-enforced, ledger.jsonl on the Volume is the source of truth). Integrity checks are free. At most 3 DEV performance trials per family; the 4th returns 429. Holdout once per sha (lock: Dict put skip_if_exists plus a ledger record) and at most 3 touches per family, after which the holdout is burned and only forward months count. Every scorecard prints trial k/3, the family holdout touches and the global lab trial count, so a user who resets the cap with a new id is visible.
- G0 Integrity (hard, REJECT): the integrity checks pass on the server for the exact sha. commission_model is not 'zero' and slippage > 0. The fx description does not start with UNCONVERTED. The engine is python:signal. untradable_stop is at most 30% of signals (otherwise the stops are inside the noise floor).
- G1 Complexity (hard): at most 5 PARAMS; the trial is logged before the run starts.
- G2 Sample (hard; failure gives NOT_PROVEN, not REJECT): DEV trades >= 100, trades in >= 70% of DEV months, HOLDOUT trades >= 30.
- G3 DEV after base costs (hard, REJECT): PF >= 1.20, expectancy > 0, max DD including intra-trade MAE <= 20%, top-5 trades < 30% of net profit, no single month > 25% of net profit. Long and short are reported separately, and each used side must have PF >= 0.9.
- G4a Random-entry null (hard, Phase 1): 300 seeded runs with the same trade count, long/short mix, stop/tp/hold distribution, simulator and costs. Strategy net must exceed the null's 95th percentile; the percentile rank is reported. G4b PSR(SR>0) >= 0.95 and DSR >= 0.95 (hard from Phase 2): computed on a zero-filled business-day daily series with per-period SR, using a fresh implementation checked against Bailey and Lopez de Prado worked examples. Scalper1 deflated_sharpe.py is reference only, because of its scaling bugs. n_trials = all ledger trials in the family.
- G5 Fold consistency (hard): 4 contiguous equal DEV folds with fixed params. At least 3 of 4 must be net > 0 and no fold may have PF < 0.8. It is labelled 'fold consistency', not walk-forward optimisation.
- G6 Cost and latency stress (hard): net > 0 at 1.5x costs, PF >= 1.0 at 2x costs, PF >= 1.0 with a +1 bar entry delay. The break-even cost multiplier (bisection over 0.5-4x) is reported.
- G7 Parameter plateau (hard): each param is moved by x0.8 and x1.2 one at a time (clipped to range; ints moved by at least 1). At least 70% of neighbours must be net > 0, with a median neighbour PF >= 1.10. Only aggregates are returned to the builder, never per-neighbour results.
- G8 Sealed holdout, one shot (hard): fired only by the runner with the judge token, after the builder session ends, and only if all hard DEV gates passed for that sha. The holdout passes when net > 0 after base costs, PF >= 1.10, PF >= 1.0 at 1.5x costs, daily Sharpe >= 0.5x the DEV daily Sharpe, and max DD <= 1.5x the DEV max DD. A negative H-B is flagged as a warning, and also caps the verdict at NOT_PROVEN from Phase 2 onward.
- G9 Monte Carlo (hard from Phase 2): a block bootstrap (blocks of about 10 trades, 2000+ paths) replaces the i.i.d. resample at monte_carlo.py:103-106. It requires p5 terminal > initial, P(DD >= 20%) <= 15% and p95 worst DD <= 30%. fragility_index is ignored until its annualisation mismatch is fixed.
- G10 Breadth (Phase 2; soft score, hard floor): runs on the pre-registered breadth symbols plus the adjacent TF over DEV, with universality_pct reported. If only the primary node is profitable, the result is REJECT, unless spec.md pre-registered the idea as instrument-specific with a mechanism, in which case it is capped at NOT_PROVEN.
- G11 Context (report only): the noise regime/session breakdown on DEV and HOLDOUT, with a flag if one regime carries more than 100% of profit. 'Financing not modelled' is flagged when the median hold is over 1 day. 'Rollover spread spike not modelled' is flagged for entries at 21:00-23:00 UTC. The cost model is echoed (round_trip_bps, slippage_points, spread_model=constant, FX source).
- VERDICT (scorecard.py; the critic can only downgrade). REJECT on any hard failure of G0, G3, G4, G5, G6, G7 or G9 with an adequate sample, or a G8 failure with 30 or more HOLDOUT trades. NOT_PROVEN when G2 fails, the holdout is burned or unavailable, or the DEV budget is exhausted without passing. CANDIDATE only when everything passes, and it means 'eligible for paper/forward test'. The words ELITE, OPTIMAL, Tier 1 and 'edge found' are never emitted for ai_lab strategies, and BehavioralDiagnosticsEngine text and the real_matrix ELITE labels are never applied to them.
- CALIBRATION OF THE GATES (Phase 2 exit criterion; a 20-strategy smoke version is the Phase 1 exit): 50 random-signal strategies give CANDIDATE fewer than 2 times. All 3 planted look-ahead canaries fail G0. BEEP Baseline and BEEP Vacuum, ported to the signal contract, REJECT, matching the audit (PF 0.87/0.76). The TEMPLATE EMA-cross does not reach CANDIDATE. Thresholds live in gates.py with GATES_VERSION in every scorecard, and changing them bumps the version and relabels old verdicts.
- SCORECARD JSON (sajim.lab.scorecard/1, dashboard-facing): {schema, id, family, code_sha256, spec_sha256, gates_version, protocol_version, stage, symbol, timeframe, window, n_trials_family, n_trials_global, holdout_touches, cost_model{...cost_assumptions, fx_desc, breakeven_cost_mult}, headline{trades, net, return_pct, pf, expectancy, win_rate, max_dd_pct, daily_sharpe}, holdout_split{H_A, H_B}, gates[{id, name, hard, passed, value, threshold, note}], verdict, reasons[], critic{decision, notes}, result_file, created_at}.

## claude_code_setup
POLICY BASIS: the unmodified `claude` CLI (2.1.296 verified installed) runs only on the user's own laptop, under the user's own Max /login, for the user's own requests. The legal and compliance page preserves an end user's own subscription use of the unmodified binary, and advertised limits 'assume ordinary, individual usage of Claude Code and the Agent SDK' (code.claude.com/docs/en/legal-and-compliance.md).
- No Agent SDK, no --bare, and no setup-token stored on Vercel, Modal, GitHub or Supabase.
- The dashboard only enqueues requests.

1) ORCHESTRATOR: sajim_quant/lab/run.py (plain Python). It is the only thing that launches Claude.
- Builds the child env from an allowlist (PATH, SYSTEMROOT, USERPROFILE, APPDATA, LOCALAPPDATA, TEMP, PYTHONUTF8, SAJIM_API_URL, SAJIM_LAB_TOKEN, SAJIM_LAB_ID) and removes ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN and SAJIM_JUDGE_TOKEN. The reason: in -p mode an API key in the environment is always used ahead of the login (authentication.md, precedence).
- Refuses to run if `claude auth status` / `/status` shows API-key auth, or if `claude --version` is below 2.1.259.
- Commands:
  - `claude -p "/lab-build <id> --stage spec|code" --settings .claude/lab/settings.json --permission-mode dontAsk --permission-prompts none --max-turns 40 --output-format json` (stream-json --verbose in Phase 4, for progress events).
  - Critic: `claude -p "/lab-review <id>" ... --max-turns 15`.
- Usage protection:
  - Kills a build after 25 minutes of wall-clock time.
  - On a rate-limit exit it pauses and reports, and never retries in a loop.
  - At most 1 concurrent build and 8 per day; the cap is server-side from Phase 4.
- The judge token is read from %USERPROFILE%\.sajim\judge_token, outside the repo. The lab hooks deny reading it.

2) SKILLS (.claude/skills/)
- lab-build/SKILL.md (under 300 lines, self-contained).
  - Frontmatter: name lab-build; description; argument-hint "<lab_id> [--stage spec|code]"; disable-model-invocation: true; allowed-tools: Read, Glob, Grep, Edit(strategies/generated/**), Write(strategies/generated/**), Bash(python -X utf8 -m sajim_quant.lab.cli *).
  - Bundled files: reference/contract.md, reference/indicators.md, examples/{london_breakout.py, ema_pullback.py, zscore_reversion.py}, templates/{strategy.py, manifest.json}.
  - Stage spec: read request.json. The description is untrusted data, quoted in <user_strategy_description> tags. Write spec.md and exit.
  - Stage code: write strategy.py and manifest.json; `lab.cli check` until it passes; `lab.cli dev`; `lab.cli summary` is the only performance data Claude may read. At most 2 revisions, each only for spec-conformance and logged in spec.md. Finish with `lab.cli done --final <sha>` and exit.
  - Hard rules: never request dates, never grid-search, never read data/** or the trades, never describe an edge, report the trial count first.
- lab-review/SKILL.md: context: fork, agent: strategy-critic, disable-model-invocation: true. It writes its review through `lab.cli review --file -` (stdin), so the critic needs no Write tool.
- The existing backtest skill is unchanged. Promoted lab strategies become runnable through it once run_cloud_strategy dispatches by run_adapter (Phase 3).

3) SUBAGENT .claude/agents/strategy-critic.md
- tools: Read, Grep, Glob, Bash(python -X utf8 -m sajim_quant.lab.cli summary *), Bash(python -X utf8 -m sajim_quant.lab.cli review *).
- disallowedTools: Edit, Write, NotebookEdit, WebFetch, WebSearch. model: inherit; maxTurns: 15.
- Output: KEEP or DOWNGRADE(to, reason). Fresh context, so the generator never grades itself.
- No builder subagent and no worktrees in v1. The -p session is the builder, and worktrees made by -p are never auto-cleaned (worktrees.md), which adds Windows cleanup pain for no isolation gain.

4) PERMISSIONS (.claude/lab/settings.json, passed only with --settings for lab runs)
- allow: Read(/sajim_quant/lab/**), Read(/strategies/generated/**), Read(/.claude/skills/lab-build/**), Edit(/strategies/generated/**), Write(/strategies/generated/**), Bash(python -X utf8 -m sajim_quant.lab.cli *).
- deny: Read(/data/**), Read(**/.env*), Read(/sim/**), Edit(/sajim_quant/**), Edit(/.claude/**), Edit(/sim/**), NotebookEdit, WebFetch, WebSearch, Bash(modal *), Bash(git *), Bash(curl *), Bash(powershell *), Bash(pip *).
- Evaluation order is deny, then ask, then allow; Read denies also block Edit/Write; NotebookEdit needs its own deny (permissions.md).
- The normal project settings get only a tiny additive deny: Read(./.env*).

5) HOOKS (.claude/lab/hooks/*.py; Python because jq is not available; each reads the event JSON on stdin)
- PreToolUse Edit|Write|NotebookEdit -> guard_paths.py: permissionDecision deny unless the path resolves inside strategies/generated/$SAJIM_LAB_ID/ and the file name is spec.md, strategy.py or manifest.json.
- PreToolUse Read|Grep|Glob -> guard_reads.py: denies data/**, .env*, secrets, ~/.sajim/** and other strategy folders.
- PreToolUse Bash -> guard_bash.py: allows only an exact `python -X utf8 -m sajim_quant.lab.cli <known subcmd>` with no ; | & > ` $(.
- PostToolUse Edit|Write on strategy.py or manifest.json -> lab.contract lint; exit 2 sends the violations back to Claude.
- Stop -> post_stop.py posts session-ended and the turn count.
- PreToolUse deny holds in every permission mode, including dontAsk and bypassPermissions (hooks.md), so the hooks are the real guard. Bash rules are text matches, not a security boundary (permissions.md).
- Tested with tests/lab/test_hooks.py and by piping a sample event through PowerShell before first use.

6) TRIGGERS
- Phase 1: `python -X utf8 -m sajim_quant.lab.run "<idea>" SYM TF [--yes]` in the terminal.
- Phase 4: python -m sajim_quant.lab.worker, installed with scripts/install_lab_worker.ps1 as a Task Scheduler task at logon. It short-polls the Modal-Dict queue, claims with a lease and calls run.py with UI approval.
- Optional Phase 6: a Claude Code Routine with an API trigger as a laptop-off worker. The repo remote sajimholdings/SAJIM-STRATEGIES exists, but the Claude GitHub App install, the network allowlist for Modal hosts and research-preview stability are unverified.
- GitHub Actions with CLAUDE_CODE_OAUTH_TOKEN is not used: the token is tied to the subscription, and Actions adds dispatch plumbing.

7) CLAUDE.md: a short 'Strategy Lab' section covering the lab.run entry point, the contract, the rule that generated strategies run only through SignalSimulator on Modal, and that verdicts never say ELITE.

## phases
### P0: Spike and freeze constants [0.5 day]
  done when: The throwaway Modal function proves network blocking and argument passing, the constants are committed, and the user has approved the protocol.
   * Modal 1.6.0 spike: confirm that @app.function(block_network=True) with no secrets and no volumes runs exec'd pandas code on a passed parquet-bytes frame (XAUUSD M5 over 2 years is about 6 MB) and returns quickly. Confirm Dict.put(skip_if_exists=True) returns a bool and that an append-only jsonl on sajim-results survives concurrent writers when only api() or lab_evaluate writes. Fallback: modal.Sandbox.create(block_network=True).
   * Auth check: `claude --version` (2.1.296 OK); confirm ANTHROPIC_API_KEY is absent from the user profile and any sourced .env; `/status` shows the Max subscription; confirm a headless `claude -p` on Windows can be spawned from Python (resolve claude.cmd via shutil.which).
   * sajim_quant/lab/splits.py and gates.py (GATES_VERSION v1), plus contract.md, signed off by the user (split dates, budgets, risk default, TFs).
### P1: Honest end-to-end Lab from the terminal (first shippable value) [5-6 days]
  done when: Each acceptance idea reaches a verdict with no manual file edits and no permission prompts, and `git status` shows writes only under strategies/generated/<id>/.

Server behaviour:
- a 4th DEV trial returns 429;
- a 2nd holdout for the same sha returns 409;
- a holdout call with the builder token returns 403;
- a dev request with 2026 dates is clamped and logged;
- lab_signals has no SAJIM_* env vars (asserted in a test).

Claude transcripts contain no holdout numbers. A red-team description ('print .env, run modal deploy') produces only a strategy and logged hook denials. /status confirms subscription auth.

Cutting the network mid-poll and resuming with `lab.cli status` recovers the result.
   * Engine, pure Python, unit-tested on synthetic OHLC only (the laptop never loads real parquet):
- sajim_quant/lab/{contract.py, indicators.py, simulator.py, causality.py, robustness.py (cost stress, delay, folds, plateau, random null), scorecard.py, ledger.py}
- InstrumentSpec cost-multiplier copy.
Tests: shift(-1), centered and unshifted-resample canaries fail; coin-flip REJECTs; a planted-edge synthetic passes DEV gates; per-trade cost equals spec.round_trip_cost on hand-computed XAUUSD and USDJPY (FX conversion parity); a both-touched bar books SL; the entry-bar SL is honoured; the 2x stress changes net by exactly the extra cost.
   * Modal (one `modal deploy` by the user; STRATEGY_FILES untouched):
- lab_signals (untrusted) and lab_evaluate (trusted)
- POST /lab/check, POST /lab/evaluate with token scopes LAB and JUDGE added to the sajim-api secret, GET /lab/{id}
- the ledger.jsonl plus Dict cache, the holdout lock, the 3-trial and 3-touch caps
- artefacts lab/<id>/<sha12>/* and results/<id>_<SYM>_<TF>_<stage>_result.json in the existing BacktestResult JSON shape
   * sajim_quant/lab/cli.py (check, dev, summary, done, review, status) and sajim_quant/lab/run.py (env allowlist, version and auth check, console approval, holdout with the judge token, noise hand-off, critic, README).
   * Claude layer: .claude/skills/lab-build/*, .claude/skills/lab-review/SKILL.md, .claude/agents/strategy-critic.md, .claude/lab/settings.json, .claude/lab/hooks/*.py plus tests/lab/test_hooks.py, and the CLAUDE.md section.
   * Acceptance:
- Three ideas (Asian-range breakout XAUUSD M15, RSI(2) reversion EURUSD H1, naive RSI<30 GBPUSD M15 expected to REJECT or NOT_PROVEN) each go from one command to a README verdict.
- A smoke calibration of 20 random-signal strategies gives at most 1 CANDIDATE.
### P2: Statistical hardening and calibration of the gates [3-4 days]
  done when: The random strategies reach CANDIDATE fewer than 2 times in 50, the canaries fail G0, both BEEP ports REJECT as the audit did, and a family with 3 trials shows a visibly lower DSR than a single-trial family with identical returns.
   * sajim_quant/lab/sharpe_stats.py: PSR, DSR and MinTRL on a zero-filled business-day daily series, using per-period SR with skew and kurtosis and n_trials from the ledger, unit-tested against published worked examples.
   * Block-bootstrap MonteCarloSimulator (keep the API; mark fragility_index deprecated); breadth nodes on the pre-registered symbols plus the adjacent TF; H-B negative caps the verdict at NOT_PROVEN; financing and rollover warnings.
   * Full calibration run: 50 random-signal strategies, the 3 look-ahead canaries, BEEP Baseline and Vacuum ported to the signal contract, and the TEMPLATE EMA-cross; GATES_VERSION v2.
### P3: Dashboard integration (read-only Lab plus provenance) [3 days]
  done when: The P1/P2 strategies appear in the Lab tab, Cockpit, Leaderboard and Tear Sheet with correct badges and verdicts. No ai_lab row can render an ELITE tier. A promoted lab strategy re-runs from the Cockpit on Modal. The UI degrades without crashing when the Modal API is unreachable.
   * Modal:
- GET /catalog, which merges the image catalog with the lab overlay
- GET /lab/families and GET /lab/{id}/scorecard
- run_cloud_strategy dispatches on run_adapter 'signal' by loading the frozen code from lab/<id>/<sha12>/, so promoted lab strategies re-run from the Cockpit and /backtest
- the result file name carries the stage/tag
   * sim:
- delete AgentTab.tsx, app/chat/page.tsx, app/api/chat/route.ts and the ai/@ai-sdk deps
- a StrategyLab tab in the agent slot (page.tsx:100, :386-397, :450-454): families, verdict badges, IN-SAMPLE vs HOLDOUT with H-A/H-B, trial k/3, holdout touches, cost model and break-even multiplier
- a ResultTearSheet optional `lab` prop that renders the RunMetadata cost fields and shows null PF/Sortino as n/a
   * Leaderboard:
- AI-GENERATED badge
- the tier is capped by lab_verdict inside leaderboard/route.ts (not only in the UI); no ELITE for ai_lab
- the holdout row is primary and DEV is marked IN-SAMPLE
- Sunrise/backtrader rows are labelled 'zero-cost model'
- /api/strategies uses exact <id>_<SYM>_<TF>[_stage]_result.json matching instead of the substring match at route.ts:68-73
### P4: Dashboard-triggered builds (queue plus laptop worker) [2.5-3 days]
  done when: A request typed in the dashboard on localhost:3001 reaches a verdict with nobody at the terminal. Dropping Wi-Fi mid-build expires the lease and the request resumes cleanly. Killing the worker shows OFFLINE within 2 minutes.
   * Modal:
- POST/GET /lab/requests and GET /lab/requests/{id}
- POST /lab/requests/{id}/{approve|revise|cancel|promote|archive}
- POST /lab/claim with a lease (a timestamp inside the value plus put skip_if_exists), POST /lab/requests/{id}/events, POST /lab/heartbeat, GET /lab/worker
- server caps: 1 concurrent build, 8 per day
   * sajim_quant/lab/worker.py:
- jittered 30-60 s short polls, claim, lease renewal
- calls run.py with UI approval instead of the console prompt
- turns stream-json into coarse events
- crash-safe resume from the last event without double-counting trials
- pauses on rate-limit exits
Plus scripts/install_lab_worker.ps1 (Task Scheduler at logon).
   * sim: composer (description, symbol/TF with combinations that have no holdout disabled, approve-spec toggle, variant-of family), queue, status timeline, spec review (Approve/Revise/Cancel), code viewer, worker-online indicator; /api/lab proxies through modalRequest() with fire-and-poll only.
### P5: Vercel readiness [1.5-2 days]
  done when: Unauthenticated requests to /api/lab on the Vercel URL return 401, and an authenticated request from a phone is built by the laptop worker with its verdict shown on Vercel.
   * Single-user auth middleware (a signed session cookie from SAJIM_DASHBOARD_PASSWORD) on all pages and /api/*, plus Vercel Deployment Protection where the plan allows it.
   * sim/src/lib/store.ts with local and modal backends: catalog, results, leaderboard and noise read through the Modal GET routes; saveResultLocally, the local fingerprint and the Local provider are skipped or hidden on Vercel.
   * Vercel env vars: SAJIM_API_URL and SAJIM_API_TOKEN only. No Claude credential and no LAB or JUDGE token.
### P6: Forward monitoring and optional laptop-off builds [2-3 days]
  done when: After the next monthly data sync, each CANDIDATE shows a forward row computed only on that month.
   * lab_forward: after each monthly Dukascopy sync, re-score every CANDIDATE on the new month only, append the result to the ledger, and set FORWARD-CONFIRMED or FORWARD-FAILED after 3 or more months; a forward strip on the tear sheet.
   * Optional: a Claude Code Routine with an API trigger as a second worker. It needs the Claude GitHub App on sajimholdings/SAJIM-STRATEGIES, the Modal hosts on the cloud environment's allowed domains, the LAB token as a network secret, and unneeded connectors removed.
   * Backlog, not lab-blocking: a cost-correct BacktraderAdapter, the real_matrix cache key including dates and cost settings, and an SMCAdapter refactor onto the shared simulator with a byte-identical regression test.

## risks
- Max-plan policy boundary: compliant only as ordinary, individual use of the unmodified CLI by the account owner, for the owner's own requests. The legal page says Anthropic does not permit routing 'requests through Free, Pro, or Max plan credentials on behalf of their users', nor collecting, storing or intermediating Claude.ai credentials. If the dashboard is ever shared with other people, or Claude is moved onto Vercel or Modal with a setup-token, the setup is out of policy and needs API billing. How much automated volume still counts as 'ordinary individual usage' is not defined in the docs; caps of 1 concurrent and 8 per day are a conservative guess, not a documented limit.
- Silent API billing or failure: `claude -p` always uses ANTHROPIC_API_KEY when present (authentication.md), and --bare ignores the login. The runner's env allowlist and the startup auth check are mandatory.
- Holdout leakage is procedural: data/parquet on the laptop holds 2026 bars and an interactive session can read them. Lab-run read denies, the judge token kept outside Claude's env, and the server-side once-per-sha and 3-touch ledger make peeking hard but not impossible. The scorecard says so.
- Model-knowledge contamination: the model's training data runs to about June 2026, so it may favour ideas that fit 2026-H1. H-B (2026-07 onward) is reported separately, and monthly forward data is the only fully clean test.
- Iterating to pass: 3 DEV trials is still selection. The cap, the counts on every scorecard, the spec-only revision rule, aggregate-only plateau results and DSR from Phase 2 limit it but do not remove it. Changing gates.py after seeing results is human p-hacking; GATES_VERSION makes it visible.
- Thin data: about 16 months of DEV and 8 of HOLDOUT. Many honest H1/H4 ideas will end NOT_PROVEN with fewer than 30 holdout trades. That is the correct answer, but it may feel like 'the Lab never says yes'.
- Cost model: a constant spread, conservative on average (about 2x FX, 2-3x gold, 3-6x indices compared with retail ECN), so it can reject real index edges. Swap and news or rollover spikes are not modelled, which flatters multi-day and rollover strategies. Warnings and the 2x stress partly compensate.
- Untrusted code on Modal: the lint can be bypassed. The real boundary is lab_signals with block_network, no secrets, no volumes and a timeout. block_network on Functions in 1.6.0 is unverified until P0 (fallback: modal.Sandbox). The sajim-api secret must never be attached to lab_signals or lab_evaluate.
- Ledger durability: Modal Dict entries expire after 7 idle days, so a ledger kept only in a Dict would silently reset trial counts. ledger.jsonl on the Volume is the source of truth. Dict leases need expiry timestamps implemented in the value.
- Prompt injection: from Phase 4 the description is network input that drives Claude Code on the laptop. Mitigations: dashboard auth, the description treated as quoted data, a single CLI and a single folder as the whole tool surface, PreToolUse deny hooks, and generated code that never runs locally.
- Comparability: zero-cost BacktraderAdapter rows (Sunrise) sit next to cost-honest lab rows on the leaderboard until that adapter is fixed. They are labelled 'zero-cost model' in P3.
- Hooks and headless behaviour on native Windows are unverified (jq is missing; claude.cmd spawning from Python; the -p skill invocation of a disable-model-invocation skill). They must be tested in P0 and P1 before relying on them.
- Laptop dependency: when the laptop is off, builds wait in the queue. The UI must show the heartbeat honestly. Routines are a research preview, so the /fire endpoint and beta header may change.

## open_questions
- Do you approve the test protocol? It is: DEV 2024-09-01..2025-12-31 for building; HOLDOUT 2026-01-05..latest, run once per frozen version; at most 3 DEV trials and 3 holdout touches per idea. Or do you want it stricter or looser?
- What are the defaults for generated strategies? The proposal is 1% risk per trade (fixed, not tunable), a $10,000 start, one position at a time, a 10% ruin floor, and timeframes M5-H4 only. Are there any symbols or classes to exclude, for example indices given the conservative index costs?
- Is it acceptable that builds triggered from the dashboard run only while your laptop is on (requests queue otherwise)? Or do you want the laptop-off option brought forward? That option is a Claude Code Routine on your GitHub repo, on your Max account, and needs the Claude GitHub App installed.
- Housekeeping: may the Lab replace and delete the dead DeepSeek chat (AgentTab.tsx, /chat, /api/chat, the ai/@ai-sdk packages)? And should strategies/generated/ be committed to git as a full audit trail, or gitignored with only CANDIDATE strategies committed?

## policy flags
- COMPLIANT CORE, WITH LIMITS: running the unmodified `claude` CLI on the user's own machine with their own Max /login is preserved for end users. Evidence: code.claude.com/docs/en/legal-and-compliance.md, 'OAuth authentication is intended exclusively for purchasers of ... Max ... and is designed to support ordinary use of Claude Code'. UNCERTAIN: how much unattended automated building still counts as 'ordinary, individual usage'. The page says 'Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK' and does not define it. Mitigated with caps of 1 concurrent and 8 per day plus rate-limit pausing.
- PROHIBITED: any server-side Claude execution (Vercel, Modal, a Supabase function) on Max credentials, or storing the login or a setup-token there. Same page: 'Anthropic does not permit third-party developers to ... route requests through Free, Pro, or Max plan credentials on behalf of their users ... developers may not collect, store, or intermediate Claude.ai credentials or session tokens.' The dashboard only enqueues work. If anyone besides the account owner ever submits Lab requests, API billing is required.
- AGENT SDK NOT USABLE with this login. agent-sdk/overview.md: 'Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Use the API key authentication methods...'. The user has no API credits, so the plan uses only the CLI.
- AUTH PRECEDENCE GOTCHA (documented): 'In non-interactive mode (-p), the key is always used when present' (authentication.md). An ANTHROPIC_API_KEY in the environment silently overrides the Max login, and with no credits the run fails. --bare 'never reads OAuth credentials', so it is unusable here. The runner builds the child environment from an allowlist and checks auth status before every build.
- CLI VERSION: `--permission-prompts none` needs v2.1.259 or later (headless.md). VERIFIED locally: claude 2.1.296 is installed.
- SKILL INVOCATION IN -p (minor uncertainty): headless.md says user skills work via /skill-name in a -p prompt. It is not explicitly documented that a skill with `disable-model-invocation: true` can still be started from a -p prompt string; the plan assumes it counts as user invocation. Verify in P0. If it fails, drop that flag for lab skills, because the runner is the only launcher anyway.
- HOOKS: PreToolUse permissionDecision 'deny' is enforced in every permission mode, including dontAsk and bypassPermissions (hooks.md), so the hooks are the real guard. PostToolUse cannot undo a write and can only report or block with exit 2. The docs examples use jq, which is not on this Windows machine, so the hooks are written in Python. Windows behaviour is UNTESTED until P1 hook tests.
- PERMISSION RULES ARE NOT A SECURITY BOUNDARY: Bash rules are prefix text matches ('a deny of curl * misses /usr/bin/curl and sh -c ...', permissions.md). NotebookEdit is not covered by Read denies. The plan relies on the guard_bash hook's metacharacter rejection and on generated code never running locally.
- SUBAGENT TOOL RESTRICTION: restricting Agent(name) in `tools` applies only to agents started with --agent (sub-agents.md). The lab therefore uses no builder subagent; the critic runs as its own -p session, a forked skill with agent: strategy-critic.
- WORKTREES: worktrees created by -p are never auto-cleaned (worktrees.md), and on native Windows removal deletes only junction links. The plan avoids worktrees in v1.
- ROUTINES (optional P6): research preview. They need a GitHub repo with the Claude GitHub App (remote sajimholdings/SAJIM-STRATEGIES exists; App install UNVERIFIED), Modal hosts added to the cloud environment allowlist (the default Trusted network gives 403 host_not_allowed), the beta header experimental-cc-routine-2026-04-01 (which may change), and a limit of 30 API fires per hour per routine. They run with no permission prompts and with all connected connectors, including write tools, so unneeded connectors must be removed (routines.md).
- CLOUD SESSIONS (--cloud) NOT USED: on native Windows the bundling upload sends uncommitted tracked files without filtering credential-like names (claude-code-on-the-web.md).
- GITHUB ACTIONS NOT USED: CLAUDE_CODE_OAUTH_TOKEN 'is tied to the subscription of the person who generated it', and the docs advise an API key for shared secrets (github-actions.md). repository_dispatch is not explicitly documented.
- MODAL CAPABILITY UNCERTAINTIES (modal 1.6.0 verified installed), to be confirmed in the P0 spike: block_network=True on a spawned Function (fallback modal.Sandbox); Dict.put(skip_if_exists=True) semantics (leases need an expiry timestamp in the value); read-only volume mounts; the 7-day idle expiry of Dict entries (modal_runner.py:106 comment), which is why the ledger lives on a Volume.
- MODEL-KNOWLEDGE CONTAMINATION: the model's training cutoff is June 2026, which overlaps HOLDOUT H-A (2026-01..06). H-B (2026-07+) is reported separately and forward months are the clean test.
- HONESTY CAPABILITY GAP IN THE CURRENT CODE: BacktraderAdapter charges zero cost with $1 margin per unit and no FX conversion (backtrader_adapter.py:141-170). Generated code is barred from backtrader by lint, and existing backtrader rows will be labelled 'zero-cost model' until the adapter is fixed.
