# SAJIM Quant & Dashboard System

## Overview
**SAJIM STRATEGIES (Sajim Quant)** is a comprehensive quantitative trading research, backtesting, and visualization framework. The system is designed to standardize the evaluation of trading strategies across multiple execution engines (Python, MetaTrader 5, TradingView Pine Script) and present the results through a modern, AI-powered web dashboard.

## Core Architecture

The system is split into two primary layers:
1. **The Quant Engine (Python)**
2. **The Dashboard & AI Agent (Next.js / React)**

### 1. The Quant Engine (`sajim_quant/`)
This is the backend orchestration and execution layer. It acts as a universal adapter for running and normalizing backtests.
- **Universal Schema (`sajim_quant/core/schema.py`)**: Defines a strict, frozen output contract (`Trade`, `BacktestResult`, `RunMetadata`). All execution engines must map their trades to this exact format.
- **Adapters (`sajim_quant/adapters/`)**: Modules that bridge external testing environments to the universal schema. Supported engines include:
  - **MetaTrader 5 (Headless)**: Automatically launches the MT5 terminal to run an EA (Expert Advisor) backtest and harvests the output trades.
  - **Python Engines**: Integration for Backtrader, VectorBT, or custom scripts.
  - **CSV Importers**: Parsers for exported logs (TradingView Pine Script, Deriv, BTC Breakout).
- **CLI Tool (`__main__.py`)**: 
  - `python -m sajim_quant scan`: Scans the `strategies/` directory and builds a master catalog saved to `data/discovered_strategies.json`.
  - `python -m sajim_quant test`: Executes a specific strategy via its adapter and outputs normalized JSON results.

### 2. The Web Dashboard (`sim/`)
A Next.js application designed to serve as the user's primary interface (the "Console").
- **Visual Analytics**: The main dashboard visualizes the normalized JSON outputs from the backtests, plotting equity curves, drawdowns, and trade histories.
- **AI Agent Integration (`sim/src/app/api/chat/route.ts`)**: An embedded AI chatbot built with the Vercel AI SDK. 
  - The AI has access to backend tools via function calling.
  - **`scan_strategies`**: Allows the AI to read the `discovered_strategies.json` catalog.
  - **`run_backtest`**: Allows the AI to spawn a `python -m sajim_quant test` subprocess, wait for completion, and analyze the resulting metrics. (Note: Data payloads are deliberately summarized/truncated here to prevent LLM context bloat).

### 3. Strategies Directory (`strategies/`)
The repository for all trading strategies. Each folder represents an individual strategy (e.g., `backtrader-pullback-window-xauusd`). These folders contain the source code (MQL5 EAs, Python scripts), historical trade CSVs, and performance metric documents.

## How it Works (The Workflow)
1. **Strategy Creation**: A new strategy (MQL5 EA, Python script, or imported CSV) is added as a folder inside the `strategies/` directory.
2. **Scanning**: The user or the AI runs the scanner to discover the new strategy and determine its adapter type (`mt5`, `pine_csv`, `python:backtrader`, etc.).
3. **Execution**: A backtest is triggered via the CLI or the Next.js AI agent. The appropriate adapter executes the test and maps the results to the universal `Trade` schema.
4. **Analysis**: The results are exported as standardized JSON files, which are parsed by the Next.js frontend to render detailed analytics on the dashboard or analyzed by the AI agent to give natural language summaries.

## Development & Execution Rules
- Always prioritize the Web Dashboard (Console) as the primary feature; the AI is simply a powerful utility within the dashboard.
- Any new execution engine must strictly adhere to the `Trade` and `BacktestResult` schemas.
- When passing backtest data to the LLM agent via tools, always truncate or summarize the raw trades array to prevent context window overflow.

## Strategy Lab
Plain-English idea -> Claude Code (owner's Max login, on the laptop) builds a signal strategy -> Modal judges it after realistic costs against a sealed holdout. Design: `docs/STRATEGY_LAB_PLAN.md`; frozen contracts: `sajim_quant/lab/CONTRACT.md` and `sajim_quant/lab/INTERFACES.md`.
- **Entry point:** `python -X utf8 -m sajim_quant.lab.run "<idea>" SYMBOL TF [--yes]`. Only the runner launches lab Claude sessions (`claude.exe` directly, with `--settings .claude/lab/settings.json`, an env allowlist and the `lab-build` / `lab-review` skills). Do not start those skills by hand in an interactive session.
- **Contract:** a generated strategy lives in `strategies/generated/<lab_id>/` (`spec.md`, `strategy.py` with `PARAMS` + `generate_signals`, `manifest.json`) and is signals only. Fills, stops, sizing (1% risk), costs, FX, windows and verdicts belong to the server.
- **Execution:** generated code never runs on the laptop (only the AST lint does) and is never run through `backtrader_adapter.py`. It runs only in a fresh no-network `modal.Sandbox`, and its sparse events are scored by `SignalSimulator` (`sajim_quant/lab/simulator.py`) with `instruments.py` costs.
- **Guards:** `.claude/lab/hooks/*.py` deny writes outside the build folder, reads of `data/**`, `.env*`, `sim/**`, `~/.sajim/**` and holdout scorecards, and any Bash other than `python -X utf8 -m sajim_quant.lab.cli <subcommand>`. They apply only to runner-started sessions.
- **Verdicts:** REJECT, NOT_PROVEN or CANDIDATE ("eligible for paper/forward test"). Never label an `ai_lab` strategy ELITE, OPTIMAL, Tier 1 or "edge found"; DEV numbers are always IN-SAMPLE.
