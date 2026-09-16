# SAJIM STRATEGY BENCHMARKING PLATFORM
## Comprehensive Problem Statement, Strategic Architecture & Implementation Roadmap

---

### Executive Summary

**SAJIM** operates as a parent holding managing a growing repository of quantitative trading algorithms, automated bots, and algorithmic trading systems. These strategies originate from three distinct technology ecosystems:
1. **Python Systems** (e.g., Backtrader, custom event-driven daemons, API bots).
2. **MetaTrader 5 Expert Advisors (MQL5)** (e.g., `Scalper1`, `GoldOrb`).
3. **TradingView Pine Script Strategies** (cloud-based `.pine` scripts).

Currently, **2 years of clean historical bar data across 20 global instruments** (Forex, Commodities, Indices, Cryptocurrencies) is stored locally in `data/dukascopy/`. However, there is no unified mechanism to execute, benchmark, stress-test, and rank these strategies against one another.

### The Core Architectural Reframe: Normalize at the Results Layer, Not the Execution Layer

Chasing "identical market mechanics across three runtimes" (MT5's fractal tick engine vs. Backtrader's bar-by-bar loop vs. TradingView's fill model) is a multi-year engineering trap. 

**The architectural breakthrough is this:**
> Every engine runs however it natively runs, but emits trades in **one normalized schema**. All analytics, ranking, and portfolio math operate strictly on that schema, completely engine-agnostic.

Residual differences (e.g., MT5 floating spread vs. Backtrader constant spread) become **documented metadata tags** attached to each run, allowing the SAJIM Leaderboard to display `Python / Zero-Slippage` vs. `MT5 / Historical Spread` side-by-side transparently.

---

## Part 1: Detailed Problem Breakdown

### 1. The Runtime Fragmentation Dilemma
* **MQL5 EAs (`.mq5`, `.ex5`)**:
  * Compiled C++-like binaries designed exclusively for the MetaTrader 5 Strategy Tester.
  * Cannot be executed natively in Python without automating MT5's proprietary engine (`terminal64.exe`).
  * Parsing MT5's `.htm` or `.xml` reports is brittle and breaks across MT5 build versions.
* **Full Python Systems (`.py`)**:
  * Heterogeneous frameworks: some use Backtrader (`backtrader-pullback-window-xauusd`), others use raw execution loops (`smc_daemon_project`), while others target WebSocket broker APIs (`Deriv UpDown`).
  * Inconsistent data loading and trade log outputs.
* **Pine Script Strategies (`.pine`)**:
  * Proprietary language locked to TradingView servers.
  * No reliable offline interpreter exists; web-scraping TradingView is fragile and violates ToS.
  * Attempting to build an offline Pine interpreter will stall platform delivery.

### 2. The Data Ingestion Disconnect
* **The Data Available**: 20 pairs (`AUDUSD`, `BTCUSD`, `ETHUSD`, `EURUSD`, `GBPUSD`, `GER40`, `US30`, `US500`, `XAUUSD`, etc.) across 6 timeframes (`M1`, `M5`, `M15`, `M30`, `H1`, `H4`) over 2 years in CSV format.
* **The Problem**: 
  * MT5 cannot read arbitrary external CSVs during CLI automated testing without registering them as "Custom Symbols" in MT5's database.
  * Repeatedly parsing gigabytes of raw M1 CSVs into Python creates massive I/O bottlenecks.

### 3. The "Zero Manual Intervention" Fallacy for MQL5
* Newly dropped `.mq5` files provided by SAJIM leadership will **not** include custom logging code.
* Relying on manual developer intervention to insert trade-logging functions into every EA defeats automated batch benchmarking.

### 4. Continuous Growth & Dynamic Folder Expansion
* New folders, bots, and revisions will be dropped into the workspace continuously.
* Without an automated discovery mechanism and standardized manifest, maintaining a clear view of portfolio capabilities is impossible.

---

## Part 2: The Three Adapters, Honestly Assessed

### 1. Python Adapter (Reference Implementation — Low Complexity)
* Wrap Backtrader and custom Python scripts behind a uniform contract:
  ```python
  run(strategy, symbol, timeframe, params) -> list[Trade]
  ```
* Converts Dukascopy CSVs into an ultra-fast local Parquet cache once.
* Serves as the primary reference implementation to validate the analytics engine end-to-end.

### 2. MQL5 Headless Adapter (High Value — Solved Architecture)
* **Custom Symbol Injection**: Use the local `MetaTrader5` Python package (`mt5.CustomSymbolCreate()` + `mt5.CustomRatesUpdate()`) to push Dukascopy bars directly into MT5.
* **Headless CLI Execution**: Generate a `.ini` configuration file on the fly and trigger:
  ```powershell
  terminal64.exe /config:sajim_test.ini
  ```
* **Compile-Time Preprocessor Injection (Zero Manual Intervention)**:
  * Do not parse MT5 `.htm` reports.
  * A Python preprocessor automatically inspects `.mq5` files before compilation, injects `#include <SajimLogger.mqh>`, and hooks trade logging into `OnTester()` / `OnDeinit()`.
  * The preprocessor invokes `metaeditor64.exe /compile:EA.mq5`.
  * MT5 executes headlessly and directly writes a schema-compliant `sajim_trades.csv` via `FileWrite()`.

### 3. Pine Script Adapter (Pragmatic De-risking)
* Stop attempting to build an offline Pine interpreter.
* **Strategy**:
  1. **Import Layer**: Provide a clean CSV mapper for TradingView's native "Export List of Trades" CSV directly into the normalized trade schema (allows instant leaderboard ranking of existing Pine results).
  2. **Python Porting**: High-conviction Pine strategies are reimplemented in VectorBT / Python for automated offline sweeps.
  3. **Milestone Sequencing**: Defer any complex Pine automation from v1. Ship Python + MT5 first.

---

## Part 3: The Universal Trade Schema Contract

Every adapter must satisfy this single output contract:

```
Trade Record:
├── Trade_ID:            str (UUID or sequential)
├── Strategy_Name:       str
├── Engine:              str ("python:backtrader" | "mt5:headless" | "pine:imported" | "python:vectorbt")
├── Symbol:              str ("XAUUSD", "EURUSD", etc.)
├── Timeframe:           str ("M1", "M5", "M15", "H1", "H4")
├── Direction:           str ("BUY" | "SELL")
├── Entry_Timestamp:     datetime (UTC)
├── Entry_Price:         float
├── Position_Size:       float (lots / units)
├── Exit_Timestamp:      datetime (UTC)
├── Exit_Price:          float
├── Exit_Reason:         str ("TP" | "SL" | "SIGNAL" | "TIMEOUT" | "STOP_OUT")
├── Gross_PnL:           float
├── Commission:          float
├── Swap_Cost:           float
├── Net_PnL:             float
├── Return_Pct:          float
├── MAE:                 float (Maximum Adverse Excursion in price/pips)
├── MFE:                 float (Maximum Favorable Excursion in price/pips)
└── Assumptions:         dict ({ "spread_model": "floating"|"constant", "slippage_pips": 0.5 })
```

---

## Part 4: The Critical Path (Build Order)

We build vertically so value is realized at Step 4 before touching MT5, and MT5 slots in cleanly without rework:

```
[Phase 1: Foundation]
  Step 1: Normalized Trade Schema (Pydantic / Dataclass / Polars schema — frozen contract)
  Step 2: Parquet Cache of Dukascopy Data + fast loader (load(symbol, tf, start, end))

[Phase 2: First End-to-End Engine]
  Step 3: Python Adapter (Backtrader wrapper producing schema-compliant trades)
  Step 4: Analytics Suite (Sharpe, Sortino, Calmar, MaxDD, MAE/MFE, WinRate, ProfitFactor)
          ---> MILESTONE: Functional, end-to-end evaluation platform live for Python!

[Phase 3: MT5 Enterprise Integration]
  Step 5: MT5 Headless Adapter
          ├── mt5.CustomSymbolCreate & CustomRatesUpdate data sync
          ├── SajimLogger.mqh compile-time injection preprocessor
          └── Headless terminal64.exe CLI execution & trade harvester

[Phase 4: Scaling & Presentation]
  Step 6: Workspace Discovery Scanner & sajim_strategy.yaml manifest
  Step 7: Centralized Leaderboard & Matrix Dashboard (Next.js in sim/ or Streamlit)
```

---

## Part 5: Success Criteria

1. **Normalized Comparison**: A single leaderboard displaying MT5, Python, and imported Pine strategies side-by-side on identical risk-adjusted metrics.
2. **True Zero-Intervention for MQL5**: Dropping an un-modified `.mq5` into a folder triggers auto-injection, compilation, headless testing against Dukascopy custom symbols, and produces schema-compliant trade logs.
3. **Sub-Second Data Reads**: 2-year multi-pair historical data reads via Parquet completing in milliseconds.
4. **Transparent Assumption Tagging**: Every backtest run clearly documents its execution assumptions so differences in mechanics are an asset, not an obstacle.

---
*Report Finalized for: SAJIM Strategic Review*  
*Status: Approved for Phase 1 Execution*
