# SAJIM STRATEGIES: Claude Code Audit & Handoff Prompt

**Instructions for the User:** 
*You can point Claude Code directly to this file or paste its contents to give it full context on what needs to be done next.*

---

## Hello Claude Code,

You are being brought in to perform a comprehensive codebase audit, review, and stabilization pass on the **SAJIM Strategy Benchmarking Platform**. 

This repository contains both the core automated trading logic (various quantitative strategies, EAs, and daemons) and the unified backtesting platform that evaluates them. 

Your immediate goal is to comb through every single component, verify that the architecture aligns with our stated goals, ensure the tests pass, and bring the platform to a completely stable, "green" state before we move on to new feature development.

### 1. Project Context & Architecture
SAJIM operates a repository of trading algorithms across three ecosystems:
1. **Python Systems** (Backtrader, custom daemons, VectorBT)
2. **MetaTrader 5 (MQL5)** (Compiled EAs)
3. **TradingView Pine Script** (Cloud scripts)

**The Core Architectural Principle:**
We do not try to make execution mechanics identical. Instead, we **normalize at the results layer**. Every engine runs natively but must output a strictly normalized **Universal Trade Schema** (Trade ID, Engine, Symbol, Entry/Exit Price, PnL, MAE/MFE, etc.). All analytics, portfolio math, and the `sim` UI leaderboard operate strictly on this engine-agnostic schema.

For a deep dive into the architecture, please read `SAJIM_STRATEGY_EVALUATION_PROBLEM_AND_GOAL.md` at the root of the workspace.

### 2. Workspace Structure
Here is where everything lives:
* `sajim_quant/`: The core benchmarking engine. Contains adapters (`mql5/SajimLogger.mqh`, `python/backtrader_adapter.py`) and analytics (`metrics.py`, `universe_matrix.py`).
* `strategies/`: The actual trading logic and bots (e.g., `BEEP_MASTER_PACKAGE`, `BTC data collector`, `smc_daemon_project`).
* `tests/`: pytest suite (schema, loader, registry, adapter end-to-end). `scripts/`: backtest runner scripts that record into `results/`. `apps/sajim_holdings_sandbox/tests/`: the sandbox's own research scripts.
* `apps/dashboard/`: The web application (Next.js/React) for the Leaderboard, Strategies registry view and Universe grid.
* `data/`: Local storage for Dukascopy historical CSVs (converted to Parquet for speed).

### 3. Your Mission: The Full Codebase Audit
Please execute the following tasks sequentially. Do not rush; ensure every single thing is running correctly.

**Phase A: Architecture & Contract Audit**
1. Review the `sajim_quant` adapters. Do they strictly adhere to the Universal Trade Schema defined in the architectural problem statement? 
2. Check the Parquet data ingestion pipeline. Is it robust against missing data and optimized for speed?
3. Review the MT5 Headless Adapter (`test_phase3_mt5_adapter.py` and `SajimLogger.mqh`). Is the compile-time injection and headless execution logic sound?

**Phase B: Test Suite Execution & Repair**
1. Discover and run the Python test suites (using `pytest` or `unittest` as configured in the repo). 
2. You will likely find broken paths, missing dependencies (check `requirements.txt`), or failing assertions. **Fix them.** We need a 100% green test pass for the Python adapters and the Universal Schema logic.
3. If any test requires a mock MT5 environment or missing Dukascopy data, mock it appropriately so the CI pipeline can run offline.

**Phase C: Trading Logic Sanity Check**
1. Audit the core strategies in `strategies/` (specifically the `BEEP_MASTER_PACKAGE` and `smc_daemon_project`). 
2. Check for memory leaks, inefficient Pandas/Polars loops, and dangerous live-trading edge cases.

**Phase D: Final Report**
Once you have stabilized the codebase, output a final report containing:
1. What was broken and how you fixed it.
2. A health score for the current state of Phase 1, Phase 2, and Phase 3.
3. Technical debt you recommend we address before scaling the `sim` UI leaderboard.

---
**Claude, please begin by analyzing the repository structure, reading the requirements, and running the primary test suites.**
