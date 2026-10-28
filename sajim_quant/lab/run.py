"""
SAJIM Strategy Lab — the laptop orchestrator (INTERFACES.md section 11). Plain Python, no LLM.

    python -X utf8 -m sajim_quant.lab.run "<idea>" SYMBOL TF [--id lab_x] [--family lab_f] [--yes]
                                           [--no-holdout] [--resume lab_x]

It is the only thing that launches Claude. Each session is the unmodified claude.exe
(never the npm claude.cmd shim: cmd.exe expands %VAR% and truncates at newlines inside
arguments), on the owner's own Max login, with a scrubbed allowlist environment:

  1. preflight   claude >= MIN_CLAUDE_VERSION; `claude auth status --json` must say
                 loggedIn / authMethod claude.ai / subscriptionType max; one run at a time;
                 at most MAX_BUILDS_PER_DAY builds per UTC day
  2. spec        /lab-build <id> --stage spec -> spec.md -> console Approve / Revise / Cancel
  3. code        /lab-build <id> --stage code -> strategy.py, manifest.json, lab.cli check/dev/done
  4. critic      /lab-review <id> -> review.json (downgrade only; runs BEFORE the holdout, so no
                 Claude session ever sees a holdout number)
  5. holdout     POST /lab/evaluate stage=holdout with the JUDGE token, only if every hard DEV gate
                 passed, the critic did not downgrade to REJECT and the family is not burned
  6. noise       report-only context (existing cloud.client noise), never changes the verdict
  7. verdict     POST /lab/finalize (judge) and README.md, verdict first

Secrets: the judge token (%USERPROFILE%\\.sajim\\judge_token) is read only after the last
Claude session has exited, is sent only as X-Judge-Token, and never enters a child env.
No token value is ever printed or written to disk by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sajim_quant.lab import cli, gates, splits
from sajim_quant.lab import interfaces as I

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]
RUNNER_VERSION = "1"
SETTINGS_RELPATH = Path(".claude") / "lab" / "settings.json"
LOCK_NAME = "sajim_lab_run.lock"
NODE_CLAUDE_EXE = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
SHIM_SUFFIXES = (".cmd", ".bat", ".ps1")
MAX_SPEC_REVISIONS = 3
MAX_IDEA_CHARS = 4000
MAX_NOTE_CHARS = 2000
HOLDOUT_WAIT_S = 1800
CLAUDE_TOOL_SET = frozenset(I.CLAUDE_TOOLS.split(","))
_FORBIDDEN_RE = re.compile("|".join(re.escape(x) for x in gates.FORBIDDEN_LABELS), re.IGNORECASE)
_SAFE_WORD_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
#: The spec.md heading that starts the Trials log (== .claude/lab/hooks/guard_paths._TRIALS_HEADING).
_TRIALS_HEADING_RE = re.compile(r"^##\s*8\.", re.M)
SPEC_CHANGED_VALUES = ("yes", "no", "unknown")

# Exit codes (INTERFACES.md section 11).
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_SESSION = 3
EXIT_CANCELLED = 4
EXIT_SERVER = 5
EXIT_BUDGET = 6

# Session outcomes.
OUTCOME_SUCCESS = "success"
OUTCOME_MAX_TURNS = "max_turns"            # result subtype error_max_turns — its own outcome
OUTCOME_ERROR = "error"                    # any other result subtype / is_error
OUTCOME_TIMEOUT = "timeout"                # SESSION_WALL_TIMEOUT_S reached; PID tree killed
OUTCOME_INIT_REFUSED = "init_refused"      # init event failed the auth/tool/MCP checks; killed
OUTCOME_NO_RESULT = "no_result"            # the process ended without a result event
COMPLETED_OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_MAX_TURNS, OUTCOME_ERROR)
RATE_LIMIT_OK = ("allowed", "allowed_warning")


class PreflightError(RuntimeError):
    """A refusal before (or instead of) running Claude: exit 2. Messages never carry secrets."""


class _Stop(Exception):
    """Ends the run with an exit code after the message was printed."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


# ── claude.exe resolution ───────────────────────────────────────────────────
def _is_shim(p: Path) -> bool:
    return p.suffix.lower() in SHIM_SUFFIXES


def find_claude_exe(environ: Optional[Mapping[str, str]] = None, *,
                    which: Callable[[str], Optional[str]] = shutil.which) -> Path:
    """
    The real claude.exe, never a shim. Order: SAJIM_CLAUDE_EXE; shutil.which("claude")
    (an npm claude.cmd/.bat/.ps1 or bare shim is mapped to <dir>/node_modules/@anthropic-ai/
    claude-code/bin/claude.exe); shutil.which("claude.exe"); %USERPROFILE%/.local/bin/claude.exe.
    """
    env = os.environ if environ is None else environ
    override = (env.get("SAJIM_CLAUDE_EXE") or "").strip()
    if override:
        p = Path(override)
        if _is_shim(p) or p.suffix.lower() != ".exe" or not p.is_file():
            raise PreflightError("SAJIM_CLAUDE_EXE must point to an existing claude.exe (never a .cmd/.bat/.ps1 shim)")
        return p
    found = which("claude")
    if found:
        p = Path(found)
        if p.suffix.lower() == ".exe" and p.is_file():
            return p
        candidate = p.parent / NODE_CLAUDE_EXE
        if candidate.is_file():
            return candidate
    found_exe = which("claude.exe")
    if found_exe and Path(found_exe).suffix.lower() == ".exe" and Path(found_exe).is_file():
        return Path(found_exe)
    local = Path(env.get("USERPROFILE") or Path.home()) / ".local" / "bin" / "claude.exe"
    if local.is_file():
        return local
    raise PreflightError("claude.exe not found (only a .cmd/.bat/.ps1 shim, or nothing, is on PATH); "
                         "set SAJIM_CLAUDE_EXE to the claude.exe path")


# ── Child environment ───────────────────────────────────────────────────────
def _forbidden_env_name(name: str) -> bool:
    n = name.upper()
    return (n in I.CHILD_ENV_FORBIDDEN or n.startswith("ANTHROPIC_") or n.startswith("CLAUDE_CODE_")
            or "JUDGE" in n or n == "SAJIM_API_TOKEN")


def child_env(base: Mapping[str, str], *, lab_id: str, stage: str, lab_token: str, api_url: str) -> Dict[str, str]:
    """Only interfaces.CHILD_ENV_ALLOWLIST keys (matched case-insensitively), plus the lab variables."""
    if stage not in I.LAB_STAGES:
        raise PreflightError(f"unknown session stage {stage!r}")
    if not re.fullmatch(gates.LAB_ID_PATTERN, lab_id or ""):
        raise PreflightError("lab id must match the lab id pattern")
    if not lab_token or not api_url:
        raise PreflightError("the lab token and API URL are required for a lab session")
    upper = {str(k).upper(): v for k, v in base.items()}
    env = {k: str(upper[k]) for k in I.CHILD_ENV_ALLOWLIST if k in upper and upper[k] is not None}
    env.update({"PYTHONUTF8": "1", "SAJIM_LAB_ID": lab_id, "SAJIM_LAB_STAGE": stage,
                "SAJIM_LAB_TOKEN": lab_token, "SAJIM_API_URL": api_url})
    bad = sorted(k for k in env if k not in I.CHILD_ENV_ALLOWLIST or _forbidden_env_name(k))
    if bad:
        raise PreflightError(f"child environment would carry forbidden variables: {', '.join(bad)}")
    return env


def assert_secret_absent(env: Mapping[str, str], secret: Optional[str]) -> None:
    """Defence in depth: no child env value may contain the judge token."""
    if secret and any(secret in str(v) for v in env.values()):
        raise PreflightError("the judge token would leak into a Claude session environment")


# ── argv / preflight ────────────────────────────────────────────────────────
def session_prompt(stage: str, lab_id: str) -> str:
    if stage == "review":
        return f"/lab-review {lab_id}"
    return f"/lab-build {lab_id} --stage {stage}"


def claude_argv(exe: Path, prompt: str, *, settings: Path, max_turns: int) -> List[str]:
    """The exact P0 argv (INTERFACES.md section 11)."""
    return [str(exe), "-p", prompt, "--settings", str(settings),
            "--tools", I.CLAUDE_TOOLS, "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--max-turns", str(int(max_turns)),
            "--output-format", "stream-json", "--verbose"]


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _safe_word(v: Any) -> str:
    s = str(v)
    return s if _SAFE_WORD_RE.fullmatch(s) else "<other>"


def preflight(exe: Path, env: Mapping[str, str], *, run: Callable[..., Any] = subprocess.run,
              timeout: float = 60) -> None:
    """Version and subscription-auth gates. Reads only loggedIn/authMethod/subscriptionType."""
    if _is_shim(Path(exe)):
        raise PreflightError("refusing to run a .cmd/.bat/.ps1 shim; claude.exe is required")
    kw = dict(env=dict(env), stdin=subprocess.DEVNULL, capture_output=True, text=True,
              encoding="utf-8", errors="replace", timeout=timeout)
    try:
        r = run([str(exe), "--version"], **kw)
    except (OSError, subprocess.SubprocessError) as e:
        raise PreflightError(f"could not run claude --version ({type(e).__name__})") from None
    version = parse_version(getattr(r, "stdout", "") or "")
    want = ".".join(map(str, I.MIN_CLAUDE_VERSION))
    if version is None:
        raise PreflightError("could not read the claude version")
    if version < I.MIN_CLAUDE_VERSION:
        raise PreflightError(f"claude {'.'.join(map(str, version))} is older than {want} "
                             f"(needed for --permission-prompts none)")
    try:
        r = run([str(exe), "auth", "status", "--json"], **kw)
    except (OSError, subprocess.SubprocessError) as e:
        raise PreflightError(f"could not run claude auth status ({type(e).__name__})") from None
    try:
        data = json.loads(getattr(r, "stdout", "") or "")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise PreflightError("could not read `claude auth status --json`")
    logged_in, method, sub = data.get("loggedIn"), data.get("authMethod"), data.get("subscriptionType")
    if logged_in is not True:
        raise PreflightError("claude is not logged in; run `claude` and /login with the Max subscription")
    if method != "claude.ai":
        raise PreflightError(f"claude auth method is {_safe_word(method)}, not claude.ai: the lab runs only on "
                             f"the subscription login (API-key auth is refused)")
    if sub != "max":
        raise PreflightError(f"subscription type is {_safe_word(sub)}, not max")


# ── stream-json sessions ────────────────────────────────────────────────────
@dataclass
class SessionOutcome:
    """What one headless claude session did. Never holds message content."""

    subtype: str = ""
    is_error: bool = True
    num_turns: int = 0
    duration_ms: int = 0
    api_key_source: str = ""
    rate_limited: bool = False
    notional_cost_usd: Optional[float] = None      # total_cost_usd: notional under Max, not a usage meter
    outcome: str = OUTCOME_NO_RESULT
    exit_code: Optional[int] = None
    rate_limit_status: str = ""
    rate_limit_resets_at: Optional[str] = None
    refusal: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_SUCCESS

    def to_log(self) -> Dict[str, Any]:
        return asdict(self)


def parse_stream_line(line: Any) -> Optional[dict]:
    """One stream-json line -> dict, or None for blank/invalid/non-object lines."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    s = str(line or "").strip()
    if not s:
        return None
    try:
        ev = json.loads(s)
    except ValueError:
        return None
    return ev if isinstance(ev, dict) else None


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _resets_at(v: Any) -> Optional[str]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        try:
            return datetime.fromtimestamp(float(v), timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return cli.clean_text(v, 40) if isinstance(v, str) and v else None


def inspect_event(ev: Mapping[str, Any], state: SessionOutcome) -> Optional[str]:
    """Update `state` from one event; return a refusal reason when the session must be killed."""
    t = ev.get("type")
    if t in ("assistant", "user", "result") and not state.api_key_source:
        return "init: no init event before session activity; apiKeySource was never verified"
    if t == "system" and ev.get("subtype") == "init":
        src = ev.get("apiKeySource")
        state.api_key_source = _safe_word(src)
        if src != "none":
            return f"init: apiKeySource is {_safe_word(src)}, expected none (subscription login only)"
        mcp = ev.get("mcp_servers", [])
        if mcp != []:
            return "init: MCP servers are attached; the lab runs with --strict-mcp-config and none"
        tools = ev.get("tools") or []
        extra = sorted({str(x) for x in tools} - CLAUDE_TOOL_SET) if isinstance(tools, list) else ["<invalid>"]
        if extra:
            return f"init: unexpected tools {', '.join(_safe_word(x) for x in extra[:10])}"
    elif t == "rate_limit_event":
        info = ev.get("rate_limit_info") if isinstance(ev.get("rate_limit_info"), Mapping) else ev
        status = str(info.get("status") or "")
        state.rate_limit_status = _safe_word(status) if status else "unknown"
        state.rate_limit_resets_at = _resets_at(info.get("resetsAt"))
        if status not in RATE_LIMIT_OK:
            state.rate_limited = True
    elif t == "result":
        state.subtype = _safe_word(ev.get("subtype") or "")
        state.is_error = bool(ev.get("is_error"))
        state.num_turns = _as_int(ev.get("num_turns"))
        state.duration_ms = _as_int(ev.get("duration_ms"))
        cost = ev.get("total_cost_usd")
        state.notional_cost_usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return None


def _classify(state: SessionOutcome, *, timed_out: bool) -> str:
    if state.refusal:
        return OUTCOME_INIT_REFUSED
    if timed_out:
        return OUTCOME_TIMEOUT
    if state.subtype == "success" and not state.is_error:
        return OUTCOME_SUCCESS
    if state.subtype == "error_max_turns":
        return OUTCOME_MAX_TURNS
    if state.subtype:
        return OUTCOME_ERROR
    return OUTCOME_NO_RESULT


def kill_tree(proc: Any) -> None:
    """Kill only this session's PID tree (taskkill /T on Windows)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _brief(ev: Mapping[str, Any]) -> Dict[str, Any]:
    """Event summary safe to show: type/subtype and counters only, never message content."""
    out: Dict[str, Any] = {"type": _safe_word(ev.get("type") or "?")}
    if ev.get("subtype"):
        out["subtype"] = _safe_word(ev.get("subtype"))
    if ev.get("type") == "result":
        out["num_turns"] = _as_int(ev.get("num_turns"))
    return out


def run_session(argv: Sequence[str], env: Mapping[str, str], cwd: Path, *,
                wall_timeout_s: int = I.SESSION_WALL_TIMEOUT_S, on_event: Optional[Callable[[dict], None]] = None,
                popen: Callable[..., Any] = subprocess.Popen, killer: Callable[[Any], None] = kill_tree,
                clock: Callable[[], float] = time.monotonic) -> SessionOutcome:
    """Run one headless session, parse stream-json, enforce init checks and the wall clock."""
    if not argv or _is_shim(Path(argv[0])):
        raise PreflightError("refusing to spawn a shim; claude.exe is required")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    proc = popen(list(argv), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, shell=False, creationflags=flags)
    lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
    stderr_tail: deque = deque(maxlen=50)

    def read_stdout() -> None:
        try:
            for raw in iter(proc.stdout.readline, b""):
                lines.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    def read_stderr() -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                stderr_tail.append(raw[:500])
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for t in readers:
        t.start()
    state = SessionOutcome()
    deadline = clock() + wall_timeout_s
    timed_out = False
    killed = False
    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                timed_out = True
                break
            try:
                raw = lines.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if raw is None:
                break
            ev = parse_stream_line(raw)
            if ev is None:
                continue
            refusal = inspect_event(ev, state)
            if on_event is not None:
                on_event(_brief(ev) if not refusal else {"type": "refused"})
            if refusal:
                state.refusal = refusal
                break
    except BaseException:
        # Ctrl-C / any runner error: never leave claude.exe running in its own process group.
        killer(proc)
        raise
    if timed_out or state.refusal:
        killer(proc)
        killed = True
    try:
        state.exit_code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if not killed:
            killer(proc)
        try:
            state.exit_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            state.exit_code = None
    for t in readers:
        t.join(timeout=5)
    state.outcome = _classify(state, timed_out=timed_out)
    if stderr_tail and state.outcome != OUTCOME_SUCCESS:
        logger.debug("claude stderr tail captured (%d lines; not shown)", len(stderr_tail))
    return state


# ── ids, locks, request.json ────────────────────────────────────────────────
def _forbidden_id(text: str) -> bool:
    """contract.forbidden_label_in: the server's own test (lab_tier_1_x, lab_tier1_x, lab_elite_x)."""
    from sajim_quant.lab import contract          # builder A's module; lazy
    return contract.forbidden_label_in(text)


def slugify(text: str, max_words: int = 4) -> str:
    """The first max_words words, skipping any word that would complete a forbidden label."""
    kept: List[str] = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(kept) >= max_words:
            break
        if not _forbidden_id(" ".join(kept + [w])):
            kept.append(w)
    return "_".join(kept)


def derive_lab_id(idea: str, symbol: str, *, exists: Callable[[str], bool] = lambda _id: False) -> str:
    """'lab_' + slug(first 4 words) + '_' + symbol, at most 44 chars; _2, _3, ... if taken."""
    sym = re.sub(r"[^a-z0-9]", "", (symbol or "").lower()) or "sym"
    slug = slugify(idea) or "idea"
    pattern = re.compile(gates.LAB_ID_PATTERN)
    for n in range(1, 1000):
        suffix = "" if n == 1 else f"_{n}"
        room = 44 - len("lab_") - len("_" + sym) - len(suffix)
        base = slug[:room].strip("_") or "idea"
        lab_id = f"lab_{base}_{sym}{suffix}"
        if pattern.fullmatch(lab_id) and not _forbidden_id(lab_id) and not exists(lab_id):
            return lab_id
    raise PreflightError("could not derive a free lab id")


class RunLock:
    """One lab run at a time: O_EXCL lock file (default %TEMP%/sajim_lab_run.lock)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.held = False

    def acquire(self) -> None:
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
        except FileExistsError:
            raise PreflightError(f"another lab run is active (lock {self.path}); if none is, delete that file") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}))
        self.held = True

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


def builds_today(root: Path, now: datetime) -> int:
    """request.json files whose created_at is today's UTC date."""
    gen = Path(root) / I.GENERATED_DIR
    today = now.astimezone(timezone.utc).date().isoformat()
    n = 0
    if gen.is_dir():
        for p in gen.glob("lab_*/request.json"):
            try:
                created = str(cli.read_json(p).get("created_at") or "")
            except (OSError, ValueError, AttributeError):
                continue
            if created[:10] == today:
                n += 1
    return n


def spec_head(raw: bytes) -> bytes:
    """
    spec.md above the '## 8. Trials' heading (all of it when there is none): the approved part.
    A BOM and CRLF line endings are normalised away, as .claude/lab/hooks/guard_paths does, so
    an editor's line-ending change is not reported as a spec change.
    """
    text = raw.decode("utf-8", errors="replace").lstrip("﻿").replace("\r\n", "\n")
    m = _TRIALS_HEADING_RE.search(text)
    return (text[:m.start()] if m else text).encode("utf-8")


def spec_head_sha256(raw: bytes) -> str:
    return hashlib.sha256(spec_head(raw)).hexdigest()


def spec_changed_since_approval(request: Mapping[str, Any], spec_raw: Optional[bytes]) -> str:
    """
    "yes" / "no" / "unknown": did spec.md above '## 8. Trials' change after the user approved it?
    Uses request.json approved_spec_head_sha256 (written at approval); an older request.json
    without it can only prove "no" when the whole file is unchanged.
    """
    if spec_raw is None:
        return "yes" if request.get("approved_spec_sha256") else "unknown"
    head = request.get("approved_spec_head_sha256")
    if isinstance(head, str) and re.fullmatch(r"[0-9a-f]{64}", head):
        return "no" if spec_head_sha256(spec_raw) == head else "yes"
    if request.get("approved_spec_sha256") == hashlib.sha256(spec_raw).hexdigest():
        return "no"
    return "unknown"


def judge_token_path(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("USERPROFILE") or Path.home()) / ".sajim" / "judge_token"


def read_judge_token(environ: Mapping[str, str]) -> str:
    try:
        token = judge_token_path(environ).read_text(encoding="utf-8-sig").strip()
    except OSError:
        token = ""
    if not token:
        raise PreflightError("the judge token file %USERPROFILE%\\.sajim\\judge_token is missing or empty")
    return token


def scrub_labels(text: str) -> str:
    return _FORBIDDEN_RE.sub("[label removed]", text or "")


def terminal_safe(text: str, limit: int = 60_000) -> str:
    """Model-written text shown on the console: no control/escape sequences."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch if (ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127 and not 0x80 <= ord(ch) < 0xA0)) else "?"
                for ch in text)
    return s if len(s) <= limit else s[:limit] + "\n[... truncated]"


# ── Noise hand-off (report only) ────────────────────────────────────────────
def run_noise(result_file: str, root: Path) -> Optional[Dict[str, Any]]:
    """
    Fetch a result JSON with run.py's OWN API token (cloud.client config), save it, and run
    the existing noise CLI as a subprocess with run.py's own environment (never a child env).
    """
    from sajim_quant.cloud import client as cloud

    data = cloud.request("GET", f"/results/{result_file}", timeout=120)
    cloud.save_result(result_file, data)
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "sajim_quant.cloud.client", "noise",
                        "--result", f"data/results/{result_file}"], cwd=str(root), env=dict(os.environ),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    stem = result_file.removesuffix(".json").removesuffix("_result")
    enriched = Path(root) / "data" / "noise" / "diagnose" / f"{stem}_noise_enriched.json"
    summary: Dict[str, Any] = {}
    if r.returncode == 0 and enriched.is_file():
        try:
            ns = json.loads(enriched.read_text(encoding="utf-8")).get("noise_summary") or {}
            summary = {k: v for k, v in ns.items() if isinstance(v, (int, float, str, bool))}
        except (OSError, ValueError, AttributeError):
            summary = {}
    return {"file": result_file, "ok": r.returncode == 0,
            "report": f"data/noise/diagnose/{stem}_noise_report.md", "summary": dict(list(summary.items())[:12])}


# ── README ──────────────────────────────────────────────────────────────────
MEANING = {
    gates.CANDIDATE: f"CANDIDATE = {gates.CANDIDATE_MEANING}. It is not proof of a durable advantage; "
                     f"forward months are the only fully clean test.",
    gates.NOT_PROVEN: "NOT_PROVEN = not enough evidence to judge either way (too few trades, a DEV-only "
                      "result, or no sealed holdout result).",
    gates.REJECT: "REJECT = failed a hard gate after realistic costs, or failed the sealed holdout.",
}


def _f(v: Any, fmt: str = "{:,.2f}") -> str:
    if v is None or isinstance(v, bool):
        return "n/a"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v != v:
            return "n/a"
        if isinstance(v, float) and v in (float("inf"), float("-inf")):
            return "inf"
        return fmt.format(v)
    return cli.clean_text(v, 40)


def _row(label: str, values: Sequence[Any], fmt: str) -> str:
    return f"| {label} | " + " | ".join(_f(v, fmt) for v in values) + " |"


@dataclass
class ReadmeInputs:
    """Everything README.md shows. Built by the runner; rendered by render_readme()."""

    lab_id: str
    family: str
    name: str
    description: str
    symbol: str
    timeframe: str
    verdict: str
    reasons: List[str]
    dev: Optional[Mapping[str, Any]]
    holdout: Optional[Mapping[str, Any]]
    review: Optional[Mapping[str, Any]]
    trials_used: Optional[int]
    holdout_touches: Optional[int]
    global_trials: Optional[int]
    final_sha: Optional[str]
    holdout_note: str = ""
    warnings: List[str] = field(default_factory=list)
    noise: List[Mapping[str, Any]] = field(default_factory=list)
    finalize_recorded: bool = False
    spec_changed: str = "unknown"            # spec.md above '## 8. Trials' changed since approval


def render_readme(r: ReadmeInputs) -> str:
    """README.md, verdict first. Never contains gates.FORBIDDEN_LABELS (masked as a last guard)."""
    dev = r.dev or {}
    ho = r.holdout or {}
    dh, hh = dev.get("headline") or {}, ho.get("headline") or {}
    split = ho.get("holdout_split") or {}
    ha, hb = split.get("H_A") or {}, split.get("H_B") or {}
    name = terminal_safe(" ".join(str(r.name or r.lab_id).split())[:200])    # model-written: one line only
    L: List[str] = [f"# {name} — {r.verdict}", "", MEANING.get(r.verdict, ""), ""]
    L.append(f"Trial {_f(r.trials_used, '{}')} of {gates.MAX_DEV_TRIALS_PER_FAMILY} · holdout touches "
             f"{_f(r.holdout_touches, '{}')}/{gates.MAX_HOLDOUT_TOUCHES_PER_FAMILY} · "
             f"global lab trials {_f(r.global_trials, '{}')}")
    L.append("")
    L.append(f"`{r.lab_id}` (family `{r.family}`) · {r.symbol} {r.timeframe} · final sha "
             f"`{(r.final_sha or 'n/a')[:12]}`")
    changed = r.spec_changed if r.spec_changed in SPEC_CHANGED_VALUES else "unknown"
    L.append("")
    L.append(f"spec changed since approval (spec.md above '## 8. Trials'): {changed}")
    if r.reasons:
        L.append("")
        L += [f"- {cli.clean_text(x, 300)}" for x in r.reasons[:12]]
    if r.warnings:
        L.append("")
        L += [f"- WARNING: {cli.clean_text(x, 300)}" for x in r.warnings[:12]]
    L += ["", "## Idea (verbatim)", ""]
    L += [f"> {line}" if line else ">" for line in (r.description or "").splitlines()] or ["> "]
    L += ["", f"## DEV ({splits.IN_SAMPLE_LABEL}) vs HOLDOUT", ""]
    if not r.holdout:
        L.append(f"Sealed holdout not run{': ' + r.holdout_note if r.holdout_note else ''}.")
        L.append("")
    L.append(f"| | DEV ({splits.IN_SAMPLE_LABEL}) | HOLDOUT | H-A (to {splits.MODEL_CUTOFF.date()}) | H-B (after) |")
    L.append("|---|---|---|---|---|")
    L.append(_row("trades", [dh.get("trades"), hh.get("trades"), ha.get("trades"), hb.get("trades")], "{}"))
    L.append(_row("net (USD)", [dh.get("net"), hh.get("net"), ha.get("net"), hb.get("net")], "{:+,.2f}"))
    L.append(_row("return %", [dh.get("return_pct"), hh.get("return_pct"), None, None], "{:.2f}"))
    L.append(_row("PF", [dh.get("pf"), hh.get("pf"), ha.get("pf"), hb.get("pf")], "{:.2f}"))
    L.append(_row("expectancy (USD)", [dh.get("expectancy"), hh.get("expectancy"), None, None], "{:.2f}"))
    wr = [None if not isinstance(x, (int, float)) or isinstance(x, bool) else 100 * x
          for x in (dh.get("win_rate"), hh.get("win_rate"))]
    L.append(_row("win rate %", wr + [None, None], "{:.1f}"))
    L.append(_row("max DD %", [dh.get("max_dd_pct"), hh.get("max_dd_pct"), None, None], "{:.2f}"))
    L.append(_row("daily Sharpe", [dh.get("daily_sharpe"), hh.get("daily_sharpe"), None, None], "{:.2f}"))
    L += ["", "## Gates", ""]
    gate_sc = r.holdout or r.dev
    if gate_sc:
        L += ["| Gate | Name | Result | Decisive |", "|---|---|---|---|"]
        for gid, name, result, decisive in cli.gate_rows(gate_sc):
            L.append(f"| {gid} | {name} | {result} | {cli.clean_text(decisive, 120)} |")
    else:
        L.append("No scorecard.")
    cm = dict(dev.get("cost_model") or ho.get("cost_model") or {})
    L += ["", "## Cost model", ""]
    for k in ("spread_model", "commission_model", "commission_rate", "slippage_pips", "round_trip_bps",
              "slippage_points", "fx_desc"):
        L.append(f"- {k}: {cli.clean_text(cm.get(k, 'n/a'), 120)}")
    L.append(f"- break-even cost multiplier (DEV): {_f(cm.get('breakeven_cost_mult'), '{:g}')}x "
             f"({cli.clean_text(cm.get('breakeven_flag') or 'n/a', 20)}; searched "
             f"{gates.BREAKEVEN_MULT_RANGE[0]:g}-{gates.BREAKEVEN_MULT_RANGE[1]:g}x)")
    L += ["", "## Critic", ""]
    rv = r.review
    if rv:
        to = f" -> {rv.get('to')}" if rv.get("decision") == "DOWNGRADE" else ""
        L.append(f"Decision: {rv.get('decision')}{to}. The critic can only downgrade.")
        if rv.get("reason"):
            L += ["", cli.clean_text(rv["reason"], 1000)]
        for key, title in (("mismatches", "Spec/code mismatches"), ("smells", "Smells"),
                           ("next_tests", "Next tests")):
            items = rv.get(key) or []
            if items:
                L += ["", f"{title}:"] + [f"- {cli.clean_text(x, 500)}" for x in items]
        if rv.get("cost_sensitivity"):
            L += ["", f"Cost sensitivity: {cli.clean_text(rv['cost_sensitivity'], 500)}"]
    else:
        L.append("No valid critic review (the verdict stands as scored).")
    L += ["", "## Context (report only)", ""]
    ctx = dict(dev.get("context") or {})
    for k in ("median_hold_days", "financing_flag", "rollover_entry_share", "rollover_flag",
              "window_end_closes", "untradable_stop", "n_signals", "halted"):
        if k in ctx:
            L.append(f"- DEV {k}: {_f(ctx[k], '{:g}') if not isinstance(ctx[k], bool) else ctx[k]}")
    for w in ho.get("warnings") or []:
        L.append(f"- HOLDOUT warning: {cli.clean_text(w, 200)}")
    if r.noise:
        for n in r.noise:
            L.append(f"- noise ({n.get('file')}): {'ok' if n.get('ok') else 'failed'}; report {n.get('report')}")
            for k, v in (n.get("summary") or {}).items():
                L.append(f"  - {cli.clean_text(k, 40)}: {cli.clean_text(v, 80)}")
    else:
        L.append("- noise diagnosis: not available")
    L += ["", "## Protocol", ""]
    L.append(f"- gates {gates.GATES_VERSION} · protocol {splits.PROTOCOL_VERSION} · interfaces "
             f"{I.INTERFACES_VERSION} · lab phase {I.LAB_PHASE} · runner {RUNNER_VERSION}")
    L.append(f"- DEV {splits.DEV_START.date()}..{splits.DEV_END.date()} is {splits.IN_SAMPLE_LABEL}; "
             f"HOLDOUT from {splits.HOLDOUT_START.date()}, once per sha, H-A/H-B split at "
             f"{splits.MODEL_CUTOFF.date()} (the model's training cutoff)")
    L.append(f"- {splits.SEAL_NOTE}")
    L.append(f"- finalize recorded in the server ledger: {'yes' if r.finalize_recorded else 'no'}")
    L += ["", "## Files", ""]
    files = ["request.json", "spec.md", "strategy.py", "manifest.json", "build_log.json"]
    if r.final_sha:
        files.append(f"scorecards/dev_{r.final_sha[:12]}.json")
        if r.holdout:
            files.append(f"scorecards/holdout_{r.final_sha[:12]}.json")
    if r.review:
        files.append("review.json")
    L += [f"- `{x}`" for x in files]
    for sc in (r.dev, r.holdout):
        if sc and sc.get("result_file"):
            L.append(f"- result: `{cli.clean_text(sc['result_file'], 160)}` (sajim-results volume)")
    text = scrub_labels("\n".join(L) + "\n")
    if gates.contains_forbidden_label(text):          # pragma: no cover — the scrub above guarantees it
        raise ValueError("README would contain a forbidden label")
    return text


def gate_results_from_json(gates_json: Sequence[Mapping[str, Any]]) -> List[I.GateResult]:
    """Rebuild interfaces.GateResult objects from a scorecard's "gates" list (scorecard's own parser)."""
    from sajim_quant.lab import scorecard as scoring      # builder C's module; lazy

    return scoring.gate_results_from_json(list(gates_json or []))


# ── The run ─────────────────────────────────────────────────────────────────
def _print(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class RunnerDeps:
    """Everything the runner touches outside pure logic, injectable for tests."""

    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    root: Path = REPO_DIR
    find_exe: Callable[[Mapping[str, str]], Path] = find_claude_exe
    preflight: Callable[[Path, Mapping[str, str]], None] = preflight
    run_session: Callable[..., SessionOutcome] = run_session
    input_fn: Callable[[str], str] = input
    out: Callable[[str], None] = _print
    client_factory: Callable[..., Any] = cli.LabClient          # (base_url, lab_token=|judge_token=)
    read_judge_token: Callable[[Mapping[str, str]], str] = read_judge_token
    noise: Optional[Callable[[str, Path], Optional[Dict[str, Any]]]] = run_noise
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    lock_path: Optional[Path] = None


class LabRun:
    """One build: state shared by the steps of main()."""

    def __init__(self, deps: RunnerDeps, args: argparse.Namespace):
        self.d = deps
        self.args = args
        self.out = deps.out
        self.lock: Optional[RunLock] = None
        self.exe: Optional[Path] = None
        self.api_url = ""
        self.lab_token = ""
        self.lab_id = ""
        self.family = ""
        self.folder = Path()
        self.request: Dict[str, Any] = {}
        self._judge: Optional[str] = None

    # helpers
    def event(self, event: str, **fields: Any) -> None:
        cli.record_event(self.folder, "runner", event, **fields)

    def log(self) -> dict:
        return cli.load_build_log(self.folder, self.lab_id, self.family)

    def session_done(self, stage: str) -> bool:
        return any(e.get("event") == "session" and e.get("stage") == stage and not e.get("rate_limited")
                   and e.get("outcome") in COMPLETED_OUTCOMES for e in self.log().get("events", []))

    def holdout_touched(self) -> bool:
        """True once the runner asked for (or saved) any holdout in this build."""
        sc_dir = self.folder / "scorecards"
        if sc_dir.is_dir() and any(p.name.startswith("holdout_") for p in sc_dir.iterdir()):
            return True
        return any(e.get("event") == "holdout" and e.get("source") == "runner"
                   for e in self.log().get("events", []))

    def judge_client(self) -> Any:
        if self._judge is None:
            self._judge = self.d.read_judge_token(self.d.environ)
        return self.d.client_factory(self.api_url, judge_token=self._judge)

    def builder_client(self) -> Any:
        return self.d.client_factory(self.api_url, lab_token=self.lab_token)

    def write_request(self) -> None:
        cli.write_json_atomic(self.folder / "request.json", self.request)

    # steps
    def setup(self) -> None:
        a, env = self.args, self.d.environ
        root = Path(self.d.root)
        if a.resume:
            self.lab_id = a.resume
            if not re.fullmatch(gates.LAB_ID_PATTERN, self.lab_id):
                raise PreflightError("--resume needs a lab id")
            self.folder = cli.lab_folder(self.lab_id, root)
            try:
                self.request = cli.read_json(self.folder / "request.json")
            except (OSError, ValueError):
                raise PreflightError(f"{I.GENERATED_DIR}/{self.lab_id}/request.json is missing or invalid") from None
            self.family = self.request.get("family") or self.lab_id
        else:
            if not (a.idea and a.symbol and a.tf):
                raise PreflightError('usage: run "<idea>" SYMBOL TF (or --resume <lab_id>)')
            idea = a.idea.strip()
            if not idea or len(idea) > MAX_IDEA_CHARS:
                raise PreflightError(f"the idea must be 1..{MAX_IDEA_CHARS} characters")
            symbol, tf = a.symbol.strip().upper(), a.tf.strip().upper()
            from sajim_quant.lab import contract          # builder A's module; lazy
            problems = contract.check_universe(symbol, tf)
            if problems:
                raise PreflightError("; ".join(problems))
            gen = root / I.GENERATED_DIR
            if a.id:
                if not re.fullmatch(gates.LAB_ID_PATTERN, a.id):
                    raise PreflightError(f"--id must match {gates.LAB_ID_PATTERN}")
                if (gen / a.id).exists():
                    raise PreflightError(f"{a.id} already exists; use --resume {a.id}")
                self.lab_id = a.id
            else:
                self.lab_id = derive_lab_id(idea, symbol, exists=lambda x: (gen / x).exists())
            self.family = a.family or self.lab_id
            if not re.fullmatch(gates.LAB_ID_PATTERN, self.family):
                raise PreflightError(f"--family must match {gates.LAB_ID_PATTERN}")
            for what, value in (("--id", self.lab_id), ("--family", self.family)):
                if _forbidden_id(value):
                    raise PreflightError(f"{what} contains a forbidden label ({', '.join(gates.FORBIDDEN_LABELS)})")
            self.folder = cli.lab_folder(self.lab_id, root)
            self.request = {"schema": I.REQUEST_SCHEMA, "lab_id": self.lab_id, "family": self.family,
                            "description": idea, "symbol": symbol, "timeframe": tf,
                            "created_at": self.d.now().astimezone(timezone.utc).isoformat(),
                            "revision_notes": [], "approved_spec_sha256": None, "runner_version": RUNNER_VERSION}
        try:
            self.api_url, self.lab_token = cli.load_config(env)
        except cli.LabApiError as e:
            raise PreflightError(str(e)) from None
        if not judge_token_path(env).is_file():
            raise PreflightError("the judge token file %USERPROFILE%\\.sajim\\judge_token is missing")
        self.exe = self.d.find_exe(env)
        self.d.preflight(self.exe, self.child("spec"))
        tmp = Path(env.get("TEMP") or env.get("TMP") or os.environ.get("TEMP") or ".")
        self.lock = RunLock(self.d.lock_path or tmp / LOCK_NAME)
        self.lock.acquire()
        if not a.resume:
            if builds_today(root, self.d.now()) >= I.MAX_BUILDS_PER_DAY:
                raise PreflightError(f"daily cap reached ({I.MAX_BUILDS_PER_DAY} lab builds per UTC day)")
            self.folder.mkdir(parents=True, exist_ok=False)
            self.write_request()
            log = cli.new_build_log(self.lab_id, self.family)
            cli.add_event(log, "runner", "start", symbol=self.request["symbol"], timeframe=self.request["timeframe"],
                          claude_version_ok=True)
            cli.save_build_log(self.folder, log)
        else:
            self.event("resume")
        self.out(f"SAJIM LAB {self.lab_id}  {self.request.get('symbol')} {self.request.get('timeframe')}  "
                 f"(family {self.family})")

    def child(self, stage: str) -> Dict[str, str]:
        env = child_env(self.d.environ, lab_id=self.lab_id, stage=stage, lab_token=self.lab_token,
                        api_url=self.api_url)
        assert_secret_absent(env, self._judge)
        return env

    def session(self, stage: str) -> SessionOutcome:
        if self.holdout_touched():                    # backstop: no Claude session ever follows a holdout
            self.out(f"[{stage}] refused: a holdout was already requested for this build")
            raise _Stop(EXIT_SESSION)
        argv =claude_argv(self.exe, session_prompt(stage, self.lab_id),
                           settings=Path(self.d.root) / SETTINGS_RELPATH, max_turns=I.MAX_TURNS[stage])
        self.out(f"[{stage}] Claude session started (max {I.MAX_TURNS[stage]} turns, "
                 f"{I.SESSION_WALL_TIMEOUT_S // 60} min wall clock)")

        def progress(ev: dict) -> None:
            if ev.get("type") == "result":
                self.out(f"[{stage}] result: {ev.get('subtype')} after {ev.get('num_turns')} turns")
            elif ev.get("type") == "rate_limit_event":
                self.out(f"[{stage}] rate-limit event")

        o = self.d.run_session(argv, self.child(stage), Path(self.d.root),
                               wall_timeout_s=I.SESSION_WALL_TIMEOUT_S, on_event=progress)
        self.event("session", stage=stage, outcome=o.outcome, subtype=o.subtype, num_turns=o.num_turns,
                   duration_ms=o.duration_ms, notional_cost_usd=o.notional_cost_usd, rate_limited=o.rate_limited,
                   rate_limit_status=o.rate_limit_status, api_key_source=o.api_key_source)
        self.out(f"[{stage}] session ended: {o.outcome} ({o.num_turns} turns)")
        if o.outcome == OUTCOME_INIT_REFUSED:
            self.out(f"REFUSED: {o.refusal}. The session was killed.")
            raise _Stop(EXIT_REFUSED)
        if o.rate_limited:
            when = f"; resets at {o.rate_limit_resets_at}" if o.rate_limit_resets_at else ""
            self.out(f"PAUSED: Claude reported a usage limit (status {o.rate_limit_status}{when}). "
                     f"Not retrying. Resume later with: python -X utf8 -m sajim_quant.lab.run --resume {self.lab_id}")
            self.event("paused", stage=stage, reason="rate_limit")
            raise _Stop(EXIT_SESSION)
        return o

    def spec_stage(self) -> None:
        if self.request.get("approved_spec_sha256"):
            return
        while True:
            o = self.session("spec")
            spec = self.folder / "spec.md"
            if not o.ok or not spec.is_file():
                self.out(f"spec session did not produce spec.md ({o.outcome}); stopping")
                raise _Stop(EXIT_SESSION)
            raw = spec.read_bytes()
            self.out("=" * 72)
            self.out(terminal_safe(raw.decode("utf-8", errors="replace")))
            self.out("=" * 72)
            can_revise = len(self.request.get("revision_notes") or []) < MAX_SPEC_REVISIONS
            decision, note = ("approve", "") if self.args.yes else self.ask_approval(can_revise)
            if decision == "approve":
                self.request["approved_spec_sha256"] = hashlib.sha256(raw).hexdigest()
                self.request["approved_spec_head_sha256"] = spec_head_sha256(raw)
                self.write_request()
                self.event("approval", decision="approve", auto=bool(self.args.yes))
                return
            if decision == "cancel":
                self.event("approval", decision="cancel")
                self.out("Cancelled. Nothing was evaluated.")
                raise _Stop(EXIT_CANCELLED)
            self.request.setdefault("revision_notes", []).append(note)
            self.write_request()
            self.event("approval", decision="revise", revision=len(self.request["revision_notes"]))

    def ask_approval(self, can_revise: bool) -> Tuple[str, str]:
        prompt = "Approve / Revise <note> / Cancel [a/r/c]: "
        for _ in range(20):
            try:
                answer = self.d.input_fn(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                return "cancel", ""
            word, _, rest = answer.partition(" ")
            word = word.lower()
            if word in ("a", "approve"):
                return "approve", ""
            if word in ("c", "cancel"):
                return "cancel", ""
            if word in ("r", "revise"):
                if not can_revise:
                    self.out(f"revision limit reached ({MAX_SPEC_REVISIONS}); approve or cancel")
                    continue
                note = rest.strip()
                if not note:
                    try:
                        note = self.d.input_fn("Revision note: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        return "cancel", ""
                if note:
                    return "revise", note[:MAX_NOTE_CHARS]
                continue
            self.out("please answer a, r <note>, or c")
        return "cancel", ""

    def code_stage(self) -> None:
        # On --resume the code step counts as complete only if it ended and left a DEV scorecard.
        if self.session_done("code") and cli.newest_dev_scorecard(self.folder) is not None:
            return
        o = self.session("code")
        if not o.ok:
            self.out(f"code session ended with {o.outcome}; judging whatever DEV scorecard exists")

    def finish_pending(self) -> None:
        """A DEV job the builder left pending (flaky link): finish it exactly like `lab.cli status`."""
        pending = self.log().get("pending")
        if not pending:
            return
        self.out("finishing the builder's pending lab job")
        ctx = cli._Ctx(self.lab_id, self.folder, self.out,
                       lambda **kw: self.d.client_factory(self.api_url, lab_token=self.lab_token, **kw))
        rc = cli.resume_pending(ctx)
        if rc not in (cli.EXIT_OK, cli.EXIT_FAILED):
            self.out(f"could not finish the pending job (exit {rc}); resume later with --resume {self.lab_id}")
            raise _Stop(EXIT_SERVER)

    def final_scorecard(self) -> Tuple[dict, List[str]]:
        warnings: List[str] = []
        final_sha = self.log().get("final_sha")
        sc = None
        if isinstance(final_sha, str) and re.fullmatch(r"[0-9a-f]{64}", final_sha):
            sc = cli.load_dev_scorecard(self.folder, final_sha[:12])
            if sc is None:
                warnings.append(f"declared final sha {final_sha[:12]} has no DEV scorecard; "
                                f"judging the newest DEV-trialled sha instead")
        if sc is None:
            sc = cli.newest_dev_scorecard(self.folder)
        if sc is None:
            self.event("verdict", status="no_dev_scorecard")
            self.out("No DEV scorecard exists, so no verdict can be formed (the build session failed early).")
            raise _Stop(EXIT_SESSION)
        return sc, warnings

    def spec_check(self) -> str:
        """Record 'spec changed since approval' in request.json (the critic reads it) and build_log."""
        spec = self.folder / "spec.md"
        raw = spec.read_bytes() if spec.is_file() else None
        changed = spec_changed_since_approval(self.request, raw)
        if self.request.get("spec_changed_since_approval") != changed:
            self.request["spec_changed_since_approval"] = changed
            self.write_request()
        self.event("spec_check", spec_changed_since_approval=changed)
        if changed == "yes":
            self.out("NOTE: spec.md above '## 8. Trials' changed after approval (shown to the critic and in README)")
        return changed

    def critic_stage(self, sha: str) -> Optional[dict]:
        review, _ = cli.load_review(self.folder, lab_id=self.lab_id, code_sha256=sha)
        if self.session_done("review") and review is not None:
            return review
        if self.holdout_touched():
            # The critic must run BEFORE the holdout; once a holdout was requested no Claude session starts.
            self.out("a holdout was already requested for this build; the critic is not re-run")
            return review
        (self.folder / cli.REVIEW_FILE).unlink(missing_ok=True)
        o = self.session("review")
        review, problems = cli.load_review(self.folder, lab_id=self.lab_id, code_sha256=sha)
        if review is None:
            self.out(f"critic produced no valid review ({o.outcome}): {'; '.join(problems)[:300]}")
        self.event("critic", valid=review is not None,
                   decision=(review or {}).get("decision"), to=(review or {}).get("to"))
        return review

    def holdout_stage(self, dev_sc: dict, review: Optional[dict], code_matches: bool,
                      lint_ok: bool) -> Tuple[Optional[dict], List[str], bool]:
        """(holdout scorecard or None, reasons it did not run, holdout_burned)."""
        sha = dev_sc["code_sha256"]
        existing = self.folder / "scorecards" / f"holdout_{sha[:12]}.json"
        if existing.is_file():
            try:
                return cli.read_json(existing), [], bool(dev_sc.get("holdout_burned"))
            except ValueError:
                pass
        burned = bool(dev_sc.get("holdout_burned"))
        blockers: List[str] = []
        if dev_sc.get("dev_hard_gates_passed") is not True:
            blockers.append("not every hard DEV gate passed")
        if review and review.get("decision") == "DOWNGRADE" and review.get("to") == gates.REJECT:
            blockers.append("the critic downgraded to REJECT (no holdout touch spent)")
        if burned:
            blockers.append("the family's holdout is burned")
        if self.args.no_holdout:
            blockers.append("--no-holdout was given")
        if not code_matches:
            blockers.append("strategy.py on disk no longer matches the judged sha")
        if not lint_ok:
            blockers.append("the local re-lint failed")
        if blockers:
            return None, blockers, burned
        client = self.judge_client()
        try:
            cli.stamp_manifest(self.folder)
            body = cli.build_body(self.folder, stage=splits.HOLDOUT, kind="holdout")
            started = client.evaluate(body)
        except cli.LabApiError as e:
            return self._holdout_refused(client, dev_sc, e)
        self.out(f"[holdout] sealed holdout job {started.get('job_id')} (touch {started.get('holdout_touch')})")
        self.event("holdout", status="started", job_id=str(started.get("job_id")),
                   touch=started.get("holdout_touch"))
        try:
            job = client.wait(str(started.get("job_id")), timeout=HOLDOUT_WAIT_S,
                              on_change=lambda s: self.out(f"  holdout status: {s}"))
        except cli.LabApiError as e:
            self.out(f"holdout poll failed: {cli.clean_text(e, 200)}; resume with --resume {self.lab_id}")
            raise _Stop(EXIT_SERVER)
        result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
        sc = result.get("scorecard")
        if job.get("status") != "done" or not result.get("success") or not isinstance(sc, Mapping) \
                or sc.get("stage") != splits.HOLDOUT or sc.get("code_sha256") != sha:
            self.event("holdout", status="failed", job_id=str(started.get("job_id")))
            self.out(f"holdout job failed: {cli.clean_text(job.get('error') or 'unexpected result', 200)}; "
                     f"resume with --resume {self.lab_id} (same request id, no new touch)")
            raise _Stop(EXIT_SERVER)
        cli.save_scorecard(self.folder, sc)
        # build_log stays free of holdout numbers/verdicts (defence in depth: Claude may read it).
        self.event("holdout", status="done", job_id=str(started.get("job_id")))
        return dict(sc), [], burned

    def _holdout_refused(self, client: Any, dev_sc: dict, e: cli.LabApiError) -> Tuple[Optional[dict], List[str], bool]:
        sha12 = dev_sc["code_sha256"][:12]
        self.event("holdout", status="refused", http_status=e.status, code=e.code)
        if e.status == I.HTTP_HOLDOUT_ALREADY_RUN:
            try:
                sc = client.scorecard(self.family, sha12, splits.HOLDOUT)
            except cli.LabApiError:
                sc = None
            if isinstance(sc, Mapping) and sc.get("code_sha256") == dev_sc["code_sha256"]:
                cli.save_scorecard(self.folder, sc)
                return dict(sc), [], bool(dev_sc.get("holdout_burned"))
            self.out("the holdout already ran for this sha but its scorecard is unavailable; no verdict can be formed")
            raise _Stop(EXIT_BUDGET)
        if e.status == I.HTTP_BUDGET_EXHAUSTED:
            return None, ["the family's holdout touches are used up (holdout burned)"], True
        if e.status == I.HTTP_DEV_NOT_PASSED:
            return None, ["the server has no passing DEV scorecard for this sha"], bool(dev_sc.get("holdout_burned"))
        if e.status in (I.HTTP_UNAUTHORIZED, I.HTTP_WRONG_TOKEN_SCOPE):
            self.out(f"holdout refused: {cli.clean_text(e, 200)} (check the judge token configuration)")
            raise _Stop(EXIT_REFUSED)
        self.out(f"holdout request failed: {cli.clean_text(e, 200)}; resume with --resume {self.lab_id}")
        raise _Stop(EXIT_SERVER)

    def noise_stage(self, scorecards: Sequence[Optional[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if self.d.noise is None:
            return out
        for sc in scorecards:
            f = (sc or {}).get("result_file")
            if not isinstance(f, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}_result\.json", f):
                continue
            try:
                res = self.d.noise(f, Path(self.d.root))
            except Exception as e:  # noqa: BLE001 — report-only: a noise failure never changes the verdict
                logger.warning("noise hand-off failed for %s: %s", f, type(e).__name__)
                res = {"file": f, "ok": False, "report": "n/a", "summary": {}}
            if res:
                out.append(res)
        return out

    def family_counts(self, dev_sc: Mapping[str, Any], ho_sc: Optional[Mapping[str, Any]]) -> Tuple[Any, Any, Any]:
        src = ho_sc or dev_sc
        trials, touches, glob = dev_sc.get("n_trials_family"), src.get("holdout_touches"), src.get("n_trials_global")
        try:
            fam = self.judge_client().family(self.family)
            trials = fam.get("n_trials", trials)
            touches = fam.get("holdout_touches", touches)
            glob = fam.get("n_trials_global", glob)
        except (cli.LabApiError, PreflightError):
            pass
        return trials, touches, glob

    def execute(self) -> int:
        self.setup()
        self.spec_stage()
        self.code_stage()
        self.finish_pending()
        dev_sc, warnings = self.final_scorecard()
        sha = dev_sc["code_sha256"]
        problems = cli.local_problems(self.folder, self.lab_id)
        local_sha = cli.sha256_file(self.folder / "strategy.py") if (self.folder / "strategy.py").is_file() else ""
        code_matches = local_sha == sha
        if problems:
            warnings.append("local re-lint failed: " + "; ".join(problems)[:300])
        spec_changed = self.spec_check()            # before the critic: it is part of the critic's input
        review = self.critic_stage(sha)
        # ---- no Claude session runs after this point; the judge token is read only from here on ----
        try:
            full = self.judge_client().scorecard(self.family, sha[:12], splits.DEV)
            if isinstance(full, Mapping) and full.get("code_sha256") == sha and full.get("stage") == splits.DEV:
                dev_sc = dict(full)
        except cli.LabApiError as e:
            logger.warning("full DEV scorecard unavailable (%s); using the local copy", e.code)
        ho_sc, blockers, burned = self.holdout_stage(dev_sc, review, code_matches, not problems)
        noise = self.noise_stage([dev_sc, ho_sc])
        if ho_sc is not None:
            verdict, reasons, source = str(ho_sc.get("verdict")), list(ho_sc.get("reasons") or []), "scorecard"
        else:
            from sajim_quant.lab import scorecard as scoring      # builder C's module; lazy
            oc = scoring.verdict(gate_results_from_json(dev_sc.get("gates") or []), splits.DEV,
                                 budget_exhausted=True, holdout_burned=burned)
            verdict, reasons, source = oc.verdict, list(oc.reasons), "budget"
        if review and review.get("decision") == "DOWNGRADE":
            after = gates.apply_downgrade(verdict, review.get("to"))
            if after != verdict:
                reasons.append(f"critic downgrade: {verdict} -> {after}")
                verdict, source = after, "critic"
        finalize_ok = self.finalize(sha, verdict, source, review)
        trials, touches, glob = self.family_counts(dev_sc, ho_sc)
        manifest = {}
        try:
            manifest = cli.read_json(self.folder / "manifest.json")
        except (OSError, ValueError):
            pass
        inputs = ReadmeInputs(
            lab_id=self.lab_id, family=self.family, name=str(manifest.get("name") or self.lab_id),
            description=str(self.request.get("description") or ""), symbol=str(self.request.get("symbol")),
            timeframe=str(self.request.get("timeframe")), verdict=verdict, reasons=reasons, dev=dev_sc,
            holdout=ho_sc, review=review, trials_used=trials, holdout_touches=touches, global_trials=glob,
            final_sha=sha, holdout_note="; ".join(blockers), warnings=warnings, noise=noise,
            finalize_recorded=finalize_ok, spec_changed=spec_changed)
        cli.write_bytes_atomic(self.folder / "README.md", render_readme(inputs).encode("utf-8"))
        self.event("readme", finalize_recorded=finalize_ok)
        self.out(f"VERDICT {verdict}  ({self.folder.relative_to(Path(self.d.root)).as_posix()}/README.md)")
        if not finalize_ok:
            self.out(f"the verdict was not recorded on the server; re-run with --resume {self.lab_id}")
            return EXIT_SERVER
        return EXIT_OK

    def finalize(self, sha: str, verdict: str, source: str, review: Optional[dict]) -> bool:
        body = {"lab_id": self.lab_id, "family": self.family, "code_sha256": sha,
                "request_id": I.derive_request_id(self.family, sha, "finalize"),
                "verdict": verdict, "source": source, "review": review}
        try:
            self.judge_client().finalize(body)
        except (cli.LabApiError, PreflightError) as e:
            self.event("finalize", ok=False, code=getattr(e, "code", "preflight"))
            self.out(f"finalize failed: {cli.clean_text(e, 200)}")
            return False
        self.event("finalize", ok=True)
        return True

    def release(self) -> None:
        if self.lock is not None:
            self.lock.release()


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sajim_quant.lab.run", description="SAJIM Strategy Lab runner")
    ap.add_argument("idea", nargs="?", help="the strategy idea in plain English (quoted)")
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("tf", nargs="?")
    ap.add_argument("--id", default=None)
    ap.add_argument("--family", default=None)
    ap.add_argument("--yes", action="store_true", help="approve spec.md without asking")
    ap.add_argument("--no-holdout", action="store_true", help="never touch the sealed holdout")
    ap.add_argument("--resume", default=None, metavar="LAB_ID")
    return ap


def main(argv: Optional[Sequence[str]] = None, *, deps: Optional[RunnerDeps] = None) -> int:
    deps = deps or RunnerDeps()
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return EXIT_REFUSED if e.code else EXIT_OK
    run = LabRun(deps, args)
    try:
        return run.execute()
    except PreflightError as e:
        deps.out(f"REFUSED: {cli.clean_text(e, 500)}")
        return EXIT_REFUSED
    except _Stop as s:
        return s.code
    finally:
        run.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
