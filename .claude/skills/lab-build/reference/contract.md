# Strategy contract — summary for the builder

**Authoritative text: `sajim_quant/lab/CONTRACT.md`** (read it when anything here is
unclear; it wins on any conflict). Numbers live in `sajim_quant/lab/gates.py` and
`sajim_quant/lab/splits.py`. This page is a working summary.

A strategy is **signals only**. Fills, stops, sizing (1% fixed risk), costs, FX
conversion, the scoring window and the verdict belong to the server.

## Folder `strategies/generated/<lab_id>/`

| File | Who writes it |
|---|---|
| `request.json` | runner (the user's description, stored as data) |
| `spec.md` | you, spec stage (and its Trials log in the code stage) |
| `strategy.py`, `manifest.json` | you, code stage |
| `scorecards/`, `build_log.json`, `README.md` | the tools — never edit, never read `scorecards/holdout_*` |

## spec.md — sections, in this order

1. **Description** — the verbatim user text, quoted.
2. **Rules** — numbered, unambiguous, all times UTC, bar-close logic.
3. **PARAMS** — table: name | default | min | max | why that range. 1..5 rows. Risk,
   costs, dates and the symbol are never params.
4. **Mechanism** — why the effect should exist and why it survives the instrument's
   round-trip cost (state the cost in pips/points, computed from `reference/costs.md`).
5. **Universe** — primary symbol + timeframe (M5, M15, M30, H1, H4), plus 1–2 same-class
   breadth symbols, or "instrument-specific" with the mechanism that makes it so.
6. **Expected trades per month.**
7. **Assumptions and ambiguities.**
8. **Trials** — one line per DEV trial after the first:
   `Trial n: spec-fix | bug-fix — <what changed>`. Tuning for performance is never a valid kind.

## strategy.py

```python
import numpy as np
import pandas as pd
from sajim_quant.lab import indicators as ind   # the only sajim_quant import allowed

PARAMS = {"name": {"default": 6, "min": 4, "max": 8, "type": "int"}, ...}   # dict literal, 1..5 entries

def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame: ...
```

- **Input** `df`: DatetimeIndex `time` (naive UTC, ascending), float64 columns
  `open, high, low, close, volume`. Row *i* may use only rows ≤ *i*. Warmup bars come
  first; early rows simply produce no signals while indicators fill.
- **PARAMS**: `0 <= min <= default <= max`, `min < max`. If `min > 0`: `max/min <= 10`.
  If `min == 0`: an int needs `max <= 23`; a float needs `default > 0` and
  `max <= 10 x default`. `params` arrives with the same keys, already clipped.
- **Output**: DataFrame with **exactly `df.index`** and columns
  - `signal` int8 in {-1, 0, 1} — enter at the NEXT bar's open when flat (`.astype("int8")`);
  - `stop_dist` float64 > 0 and finite wherever `signal != 0` (price units from the fill);
  - `tp_dist` float64 > 0, or NaN for no target;
  - `exit` bool (optional) — close an open position at the next bar's open.
  Values on `signal == 0` rows are ignored. NaN in `signal` is an integrity failure.
  `-1` with `allow_short: false` is an integrity failure.

## manifest.json (`sajim.lab.manifest/1`)

Start from `templates/manifest.json`. Keys (no others): `schema, id, family, name,
description, origin "ai_lab", run_adapter "signal", engine "python:signal", symbol,
timeframe, supported_symbols (includes the primary), breadth_symbols (0–2, same asset
class, not the primary), max_hold_bars (1..2000), allow_short, warmup_bars (0..2000),
spec_sha256, code_sha256`. Leave both sha fields `""` — `lab.cli` stamps them.
There is **no** risk, balance, cost or date field.

## Execution (the simulator's job — do not re-implement)

One position at a time. A signal on bar *i* fills at `open[i+1]`. SL = fill ∓ stop_dist,
TP = fill ± tp_dist, checked from the entry bar on; a bar touching both books SL; a gap
through SL fills at the open. `exit` or an opposite signal closes at the next open (no
reversal on the same fill). `max_hold_bars` closes at that bar's close. Signals while a
position is open are ignored. 0-lot stops are skipped and counted (`untradable_stop`;
more than 30% fails G0 — stops that are too tight for the instrument).

## Lint (runs on every save, again on the server)

- Imports: only `numpy`, `pandas`, `math`, `typing`, `sajim_quant.lab.indicators`.
- No `open/exec/eval/compile/__import__/globals/locals/print`, no dunders, no
  `read_*` / `to_*` I/O (`to_numpy` is fine), no module-level calls.
- Look-ahead: `.shift()` needs a literal integer ≥ 0; no `center=True`; no
  `bfill/backfill/interpolate`; no `.resample()` (use `ind.htf`); no whole-series
  `.mean/.std/.max/.min/.quantile/.rank/...` outside `.rolling()/.expanding()/.ewm()`;
  no `np.roll`; no `groupby(...).transform("max")`-style whole-group aggregates.
- No date strings and no price-like numbers outside PARAMS (small ints, session
  hours, round multipliers such as 1.5 and powers of ten are fine).
- No unseeded randomness (and there is no reason to use any).

## Integrity check (`lab.cli check`, free, never a trial)

Lint + manifest, then in a sandbox: schema/dtype/index, determinism (two runs equal),
truncation causality (cuts at 20/40/60/80/95%: earlier outputs must not change),
future-scramble (bars after the midpoint replaced: earlier outputs must not change),
and at least one signal. Reasons only — never performance numbers.
