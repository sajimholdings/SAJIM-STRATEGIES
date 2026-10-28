"""The in-sandbox runner: import guard, output validation, per-run isolation, timeouts, budget, status."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pytest

from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_io
from sajim_quant.lab import sandbox_runner as R
from sajim_quant.lab.sandbox_local import LocalSubprocessExecutor

from _sandbox_testkit import errors_of, make_job, run_jobs, strategy, synthetic_frame

N = 300
FRAME = synthetic_frame(N, seed=3)

BENIGN = strategy('''
    import numpy as np
    import pandas as pd
    from sajim_quant.lab import indicators as ind

    PARAMS = {"n": {"default": 10, "min": 2, "max": 50, "type": "int"},
              "k": {"default": 0.5, "min": 0.1, "max": 1.0, "type": "float"}}

    def generate_signals(df, params):
        assert isinstance(params["n"], int) and isinstance(params["k"], float)
        m = ind.sma(df["close"], params["n"])
        up = (df["close"] > m) & (df["close"].shift(1) <= m.shift(1))
        out = pd.DataFrame(index=df.index)
        out["signal"] = up.astype("int8")
        out["stop_dist"] = params["k"]
        out["tp_dist"] = np.nan
        out["exit"] = (df["close"] < m) & (df["close"].shift(1) >= m.shift(1))
        return out
''')
P = {"n": 10, "k": 0.5}


def _src_with(body: str) -> bytes:
    return strategy('''
        import numpy as np
        import pandas as pd
        PARAMS = {"n": {"default": 10, "min": 2, "max": 50, "type": "int"}}
    ''') + strategy(body)


# ── pure, in-process ────────────────────────────────────────────────────────
def test_runner_imports_nothing_from_sajim_quant_and_mirrors_the_frozen_constants():
    tree = ast.parse(open(R.__file__, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert "sajim_quant" not in mods
    allowed = set(sys.stdlib_module_names) | {"numpy", "pandas", "pyarrow"}
    assert mods <= allowed, mods - allowed
    assert R.EVENTS_DTYPE == I.EVENTS_DTYPE and R.EVENTS_DTYPE.descr == I.EVENTS_DTYPE.descr
    assert (R.PLAN_SCHEMA, R.STATUS_SCHEMA) == (I.PLAN_SCHEMA, I.STATUS_SCHEMA)
    assert (R.IN_FRAME, R.IN_STRATEGY, R.IN_INDICATORS, R.IN_PLAN, R.OUT_STATUS) == \
           (I.IN_FRAME, I.IN_STRATEGY, I.IN_INDICATORS, I.IN_PLAN, I.OUT_STATUS)
    assert R.RUN_ID_RE.pattern == I.RUN_ID_PATTERN
    assert set(R.RUNNER_ERROR_KINDS) <= set(I.ERROR_KINDS)
    assert R.RUNNER_BUDGET_MARGIN_S == I.RUNNER_BUDGET_MARGIN_S > R.STATUS_RESERVE_S
    assert R.MAX_ERROR_MESSAGE_CHARS == I.MAX_ERROR_MESSAGE_CHARS
    assert R.MAX_RUNS_PER_JOB == I.MAX_RUNS_PER_JOB


@pytest.mark.parametrize("name,fromlist", [
    ("numpy", ()), ("numpy.random", ()), ("pandas", ("DataFrame",)), ("math", ()), ("typing", ("Any",)),
    ("__future__", ("annotations",)), ("sajim_quant.lab", ("indicators",)), ("sajim_quant.lab.indicators", ()),
])
def test_import_guard_allows_the_contract_modules(name, fromlist):
    assert R._import_allowed(name, fromlist) is None


@pytest.mark.parametrize("name,fromlist", [
    ("os", ()), ("socket", ()), ("subprocess", ()), ("sys", ()), ("importlib", ()), ("pickle", ()),
    ("pathlib", ()), ("urllib.request", ()), ("ctypes", ()), ("builtins", ()), ("backtrader", ()),
    ("numpy.f2py", ()), ("numpy", ("f2py",)), ("numpy.ctypeslib", ()), ("sajim_quant.lab", ("contract",)),
    ("sajim_quant", ("cloud",)), ("sajim_quant.core", ()),
])
def test_import_guard_blocks_everything_else(name, fromlist):
    assert R._import_allowed(name, fromlist) is not None
    with pytest.raises(ImportError, match="blocked"):
        R.guarded_import(name, {}, {}, fromlist, 0)


def test_import_guard_blocks_relative_imports_and_strategy_builtins_lack_io():
    with pytest.raises(ImportError, match="relative"):
        R.guarded_import("x", {}, {}, (), 1)
    b = R.strategy_builtins()
    for name in ("open", "exec", "eval", "compile", "breakpoint", "input"):
        assert name not in b
    assert b["__import__"] is R.guarded_import and "len" in b


def _dense(**cols):
    return pd.DataFrame(cols, index=FRAME.index[:5])


def test_dense_to_events_converts_to_sparse_records():
    sig = np.array([0, 1, 0, -1, 0], dtype=np.int8)
    out = _dense(signal=sig, stop_dist=[9.0, 1.0, 9.0, 2.0, 9.0], tp_dist=[9.0, np.nan, 9.0, 3.0, 9.0],
                 exit=[False, False, True, False, False], extra=1)
    e = R.dense_to_events(out, FRAME.index[:5], allow_short=True, max_events=5)
    assert e.dtype == I.EVENTS_DTYPE
    assert e["bar"].tolist() == [1, 2, 3] and e["signal"].tolist() == [1, 0, -1]
    assert e["stop_dist"][0] == 1.0 and np.isnan(e["stop_dist"][1]) and e["tp_dist"][2] == 3.0
    assert e["exit"].tolist() == [False, True, False]
    no_exit = _dense(signal=np.zeros(5, dtype=np.int64), stop_dist=np.nan, tp_dist=np.nan)
    assert len(R.dense_to_events(no_exit, FRAME.index[:5], allow_short=False, max_events=5)) == 0


@pytest.mark.parametrize("make,kind", [
    (lambda: [1, 2, 3], "schema"),
    (lambda: _dense(signal=np.zeros(5, np.int8), stop_dist=1.0), "schema"),
    (lambda: _dense(signal=np.zeros(5, np.int8), stop_dist=1.0, tp_dist=np.nan).iloc[1:], "index"),
    (lambda: _dense(signal=np.zeros(5, np.int8), stop_dist=1.0, tp_dist=np.nan).reset_index(drop=True), "index"),
    (lambda: _dense(signal=np.zeros(5), stop_dist=1.0, tp_dist=np.nan), "dtype"),
    (lambda: _dense(signal=np.zeros(5, bool), stop_dist=1.0, tp_dist=np.nan), "dtype"),
    (lambda: _dense(signal=np.zeros(5, np.int8), stop_dist="a", tp_dist=np.nan), "dtype"),
    (lambda: _dense(signal=np.zeros(5, np.int8), stop_dist=1.0, tp_dist=np.nan, exit=np.zeros(5)), "dtype"),
    (lambda: _dense(signal=np.array([0, 2, 0, 0, 0]), stop_dist=1.0, tp_dist=np.nan), "value"),
    (lambda: _dense(signal=np.array([0, 257, 0, 0, 0]), stop_dist=1.0, tp_dist=np.nan), "value"),  # no int8 wrap
    (lambda: _dense(signal=pd.array([0, None, 0, 0, 0], dtype="Int8"), stop_dist=1.0, tp_dist=np.nan), "value"),
    (lambda: _dense(signal=np.array([0, 1, 0, 0, 0]), stop_dist=np.nan, tp_dist=np.nan), "value"),
    (lambda: _dense(signal=np.array([0, 1, 0, 0, 0]), stop_dist=np.inf, tp_dist=np.nan), "value"),
    (lambda: _dense(signal=np.array([0, 1, 0, 0, 0]), stop_dist=-1.0, tp_dist=np.nan), "value"),
    (lambda: _dense(signal=np.array([0, 1, 0, 0, 0]), stop_dist=1.0, tp_dist=0.0), "value"),
    (lambda: _dense(signal=np.array([0, -1, 0, 0, 0]), stop_dist=1.0, tp_dist=np.nan), "value"),
])
def test_dense_to_events_rejects_contract_violations(make, kind):
    with pytest.raises(R.RunFailure) as exc:
        R.dense_to_events(make(), FRAME.index[:5], allow_short=False, max_events=5)
    assert exc.value.kind == kind


def test_dense_to_events_enforces_max_events():
    out = _dense(signal=np.ones(5, np.int8), stop_dist=1.0, tp_dist=np.nan)
    with pytest.raises(R.RunFailure) as exc:
        R.dense_to_events(out, FRAME.index[:5], allow_short=False, max_events=4)
    assert exc.value.kind == "too_many_events"


def test_scramble_frame_is_frozen_and_keeps_the_past():
    df = synthetic_frame(200, seed=11)
    a = R.scramble_frame(df, 120, 42)
    b = R.scramble_frame(df, 120, 42)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(a.iloc[:120], df.iloc[:120])
    assert a.index.equals(df.index)
    assert not np.allclose(a["close"].to_numpy()[120:], df["close"].to_numpy()[120:])
    # the frozen algorithm, written out independently
    rng = np.random.default_rng(42)
    src = rng.permutation(np.arange(120, 200))
    c = df["close"].to_numpy()
    prev = c[src - 1]
    new_close = c[119] * np.cumprod(c[src] / prev)
    np.testing.assert_allclose(a["close"].to_numpy()[120:], new_close, rtol=1e-12)
    new_prev = np.r_[c[119], new_close[:-1]]
    np.testing.assert_allclose(a["open"].to_numpy()[120:], new_prev * df["open"].to_numpy()[src] / prev, rtol=1e-12)
    np.testing.assert_array_equal(a["volume"].to_numpy()[120:], df["volume"].to_numpy()[src])
    assert (a["high"] >= a[["open", "close"]].max(axis=1) - 1e-12).all()
    assert (a["low"] <= a[["open", "close"]].min(axis=1) + 1e-12).all()
    assert not R.scramble_frame(df, 120, 43)["close"].equals(a["close"])
    pd.testing.assert_frame_equal(R.scramble_frame(df, 200, 1), df)
    with pytest.raises(ValueError):
        R.scramble_frame(df, 0, 1)


# ── end to end through LocalSubprocessExecutor (jobs run concurrently) ──────
def _full(rid="full1", params=None):
    return I.SandboxRun(rid, dict(P if params is None else params))


JOBS = {
    "benign": make_job(BENIGN, (I.SandboxRun("cut50", P, cut=150),
                                I.SandboxRun("scramble", P, scramble=I.ScrambleSpec(150, 9)), _full()), FRAME),
    "raises_on_cuts": make_job(_src_with('''
        def generate_signals(df, params):
            if len(df) < 300:
                raise ZeroDivisionError("only short frames fail")
            out = pd.DataFrame(index=df.index)
            out["signal"] = np.zeros(len(df), dtype="int8"); out.iloc[10, 0] = 1
            out["stop_dist"] = 1.0
            out["tp_dist"] = np.nan
            return out
    '''), (I.SandboxRun("cut50", {"n": 10}, cut=150), _full("full1", {"n": 10})), FRAME),
    "sleeps": make_job(_src_with('''
        def generate_signals(df, params):
            while True:
                pass
    '''), (_full("full1", {"n": 10}),), FRAME, per_run_timeout_s=2.0),
    "import_os": make_job(_src_with("import os\ndef generate_signals(df, params):\n    return None\n"),
                          (_full("full1", {"n": 10}),), FRAME),
    "import_socket": make_job(_src_with("import socket\ndef generate_signals(df, params):\n    return None\n"),
                              (_full("full1", {"n": 10}),), FRAME),
    "import_subprocess": make_job(_src_with("from subprocess import run\ndef generate_signals(df, params):\n"
                                            "    return None\n"), (_full("full1", {"n": 10}),), FRAME),
    "runtime_import": make_job(_src_with('''
        def generate_signals(df, params):
            import importlib
            return importlib
    '''), (_full("full1", {"n": 10}),), FRAME),
    "open_builtin": make_job(_src_with('''
        def generate_signals(df, params):
            return open("../../in/strategy.py").read()
    '''), (_full("full1", {"n": 10}),), FRAME),
    "reads_full_frame": make_job(_src_with('''
        def generate_signals(df, params):
            pd.read_csv("../../in/plan.json", sep="\\x07", header=None, quoting=3)   # control: the relative path is right
            return pd.read_parquet("../../in/frame.parquet")
    '''), (_full("full1", {"n": 10}),), FRAME),
    "no_params": make_job(strategy("def generate_signals(df, params):\n    return None\n"),
                          (_full("full1", {}),), FRAME),
    "syntax_error": make_job(strategy("PARAMS = {\n"), (_full("full1", {}),), FRAME),
    "float_signal": make_job(_src_with('''
        def generate_signals(df, params):
            return pd.DataFrame({"signal": 0.0, "stop_dist": 1.0, "tp_dist": np.nan}, index=df.index)
    '''), (_full("full1", {"n": 10}),), FRAME),
    "short_not_allowed": make_job(_src_with('''
        def generate_signals(df, params):
            s = np.zeros(len(df), dtype="int8"); s[5] = -1
            return pd.DataFrame({"signal": s, "stop_dist": 1.0, "tp_dist": np.nan}, index=df.index)
    '''), (_full("full1", {"n": 10}),), FRAME),
    "sys_exit": make_job(_src_with('''
        def generate_signals(df, params):
            raise SystemExit(3)
    '''), (_full("full1", {"n": 10}),), FRAME),
    "budget": make_job(_src_with('''
        def generate_signals(df, params):
            total = 0
            for i in range(3_000_000):
                total += i
            s = np.zeros(len(df), dtype="int8"); s[1] = 1
            return pd.DataFrame({"signal": s, "stop_dist": 1.0, "tp_dist": np.nan}, index=df.index)
    '''), (_full("full1", {"n": 10}), _full("full2", {"n": 10}), _full("full3", {"n": 10})), FRAME,
        job_timeout_s=I.RUNNER_BUDGET_MARGIN_S + 0.5),
    "n_bars_mismatch": make_job(BENIGN, (_full(),), FRAME, n_bars=N - 1),
}


@pytest.fixture(scope="module")
def results():
    return run_jobs(JOBS)


def test_benign_strategy_runs_every_plan_entry(results):
    res, dec = results["benign"]
    assert res.exit_code == 0 and not res.timed_out
    assert dec.errors == () and dec.job_ok
    assert set(dec.events) == {"cut50", "scramble", "full1"}
    full = dec.events["full1"]
    assert (full["signal"] != 0).sum() > 0 and full["exit"].any()
    assert np.all(full["stop_dist"][full["signal"] != 0] == 0.5)
    assert np.all(dec.events["cut50"]["bar"] < 150)
    assert set(dec.versions) == {"python", "numpy", "pandas", "pyarrow"}
    assert {"cut50", "scramble", "full1", "_total"} <= set(dec.timings)
    assert dec.sensitive_env_names == ()


def test_a_raising_run_is_reported_per_run_and_does_not_stop_the_rest(results):
    _, dec = results["raises_on_cuts"]
    (err,) = errors_of(dec, "cut50")
    assert err.kind == "exception"
    assert err.message.startswith("ZeroDivisionError: only short frames fail (strategy.py line 6)")
    assert dec.ok("full1") and dec.job_ok


def test_a_sleeping_strategy_is_killed_at_the_per_run_timeout(results):
    res, dec = results["sleeps"]
    (err,) = errors_of(dec, "full1")
    assert err.kind == "timeout" and "per-run limit of 2 s" in err.message
    assert dec.job_ok and not res.timed_out                      # status.json was still written
    assert res.wall_s < 30


@pytest.mark.parametrize("name,module", [("import_os", "os"), ("import_socket", "socket"),
                                         ("import_subprocess", "subprocess")])
def test_dangerous_imports_in_strategy_are_blocked(results, name, module):
    _, dec = results[name]
    (err,) = errors_of(dec, "full1")
    assert err.kind == "import"
    assert f"import of '{module}' is blocked" in err.message


def test_runtime_imports_and_open_are_blocked_too(results):
    (err,) = errors_of(results["runtime_import"][1], "full1")
    assert err.kind == "exception" and "import of 'importlib' is blocked" in err.message
    (err,) = errors_of(results["open_builtin"][1], "full1")
    assert err.kind == "exception" and err.message.startswith("NameError")


def test_the_full_frame_is_deleted_before_strategy_code_runs(results):
    (err,) = errors_of(results["reads_full_frame"][1], "full1")
    assert err.kind == "exception" and ("FileNotFoundError" in err.message or "No such file" in err.message)
    # in/plan.json (same directory) was readable, so the failure is the deleted frame, not a wrong path
    assert "frame.parquet" in err.message and "plan.json" not in err.message.splitlines()[0]


def test_missing_params_syntax_errors_and_bad_outputs_get_their_kinds(results):
    assert [e.kind for e in errors_of(results["no_params"][1], "full1")] == ["params"]
    (err,) = errors_of(results["syntax_error"][1], "full1")
    assert err.kind == "import" and err.message.startswith("SyntaxError")
    (err,) = errors_of(results["float_signal"][1], "full1")
    assert err.kind == "dtype" and "astype('int8')" in err.message
    (err,) = errors_of(results["short_not_allowed"][1], "full1")
    assert err.kind == "value" and "allow_short" in err.message
    (err,) = errors_of(results["sys_exit"][1], "full1")
    assert err.kind == "exception" and err.message.startswith("SystemExit")


def test_runs_past_the_job_budget_are_not_started(results):
    _, dec = results["budget"]
    assert dec.ok("full1")
    assert [e.kind for e in errors_of(dec, "full2")] == ["budget"]
    assert [e.kind for e in errors_of(dec, "full3")] == ["budget"]


def test_frame_row_count_mismatch_is_a_job_level_internal_error(results):
    _, dec = results["n_bars_mismatch"]
    (err,) = errors_of(dec, None)
    assert err.kind == "internal" and "n_bars" in err.message
    assert not dec.job_ok and dec.events == {}


# ── parent_main in-process (children stubbed) ───────────────────────────────
def _stage_inputs(root, job):
    for rel, data in sandbox_io.encode_job(job).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    (root / "work").mkdir()
    (root / "out").mkdir()
    return root / "in", root / "out"


def test_status_json_stays_within_its_limit_when_every_run_fails_with_non_ascii_text(tmp_path, monkeypatch):
    # json.dumps escapes an astral char as 12 bytes: 16 runs x 2000 such chars used to make
    # status.json ~0.4-0.6 MB > MAX_STATUS_BYTES, so the trusted side rejected the WHOLE job
    # as status_invalid and every per-run error was lost.
    runs = tuple(I.SandboxRun(f"nb{i:02d}", {"n": 10}) for i in range(I.MAX_RUNS_PER_JOB))
    in_dir, out_dir = _stage_inputs(tmp_path, make_job(BENIGN, runs, FRAME))
    msg = "ValueError: " + "\U0001F680" * 3000 + "\x01"
    monkeypatch.setattr(R, "_run_child", lambda run, frame, i, o, t, **kw: (("exception", msg), 0))
    R.parent_main(in_dir, out_dir)
    raw = (out_dir / "status.json").read_bytes()
    assert len(raw) <= I.MAX_STATUS_BYTES
    st = sandbox_io.decode_status(raw)
    assert [(e["run_id"], e["kind"]) for e in st["errors"]] == [(r.run_id, "exception") for r in runs]
    for e in st["errors"]:
        assert e["message"].startswith("ValueError: ???") and len(e["message"]) <= I.MAX_ERROR_MESSAGE_CHARS


def test_a_run_cut_short_by_the_job_budget_is_kind_budget_not_timeout(tmp_path, monkeypatch):
    """Per-run kills say `timeout`; a kill because the JOB budget ran out says `budget` (p1.2)."""
    sleeper = _src_with('''
        def generate_signals(df, params):
            while True:
                pass
    ''')
    job = make_job(sleeper, (_full("full1", {"n": 10}), _full("full2", {"n": 10})), FRAME,
                   per_run_timeout_s=30.0, job_timeout_s=I.RUNNER_BUDGET_MARGIN_S + 0.5)
    in_dir, out_dir = _stage_inputs(tmp_path, job)
    monkeypatch.setattr(R, "RUNNER_BUDGET_MARGIN_S", 3)         # hard stop = 0.5 + 3 - 1 = 2.5 s < 30 s
    monkeypatch.setattr(R, "STATUS_RESERVE_S", 1)
    R.parent_main(in_dir, out_dir)
    st = sandbox_io.decode_status((out_dir / "status.json").read_bytes())
    kinds = {e["run_id"]: (e["kind"], e["message"]) for e in st["errors"]}
    assert kinds["full1"][0] == "budget" and "job time budget ran out" in kinds["full1"][1]
    assert kinds["full2"][0] == "budget" and kinds["full2"][1].startswith("not started")
    assert set(I.RUN_TIMEOUT_KINDS) == {"timeout", "budget"}


def test_runner_fails_closed_if_the_full_frame_cannot_be_deleted(tmp_path, monkeypatch):
    in_dir, out_dir = _stage_inputs(tmp_path, make_job(BENIGN, (_full(),), FRAME))
    frame_file = in_dir / I.IN_FRAME
    real_remove = R._remove
    monkeypatch.setattr(R, "_remove", lambda p: None if p == frame_file else real_remove(p))
    started = []
    monkeypatch.setattr(R, "_run_child", lambda *a: started.append(a) or (None, 0))
    R.parent_main(in_dir, out_dir)
    st = sandbox_io.decode_status((out_dir / "status.json").read_bytes())
    assert started == []                                   # no strategy code ran next to the full frame
    assert [(e["run_id"], e["kind"]) for e in st["errors"]] == [(None, "internal")]
    assert "frame.parquet" in st["errors"][0]["message"]


def test_out_dir_holds_only_what_the_parent_writes(tmp_path, monkeypatch):
    """A strategy process that drops or rewrites files in out/ cannot plant run outputs:
    events live in parent memory until the end, strays are killed, out/ is cleared first."""
    in_dir, out_dir = _stage_inputs(tmp_path, make_job(BENIGN, (_full(),), FRAME))
    planted = out_dir / "cut20.events.npy"
    sweeps = []
    monkeypatch.setattr(R, "_kill_strays", lambda keep: sweeps.append(set(keep)) or 0)

    def fake_child(run, frame, i, o, t, *, collected=None, keep=None, **kw):
        planted.write_bytes(b"forged")                 # what a detached process would do
        collected[run["run_id"]] = b"real-events"
        return None, 1

    monkeypatch.setattr(R, "_run_child", fake_child)
    R.parent_main(in_dir, out_dir)
    assert sorted(p.name for p in out_dir.iterdir()) == ["full1.events.npy", "status.json"]
    assert (out_dir / "full1.events.npy").read_bytes() == b"real-events"
    assert sweeps                                       # a final stray sweep always runs


def test_kill_strays_spares_the_keep_set_and_own_ancestry(monkeypatch):
    killed = []
    monkeypatch.setattr(R.os, "name", "posix")
    monkeypatch.setattr(R.Path, "is_dir", lambda self: True)
    monkeypatch.setattr(R, "_proc_pids", lambda: {1, 50, 77, 88})
    monkeypatch.setattr(R, "_ancestry", lambda pid: {1, 50})
    monkeypatch.setattr(R.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    sent = R._kill_strays({77})
    assert set(killed) == {88} and sent == len(killed)


def test_local_executor_passes_no_inherited_env(monkeypatch):
    monkeypatch.setenv("SAJIM_API_TOKEN", "do-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-leak")
    ex = LocalSubprocessExecutor()
    env = ex._env(__import__("pathlib").Path("."))
    assert set(env) <= {"PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONUTF8"}
    assert "do-not-leak" not in json.dumps(env)


def test_runner_reports_sensitive_env_names_but_never_values(tmp_path):
    job = make_job(BENIGN, (_full(),), FRAME)
    for rel, data in sandbox_io.encode_job(job).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    (tmp_path / "work").mkdir()
    (tmp_path / "out").mkdir()
    env = {"PATH": os.path.dirname(sys.executable), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "SAJIM_FAKE_TOKEN": "s3cret-value", "MODAL_TOKEN_ID": "s3cret-value", "UNRELATED": "x"}
    argv = I.runner_argv(tmp_path.as_posix())
    argv[0] = sys.executable
    subprocess.run(argv, env=env, cwd=tmp_path / "work", timeout=120, check=True, stdin=subprocess.DEVNULL)
    raw = (tmp_path / "out" / "status.json").read_bytes()
    assert b"s3cret-value" not in raw
    st = sandbox_io.decode_status(raw)
    assert st["sensitive_env_names"] == ["MODAL_TOKEN_ID", "SAJIM_FAKE_TOKEN"]
    assert not (tmp_path / "in" / "frame.parquet").exists()
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["full1.events.npy", "status.json"]
    assert list((tmp_path / "work").iterdir()) == []


def test_local_executor_cleans_up_and_bounds_reads(tmp_path):
    ex = LocalSubprocessExecutor(base_dir=tmp_path / "sb")
    res = ex.run(make_job(BENIGN, (_full(),), FRAME))
    assert set(res.files) == {"status.json", "full1.events.npy"}
    assert ex.last_root is not None and not ex.last_root.exists()
    keep = LocalSubprocessExecutor(base_dir=tmp_path / "sb", keep_dirs=True)
    keep.run(make_job(BENIGN, (_full(),), FRAME))
    assert (keep.last_root / "out" / "status.json").exists()


def test_job_timeout_kills_the_whole_runner(monkeypatch):
    # The real runner always finishes inside job_timeout_s (it clamps run timeouts to its budget),
    # so a hung runner is simulated to exercise the executor's own kill path.
    monkeypatch.setattr(sandbox_io, "runner_source", lambda: b"while True:\n    pass\n")
    job = make_job(BENIGN, (_full(),), FRAME, job_timeout_s=4.0)
    t0 = time.monotonic()
    res = LocalSubprocessExecutor().run(job)
    assert res.timed_out and res.exit_code is None
    assert time.monotonic() - t0 < 30
    dec = sandbox_io.decode_result(job, res)
    assert not dec.job_ok and "timeout" in {e.kind for e in errors_of(dec, None)}


def test_plan_run_ids_must_fully_match(tmp_path):
    """'^...$' with re.match accepts a trailing newline; the runner uses fullmatch (p1.2)."""
    base = {"schema": R.PLAN_SCHEMA, "n_bars": 10, "per_run_timeout_s": 14, "job_budget_s": 255,
            "max_events_per_run": 10}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({**base, "runs": [{"run_id": "full1", "params": {}}]}), encoding="utf-8")
    assert R._load_plan(path)["runs"][0]["run_id"] == "full1"
    path.write_text(json.dumps({**base, "runs": [{"run_id": "full1\n", "params": {}}]}), encoding="utf-8")
    with pytest.raises(R._JobFailure, match="bad or duplicate run_id"):
        R._load_plan(path)
    assert R.child_main("full1\n", tmp_path, tmp_path, tmp_path / "frame.parquet") == 2
