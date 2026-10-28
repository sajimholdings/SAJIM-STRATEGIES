"""
Strategy Lab PreToolUse hook for Skill.

A lab session needs no skill beyond the one the runner starts it with (lab-build or
lab-review, both `disable-model-invocation: true`). Any other skill visible to the
session (the repo's backtest skill, user or plugin skills, some of which run shell
commands through `!` injection) would add instructions or tools the other guards were
not written for, so every Skill call is denied unless it names a lab skill.
"""

from __future__ import annotations

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

LAB_SKILLS = ("lab-build", "lab-review")


def decide(event: Dict[str, Any], root: Path) -> Optional[str]:
    tool = event["tool_name"]
    if tool != "Skill":
        return f"unexpected tool {tool!r} for the skill guard"
    stage_from_env()
    name = event["tool_input"].get("skill")
    if not isinstance(name, str) or name.strip().lstrip("/") not in LAB_SKILLS:
        return "only the lab-build and lab-review skills are available in lab sessions"
    return None


if __name__ == "__main__":
    raise SystemExit(run_pre_tool_hook(decide))
