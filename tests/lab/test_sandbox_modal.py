"""sandbox_modal.ModalSandboxExecutor: offline tests with a fake modal.Sandbox, plus one opt-in live smoke.

The fake runs the executor's real SETUP / FINISH helper scripts locally (see _modal_fakes.py).
The live smoke (test_live_modal_sandbox_smoke) runs ONE real Modal sandbox on an ephemeral app;
it is skipped unless SAJIM_LIVE_MODAL=1. Synthetic OHLC only; never loads data/**.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from sajim_quant.lab import causality, contract, sandbox_io
from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_modal as SM

sys.path.insert(0, str(Path(__file__).parent))
from _judging_testkit import PLANTED_SRC, dense_to_events, npy_bytes  # noqa: E402
from _modal_fakes import FakeFactory, FakeProcess, FakeSandbox, real_local_runner, write_outputs  # noqa: E402
from _sandbox_testkit import STUB_INDICATORS, make_job, strategy, synthetic_frame  # noqa: E402

APP, IMAGE = object(), object()
RUN_IDS = ("full1", "full2")


def _job(run_ids=RUN_IDS, **kw) -> I.SandboxJob:
    runs = [I.SandboxRun(r, {"n": 5}) for r in run_ids]
    return make_job(strategy("PARAMS = {}\n"), runs, synthetic_frame(300), **kw)


def _events() -> np.ndarray:
    ev = np.zeros(3, dtype=I.EVENTS_DTYPE)
    ev["bar"] = [10, 50, 200]
    ev["signal"] = 1
    ev["stop_dist"] = 1.5
    ev["tp_dist"] = np.nan
    return ev


def _status(run_ids=RUN_IDS) -> bytes:
    return json.dumps({"schema": I.STATUS_SCHEMA, "ok": True,
                       "runs": {r: {"ok": True, "n_events": 3, "seconds": 0.5} for r in run_ids},
                       "errors": [], "timings": {r: 0.5 for r in run_ids},
                       "versions": {"python": "3.12.10"}, "sensitive_env_names": []}).encode()


def _outputs(run_ids=RUN_IDS) -> dict:
    files = {I.OUT_STATUS: _status(run_ids)}
    files.update({I.events_file(r): npy_bytes(_events()) for r in run_ids})
    return files


def _executor(tmp_path, runner=None, **kw):
    sb = FakeSandbox(tmp_path / "sb", runner or write_outputs(_outputs()), **kw)
    factory = FakeFactory(sb)
    return SM.ModalSandboxExecutor(APP, IMAGE, sandbox_factory=factory), sb, factory


def _printable(s: str) -> bool:
    return all(" " <= c <= "~" or c in "\n\t" for c in s)


# ── configuration ───────────────────────────────────────────────────────────

def test_is_a_sandbox_executor_and_builds_no_image_eagerly(monkeypatch):
    monkeypatch.setattr(SM, "_IMAGE", None)
    ex = SM.ModalSandboxExecutor(APP, sandbox_factory=FakeFactory())
    assert isinstance(ex, I.SandboxExecutor)
    assert SM._IMAGE is None and ex._image is None            # nothing built until the first run


def test_pins_are_exact_and_contain_no_modal_or_sajim():
    names = [p.split("==")[0] for p in SM.SANDBOX_PIP_PINS]
    assert names == ["numpy", "pandas", "pyarrow"]
    assert all(p.count("==") == 1 and p.split("==")[1][0].isdigit() for p in SM.SANDBOX_PIP_PINS)
    assert SM.SANDBOX_PYTHON_VERSION == "3.12"


def test_sandbox_image_is_debian_slim_with_the_pins_only(monkeypatch):
    import modal

    calls = []

    class FakeImg:
        def pip_install(self, *pkgs, **kw):
            calls.append(("pip_install", pkgs, kw))
            return "IMAGE"

    def debian_slim(**kw):
        calls.append(("debian_slim", kw))
        return FakeImg()

    monkeypatch.setattr(modal.Image, "debian_slim", staticmethod(debian_slim))
    monkeypatch.setattr(SM, "_IMAGE", None)
    assert SM.sandbox_image() == "IMAGE" and SM.sandbox_image() == "IMAGE"     # cached
    assert calls == [("debian_slim", {"python_version": "3.12"}), ("pip_install", SM.SANDBOX_PIP_PINS, {})]


def test_create_kwargs_have_no_secrets_volumes_or_env(tmp_path):
    ex, sb, factory = _executor(tmp_path)
    ex.run(_job())
    assert len(factory.calls) == 1
    kw = factory.calls[0]
    assert tuple(kw) == SM.CREATE_KWARGS == ("app", "image", "block_network", "cpu", "memory", "timeout")
    for banned in ("secrets", "volumes", "network_file_systems", "env", "cloud_bucket_mounts",
                   "outbound_cidr_allowlist", "outbound_domain_allowlist", "workdir", "name"):
        assert banned not in kw
    assert kw["app"] is APP and kw["image"] is IMAGE and kw["block_network"] is True
    assert (kw["cpu"], kw["memory"]) == (I.SANDBOX_CPU, I.SANDBOX_MEMORY_MB) == (2.0, 4096)
    assert kw["timeout"] == int(I.JOB_TIMEOUT_S) + SM.SANDBOX_LIFETIME_MARGIN_S
    for entry in sb.log:
        if len(entry) > 2:                                       # every exec: no env, no secrets
            assert "env" not in entry[2] and "secrets" not in entry[2]
    runner_exec = [e for e in sb.log if e[0] == "exec_runner"][0]
    assert runner_exec[2]["timeout"] == int(I.JOB_TIMEOUT_S) and runner_exec[2]["workdir"] == "/tmp/work"


def test_exec_timeout_is_whole_seconds_and_capped_by_the_executor():
    ex = SM.ModalSandboxExecutor(APP, IMAGE, timeout=20, sandbox_factory=FakeFactory())
    assert ex.exec_timeout(_job(job_timeout_s=30.5)) == 20
    assert ex.create_kwargs(_job(job_timeout_s=30.5))["timeout"] == 20 + SM.SANDBOX_LIFETIME_MARGIN_S
    ex = SM.ModalSandboxExecutor(APP, IMAGE, sandbox_factory=FakeFactory())
    assert ex.exec_timeout(_job(job_timeout_s=30.5)) == 31
    with pytest.raises(ValueError):
        SM.ModalSandboxExecutor(None, IMAGE)


def test_installed_modal_defaults_keep_the_sandbox_bare():
    """Only CREATE_KWARGS are passed, so every other Sandbox.create / exec default must stay inert."""
    import inspect

    import modal

    create = inspect.signature(modal.Sandbox.create).parameters
    assert set(SM.CREATE_KWARGS) <= set(create)
    for name in ("secrets", "env", "outbound_cidr_allowlist", "outbound_domain_allowlist", "cidr_allowlist",
                 "inbound_cidr_allowlist", "proxy", "custom_domain", "_experimental_outbound_policy"):
        if name in create:
            assert create[name].default is None, name
    for name in ("volumes", "network_file_systems"):
        assert create[name].default == {}, name
    for name in ("encrypted_ports", "h2_ports", "unencrypted_ports"):
        if name in create:
            assert list(create[name].default) == [], name
    assert create["include_oidc_identity_token"].default is False       # no cloud identity token inside
    assert create["block_network"].default is False                     # so passing True is what blocks it
    ex = inspect.signature(modal.Sandbox.exec).parameters
    assert ex["env"].default is None and ex["secrets"].default is None


#: Runs the REAL FINISH script with os.kill recorded (never sent) and a fixed pid, on any OS.
_KILL_PRELUDE = (
    "import atexit, os, signal, sys\n"
    "_sent = []\n"
    "os.getpid = lambda: 500\n"
    "def _kill(pid, sig):\n"
    "    if pid == 999:\n"
    "        raise ProcessLookupError(pid)\n"
    "    _sent.append((pid, int(sig)))\n"
    "os.kill = _kill\n"
    "if not hasattr(signal, 'SIGKILL'):\n"
    "    signal.SIGKILL = 9\n"
    "atexit.register(lambda: sys.stderr.write(repr(sorted(_sent))))\n"
)


def test_finish_killer_spares_the_keep_set_and_its_own_ancestry(tmp_path):
    import subprocess

    root, proc = tmp_path / "root", tmp_path / "proc"
    (root / I.OUT_DIR).mkdir(parents=True)
    (root / I.OUT_DIR / I.OUT_STATUS).write_bytes(b"{}")
    for pid in (1, 5, 400, 500, 401, 777, 999):
        (proc / str(pid)).mkdir(parents=True)
    (proc / "self").mkdir()
    (proc / "500" / "stat").write_text("500 (python) S 400 1 1", encoding="ascii")
    (proc / "400" / "stat").write_text("400 (odd) name) S 1 1 1", encoding="ascii")   # ')' in comm
    (proc / "401" / "stat").write_text("401 (sh) S 400 1 1", encoding="ascii")        # a sibling, not an ancestor
    job = _job(run_ids=("full1",))
    args = [a.replace(SM.ROOT, root.as_posix()) if a == SM.ROOT else a for a in SM.finish_args(job, (1, 5))]
    args[1] = proc.as_posix()
    cp = subprocess.run([sys.executable, "-I", "-S", "-c", _KILL_PRELUDE + SM.FINISH_SCRIPT, *args],
                        capture_output=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    sent = ast.literal_eval(cp.stderr.decode("ascii"))
    assert sent == sorted([(401, 9), (777, 9)] * 3)            # 3 sweeps; 1, 5, 400 (parent), 500 (self) spared
    notes = []
    files, killed = SM.parse_finish(cp.stdout, False, 0, job, notes)
    assert killed == 6 and files == {I.OUT_STATUS: b"{}"}       # 999 vanished: not counted
    assert notes == []


def test_strays_killed_after_the_runner_are_noted(tmp_path):
    ex, sb, _ = _executor(tmp_path)
    listing = b"4\nstatus.json m 0\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\n"
    sb.finish_override = lambda: FakeProcess(0, stdout=[listing])
    res = ex.run(_job())
    assert ex.last_info["killed"] == 4
    assert "4 kill signal(s) sent to processes that outlived the runner" in res.stderr_tail


def test_helper_scripts_compile():
    compile(SM.SETUP_SCRIPT, "setup", "exec")
    compile(SM.FINISH_SCRIPT, "finish", "exec")


# ── happy paths ─────────────────────────────────────────────────────────────

def test_happy_path_order_and_decoding(tmp_path):
    ex, sb, _ = _executor(tmp_path)
    job = _job()
    res = ex.run(job)
    # exactly: SETUP (trusted, before any untrusted code), the runner, FINISH, terminate
    # SETUP (trusted, before any untrusted code), five uploads, the runner, FINISH, terminate
    assert sb.ops() == ["setup"] + ["write"] * 5 + ["exec_runner", "finish", "terminate"] and sb.terminated == 1
    assert sb.log[0][1] == ("/tmp", "/proc")
    inputs = sandbox_io.encode_job(job)
    assert sorted(e[1] for e in sb.log[1:6]) == sorted(f"/tmp/{k}" for k in inputs)
    for rel, data in inputs.items():
        assert (sb.root / rel).read_bytes() == data
    # FINISH keeps exactly SETUP's pids and asks only for the expected names with their limits
    fin = list(next(e for e in sb.log if e[0] == "finish")[1])
    assert fin[:7] == ["/tmp", "/proc", str(I.MAX_TOTAL_OUT_BYTES), "3", "1", "5", "9"]
    names = sandbox_io.expected_output_names(job)
    assert fin[7:] == [x for n in names for x in (n, str(sandbox_io.output_limit(n)))]
    assert res.exit_code == 0 and res.timed_out is False and res.stderr_tail == ""
    assert set(res.files) == set(names)
    assert all(res.files[n] == _outputs()[n] for n in names)
    dec = sandbox_io.decode_result(job, res)
    assert dec.job_ok and dec.ok("full1") and dec.ok("full2") and dec.errors == ()
    assert sandbox_io.events_equal(dec.events["full1"], _events())[0]
    assert set(ex.last_timings) == {"create", "setup", "upload", "exec", "finish", "terminate", "total"}
    assert ex.last_info == {"preexisting_pids": 3, "killed": 0}


def test_uploads_run_concurrently_and_land_intact(tmp_path):
    frame = synthetic_frame(60_000, freq="1h")
    job = make_job(strategy("PARAMS = {}\n"), [I.SandboxRun("full1", {})], frame)
    ex, sb, _ = _executor(tmp_path, runner=write_outputs({}))
    ex.run(job)
    assert (sb.root / "in" / "frame.parquet").read_bytes() == job.frame_parquet
    assert len(sb.filesystem.threads) >= 1 and threading.get_ident() not in sb.filesystem.threads


def test_real_runner_through_the_fake_sandbox(tmp_path):
    """The real sandbox_runner.py runs on the files SETUP wrote, at the paths the executor chose."""
    src = PLANTED_SRC
    frame = synthetic_frame(600, freq="1h")
    params = contract.default_params(contract.parse_params(src))
    runs = (I.SandboxRun("cut60", params, cut=360), I.SandboxRun("full1", params))
    job = I.SandboxJob(frame_parquet=sandbox_io.frame_to_parquet(frame), n_bars=len(frame), strategy_py=src,
                       indicators_py=STUB_INDICATORS, runs=runs, allow_short=False, label="fake-real")
    ex, sb, _ = _executor(tmp_path, runner=real_local_runner())
    res = ex.run(job)
    dec = sandbox_io.decode_result(job, res)
    assert res.exit_code == 0 and dec.job_ok, (res.stderr_tail, dec.errors)
    assert dec.ok("cut60") and dec.ok("full1")
    ns = {}
    exec(compile(src, "strategy.py", "exec"), ns)  # noqa: S102 - trusted test strategy
    expect = dense_to_events(ns["generate_signals"](frame.copy(), dict(params)))
    assert sandbox_io.events_equal(dec.events["full1"], expect)[0]
    assert not (sb.root / I.IN_DIR / I.IN_FRAME).exists()        # runner deleted the frame


# ── refusals and failures ───────────────────────────────────────────────────

def test_oversize_output_is_refused_without_reading(tmp_path):
    files = _outputs()
    files["full1.events.npy"] = b"\x93NUMPY" + b"A" * I.MAX_EVENTS_FILE_BYTES       # limit + 6 bytes
    files[I.OUT_STATUS] = b" " * (I.MAX_STATUS_BYTES + 1)
    ex, sb, _ = _executor(tmp_path, runner=write_outputs(files))
    job = _job()
    res = ex.run(job)
    assert set(res.files) == {"full2.events.npy"}
    assert sb.processes["finish"].sent < 64 * 1024                 # the oversize bytes never left the sandbox
    assert res.stderr_tail.count("refused without reading") == 2 and sb.terminated == 1
    dec = sandbox_io.decode_result(job, res)
    assert not dec.job_ok and not dec.ok("full1") and dec.ok("full2")


def test_missing_output_and_non_regular_files(tmp_path):
    files = _outputs()
    del files["full2.events.npy"]
    files["full1.events.npy"] = None                               # a directory, not a file
    ex, sb, _ = _executor(tmp_path, runner=write_outputs(files))
    job = _job()
    res = ex.run(job)
    assert set(res.files) == {I.OUT_STATUS}
    assert "full1.events.npy is not a regular file; not read" in res.stderr_tail
    dec = sandbox_io.decode_result(job, res)
    assert {e.run_id: e.kind for e in dec.errors} == {"full1": "missing_output", "full2": "missing_output"}


def test_total_output_cap(tmp_path, monkeypatch):
    files = _outputs()
    monkeypatch.setattr(I, "MAX_TOTAL_OUT_BYTES", len(files[I.OUT_STATUS]) + len(files["full1.events.npy"]) + 1)
    ex, sb, _ = _executor(tmp_path)
    res = ex.run(_job())
    assert set(res.files) == {I.OUT_STATUS, "full1.events.npy"}
    assert "total output would exceed" in res.stderr_tail


def _tampered(payload: bytes, rc: int = 0, endless: bool = False):
    def make():
        def gen():
            yield payload
            while endless:
                yield b"\0" * (1024 * 1024)
        return FakeProcess(rc, stdout=gen())
    return make


TAMPERED = [
    (b"0\nstatus.json f 5\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\nhello", True, "endless stream"),
    (b"0\nstatus.json f 5\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\nhelloEXTRA", False, "extra bytes"),
    (b"0\nstatus.json f 5\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\nhel", False, "short body"),
    (b"0\nstatus.json f 5\nfull9.events.npy m 0\nfull2.events.npy m 0\n--\nhello", False, "renamed entry"),
    (b"0\nstatus.json f 5\n--\nhello", False, "missing entries"),
    (b"0\n" + b"x" * (SM.LISTING_CAP + 10) + b"\n--\n", False, "listing too long"),
    (b"zero\nstatus.json f 5\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\nhello", False, "bad kill count"),
    (b"0\nstatus.json f 99999999999\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\n", False, "lying size"),
]


@pytest.mark.parametrize("payload, endless, why", TAMPERED, ids=[t[2].replace(" ", "_") for t in TAMPERED])
def test_tampered_finish_output_uses_no_file_and_stays_bounded(tmp_path, payload, endless, why):
    ex, sb, _ = _executor(tmp_path)
    sb.finish_override = _tampered(payload, endless=endless)
    job = _job()
    res = ex.run(job)
    assert res.files == {}, why
    proc = sb.processes["finish"]
    assert proc.sent <= SM.LISTING_CAP + I.MAX_TOTAL_OUT_BYTES + 1024 * 1024 + len(payload), why
    assert "[executor]" in res.stderr_tail and _printable(res.stderr_tail)
    assert sandbox_io.decode_result(job, res).job_ok is False


def test_finish_exit_code_must_be_zero(tmp_path):
    ex, sb, _ = _executor(tmp_path)
    good = b"0\nstatus.json m 0\nfull1.events.npy m 0\nfull2.events.npy m 0\n--\n"
    sb.finish_override = _tampered(good, rc=1)
    res = ex.run(_job())
    assert res.files == {} and "exit 1" in res.stderr_tail


def test_timeout_maps_to_timed_out_and_still_terminates(tmp_path):
    ex, sb, _ = _executor(tmp_path, runner=write_outputs({}, rc=-1))     # modal: wait() == -1 at the deadline
    job = _job()
    res = ex.run(job)
    assert res.timed_out is True and res.exit_code is None
    assert sb.ops()[-3:] == ["exec_runner", "finish", "terminate"]      # strays killed, outputs still read
    dec = sandbox_io.decode_result(job, res)
    assert any(e.run_id is None and e.kind == "timeout" for e in dec.errors)


def test_wait_failure_is_the_jobs_failure_not_an_exception(tmp_path):
    ex, sb, _ = _executor(tmp_path, runner=write_outputs({}, wait_exc=RuntimeError("router \x1b[31mgone☃")))
    job = _job()
    res = ex.run(job)
    assert res.exit_code == -1 and res.timed_out is False and sb.terminated == 1
    assert "wait failed: RuntimeError: router ?[31mgone?" in res.stderr_tail
    assert any(e.kind == "sandbox" for e in sandbox_io.decode_result(job, res).errors)


def test_nonzero_exit_is_reported(tmp_path):
    ex, sb, _ = _executor(tmp_path, runner=write_outputs(_outputs(), rc=3, stderr=[b"Traceback ...\n" * 10]))
    job = _job()
    res = ex.run(job)
    assert res.exit_code == 3 and "Traceback" in res.stderr_tail
    assert any(e.kind == "sandbox" for e in sandbox_io.decode_result(job, res).errors)


def test_stderr_tail_is_bounded_and_sanitised(tmp_path):
    big = [b"\xff\x00evil\x1b[0m" + b"x" * 10_000 for _ in range(100)]
    ex, sb, _ = _executor(tmp_path, runner=write_outputs(_outputs(), stderr=big))
    res = ex.run(_job())
    assert len(res.stderr_tail) <= SM.STDERR_TAIL_CHARS and _printable(res.stderr_tail)


EVIL = "bad \x1b]0;pwn\x07 path /tmp/‮what\n" + "Z" * 50_000


@pytest.mark.parametrize("where", ["create", "setup_exc", "setup_rc", "setup_pids", "setup_header", "setup_none",
                                   "write", "exec"])
def test_setup_failures_raise_sanitised_and_terminate(tmp_path, where):
    evil = RuntimeError(EVIL)
    sb = FakeSandbox(tmp_path / "sb", write_outputs(_outputs()), exec_exc=evil if where == "exec" else None)
    bad_setup = {
        "setup_exc": lambda: FakeProcess(0, wait_exc=evil),
        "setup_rc": lambda: FakeProcess(1, stdout=[b"ok\n1 2\n"]),
        "setup_pids": lambda: FakeProcess(0, stdout=[b"ok\n1 2; rm -rf /\n"]),
        "setup_header": lambda: FakeProcess(0, stdout=[b"10\n1 2\n"]),
        "setup_none": lambda: FakeProcess(0, stdout=[b"ok\n\n"]),               # no processes
    }.get(where)
    sb.setup_override = bad_setup
    if where == "write":
        sb.filesystem.write_exc = evil
    factory = FakeFactory(sb, exc=evil if where == "create" else None)
    ex = SM.ModalSandboxExecutor(APP, IMAGE, sandbox_factory=factory)
    with pytest.raises(SM.SandboxUnavailable) as info:
        ex.run(_job())
    msg = str(info.value)
    assert len(msg) <= SM.EXCEPTION_TEXT_CHARS + 40 and _printable(msg) and "\x1b" not in msg
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True
    assert sb.terminated == (0 if where == "create" else 1)
    assert ("exec_runner" in sb.ops()) == (where == "exec")


def test_errors_after_the_runner_started_never_raise(tmp_path):
    ex, sb, _ = _executor(tmp_path)
    sb.finish_exc = RuntimeError("Sandbox \x00gone " + "y" * 5000)
    job = _job()
    res = ex.run(job)
    assert sb.terminated == 1 and res.files == {}
    assert "reading outputs failed: RuntimeError: Sandbox ?gone" in res.stderr_tail and _printable(res.stderr_tail)
    assert ex.last_info["killed"] is None


def test_terminate_failure_does_not_mask_the_result(tmp_path):
    ex, sb, _ = _executor(tmp_path)

    def boom():
        sb.log.append(("terminate",))
        raise RuntimeError("already gone")

    sb.terminate = boom
    res = ex.run(_job())
    assert res.exit_code == 0 and sb.ops()[-1] == "terminate"


def test_safe_exc_text_survives_a_hostile_str():
    class Hostile(Exception):
        def __str__(self):
            raise ValueError("no")

    assert SM.safe_exc_text(Hostile()) == "Hostile: <unprintable>"



# ── live smoke (opt-in, one real sandbox) ───────────────────────────────────
LIVE = os.environ.get("SAJIM_LIVE_MODAL") == "1"
EXAMPLE = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "lab-build" / "examples" / "ema_pullback.py"


@pytest.mark.integration
@pytest.mark.skipif(not LIVE, reason="live Modal smoke: set SAJIM_LIVE_MODAL=1")
def test_live_modal_sandbox_smoke(capsys):
    """One real sandbox on an ephemeral app: ~20k synthetic H1 bars, the frozen 8-run check plan."""
    import modal

    src = EXAMPLE.read_bytes()
    assert contract.lint_source(src) == []
    frame = synthetic_frame(20_000, seed=3, start="2022-01-03 00:00", freq="1h")
    params = contract.default_params(contract.parse_params(src))
    plan = causality.build_check_plan(len(frame), params, I.sha256_hex(src))
    job = I.SandboxJob(frame_parquet=sandbox_io.frame_to_parquet(frame), n_bars=len(frame), strategy_py=src,
                       indicators_py=sandbox_io.indicators_source(), runs=plan, allow_short=True, label="smoke")
    app = modal.App.lookup("sajim-lab-smoke", create_if_missing=True)
    ex = SM.ModalSandboxExecutor(app)
    t0 = time.monotonic()
    res = ex.run(job)
    wall = time.monotonic() - t0
    dec = sandbox_io.decode_result(job, res)
    report = causality.assess_check(plan, dec, len(frame))
    summary = {
        "wall_s": round(wall, 2), "executor_timings": {k: round(v, 2) for k, v in ex.last_timings.items()},
        "executor_info": dict(ex.last_info), "frame_parquet_bytes": len(job.frame_parquet),
        "sandbox_timings": dict(dec.timings), "versions": dict(dec.versions),
        "sensitive_env_names": list(dec.sensitive_env_names), "exit_code": res.exit_code,
        "timed_out": res.timed_out, "errors": [(e.run_id, e.kind, e.message[:200]) for e in dec.errors],
        "n_events": {k: int(len(v)) for k, v in dec.events.items()}, "check_passed": report.passed,
        "check_reasons": list(report.reasons), "stderr_tail": res.stderr_tail[-800:],
    }
    out = os.environ.get("SAJIM_LIVE_MODAL_OUT")
    if out:
        Path(out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with capsys.disabled():
        print("\nLIVE MODAL SMOKE", json.dumps(summary, indent=2))
    assert res.timed_out is False and res.exit_code == 0, summary
    assert dec.job_ok and all(dec.ok(r.run_id) for r in plan), summary
    assert sandbox_io.events_equal(dec.events["full1"], dec.events["full2"])[0]
    assert not any(n.startswith("SAJIM_") for n in dec.sensitive_env_names), summary
    assert dec.versions.get("pandas") == SM.SANDBOX_PIP_PINS[1].split("==")[1], summary
