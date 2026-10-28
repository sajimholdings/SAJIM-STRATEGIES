"""
Shared helpers for the Strategy Lab hooks (.claude/lab/hooks/*.py).

The hooks are the real guard of a lab session (permission rules are text matches),
so everything here fails CLOSED: malformed stdin, a missing lab env var, an odd
path shape or an internal error all end in a deny. A PreToolUse hook never emits
"allow" (that would bypass the permission rules); it prints nothing to allow.

Self-contained on purpose: stdlib only, no sajim_quant import (that would pull in
numpy/pandas on every tool call). The constants below duplicate
sajim_quant.lab.gates.LAB_ID_PATTERN and sajim_quant.lab.interfaces.LAB_WRITABLE;
tests/lab/test_hooks.py pins them to the originals.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

sys.dont_write_bytecode = True        # a hook process never writes bytecode next to the hooks

#: == sajim_quant.lab.gates.LAB_ID_PATTERN
LAB_ID_RE = re.compile(r"^lab_[a-z0-9_]{3,40}$")
#: == sajim_quant.lab.interfaces.LAB_WRITABLE (files Claude may write, per stage)
LAB_WRITABLE: Mapping[str, Tuple[str, ...]] = {
    "spec": ("spec.md",),
    "code": ("spec.md", "strategy.py", "manifest.json"),
    "review": ("review.json",),
}
GENERATED_PARTS = ("strategies", "generated")
MAX_STDIN_BYTES = 4 * 1024 * 1024
IS_WINDOWS = os.name == "nt"


class HookError(Exception):
    """A refusal; str(exc) is the builder-facing reason."""


# ── Paths ───────────────────────────────────────────────────────────────────
def project_root() -> Path:
    """The repo that holds this hook: <root>/.claude/lab/hooks/<x>.py -> <root>."""
    return Path(__file__).resolve().parents[3]


def norm(p: Any) -> str:
    """Case-normalised absolute string for comparisons (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(str(p)))


def is_within(child: Path, parent: Path) -> bool:
    """child == parent or child is below parent (both already resolved)."""
    c, p = norm(child), norm(parent).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


def lab_dir(root: Path, lab_id: str) -> Path:
    return root.joinpath(*GENERATED_PARTS, lab_id).resolve(strict=False)


def home_dir() -> Path:
    return Path(os.path.expanduser("~")).resolve(strict=False)


def resolve_tool_path(raw: Any, cwd: Any) -> Path:
    """
    A tool's path argument as an absolute, resolved Path.

    Relative paths are joined to the event's cwd. Shapes that Windows resolves
    against hidden per-drive or per-process state are refused: drive-relative
    ("F:foo"), root-relative ("\\foo"), UNC / device paths ("\\\\server", "\\\\?\\")
    and alternate data streams ("file:stream").
    """
    if not isinstance(raw, str) or not raw:
        raise HookError("missing or empty path")
    if "\x00" in raw or raw != raw.strip():
        raise HookError("path has NUL or leading/trailing whitespace")
    text = raw
    if text == "~" or text.startswith(("~/", "~\\")):
        text = os.path.expanduser(text)
    p = Path(text)
    if IS_WINDOWS:
        if p.drive.startswith(("\\\\", "//")):
            raise HookError("UNC and device paths are not allowed")
        if p.drive and not p.root:
            raise HookError("drive-relative paths (like F:foo) are not allowed")
        if p.root and not p.drive:
            raise HookError("root-relative paths (like \\foo) are not allowed")
        if any(":" in part for part in p.parts[1:]):
            raise HookError("alternate data streams are not allowed")
    if not p.is_absolute():
        if not isinstance(cwd, str) or not cwd:
            raise HookError("relative path without a session cwd")
        base = Path(cwd)
        if not base.is_absolute() or (IS_WINDOWS and not (base.drive and base.root)):
            raise HookError("session cwd is not an absolute path")
        p = base / p
    return p.resolve(strict=False)


# ── Event and environment ───────────────────────────────────────────────────
def read_event() -> Dict[str, Any]:
    """The hook event JSON from stdin (a dict with tool_name and a tool_input dict)."""
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookError("hook event too large")
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise HookError("malformed hook event (not JSON)") from None
    if not isinstance(event, dict):
        raise HookError("malformed hook event (not an object)")
    if not isinstance(event.get("tool_name"), str):
        raise HookError("malformed hook event (no tool_name)")
    if not isinstance(event.get("tool_input"), dict):
        raise HookError("malformed hook event (no tool_input)")
    return event


def check_project_dir(root: Path) -> None:
    """CLAUDE_PROJECT_DIR (forward slashes on Windows) must be this hook's repo."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and norm(Path(env).resolve(strict=False)) != norm(root):
        raise HookError("CLAUDE_PROJECT_DIR is not the repository these lab hooks belong to")


def lab_id_from_env() -> str:
    lab_id = os.environ.get("SAJIM_LAB_ID", "")
    if not LAB_ID_RE.fullmatch(lab_id):
        raise HookError("SAJIM_LAB_ID is missing or invalid; lab sessions are started only by sajim_quant.lab.run")
    return lab_id


def stage_from_env() -> str:
    stage = os.environ.get("SAJIM_LAB_STAGE", "")
    if stage not in LAB_WRITABLE:
        raise HookError("SAJIM_LAB_STAGE is missing or invalid; lab sessions are started only by sajim_quant.lab.run")
    return stage


# ── Output ──────────────────────────────────────────────────────────────────
def deny_line(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"Strategy Lab: {reason}",
    }})


def run_pre_tool_hook(decide: Callable[[Dict[str, Any], Path], Optional[str]]) -> int:
    """
    Run a PreToolUse decision function. decide(event, root) returns a deny reason
    or None (allow = print nothing). Every failure path denies; exit code is 0.
    """
    try:
        root = project_root()
        event = read_event()
        check_project_dir(root)
        if event.get("hook_event_name", "PreToolUse") != "PreToolUse":
            raise HookError("this hook handles PreToolUse events only")
        reason = decide(event, root)
    except HookError as exc:
        reason = str(exc)
    except Exception as exc:                       # fail closed on any bug
        reason = f"internal hook error ({type(exc).__name__}); denied"
    if reason:
        sys.stdout.write(deny_line(reason) + "\n")
        sys.stdout.flush()
    return 0
