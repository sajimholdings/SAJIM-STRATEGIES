"""
The Strategy Lab Claude layer: lab skills, critic agent, lab settings, bundled examples
and templates, the cost reference and the CLAUDE.md section.

The worked examples are run in-process on SYNTHETIC OHLC only, to prove they follow
CONTRACT.md section 3 (schema, dtypes, value rules) and are causal under truncation.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from sajim_quant.lab import contract, gates, interfaces, splits

sys.dont_write_bytecode = True        # no __pycache__ in .claude/lab/hooks or .claude/skills

REPO = Path(__file__).resolve().parents[2]
CLAUDE = REPO / ".claude"
BUILD = CLAUDE / "skills" / "lab-build"
REVIEW = CLAUDE / "skills" / "lab-review"
AGENT = CLAUDE / "agents" / "strategy-critic.md"
SETTINGS = CLAUDE / "lab" / "settings.json"
HOOKS = CLAUDE / "lab" / "hooks"
EXAMPLES = {
    "london_breakout": {"allow_short": False},
    "ema_pullback": {"allow_short": True},
    "zscore_reversion": {"allow_short": True},
}


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, f"{path} has no YAML frontmatter"
    return yaml.safe_load(m.group(1)), m.group(2)


def tool_list(value) -> list[str]:
    """allowed-tools / tools: a comma-separated string (commas inside (...) kept)."""
    if isinstance(value, list):
        return [str(v).strip() for v in value]
    return [t.strip() for t in re.split(r",\s*(?![^()]*\))", str(value)) if t.strip()]


# ── Skills and agent ────────────────────────────────────────────────────────
def test_lab_build_frontmatter():
    fm, body = frontmatter(BUILD / "SKILL.md")
    assert fm["name"] == "lab-build"
    assert fm["description"]
    assert fm["argument-hint"] == "<lab_id> --stage spec|code"
    assert fm["disable-model-invocation"] is True
    tools = tool_list(fm["allowed-tools"])
    assert tools == ["Read", "Glob", "Grep", "Edit(strategies/generated/**)",
                     "Bash(python -X utf8 -m sajim_quant.lab.cli *)"]
    assert not any(t.startswith("Write(") for t in tools), "Write(path) rules are ignored (P0)"
    assert len((BUILD / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 300
    assert "$ARGUMENTS" in body
    assert "<user_strategy_description>" in body


def test_lab_build_body_covers_the_hard_rules():
    body = frontmatter(BUILD / "SKILL.md")[1]
    for phrase in ("## Stage `spec`", "## Stage `code`", "at most 2", "spec-fix", "bug-fix",
                   "Trial k of 3", "never", "data/**", "grid-search", "edge", "IN-SAMPLE",
                   "done --final", "lab.cli check", "lab.cli dev", "summary"):
        assert phrase in body, phrase
    # every spec.md section of CONTRACT.md section 2, in order
    heads = re.findall(r"^## (\d)\. (.+)$", body, re.M)
    assert [h[1] for h in heads] == ["Description", "Rules", "PARAMS", "Mechanism", "Universe",
                                     "Expected trades per month", "Assumptions and ambiguities",
                                     "Trials"]


def test_lab_build_bundled_files_exist():
    for rel in ("reference/contract.md", "reference/indicators.md", "reference/costs.md",
                "examples/london_breakout.py", "examples/ema_pullback.py",
                "examples/zscore_reversion.py", "templates/strategy.py", "templates/manifest.json"):
        assert (BUILD / rel).is_file(), rel
    ref = (BUILD / "reference" / "contract.md").read_text(encoding="utf-8")
    assert "sajim_quant/lab/CONTRACT.md" in ref


def test_lab_review_frontmatter():
    fm, body = frontmatter(REVIEW / "SKILL.md")
    assert fm["name"] == "lab-review"
    assert fm["context"] == "fork"
    assert fm["agent"] == "strategy-critic"
    assert fm["disable-model-invocation"] is True
    tools = tool_list(fm["allowed-tools"])
    assert "Edit(strategies/generated/**)" in tools
    assert not any(t.startswith("Write(") for t in tools)
    assert "Bash(python -X utf8 -m sajim_quant.lab.cli review *)" in tools
    for phrase in ("KEEP", "DOWNGRADE", "review.json", interfaces.REVIEW_SCHEMA,
                   "review --file review.json", "holdout"):
        assert phrase in body, phrase


def test_strategy_critic_agent():
    fm, body = frontmatter(AGENT)
    assert fm["name"] == "strategy-critic"
    assert tool_list(fm["tools"]) == ["Read", "Grep", "Glob", "Write", "Bash"]
    assert tool_list(fm["disallowedTools"]) == ["Edit", "NotebookEdit", "WebFetch", "WebSearch"]
    assert fm["model"] == "inherit"
    assert fm["maxTurns"] == interfaces.MAX_TURNS["review"] == 15
    assert "Downgrade-only" in body and "review.json" in body


# ── Commands in the skills match the bash guard exactly ─────────────────────
def _guard_bash_patterns():
    spec = importlib.util.spec_from_file_location("lab_guard_bash", HOOKS / "guard_bash.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.STAGE_PATTERNS


@pytest.mark.parametrize("skill,stage", [(BUILD / "SKILL.md", "code"), (REVIEW / "SKILL.md", "review"),
                                         (AGENT, "review")])
def test_documented_commands_pass_the_bash_guard(skill, stage):
    pattern = _guard_bash_patterns()[stage]
    text = skill.read_text(encoding="utf-8")
    body = frontmatter(skill)[1]
    cmds = re.findall(r"`(python -X utf8 -m sajim_quant\.lab\.cli [^`]*)`", body)
    assert cmds, "the skill documents its commands"
    for cmd in cmds:
        concrete = (cmd.replace("<sha12>", "0123456789ab").replace("[--sha 0123456789ab]", "")
                    .replace("<subcommand>", "check").strip())
        assert pattern.fullmatch(concrete), (skill.name, cmd)
    assert "--file -" not in text


# ── settings.json ───────────────────────────────────────────────────────────
def test_settings_permissions():
    s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    allow, deny = s["permissions"]["allow"], s["permissions"]["deny"]
    assert not any(r.startswith("Write(") for r in allow + deny), "use Edit(path), never Write(path)"
    for rule in ("Edit(./strategies/generated/**)", "Bash(python -X utf8 -m sajim_quant.lab.cli *)",
                 "Read(./sajim_quant/lab/**)", "Read(./strategies/generated/**)",
                 "Skill(lab-build)", "Skill(lab-review)"):
        assert rule in allow, rule
    for rule in ("Read(./data/**)", "Read(**/.env*)", "Read(./sim/**)", "Read(~/.sajim/**)",
                 "Edit(./sajim_quant/**)", "Edit(./.claude/**)", "Edit(./sim/**)", "Edit(./data/**)",
                 "NotebookEdit", "WebFetch", "WebSearch", "Bash(modal *)", "Bash(git *)",
                 "Bash(curl *)", "Bash(powershell *)", "Bash(pip *)"):
        assert rule in deny, rule


def test_settings_hooks_are_wired_and_quoted():
    s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    want = {
        ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit"): "guard_paths.py",
        ("PreToolUse", "Read|Grep|Glob"): "guard_reads.py",
        ("PreToolUse", "Bash|PowerShell"): "guard_bash.py",
        ("PreToolUse", "Skill"): "guard_skill.py",
        ("PostToolUse", "Edit|Write|MultiEdit"): "lint_on_save.py",
    }
    got = {}
    for event_name, groups in s["hooks"].items():
        for g in groups:
            (h,) = g["hooks"]
            assert h["type"] == "command"
            got[(event_name, g["matcher"])] = h["command"]
    assert set(got) == set(want)
    for key, script in want.items():
        cmd = got[key]
        assert cmd == f'python -X utf8 "${{CLAUDE_PROJECT_DIR}}/.claude/lab/hooks/{script}"'
        assert (HOOKS / script).is_file()
    assert "Stop" not in s["hooks"]


# ── Examples and templates ──────────────────────────────────────────────────
def synthetic_ohlc(n: int = 4000, freq: str = "15min", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2030-01-06", periods=n, freq=freq, name="time")
    hour = idx.hour.to_numpy()
    vol = np.where((hour >= 7) & (hour < 16), 0.0016, 0.0006)        # busier London/NY hours
    trend = 0.00025 * np.sin(np.arange(n) / 400.0)
    close = 100.0 * np.exp(np.cumsum(trend + vol * rng.standard_normal(n)))
    open_ = np.r_[100.0, close[:-1]]
    wick = np.abs(rng.standard_normal((2, n))) * vol * close
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(50, 500, n).astype("float64")}, index=idx)


def load_strategy(path: Path):
    spec = importlib.util.spec_from_file_location(f"lab_example_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assert_contract_output(out: pd.DataFrame, df: pd.DataFrame, allow_short: bool) -> None:
    assert isinstance(out, pd.DataFrame)
    assert out.index.equals(df.index)
    assert out["signal"].dtype == np.int8
    assert out["stop_dist"].dtype == np.float64 and out["tp_dist"].dtype == np.float64
    assert out["exit"].dtype == np.bool_
    sig = out["signal"].to_numpy()
    assert set(np.unique(sig)) <= {-1, 0, 1}
    if not allow_short:
        assert not (sig == -1).any()
    on = sig != 0
    sd, tp = out["stop_dist"].to_numpy()[on], out["tp_dist"].to_numpy()[on]
    assert np.isfinite(sd).all() and (sd > 0).all()
    assert (np.isnan(tp) | (np.isfinite(tp) & (tp > 0))).all()


def frames_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    for col in ("signal", "exit"):
        if not np.array_equal(a[col].to_numpy(), b[col].to_numpy()):
            return False
    for col in ("stop_dist", "tp_dist"):
        x, y = a[col].to_numpy(), b[col].to_numpy()
        same = (np.isnan(x) & np.isnan(y)) | (np.abs(x - y) <= gates.CAUSALITY_ATOL)
        if not same.all():
            return False
    return True


@pytest.mark.parametrize("name", list(EXAMPLES) + ["template"])
def test_examples_lint_clean(name):
    path = BUILD / ("templates/strategy.py" if name == "template" else f"examples/{name}.py")
    src = path.read_bytes()
    assert contract.lint_source(src) == []
    spec = contract.parse_params(src)
    assert gates.MIN_PARAMS <= len(spec) <= gates.MAX_PARAMS
    for text in (src.decode("utf-8"),):
        assert not gates.contains_forbidden_label(text)


@pytest.mark.parametrize("name", list(EXAMPLES) + ["template"])
def test_examples_follow_the_output_contract_and_are_causal(name):
    path = BUILD / ("templates/strategy.py" if name == "template" else f"examples/{name}.py")
    allow_short = EXAMPLES.get(name, {"allow_short": False})["allow_short"]
    mod = load_strategy(path)
    params = contract.default_params(contract.parse_params(path.read_bytes()))
    freq = "1h" if name == "ema_pullback" else "15min"
    df = synthetic_ohlc(freq=freq)
    full = mod.generate_signals(df.copy(), dict(params))
    assert_contract_output(full, df, allow_short)
    assert int((full["signal"] != 0).sum()) >= 3, "the example fires on synthetic data"
    again = mod.generate_signals(df.copy(), dict(params))
    assert frames_equal(full, again), "deterministic"
    for frac in gates.CAUSALITY_CUTS:
        cut = max(1, int(frac * len(df)))
        part = mod.generate_signals(df.iloc[:cut].copy(), dict(params))
        assert_contract_output(part, df.iloc[:cut], allow_short)
        assert frames_equal(part, full.iloc[:cut]), f"{name}: outputs before the {frac:.0%} cut changed"


def test_examples_pass_the_integrity_check_in_the_local_sandbox():
    """End to end through B's runner + decoder + causality (the same check /lab/check runs)."""
    causality = pytest.importorskip("sajim_quant.lab.causality")
    sandbox_io = pytest.importorskip("sajim_quant.lab.sandbox_io")
    sandbox_local = pytest.importorskip("sajim_quant.lab.sandbox_local")
    from concurrent.futures import ThreadPoolExecutor

    def one(name: str):
        src = (BUILD / "examples" / f"{name}.py").read_bytes()
        df = synthetic_ohlc(n=3000, freq="1h" if name == "ema_pullback" else "15min")
        params = contract.default_params(contract.parse_params(src))
        plan = causality.build_check_plan(len(df), params, interfaces.sha256_hex(src))
        job = interfaces.SandboxJob(
            frame_parquet=sandbox_io.frame_to_parquet(df), n_bars=len(df), strategy_py=src,
            indicators_py=sandbox_io.indicators_source(), runs=plan,
            allow_short=EXAMPLES[name]["allow_short"], label="check",
            per_run_timeout_s=float(gates.MAX_SIGNAL_RUNTIME_S))   # laptop: 3 jobs in parallel (p1.2 limit is 14 s)
        decoded = sandbox_io.decode_result(job, sandbox_local.LocalSubprocessExecutor().run(job))
        return causality.assess_check(plan, decoded, len(df))

    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = dict(zip(EXAMPLES, pool.map(one, EXAMPLES)))
    for name, report in reports.items():
        assert report.passed, (name, report.reasons)
        assert report.n_signals >= gates.MIN_SIGNALS


def test_template_manifest_validates_once_filled():
    m = json.loads((BUILD / "templates" / "manifest.json").read_text(encoding="utf-8"))
    assert list(m) == list(contract.MANIFEST_KEYS)
    assert m["spec_sha256"] == "" and m["code_sha256"] == ""
    assert contract.validate_manifest(m, require_shas=False) == []
    src = (BUILD / "examples" / "london_breakout.py").read_bytes()
    stamped = dict(m, code_sha256=interfaces.sha256_hex(src), spec_sha256=interfaces.sha256_hex(b"spec"))
    assert contract.validate_manifest(stamped, strategy_py=src, spec_md=b"spec") == []


def test_cost_reference_matches_instruments():
    from sajim_quant.core.instruments import get_spec

    text = (BUILD / "reference" / "costs.md").read_text(encoding="utf-8")
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 8 and cells[0] in splits.ALLOWED_SYMBOLS:
            rows[cells[0]] = cells
    assert set(rows) == set(splits.ALLOWED_SYMBOLS)
    for sym, (_, cls, ccy, point, pip, bps, slip, slip2) in rows.items():
        s = get_spec(sym)
        assert (cls, ccy) == (s.asset_class, s.quote_currency), sym
        assert float(point) == pytest.approx(s.point) and float(pip) == pytest.approx(s.pip)
        assert float(bps) == pytest.approx(s.round_trip_bps), sym
        assert float(slip) == pytest.approx(s.slippage_points), sym
        assert float(slip2) == pytest.approx(2 * s.slippage_points * s.point), sym
    for sym, tf in interfaces.SHORT_COVERAGE_REFUSED:
        assert f"{sym} " in text and tf in text


def test_indicator_reference_lists_every_public_indicator():
    from sajim_quant.lab import indicators

    text = (BUILD / "reference" / "indicators.md").read_text(encoding="utf-8")
    public = [n for n in dir(indicators) if not n.startswith("_") and callable(getattr(indicators, n))
              and getattr(getattr(indicators, n), "__module__", "") == indicators.__name__]
    assert public
    for name in public:
        assert f"ind.{name}(" in text, name
    assert f'INDICATORS_VERSION = "{indicators.INDICATORS_VERSION}"' in text


def test_claude_md_has_a_strategy_lab_section():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "\n## Strategy Lab\n" in text
    section = text.split("\n## Strategy Lab\n", 1)[1]
    for phrase in ("python -X utf8 -m sajim_quant.lab.run", "CONTRACT.md", "SignalSimulator", "ELITE"):
        assert phrase in section, phrase
