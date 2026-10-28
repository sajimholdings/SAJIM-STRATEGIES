"""lab.run: env allowlist, claude.exe resolution, preflight, stream-json sessions, and the whole flow (fakes)."""

from __future__ import annotations

import io
import itertools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sajim_quant.lab import cli, gates, run
from sajim_quant.lab import interfaces as I

pytest.importorskip("sajim_quant.lab.contract")    # lazy imports inside run.py (builder A)
pytest.importorskip("sajim_quant.lab.scorecard")   # lazy import for the DEV-only verdict (builder C)

from _laptop_testkit import (  # noqa: E402
    FakeClient, make_lab_folder, make_scorecard, manifest, review, sha_of,
)

LAB = "lab_test_ema_xauusd"
JUDGE = "JUDGE-SECRET-123"
HOSTILE_ENV = {
    "Path": r"C:\Windows", "SYSTEMROOT": r"C:\Windows", "USERPROFILE": r"C:\Users\x", "APPDATA": "a",
    "LOCALAPPDATA": "la", "TEMP": "t", "TMP": "t", "PYTHONUTF8": "0",
    "ANTHROPIC_API_KEY": "sk-ant-xxx", "ANTHROPIC_AUTH_TOKEN": "y", "ANTHROPIC_BASE_URL": "https://evil",
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth", "SAJIM_JUDGE_TOKEN": JUDGE, "SAJIM_API_TOKEN": "dash",
    "SAJIM_MODAL_API_URL": "https://m", "AWS_SECRET_ACCESS_KEY": "aws", "HOMEDRIVE": "C:",
}


# ── Environment ─────────────────────────────────────────────────────────────
def test_child_env_is_an_allowlist():
    env = run.child_env(HOSTILE_ENV, lab_id=LAB, stage="code", lab_token="labtok", api_url="https://lab")
    assert set(env) <= set(I.CHILD_ENV_ALLOWLIST)
    assert env["PATH"] == r"C:\Windows"                       # matched case-insensitively
    assert env["PYTHONUTF8"] == "1" and env["SAJIM_LAB_STAGE"] == "code" and env["SAJIM_LAB_ID"] == LAB
    assert env["SAJIM_LAB_TOKEN"] == "labtok" and env["SAJIM_API_URL"] == "https://lab"
    for name in I.CHILD_ENV_FORBIDDEN + ("ANTHROPIC_BASE_URL", "AWS_SECRET_ACCESS_KEY", "SAJIM_MODAL_API_URL"):
        assert name not in env
    assert JUDGE not in json.dumps(env) and "sk-ant" not in json.dumps(env)
    with pytest.raises(run.PreflightError):
        run.child_env(HOSTILE_ENV, lab_id=LAB, stage="holdout", lab_token="t", api_url="u")
    with pytest.raises(run.PreflightError):
        run.assert_secret_absent({"X": "a" + JUDGE}, JUDGE)


# ── claude.exe resolution ───────────────────────────────────────────────────
def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"MZ")
    return p


def test_claude_cmd_resolves_to_the_npm_claude_exe(tmp_path):
    npm = tmp_path / "npm"
    cmd = _touch(npm / "claude.cmd")
    exe = _touch(npm / run.NODE_CLAUDE_EXE)
    which = {"claude": str(cmd)}.get
    assert run.find_claude_exe({"USERPROFILE": str(tmp_path / "home")}, which=which) == exe


def test_claude_exe_fallbacks_and_shim_refusal(tmp_path):
    npm = tmp_path / "npm"
    cmd = _touch(npm / "claude.cmd")
    home = tmp_path / "home"
    with pytest.raises(run.PreflightError):                          # only a shim exists
        run.find_claude_exe({"USERPROFILE": str(home)}, which={"claude": str(cmd)}.get)
    local = _touch(home / ".local" / "bin" / "claude.exe")
    assert run.find_claude_exe({"USERPROFILE": str(home)}, which={"claude": str(cmd)}.get) == local
    other = _touch(tmp_path / "bin" / "claude.exe")
    assert run.find_claude_exe({"USERPROFILE": str(home)}, which={"claude.exe": str(other)}.get) == other
    assert run.find_claude_exe({"USERPROFILE": str(home), "SAJIM_CLAUDE_EXE": str(other)}, which=lambda n: None) == other
    with pytest.raises(run.PreflightError):
        run.find_claude_exe({"SAJIM_CLAUDE_EXE": str(cmd)}, which=lambda n: None)
    with pytest.raises(run.PreflightError):
        run.run_session([str(cmd), "-p", "x"], {}, tmp_path, popen=lambda *a, **k: pytest.fail("spawned"))


def test_claude_argv_is_the_p0_argv(tmp_path):
    exe, settings = Path(r"C:\c\claude.exe"), tmp_path / ".claude" / "lab" / "settings.json"
    argv = run.claude_argv(exe, run.session_prompt("code", LAB), settings=settings, max_turns=40)
    assert argv == [str(exe), "-p", f"/lab-build {LAB} --stage code", "--settings", str(settings),
                    "--tools", "Read,Edit,Write,Glob,Grep,Bash,Skill", "--strict-mcp-config",
                    "--permission-mode", "dontAsk", "--permission-prompts", "none", "--max-turns", "40",
                    "--output-format", "stream-json", "--verbose"]
    assert run.session_prompt("review", LAB) == f"/lab-review {LAB}"


# ── Preflight ───────────────────────────────────────────────────────────────
class Done:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


def fake_run(version="2.1.296 (Claude Code)", auth=None):
    auth = auth if auth is not None else {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                                          "email": "owner@example.com", "orgId": "org-123"}
    seen = []

    def runner(argv, **kw):
        seen.append((argv, kw.get("env")))
        return Done(version if argv[1] == "--version" else json.dumps(auth))
    return runner, seen


def test_preflight_accepts_the_max_login():
    runner, seen = fake_run()
    env = {"PATH": "x"}
    run.preflight(Path("claude.exe"), env, run=runner)
    assert [a[1:] for a, _ in seen] == [["--version"], ["auth", "status", "--json"]]
    assert all(e == env for _, e in seen)


@pytest.mark.parametrize("auth,needle", [
    ({"loggedIn": True, "authMethod": "api_key", "subscriptionType": None, "email": "owner@example.com"}, "api_key"),
    ({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro", "orgId": "org-123"}, "pro"),
    ({"loggedIn": False, "email": "owner@example.com"}, "not logged in"),
])
def test_preflight_refuses_api_key_and_non_max_auth(auth, needle):
    runner, _ = fake_run(auth=auth)
    with pytest.raises(run.PreflightError) as ei:
        run.preflight(Path("claude.exe"), {}, run=runner)
    msg = str(ei.value)
    assert needle in msg and "owner@example.com" not in msg and "org-123" not in msg


def test_preflight_refuses_old_claude():
    runner, _ = fake_run(version="2.1.258 (Claude Code)")
    with pytest.raises(run.PreflightError, match="older than 2.1.259"):
        run.preflight(Path("claude.exe"), {}, run=runner)


# ── stream-json sessions ────────────────────────────────────────────────────
INIT_OK = {"type": "system", "subtype": "init", "apiKeySource": "none", "mcp_servers": [],
           "tools": ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Skill"]}
ASSIST = {"type": "assistant", "message": {"content": [{"type": "text", "text": "SECRET THOUGHTS"}]}}


def result(subtype="success", is_error=False, turns=7):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": turns,
            "duration_ms": 1234, "total_cost_usd": 0.42}


class BlockingStream:
    def __init__(self):
        self.released = threading.Event()

    def readline(self):
        self.released.wait(10)
        return b""


class FakeProc:
    def __init__(self, events, *, block=False, garbage=True):
        data = b"".join(json.dumps(e).encode() + b"\n" for e in events)
        if garbage:
            data = b"not json\n\n" + data
        self.stdout = BlockingStream() if block else io.BytesIO(data)
        self.stderr = io.BytesIO(b"some stderr\n")
        self.pid, self.returncode, self.killed = 4242, None, False

    def wait(self, timeout=None):
        self.returncode = 1 if self.killed else 0
        return self.returncode

    def kill(self):
        self.killed = True
        if isinstance(self.stdout, BlockingStream):
            self.stdout.released.set()


def session(events, **kw):
    procs, spawned = [], []
    block = kw.pop("block", False)

    def popen(argv, **pk):
        spawned.append((argv, pk))
        procs.append(FakeProc(events, block=block))
        return procs[-1]
    seen = []
    out = run.run_session([r"C:\c\claude.exe", "-p", "x"], {"A": "1"}, Path("."), popen=popen,
                          killer=lambda p: p.kill(), on_event=seen.append, **kw)
    return out, procs[0], spawned[0], seen


def test_terminal_safe_strips_escapes_but_keeps_lines():
    assert run.terminal_safe("a\r\nb\x1b[2Jc\x9bd\te") == "a\nb?[2Jc?d\te"


def test_parse_stream_line():
    assert run.parse_stream_line("") is None and run.parse_stream_line("{bad") is None
    assert run.parse_stream_line("[1, 2]") is None
    assert run.parse_stream_line(b'{"type": "result"}\n') == {"type": "result"}


def test_session_success_spawns_directly_and_never_surfaces_content():
    o, proc, (argv, pk), seen = session([INIT_OK, ASSIST, result()])
    assert o.outcome == run.OUTCOME_SUCCESS and o.ok and o.num_turns == 7 and o.api_key_source == "none"
    assert o.notional_cost_usd == 0.42 and not proc.killed
    assert pk["shell"] is False and pk["env"] == {"A": "1"} and pk["stdin"] is not None
    assert "SECRET THOUGHTS" not in json.dumps(seen) and "SECRET THOUGHTS" not in json.dumps(o.to_log())


def test_session_error_max_turns_is_its_own_outcome():
    o, *_ = session([INIT_OK, result("error_max_turns", is_error=True, turns=40)])
    assert o.outcome == run.OUTCOME_MAX_TURNS and o.num_turns == 40 and not o.ok
    o, *_ = session([INIT_OK, result("error_during_execution", is_error=True)])
    assert o.outcome == run.OUTCOME_ERROR
    o, *_ = session([INIT_OK])
    assert o.outcome == run.OUTCOME_NO_RESULT


def test_session_rate_limit_event():
    allowed = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1760000000}}
    o, *_ = session([INIT_OK, allowed, result()])
    assert not o.rate_limited and o.rate_limit_status == "allowed"
    rejected = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1760000000}}
    o, *_ = session([INIT_OK, rejected, result("error_during_execution", is_error=True)])
    assert o.rate_limited and o.rate_limit_status == "rejected" and o.rate_limit_resets_at.startswith("2025-")


@pytest.mark.parametrize("init", [
    dict(INIT_OK, apiKeySource="ANTHROPIC_API_KEY"),
    dict(INIT_OK, mcp_servers=[{"name": "gmail", "status": "connected"}]),
    dict(INIT_OK, tools=INIT_OK["tools"] + ["mcp__gmail__send"]),
])
def test_session_init_checks_kill_the_session(init):
    o, proc, _, _ = session([init, result()])
    assert o.outcome == run.OUTCOME_INIT_REFUSED and proc.killed and o.refusal


def test_session_wall_clock_kill():
    clock = itertools.count(0, 10).__next__
    o, proc, _, _ = session([], block=True, wall_timeout_s=5, clock=clock)
    assert o.outcome == run.OUTCOME_TIMEOUT and proc.killed


def test_real_subprocess_stream_and_tree_kill(tmp_path):
    """The real Popen/reader-thread/kill_tree path, with python standing in for claude.exe."""
    import os
    import sys
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import json, sys, time\n"
        f"print(json.dumps({INIT_OK!r}), flush=True)\n"
        "if sys.argv[1] == 'hang':\n    time.sleep(60)\n"
        f"print(json.dumps({result()!r}), flush=True)\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
    o = run.run_session([sys.executable, str(script), "ok"], env, tmp_path, wall_timeout_s=60)
    assert o.outcome == run.OUTCOME_SUCCESS and o.exit_code == 0 and o.api_key_source == "none"
    o = run.run_session([sys.executable, str(script), "hang"], env, tmp_path, wall_timeout_s=2)
    assert o.outcome == run.OUTCOME_TIMEOUT and o.exit_code not in (None, 0)


# ── ids ─────────────────────────────────────────────────────────────────────
def test_derive_lab_id():
    import re
    pat = re.compile(gates.LAB_ID_PATTERN)
    a = run.derive_lab_id("Buy gold when London breaks the Asian range high", "XAUUSD")
    assert a == "lab_buy_gold_when_london_xauusd" and pat.match(a)
    taken = {a}
    b = run.derive_lab_id("Buy gold when London breaks", "XAUUSD", exists=taken.__contains__)
    assert b == a + "_2"
    long = run.derive_lab_id("Supercalifragilisticexpialidocious antidisestablishmentarianism x y", "EURUSD")
    assert pat.match(long) and len(long) <= 44 and long.endswith("_eurusd")
    assert pat.match(run.derive_lab_id("¡¿ %%% ?!", "US30"))
    # a forbidden label never becomes part of an id (contract.validate_manifest would refuse it)
    lid = run.derive_lab_id("ELITE edge found: Tier 1 OPTIMAL gold breakout", "XAUUSD")
    assert lid == "lab_edge_tier_gold_breakout_xauusd"
    assert not gates.contains_forbidden_label(lid.replace("_", " "))
    # the runner's id preflight and slug agree with contract.forbidden_label_in (the server's check)
    from sajim_quant.lab import contract
    lid = run.derive_lab_id("Tier1 edgefound gold breakout", "XAUUSD")
    assert not contract.forbidden_label_in(lid) and lid == "lab_gold_breakout_xauusd"
    for bad in ("lab_tier1_gold", "lab_edgefound_x", "lab_elite_x"):
        assert run._forbidden_id(bad), bad


# ── README ──────────────────────────────────────────────────────────────────
def test_readme_order_and_no_forbidden_labels():
    sha = "c" * 64
    inputs = run.ReadmeInputs(
        lab_id=LAB, family=LAB, name="ELITE Tier 1 breakout", description="An optimal edge found idea\nline 2",
        symbol="XAUUSD", timeframe="M15", verdict=gates.CANDIDATE, reasons=["all hard gates passed"],
        dev=make_scorecard(LAB, sha), holdout=make_scorecard(LAB, sha, stage="holdout", final=True),
        review=review(LAB, sha, reason="looks fine"), trials_used=2, holdout_touches=1, global_trials=9,
        final_sha=sha, noise=[{"file": "f_result.json", "ok": True, "report": "r.md", "summary": {"n": 3}}])
    text = run.render_readme(inputs)
    assert text.startswith("# [label removed] [label removed] breakout — CANDIDATE\n")
    assert not gates.contains_forbidden_label(text)
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert heads == ["## Idea (verbatim)", "## DEV (IN-SAMPLE) vs HOLDOUT", "## Gates", "## Cost model",
                     "## Critic", "## Context (report only)", "## Protocol", "## Files"]
    assert "Trial 2 of 3 · holdout touches 1/3 · global lab trials 9" in text
    assert "H-A" in text and "H-B" in text and "1.84" in text and gates.CANDIDATE_MEANING in text


# ── Whole flow ──────────────────────────────────────────────────────────────
class World:
    """Fake Claude sessions + fake lab API around a tmp repo. `timeline` records everything in order."""

    def __init__(self, tmp_path, *, dev_fail=None, critic=None, code_outcome=None, holdout_pass=True,
                 inputs=(), evaluate=None):
        self.root = tmp_path / "repo"
        self.home = tmp_path / "home"
        (self.home / ".sajim").mkdir(parents=True)
        (self.home / ".sajim" / "judge_token").write_text(JUDGE, encoding="utf-8")
        self.env = dict(HOSTILE_ENV, USERPROFILE=str(self.home), SAJIM_API_URL="https://lab", SAJIM_LAB_TOKEN="labtok")
        self.timeline, self.envs, self.out = [], [], []
        self.dev_fail, self.critic, self.code_outcome = dev_fail, critic, code_outcome
        self.inputs = list(inputs)
        self.folder = self.root / I.GENERATED_DIR / LAB
        self.noise_calls = []
        self.script = {
            "scorecard": self._scorecard, "family": {"n_trials": 1, "holdout_touches": 1, "n_trials_global": 4},
            "evaluate": evaluate if evaluate is not None else {"job_id": "fc-h", "holdout_touch": 1},
            "wait": lambda job_id: {"status": "done", "result": {
                "success": True, "kind": "lab_holdout", "code_sha256": self.sha, "stage": "holdout",
                "scorecard": make_scorecard(LAB, self.sha, stage="holdout", final=True, holdout_pass=holdout_pass,
                                            holdout_touches=1)}},
            "finalize": {"ok": True},
        }

    @property
    def sha(self):
        return sha_of(self.folder)

    def _scorecard(self, family, sha12, stage):
        sc = cli.load_dev_scorecard(self.folder, sha12)
        return dict(sc, detail={"null": {"runs": 300}}) if (sc and stage == "dev") else cli.LabApiError("404", status=404)

    def session(self, argv, env, cwd, *, wall_timeout_s, on_event):
        stage = env["SAJIM_LAB_STAGE"]
        self.timeline.append(("session", stage))
        self.envs.append(dict(env))
        assert argv[0].endswith("claude.exe") and "-p" in argv
        if stage == "spec":
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "spec.md").write_text("# Spec\n\nrules\n", encoding="utf-8")
        elif stage == "code":
            if self.code_outcome:
                return self.code_outcome
            make_lab_folder(self.root, LAB, spec=None, man=manifest(LAB, name="EMA test (XAUUSD M15)"))
            cli.stamp_manifest(self.folder)
            cli.save_scorecard(self.folder, make_scorecard(LAB, self.sha, fail=self.dev_fail))
            log = cli.load_build_log(self.folder)
            log["final_sha"] = self.sha
            cli.save_build_log(self.folder, log)
        elif stage == "review":
            (self.folder / "review.json").write_text(json.dumps(self.critic or review(LAB, self.sha)), encoding="utf-8")
        return run.SessionOutcome(subtype="success", is_error=False, num_turns=5, api_key_source="none",
                                  outcome=run.OUTCOME_SUCCESS)

    def client(self, base_url, *, lab_token=None, judge_token=None, **kw):
        return FakeClient(base_url, lab_token=lab_token, judge_token=judge_token, script=self.script,
                          calls=self.timeline)

    def noise(self, file, root):
        self.noise_calls.append(file)
        return {"file": file, "ok": True, "report": "r.md", "summary": {"trades_total": 3}}

    def deps(self, **over):
        d = run.RunnerDeps(environ=self.env, root=self.root, find_exe=lambda env: Path(r"C:\c\claude.exe"),
                           preflight=lambda exe, env: self.timeline.append(("preflight", env["SAJIM_LAB_STAGE"])),
                           run_session=self.session, input_fn=self._input, out=self.out.append,
                           client_factory=self.client, read_judge_token=lambda env: JUDGE, noise=self.noise,
                           now=lambda: datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc),
                           lock_path=self.root.parent / "lab.lock")
        for k, v in over.items():
            setattr(d, k, v)
        return d

    def _input(self, prompt):
        self.timeline.append(("input", prompt))
        return self.inputs.pop(0)

    def main(self, *extra, **over):
        self.root.mkdir(parents=True, exist_ok=True)
        return run.main(["Buy gold above the EMA", "XAUUSD", "M15", "--id", LAB, *extra], deps=self.deps(**over))

    def calls(self, name, scope=None):
        return [c for c in self.timeline if c[0] in ("builder", "judge") and c[1] == name
                and (scope is None or c[0] == scope)]

    def readme(self):
        return (self.folder / "README.md").read_text(encoding="utf-8")


def _holdout_posts(w):
    return [c for c in w.calls("evaluate") if c[2][0]["stage"] == "holdout"]


def test_full_flow_holdout_only_after_every_claude_session(tmp_path):
    w = World(tmp_path)
    assert w.main("--yes") == 0
    kinds = [(c[0], c[1]) for c in w.timeline]
    assert kinds[:4] == [("preflight", "spec"), ("session", "spec"), ("session", "code"), ("session", "review")]
    post = _holdout_posts(w)
    assert len(post) == 1 and post[0][0] == "judge"
    assert kinds.index(("judge", "evaluate")) > kinds.index(("session", "review"))
    assert post[0][2][0]["request_id"] == I.derive_request_id(LAB, w.sha, "holdout")
    # the judge token never reaches a Claude session; neither do API keys or the dashboard token
    for env in w.envs:
        assert set(env) <= set(I.CHILD_ENV_ALLOWLIST)
        blob = json.dumps(env)
        assert JUDGE not in blob and "sk-ant" not in blob and "dash" not in blob and "oauth" not in blob
    fin = w.calls("finalize", "judge")[0][2][0]
    assert fin["verdict"] == gates.CANDIDATE and fin["source"] == "scorecard" and fin["review"]["decision"] == "KEEP"
    text = w.readme()
    assert text.startswith("# EMA test (XAUUSD M15) — CANDIDATE") and not gates.contains_forbidden_label(text)
    assert (w.folder / "scorecards" / f"holdout_{w.sha[:12]}.json").is_file()
    assert w.noise_calls == [f"{LAB}_XAUUSD_M15_dev_result.json", f"{LAB}_XAUUSD_M15_holdout_result.json"]
    req = json.loads((w.folder / "request.json").read_text())
    assert req["description"] == "Buy gold above the EMA" and req["approved_spec_sha256"]
    log_text = (w.folder / "build_log.json").read_text()
    assert gates.CANDIDATE not in log_text                   # no holdout outcome where Claude could read it
    assert not (w.root.parent / "lab.lock").exists()


def test_approval_flow_revise_then_approve_and_cancel(tmp_path):
    w = World(tmp_path / "a", inputs=["r use a 2R target", "a"])
    assert w.main() == 0
    assert [c for c in w.timeline if c[0] == "session"][:3] == [("session", "spec")] * 2 + [("session", "code")]
    assert json.loads((w.folder / "request.json").read_text())["revision_notes"] == ["use a 2R target"]
    w2 = World(tmp_path / "b", inputs=["c"])
    assert w2.main() == run.EXIT_CANCELLED
    assert ("session", "code") not in w2.timeline and w2.calls("evaluate") == []


def test_dev_failure_means_no_holdout(tmp_path):
    w = World(tmp_path, dev_fail="G3")
    assert w.main("--yes") == 0
    assert _holdout_posts(w) == []
    fin = w.calls("finalize")[0][2][0]
    assert fin["verdict"] == gates.REJECT and fin["source"] == "budget"
    assert w.readme().startswith("# EMA test (XAUUSD M15) — REJECT")
    assert "Sealed holdout not run: not every hard DEV gate passed" in w.readme()


def test_critic_downgrade_to_reject_saves_the_holdout_touch(tmp_path):
    w = World(tmp_path)
    w.critic = None
    w.script_review = True

    def critic_session(argv, env, cwd, **kw):
        out = World.session(w, argv, env, cwd, **kw)
        if env["SAJIM_LAB_STAGE"] == "review":
            bad = review(LAB, w.sha, decision="DOWNGRADE", to="REJECT", reason="exit rule differs from spec")
            (w.folder / "review.json").write_text(json.dumps(bad), encoding="utf-8")
        return out
    assert w.main("--yes", run_session=critic_session) == 0
    assert _holdout_posts(w) == []
    fin = w.calls("finalize")[0][2][0]
    assert fin["verdict"] == gates.REJECT and fin["source"] == "critic"


def test_no_holdout_flag_and_holdout_failure(tmp_path):
    w = World(tmp_path / "a")
    assert w.main("--yes", "--no-holdout") == 0
    assert _holdout_posts(w) == [] and w.calls("finalize")[0][2][0]["verdict"] == gates.NOT_PROVEN
    w2 = World(tmp_path / "b", holdout_pass=False)
    assert w2.main("--yes") == 0 and w2.calls("finalize")[0][2][0]["verdict"] == gates.REJECT


def test_holdout_already_run_reuses_the_server_scorecard(tmp_path):
    w = World(tmp_path, evaluate=cli.LabApiError("HTTP 409", status=409, code="holdout_already_run"))
    w.script["scorecard"] = lambda fam, sha12, stage: (
        make_scorecard(LAB, w.sha, stage="holdout", final=True) if stage == "holdout"
        else cli.load_dev_scorecard(w.folder, sha12))
    assert w.main("--yes") == 0 and w.calls("finalize")[0][2][0]["verdict"] == gates.CANDIDATE


def test_rate_limited_code_session_pauses_without_retry(tmp_path):
    limited = run.SessionOutcome(subtype="error_during_execution", outcome=run.OUTCOME_ERROR, rate_limited=True,
                                 rate_limit_status="rejected")
    w = World(tmp_path, code_outcome=limited)
    assert w.main("--yes") == run.EXIT_SESSION
    assert [c for c in w.timeline if c[0] == "session"] == [("session", "spec"), ("session", "code")]
    assert w.calls("evaluate") == [] and any("PAUSED" in m and "--resume" in m for m in w.out)


def test_resume_after_rate_limit_skips_completed_steps(tmp_path):
    limited = run.SessionOutcome(subtype="error_during_execution", outcome=run.OUTCOME_ERROR, rate_limited=True,
                                 rate_limit_status="rejected")
    w = World(tmp_path, code_outcome=limited)
    assert w.main("--yes") == run.EXIT_SESSION
    w.code_outcome = None
    w.timeline.clear()
    assert run.main(["--resume", LAB], deps=w.deps()) == 0
    sessions = [c for c in w.timeline if c[0] == "session"]
    assert sessions == [("session", "code"), ("session", "review")]          # spec was already approved
    assert len(_holdout_posts(w)) == 1
    w.timeline.clear()
    assert run.main(["--resume", LAB], deps=w.deps()) == 0                   # everything done: no Claude, no holdout
    assert not [c for c in w.timeline if c[0] == "session"] and _holdout_posts(w) == []
    assert len(w.calls("finalize")) == 1                                     # finalize is idempotent server-side


def test_max_turns_without_scorecard_is_exit_3(tmp_path):
    w = World(tmp_path, code_outcome=run.SessionOutcome(subtype="error_max_turns", outcome=run.OUTCOME_MAX_TURNS))
    assert w.main("--yes") == run.EXIT_SESSION and ("session", "review") not in w.timeline


def test_init_refusal_and_preflight_refusal_are_exit_2(tmp_path):
    refused = run.SessionOutcome(outcome=run.OUTCOME_INIT_REFUSED, refusal="init: apiKeySource is ANTHROPIC_API_KEY")
    w = World(tmp_path / "a")
    assert w.main("--yes", run_session=lambda *a, **k: refused) == run.EXIT_REFUSED

    def deny(exe, env):
        raise run.PreflightError("claude auth method is api_key, not claude.ai")
    w2 = World(tmp_path / "b")
    assert w2.main("--yes", preflight=deny) == run.EXIT_REFUSED
    assert not [c for c in w2.timeline if c[0] == "session"] and any("REFUSED" in m for m in w2.out)
    assert not (w2.root / I.GENERATED_DIR / LAB).exists()


def test_universe_lock_and_daily_cap(tmp_path):
    w = World(tmp_path / "a")
    w.root.mkdir(parents=True)
    assert run.main(["idea", "BTCUSD", "M5"], deps=w.deps()) == run.EXIT_REFUSED      # short coverage
    (w.root.parent / "lab.lock").write_text("{}")
    assert w.main("--yes") == run.EXIT_REFUSED and any("lock" in m for m in w.out)
    (w.root.parent / "lab.lock").unlink()
    for k in range(I.MAX_BUILDS_PER_DAY):
        f = w.root / I.GENERATED_DIR / f"lab_other_{k}" / "request.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"created_at": "2026-10-13T01:00:00+00:00"}))
    assert w.main("--yes") == run.EXIT_REFUSED and any("daily cap" in m for m in w.out)


def test_forbidden_words_in_the_idea_never_reach_the_readme(tmp_path):
    w = World(tmp_path)
    w.root.mkdir(parents=True)
    rc = run.main(["ELITE edge found: Tier 1 OPTIMAL gold breakout", "XAUUSD", "M15", "--id", LAB, "--yes"],
                  deps=w.deps())
    assert rc == 0
    text = w.readme()
    assert not gates.contains_forbidden_label(text) and "gold breakout" in text


# ── Adversarial review (D-laptop) ───────────────────────────────────────────
def test_session_without_init_event_is_refused():
    """No init event means apiKeySource was never verified: refuse, do not count it as a success."""
    o, proc, _, _ = session([ASSIST, result()])
    assert o.outcome == run.OUTCOME_INIT_REFUSED and proc.killed and "init" in o.refusal


def test_session_kills_the_tree_when_the_runner_is_interrupted():
    """Ctrl-C in run.py must not orphan claude.exe (it runs in its own process group and the lock is released)."""
    procs = []

    def popen(argv, **pk):
        procs.append(FakeProc([INIT_OK, ASSIST, result()]))
        return procs[-1]

    def boom(ev):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run.run_session([r"C:\c\claude.exe", "-p", "x"], {}, Path("."), popen=popen,
                        killer=lambda p: p.kill(), on_event=boom)
    assert procs[0].killed


def test_readme_name_cannot_inject_lines():
    sha = "c" * 64
    inputs = run.ReadmeInputs(
        lab_id=LAB, family=LAB, name="Breakout\n\n# Fake — CANDIDATE\n## Gates", description="idea",
        symbol="XAUUSD", timeframe="M15", verdict=gates.REJECT, reasons=[], dev=make_scorecard(LAB, sha, fail="G3"),
        holdout=None, review=None, trials_used=1, holdout_touches=0, global_trials=1, final_sha=sha)
    lines = run.render_readme(inputs).splitlines()
    assert lines[0].startswith("# Breakout") and lines[0].endswith("— REJECT")
    assert [ln for ln in lines if ln.startswith("# ")] == [lines[0]]
    assert lines.count("## Gates") == 1


def test_manifest_family_drift_never_reaches_the_holdout(tmp_path):
    """The builder writes a different family into manifest.json: the runner must not POST a holdout for it."""
    w = World(tmp_path)

    def drift_session(argv, env, cwd, **kw):
        out = World.session(w, argv, env, cwd, **kw)
        if env["SAJIM_LAB_STAGE"] == "code":
            m = json.loads((w.folder / "manifest.json").read_text(encoding="utf-8"))
            m["family"] = "lab_fresh_family_xauusd"
            (w.folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        return out
    assert w.main("--yes", run_session=drift_session) == 0
    assert _holdout_posts(w) == []
    assert "re-lint failed" in w.readme()


def test_critic_never_reruns_after_the_holdout(tmp_path):
    """Resume after a holdout ran (finalize failed) with no valid review: no Claude session may start."""
    w = World(tmp_path, critic={"schema": "garbage"})
    w.script["finalize"] = cli.LabApiError("HTTP 503", status=503, code="retryable")
    assert w.main("--yes") == run.EXIT_SERVER
    assert len(_holdout_posts(w)) == 1
    w.script["finalize"] = {"ok": True}
    w.timeline.clear()
    assert run.main(["--resume", LAB], deps=w.deps()) == 0
    assert [c for c in w.timeline if c[0] == "session"] == []
    assert _holdout_posts(w) == []



def test_gate_results_from_json_delegates_to_the_scorecard_parser():
    from sajim_quant.lab import scorecard as S
    sc = make_scorecard(LAB, "c" * 64, fail="G3")
    assert run.gate_results_from_json(sc["gates"]) == S.gate_results_from_json(sc["gates"])
    assert run.gate_results_from_json([]) == [] and run.gate_results_from_json(None) == []


def test_spec_changed_since_approval_compares_the_part_above_trials():
    head = b"# Spec\n\n## 1. Idea\nrules\n\n"
    approved = head + b"## 8. Trials\n"
    req = {"approved_spec_sha256": run.hashlib.sha256(approved).hexdigest(),
           "approved_spec_head_sha256": run.spec_head_sha256(approved)}
    assert run.spec_changed_since_approval(req, approved) == "no"
    assert run.spec_changed_since_approval(req, approved + b"- trial 1: bug-fix (off-by-one)\n") == "no"
    assert run.spec_changed_since_approval(req, head.replace(b"rules", b"rules v2") + b"## 8. Trials\n") == "yes"
    assert run.spec_changed_since_approval(req, None) == "yes"
    old = {"approved_spec_sha256": req["approved_spec_sha256"]}             # request.json from before p1.2
    assert run.spec_changed_since_approval(old, approved) == "no"
    assert run.spec_changed_since_approval(old, approved + b"- trial 1\n") == "unknown"
    assert run.spec_changed_since_approval({}, approved) == "unknown"
    # line endings / a BOM are not a change: guard_paths compares spec.md the same way
    assert run.spec_changed_since_approval(req, approved.replace(b"\n", b"\r\n")) == "no"
    assert run.spec_changed_since_approval(req, b"\xef\xbb\xbf" + approved + b"- trial 1\n") == "no"


def test_spec_change_is_recorded_for_the_critic_and_shown_in_the_readme(tmp_path):
    w = World(tmp_path / "a")
    assert w.main("--yes") == 0
    req = json.loads((w.folder / "request.json").read_text())
    assert req["approved_spec_head_sha256"] and req["spec_changed_since_approval"] in ("yes", "no")
    log = json.loads((w.folder / "build_log.json").read_text())
    checks = [e for e in log["events"] if e.get("event") == "spec_check"]
    assert checks and checks[0]["spec_changed_since_approval"] == req["spec_changed_since_approval"]
    kinds = [e.get("event") for e in log["events"]]
    assert kinds.index("spec_check") < kinds.index("critic")                  # recorded before the critic
    assert f"spec changed since approval (spec.md above '## 8. Trials'): {req['spec_changed_since_approval']}" \
        in w.readme()

    w2 = World(tmp_path / "b")

    def editing_session(argv, env, cwd, **kw):
        out = World.session(w2, argv, env, cwd, **kw)
        if env["SAJIM_LAB_STAGE"] == "code":
            (w2.folder / "spec.md").write_text("# Spec\n\nrules, tuned after DEV\n", encoding="utf-8")
            cli.stamp_manifest(w2.folder)
        return out
    assert w2.main("--yes", run_session=editing_session) == 0
    assert json.loads((w2.folder / "request.json").read_text())["spec_changed_since_approval"] == "yes"
    assert "spec changed since approval (spec.md above '## 8. Trials'): yes" in w2.readme()
    assert any("changed after approval" in m for m in w2.out)


def test_forbidden_label_ids_are_refused_at_preflight(tmp_path):
    w = World(tmp_path)
    w.root.mkdir(parents=True)
    rc = run.main(["gold idea", "XAUUSD", "M15", "--id", "lab_elite_gold", "--yes"], deps=w.deps())
    assert rc == run.EXIT_REFUSED and any("forbidden label" in m for m in w.out)
    rc = run.main(["gold idea", "XAUUSD", "M15", "--family", "lab_tier_1_fam", "--yes"], deps=w.deps())
    assert rc == run.EXIT_REFUSED
