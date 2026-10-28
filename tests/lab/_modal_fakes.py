"""Offline fakes for modal.Sandbox / modal.Dict / modal.Volume (sandbox_modal + store_modal tests).

FakeSandbox runs the executor's REAL SETUP and FINISH helper scripts as local subprocesses,
with "/tmp" mapped to a temp dir and "/proc" mapped to a fake proc dir that only ever holds
PIDs the script keeps (so no local process is ever signalled).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_modal as SM


# ── Sandbox ─────────────────────────────────────────────────────────────────
class FakeStdin:
    def __init__(self):
        self.buf = bytearray()
        self.drains = 0
        self.eof = False

    def write(self, data) -> None:
        assert not self.eof, "write after EOF"
        self.buf += bytes(data)

    def write_eof(self) -> None:
        self.eof = True

    def drain(self) -> None:
        self.drains += 1


class FakeProcess:
    """A finished-on-demand exec. `fn(stdin_bytes) -> (rc, stdout_chunks, stderr_chunks)` runs lazily."""

    def __init__(self, rc: int = 0, stdout: Iterable[bytes] = (), stderr: Iterable[bytes] = (),
                 wait_exc: Optional[BaseException] = None, fn: Optional[Callable[[bytes], tuple]] = None):
        self.rc = rc
        self._stdout = stdout
        self._stderr = stderr
        self.wait_exc = wait_exc
        self.fn = fn
        self.stdin = FakeStdin()
        self.consumed = 0                 # stdout chunks handed out
        self.sent = 0                     # stdout bytes handed out
        self._ran = fn is None

    def _run(self) -> None:
        if not self._ran:
            self._ran = True
            self.rc, self._stdout, self._stderr = self.fn(bytes(self.stdin.buf))

    @property
    def stdout(self):
        self._run()
        for chunk in self._stdout:
            self.consumed += 1
            self.sent += len(chunk)
            yield chunk

    @property
    def stderr(self):
        self._run()
        yield from self._stderr

    def wait(self) -> int:
        self._run()
        if self.wait_exc is not None:
            raise self.wait_exc
        return self.rc


def _chunks(data: bytes, size: int = 64 * 1024) -> List[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


class FakeFilesystem:
    """write_bytes onto the fake root (thread-safe); read_bytes must never be called."""

    def __init__(self, sb: "FakeSandbox"):
        self.sb = sb
        self.write_exc: Optional[BaseException] = None
        self.threads: set = set()

    def write_bytes(self, data: bytes, path: str) -> None:
        assert path.startswith(SM.ROOT + "/"), path
        self.threads.add(threading.get_ident())
        with self.sb.lock:
            self.sb.log.append(("write", path))
        if self.write_exc is not None:
            raise self.write_exc
        target = self.sb.root / path[len(SM.ROOT) + 1:]
        assert target.parent.is_dir(), "write before SETUP made the directories"
        target.write_bytes(bytes(data))

    def read_bytes(self, path: str) -> bytes:                      # pragma: no cover - must never run
        raise AssertionError("unbounded read_bytes must never be called")


Runner = Callable[["FakeSandbox", List[str], Mapping[str, Any]], FakeProcess]


class FakeSandbox:
    """Records every exec in `log`; the runner exec is delegated to `runner`."""

    def __init__(self, root: Path, runner: Optional[Runner] = None, *, pids: Sequence[int] = (1, 5, 9),
                 exec_exc: Optional[BaseException] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.proc_dir = self.root.parent / (self.root.name + "_proc")
        for p in pids:
            (self.proc_dir / str(p)).mkdir(parents=True, exist_ok=True)
        self.log: List[tuple] = []
        self.lock = threading.Lock()
        self.filesystem = FakeFilesystem(self)
        self.runner = runner or write_outputs({})
        self.exec_exc = exec_exc
        self.setup_override: Optional[Callable[[], FakeProcess]] = None
        self.finish_override: Optional[Callable[[], FakeProcess]] = None
        self.finish_exc: Optional[BaseException] = None
        self.processes: Dict[str, FakeProcess] = {}
        self.terminated = 0

    @property
    def out_dir(self) -> Path:
        return self.root / I.OUT_DIR

    def _local_args(self, args: Sequence[str]) -> List[str]:
        mapped = {SM.ROOT: self.root.as_posix(), SM.PROC: self.proc_dir.as_posix()}
        return [mapped.get(a, a) for a in args]

    def _script(self, prefix: Sequence[str], args: Sequence[str]) -> Callable[[bytes], tuple]:
        def fn(stdin: bytes) -> tuple:
            cp = subprocess.run([sys.executable, "-I", "-S", "-c", prefix[-1], *self._local_args(args)],
                                input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            return cp.returncode, _chunks(cp.stdout), [cp.stderr]
        return fn

    def exec(self, *args: str, **kw: Any) -> FakeProcess:
        args = list(args)
        n_setup, n_finish = len(SM.SETUP_ARGV_PREFIX), len(SM.FINISH_ARGV_PREFIX)
        if args == I.runner_argv(SM.ROOT):
            self.log.append(("exec_runner", tuple(args), dict(kw)))
            if self.exec_exc is not None:
                raise self.exec_exc
            proc = self.runner(self, args, kw)
            self.processes["runner"] = proc
        elif tuple(args[:n_setup]) == SM.SETUP_ARGV_PREFIX:
            self.log.append(("setup", tuple(args[n_setup:]), dict(kw)))
            proc = (self.setup_override() if self.setup_override
                    else FakeProcess(fn=self._script(SM.SETUP_ARGV_PREFIX, args[n_setup:])))
            self.processes["setup"] = proc
        elif tuple(args[:n_finish]) == SM.FINISH_ARGV_PREFIX:
            rest = args[n_finish:]
            self.log.append(("finish", tuple(rest), dict(kw)))
            if self.finish_exc is not None:
                raise self.finish_exc
            k = int(rest[3])
            keep = {int(x) for x in rest[4:4 + k]}
            strays = [d for d in os.listdir(self.proc_dir) if d.isdigit() and int(d) not in keep]
            assert not strays, f"refusing to run the real killer locally with strays {strays}"
            proc = (self.finish_override() if self.finish_override
                    else FakeProcess(fn=self._script(SM.FINISH_ARGV_PREFIX, rest)))
            self.processes["finish"] = proc
        else:
            raise AssertionError(f"unexpected exec {args[:3]!r}")
        return proc

    def terminate(self) -> None:
        self.log.append(("terminate",))
        self.terminated += 1

    def ops(self) -> List[str]:
        return [e[0] for e in self.log]


class FakeFactory:
    """Stands in for modal.Sandbox.create; records kwargs and hands out one FakeSandbox."""

    def __init__(self, sandbox: Optional[FakeSandbox] = None, exc: Optional[BaseException] = None):
        self.sandbox = sandbox
        self.exc = exc
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, *args: Any, **kw: Any) -> FakeSandbox:
        assert not args, "Sandbox.create must get no entrypoint args"
        self.calls.append(dict(kw))
        if self.exc is not None:
            raise self.exc
        return self.sandbox


def write_outputs(files: Mapping[str, Any], *, rc: int = 0, stderr: Iterable[bytes] = (),
                  wait_exc: Optional[BaseException] = None) -> Runner:
    """A scripted runner: writes `files` into /tmp/out (bytes -> file, None -> a directory)."""

    def run(sb: FakeSandbox, argv: List[str], kw: Mapping[str, Any]) -> FakeProcess:
        assert (sb.root / I.IN_DIR / I.IN_RUNNER).is_file(), "runner started before SETUP wrote the inputs"
        for name, data in files.items():
            if data is None:
                (sb.out_dir / name).mkdir()
            else:
                (sb.out_dir / name).write_bytes(data)
        return FakeProcess(rc, stderr=list(stderr), wait_exc=wait_exc)

    return run


def real_local_runner(timeout_s: float = 120.0) -> Runner:
    """Runs the REAL sandbox_runner.py as a local subprocess on the fake root (paths remapped)."""

    def run(sb: FakeSandbox, argv: List[str], kw: Mapping[str, Any]) -> FakeProcess:
        root = sb.root.as_posix()
        local = [sys.executable] + [a.replace(SM.ROOT + "/", root + "/", 1) if a.startswith(SM.ROOT + "/") else a
                                    for a in argv[1:]]
        env = {"PATH": str(Path(sys.executable).parent), "PYTHONUTF8": "1",
               "TEMP": f"{root}/{I.WORK_DIR}", "TMP": f"{root}/{I.WORK_DIR}"}
        if os.name == "nt" and os.environ.get("SYSTEMROOT"):
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        cp = subprocess.run(local, cwd=f"{root}/{I.WORK_DIR}", env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout_s)
        return FakeProcess(cp.returncode, stderr=[cp.stderr])

    return run


# ── Dict / Volume ───────────────────────────────────────────────────────────
class FakeDict:
    """modal.Dict subset: put(key, value, skip_if_exists) -> created; get(key, default)."""

    def __init__(self):
        self.data: Dict[Any, Any] = {}
        self._lock = threading.Lock()
        self.puts = 0

    def put(self, key: Any, value: Any, *, skip_if_exists: bool = False) -> bool:
        with self._lock:
            self.puts += 1
            if skip_if_exists and key in self.data:
                return False
            self.data[key] = value
            return True

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            return self.data.get(key, default)

    def expire(self, key: Any) -> None:
        """Simulate the 7-day idle expiry of one entry."""
        with self._lock:
            self.data.pop(key, None)


class FakeVolume:
    def __init__(self, reload_exc: Optional[BaseException] = None):
        self.commits = 0
        self.reloads = 0
        self.reload_exc = reload_exc
        self._lock = threading.Lock()

    def commit(self) -> None:
        with self._lock:
            self.commits += 1

    def reload(self) -> None:
        with self._lock:
            self.reloads += 1
        if self.reload_exc is not None:
            raise self.reload_exc
