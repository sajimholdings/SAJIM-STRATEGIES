"""
SAJIM Strategy Lab — LocalSubprocessExecutor (INTERFACES.md §2.4).

Runs a SandboxJob in a local subprocess, in a fresh temp directory, with a minimal
environment and a hard job timeout. Used by tests and local development.

THIS IS NOT A SECURITY BOUNDARY. The child is an ordinary process on the host: it
has the host's network, can read any file the user can read and can outlive a
careless kill. Untrusted model-written code runs in production ONLY inside a fresh
``modal.Sandbox`` (ModalSandboxExecutor: no network, no secrets, no volumes, no
``modal`` package). Never use this executor on code you would not run yourself.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_io

logger = logging.getLogger(__name__)

STDERR_TAIL_CHARS = 4000


class LocalSubprocessExecutor:
    """SandboxExecutor backed by a local subprocess. NOT a security boundary (see module docstring)."""

    def __init__(self, python_exe: Optional[str] = None, *, base_dir: Optional[Path] = None,
                 keep_dirs: bool = False):
        self.python_exe = python_exe or sys.executable
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.keep_dirs = keep_dirs
        self.last_root: Optional[Path] = None

    def _env(self, work: Path) -> Dict[str, str]:
        """Minimal env: no SAJIM_* or any other inherited variable."""
        env = {
            "PATH": str(Path(self.python_exe).resolve().parent),
            "TEMP": str(work),
            "TMP": str(work),
            "PYTHONUTF8": "1",
        }
        if os.name == "nt" and os.environ.get("SYSTEMROOT"):
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]   # Windows needs it for crypto/sockets init
        return env

    def run(self, job: I.SandboxJob) -> I.SandboxResult:
        if self.base_dir is not None:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="sajim_lab_sb_",
                                     dir=str(self.base_dir) if self.base_dir else None)).resolve()
        self.last_root = root
        try:
            return self._run_in(root, job)
        finally:
            if not self.keep_dirs:
                _rmtree(root)

    def _run_in(self, root: Path, job: I.SandboxJob) -> I.SandboxResult:
        for rel, data in sandbox_io.encode_job(job).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        work, out = root / I.WORK_DIR, root / I.OUT_DIR
        work.mkdir()
        out.mkdir()
        argv = I.runner_argv(root.as_posix())
        argv[0] = self.python_exe
        stderr_path = root / "runner.stderr"
        t0 = time.monotonic()
        timed_out = False
        with open(stderr_path, "wb") as err_fh:
            proc = subprocess.Popen(argv, cwd=str(work), env=self._env(work), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=err_fh,
                                    start_new_session=(os.name != "nt"))
            try:
                proc.wait(timeout=job.job_timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
        wall = time.monotonic() - t0
        files = self._read_outputs(out, job)
        tail = _tail(stderr_path, STDERR_TAIL_CHARS)
        logger.debug("local sandbox %s: exit=%s timed_out=%s wall=%.2fs", job.label, proc.returncode,
                     timed_out, wall)
        return I.SandboxResult(files=files, exit_code=None if timed_out else proc.returncode,
                               timed_out=timed_out, wall_s=wall, stderr_tail=tail)

    @staticmethod
    def _read_outputs(out: Path, job: I.SandboxJob) -> Dict[str, bytes]:
        """Read only the expected names, each bounded at its limit + 1 bytes."""
        files: Dict[str, bytes] = {}
        total = 0
        for name in sandbox_io.expected_output_names(job):
            path = out / name
            if path.is_symlink() or not path.is_file():
                continue
            limit = sandbox_io.output_limit(name) + 1
            with open(path, "rb") as fh:
                data = fh.read(limit)
            total += len(data)
            if total > I.MAX_TOTAL_OUT_BYTES + 1:
                logger.warning("local sandbox %s: output exceeds the total limit; %s not read", job.label, name)
                break
            files[name] = data
        return files


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the runner and its per-run children."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=False)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        logger.warning("local sandbox runner pid %s did not exit after kill", proc.pid)


def _tail(path: Path, n: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _rmtree(root: Path) -> None:
    for _ in range(20):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            return
        time.sleep(0.1)
    logger.warning("could not remove sandbox dir %s", root)
