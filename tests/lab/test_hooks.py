"""
.claude/lab/hooks/*.py: the Strategy Lab PreToolUse guards and the PostToolUse lint.

Each case pipes a hook event (P0-observed field names, Windows backslash paths) into
the hook with subprocess, exactly as Claude Code does. The hooks are copied into a temp
repo whose path contains a space ("SAJIM REPO"), and CLAUDE_PROJECT_DIR is passed with
forward slashes, as Claude Code sends it on Windows. A few smoke cases run the hooks
in place in the real repo. Nothing here starts `claude`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sajim_quant.lab import gates, interfaces

sys.dont_write_bytecode = True        # no __pycache__ in .claude/lab/hooks or .claude/skills

REPO = Path(__file__).resolve().parents[2]
HOOKS_SRC = REPO / ".claude" / "lab" / "hooks"
LAB = "lab_hooktest_xauusd"
OTHER = "lab_other_eurusd"
BURNED = "lab_burned_xauusd"            # has a holdout scorecard
SHA12 = "0123456789ab"
SHA64 = "0123456789abcdef" * 4

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")

CLEAN_STRATEGY = (REPO / ".claude/skills/lab-build/examples/zscore_reversion.py").read_text(encoding="utf-8")
LOOKAHEAD_STRATEGY = CLEAN_STRATEGY.replace("z_prev = z.shift(1)", "z_prev = z.shift(-1)")


# ── Fixtures ────────────────────────────────────────────────────────────────
def _make_repo(base: Path, *, with_engine: bool) -> Path:
    root = base / "SAJIM REPO"
    shutil.copytree(HOOKS_SRC, root / ".claude" / "lab" / "hooks",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if with_engine:
        shutil.copytree(REPO / "sajim_quant", root / "sajim_quant",
                        ignore=shutil.ignore_patterns("__pycache__"))
    else:
        (root / "sajim_quant" / "lab").mkdir(parents=True)
        (root / "sajim_quant" / "lab" / "CONTRACT.md").write_text("contract\n", encoding="utf-8")
    for d in (".claude/skills/lab-build/reference", ".claude/skills/lab-review", ".claude/agents",
              "data/parquet/XAUUSD", "sim", "docs", "strategies/BEEP_MASTER_PACKAGE",
              f"strategies/generated/{LAB}/scorecards", f"strategies/generated/{OTHER}",
              f"strategies/generated/{BURNED}/scorecards"):
        (root / d).mkdir(parents=True, exist_ok=True)
    files = {
        ".claude/skills/lab-build/SKILL.md": "skill",
        ".claude/skills/lab-build/reference/contract.md": "ref",
        ".claude/agents/strategy-critic.md": "agent",
        ".env": "SECRET=1",
        "sim/.env.local": "SAJIM_API_TOKEN=x",
        "data/parquet/XAUUSD/M5.parquet": "bars",
        "docs/STRATEGY_LAB_PLAN.md": "plan",
        f"strategies/generated/{LAB}/request.json": "{}",
        f"strategies/generated/{LAB}/scorecards/dev_{SHA12}.json": "{}",
        f"strategies/generated/{OTHER}/spec.md": "other",
        f"strategies/generated/{BURNED}/scorecards/dev_{SHA12}.json": "{}",
        f"strategies/generated/{BURNED}/scorecards/holdout_{SHA12}.json": "{}",
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    return _make_repo(tmp_path_factory.mktemp("hooks"), with_engine=True)


@pytest.fixture(scope="module")
def home(tmp_path_factory) -> Path:
    h = tmp_path_factory.mktemp("home")
    (h / ".sajim").mkdir()
    (h / ".sajim" / "judge_token").write_text("judge", encoding="utf-8")
    (h / ".claude").mkdir()
    return h


def fwd(p: Path) -> str:
    return str(p).replace("\\", "/")


def run_hook(root: Path, hook: str, event, *, home: Path, stage: str | None = "code",
             lab_id: str | None = LAB, project_dir: str | None = "default", raw: bytes | None = None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SAJIM_", "CLAUDE_", "ANTHROPIC_")) and k != "PYTHONPATH"}
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    if lab_id is not None:
        env["SAJIM_LAB_ID"] = lab_id
    if stage is not None:
        env["SAJIM_LAB_STAGE"] = stage
    if project_dir == "default":
        env["CLAUDE_PROJECT_DIR"] = fwd(root)
    elif project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    data = raw if raw is not None else json.dumps(event).encode("utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(root / ".claude/lab/hooks" / hook)],
                          input=data, capture_output=True, env=env, cwd=str(root), timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def decision(rc: int, out: str, err: str) -> str:
    assert rc == 0, f"PreToolUse hooks always exit 0 (rc={rc}, stderr={err!r})"
    assert '"allow"' not in out, "a lab hook must never emit allow"
    if not out.strip():
        return "allow"
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    hso = json.loads(lines[0])["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"].startswith("Strategy Lab: ")
    # every deny in these tests is a deliberate rule, never a crashed hook failing closed
    assert "internal hook error" not in hso["permissionDecisionReason"], hso["permissionDecisionReason"]
    return "deny"


def event(root: Path, tool: str, tool_input: dict, *, hook_event: str = "PreToolUse") -> dict:
    return {"session_id": "s-1", "transcript_path": "C:\\Users\\x\\t.jsonl", "cwd": str(root),
            "permission_mode": "dontAsk", "hook_event_name": hook_event,
            "tool_name": tool, "tool_input": tool_input}


def lab_path(root: Path, name: str, lab: str = LAB) -> str:
    return str(root / "strategies" / "generated" / lab / name)


# ── Pinned constants ────────────────────────────────────────────────────────
def _load_common():
    spec = importlib.util.spec_from_file_location("lab_hooks_common", HOOKS_SRC / "_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_common_constants_match_the_frozen_interfaces():
    common = _load_common()
    assert common.LAB_ID_RE.pattern == gates.LAB_ID_PATTERN
    assert dict(common.LAB_WRITABLE) == dict(interfaces.LAB_WRITABLE)
    assert "/".join(common.GENERATED_PARTS) == interfaces.GENERATED_DIR


# ── guard_paths ─────────────────────────────────────────────────────────────
def paths(repo, home, file_path, *, tool="Edit", stage="code", **kw):
    ev = event(repo, tool, {"file_path": file_path, "old_string": "a", "new_string": "b"})
    return decision(*run_hook(repo, "guard_paths.py", ev, home=home, stage=stage, **kw))


@pytest.mark.parametrize("stage,name", [
    ("spec", "spec.md"), ("code", "strategy.py"), ("code", "manifest.json"), ("review", "review.json"),
])
def test_paths_allows_stage_files(repo, home, stage, name):
    assert paths(repo, home, lab_path(repo, name), stage=stage) == "allow"
    assert paths(repo, home, lab_path(repo, name), stage=stage, tool="Write") == "allow"



# code stage: spec.md is the approved pre-registration; only section 8 (Trials) may change
APPROVED_SPEC = (
    "# Breakout - XAUUSD M15\r\n\r\n## 1. Description\r\n> buy the London break\r\n\r\n"
    "## 2. Rules\r\n1. Long on the first close above the Asian high.\r\n\r\n"
    "## 3. PARAMS\r\n| name | default | min | max | why |\r\n|---|---|---|---|---|\r\n"
    "| rr | 2.0 | 1.0 | 3.0 | standard |\r\n\r\n## 8. Trials\r\n(none yet)\r\n"
)


@pytest.fixture()
def approved_spec(repo):
    spec = Path(lab_path(repo, "spec.md"))
    spec.write_bytes(APPROVED_SPEC.encode("utf-8"))
    yield spec
    spec.unlink()


def spec_write(repo, home, tool, tool_input, *, stage="code"):
    ev = event(repo, tool, dict(tool_input, file_path=lab_path(repo, "spec.md")))
    return decision(*run_hook(repo, "guard_paths.py", ev, home=home, stage=stage))


def test_code_stage_may_log_trials_in_spec(repo, home, approved_spec):
    trial = "## 8. Trials\nTrial 2: bug-fix - rule 1 compared the open, not the close\n"
    assert spec_write(repo, home, "Edit", {"old_string": "## 8. Trials\n(none yet)", "new_string": trial}) == "allow"
    assert spec_write(repo, home, "Edit", {"old_string": "(none yet)", "new_string": "Trial 2: bug-fix"}) == "allow"
    assert spec_write(repo, home, "MultiEdit", {"edits": [
        {"old_string": "(none yet)", "new_string": "Trial 2: spec-fix - rule 1 hour"},
        {"old_string": "Trial 2: spec-fix - rule 1 hour",
         "new_string": "Trial 2: spec-fix - rule 1 hour\nTrial 3: bug-fix"},
    ]}) == "allow"
    same_head = APPROVED_SPEC.replace("\r\n", "\n").replace("(none yet)", "Trial 2: bug-fix - NaN warmup")
    assert spec_write(repo, home, "Write", {"content": same_head}) == "allow"


@pytest.mark.parametrize("tool,tool_input", [
    ("Edit", {"old_string": "| rr | 2.0 | 1.0 | 3.0 |", "new_string": "| rr | 2.6 | 1.0 | 3.0 |"}),
    ("Edit", {"old_string": "first close above the Asian high",
              "new_string": "first close above the Asian high when ATR is rising"}),
    ("Edit", {"old_string": "## 8. Trials", "new_string": "## 2b. Extra rule\n2. Skip Mondays.\n\n## 8. Trials"}),
    ("Edit", {"old_string": "## 8. Trials\r\n(none yet)", "new_string": "Trial 2: bug-fix"}),   # drops the heading
    ("Edit", {"old_string": "r", "new_string": "R", "replace_all": True}),
    ("MultiEdit", {"edits": [{"old_string": "(none yet)", "new_string": "Trial 2: bug-fix"},
                             {"old_string": "rr | 2.0", "new_string": "rr | 2.4"}]}),
    ("Write", {"content": "# Breakout - XAUUSD M15\n\n## 8. Trials\nTrial 2: bug-fix\n"}),
    ("Write", {"content": APPROVED_SPEC.replace("2.0 | 1.0", "2.5 | 1.0")}),
    ("Edit", {"old_string": "not in the file", "new_string": "x"}),
    ("Edit", {"old_string": "", "new_string": "x"}),
    ("Write", {}),
])
def test_code_stage_may_not_rewrite_the_approved_spec(repo, home, approved_spec, tool, tool_input):
    assert spec_write(repo, home, tool, tool_input) == "deny"
    assert approved_spec.read_bytes() == APPROVED_SPEC.encode("utf-8")


def test_spec_stage_may_rewrite_spec_freely(repo, home, approved_spec):
    assert spec_write(repo, home, "Write", {"content": "# new spec\n"}, stage="spec") == "allow"
    assert spec_write(repo, home, "Edit", {"old_string": "rr | 2.0", "new_string": "rr | 2.5"},
                      stage="spec") == "allow"


def test_code_stage_spec_without_trials_heading_is_append_only(repo, home):
    spec = Path(lab_path(repo, "spec.md"))
    spec.write_text("# spec\n## 2. Rules\n1. buy\n", encoding="utf-8")
    try:
        grown = "# spec\n## 2. Rules\n1. buy\n## 8. Trials\nTrial 2: bug-fix\n"
        assert spec_write(repo, home, "Write", {"content": grown}) == "allow"
        assert spec_write(repo, home, "Write", {"content": "# spec\n## 2. Rules\n1. sell\n"}) == "deny"
    finally:
        spec.unlink()


def test_code_stage_spec_must_exist(repo, home):
    assert not Path(lab_path(repo, "spec.md")).exists()
    assert spec_write(repo, home, "Write", {"content": "# spec\n"}) == "deny"


@pytest.mark.parametrize("stage,name", [
    ("spec", "strategy.py"), ("spec", "manifest.json"), ("review", "spec.md"), ("review", "strategy.py"),
    ("code", "review.json"), ("code", "request.json"), ("code", "build_log.json"), ("code", "README.md"),
    ("code", f"scorecards\\dev_{SHA12}.json"), ("code", "sub\\strategy.py"),
])
def test_paths_denies_other_files_in_the_folder(repo, home, stage, name):
    assert paths(repo, home, lab_path(repo, name), stage=stage) == "deny"


def test_paths_relative_forward_slash_and_multiedit(repo, home):
    assert paths(repo, home, f"strategies\\generated\\{LAB}\\strategy.py") == "allow"
    assert paths(repo, home, f"strategies/generated/{LAB}/manifest.json") == "allow"
    assert paths(repo, home, fwd(Path(lab_path(repo, "strategy.py")))) == "allow"
    assert paths(repo, home, lab_path(repo, "strategy.py"), tool="MultiEdit") == "allow"


@windows_only
def test_paths_case_variants_resolve_to_the_same_folder(repo, home):
    upper = str(repo).upper() + f"\\STRATEGIES\\GENERATED\\{LAB.upper()}\\strategy.py"
    assert paths(repo, home, upper) == "allow"
    # spec.md does not exist yet, so a different spelling would create a new, wrong name
    assert not Path(lab_path(repo, "spec.md")).exists()
    assert paths(repo, home, lab_path(repo, "SPEC.MD")) == "deny"


@pytest.mark.parametrize("target", [
    f"strategies\\generated\\{LAB}\\..\\{OTHER}\\spec.md",
    f"strategies\\generated\\{LAB}\\..\\..\\..\\sajim_quant\\lab\\gates.py",
    f"strategies/generated/{LAB}/../../../.claude/lab/settings.json",
    f"strategies\\generated\\{OTHER}\\strategy.py",
    "sajim_quant\\lab\\gates.py",
    ".claude\\lab\\hooks\\guard_paths.py",
    "CLAUDE.md",
    "sim\\.env.local",
    "data\\parquet\\XAUUSD\\M5.parquet",
    "~/.sajim/judge_token",
    "~\\.claude\\settings.json",
])
def test_paths_denies_escapes(repo, home, target):
    assert paths(repo, home, target) == "deny"


@windows_only
def test_paths_denies_windows_path_tricks(repo, home):
    good = lab_path(repo, "strategy.py")
    drive = Path(good).drive                              # e.g. "C:"
    rel_to_drive_root = good[len(drive):]                 # "\\Users\\...\\strategy.py"
    for target in (
        drive + f"strategies\\generated\\{LAB}\\strategy.py",    # drive-relative
        rel_to_drive_root,                                      # root-relative
        "\\\\?\\" + good,                                       # device path
        "\\\\.\\" + good,
        "\\\\localhost\\c$\\x\\strategy.py",                   # UNC
        good + ":evil",                                         # alternate data stream
        lab_path(repo, "strategy.py:hidden"),
        lab_path(repo, "spec.md."),                             # trailing dot
        good + " ",                                             # trailing space
        " " + good,
        good.replace("strategy.py", "strategy.py\x00.md"),      # NUL
        "",
    ):
        assert paths(repo, home, target) == "deny", target


def test_paths_denies_without_lab_environment(repo, home):
    good = lab_path(repo, "strategy.py")
    assert paths(repo, home, good, lab_id=None) == "deny"
    assert paths(repo, home, good, stage=None) == "deny"
    assert paths(repo, home, good, stage="build") == "deny"
    assert paths(repo, home, good, lab_id="lab_x/../..") == "deny"
    assert paths(repo, home, good, lab_id="LAB_HOOKTEST") == "deny"


def test_paths_denies_notebookedit_and_other_tools(repo, home):
    nb = event(repo, "NotebookEdit", {"notebook_path": lab_path(repo, "strategy.py"), "new_source": "x"})
    assert decision(*run_hook(repo, "guard_paths.py", nb, home=home)) == "deny"
    bash = event(repo, "Bash", {"command": "echo"})
    assert decision(*run_hook(repo, "guard_paths.py", bash, home=home)) == "deny"
    wrong_event = event(repo, "Write", {"file_path": lab_path(repo, "strategy.py")}, hook_event="PostToolUse")
    assert decision(*run_hook(repo, "guard_paths.py", wrong_event, home=home)) == "deny"


def test_paths_project_dir_must_match_the_hooks_repo(repo, home, tmp_path):
    good = lab_path(repo, "strategy.py")
    assert paths(repo, home, good, project_dir=fwd(tmp_path)) == "deny"
    assert paths(repo, home, good, project_dir=None) == "allow"           # unset: hook's own repo
    assert paths(repo, home, good, project_dir=str(repo)) == "allow"      # backslashes are fine too


@pytest.mark.parametrize("hook", ["guard_paths.py", "guard_reads.py", "guard_bash.py", "guard_skill.py"])
@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2]", b'{"tool_name": "Edit"}',
                                 b'{"tool_input": {}}', b"\xff\xfe\x00garbage",
                                 b'{"tool_name": "Edit", "tool_input": "x"}'])
def test_malformed_events_are_denied(repo, home, hook, raw):
    assert decision(*run_hook(repo, hook, None, home=home, raw=raw)) == "deny"


@pytest.mark.parametrize("hook", ["guard_paths.py", "guard_reads.py", "guard_bash.py", "guard_skill.py"])
def test_guards_block_with_exit_2_when_their_helpers_are_missing(tmp_path, home, hook):
    """A crashing hook (exit 1) would be a NON-blocking error in Claude Code; exit 2 blocks."""
    bare = _make_repo(tmp_path, with_engine=False)
    (bare / ".claude/lab/hooks/_common.py").unlink()
    ev = event(bare, "Bash" if hook == "guard_bash.py" else "Read",
               {"command": f"{CLI} check", "file_path": lab_path(bare, "request.json")})
    rc, out, err = run_hook(bare, hook, ev, home=home)
    assert rc == 2 and out == "" and "denied" in err


# ── guard_reads ─────────────────────────────────────────────────────────────
def reads(repo, home, tool, tool_input, *, lab_id=LAB, **kw):
    ev = event(repo, tool, tool_input)
    return decision(*run_hook(repo, "guard_reads.py", ev, home=home, lab_id=lab_id, **kw))


@pytest.mark.parametrize("rel", [
    f"strategies\\generated\\{LAB}\\request.json",
    f"strategies\\generated\\{LAB}\\strategy.py",
    f"strategies\\generated\\{LAB}\\scorecards\\dev_{SHA12}.json",
    "sajim_quant\\lab\\CONTRACT.md",
    ".claude\\skills\\lab-build\\SKILL.md",
    ".claude\\skills\\lab-build\\reference\\contract.md",
    ".claude\\skills\\lab-review\\SKILL.md",
    ".claude\\agents\\strategy-critic.md",
])
def test_reads_allows_the_lab_scope(repo, home, rel):
    assert reads(repo, home, "Read", {"file_path": str(repo / rel)}) == "allow"
    assert reads(repo, home, "Read", {"file_path": rel}) == "allow"           # relative to cwd


def test_reads_allows_scoped_searches(repo, home):
    assert reads(repo, home, "Grep", {"pattern": "PARAMS", "path": lab_path(repo, "")}) == "allow"
    assert reads(repo, home, "Grep", {"pattern": "def ", "path": "sajim_quant/lab", "glob": "*.py"}) == "allow"
    assert reads(repo, home, "Glob", {"pattern": "**/*.md", "path": str(repo / ".claude/skills/lab-build")}) == "allow"
    assert reads(repo, home, "Glob", {"pattern": "*.json", "path": lab_path(repo, "scorecards")}) == "allow"


@pytest.mark.parametrize("target", [
    ".env", "sajim_quant\\lab\\.env", "sim\\.env.local", "SIM\\.ENV.LOCAL", f"strategies\\generated\\{LAB}\\.env",
    "data\\parquet\\XAUUSD\\M5.parquet", "data/parquet/XAUUSD/M5.parquet", "DATA\\PARQUET\\XAUUSD\\M5.parquet",
    "data", "sim", "docs\\STRATEGY_LAB_PLAN.md", "CLAUDE.md", "sajim_quant\\core\\instruments.py",
    ".claude\\lab\\settings.json", ".claude\\skills\\backtest\\SKILL.md",
    f"strategies\\generated\\{OTHER}\\spec.md", "strategies\\generated", "strategies\\BEEP_MASTER_PACKAGE",
    f"strategies\\generated\\{LAB}\\..\\{OTHER}\\spec.md",
    f"sajim_quant\\lab\\..\\..\\data\\parquet\\XAUUSD\\M5.parquet",
    f"strategies\\generated\\{LAB}\\scorecards\\holdout_{SHA12}.json",
    f"strategies\\generated\\{LAB}\\SCORECARDS\\HOLDOUT_{SHA12}.JSON",
    f"strategies\\generated\\{LAB}\\README.md",
    "~/.sajim/judge_token", "~\\.sajim\\lab_token", "~/.claude/.credentials.json",
])
def test_reads_denies_secrets_data_and_other_folders(repo, home, target):
    assert reads(repo, home, "Read", {"file_path": target}) == "deny"


def test_reads_denies_judge_token_by_absolute_path(repo, home):
    for p in (home / ".sajim" / "judge_token", Path(fwd(home / ".sajim" / "judge_token"))):
        assert reads(repo, home, "Read", {"file_path": str(p)}) == "deny"
    assert reads(repo, home, "Grep", {"pattern": "x", "path": str(home / ".sajim")}) == "deny"
    assert reads(repo, home, "Glob", {"pattern": "*", "path": str(home)}) == "deny"


def test_reads_denies_unscoped_or_escaping_searches(repo, home):
    assert reads(repo, home, "Grep", {"pattern": "TOKEN"}) == "deny"                   # repo root
    assert reads(repo, home, "Glob", {"pattern": "**/.env*"}) == "deny"
    assert reads(repo, home, "Grep", {"pattern": "x", "path": ""}) == "deny"
    assert reads(repo, home, "Glob", {"pattern": "../../data/**", "path": lab_path(repo, "")}) == "deny"
    assert reads(repo, home, "Glob", {"pattern": fwd(repo) + "/data/**", "path": "sajim_quant/lab"}) == "deny"
    assert reads(repo, home, "Glob", {"pattern": "/etc/*", "path": "sajim_quant/lab"}) == "deny"
    assert reads(repo, home, "Glob", {"pattern": "~/.sajim/*", "path": "sajim_quant/lab"}) == "deny"
    assert reads(repo, home, "Grep", {"pattern": "x", "path": "sajim_quant/lab", "glob": "..\\..\\*"}) == "deny"


def test_reads_hides_folders_once_a_holdout_scorecard_exists(repo, home):
    burned = lambda name: lab_path(repo, name, BURNED)  # noqa: E731
    assert reads(repo, home, "Read", {"file_path": burned(f"scorecards\\dev_{SHA12}.json")}, lab_id=BURNED) == "allow"
    assert reads(repo, home, "Read", {"file_path": burned(f"scorecards\\holdout_{SHA12}.json")}, lab_id=BURNED) == "deny"
    assert reads(repo, home, "Grep", {"pattern": "net", "path": burned("")}, lab_id=BURNED) == "deny"
    assert reads(repo, home, "Grep", {"pattern": "net", "path": burned("scorecards")}, lab_id=BURNED) == "deny"
    assert reads(repo, home, "Glob", {"pattern": "**/*", "path": burned("")}, lab_id=BURNED) == "deny"
    # the same searches are fine in a folder without a holdout scorecard
    assert reads(repo, home, "Grep", {"pattern": "net", "path": lab_path(repo, "scorecards")}) == "allow"



def test_reads_denies_anchored_brace_alternatives(repo, home):
    """A brace alternative is a pattern start too: '{C:/Users/**,x}' must not pass as relative."""
    lab = "sajim_quant/lab"
    for pattern in ("{C:/Users/**,x}", r"{x,F:\Tete\**}", "{/c/Users/**,x}", "{x, /etc/*}",
                    r"{\\localhost\c$\**,x}", "{~/.sajim/*,x}", "x/{a,~/.claude/*}"):
        assert reads(repo, home, "Glob", {"pattern": pattern, "path": lab}) == "deny", pattern
        assert reads(repo, home, "Grep", {"pattern": "x", "path": lab, "glob": pattern}) == "deny", pattern
    # ordinary relative alternatives stay allowed
    assert reads(repo, home, "Glob", {"pattern": "**/*.{py,md}", "path": lab}) == "allow"
    assert reads(repo, home, "Grep", {"pattern": "x", "path": lab, "glob": "{gates,splits}.py"}) == "allow"

@windows_only
def test_reads_denies_windows_path_tricks(repo, home):
    good = lab_path(repo, "request.json")
    drive = Path(good).drive
    for target in (drive + f"strategies\\generated\\{LAB}\\request.json", good[len(drive):],
                   "\\\\?\\" + good, "\\\\localhost\\c$\\x", good + ":stream"):
        assert reads(repo, home, "Read", {"file_path": target}) == "deny", target


def test_reads_denies_without_lab_id_and_other_tools(repo, home):
    assert reads(repo, home, "Read", {"file_path": lab_path(repo, "request.json")}, lab_id=None) == "deny"
    assert reads(repo, home, "Write", {"file_path": lab_path(repo, "request.json")}) == "deny"
    assert reads(repo, home, "Read", {"file_path": lab_path(repo, "request.json")},
                 project_dir=fwd(repo.parent)) == "deny"


# ── guard_bash ──────────────────────────────────────────────────────────────
CLI = "python -X utf8 -m sajim_quant.lab.cli"


def bash(repo, home, command, *, stage="code", tool="Bash", **kw):
    ev = event(repo, tool, {"command": command, "description": "lab"})
    return decision(*run_hook(repo, "guard_bash.py", ev, home=home, stage=stage, **kw))


@pytest.mark.parametrize("stage,sub", [
    ("code", "check"), ("code", "dev"), ("code", "status"), ("code", "summary"),
    ("code", f"summary --sha {SHA12}"), ("code", f"done --final {SHA12}"), ("code", f"done --final {SHA64}"),
    ("review", "summary"), ("review", f"summary --sha {SHA12}"), ("review", "review --file review.json"),
])
def test_bash_allows_exact_stage_commands(repo, home, stage, sub):
    assert bash(repo, home, f"{CLI} {sub}", stage=stage) == "allow"
    assert bash(repo, home, f"  {CLI} {sub}  ", stage=stage) == "allow"


@pytest.mark.parametrize("stage,sub", [
    ("spec", "check"), ("spec", "summary"), ("review", "check"), ("review", "dev"),
    ("review", f"done --final {SHA12}"), ("code", "review --file review.json"),
])
def test_bash_denies_commands_of_other_stages(repo, home, stage, sub):
    assert bash(repo, home, f"{CLI} {sub}", stage=stage) == "deny"


@pytest.mark.parametrize("command", [
    "modal deploy sajim_quant/cloud/modal_runner.py",
    "python -X utf8 -m modal deploy sajim_quant/cloud/modal_runner.py",
    f"{CLI} check; whoami",
    f"{CLI} check && whoami",
    f"{CLI} check & whoami",
    f"{CLI} check | cat",
    f"{CLI} summary > out.txt",
    f"{CLI} summary < in.txt",
    f"{CLI} summary --sha `whoami`",
    f"{CLI} summary --sha $(whoami)",
    f"{CLI} summary --sha $SAJIM_LAB_TOKEN",
    f"{CLI} check\nwhoami",
    f"{CLI} check\r\nwhoami",
    f"{CLI} check --verbose",
    f"{CLI}  check",
    f"{CLI} done --final XYZ",
    f"{CLI} done --final {SHA12.upper()}",
    f"{CLI} done --final {SHA12}0",
    f"{CLI} review --file ../review.json",
    f"{CLI} review --file -",
    "python -m sajim_quant.lab.cli check",
    "python3 -X utf8 -m sajim_quant.lab.cli check",
    "SAJIM_LAB_ID=lab_other_eurusd python -X utf8 -m sajim_quant.lab.cli check",
    "cd .. && python -X utf8 -m sajim_quant.lab.cli check",
    "cat ~/.sajim/judge_token",
    "type %USERPROFILE%\\.sajim\\judge_token",
    "git status",
    "curl https://example.com",
    "powershell -c Get-ChildItem",
    "python -c print(1)",
    "",
    "   ",
])
def test_bash_denies_everything_else(repo, home, command):
    assert bash(repo, home, command) == "deny"


def test_bash_denies_powershell_and_missing_stage(repo, home):
    assert bash(repo, home, f"{CLI} check", tool="PowerShell") == "deny"
    assert bash(repo, home, "Get-Content $env:USERPROFILE\\.sajim\\judge_token", tool="PowerShell") == "deny"
    assert bash(repo, home, f"{CLI} check", stage=None) == "deny"
    assert bash(repo, home, f"{CLI} check", stage="admin") == "deny"
    ev = event(repo, "Bash", {"description": "no command"})
    assert decision(*run_hook(repo, "guard_bash.py", ev, home=home)) == "deny"



# ── guard_skill ─────────────────────────────────────────────────────────────
def skill(repo, home, name, *, tool="Skill", **kw):
    ev = event(repo, tool, {} if name is None else {"skill": name, "args": "lab_x"})
    return decision(*run_hook(repo, "guard_skill.py", ev, home=home, **kw))


@pytest.mark.parametrize("name", ["lab-build", "lab-review", "/lab-build"])
def test_skill_allows_the_lab_skills(repo, home, name):
    assert skill(repo, home, name) == "allow"
    assert skill(repo, home, name, stage="review") == "allow"


@pytest.mark.parametrize("name", ["backtest", "discord:configure", "commit-commands:commit",
                                  "update-config", "lab-build-x", "", None])
def test_skill_denies_every_other_skill(repo, home, name):
    assert skill(repo, home, name) == "deny"


def test_skill_denies_without_lab_stage_and_other_tools(repo, home):
    assert skill(repo, home, "lab-build", stage=None) == "deny"
    assert skill(repo, home, "lab-build", tool="Bash") == "deny"

# ── lint_on_save (PostToolUse) ──────────────────────────────────────────────
def lint(root, home, name, content: str | None, *, tool="Write", lab=LAB, lab_id=LAB):
    target = Path(lab_path(root, name, lab))
    if content is not None:
        target.write_text(content, encoding="utf-8")
    ev = event(root, tool, {"file_path": str(target), "content": content or ""}, hook_event="PostToolUse")
    ev["tool_response"] = {"filePath": str(target), "success": True}
    return run_hook(root, "lint_on_save.py", ev, home=home, lab_id=lab_id)


def _manifest(**over) -> str:
    m = json.loads((REPO / ".claude/skills/lab-build/templates/manifest.json").read_text(encoding="utf-8"))
    m.update(id=LAB, family=LAB)
    m.update(over)
    return json.dumps(m, indent=2)


def test_lint_clean_strategy_exits_0_silently(repo, home):
    rc, out, err = lint(repo, home, "strategy.py", CLEAN_STRATEGY)
    assert (rc, out, err) == (0, "", "")


def test_lint_lookahead_exits_2_with_violations_on_stderr(repo, home):
    rc, out, err = lint(repo, home, "strategy.py", LOOKAHEAD_STRATEGY, tool="Edit")
    assert rc == 2
    lines = err.strip().splitlines()
    assert lines and all(line.startswith("strategy.py:") for line in lines)
    assert any("LOOKAHEAD" in line and "shift(-1)" in line for line in lines)


def test_lint_manifest(repo, home):
    assert lint(repo, home, "manifest.json", _manifest())[0] == 0
    rc, _, err = lint(repo, home, "manifest.json", _manifest(risk_pct=0.02))
    assert rc == 2 and "risk_pct" in err
    rc, _, err = lint(repo, home, "manifest.json", "{not json")
    assert rc == 2 and "invalid JSON" in err


def test_lint_ignores_other_files_tools_and_folders(repo, home):
    assert lint(repo, home, "spec.md", "# spec\n") == (0, "", "")
    other = Path(lab_path(repo, "strategy.py", OTHER))
    other.write_text(LOOKAHEAD_STRATEGY, encoding="utf-8")
    ev = event(repo, "Write", {"file_path": str(other)}, hook_event="PostToolUse")
    assert run_hook(repo, "lint_on_save.py", ev, home=home)[0] == 0
    Path(lab_path(repo, "strategy.py")).write_text(LOOKAHEAD_STRATEGY, encoding="utf-8")
    ev = event(repo, "Read", {"file_path": lab_path(repo, "strategy.py")}, hook_event="PostToolUse")
    assert run_hook(repo, "lint_on_save.py", ev, home=home)[0] == 0


def test_lint_malformed_event_exits_2(repo, home):
    rc, _, err = run_hook(repo, "lint_on_save.py", None, home=home, raw=b"nope")
    assert rc == 2 and "Strategy Lab lint" in err


def test_lint_fails_closed_when_the_linter_is_missing(tmp_path, home):
    bare = _make_repo(tmp_path, with_engine=False)
    rc, _, err = lint(bare, home, "strategy.py", CLEAN_STRATEGY)
    assert rc == 2 and "UNCHECKED" in err


# ── In-place smoke run in the real repository ───────────────────────────────
def test_hooks_work_in_place_in_the_real_repo(home):
    lab = "lab_inplace_smoke_test"
    real_lab = lab_path(REPO, "strategy.py", lab)                 # never created
    ev = event(REPO, "Write", {"file_path": real_lab, "content": "x"})
    assert decision(*run_hook(REPO, "guard_paths.py", ev, home=home, lab_id=lab)) == "allow"
    ev = event(REPO, "Edit", {"file_path": str(REPO / "sajim_quant/lab/gates.py")})
    assert decision(*run_hook(REPO, "guard_paths.py", ev, home=home, lab_id=lab)) == "deny"
    ev = event(REPO, "Read", {"file_path": str(REPO / "sim/.env.local")})
    assert decision(*run_hook(REPO, "guard_reads.py", ev, home=home, lab_id=lab)) == "deny"
    ev = event(REPO, "Bash", {"command": f"{CLI} check"})
    assert decision(*run_hook(REPO, "guard_bash.py", ev, home=home, lab_id=lab)) == "allow"
    assert not (REPO / "strategies" / "generated" / lab).exists()


def test_lab_id_with_a_trailing_newline_is_refused_and_hooks_write_no_bytecode(repo, home):
    """LAB_ID_RE is used with fullmatch ('$' alone would accept 'lab_x\n'); hook runs leave no __pycache__."""
    assert paths(repo, home, lab_path(repo, "strategy.py"), lab_id=LAB + "\n") == "deny"
    assert paths(repo, home, lab_path(repo, "strategy.py")) == "allow"
    assert _load_common().LAB_ID_RE.fullmatch(LAB + "\n") is None
    assert not (repo / ".claude" / "lab" / "hooks" / "__pycache__").exists()
