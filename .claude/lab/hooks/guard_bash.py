"""
Strategy Lab PreToolUse hook for Bash (and PowerShell, which is always denied).

A command is allowed only when it contains none of  ; | & > < ` $ ( )  or a
newline / carriage return, and its strip()ped text FULLY matches the pattern of
the current $SAJIM_LAB_STAGE:
  spec    nothing
  code    python -X utf8 -m sajim_quant.lab.cli check | dev | status | summary [--sha <sha12>]
          | done --final <sha12 or sha64>
  review  python -X utf8 -m sajim_quant.lab.cli summary [--sha <sha12>] | review --file review.json
Bash rules in settings.json are prefix text matches; this exact match is the guard.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True        # never leave __pycache__ in .claude/lab/hooks
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _common import run_pre_tool_hook, stage_from_env  # noqa: E402
except Exception:                      # fail closed: exit 2 blocks the tool call
    sys.stderr.write("Strategy Lab: lab hook helpers unavailable; denied\n")
    raise SystemExit(2)

METACHARACTERS = frozenset(";|&><`$()\n\r")
_CLI = r"python -X utf8 -m sajim_quant\.lab\.cli "
STAGE_PATTERNS: Dict[str, Optional[re.Pattern]] = {
    "spec": None,
    "code": re.compile(
        _CLI + r"(check|dev|status|summary( --sha [0-9a-f]{12})?"
               r"|done --final [0-9a-f]{12}(?:[0-9a-f]{52})?)"),
    "review": re.compile(_CLI + r"(summary( --sha [0-9a-f]{12})?|review --file review\.json)"),
}


def decide(event: Dict[str, Any], root: Path) -> Optional[str]:
    tool = event["tool_name"]
    if tool == "PowerShell":
        return "PowerShell is not available in lab sessions"
    if tool != "Bash":
        return f"unexpected tool {tool!r} for the shell guard"
    stage = stage_from_env()
    command = event["tool_input"].get("command")
    if not isinstance(command, str) or not command.strip():
        return "empty command"
    bad = sorted({c for c in command if c in METACHARACTERS})
    if bad:
        shown = " ".join(repr(c) for c in bad)
        return f"shell metacharacters are not allowed ({shown}); run one lab.cli command at a time"
    pattern = STAGE_PATTERNS[stage]
    if pattern is None:
        return "the spec stage runs no commands; write spec.md and stop"
    if not pattern.fullmatch(command.strip()):
        return (f"only `python -X utf8 -m sajim_quant.lab.cli <subcommand>` with a {stage}-stage "
                f"subcommand is allowed; see the lab skill")
    return None


if __name__ == "__main__":
    raise SystemExit(run_pre_tool_hook(decide))
