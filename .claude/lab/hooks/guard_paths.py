"""
Strategy Lab PreToolUse hook for Edit | Write | MultiEdit | NotebookEdit.

Allows a write only when the resolved target is
strategies/generated/$SAJIM_LAB_ID/<name> with <name> in LAB_WRITABLE[$SAJIM_LAB_STAGE]:
  spec   -> spec.md
  code   -> spec.md, strategy.py, manifest.json
  review -> review.json
Everything else is denied, including NotebookEdit, traversal (..), junction escapes,
drive-relative / UNC / alternate-data-stream paths and a missing lab environment.
In the code stage spec.md is the approved pre-registration: a write is allowed only
when sections 1-7 (everything above the "## 8. Trials" heading) stay unchanged, so
DEV results can never be followed by a silent rewrite of the rules or PARAMS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True        # never leave __pycache__ in .claude/lab/hooks
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _common import (  # noqa: E402
        LAB_WRITABLE, HookError, is_within, lab_dir, lab_id_from_env, norm, resolve_tool_path,
        run_pre_tool_hook, stage_from_env,
    )
except Exception:                      # fail closed: exit 2 blocks the tool call
    sys.stderr.write("Strategy Lab: lab hook helpers unavailable; denied\n")
    raise SystemExit(2)

WRITE_TOOLS = ("Edit", "Write", "MultiEdit")
#: the spec.md "Trials" heading; everything above it is the approved pre-registration
_TRIALS_HEADING = re.compile(r"^##\s*8\.", re.M)


def _norm_text(text: str) -> str:
    return text.lstrip("﻿").replace("\r\n", "\n")


def _registered_part(text: str) -> str:
    """spec.md up to its '## 8. Trials' heading (the whole text when there is none)."""
    m = _TRIALS_HEADING.search(text)
    return text[:m.start()] if m else text


def _spec_after_write(tool: str, ti: Dict[str, Any], current: str) -> str:
    """The spec.md text this tool call would produce (Edit / MultiEdit applied verbatim)."""
    if tool == "Write":
        content = ti.get("content")
        if not isinstance(content, str):
            raise HookError("Write without string content")
        return _norm_text(content)
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    if not isinstance(edits, list) or not edits:
        raise HookError(f"{tool} without edits")
    text = current
    for e in edits:
        old, new = (e.get("old_string"), e.get("new_string")) if isinstance(e, dict) else (None, None)
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise HookError(f"{tool} needs old_string and new_string")
        old, new = _norm_text(old), _norm_text(new)
        if old not in text:
            raise HookError("old_string is not in spec.md verbatim; Read spec.md again and edit only "
                            "section 8 (Trials)")
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def _spec_change_reason(tool: str, ti: Dict[str, Any], target: Path) -> Optional[str]:
    """
    Code stage: spec.md is the approved pre-registration. Only its section 8 (Trials) may
    change; sections 1-7 must stay byte-for-byte as approved (line endings aside).
    """
    try:
        current = _norm_text(target.read_text(encoding="utf-8"))
    except OSError:
        return "spec.md must already exist in the code stage (it is the approved spec)"
    except UnicodeDecodeError:
        return "spec.md is not valid UTF-8"
    approved = _registered_part(current)
    after = _spec_after_write(tool, ti, current)
    if not after.startswith(approved) or (approved != current
                                          and not _TRIALS_HEADING.match(after, len(approved))):
        return ("the code stage may change only section 8 (Trials) of spec.md; sections 1-7 are the "
                "approved pre-registration and must stay as approved")
    return None


def decide(event: Dict[str, Any], root: Path) -> Optional[str]:
    tool = event["tool_name"]
    if tool == "NotebookEdit":
        return "NotebookEdit is not available in lab sessions"
    if tool not in WRITE_TOOLS:
        return f"unexpected tool {tool!r} for the write guard"
    lab_id = lab_id_from_env()
    stage = stage_from_env()
    allowed = LAB_WRITABLE[stage]
    folder = lab_dir(root, lab_id)
    if not is_within(folder, root):
        raise HookError("the lab folder resolves outside the repository")
    target = resolve_tool_path(event["tool_input"].get("file_path"), event.get("cwd"))
    if norm(target.parent) != norm(folder) or target.name not in allowed:
        names = ", ".join(allowed)
        return (f"the {stage} stage may write only strategies/generated/{lab_id}/{{{names}}}; "
                f"denied {target}")
    if stage == "code" and target.name == "spec.md":
        return _spec_change_reason(tool, event["tool_input"], target)
    return None


if __name__ == "__main__":
    raise SystemExit(run_pre_tool_hook(decide))
