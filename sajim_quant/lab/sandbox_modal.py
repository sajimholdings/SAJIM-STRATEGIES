"""
SAJIM Strategy Lab — ModalSandboxExecutor (INTERFACES.md §2.4, §13.1, §13.4).

The production SandboxExecutor: every job runs model-written code in a FRESH
``modal.Sandbox`` that has

  * ``block_network=True`` and NO secrets, volumes, network file systems or env;
  * its own image (``sandbox_image()``): debian_slim + Python 3.12 + numpy/pandas/
    pyarrow pinned by ``SANDBOX_PIP_PINS``. No ``modal`` and no ``sajim_quant``: the
    trusted runner and indicators arrive as plain files written by this executor.

Each Modal exec costs ~1-1.5 s of round trips (a first draft with 26 sequential execs spent
30 of its 41 s on them), so one ``run(job)`` is SETUP + 5 concurrent uploads + RUNNER + FINISH.
Live smoke, 2026-10-13, 20k H1 bars, the 8-run check plan: 14.6 s wall = create 2.4 + setup 2.4
+ upload 2.8 + runner 4.0 (8 children, ~0.38 s each incl. start-up) + finish 2.0 + terminate 0.5.

  1. ``Sandbox.create`` (lifetime = job timeout + SANDBOX_LIFETIME_MARGIN_S);
  2. SETUP (trusted, before any untrusted code): makes ``/tmp/{in,work,out}`` and prints the
     PIDs that exist now (the container's own init/agent processes); then the five
     ``sandbox_io.encode_job`` files are written with ``sb.filesystem.write_bytes``, concurrently
     (streaming them through SETUP's stdin with the public write/drain API took 23 s for 1 MB
     in a live smoke; five concurrent write_bytes took 2.8 s);
  3. RUNNER: ``exec(*interfaces.runner_argv("/tmp"), timeout=<job timeout>)``, wait;
  4. FINISH: SIGKILLs every process outside SETUP's PID set and its own ancestry (three
     sweeps), so nothing can still be writing to ``/tmp/out``; then ``lstat``s each
     ``sandbox_io.expected_output_names(job)`` entry and streams back a listing plus the
     bytes of the files that are regular, within their limit and within the total limit.
     A non-regular or oversize file is refused WITHOUT being sent. (``kill -9 -1`` is not
     usable: the 2026-10-13 live smoke showed it takes the whole sandbox down.)
  5. ``terminate()`` in ``finally``.

Bounds that do not depend on the container's honesty: the untrusted code ran as root in
the same container, so it may have replaced ``python`` and made FINISH lie. This side
therefore (a) re-derives which files may be sent from the listing with its OWN limits,
(b) reads FINISH's stdout through a stream it cuts off at listing cap + total output
limit + 1 bytes, and (c) accepts the body only when its length equals the sum of the
accepted sizes exactly, else uses no file at all. modal 1.6's ``sb.filesystem.read_bytes``
(unbounded, §13.1) is never called. Per-exec timeouts and the sandbox lifetime bound time.

Errors: anything that fails BEFORE the untrusted runner starts (create, SETUP, exec start)
raises ``SandboxUnavailable`` — an infrastructure failure the Modal job reports as failed
(respawned under the same claim), never as a strategy failure. Anything that fails AFTER it
started is folded into the SandboxResult (missing files / exit code / stderr note), because
the untrusted code may have caused it. Exception text from the sandbox side can carry
untrusted bytes, so it only leaves this module through ``sandbox_io.sanitize_message``,
with its exception chain suppressed.
"""

from __future__ import annotations

import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sajim_quant.lab import gates
from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_io

logger = logging.getLogger(__name__)

# ── Sandbox image (shared constant: the integrator pins the trusted image to the same) ──
SANDBOX_PYTHON_VERSION = "3.12"
#: Exact versions for the untrusted sandbox image (INTERFACES §13.4). These are the versions the
#: P1 suite and the lint/look-ahead findings were verified against, and the live smoke confirmed
#: them inside the sandbox. modal_runner.py's trusted image should import this tuple instead of
#: its ">=" ranges so both sides run identical numerics.
SANDBOX_PIP_PINS: Tuple[str, ...] = ("numpy==2.5.3", "pandas==3.0.5", "pyarrow==25.0.1")

# ── Executor constants ──────────────────────────────────────────────────────
ROOT = I.SANDBOX_ROOT                       # "/tmp"
PROC = "/proc"
SANDBOX_LIFETIME_MARGIN_S = 90              # create + SETUP before, FINISH after the job
HELPER_TIMEOUT_S = 60                       # SETUP / FINISH exec timeout
LISTING_CAP = 64 * 1024                     # bytes of FINISH's header (kill count + listing) at most
SETUP_OUTPUT_CAP = 64 * 1024
STDERR_TAIL_CHARS = 4000
STDERR_READ_CAP = 64 * 1024                 # bytes of runner stderr consumed at most
EXCEPTION_TEXT_CHARS = 300
TIMEOUT_SLACK_S = 1.0
MAX_KEEP_PIDS = 256
#: argv: <root> <proc>. Makes the work dirs; stdout: "ok\n<pids alive now, space separated>\n".
SETUP_SCRIPT = (
    "import os, sys\n"
    "root, proc = sys.argv[1], sys.argv[2]\n"
    "for d in ('in', 'work', 'out'):\n"
    "    os.makedirs(os.path.join(root, d), exist_ok=True)\n"
    "me = os.getpid()\n"
    "pids = sorted(int(p) for p in os.listdir(proc) if p.isdigit() and int(p) != me)\n"
    "sys.stdout.buffer.write(('ok\\n%s\\n' % ' '.join(map(str, pids))).encode('ascii'))\n"
)
#: argv: <root> <proc> <total_cap> <k> <k keep pids> then (<name> <limit>) pairs.
#: stdout: "<killed>\n" + one "<name> <m|e|o|f> <size>\n" per pair + "--\n" + accepted bytes.
FINISH_SCRIPT = (
    "import os, signal, stat, sys\n"
    "a = sys.argv[1:]\n"
    "root, proc, total_cap, k = a[0], a[1], int(a[2]), int(a[3])\n"
    "keep = {int(x) for x in a[4:4 + k]} | {1, os.getpid()}\n"
    "rest = a[4 + k:]\n"
    "p = os.getpid()\n"
    "while p > 1:\n"
    "    try:\n"
    "        p = int(open(os.path.join(proc, str(p), 'stat')).read().rsplit(')', 1)[1].split()[1])\n"
    "    except Exception:\n"
    "        break\n"
    "    keep.add(p)\n"
    "killed = 0\n"
    "for _ in range(3):\n"
    "    try:\n"
    "        names = os.listdir(proc)\n"
    "    except OSError:\n"
    "        killed = -1\n"
    "        break\n"
    "    for d in names:\n"
    "        if d.isdigit() and int(d) not in keep:\n"
    "            try:\n"
    "                os.kill(int(d), signal.SIGKILL)\n"
    "                killed += 1\n"
    "            except Exception:\n"
    "                pass\n"
    "lines, send, total = [], [], 0\n"
    "for name, lim in zip(rest[0::2], rest[1::2]):\n"
    "    path = os.path.join(root, 'out', name)\n"
    "    try:\n"
    "        st = os.lstat(path)\n"
    "    except FileNotFoundError:\n"
    "        lines.append(name + ' m 0')\n"
    "        continue\n"
    "    except OSError:\n"
    "        lines.append(name + ' e 0')\n"
    "        continue\n"
    "    if not stat.S_ISREG(st.st_mode):\n"
    "        lines.append('%s o %d' % (name, st.st_size))\n"
    "        continue\n"
    "    lines.append('%s f %d' % (name, st.st_size))\n"
    "    if st.st_size <= int(lim) and total + st.st_size <= total_cap:\n"
    "        total += st.st_size\n"
    "        send.append((path, st.st_size))\n"
    "out = sys.stdout.buffer\n"
    "out.write(('%d\\n' % killed + ''.join(x + '\\n' for x in lines) + '--\\n').encode('ascii'))\n"
    "for path, size in send:\n"
    "    with open(path, 'rb') as f:\n"
    "        data = f.read(size)\n"
    "    out.write(data + b'\\0' * (size - len(data)))\n"
    "out.flush()\n"
)
SETUP_ARGV_PREFIX: Tuple[str, ...] = ("python", "-I", "-S", "-c", SETUP_SCRIPT)
FINISH_ARGV_PREFIX: Tuple[str, ...] = ("python", "-I", "-S", "-c", FINISH_SCRIPT)
_PIDS_RE = re.compile(r"^[0-9 ]*$")
_LISTING_RE = re.compile(r"^([a-z0-9_.]{1,64}) ([meof]) ([0-9]{1,15})$")

#: The only keyword arguments ever passed to Sandbox.create (checked on every call).
CREATE_KWARGS: Tuple[str, ...] = ("app", "image", "block_network", "cpu", "memory", "timeout")


class SandboxUnavailable(RuntimeError):
    """Infrastructure failure before the untrusted runner started. The message is sanitised."""


def safe_exc_text(exc: BaseException, limit: int = EXCEPTION_TEXT_CHARS) -> str:
    """'<Type>: <message>' as printable ASCII, at most `limit` chars (the message may be untrusted)."""
    try:
        msg = str(exc)[: limit * 2]
    except Exception:  # noqa: BLE001 — a hostile __str__ must not break error handling
        msg = "<unprintable>"
    return sandbox_io.sanitize_message(f"{type(exc).__name__}: {msg}", limit)


_IMAGE: Any = None


def sandbox_image() -> Any:
    """The untrusted sandbox image, built lazily once per process (needs ``modal``)."""
    global _IMAGE
    if _IMAGE is None:
        import modal

        _IMAGE = modal.Image.debian_slim(python_version=SANDBOX_PYTHON_VERSION).pip_install(*SANDBOX_PIP_PINS)
    return _IMAGE


def _stream_types() -> Tuple[Any, Any]:
    from modal.stream_type import StreamType

    return StreamType.PIPE, StreamType.DEVNULL


def _drain(stream: Any, cap: int) -> Tuple[bytes, bool]:
    """Read at most `cap` bytes from an exec stream; (data, cut_off)."""
    buf = bytearray()
    for chunk in stream:
        if not chunk:
            continue
        buf += bytes(chunk[: cap - len(buf)])
        if len(buf) >= cap:
            return bytes(buf), True
    return bytes(buf), False


def finish_args(job: I.SandboxJob, keep: Sequence[int], *, root: str = ROOT, proc: str = PROC) -> List[str]:
    args = [root, proc, str(int(I.MAX_TOTAL_OUT_BYTES)), str(len(keep))] + [str(int(p)) for p in keep]
    for name in sandbox_io.expected_output_names(job):
        args += [name, str(sandbox_io.output_limit(name))]
    return args


def parse_finish(raw: bytes, cut: bool, rc: Optional[int], job: I.SandboxJob,
                 notes: List[str]) -> Tuple[Dict[str, bytes], Optional[int]]:
    """
    Trusted parse of FINISH's (untrusted) stdout -> (files, killed). The accepted set is
    re-derived here with this side's own limits; any inconsistency means NO file is used.
    """
    pos = raw.find(b"\n--\n", 0, LISTING_CAP)
    if pos < 0:
        notes.append("output listing missing or too long; no output used")
        return {}, None
    try:
        lines = raw[:pos].decode("ascii").split("\n")
        killed = int(lines[0])
    except (UnicodeDecodeError, ValueError):
        notes.append("output listing unreadable; no output used")
        return {}, None
    names = sandbox_io.expected_output_names(job)
    if len(lines) - 1 != len(names):
        notes.append("output listing has the wrong number of entries; no output used")
        return {}, killed
    accepted: List[Tuple[str, int]] = []
    total = 0
    for name, line in zip(names, lines[1:]):
        m = _LISTING_RE.match(line)
        if m is None or m.group(1) != name:
            notes.append("output listing does not match the expected names; no output used")
            return {}, killed
        kind, size, limit = m.group(2), int(m.group(3)), sandbox_io.output_limit(name)
        if kind == "e":
            notes.append(f"{name} could not be inspected; not read")
        elif kind == "o":
            notes.append(f"{name} is not a regular file; not read")
        elif kind == "f":
            if size > limit:
                notes.append(f"{name} is {size} bytes, over its {limit} byte limit; refused without reading")
            elif total + size > I.MAX_TOTAL_OUT_BYTES:
                notes.append(f"total output would exceed {I.MAX_TOTAL_OUT_BYTES} bytes; {name} not read")
            else:
                accepted.append((name, size))
                total += size
    body = raw[pos + 4:]
    if cut or len(body) != total or rc != 0:
        notes.append(f"output stream inconsistent (got {len(body)}{'+' if cut else ''} bytes for {total}, "
                     f"exit {rc}); no output used")
        return {}, killed
    files: Dict[str, bytes] = {}
    off = 0
    for name, size in accepted:
        files[name] = body[off:off + size]
        off += size
    return files, killed


class ModalSandboxExecutor:
    """
    SandboxExecutor backed by a fresh ``modal.Sandbox`` per job (the production boundary).

    ``app`` is injected (the deployed app inside a Modal function, ``modal.App.lookup(...)``
    elsewhere, or a fake in tests). ``image`` defaults to ``sandbox_image()``, built on first
    use. ``timeout`` caps the job timeout this executor accepts; the sandbox itself lives
    ``SANDBOX_LIFETIME_MARGIN_S`` longer so inputs can be written and outputs read.
    ``sandbox_factory`` replaces ``modal.Sandbox.create`` in tests.
    ``last_timings`` / ``last_info`` describe the most recent run (logs and the live smoke).
    """

    def __init__(self, app: Any, image: Any = None, *, cpu: float = I.SANDBOX_CPU,
                 memory: int = I.SANDBOX_MEMORY_MB, timeout: int = gates.LAB_SIGNALS_TIMEOUT_S,
                 sandbox_factory: Optional[Callable[..., Any]] = None):
        if app is None:
            raise ValueError("ModalSandboxExecutor needs a modal App")
        if not (cpu > 0 and memory > 0 and timeout > 0):
            raise ValueError("cpu, memory and timeout must be positive")
        self.app = app
        self._image = image
        self.cpu = float(cpu)
        self.memory = int(memory)
        self.timeout = int(timeout)
        self._factory = sandbox_factory
        self.last_timings: Dict[str, float] = {}
        self.last_info: Dict[str, Any] = {}

    # ── configuration ───────────────────────────────────────────────────────
    @property
    def image(self) -> Any:
        if self._image is None:
            self._image = sandbox_image()
        return self._image

    def exec_timeout(self, job: I.SandboxJob) -> int:
        """Whole seconds the runner may take (Modal exec timeouts are ints)."""
        return max(1, int(math.ceil(min(float(job.job_timeout_s), float(self.timeout)))))

    def create_kwargs(self, job: I.SandboxJob) -> Dict[str, Any]:
        """Exactly CREATE_KWARGS: no secrets, volumes, network file systems or env, ever."""
        kw = {
            "app": self.app, "image": self.image, "block_network": True, "cpu": self.cpu,
            "memory": self.memory, "timeout": self.exec_timeout(job) + SANDBOX_LIFETIME_MARGIN_S,
        }
        if tuple(kw) != CREATE_KWARGS or kw["block_network"] is not True:      # survives python -O
            raise RuntimeError("sandbox create kwargs drifted from CREATE_KWARGS")
        return kw

    def _create(self, **kw: Any) -> Any:
        if self._factory is not None:
            return self._factory(**kw)
        import modal

        return modal.Sandbox.create(**kw)

    # ── SandboxExecutor ─────────────────────────────────────────────────────
    def run(self, job: I.SandboxJob) -> I.SandboxResult:
        inputs = sandbox_io.encode_job(job)                    # trusted; a ValueError is a caller bug
        timings: Dict[str, float] = {}
        info: Dict[str, Any] = {}
        self.last_timings, self.last_info = timings, info
        t0 = time.monotonic()
        sb = None
        try:
            try:
                sb = self._create(**self.create_kwargs(job))
                timings["create"] = time.monotonic() - t0
                t1 = time.monotonic()
                keep = self._setup(sb)
                info["preexisting_pids"] = len(keep)
                timings["setup"] = time.monotonic() - t1
                t1 = time.monotonic()
                self._upload(sb, inputs)
                timings["upload"] = time.monotonic() - t1
            except Exception as exc:  # noqa: BLE001
                msg = safe_exc_text(exc)
                logger.error("modal sandbox %s: setup failed: %s", job.label, msg)
                raise SandboxUnavailable(f"sandbox setup failed: {msg}") from None
            return self._execute(sb, job, keep, t0, timings, info)
        finally:
            if sb is not None:
                t1 = time.monotonic()
                self._terminate(sb, job)
                timings["terminate"] = time.monotonic() - t1
            timings["total"] = time.monotonic() - t0
            logger.info("modal sandbox %s: %s", job.label,
                        " ".join(f"{k}={v:.2f}s" for k, v in timings.items()))

    # ── steps ───────────────────────────────────────────────────────────────
    @staticmethod
    def _setup(sb: Any) -> Tuple[int, ...]:
        """Trusted, before any untrusted code: make the dirs, return the PIDs alive now."""
        pipe, devnull = _stream_types()
        proc = sb.exec(*SETUP_ARGV_PREFIX, ROOT, PROC, timeout=HELPER_TIMEOUT_S, stdout=pipe, stderr=devnull,
                       text=False)
        raw, cut = _drain(proc.stdout, SETUP_OUTPUT_CAP)
        rc = proc.wait()
        lines = raw.decode("ascii", "replace").split("\n")
        if rc != 0 or cut or len(lines) < 2 or lines[0] != "ok" or not _PIDS_RE.match(lines[1]):
            raise RuntimeError(f"sandbox setup helper failed (exit {rc})")
        pids = tuple(int(p) for p in lines[1].split())
        if not pids or len(pids) > MAX_KEEP_PIDS:
            raise RuntimeError(f"sandbox setup helper saw {len(pids)} processes")
        return pids

    @staticmethod
    def _upload(sb: Any, inputs: Mapping[str, bytes]) -> None:
        """The five encode_job files via sb.filesystem.write_bytes, concurrently (each is one exec)."""
        with ThreadPoolExecutor(max_workers=max(1, len(inputs)), thread_name_prefix="sb-upload") as pool:
            futures = [pool.submit(sb.filesystem.write_bytes, data, f"{ROOT}/{rel}") for rel, data in inputs.items()]
            for f in futures:
                f.result()                                   # first failure propagates (sanitised by run())

    def _execute(self, sb: Any, job: I.SandboxJob, keep: Tuple[int, ...], t0: float,
                 timings: Dict[str, float], info: Dict[str, Any]) -> I.SandboxResult:
        pipe, devnull = _stream_types()
        exec_timeout = self.exec_timeout(job)
        notes: List[str] = []
        t1 = time.monotonic()
        try:
            proc = sb.exec(*I.runner_argv(ROOT), timeout=exec_timeout, workdir=f"{ROOT}/{I.WORK_DIR}",
                           stdout=devnull, stderr=pipe, text=False)
        except Exception as exc:  # noqa: BLE001 — the runner never started: infrastructure
            msg = safe_exc_text(exc)
            logger.error("modal sandbox %s: exec failed: %s", job.label, msg)
            raise SandboxUnavailable(f"sandbox exec failed: {msg}") from None

        rc: Optional[int] = None
        timed_out = False
        try:
            rc = int(proc.wait())
        except Exception as exc:  # noqa: BLE001
            notes.append(f"wait failed: {safe_exc_text(exc)}")
        elapsed = time.monotonic() - t1
        timings["exec"] = elapsed
        near_deadline = elapsed >= exec_timeout - TIMEOUT_SLACK_S
        if rc == -1 or (near_deadline and rc != 0):
            timed_out = True                             # modal 1.6: wait() returns -1 at the exec deadline
        elif rc is None:
            rc = -1                                      # lost the runner: blame the job, not the infra

        t2 = time.monotonic()
        files, killed = self._finish(sb, job, keep, notes)
        info["killed"] = killed
        if killed is not None and killed < 0:
            notes.append("the output reader could not list processes; strays were not killed")
        elif killed:
            # The runner reaps its children, so a survivor means a timed-out runner or strategy code
            # that detached a process (it could have rewritten earlier runs' outputs): visible in logs.
            notes.append(f"{killed} kill signal(s) sent to processes that outlived the runner")
        timings["finish"] = time.monotonic() - t2
        tail = "" if timed_out else self._stderr_tail(proc, notes)
        if notes:
            note_text = "\n".join(f"[executor] {n}" for n in notes)
            logger.warning("modal sandbox %s: %s", job.label, note_text)
            tail = f"{tail}\n{note_text}" if tail else note_text
        tail = sandbox_io.sanitize_message(tail[-STDERR_TAIL_CHARS:], STDERR_TAIL_CHARS)
        return I.SandboxResult(files=files, exit_code=None if timed_out else rc, timed_out=timed_out,
                               wall_s=time.monotonic() - t0, stderr_tail=tail)

    @staticmethod
    def _finish(sb: Any, job: I.SandboxJob, keep: Tuple[int, ...],
                notes: List[str]) -> Tuple[Dict[str, bytes], Optional[int]]:
        """Kill strays, list the expected outputs, read the accepted ones: one exec, bounded here."""
        pipe, devnull = _stream_types()
        cap = LISTING_CAP + int(I.MAX_TOTAL_OUT_BYTES) + 1
        try:
            proc = sb.exec(*FINISH_ARGV_PREFIX, *finish_args(job, keep), timeout=HELPER_TIMEOUT_S,
                           stdout=pipe, stderr=devnull, text=False)
            raw, cut = _drain(proc.stdout, cap)
            rc = None if cut else proc.wait()
        except Exception as exc:  # noqa: BLE001 — the untrusted code may have broken the container
            notes.append(f"reading outputs failed: {safe_exc_text(exc)}")
            return {}, None
        return parse_finish(raw, cut, rc, job, notes)

    @staticmethod
    def _stderr_tail(proc: Any, notes: List[str]) -> str:
        """Last STDERR_TAIL_CHARS of the runner's stderr, consuming at most STDERR_READ_CAP bytes."""
        try:
            raw, _ = _drain(proc.stderr, STDERR_READ_CAP)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"stderr unavailable: {safe_exc_text(exc)}")
            return ""
        return raw.decode("utf-8", "replace")[-STDERR_TAIL_CHARS:]

    @staticmethod
    def _terminate(sb: Any, job: I.SandboxJob) -> None:
        try:
            sb.terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("modal sandbox %s: terminate failed: %s", job.label, safe_exc_text(exc))

