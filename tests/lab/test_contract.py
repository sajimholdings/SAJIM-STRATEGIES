"""contract.py: the strategy.py AST lint, PARAMS rules, manifest validation and lint CLI."""

import hashlib
import json
import textwrap

import pytest

from sajim_quant.lab import contract as C
from sajim_quant.lab import gates as G

TEMPLATE = textwrap.dedent('''\
    """EMA cross with an ATR stop (template)."""
    import math
    from typing import Dict

    import numpy as np
    import pandas as pd
    from sajim_quant.lab import indicators as ind

    PARAMS = {
        "fast": {"default": 10, "min": 5, "max": 20, "type": "int"},
        "rr": {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"},
        "start_h": {"default": 7, "min": 0, "max": 12, "type": "int"},
    }
    ATR_N = 14


    def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
        """True on the bar where a crosses above b. Written 2024-01-01."""
        return (a > b) & (a.shift(1) <= b.shift(1))


    def generate_signals(df: pd.DataFrame, params: Dict) -> pd.DataFrame:
        fast = ind.ema(df["close"], params["fast"])
        slow = ind.ema(df["close"], 3 * params["fast"])
        a = ind.atr(df, ATR_N)
        hour_ok = ind.session_mask(df.index, params["start_h"], 20)
        h1 = ind.htf(df, "1h")
        prior_high = df["high"].rolling(20, min_periods=20).max().shift(1)
        rng = df["close"].rolling(10).apply(lambda w: w.max() - w.min(), raw=True)
        both = pd.concat([fast, slow], axis=1).max(axis=1)
        trend = df["close"].expanding().mean()
        up = _cross_up(fast, slow) & hour_ok & (df["close"] > h1["close"]) & (both > 0)
        noise = np.random.default_rng(7).normal(size=len(df))
        out = pd.DataFrame(index=df.index)
        out["signal"] = up.astype("int8")
        out["stop_dist"] = 1.5 * a
        out["tp_dist"] = params["rr"] * 1.5 * a
        out["exit"] = (df["close"] < trend) & (noise > 9) & (prior_high > 0) & (rng >= 0)
        return out.fillna({"stop_dist": math.nan})
''')


def codes(src):
    return [i.code for i in C.lint_source(src)]


def with_body(body: str, params: str = '"n": {"default": 10, "min": 5, "max": 20, "type": "int"}') -> str:
    return (
        "import numpy as np\nimport pandas as pd\nfrom sajim_quant.lab import indicators as ind\n\n"
        f"PARAMS = {{{params}}}\n\n\ndef generate_signals(df, params):\n"
        + textwrap.indent(textwrap.dedent(body), "    ")
    )


# ── template and canaries ───────────────────────────────────────────────────

def test_clean_template_passes():
    assert C.lint_source(TEMPLATE) == []
    assert C.lint_source(TEMPLATE.encode("utf-8")) == []
    future = TEMPLATE.replace("import math" + chr(10), "from __future__ import annotations" + chr(10) + "import math" + chr(10), 1)
    assert C.lint_source(future) == []
    assert C.lint_source(bytes([0xEF, 0xBB, 0xBF]) + TEMPLATE.encode("utf-8")) == []     # BOM tolerated
    assert "IMPORT" in codes(future.replace("annotations", "braces"))


@pytest.mark.parametrize("body", [
    # 1. shift(-1): tomorrow's close decides today's signal
    'up = df["close"].shift(-1) > df["close"]\nreturn up\n',
    # 2. centred rolling window
    'm = df["close"].rolling(5, center=True).mean()\nreturn m\n',
    # 3. unshifted H4 resample reindexed to the base TF
    'h4 = df["close"].resample("4h").last().reindex(df.index).ffill()\nreturn h4\n',
])
def test_lookahead_canaries_rejected(body):
    issues = C.lint_source(with_body(body))
    assert "LOOKAHEAD" in [i.code for i in issues]
    look = [i for i in issues if i.code == "LOOKAHEAD"][0]
    assert look.line == 9      # the first body line


def test_issue_format_and_positions():
    issues = C.lint_source(with_body('x = df["close"].shift(-1)\nreturn x\n'))
    look = [i for i in issues if i.code == "LOOKAHEAD"][0]
    assert look.format("strategy.py") == f"strategy.py:9:9: LOOKAHEAD .shift(-1) reads the future"


# ── individual bans ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,code", [
    ("import os\nreturn df\n", "IMPORT"),
    ("import subprocess\nreturn df\n", "IMPORT"),
    ("import json\nreturn df\n", "IMPORT"),
    ("from pathlib import Path\nreturn df\n", "IMPORT"),
    ("from . import x\nreturn df\n", "IMPORT"),
    ("from numpy import *\nreturn df\n", "IMPORT"),
    ("f = open('x')\nreturn df\n", "CALL"),
    ("eval('1')\nreturn df\n", "CALL"),
    ("exec('1')\nreturn df\n", "CALL"),
    ("g = globals()\nreturn df\n", "CALL"),
    ("__import__('os')\nreturn df\n", "CALL"),
    ("x = getattr(df, name)\nreturn df\n", "CALL"),
    ("x = getattr(df, '__class__')\nreturn df\n", "DUNDER"),
    ("x = df.__class__\nreturn df\n", "DUNDER"),
    ("x = df.eval('a+b')\nreturn df\n", "CALL"),
    ("x = '{0.__class__}'.format(df)\nreturn df\n", "DUNDER"),
    ("x = pd.read_csv('f.csv')\nreturn df\n", "IO"),
    ("df.to_csv('f.csv')\nreturn df\n", "IO"),
    ("df.to_pickle('f')\nreturn df\n", "IO"),
    ("x = np.load('f.npy')\nreturn df\n", "IO"),
    ("print(df)\nreturn df\n", "IO"),
    ("x = df['close'].shift(params['n'])\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].shift(periods=-2)\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].diff(-1)\nreturn x\n", "LOOKAHEAD"),
    ("x = np.roll(df['close'].to_numpy(), 1)\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].bfill()\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].interpolate()\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].fillna(method='bfill')\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].reindex(df.index, method='nearest')\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'] > df['close'].mean()\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'] / df['close'].max()\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].rank(pct=True)\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].quantile(0.9)\nreturn x\n", "LOOKAHEAD"),
    ("x = np.mean(df['close'])\nreturn x\n", "LOOKAHEAD"),
    ("x = df.groupby(df.index.date)['high'].transform('max')\nreturn x\n", "LOOKAHEAD"),
    ("x = df.groupby(df.index.date)['high'].max()\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'] > 1950.5\nreturn x\n", "LITERAL"),
    ("x = df.index.year == 2025\nreturn x\n", "LITERAL"),
    ("x = df.index >= '2025-03-01'\nreturn x\n", "LITERAL"),
    ("x = df['close'] > 1.08345\nreturn x\n", "LITERAL"),
    ("x = np.random.rand(3)\nreturn x\n", "RANDOM"),
    ("x = np.random.default_rng()\nreturn x\n", "RANDOM"),
    ("x = df.sample(5)\nreturn x\n", "RANDOM"),
    ("PARAMS['n'] = 3\nreturn df\n", "PARAMS"),
    ("PARAMS.update({})\nreturn df\n", "PARAMS"),
])
def test_each_ban(body, code):
    assert code in codes(with_body(body)), (body, C.lint_source(with_body(body)))


@pytest.mark.parametrize("body", [
    "x = df['close'].shift(1)\nreturn x\n",
    "x = df['close'].shift()\nreturn x\n",
    "x = df['close'].diff(3).pct_change(2)\nreturn x\n",
    "x = df['close'].rolling(20).max()\nreturn x\n",
    "x = df['close'].rolling(20)['x'].max() if False else df['close'].ewm(span=5).mean()\nreturn x\n",
    "x = df['close'].expanding().std()\nreturn x\n",
    "x = df[['open', 'close']].max(axis=1)\nreturn x\n",
    "x = df['close'].rolling(5).apply(lambda w: w.mean() / w.std())\nreturn x\n",
    "x = df.groupby(df.index.date)['high'].cummax()\nreturn x\n",
    "x = df['close'].ffill().to_numpy()\nreturn x\n",
    "x = (df.index.hour >= 7) & (df.index.hour < 16) & (df['close'] > 0.0001) & (df['close'] < 1440)\nreturn x\n",
    "x = df['close'] * 2.5 + 0.618 + 1e-9 + 100000\nreturn x\n",
    "x = df.between_time('08:00', '16:00')\nreturn x\n",
    "rng = np.random.default_rng(123)\nreturn rng.normal(size=3)\n",
    "x = df.sample(5, random_state=1)\nreturn x\n",
    "x = max(1, 2) + min(3, 4)\nreturn x\n",
])
def test_legitimate_code_not_flagged(body):
    assert C.lint_source(with_body(body)) == []


def test_module_level_rules():
    bad = TEMPLATE + "\nprint('hi')\nX = np.zeros(3)\n\nclass Foo:\n    pass\n\nif True:\n    pass\n"
    got = [i for i in C.lint_source(bad) if i.code in ("MODULE", "IO")]
    assert sum(i.code == "MODULE" for i in got) >= 4
    no_entry = TEMPLATE.replace("def generate_signals(", "def generate(")
    assert any("generate_signals" in i.message for i in C.lint_source(no_entry))
    wrong_sig = TEMPLATE.replace("def generate_signals(df: pd.DataFrame, params: Dict)",
                                 "def generate_signals(df)")
    assert "MODULE" in codes(wrong_sig)
    deco = TEMPLATE.replace("def _cross_up", "@staticmethod\ndef _cross_up")
    assert "MODULE" in codes(deco)


def test_docstrings_may_hold_dates_but_code_may_not():
    assert C.lint_source(TEMPLATE) == []      # the helper docstring mentions 2024-01-01
    assert "LITERAL" in codes(TEMPLATE.replace("ATR_N = 14", "ATR_N = 14\nSTART = '2024-01-01'"))


def test_syntax_encoding_and_size():
    assert codes("def generate_signals(df, params):\n    return (\n") == ["SYNTAX"]
    assert codes(b"\xff\xfe junk") == ["ENCODING"]
    assert codes("x = 1\x00") == ["SYNTAX"]
    big = TEMPLATE + "\n" + "# pad\n" * (G.MAX_SOURCE_BYTES // 6 + 1)
    assert "SIZE" in codes(big)


# ── PARAMS rules ────────────────────────────────────────────────────────────

def _p(**entries):
    inner = ", ".join(f'"{k}": {v}' for k, v in entries.items())
    return with_body("return df\n", params=inner)


@pytest.mark.parametrize("params_src,ok", [
    ('{"default": 10, "min": 5, "max": 20, "type": "int"}', True),
    ('{"default": 10, "min": 5, "max": 50, "type": "int"}', True),      # ratio exactly 10
    ('{"default": 10, "min": 5, "max": 51, "type": "int"}', False),     # ratio > 10
    ('{"default": 4, "min": 5, "max": 20, "type": "int"}', False),      # default below min
    ('{"default": 5, "min": 5, "max": 5, "type": "int"}', False),       # no range
    ('{"default": 6, "min": 0, "max": 23, "type": "int"}', True),       # zero-based hour
    ('{"default": 6, "min": 0, "max": 24, "type": "int"}', False),      # zero-based int > 23
    ('{"default": 0.5, "min": 0.0, "max": 5.0, "type": "float"}', True),
    ('{"default": 0.5, "min": 0.0, "max": 5.5, "type": "float"}', False),   # > 10x default
    ('{"default": 0.0, "min": 0.0, "max": 1.0, "type": "float"}', False),   # zero-based default 0
    ('{"default": -1, "min": -2, "max": 2, "type": "int"}', False),     # negative
    ('{"default": 6.0, "min": 4, "max": 8, "type": "int"}', False),     # int param with float
    ('{"default": 6, "min": 4, "max": 8, "type": "str"}', False),
    ('{"default": 6, "min": 4, "max": 8}', False),
    ('{"default": 6, "min": 4, "max": 8, "type": "int", "step": 1}', False),
    ('{"default": True, "min": 0, "max": 1, "type": "int"}', False),
    ('{"default": 2.0, "min": 1, "max": 3, "type": "float"}', True),    # int literals ok for floats
])
def test_param_entry_rules(params_src, ok):
    issues = C.lint_source(_p(x=params_src))
    assert (not any(i.code == "PARAMS" for i in issues)) is ok, issues


def test_param_count_rules():
    six = {f"p{k}": '{"default": 2, "min": 1, "max": 3, "type": "int"}' for k in range(6)}
    assert "PARAMS" in codes(_p(**six))
    assert "PARAMS" in codes(with_body("return df\n", params=""))
    # parse_params still counts 6, so G1 can report it
    assert len(C.parse_params(_p(**six))) == 6
    assert "PARAMS" in codes("def generate_signals(df, params):\n    return df\n")
    assert "PARAMS" in codes("PARAMS = dict(a=1)\n\ndef generate_signals(df, params):\n    return df\n")
    twice = TEMPLATE + "\nPARAMS = {}\n"
    assert "PARAMS" in codes(twice)


def test_parse_params_and_defaults():
    spec = C.parse_params(TEMPLATE)
    assert list(spec) == ["fast", "rr", "start_h"]
    assert spec["fast"] == {"default": 10, "min": 5, "max": 20, "type": "int"}
    assert spec["rr"] == {"default": 2.0, "min": 1.0, "max": 3.0, "type": "float"}
    assert isinstance(spec["rr"]["min"], float) and isinstance(spec["fast"]["default"], int)
    d = C.default_params(spec)
    assert d == {"fast": 10, "rr": 2.0, "start_h": 7} and isinstance(d["fast"], int)
    with pytest.raises(C.ContractError):
        C.parse_params("x = 1\n")
    with pytest.raises(C.ContractError):
        C.parse_params(_p(x='{"default": 6, "min": 4, "max": 8, "type": "str"}'))
    with pytest.raises(C.ContractError):
        C.parse_params("def f(:\n")


def test_clip_params_rounds_half_away_and_clips():
    spec = C.parse_params(TEMPLATE)
    assert C.clip_params(spec, {"fast": 12.5}) == {"fast": 13, "rr": 2.0, "start_h": 7}
    assert C.clip_params(spec, {"fast": 11.5, "rr": 9.0, "start_h": -3}) == {"fast": 12, "rr": 3.0, "start_h": 0}
    assert C.clip_params(spec, {"fast": 8.4})["fast"] == 8
    out = C.clip_params(spec, {"fast": 4})
    assert out["fast"] == 5 and isinstance(out["fast"], int) and isinstance(out["rr"], float)
    with pytest.raises(C.ContractError):
        C.clip_params(spec, {"nope": 1})
    with pytest.raises(C.ContractError):
        C.clip_params(spec, {"rr": float("nan")})


# ── manifest ────────────────────────────────────────────────────────────────

STRATEGY = TEMPLATE.encode("utf-8")
SPEC = b"# spec\n"


def manifest(**over):
    m = {
        "schema": G.MANIFEST_SCHEMA, "id": "lab_ema_cross_xauusd", "family": "lab_ema_cross_xauusd",
        "name": "EMA cross (XAUUSD M15)", "description": "Buy gold when the fast EMA crosses up.",
        "origin": "ai_lab", "run_adapter": "signal", "engine": "python:signal",
        "symbol": "XAUUSD", "timeframe": "M15", "supported_symbols": ["XAUUSD"],
        "breadth_symbols": ["XAGUSD"], "max_hold_bars": 96, "allow_short": False, "warmup_bars": 200,
        "spec_sha256": hashlib.sha256(SPEC).hexdigest(), "code_sha256": hashlib.sha256(STRATEGY).hexdigest(),
    }
    m.update(over)
    return m


def test_valid_manifest():
    assert C.validate_manifest(manifest(), strategy_py=STRATEGY, spec_md=SPEC) == []
    m = manifest()
    del m["family"]                                 # family defaults to id
    assert C.validate_manifest(m, strategy_py=STRATEGY, spec_md=SPEC) == []
    assert C.validate_manifest(manifest(breadth_symbols=[]), strategy_py=STRATEGY, spec_md=SPEC) == []


def test_manifest_shas():
    assert C.validate_manifest(manifest(), strategy_py=STRATEGY + b" ", spec_md=SPEC)
    assert C.validate_manifest(manifest(), spec_md=SPEC)               # bytes required
    assert C.validate_manifest(manifest(code_sha256="", spec_sha256=""), require_shas=False) == []
    assert C.validate_manifest(manifest(), require_shas=False) == []
    assert C.validate_manifest(manifest(code_sha256="ABC"), require_shas=False)
    assert C.validate_manifest(manifest(code_sha256=""), strategy_py=STRATEGY, spec_md=SPEC)


@pytest.mark.parametrize("over,needle", [
    ({"risk_pct": 0.02}, "risk_pct"),
    ({"initial_balance": 5}, "initial_balance"),
    ({"start_date": "2024-01-01"}, "start_date"),
    ({"surprise": 1}, "unknown key"),
    ({"schema": "x"}, "schema"),
    ({"id": "my_strategy"}, "id must match"),
    ({"origin": "human"}, "origin"),
    ({"engine": "python:backtrader"}, "engine"),
    ({"timeframe": "M1"}, "timeframe"),
    ({"timeframe": "D1"}, "timeframe"),
    ({"symbol": "DOGEUSD", "supported_symbols": ["DOGEUSD"]}, "symbol"),
    ({"symbol": "BTCUSD", "timeframe": "M5", "supported_symbols": ["BTCUSD"], "breadth_symbols": []},
     "coverage"),
    ({"supported_symbols": ["XAGUSD"]}, "primary"),
    ({"breadth_symbols": ["XAUUSD"]}, "primary"),
    ({"breadth_symbols": ["EURUSD"]}, "asset class"),
    ({"breadth_symbols": ["XAGUSD", "XAGUSD"]}, "duplicates"),
    ({"max_hold_bars": 0}, "max_hold_bars"),
    ({"max_hold_bars": G.MAX_HOLD_BARS + 1}, "max_hold_bars"),
    ({"max_hold_bars": True}, "max_hold_bars"),
    ({"warmup_bars": -1}, "warmup_bars"),
    ({"warmup_bars": 2001}, "warmup_bars"),
    ({"allow_short": "no"}, "allow_short"),
    ({"name": ""}, "name"),
    ({"id": "lab_ema_cross_xauusd\n", "family": "lab_ema_cross_xauusd"}, "id must match"),
    ({"family": "lab_ema_cross_xauusd\n"}, "family must match"),
    ({"id": "lab_elite_cross_xauusd", "family": "lab_ema_cross_xauusd"}, "id contains a forbidden label"),
    ({"family": "lab_tier_1_family"}, "family contains a forbidden label"),
    ({"family": "lab_edge_found_x"}, "family contains a forbidden label"),
    ({"name": "OPTIMAL EMA cross"}, "name contains a forbidden label"),
    ({"name": "Tier 1 gold"}, "name contains a forbidden label"),
])
def test_manifest_violations(over, needle):
    errs = C.validate_manifest(manifest(**over), strategy_py=STRATEGY, spec_md=SPEC)
    assert any(needle in e for e in errs), errs
    assert all(e.startswith("manifest: ") for e in errs)


def test_manifest_missing_keys_and_type():
    m = manifest()
    del m["symbol"]
    assert any("missing 'symbol'" in e for e in C.validate_manifest(m, strategy_py=STRATEGY, spec_md=SPEC))
    assert C.validate_manifest([1, 2]) == ["manifest: must be a JSON object"]


def test_check_universe():
    assert C.check_universe("EURUSD", "H1") == []
    assert C.check_universe("BTCUSD", "H1") == []
    assert C.check_universe("LTCUSD", "M5")
    assert C.check_universe("eurusd", "H1")          # canonical spelling only
    assert C.check_universe("EURUSD", "M1")


# ── lint_path and CLI ───────────────────────────────────────────────────────

def test_lint_path_and_main(tmp_path, capsys):
    good = tmp_path / "strategy.py"
    good.write_text(TEMPLATE, encoding="utf-8")
    assert C.lint_path(good) == []
    bad = tmp_path / "bad" / "strategy.py"
    bad.parent.mkdir()
    bad.write_text(with_body("x = df['close'].shift(-1)\nreturn x\n"), encoding="utf-8")
    out = C.lint_path(bad)
    assert out and out[0].startswith("strategy.py:9:")

    man = tmp_path / "manifest.json"
    man.write_text(json.dumps(manifest(code_sha256="", spec_sha256="")), encoding="utf-8")
    assert C.lint_path(man) == []
    man2 = tmp_path / "bad" / "manifest.json"
    man2.write_text('{"id": 1, "id": 2}', encoding="utf-8")
    assert "invalid JSON" in C.lint_path(man2)[0]
    assert C.lint_path(tmp_path / "spec.md") == []      # other names are not linted

    assert C.main(["lint", str(good), str(man)]) == 0
    assert C.main(["lint", str(bad)]) == 1
    assert "LOOKAHEAD" in capsys.readouterr().out
    assert C.main(["lint", str(tmp_path / "missing" / "strategy.py")]) == 2
    assert C.main([]) == 2
    assert C.main(["check", str(good)]) == 2


# ── adversarial review: lint bypasses that must be caught ───────────────────

@pytest.mark.parametrize("body,code", [
    # getattr with a literal name used to hide every attribute rule
    ("x = getattr(df['close'], 'shift')(-1)\nreturn x\n", "CALL"),
    ("x = getattr(df['close'], 'bfill')()\nreturn x\n", "LOOKAHEAD"),
    ("x = getattr(pd, 'read_csv')\nreturn df\n", "IO"),
    ("x = getattr(np, 'load')\nreturn df\n", "IO"),
    # stdlib modules reachable through public numpy/pandas/typing attributes
    ("x = typing.sys.modules\nreturn df\n", "IMPORT"),
    ("x = np.ma.core.builtins.open('f')\nreturn df\n", "IMPORT"),
    ("x = pd.compat.pickle_compat.pickle.loads(b'')\nreturn df\n", "IMPORT"),
    ("x = np.char.overrides.inspect\nreturn df\n", "IMPORT"),
    # private internals (np.lib._datasource.open reads files, ._datasource.os runs commands)
    ("x = np.lib._datasource.open('f')\nreturn df\n", "DUNDER"),
    # whole-series aggregates passed as functions / lists
    ("x = df['close'].agg(np.max)\nreturn x\n", "LOOKAHEAD"),
    ("x = df['close'].agg(['max'])\nreturn x\n", "LOOKAHEAD"),
    ("x = df.groupby(df.index.date)['high'].transform(np.max)\nreturn x\n", "LOOKAHEAD"),
    ("x = df.apply(max)\nreturn x\n", "LOOKAHEAD"),
    # a negative freq shifts the index back, so alignment reads the next bar
    ("df['fut'] = df['close'].shift(1, freq='-5min')\nreturn df\n", "LOOKAHEAD"),
    ("df['fut'] = df['close'].shift(freq=params['f'])\nreturn df\n", "LOOKAHEAD"),
    # reversing the series turns a cumulative op into a look-ahead one
    ("x = df['close'][::-1].cummax()[::-1]\nreturn x\n", "LOOKAHEAD"),
    ("x = np.flip(df['close'].to_numpy())\nreturn x\n", "LOOKAHEAD"),
    # nested PARAMS mutation
    ("PARAMS['n']['max'] = 99\nreturn df\n", "PARAMS"),
    ("PARAMS['n'].update(max=99)\nreturn df\n", "PARAMS"),
])
def test_review_bypasses_are_caught(body, code):
    src = with_body(body).replace("import numpy as np\n", "import typing\nimport numpy as np\n", 1)
    assert code in codes(src), (body, C.lint_source(src))


@pytest.mark.parametrize("body", [
    "x = df.open - df.close\nreturn x\n",                         # column attribute access stays legal
    "x = df['close'].shift(1, freq='5min').reindex(df.index)\nreturn x\n",
    "x = df['close'].rolling(5).agg(np.max)\nreturn x\n",
    "x = df[['open', 'close']].agg(np.max, axis=1)\nreturn x\n",
    "x = df['close'].iloc[1:]\nreturn x\n",
    "x = hasattr(df, 'close')\nreturn x\n",
])
def test_review_legitimate_code_still_clean(body):
    assert C.lint_source(with_body(body)) == []


@pytest.mark.parametrize("name", ["optimal_len", "EliteFast", "tier_1_mult", "edge_found_n", "Tier1x",
                                  "edgefound_n"])
def test_param_names_with_a_forbidden_label_are_refused(name):
    """Forbidden labels may not reach the scorecard through PARAMS names either (they appear in params)."""
    src = _p(**{name: '{"default": 10, "min": 5, "max": 20, "type": "int"}'})
    issues = [i for i in C.lint_source(src) if i.code == "PARAMS"]
    assert issues and "forbidden label" in issues[0].message
    with pytest.raises(C.ContractError):
        C.parse_params(src)


def test_forbidden_label_in_identifiers():
    assert C.forbidden_label_in("lab_elite_x") and C.forbidden_label_in("lab_tier_1_gold")
    assert C.forbidden_label_in("edge-found") and C.forbidden_label_in("OptimalLen")
    assert not C.forbidden_label_in("lab_breakout_tier_x") and not C.forbidden_label_in("lab_edge_x")


def test_forbidden_label_in_catches_multi_word_labels_written_as_one_word():
    """'Tier 1' / 'edge found' written without the space ('tier1', 'EdgeFound') is the same label."""
    for text in ("lab_tier1_gold", "TIER1 breakout", "lab_edgefound_x", "EdgeFound", "Tier-1"):
        assert C.forbidden_label_in(text), text
    # single-word labels are NOT matched across a joined word boundary ('range_lite' is not 'elite')
    for text in ("lab_range_lite_x", "volume_lite", "lab_tier_x_1", "lab_edge_x_found"):
        assert not C.forbidden_label_in(text), text
    errs = C.validate_manifest({"id": "lab_tier1_gold", "family": "lab_edgefound_x", "name": "Tier1 gold"},
                               require_shas=False)
    for key in ("id", "family", "name"):
        assert any(e.startswith(f"manifest: {key} contains a forbidden label") for e in errs), (key, errs)
