"""
Fingerprint of the code that `modal deploy` snapshots into the cloud image.

The deployed app bakes this value in (env SAJIM_DEPLOY_ID) and returns it with every
job, so callers can tell when the cloud runs older strategy/engine code than the
working tree. sim/src/lib/modalApi.ts implements the same algorithm — keep them in sync.

Algorithm: for every file under MOUNTED_DIRS whose suffix is in CODE_SUFFIXES and no
path segment starts with "." or equals "__pycache__", sorted by repo-relative POSIX
path, sha256-update(path + "\\0" + file bytes + "\\0"); first 12 hex chars.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent.parent

# Must match the add_local_dir mounts in modal_runner.py.
MOUNTED_DIRS = (
    "strategies/backtrader-pullback-window-xauusd",
    "strategies/BEEP_MASTER_PACKAGE",
    "strategies/smc_daemon_project",
    "strategies/GoldOrb",
    "sajim_quant",
)
CODE_SUFFIXES = (".py", ".json")


def _skip(rel_parts: tuple[str, ...]) -> bool:
    return any(p.startswith(".") or p == "__pycache__" for p in rel_parts)


def code_fingerprint(repo_dir: Path = REPO_DIR) -> str:
    files: list[tuple[str, Path]] = []
    for d in MOUNTED_DIRS:
        base = repo_dir / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in CODE_SUFFIXES:
                continue
            rel = f.relative_to(repo_dir)
            if _skip(rel.parts):
                continue
            files.append((rel.as_posix(), f))
    h = hashlib.sha256()
    for rel, f in sorted(files, key=lambda x: x[0]):
        h.update(rel.encode("utf-8") + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


if __name__ == "__main__":
    print(code_fingerprint())
