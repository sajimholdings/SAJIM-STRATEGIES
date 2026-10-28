"""
Strategy Lab PreToolUse hook for Read | Grep | Glob. Default deny.

Readable roots:
  sajim_quant/lab/                     contract, indicators, gates (the lab engine)
  .claude/skills/lab-build/            the builder skill and its bundled files
  .claude/skills/lab-review/
  .claude/agents/strategy-critic.md
  strategies/generated/$SAJIM_LAB_ID/  this build's own folder

Always denied, even inside those roots: any path part starting with ".env",
~/.sajim/** (tokens), ~/.claude/** (credentials), data/** (market data, results),
sim/** (dashboard + its .env.local), other strategy folders, the build's
scorecards/holdout_* and README.md. Grep/Glob without a path search the repo root
and are denied; a search over the build folder (or its scorecards/) is denied once
a holdout scorecard exists there. Glob patterns and Grep globs may not be
absolute or contain "..".
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.dont_write_bytecode = True        # never leave __pycache__ in .claude/lab/hooks
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _common import (  # noqa: E402
        HookError, home_dir, is_within, lab_dir, lab_id_from_env, norm, resolve_tool_path,
        run_pre_tool_hook,
    )
except Exception:                      # fail closed: exit 2 blocks the tool call
    sys.stderr.write("Strategy Lab: lab hook helpers unavailable; denied\n")
    raise SystemExit(2)

READ_TOOLS = ("Read", "Grep", "Glob")
#: a pattern (or any {a,b} alternative of it) that starts at a root, a UNC share or home
_ANCHORED_ALTERNATIVE = re.compile(r"(?:^|[{,])\s*[/\\~]")


def _allowed_roots(root: Path, folder: Path) -> List[Path]:
    return [
        (root / "sajim_quant" / "lab").resolve(strict=False),
        (root / ".claude" / "skills" / "lab-build").resolve(strict=False),
        (root / ".claude" / "skills" / "lab-review").resolve(strict=False),
        folder,
    ]


def _critic_file(root: Path) -> Path:
    return (root / ".claude" / "agents" / "strategy-critic.md").resolve(strict=False)


def _holdout_scorecards_exist(folder: Path) -> bool:
    sc = folder / "scorecards"
    try:
        return any(p.name.lower().startswith("holdout_") for p in sc.iterdir())
    except OSError:
        return False


def _check_pattern(value: Any, what: str) -> None:
    """Glob pattern / Grep glob: relative, no '..', no drive or home anchor."""
    if value is None:
        return
    if not isinstance(value, str):
        raise HookError(f"{what} must be a string")
    v = value.strip()
    if ".." in v:
        raise HookError(f"{what} may not contain '..'")
    # every brace alternative counts as a pattern start: "{C:/Users/**,x}" or "{~/.sajim/*,x}"
    # would otherwise slip an absolute or home-anchored pattern past a check of v[0] only
    if ":" in v or _ANCHORED_ALTERNATIVE.search(v):
        raise HookError(f"{what} must be relative to the searched folder")


def _always_denied(target: Path, root: Path, folder: Path, lab_id: str) -> Optional[str]:
    if any(part.lower().startswith(".env") for part in target.parts):
        return ".env files hold secrets"
    home = home_dir()
    if is_within(target, home / ".sajim"):
        return "~/.sajim holds the lab and judge tokens"
    if is_within(target, home / ".claude"):
        return "~/.claude holds Claude credentials and user settings"
    if is_within(target, (root / "data").resolve(strict=False)):
        return "data/** (market data and results) is server-only for the lab"
    if is_within(target, (root / "sim").resolve(strict=False)):
        return "sim/** (dashboard and its env files) is off-limits"
    strategies = (root / "strategies").resolve(strict=False)
    if is_within(target, strategies) and not is_within(target, folder):
        return f"only this build's folder strategies/generated/{lab_id}/ is readable"
    if is_within(target, folder) and norm(target) != norm(folder):
        rel = norm(target)[len(norm(folder).rstrip("\\/")) + 1:].lower().split(os.sep)
        if rel[0] == "scorecards" and len(rel) > 1 and rel[1].startswith("holdout_"):
            return "holdout scorecards are never shown to a Claude session"
        if rel == ["readme.md"]:
            return "README.md is written by the runner after the build and is not an input"
    return None


def decide(event: Dict[str, Any], root: Path) -> Optional[str]:
    tool = event["tool_name"]
    if tool not in READ_TOOLS:
        return f"unexpected tool {tool!r} for the read guard"
    lab_id = lab_id_from_env()
    folder = lab_dir(root, lab_id)
    if not is_within(folder, root):
        raise HookError("the lab folder resolves outside the repository")
    ti = event["tool_input"]
    cwd = event.get("cwd")
    if tool == "Read":
        target = resolve_tool_path(ti.get("file_path"), cwd)
    else:
        raw = ti.get("path")
        if raw is None or raw == "":
            if not isinstance(cwd, str) or not cwd:
                raise HookError("search without a path or session cwd")
            raw = cwd
        target = resolve_tool_path(raw, cwd)
        _check_pattern(ti.get("pattern") if tool == "Glob" else ti.get("glob"),
                       "Glob pattern" if tool == "Glob" else "Grep glob")

    why = _always_denied(target, root, folder, lab_id)
    if why:
        return why
    in_scope = any(is_within(target, r) for r in _allowed_roots(root, folder)) \
        or norm(target) == norm(_critic_file(root))
    if not in_scope:
        return ("outside the lab read scope (sajim_quant/lab/, the lab skills, the critic agent, "
                f"strategies/generated/{lab_id}/); Grep/Glob need an explicit path inside it")
    if tool in ("Grep", "Glob") and _holdout_scorecards_exist(folder):
        scorecards = folder / "scorecards"
        if norm(target) in (norm(folder), norm(scorecards)):
            return "a holdout scorecard exists here; search specific files instead of the whole folder"
    return None


if __name__ == "__main__":
    raise SystemExit(run_pre_tool_hook(decide))
