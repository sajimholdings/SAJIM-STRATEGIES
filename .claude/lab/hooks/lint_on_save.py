"""
Strategy Lab PostToolUse hook for Edit | Write | MultiEdit.

After a write to strategies/generated/$SAJIM_LAB_ID/strategy.py or manifest.json it
runs sajim_quant.lab.contract.lint_path on the saved file. Violations go to stderr,
one per line, with exit code 2 (Claude Code feeds them back to the model); a clean
file exits 0 silently. Any other file is ignored.

PostToolUse cannot undo a write, so this is feedback, not a guard: `lab.cli done`
and the server re-lint. If the linter cannot be imported or the file cannot be
read, the hook reports it and exits 2 (fails closed: the model is told the file is
unchecked).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True        # never leave __pycache__ in .claude/lab/hooks
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    LAB_ID_RE, HookError, lab_dir, norm, project_root, read_event, resolve_tool_path,
)

WRITE_TOOLS = ("Edit", "Write", "MultiEdit")
LINTED = ("strategy.py", "manifest.json")


def _fail(message: str) -> int:
    sys.stderr.write(f"Strategy Lab lint: {message}\n")
    return 2


def main() -> int:
    try:
        root = project_root()
        event = read_event()
        if event["tool_name"] not in WRITE_TOOLS:
            return 0
        lab_id = os.environ.get("SAJIM_LAB_ID", "")
        if not LAB_ID_RE.fullmatch(lab_id):
            return 0
        target = resolve_tool_path(event["tool_input"].get("file_path"), event.get("cwd"))
    except HookError as exc:
        return _fail(f"could not interpret the write event ({exc})")
    except Exception as exc:                       # never crash silently
        return _fail(f"internal hook error ({type(exc).__name__})")

    if norm(target.parent) != norm(lab_dir(root, lab_id)) or target.name not in LINTED:
        return 0

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from sajim_quant.lab import contract
    except Exception as exc:
        return _fail(f"the contract linter (sajim_quant.lab.contract) is unavailable: "
                     f"{type(exc).__name__}: {exc}; {target.name} is UNCHECKED")
    try:
        problems = contract.lint_path(target)
    except OSError as exc:
        return _fail(f"cannot read {target.name} ({exc.strerror or exc})")
    except Exception as exc:
        return _fail(f"linter error on {target.name} ({type(exc).__name__}: {exc})")
    if not problems:
        return 0
    for line in problems:
        sys.stderr.write(line + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
