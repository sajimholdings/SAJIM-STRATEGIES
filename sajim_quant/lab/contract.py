"""
SAJIM Strategy Lab — the generated-strategy contract, enforced statically.

  lint_source(src)        AST lint of strategy.py (CONTRACT.md section 6, incl. PARAMS rules)
  parse_params(src)       the PARAMS literal as {name: {default, min, max, type}}
  default_params / clip_params
  validate_manifest(m)    manifest.json rules (CONTRACT.md section 4)
  check_universe(sym, tf) symbol / timeframe / coverage refusals
  lint_path(path)         what the PostToolUse hook calls
  main(argv)              `python -X utf8 -m sajim_quant.lab.contract lint <path>...`

The lint is HYGIENE, not the security boundary: it catches the accidental look-ahead,
smuggled chart knowledge and I/O a model is likely to write. Untrusted code only ever
runs inside a fresh no-network, no-secret sandbox; a determined author can defeat any
static check, which only costs the honesty of their own scorecard.

Nothing here imports pandas or numpy, so the local hook stays fast.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from sajim_quant.lab import gates, splits

logger = logging.getLogger("sajim_quant.lab.contract")

ISSUE_CODES: Tuple[str, ...] = (
    "SYNTAX", "ENCODING", "SIZE", "IMPORT", "CALL", "DUNDER", "IO", "LOOKAHEAD",
    "LITERAL", "MODULE", "RANDOM", "PARAMS",
)

# ── Import rules ────────────────────────────────────────────────────────────
ALLOWED_MODULES: Tuple[str, ...] = ("numpy", "pandas", "math", "typing")
INDICATORS_MODULE = "sajim_quant.lab.indicators"
BANNED_MODULES: Tuple[str, ...] = (
    "backtrader", "os", "sys", "subprocess", "socket", "pathlib", "importlib",
    "pickle", "requests", "urllib",
)

# ── Call / name / attribute rules ───────────────────────────────────────────
BANNED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
    "breakpoint", "input", "exit", "quit", "help", "memoryview",
})
DYNAMIC_ATTR_CALLS = frozenset({"getattr", "setattr", "delattr", "hasattr"})
#: Attribute names that evaluate strings or reach raw memory.
BANNED_ATTRS = frozenset({"eval", "query", "as_strided", "ctypes", "ctypeslib", "f2py"})
#: Frame / code introspection that is not spelled as a dunder.
INTROSPECTION_ATTRS = frozenset({
    "f_globals", "f_locals", "f_back", "f_builtins", "f_code", "gi_frame", "gi_code",
    "cr_frame", "cr_code", "ag_frame", "tb_frame", "tb_next", "co_code", "func_globals",
})
#: Stdlib modules that public numpy/pandas/typing attributes re-export
#: (typing.sys, np.ma.core.builtins, pd.compat.pickle_compat.pickle, np.f2py.os, ...).
ESCAPE_MODULE_ATTRS = frozenset(BANNED_MODULES) | frozenset({
    "builtins", "shutil", "inspect", "codecs", "mmap", "platform", "sysconfig", "tempfile",
    "zipfile", "tarfile", "marshal", "runpy", "multiprocessing", "threading", "posix", "nt", "http",
})
IO_NAMES = frozenset({"print"})
IO_ATTRS = frozenset({
    "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt",
    "fromfile", "tofile", "memmap", "fromregex", "DataSource", "dump", "dumps", "io",
    "testing", "show_config", "HDFStore", "ExcelWriter", "ExcelFile",
})
#: `to_*` methods that only convert in memory; every other `to_*` is treated as I/O.
SAFE_TO_ATTRS = frozenset({
    "to_numpy", "to_frame", "to_series", "to_list", "to_dict", "to_period", "to_timestamp",
    "to_datetime", "to_numeric", "to_timedelta", "to_pydatetime", "to_offset",
    "to_flat_index", "to_records", "to_string", "to_period_index", "to_julian_date",
})

# ── Look-ahead rules ────────────────────────────────────────────────────────
WINDOW_METHODS = frozenset({"rolling", "expanding", "ewm"})
WHOLE_SERIES_AGGS = frozenset({
    "mean", "std", "max", "min", "quantile", "rank", "median", "var", "sem", "skew", "kurt",
    "idxmax", "idxmin", "argmax", "argmin", "nlargest", "nsmallest", "describe", "percentile",
    "nanmean", "nanstd", "nanmax", "nanmin", "nanmedian", "nanquantile", "nanpercentile",
    "nanvar", "amax", "amin", "ptp",
})
FUNC_ARG_METHODS = frozenset({"apply", "agg", "aggregate", "transform"})
BACKFILL_ATTRS = frozenset({"bfill", "backfill", "interpolate"})
#: Reversing the row order turns a cumulative op into a look-ahead one.
REVERSE_ATTRS = frozenset({"flip", "flipud", "fliplr"})
BACKFILL_METHOD_VALUES = frozenset({"bfill", "backfill", "nearest"})

# ── Randomness ──────────────────────────────────────────────────────────────
SEEDABLE_RNG = frozenset({"default_rng", "Generator", "PCG64", "SeedSequence", "RandomState",
                          "Philox", "SFC64", "MT19937"})

# ── Smuggled-knowledge literals ─────────────────────────────────────────────
_DATE_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?:[-/.](?:0?[1-9]|1[0-2])(?:[-/.](?:0?[1-9]|[12]\d|3[01]))?"
    r"|(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))(?!\d)"
)
_DUNDER_STR_RE = re.compile(r"__\w+__")
#: Time-unit integers that exceed the small-integer bound but carry no chart knowledge.
TIME_CONSTANTS = frozenset({1440, 3600, 10080, 86400, 604800})
MAX_SMALL_INT = 1000

# ── PARAMS ──────────────────────────────────────────────────────────────────
PARAM_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
PARAM_KEYS = ("default", "min", "max", "type")
PARAM_TYPES = ("int", "float")
PARAMS_NAME = "PARAMS"
ENTRY_FUNCTION = "generate_signals"

# ── Manifest ────────────────────────────────────────────────────────────────
MANIFEST_REQUIRED: Tuple[str, ...] = (
    "schema", "id", "name", "description", "origin", "run_adapter", "engine", "symbol",
    "timeframe", "supported_symbols", "breadth_symbols", "max_hold_bars", "allow_short",
    "warmup_bars", "spec_sha256", "code_sha256",
)
MANIFEST_OPTIONAL: Tuple[str, ...] = ("family",)
MANIFEST_KEYS: Tuple[str, ...] = MANIFEST_REQUIRED[:2] + ("family",) + MANIFEST_REQUIRED[2:]
_SYSTEM_KEY_RE = re.compile(
    r"risk|balance|equity|cost|commission|spread|slippage|leverage|lot|date|start|end|window|"
    r"stage|holdout|capital", re.IGNORECASE,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_LAB_ID_RE = re.compile(gates.LAB_ID_PATTERN)
MAX_NAME_CHARS = 200


#: Multi-word FORBIDDEN_LABELS written as one word ('tier1', 'edgefound'), casefolded.
_JOINED_LABELS: Tuple[str, ...] = tuple(re.sub(r"\s+", "", lab).casefold()
                                       for lab in gates.FORBIDDEN_LABELS if re.search(r"\s", lab))


def forbidden_label_in(text: str) -> bool:
    """
    gates.contains_forbidden_label for identifiers too: '_' and '-' count as spaces, so
    'lab_tier_1_x', 'edge_found_len' and 'OptimalLen' are caught as well as plain text, and
    a multi-word label written as one word ('lab_tier1_x', 'EdgeFound') is caught too.
    Single-word labels are not matched across joined words ('range_lite' is not 'elite').
    """
    t = text or ""
    if gates.contains_forbidden_label(t) or gates.contains_forbidden_label(re.sub(r"[_\-]+", " ", t)):
        return True
    joined = re.sub(r"[\s_\-]+", "", t).casefold()
    return any(lab in joined for lab in _JOINED_LABELS)


class ContractError(ValueError):
    """The source or manifest cannot be interpreted at all (e.g. no PARAMS literal)."""


@dataclass(frozen=True)
class LintIssue:
    """One lint finding. `line` and `col` are 1-based (col as editors show it)."""

    line: int
    col: int
    code: str
    message: str

    def format(self, filename: str) -> str:
        return f"{filename}:{self.line}:{self.col}: {self.code} {self.message}"


# ── Source helpers ──────────────────────────────────────────────────────────
def _decode(source: Union[str, bytes]) -> Tuple[Optional[str], int, List[LintIssue]]:
    """(text or None, byte size, issues) — UTF-8 only (a BOM is tolerated)."""
    if isinstance(source, str):
        raw = source.encode("utf-8", errors="surrogatepass")
        text: Optional[str] = source
    else:
        raw = bytes(source)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return None, len(raw), [LintIssue(1, 1, "ENCODING", f"strategy.py must be UTF-8 ({exc.reason})")]
    issues: List[LintIssue] = []
    if len(raw) > gates.MAX_SOURCE_BYTES:
        issues.append(LintIssue(1, 1, "SIZE",
                                f"source is {len(raw)} bytes; the limit is {gates.MAX_SOURCE_BYTES}"))
    return text, len(raw), issues


def _parse(text: str) -> Tuple[Optional[ast.Module], List[LintIssue]]:
    try:
        return ast.parse(text, filename="strategy.py"), []
    except SyntaxError as exc:
        return None, [LintIssue(exc.lineno or 1, (exc.offset or 0) or 1, "SYNTAX", exc.msg or "invalid syntax")]
    except ValueError as exc:                       # e.g. null bytes
        return None, [LintIssue(1, 1, "SYNTAX", str(exc))]


def _pos(node: ast.AST) -> Tuple[int, int]:
    return getattr(node, "lineno", 1) or 1, (getattr(node, "col_offset", 0) or 0) + 1


def _num_literal(node: ast.AST) -> Optional[Union[int, float]]:
    """An int/float literal, allowing a leading unary minus/plus; bools are not numbers."""
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return sign * node.value
    return None


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _find_params_assigns(tree: ast.Module) -> List[Tuple[ast.stmt, ast.expr]]:
    out = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == PARAMS_NAME for t in stmt.targets):
            out.append((stmt, stmt.value))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.target.id == PARAMS_NAME and stmt.value is not None:
            out.append((stmt, stmt.value))
    return out


# ── PARAMS ──────────────────────────────────────────────────────────────────
def _params_from_node(node: ast.expr) -> Tuple[Dict[str, Dict[str, Any]], List[LintIssue]]:
    """
    Structural parse of the PARAMS dict literal. Returns the spec of every entry that
    could be read, plus STRUCTURAL issues (not a literal, bad keys, wrong value types).
    Range/count rules are checked separately by _params_rule_issues.
    """
    issues: List[LintIssue] = []
    spec: Dict[str, Dict[str, Any]] = {}
    if not isinstance(node, ast.Dict):
        return spec, [LintIssue(*_pos(node), "PARAMS", "PARAMS must be a dict literal")]
    for key_node, val_node in zip(node.keys, node.values):
        if key_node is None:
            issues.append(LintIssue(*_pos(val_node), "PARAMS", "PARAMS cannot use ** unpacking"))
            continue
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            issues.append(LintIssue(*_pos(key_node), "PARAMS", "PARAMS keys must be string literals"))
            continue
        name = key_node.value
        if not PARAM_NAME_RE.fullmatch(name):
            issues.append(LintIssue(*_pos(key_node), "PARAMS",
                                    f"param name {name!r} must match {PARAM_NAME_RE.pattern}"))
            continue
        if forbidden_label_in(name):
            issues.append(LintIssue(*_pos(key_node), "PARAMS",
                                    f"param name {name!r} contains a forbidden label "
                                    f"({', '.join(gates.FORBIDDEN_LABELS)})"))
            continue
        if name in spec:
            issues.append(LintIssue(*_pos(key_node), "PARAMS", f"duplicate param {name!r}"))
            continue
        if not isinstance(val_node, ast.Dict) or any(k is None for k in val_node.keys):
            issues.append(LintIssue(*_pos(val_node), "PARAMS",
                                    f"param {name!r} must be a dict literal with keys {list(PARAM_KEYS)}"))
            continue
        entry: Dict[str, Any] = {}
        bad = False
        for k, v in zip(val_node.keys, val_node.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)) or k.value not in PARAM_KEYS:
                issues.append(LintIssue(*_pos(k), "PARAMS",
                                        f"param {name!r}: unknown key (allowed: {list(PARAM_KEYS)})"))
                bad = True
                continue
            if k.value in entry:
                issues.append(LintIssue(*_pos(k), "PARAMS", f"param {name!r}: duplicate key {k.value!r}"))
                bad = True
                continue
            if k.value == "type":
                if not (isinstance(v, ast.Constant) and v.value in PARAM_TYPES):
                    issues.append(LintIssue(*_pos(v), "PARAMS", f"param {name!r}: type must be 'int' or 'float'"))
                    bad = True
                    continue
                entry["type"] = v.value
            else:
                num = _num_literal(v)
                if num is None or not math.isfinite(num):
                    issues.append(LintIssue(*_pos(v), "PARAMS",
                                            f"param {name!r}: {k.value} must be a finite number literal"))
                    bad = True
                    continue
                entry[k.value] = num
        missing = [k for k in PARAM_KEYS if k not in entry]
        if missing and not bad:
            issues.append(LintIssue(*_pos(val_node), "PARAMS", f"param {name!r}: missing {missing}"))
            bad = True
        if bad:
            continue
        if entry["type"] == "int":
            non_int = [k for k in ("default", "min", "max") if not isinstance(entry[k], int)]
            if non_int:
                issues.append(LintIssue(*_pos(val_node), "PARAMS",
                                        f"param {name!r}: an int param needs integer {non_int}"))
                continue
        else:
            for k in ("default", "min", "max"):
                entry[k] = float(entry[k])
        spec[name] = {"default": entry["default"], "min": entry["min"], "max": entry["max"],
                      "type": entry["type"]}
    return spec, issues


def _params_rule_issues(spec: Mapping[str, Mapping[str, Any]], line: int, col: int) -> List[LintIssue]:
    """CONTRACT.md section 3 range rules and the MIN..MAX_PARAMS count."""
    issues: List[LintIssue] = []
    if not gates.MIN_PARAMS <= len(spec) <= gates.MAX_PARAMS:
        issues.append(LintIssue(line, col, "PARAMS",
                                f"PARAMS has {len(spec)} entries; allowed {gates.MIN_PARAMS}..{gates.MAX_PARAMS}"))
    for name, e in spec.items():
        lo, hi, d, typ = e["min"], e["max"], e["default"], e["type"]
        if lo < 0:
            issues.append(LintIssue(line, col, "PARAMS", f"param {name!r}: min must be >= 0"))
            continue
        if not lo <= d <= hi:
            issues.append(LintIssue(line, col, "PARAMS", f"param {name!r}: needs min <= default <= max"))
        if lo >= hi:
            issues.append(LintIssue(line, col, "PARAMS",
                                    f"param {name!r}: min must be < max (a fixed value is a constant, not a param)"))
            continue
        if lo > 0:
            if hi / lo > gates.MAX_PARAM_RANGE_RATIO:
                issues.append(LintIssue(line, col, "PARAMS",
                                        f"param {name!r}: max/min = {hi / lo:g} exceeds {gates.MAX_PARAM_RANGE_RATIO}"))
        elif typ == "int":
            if hi > gates.MAX_ZERO_BASED_INT_MAX:
                issues.append(LintIssue(line, col, "PARAMS",
                                        f"param {name!r}: a zero-based int param needs max <= "
                                        f"{gates.MAX_ZERO_BASED_INT_MAX}"))
        else:
            if d <= 0:
                issues.append(LintIssue(line, col, "PARAMS",
                                        f"param {name!r}: a zero-based float param needs default > 0"))
            elif hi > gates.MAX_PARAM_RANGE_RATIO * d:
                issues.append(LintIssue(line, col, "PARAMS",
                                        f"param {name!r}: a zero-based float param needs max <= "
                                        f"{gates.MAX_PARAM_RANGE_RATIO} x default"))
    return issues


def parse_params(source: Union[str, bytes]) -> Dict[str, Dict[str, Any]]:
    """
    The module-level PARAMS literal as {name: {"default", "min", "max", "type"}}, in
    insertion order (int params hold ints, float params floats).

    Raises ContractError when there is no single readable PARAMS literal. Range and count
    rules are NOT enforced here (lint_source does that), so G1 can report n_params = 6
    honestly instead of failing to count.
    """
    text, _, enc = _decode(source)
    if text is None:
        raise ContractError(enc[0].message)
    tree, syn = _parse(text)
    if tree is None:
        raise ContractError(f"syntax error: {syn[0].message} (line {syn[0].line})")
    assigns = _find_params_assigns(tree)
    if len(assigns) != 1:
        raise ContractError("strategy.py must assign PARAMS exactly once at module level"
                            if assigns else "strategy.py has no module-level PARAMS dict literal")
    spec, issues = _params_from_node(assigns[0][1])
    if issues:
        raise ContractError("; ".join(f"line {i.line}: {i.message}" for i in issues))
    return spec


def default_params(params_spec: Mapping[str, Mapping[str, Any]]) -> Dict[str, Union[int, float]]:
    """{name: default}, typed per the spec, insertion order kept."""
    return {name: (int(e["default"]) if e["type"] == "int" else float(e["default"]))
            for name, e in params_spec.items()}


def _round_half_away(v: float) -> int:
    return int(math.copysign(math.floor(abs(v) + 0.5), v))


def clip_params(params_spec: Mapping[str, Mapping[str, Any]],
                values: Mapping[str, float]) -> Dict[str, Union[int, float]]:
    """
    Values for every spec'd param (missing ones take the default), clipped to [min, max].
    Int params are rounded half-away-from-zero first. Unknown names or non-finite values
    raise ContractError.
    """
    unknown = [k for k in values if k not in params_spec]
    if unknown:
        raise ContractError(f"unknown params {unknown}")
    out: Dict[str, Union[int, float]] = {}
    for name, e in params_spec.items():
        v = float(values.get(name, e["default"]))
        if not math.isfinite(v):
            raise ContractError(f"param {name!r} is not finite")
        if e["type"] == "int":
            out[name] = int(min(max(_round_half_away(v), int(e["min"])), int(e["max"])))
        else:
            out[name] = float(min(max(v, float(e["min"])), float(e["max"])))
    return out


# ── The linter ──────────────────────────────────────────────────────────────
def _price_like(v: Union[int, float]) -> bool:
    """
    Heuristic for smuggled chart knowledge: years, price levels, many-decimal quotes.
    Small integers, session hours, round multipliers, time-unit constants and powers of
    ten (pip sizes, epsilons) are fine.
    """
    a = abs(v)
    if a == 0 or a in TIME_CONSTANTS:
        return False
    lg = math.log10(a)
    if abs(lg - round(lg)) < 1e-12:
        return False
    if isinstance(v, int):
        return a > MAX_SMALL_INT
    if a > MAX_SMALL_INT:
        return True
    if a >= 10 and a != round(a):
        return True
    return a >= 0.5 and round(a, 3) != a


def _is_window_expr(node: ast.AST) -> bool:
    """True for `<x>.rolling(...)` / `.expanding(...)` / `.ewm(...)`, optionally subscripted."""
    while isinstance(node, (ast.Subscript, ast.Attribute)) and not (
            isinstance(node, ast.Attribute) and node.attr in WINDOW_METHODS):
        node = node.value
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WINDOW_METHODS


def _kw(call: ast.Call, name: str) -> Optional[ast.expr]:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _row_wise(call: ast.Call) -> bool:
    ax = _kw(call, "axis")
    return isinstance(ax, ast.Constant) and ax.value in (1, "columns")


def _whole_agg_ref(node: Optional[ast.AST]) -> Optional[str]:
    """The aggregate named by an apply/agg/transform argument: 'max', np.max, max, ['max'], {c: 'max'}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in WHOLE_SERIES_AGGS:
        return node.value
    if isinstance(node, ast.Attribute) and node.attr in WHOLE_SERIES_AGGS:
        return node.attr
    if isinstance(node, ast.Name) and node.id in WHOLE_SERIES_AGGS:
        return node.id
    elts = node.elts if isinstance(node, (ast.List, ast.Tuple, ast.Set)) else (
        node.values if isinstance(node, ast.Dict) else [])
    for e in elts:
        found = _whole_agg_ref(e)
        if found:
            return found
    return None


def _root_name(node: ast.AST) -> Optional[str]:
    """`PARAMS` for PARAMS['a']['b'] / PARAMS['a'].x; None when the chain does not start at a name."""
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _Linter(ast.NodeVisitor):
    def __init__(self) -> None:
        self.issues: List[LintIssue] = []
        self._ok_random: set = set()
        self._skip: set = set()
        self._in_window_fn = 0

    def add(self, node: ast.AST, code: str, message: str) -> None:
        self.issues.append(LintIssue(*_pos(node), code, message))

    # imports
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name
            if mod in ALLOWED_MODULES or mod == INDICATORS_MODULE:
                continue
            top = mod.split(".")[0]
            why = "is banned" if top in BANNED_MODULES else "is not allowed"
            self.add(node, "IMPORT", f"import {mod} {why}; allowed: {', '.join(ALLOWED_MODULES)}, "
                                     f"and `from sajim_quant.lab import indicators as ind`")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        names = [a.name for a in node.names]
        if node.level:
            self.add(node, "IMPORT", "relative imports are not allowed")
            return
        if "*" in names:
            self.add(node, "IMPORT", f"from {mod} import * is not allowed")
            return
        if mod == "__future__":
            if names != ["annotations"]:
                self.add(node, "IMPORT", "only `from __future__ import annotations` is allowed")
            return
        if mod == "sajim_quant.lab":
            if names != ["indicators"]:
                self.add(node, "IMPORT", "only `from sajim_quant.lab import indicators` is allowed")
            return
        if mod == INDICATORS_MODULE:
            for n in names:
                if n.startswith("_"):
                    self.add(node, "DUNDER", f"private indicators name {n!r}")
            return
        if mod not in ALLOWED_MODULES:
            top = mod.split(".")[0]
            why = "is banned" if top in BANNED_MODULES else "is not allowed"
            self.add(node, "IMPORT", f"from {mod} import ... {why}")
            return
        for n in names:
            self._check_attr_name(node, n)
            if n == "random":
                self.add(node, "RANDOM", "unseeded randomness: use np.random.default_rng(<seed>)")

    # names
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BANNED_NAMES:
            self.add(node, "CALL", f"{node.id} is not allowed")
        elif node.id in IO_NAMES:
            self.add(node, "IO", f"{node.id}() is I/O; strategies return data only")
        elif node.id.startswith("__"):
            self.add(node, "DUNDER", f"dunder name {node.id}")

    def _check_attr_name(self, node: ast.AST, attr: str) -> None:
        if attr.startswith("__") or attr in INTROSPECTION_ATTRS:
            self.add(node, "DUNDER", f"attribute .{attr} is not allowed")
        elif attr.startswith("_"):
            self.add(node, "DUNDER", f"private attribute .{attr} is not allowed (library internals)")
        elif attr in ESCAPE_MODULE_ATTRS:
            self.add(node, "IMPORT", f".{attr} reaches a banned module; allowed: {', '.join(ALLOWED_MODULES)}")
        elif attr in BANNED_ATTRS:
            self.add(node, "CALL", f".{attr} is not allowed (string evaluation / raw memory)")
        elif attr.startswith("read_") or attr in IO_ATTRS or (attr.startswith("to_") and attr not in SAFE_TO_ATTRS):
            self.add(node, "IO", f".{attr} is I/O; strategies receive df and return data only")
        elif attr in BACKFILL_ATTRS:
            self.add(node, "LOOKAHEAD", f".{attr}() fills from later rows (look-ahead); use .ffill()")
        elif attr == "resample":
            self.add(node, "LOOKAHEAD", ".resample() labels bars with data from the future; "
                                        "use ind.htf(df, rule) for closed higher-TF bars")
        elif attr == "roll":
            self.add(node, "LOOKAHEAD", "np.roll wraps later rows into earlier ones; use .shift(n) with n >= 0")
        elif attr in REVERSE_ATTRS:
            self.add(node, "LOOKAHEAD", f".{attr}() reverses the rows, so later rows feed earlier ones")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_attr_name(node, node.attr)
        if node.attr == "random" and id(node) not in self._ok_random:
            self.add(node, "RANDOM", "unseeded randomness: use np.random.default_rng(<seed>)")
        self.generic_visit(node)

    # strings / numbers
    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self._skip:
            return
        v = node.value
        if isinstance(v, str):
            if _DATE_RE.search(v):
                self.add(node, "LITERAL", f"date literal {v[:40]!r}; dates belong to the server, not the strategy")
            if _DUNDER_STR_RE.search(v):
                self.add(node, "DUNDER", "string literal names a dunder")
        elif type(v) in (int, float) and _price_like(v):
            self.add(node, "LITERAL", f"literal {v!r} looks like a price or date; "
                                      f"make it a PARAMS entry or derive it from the data")

    # calls
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in DYNAMIC_ATTR_CALLS:
            name_arg = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
                self.add(node, "CALL", f"{func.id}() needs a literal attribute name")
            else:
                # the literal name gets every attribute rule; a call through getattr would
                # also hide its arguments (shift(-1), agg('max')), so only hasattr is allowed
                self._check_attr_name(node, name_arg.value)
                if func.id != "hasattr":
                    self.add(node, "CALL", f"{func.id}(x, {name_arg.value!r}) hides the attribute from "
                                           f"the lint; write x.{name_arg.value}")
        for kw in node.keywords:
            if kw.arg is None:
                continue
            val = kw.value
            if kw.arg == "center" and not (isinstance(val, ast.Constant) and val.value is False):
                self.add(kw.value, "LOOKAHEAD", "center=True windows use later rows")
            elif kw.arg in ("method", "fill_method") and isinstance(val, ast.Constant) \
                    and val.value in BACKFILL_METHOD_VALUES:
                self.add(kw.value, "LOOKAHEAD", f"{kw.arg}={val.value!r} fills from later rows")
            elif kw.arg == "direction" and isinstance(val, ast.Constant) and val.value in ("forward", "nearest"):
                self.add(kw.value, "LOOKAHEAD", f"direction={val.value!r} matches later rows")
            elif kw.arg == "limit_direction" and isinstance(val, ast.Constant) and val.value in ("backward", "both"):
                self.add(kw.value, "LOOKAHEAD", f"limit_direction={val.value!r} fills from later rows")
        if isinstance(func, ast.Attribute):
            self._check_method_call(node, func)
        # visit children; arguments of rolling(...).apply(f) may aggregate a window
        self.visit(func)
        window_fn = isinstance(func, ast.Attribute) and func.attr in FUNC_ARG_METHODS and _is_window_expr(func.value)
        if window_fn:
            self._in_window_fn += 1
        for a in node.args:
            self.visit(a)
        for kw in node.keywords:
            self.visit(kw.value)
        if window_fn:
            self._in_window_fn -= 1

    def _check_method_call(self, node: ast.Call, func: ast.Attribute) -> None:
        attr = func.attr
        if attr == "shift":
            arg = node.args[0] if node.args else _kw(node, "periods")
            if arg is not None:
                val = _num_literal(arg)
                if val is None or not isinstance(val, int):
                    self.add(node, "LOOKAHEAD", ".shift() needs a literal integer >= 0 "
                                                "(a computed shift can read the future)")
                elif val < 0:
                    self.add(node, "LOOKAHEAD", f".shift({val}) reads the future")
            freq = _kw(node, "freq")
            if freq is not None and not (isinstance(freq, ast.Constant) and (
                    freq.value is None or (isinstance(freq.value, str) and not freq.value.lstrip().startswith("-")))):
                self.add(node, "LOOKAHEAD", ".shift(freq=...) needs a literal non-negative frequency "
                                            "(a negative one moves later rows onto earlier times)")
        elif attr in ("diff", "pct_change"):
            arg = node.args[0] if node.args else _kw(node, "periods")
            val = _num_literal(arg) if arg is not None else None
            if val is not None and val < 0:
                self.add(node, "LOOKAHEAD", f".{attr}({val}) reads the future")
        elif attr in WHOLE_SERIES_AGGS and not self._in_window_fn:
            if not _is_window_expr(func.value) and not _row_wise(node):
                self.add(node, "LOOKAHEAD", f"whole-series .{attr}() sees every later row; "
                                            f"use .rolling(n).{attr}() / .expanding().{attr}()")
        elif attr in FUNC_ARG_METHODS and not _is_window_expr(func.value) and not self._in_window_fn:
            first = node.args[0] if node.args else _kw(node, "func")
            agg = _whole_agg_ref(first)
            if agg and not _row_wise(node):
                self.add(node, "LOOKAHEAD", f".{attr}({agg!r}) aggregates the whole series or group, "
                                            f"including later rows")
        elif attr in SEEDABLE_RNG and isinstance(func.value, ast.Attribute) and func.value.attr == "random":
            seed = node.args[0] if node.args else (_kw(node, "seed") or _kw(node, "entropy"))
            if seed is None or (isinstance(seed, ast.Constant) and seed.value is None):
                self.add(node, "RANDOM", f"np.random.{attr}() needs an explicit seed")
            else:
                self._ok_random.add(id(func.value))
        elif attr == "sample" and _kw(node, "random_state") is None:
            self.add(node, "RANDOM", ".sample() needs random_state=<seed>")
        if _root_name(func.value) == PARAMS_NAME and attr in (
                "update", "pop", "popitem", "clear", "setdefault"):
            self.add(node, "PARAMS", "PARAMS is read-only; use the params argument")

    # stores to PARAMS (also nested: PARAMS["n"]["max"] = ...)
    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)) and _root_name(node.value) == PARAMS_NAME:
            self.add(node, "PARAMS", "PARAMS is read-only; use the params argument")
        self.generic_visit(node)

    def visit_Slice(self, node: ast.Slice) -> None:
        step = _num_literal(node.step) if node.step is not None else None
        if node.step is not None and (step is None or step < 0):
            self.add(node, "LOOKAHEAD", "a negative or computed slice step reverses the rows, "
                                        "so later rows feed earlier ones")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        if PARAMS_NAME in node.names:
            self.add(node, "PARAMS", "PARAMS is read-only; use the params argument")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.body and _is_docstring(node.body[0]):
            self._skip.add(id(node.body[0].value))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef     # type: ignore[assignment]


def _const_expr(node: ast.AST) -> bool:
    """A side-effect-free module-level value: literals, names, attribute reads and arithmetic."""
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        return _const_expr(node.value)
    if isinstance(node, ast.UnaryOp):
        return _const_expr(node.operand)
    if isinstance(node, ast.BinOp):
        return _const_expr(node.left) and _const_expr(node.right)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_const_expr(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _const_expr(k) for k in node.keys) and all(_const_expr(v) for v in node.values)
    return False


def _module_issues(tree: ast.Module) -> List[LintIssue]:
    issues: List[LintIssue] = []
    entry_defs = 0
    for k, stmt in enumerate(tree.body):
        if _is_docstring(stmt) or isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names_ok = all(isinstance(t, ast.Name) or (isinstance(t, ast.Tuple) and all(
                isinstance(e, ast.Name) for e in t.elts)) for t in targets)
            if not names_ok or stmt.value is None or not _const_expr(stmt.value):
                issues.append(LintIssue(*_pos(stmt), "MODULE",
                                        "module level may only assign constants (no calls or I/O)"))
            continue
        if isinstance(stmt, ast.FunctionDef):
            if stmt.decorator_list:
                issues.append(LintIssue(*_pos(stmt), "MODULE", f"decorators are not allowed ({stmt.name})"))
            defaults = list(stmt.args.defaults) + [d for d in stmt.args.kw_defaults if d is not None]
            if not all(_const_expr(d) for d in defaults):
                issues.append(LintIssue(*_pos(stmt), "MODULE", f"{stmt.name}: default values must be constants"))
            if stmt.name == ENTRY_FUNCTION:
                entry_defs += 1
                a = stmt.args
                n_pos = len(a.posonlyargs) + len(a.args)
                required_kwonly = [x for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
                if not ((n_pos >= 2 or a.vararg) and n_pos - len(a.defaults) <= 2 and not required_kwonly):
                    issues.append(LintIssue(*_pos(stmt), "MODULE",
                                            f"{ENTRY_FUNCTION} must accept (df, params)"))
            continue
        what = type(stmt).__name__
        issues.append(LintIssue(*_pos(stmt), "MODULE",
                                f"module-level {what} is not allowed; only imports, PARAMS, constants "
                                f"and function definitions"))
    if entry_defs == 0:
        issues.append(LintIssue(1, 1, "MODULE", f"no module-level def {ENTRY_FUNCTION}(df, params)"))
    elif entry_defs > 1:
        issues.append(LintIssue(1, 1, "MODULE", f"{ENTRY_FUNCTION} is defined more than once"))
    return issues


def lint_source(source: Union[str, bytes]) -> List[LintIssue]:
    """
    Every CONTRACT.md section 6 violation in a strategy.py, sorted by position.
    [] means clean. Never raises on bad input; unreadable source is a SYNTAX/ENCODING issue.
    """
    text, _, issues = _decode(source)
    if text is None:
        return issues
    tree, syn = _parse(text)
    if tree is None:
        return issues + syn

    linter = _Linter()
    if tree.body and _is_docstring(tree.body[0]):
        linter._skip.add(id(tree.body[0].value))

    assigns = _find_params_assigns(tree)
    if not assigns:
        issues.append(LintIssue(1, 1, "PARAMS", "no module-level PARAMS dict literal"))
    else:
        if len(assigns) > 1:
            issues.append(LintIssue(*_pos(assigns[1][0]), "PARAMS", "PARAMS is assigned more than once"))
        stmt, value = assigns[0]
        spec, struct = _params_from_node(value)
        issues.extend(struct)
        if not struct:
            issues.extend(_params_rule_issues(spec, *_pos(stmt)))
        for sub in ast.walk(value):                # numbers inside PARAMS are the point of PARAMS
            linter._skip.add(id(sub))
    # any other store to the PARAMS name (e.g. a rebinding inside a function)
    top_targets = {id(t) for s, _ in assigns
                   for t in (s.targets if isinstance(s, ast.Assign) else [s.target])}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == PARAMS_NAME and isinstance(node.ctx, (ast.Store, ast.Del)) \
                and id(node) not in top_targets:
            issues.append(LintIssue(*_pos(node), "PARAMS", "PARAMS is read-only; use the params argument"))

    issues.extend(_module_issues(tree))
    linter.visit(tree)
    issues.extend(linter.issues)
    uniq = sorted(set(issues), key=lambda i: (i.line, i.col, i.code, i.message))
    return uniq


# ── Universe and manifest ───────────────────────────────────────────────────
def check_universe(symbol: str, timeframe: str) -> List[str]:
    """[] when SYMBOL x TF may be built; otherwise human-readable refusals."""
    from sajim_quant.lab.interfaces import SHORT_COVERAGE_REFUSED     # lazy: interfaces imports pandas

    errs: List[str] = []
    if not isinstance(symbol, str) or symbol not in splits.ALLOWED_SYMBOLS:
        errs.append(f"symbol {symbol!r} is not one of {', '.join(splits.ALLOWED_SYMBOLS)}")
    if not isinstance(timeframe, str) or timeframe not in splits.ALLOWED_TIMEFRAMES:
        errs.append(f"timeframe {timeframe!r} is not one of {', '.join(splits.ALLOWED_TIMEFRAMES)}")
    if not errs and (symbol, timeframe) in SHORT_COVERAGE_REFUSED:
        errs.append(f"{symbol} {timeframe} is refused: DEV or HOLDOUT coverage is below "
                    f"{splits.MIN_COVERAGE:.0%}")
    return errs


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _sha_issue(key: str, value: Any, blob: Optional[bytes], require: bool, what: str) -> Optional[str]:
    if not isinstance(value, str):
        return f"{key} must be a string"
    if not require:
        if value == "" or _SHA_RE.fullmatch(value):
            return None
        return f"{key} must be empty or 64 lowercase hex characters"
    if blob is None:
        return f"{key}: the {what} bytes are required to verify it"
    actual = hashlib.sha256(blob).hexdigest()
    if value != actual:
        return f"{key} does not match sha256({what})"
    return None


def validate_manifest(manifest: Mapping, *, strategy_py: Optional[bytes] = None,
                      spec_md: Optional[bytes] = None, require_shas: bool = True) -> List[str]:
    """Every CONTRACT.md section 4 violation as a 'manifest: ...' string; [] when valid."""
    from sajim_quant.core.instruments import get_spec

    if not isinstance(manifest, Mapping):
        return ["manifest: must be a JSON object"]
    errs: List[str] = []

    def bad(msg: str) -> None:
        errs.append(f"manifest: {msg}")

    for key in manifest:
        if key in MANIFEST_KEYS:
            continue
        if isinstance(key, str) and _SYSTEM_KEY_RE.search(key):
            bad(f"{key!r} is not allowed: risk, balance, costs and dates are system/server settings")
        else:
            bad(f"unknown key {key!r}")
    for key in MANIFEST_REQUIRED:
        if key not in manifest:
            bad(f"missing {key!r}")
    m = manifest
    if "schema" in m and m["schema"] != gates.MANIFEST_SCHEMA:
        bad(f"schema must be {gates.MANIFEST_SCHEMA!r}")
    if "id" in m and not (isinstance(m["id"], str) and _LAB_ID_RE.fullmatch(m["id"])):
        bad(f"id must match {gates.LAB_ID_PATTERN}")
    if "family" in m and not (isinstance(m["family"], str) and _LAB_ID_RE.fullmatch(m["family"])):
        bad(f"family must match {gates.LAB_ID_PATTERN}")
    if "name" in m and not (isinstance(m["name"], str) and m["name"].strip() and len(m["name"]) <= MAX_NAME_CHARS):
        bad(f"name must be a non-empty string of at most {MAX_NAME_CHARS} characters")
    for key in ("id", "family", "name"):
        if isinstance(m.get(key), str) and forbidden_label_in(m[key]):
            bad(f"{key} contains a forbidden label ({', '.join(gates.FORBIDDEN_LABELS)}); "
                f"lab strategies are never labelled that way")
    if "description" in m and not (isinstance(m["description"], str) and m["description"].strip()):
        bad("description must be the verbatim (non-empty) user text")
    for key, want in (("origin", gates.ORIGIN), ("run_adapter", gates.RUN_ADAPTER), ("engine", gates.ENGINE)):
        if key in m and m[key] != want:
            bad(f"{key} must be {want!r}")

    symbol, tf = m.get("symbol"), m.get("timeframe")
    if "symbol" in m and "timeframe" in m:
        errs.extend(f"manifest: {e}" for e in check_universe(symbol, tf))
    primary_ok = isinstance(symbol, str) and symbol in splits.ALLOWED_SYMBOLS
    primary_class = get_spec(symbol).asset_class if primary_ok else None

    sup = m.get("supported_symbols")
    if "supported_symbols" in m:
        if not (isinstance(sup, list) and sup and all(isinstance(s, str) for s in sup)):
            bad("supported_symbols must be a non-empty list of symbols")
        else:
            if len(set(sup)) != len(sup):
                bad("supported_symbols has duplicates")
            for s in sup:
                if s not in splits.ALLOWED_SYMBOLS:
                    bad(f"supported_symbols: {s!r} is not an allowed symbol")
            if primary_ok and symbol not in sup:
                bad("supported_symbols must include the primary symbol")

    br = m.get("breadth_symbols")
    if "breadth_symbols" in m:
        if not (isinstance(br, list) and all(isinstance(s, str) for s in br)):
            bad("breadth_symbols must be a list of symbols")
        else:
            if len(br) > gates.MAX_BREADTH_SYMBOLS:
                bad(f"breadth_symbols allows at most {gates.MAX_BREADTH_SYMBOLS}")
            if len(set(br)) != len(br):
                bad("breadth_symbols has duplicates")
            for s in br:
                if s not in splits.ALLOWED_SYMBOLS:
                    bad(f"breadth_symbols: {s!r} is not an allowed symbol")
                    continue
                if s == symbol:
                    bad("breadth_symbols must not include the primary symbol")
                elif primary_class is not None and get_spec(s).asset_class != primary_class:
                    bad(f"breadth_symbols: {s} is not in the primary's asset class ({primary_class})")
                elif isinstance(tf, str) and tf in splits.ALLOWED_TIMEFRAMES:
                    for e in check_universe(s, tf):
                        bad(f"breadth_symbols: {e}")

    mh = m.get("max_hold_bars")
    if "max_hold_bars" in m and not (_is_int(mh) and 1 <= mh <= gates.MAX_HOLD_BARS):
        bad(f"max_hold_bars must be an integer in 1..{gates.MAX_HOLD_BARS}")
    wb = m.get("warmup_bars")
    if "warmup_bars" in m and not (_is_int(wb) and 0 <= wb <= gates.MAX_WARMUP_BARS):
        bad(f"warmup_bars must be an integer in 0..{gates.MAX_WARMUP_BARS}")
    if "allow_short" in m and not isinstance(m["allow_short"], bool):
        bad("allow_short must be true or false")

    for key, blob, what in (("code_sha256", strategy_py, "strategy.py"), ("spec_sha256", spec_md, "spec.md")):
        if key in m:
            msg = _sha_issue(key, m[key], blob, require_shas, what)
            if msg:
                bad(msg)
    return errs


def _strict_json(raw: bytes) -> Any:
    def no_dupes(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError(f"duplicate key {k!r}")
            d[k] = v
        return d

    def no_const(name):
        raise ValueError(f"{name} is not valid JSON")

    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=no_dupes, parse_constant=no_const)


def lint_path(path: Path) -> List[str]:
    """
    Formatted problems for one file, by name: strategy.py -> lint_source;
    manifest.json -> validate_manifest(require_shas=False); anything else -> [].
    Raises OSError when the file cannot be read.
    """
    path = Path(path)
    name = path.name.lower()
    if name == "strategy.py":
        return [i.format(path.name) for i in lint_source(path.read_bytes())]
    if name == "manifest.json":
        raw = path.read_bytes()
        try:
            data = _strict_json(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            return [f"{path.name}: invalid JSON ({exc})"]
        return [f"{path.name}: {e}" for e in validate_manifest(data, require_shas=False)]
    return []


USAGE = "usage: python -X utf8 -m sajim_quant.lab.contract lint <path> [<path> ...]"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Exit 0 clean, 1 issues found, 2 usage error or unreadable file."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "lint":
        print(USAGE)
        return 2
    found = False
    for p in args[1:]:
        try:
            problems = lint_path(Path(p))
        except OSError as exc:
            print(f"{p}: cannot read ({exc.strerror or exc})")
            return 2
        for line in problems:
            print(line)
        found = found or bool(problems)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
