"""
SAJIM Strategy Lab — frozen evaluation calendar (the "protocol").

Approved by the user on 2026-10-12 (docs/STRATEGY_LAB_PLAN.md, "Decisions").
These are SERVER constants: request dates are ignored, and PROTOCOL_VERSION is
stamped into every scorecard. Changing anything here is a protocol change — bump
PROTOCOL_VERSION so old verdicts are visibly from a different protocol.

    DEV      2024-09-01 .. 2025-12-31   build + DEV trials, always labelled IN-SAMPLE
    PURGE    2026-01-01 .. 2026-01-04   never scored (gap between DEV and HOLDOUT)
    HOLDOUT  2026-01-05 .. last bar     sealed, once per frozen sha
      H-A    2026-01-05 .. 2026-06-30   may be inside the model's training data
      H-B    2026-07-01 .. last bar     after the model's cutoff

Time convention — matches DataLoader: bar times are timezone-NAIVE UTC
(parquet `time` is timestamp[us] with no tz; first bars are Sunday 21:00/22:00 UTC
opens). Every helper here returns naive datetimes; a tz-aware input is converted
to UTC and its tz dropped.

Interval convention: the *_START / *_END constants are inclusive CALENDAR DAYS
(what people quote). The helpers work in half-open [start, stop) timestamps so a
bar at 2025-12-31 23:55 is DEV and 2026-01-01 00:00 is PURGE. Do not pass
DEV_END straight to DataLoader.load(end_date=...): its filter is `time <= end`,
which would keep only the 00:00 bar of the last day. Use loader_bounds().

Pure Python; numpy/pandas are used only if the caller hands in their objects.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Tuple

PROTOCOL_VERSION = "v1"

# ── Calendar (inclusive days, naive UTC midnight) ───────────────────────────
DEV_START = datetime(2024, 9, 1)
DEV_END = datetime(2025, 12, 31)
PURGE_START = datetime(2026, 1, 1)
PURGE_END = datetime(2026, 1, 4)
HOLDOUT_START = datetime(2026, 1, 5)
HOLDOUT_END: Optional[datetime] = None      # None = last available bar

# The model's training data runs to about June 2026, so the holdout is reported
# in two parts. MODEL_CUTOFF is the last day of H-A.
MODEL_CUTOFF = datetime(2026, 6, 30)
H_A_START, H_A_END = HOLDOUT_START, MODEL_CUTOFF
H_B_START = datetime(2026, 7, 1)
H_B_END: Optional[datetime] = None

# A window whose bars span less than this fraction of it is refused (not scored
# on whatever is there). Same ratio as DataLoader.SHORT_SPAN_RATIO.
MIN_COVERAGE = 0.90

# ── Universe ────────────────────────────────────────────────────────────────
# M1 is excluded (cost noise floor, short crypto M1 coverage); D1 is not cached
# and gives too few trades for the sample gates.
ALLOWED_TIMEFRAMES: Tuple[str, ...] = ("M5", "M15", "M30", "H1", "H4")

# "All 20 symbols": the explicit specs in core/instruments.py (Dukascopy cache).
ALLOWED_SYMBOLS: Tuple[str, ...] = (
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP",
    "USDJPY", "GBPJPY", "EURJPY",
    "XAUUSD", "XAGUSD",
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD",
    "US30", "US500", "GER40", "UK100",
)

# ── Stage names ─────────────────────────────────────────────────────────────
DEV = "dev"
PURGE = "purge"
HOLDOUT = "holdout"
H_A = "h_a"
H_B = "h_b"
STAGES: Tuple[str, ...] = (DEV, PURGE, HOLDOUT, H_A, H_B)
SCORED_STAGES: Tuple[str, ...] = (DEV, HOLDOUT)   # the only stages /lab/evaluate accepts

IN_SAMPLE_LABEL = "IN-SAMPLE"
SEAL_NOTE = ("The holdout seal is procedural, not cryptographic: the laptop's "
             "parquet cache contains the holdout bars.")

_ONE_DAY = timedelta(days=1)
_ONE_US = timedelta(microseconds=1)

# Inclusive calendar days per stage; None = open end (last available bar).
_DAYS = {
    DEV: (DEV_START, DEV_END),
    PURGE: (PURGE_START, PURGE_END),
    HOLDOUT: (HOLDOUT_START, HOLDOUT_END),
    H_A: (H_A_START, H_A_END),
    H_B: (H_B_START, H_B_END),
}


def normalize_stage(stage: str) -> str:
    """'DEV', 'H-A', ' holdout ' -> canonical stage id; ValueError if unknown."""
    s = str(stage).strip().lower().replace("-", "_")
    if s not in _DAYS:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    return s


def window(stage: str) -> Tuple[datetime, Optional[datetime]]:
    """
    Half-open [start, stop) bounds of a stage as naive UTC datetimes.
    stop is None for open-ended stages (HOLDOUT, H-B) = up to the last bar.
    """
    start, last_day = _DAYS[normalize_stage(stage)]
    return start, (None if last_day is None else last_day + _ONE_DAY)


def loader_bounds(stage: str) -> Tuple[datetime, Optional[datetime]]:
    """
    (start_date, end_date) safe for DataLoader.load, whose filters are inclusive
    on both ends: end_date is the last microsecond before the stop.
    Warmup bars before `start` are the caller's job (see CONTRACT.md).
    """
    start, stop = window(stage)
    return start, (None if stop is None else stop - _ONE_US)


def to_utc_naive(t: Any) -> datetime:
    """datetime / date / ISO string / pd.Timestamp / np.datetime64 -> naive UTC datetime."""
    if isinstance(t, str):
        t = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
    elif hasattr(t, "to_pydatetime"):                       # pd.Timestamp
        t = t.to_pydatetime()
    elif type(t).__name__ == "datetime64":                  # np.datetime64, no numpy import
        t = datetime(1970, 1, 1) + timedelta(microseconds=int(t.astype("datetime64[us]").astype("int64")))
    elif isinstance(t, date) and not isinstance(t, datetime):
        t = datetime(t.year, t.month, t.day)
    if not isinstance(t, datetime):
        raise TypeError(f"cannot interpret {t!r} as a timestamp")
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def in_window(t: Any, stage: str) -> bool:
    """True when timestamp `t` falls inside the stage's [start, stop)."""
    start, stop = window(stage)
    t = to_utc_naive(t)
    return t >= start and (stop is None or t < stop)


def stage_of(t: Any) -> Optional[str]:
    """'dev', 'purge', 'holdout' or None (before DEV_START)."""
    for s in (DEV, PURGE, HOLDOUT):
        if in_window(t, s):
            return s
    return None


def holdout_part(t: Any) -> Optional[str]:
    """'h_a', 'h_b' or None (not a holdout bar)."""
    if in_window(t, H_A):
        return H_A
    if in_window(t, H_B):
        return H_B
    return None


def _datetime64_us(times: Any):
    """
    Vectorised path for pandas / numpy inputs: returns a numpy datetime64[us]
    array of naive UTC, or None when `times` is a plain Python iterable.
    """
    tz = getattr(getattr(times, "dt", times), "tz", None)
    if tz is not None:                                      # tz-aware pandas index/series
        conv = times.dt if hasattr(times, "dt") else times
        times = conv.tz_convert("UTC").tz_localize(None)
    values = getattr(times, "values", times)
    dtype = getattr(values, "dtype", None)
    if dtype is not None and getattr(dtype, "kind", "") == "M":
        return values.astype("datetime64[us]")
    return None


def window_mask(times: Any, stage: str):
    """
    Boolean membership of each time in the stage. Returns a numpy array for
    pandas/numpy datetime inputs, else a list of bools.
    """
    start, stop = window(stage)
    arr = _datetime64_us(times)
    if arr is not None:
        import numpy as np
        m = arr >= np.datetime64(start, "us")
        if stop is not None:
            m &= arr < np.datetime64(stop, "us")
        return m
    return [in_window(t, stage) for t in times]


def split_holdout(times: Any):
    """
    Label each time 'h_a', 'h_b' or None (outside the holdout).
    numpy object array for pandas/numpy inputs, list otherwise.
    """
    arr = _datetime64_us(times)
    if arr is not None:
        import numpy as np
        a, b = window_mask(arr, H_A), window_mask(arr, H_B)
        out = np.full(arr.shape, None, dtype=object)
        out[a] = H_A
        out[b] = H_B
        return out
    return [holdout_part(t) for t in times]


def months(stage: str, last_bar: Any = None) -> List[str]:
    """
    Calendar months ('YYYY-MM') a stage touches. Open-ended stages need
    `last_bar` (the data's last bar) to know where they stop.
    G2 counts "months with trades" against len(months(DEV)) == 16.
    """
    start, stop = window(stage)
    if stop is None:
        if last_bar is None:
            raise ValueError(f"stage {stage!r} is open-ended; pass last_bar")
        stop = to_utc_naive(last_bar) + _ONE_US
    out, y, m = [], start.year, start.month
    while datetime(y, m, 1) < stop:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def coverage(stage: str, first_bar: Any, last_bar: Any, reference_end: Any = None) -> float:
    """
    Fraction of the stage's span that the data covers, by span like
    DataLoader._warn_if_short: (min(last, stop) - max(first, start)) / (stop - start).

    Open-ended stages (HOLDOUT, H-B) end at `reference_end` — pass the latest
    bar across the symbol universe so a symbol whose data stops early is caught.
    Without it the stage ends at this symbol's own last bar and only the start
    gap is measured.
    """
    start, stop = window(stage)
    first, last = to_utc_naive(first_bar), to_utc_naive(last_bar)
    if stop is None:
        stop = to_utc_naive(reference_end if reference_end is not None else last_bar)
    span = (stop - start).total_seconds()
    if span <= 0:
        return 0.0
    got = (min(last, stop) - max(first, start)).total_seconds()
    return max(0.0, min(1.0, got / span))


def has_min_coverage(stage: str, first_bar: Any, last_bar: Any, reference_end: Any = None) -> bool:
    """True when coverage() reaches MIN_COVERAGE; below it the window is refused."""
    return coverage(stage, first_bar, last_bar, reference_end) >= MIN_COVERAGE


def is_allowed_tf(tf: Any) -> bool:
    """'m15' / ' H1 ' -> True; M1, D1 and anything unknown -> False."""
    return isinstance(tf, str) and tf.strip().upper() in ALLOWED_TIMEFRAMES


def is_allowed_symbol(symbol: Any) -> bool:
    return isinstance(symbol, str) and symbol.strip().upper() in ALLOWED_SYMBOLS


def describe() -> dict:
    """The protocol block stamped into every scorecard (JSON-safe)."""
    def iso(d: Optional[datetime]) -> Optional[str]:
        return None if d is None else d.date().isoformat()
    return {
        "protocol_version": PROTOCOL_VERSION,
        "time_basis": "UTC, naive timestamps, half-open [start, stop)",
        "windows": {s: {"first_day": iso(a), "last_day": iso(b)} for s, (a, b) in _DAYS.items()},
        "model_cutoff": iso(MODEL_CUTOFF),
        "min_coverage": MIN_COVERAGE,
        "allowed_timeframes": list(ALLOWED_TIMEFRAMES),
        "dev_label": IN_SAMPLE_LABEL,
        "seal_note": SEAL_NOTE,
    }
