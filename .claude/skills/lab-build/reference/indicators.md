# Indicators — `from sajim_quant.lab import indicators as ind`

Source: `sajim_quant/lab/indicators.py` (`INDICATORS_VERSION = "1"`), the same bytes
the sandbox runs. Every function is **causal** (row *i* uses rows ≤ *i* only; proven
by truncation tests) and returns values aligned to the input index. Window lengths
must be positive integers — cast params with `int(params["n"])`.

| Function | Returns | Notes |
|---|---|---|
| `ind.sma(x, n)` | Series | rolling mean of the last n rows; NaN until n rows exist |
| `ind.ema(x, n)` | Series | span n, `adjust=False`, NaN for the first n-1 rows |
| `ind.atr(df, n=14)` | Series | Wilder ATR; NaN for the first n bars |
| `ind.rsi(x, n=14)` | Series | Wilder RSI 0..100; NaN for the first n rows; 100 with no losses, 50 when flat |
| `ind.donchian(df, n)` | DataFrame `upper, lower, mid` | window **includes the current bar**; for a breakout of the *prior* n bars compare against `.shift(1)` |
| `ind.zscore(x, n)` | Series | (x − rolling mean) / rolling std (ddof=1); NaN where std is 0 |
| `ind.session_mask(df.index, start_h, end_h)` | bool Series | open hour h with `start_h <= h < end_h` (UTC); wraps midnight when `start_h > end_h`; `end_h` may be 24 |
| `ind.session_range(df, start_h, end_h)` | DataFrame `high, low` | high/low of the most recent **completed** session `[start_h, end_h)`; visible from the first bar opening at or after the session end; NaN before |
| `ind.htf(df, rule, base_minutes=None)` | DataFrame `open, high, low, close` | the most recent **closed** higher-TF bar; `rule` in `"30min"`, `"1h"`, `"4h"`, `"1D"` (UTC buckets); NaN before the first closed bucket |

## Patterns

```python
close = df["close"]
prev = close.shift(1)                                   # previous bar: allowed (literal >= 0)
cross_up = (close > level) & (prev <= level.shift(1))   # a crossing on THIS bar's close
hours = pd.Series(df.index.hour, index=df.index)        # UTC hour of each bar's open
day = pd.Series(df.index.normalize(), index=df.index)   # calendar-day key
first_today = flag & (flag.astype(int).groupby(day).cumsum() == 1)   # first event per day
h4 = ind.htf(df, "4h")                                  # trend filter on closed H4 bars
upper_prior = ind.donchian(df, 20)["upper"].shift(1)    # prior 20-bar high
```

## Do not

- `.resample(...)` — use `ind.htf`. `.shift(-1)`, `center=True`, `.bfill()` — look-ahead.
- `close.max()`, `close.mean()`, `close.quantile(...)` on the whole series — use
  `.rolling(n)` / `.expanding()` versions.
- Hard-code prices or dates — derive levels from the data, put thresholds in PARAMS.
- Trade on the bar's own future: the signal on bar *i* is decided at bar *i*'s close and
  fills at bar *i+1*'s open (the simulator does this; do not shift your signal forward).
