# Strategy Lab — generated-strategy contract

Contract version: `sajim.lab.manifest/1` (`gates.MANIFEST_SCHEMA`), under protocol
`splits.PROTOCOL_VERSION` and gates `gates.GATES_VERSION`. Source of truth for every
number below is `sajim_quant/lab/splits.py` and `sajim_quant/lab/gates.py`; this file
names constants instead of repeating them. Design rationale: `docs/STRATEGY_LAB_PLAN.md`.

A strategy is **signals only**. Fills, stops, sizing, costs, FX conversion, the
scoring window and the verdict belong to the server. Nothing a strategy writes can
change them.

---

## 1. Folder

`strategies/generated/<lab_id>/`, where `lab_id` matches `gates.LAB_ID_PATTERN`
(`^lab_[a-z0-9_]{3,40}$`), so it can never collide with a human catalog id.

| File | Written by | Notes |
|---|---|---|
| `request.json` | runner | the verbatim description, stored as data |
| `spec.md` | Claude (spec stage) | pre-registration, approved before any data run |
| `strategy.py` | Claude (code stage) | `PARAMS` + `generate_signals` |
| `manifest.json` | Claude (code stage) | schema below |
| `scorecards/<stage>_<sha12>.json` | tools | `gates.SCORECARD_SCHEMA` |
| `README.md`, `build_log.json` | runner | verdict first |

Claude writes **only** `spec.md`, `strategy.py`, `manifest.json` (enforced by the
path hook). Every folder, rejects included, is committed to git as an audit trail.

## 2. spec.md (pre-registration)

Required sections, in this order:

1. **Description** — the verbatim user text, quoted.
2. **Rules** — numbered, unambiguous, all times UTC.
3. **PARAMS** — table: name, default, min, max, why that range. Between
   `gates.MIN_PARAMS` and `gates.MAX_PARAMS` rows. Risk, costs, dates and the symbol
   are never params.
4. **Mechanism** — why the effect should exist and why it should survive the
   instrument's round-trip cost (state the cost in pips/points).
5. **Universe** — primary symbol and timeframe (`splits.ALLOWED_SYMBOLS`,
   `splits.ALLOWED_TIMEFRAMES`), plus 1..`gates.MAX_BREADTH_SYMBOLS` same-class
   breadth symbols (used by G10 from Phase 2). Zero breadth symbols are allowed only
   when this section pre-registers the idea as instrument-specific, with the mechanism
   (G10 then caps the verdict at NOT_PROVEN instead of testing breadth).
   A symbol/timeframe whose DEV or HOLDOUT coverage is below `splits.MIN_COVERAGE` is
   refused (as of 2026-10-12: BTCUSD M5/M15/M30, ETHUSD M5/M15/M30, LTCUSD M5).
6. **Expected trades per month.**
7. **Assumptions and ambiguities.**
8. **Trials** — one entry per DEV trial after the first: `Trial n: spec-fix | bug-fix — <what changed>`.
   Tuning for performance is not an allowed revision kind.

## 3. strategy.py

```python
import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind   # the only sajim_quant import allowed

PARAMS = {  # dict literal, MIN_PARAMS..MAX_PARAMS entries
    "range_end_h": {"default": 6,   "min": 4,   "max": 8,   "type": "int"},
    "rr":          {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"},
}

def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    ...
```

**Input `df`:** DataLoader output — `DatetimeIndex` named `time`, timezone-naive UTC,
ascending, columns `open, high, low, close, volume` (float64). It holds the scored
window plus up to `manifest.warmup_bars` earlier bars. Row *i* may use only rows ≤ *i*.

**`PARAMS` entries:** `{"default", "min", "max", "type"}` with `type` in `int|float`;
`0 <= min <= default <= max`. If `min > 0`: `max / min <= gates.MAX_PARAM_RANGE_RATIO`.
If `min == 0` (zero-based, e.g. a session start hour of 0 UTC): an `int` param needs
`max <= gates.MAX_ZERO_BASED_INT_MAX`; a `float` param needs `default > 0` and
`max <= gates.MAX_PARAM_RANGE_RATIO × default`.
`params` passed to `generate_signals` has the same keys, already clipped to range.

**Output:** a DataFrame with **exactly the input index** and these columns:

| Column | dtype | Meaning |
|---|---|---|
| `signal` | int8 in {-1, 0, 1} | enter long/short at the NEXT bar's open, if flat |
| `stop_dist` | float64 | > 0 and finite wherever `signal != 0`; price units from the fill |
| `tp_dist` | float64 | > 0, or NaN for no target |
| `exit` | bool (optional) | close an open position at the next bar's open |

Values on rows with `signal == 0` in `stop_dist`/`tp_dist` are ignored. NaN in
`signal` is an integrity failure (not "0").

## 4. manifest.json (`sajim.lab.manifest/1`)

```json
{
  "schema": "sajim.lab.manifest/1",
  "id": "lab_asian_range_breakout_xauusd",
  "family": "lab_asian_range_breakout_xauusd",
  "name": "Asian range breakout (XAUUSD M15)",
  "description": "<verbatim user text>",
  "origin": "ai_lab",
  "run_adapter": "signal",
  "engine": "python:signal",
  "symbol": "XAUUSD",
  "timeframe": "M15",
  "supported_symbols": ["XAUUSD"],
  "breadth_symbols": ["XAGUSD"],
  "max_hold_bars": 96,
  "allow_short": false,
  "warmup_bars": 200,
  "spec_sha256": "<hex>",
  "code_sha256": "<hex>"
}
```

- `id` matches `LAB_ID_PATTERN`; `family` defaults to `id` (variants share a family
  and therefore share its trial budget and holdout touches).
- `origin` = `gates.ORIGIN`, `run_adapter` = `gates.RUN_ADAPTER`, `engine` = `gates.ENGINE`.
- `symbol` ∈ `splits.ALLOWED_SYMBOLS`; `timeframe` ∈ `splits.ALLOWED_TIMEFRAMES`.
- `breadth_symbols`: at most `gates.MAX_BREADTH_SYMBOLS`, same asset class, not the primary;
  empty only if spec.md pre-registers the idea as instrument-specific.
- `1 <= max_hold_bars <= gates.MAX_HOLD_BARS`; `0 <= warmup_bars <= gates.MAX_WARMUP_BARS`.
- `allow_short: false` means short signals are an integrity failure, not silently dropped.
- `code_sha256` = sha256 of the exact `strategy.py` bytes; the server recomputes it and
  rejects a mismatch. `spec_sha256` likewise for `spec.md`.
- There is **no** `risk_pct`, balance, cost or date field. Risk is `gates.RISK_PCT`
  (clamped to `gates.MAX_RISK_PCT`), a system constant. Unknown keys are rejected.

## 5. Execution semantics (owned by SignalSimulator, never by the strategy)

**Windows.** The server picks the window from `splits` (`dev` or `holdout`); request
dates are ignored. The frame handed to the strategy is `[start − warmup_bars, stop)`.
A DEV run never receives bars at or after `splits.window("dev")[1]`: a position still
open on the last DEV bar is closed at that bar's close (booked `TIMEOUT`, counted in
the scorecard as a window-end close). Trades **entered** before the window start are
dropped; equity is `gates.INITIAL_BALANCE` at the window start. Because the data cache
begins at `splits.DEV_START`, DEV warmup comes out of DEV itself (early DEV bars
produce no trades while indicators fill).

**Entry.** Only when flat (`gates.ONE_POSITION`). A signal on bar *i* fills at
`open[i + 1 + delay]` (`delay` = 0, or `gates.DELAY_BARS` under G6 stress).
SL = fill ∓ `stop_dist`, TP = fill ± `tp_dist`, both measured from the actual fill.
Signals that arrive while a position is open are ignored, except as below.

**Exits**, checked from the entry bar itself onward:
- A bar that touches both SL and TP books **SL**. A gap through a level fills at that
  bar's open (worse than the level).
- `exit` flag, or an opposite `signal`, closes at the next bar's open (`SIGNAL`); an
  opposite signal does not also open a reversal on the same fill.
- After `max_hold_bars` bars the position closes at that bar's close (`TIMEOUT`).
- Equity at or below `gates.RUIN_FLOOR × INITIAL_BALANCE` halts the run (`STOP_OUT`).

**Sizing.** `spec.lots_for_risk(equity × RISK_PCT, stop_dist, quote_rate, fill, equity)`
from `core/instruments.py` (leverage-capped, lot-stepped). 0 lots ⇒ the signal is
skipped and counted as `untradable_stop` (G0 limit `gates.MAX_UNTRADABLE_STOP_FRAC`).

**P&L.** `net = spec.gross_pnl − spec.round_trip_cost × cost_mult`, converted with
`core.fx` quote-rate series. Equity compounds.

**Output.** `schema.Trade` / `BacktestResult`. `RunMetadata`: `cost_assumptions(symbol)`,
`commission_model = gates.COMMISSION_MODEL`, `engine = gates.ENGINE`,
`strategy_name = id` (the leaderboard join key). `notes` is JSON with
`code_sha256, params, stage, window, cost_mult, delay, untradable_stop,
gates_version, protocol_version`.

## 6. Lint (`sajim_quant/lab/contract.py`)

Runs in the local PostToolUse hook and again on the server. It is hygiene, not the
security boundary (that is the no-network, no-secret, no-volume `lab_signals` function).

- **Imports:** only `numpy`, `pandas`, `math`, `typing`, `sajim_quant.lab.indicators`.
  Explicitly banned: `backtrader, os, sys, subprocess, socket, pathlib, importlib,
  pickle, requests, urllib`.
- **Calls/attributes:** no `open, exec, eval, compile, __import__, globals, locals,
  getattr/setattr` on dunders; no dunder attribute access; no `read_*` / `to_*` I/O.
- **Look-ahead patterns:** `.shift()` with a negative or non-literal argument;
  `center=True`; `bfill` / `backfill` / `fillna(method="bfill")`; `resample()` outside
  `ind.htf`; whole-series `.mean/.std/.max/.min/.quantile/.rank` outside
  `rolling`/`expanding`.
- **Smuggled knowledge:** no datetime literals and no price-like numeric literals outside
  `PARAMS` (session hours and small integers are fine).
- **Module level:** only imports, `PARAMS`, constants and function defs; no I/O; no
  unseeded randomness anywhere.
- **PARAMS:** a dict literal obeying section 3.
- **Size/runtime:** source ≤ `gates.MAX_SOURCE_BYTES`; `generate_signals` on DEV M5
  under `gates.MAX_SIGNAL_RUNTIME_S` as the outer cap, but the sandbox enforces the stricter
  `interfaces.PER_RUN_TIMEOUT_S` (14 s per run, process start-up included, so aim for well under
  10 s on ~105k M5 bars: vectorised pandas/numpy, no Python loops over bars); whole untrusted
  call under `gates.LAB_SIGNALS_TIMEOUT_S`.

## 7. Integrity checks (server, `POST /lab/check`; free, never a trial)

Returns pass/fail and reasons only — never performance numbers.

1. Lint (section 6) and manifest schema (section 4), including `code_sha256`.
2. Output schema, dtypes and index equality (section 3).
3. Determinism: `gates.DETERMINISM_RUNS` runs are identical.
4. Truncation causality: for each cut in `gates.CAUSALITY_CUTS`, outputs on rows before
   the cut equal the full-frame outputs (NaN-aware, `gates.CAUSALITY_ATOL`).
5. Future-scramble: bars after T replaced by a shuffled random walk leave outputs up to
   T unchanged.
6. At least `gates.MIN_SIGNALS` signal.
7. Repo canaries (`shift(-1)`, centred rolling, unshifted H4 resample) must all fail 1–6.

## 8. What the builder may see

`lab.cli summary` only: the gate table (`gates.GATES`, IN-SAMPLE label), trial k of
`gates.MAX_DEV_TRIALS_PER_FAMILY`, family holdout touches and the global trial count.
Never trades, never per-neighbour plateau numbers, never holdout numbers. Verdicts are
`gates.VERDICTS`; none of `gates.FORBIDDEN_LABELS` is ever written about a lab strategy.
