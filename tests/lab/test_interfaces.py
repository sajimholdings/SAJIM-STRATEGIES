"""The frozen P1 cross-component contract (sajim_quant/lab/interfaces.py) stays as INTERFACES.md says."""

import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sajim_quant.lab import gates as G
from sajim_quant.lab import interfaces as I

LAB_DIR = Path(I.__file__).resolve().parent


def test_events_dtype_is_packed_and_npy_roundtrips_without_pickle():
    assert I.EVENTS_DTYPE.names == ("bar", "signal", "stop_dist", "tp_dist", "exit")
    assert I.EVENTS_DTYPE.itemsize == 26 and not I.EVENTS_DTYPE.hasobject
    ev = np.zeros(3, dtype=I.EVENTS_DTYPE)
    ev["bar"] = [3, 7, 9]
    ev["signal"] = [1, 0, -1]
    ev["tp_dist"] = np.nan
    buf = io.BytesIO()
    np.save(buf, ev, allow_pickle=False)
    raw = buf.getvalue()
    f = io.BytesIO(raw)
    assert np.lib.format.read_magic(f) == (1, 0)
    shape, fortran, dtype = np.lib.format.read_array_header_1_0(f, max_header_size=10000)
    assert (shape, fortran, dtype) == ((3,), False, I.EVENTS_DTYPE)
    assert f.tell() + 3 * 26 == len(raw)
    back = np.load(io.BytesIO(raw), allow_pickle=False)
    assert back.dtype == I.EVENTS_DTYPE and list(back["bar"]) == [3, 7, 9]
    assert I.empty_events().dtype == I.EVENTS_DTYPE and len(I.empty_events()) == 0


def test_sandbox_layout_and_limits():
    assert I.runner_argv("/tmp") == ["python", "-I", "-X", "utf8", "/tmp/in/sandbox_runner.py", "/tmp/in", "/tmp/out"]
    assert I.events_file("cut20") == "cut20.events.npy"
    assert I.JOB_TIMEOUT_S == G.LAB_SIGNALS_TIMEOUT_S and I.RUNNER_BUDGET_MARGIN_S < I.JOB_TIMEOUT_S
    assert I.JOB_BUDGET_S == I.JOB_TIMEOUT_S - I.RUNNER_BUDGET_MARGIN_S == 255
    # p1.2: one per-run limit for every job, below CONTRACT's outer cap, such that a full job whose
    # runs all use their whole limit still starts every run inside the job budget (no budget kills)
    assert I.PER_RUN_TIMEOUT_S == 14 and I.PER_RUN_TIMEOUT_S <= G.MAX_SIGNAL_RUNTIME_S
    assert I.MAX_RUNS_PER_JOB * (I.PER_RUN_TIMEOUT_S + I.RUNNER_PER_RUN_OVERHEAD_S) <= I.JOB_BUDGET_S
    assert I.PER_RUN_TIMEOUT_S - I.CHILD_STARTUP_ALLOWANCE_S >= 10
    assert set(I.RUN_TIMEOUT_KINDS) == {"timeout", "budget"} and set(I.RUN_TIMEOUT_KINDS) <= set(I.ERROR_KINDS)
    # 8 check runs fit in one job; so do the adaptive cuts next to up to 2 * MAX_PARAMS neighbours
    from sajim_quant.lab.causality import ADAPTIVE_CUTS
    assert I.MAX_RUNS_PER_JOB >= max(8, 2 * G.MAX_PARAMS + ADAPTIVE_CUTS)
    # any frame's events fit the per-run file limit
    assert I.MAX_FRAME_BARS * I.EVENTS_DTYPE.itemsize + 128 <= I.MAX_EVENTS_FILE_BYTES * 2
    assert {"timeout", "decode", "status_invalid", "missing_output"} <= set(I.ERROR_KINDS)


def test_sandbox_run_and_job_validation():
    p = {"rr": 2.0}
    I.SandboxRun("full1", p)
    with pytest.raises(ValueError):
        I.SandboxRun("Full-1", p)
    with pytest.raises(ValueError):
        I.SandboxRun("x", p, cut=10, scramble=I.ScrambleSpec(5, 1))
    with pytest.raises(ValueError):
        I.SandboxRun("x", p, scramble=I.ScrambleSpec(0, 1))
    assert I.SandboxRun("cut20", p, cut=40).n_bars(200) == 40
    assert I.SandboxRun("full1", p).n_bars(200) == 200
    runs = (I.SandboxRun("a", p), I.SandboxRun("b", p))
    I.SandboxJob(b"pq", 200, b"src", b"ind", runs, allow_short=False)
    with pytest.raises(ValueError):
        I.SandboxJob(b"pq", 200, b"src", b"ind", runs + (I.SandboxRun("a", p),), allow_short=False)
    with pytest.raises(ValueError):
        I.SandboxJob(b"pq", 200, b"x" * (G.MAX_SOURCE_BYTES + 1), b"ind", runs, allow_short=False)
    with pytest.raises(ValueError):
        I.SandboxJob(b"pq", 200, b"src", b"ind", tuple(I.SandboxRun(f"r{i}", p) for i in range(17)),
                     allow_short=False)


def test_decoded_job_ok_semantics():
    d = I.DecodedJob(events={"full1": I.empty_events(), "full2": I.empty_events()},
                     errors=(I.RunError("full2", "value", "bad"),), timings={}, versions={},
                     sensitive_env_names=())
    assert d.ok("full1") and not d.ok("full2") and not d.ok("cut20") and d.job_ok
    d2 = I.DecodedJob(events={}, errors=(I.RunError(None, "timeout", "x"),), timings={}, versions={},
                      sensitive_env_names=())
    assert not d2.job_ok


def test_keys_names_and_request_ids():
    sha = "ab" * 32
    assert I.trial_key("lab_abc", 2) == "trial:lab_abc:2"
    assert I.holdout_touch_key("lab_abc", 3) == "holdout_touch:lab_abc:3"
    assert I.holdout_sha_key(sha) == f"holdout_sha:{sha}"
    for key in (I.trial_key("lab_abc", 1), I.holdout_sha_key(sha)):
        name = I.slot_filename(key)
        assert ":" not in name and name.endswith(".json")      # NTFS-safe
    with pytest.raises(ValueError):
        I.trial_key("beep_vacuum", 1)
    with pytest.raises(ValueError):
        I.holdout_sha_key("XYZ")
    with pytest.raises(ValueError):
        I.slot_filename("trial:lab_abc:0")
    rid = I.derive_request_id("lab_abc", sha, "dev")
    assert re.fullmatch(r"[0-9a-f]{32}", rid) and I.REQUEST_ID_RE.match(rid)
    assert rid == I.derive_request_id("lab_abc", sha, "dev")
    assert len({I.derive_request_id("lab_abc", sha, k) for k in I.REQUEST_KINDS}) == len(I.REQUEST_KINDS)
    assert I.derive_request_id("lab_abd", sha, "dev") != rid
    stamp = I.utc_stamp()
    assert re.fullmatch(r"\d{8}T\d{12}Z", stamp)
    assert I.ledger_filename(stamp, rid, "dev_trial") == f"{stamp}_{rid}_dev_trial.json"
    with pytest.raises(ValueError):
        I.ledger_filename(stamp, rid, "trial")
    assert I.result_file_name("lab_x1y", "XAUUSD", "M15", "dev") == "lab_x1y_XAUUSD_M15_dev_result.json"


def test_patterns_are_full_matches_a_trailing_newline_never_passes():
    """'$' also matches before a final newline under re.match; every check uses fullmatch."""
    sha = "ab" * 32
    with pytest.raises(ValueError):
        I.trial_key("lab_abc\n", 1)
    with pytest.raises(ValueError):
        I.holdout_sha_key(sha + "\n")
    with pytest.raises(ValueError):
        I.slot_filename(I.trial_key("lab_abc", 1) + "\n")
    with pytest.raises(ValueError):
        I.ledger_filename(I.utc_stamp(), "abcdefgh\n", "check")
    with pytest.raises(ValueError):
        I.SandboxRun("full1\n", {})
    with pytest.raises(ValueError):
        I.derive_request_id("lab_abc\n", sha, "dev")


def test_submission_recomputes_shas():
    s = I.Submission("lab_abc", "lab_abc", b"PARAMS = {}\n", b"# spec\n", {"code_sha256": "forged"})
    assert s.code_sha256 == I.sha256_hex(b"PARAMS = {}\n") and s.sha12 == s.code_sha256[:12]
    assert s.spec_sha256 == I.sha256_hex(b"# spec\n")


def test_errors_map_to_frozen_http_statuses():
    assert (I.WrongScope.status, I.HoldoutAlreadyRun.status, I.BudgetExhausted.status) == (
        G.HTTP_WRONG_TOKEN_SCOPE, G.HTTP_HOLDOUT_ALREADY_RUN, G.HTTP_DEV_BUDGET_EXHAUSTED)
    assert (I.DevNotPassed.status, I.PayloadTooLarge.status, I.ContractRefused.status) == (412, 413, 422)
    e = I.BudgetExhausted("no trials left", code="holdout_burned", reasons=["3/3 touches"])
    assert e.detail() == {"code": "holdout_burned", "message": "no trials left", "reasons": ["3/3 touches"]}
    assert I.ContractRefused("x").detail()["code"] == "contract_refused"


def test_runner_env_and_stage_constants():
    assert not set(I.CHILD_ENV_ALLOWLIST) & set(I.CHILD_ENV_FORBIDDEN)
    assert {"SAJIM_LAB_TOKEN", "SAJIM_LAB_ID", "SAJIM_LAB_STAGE", "SAJIM_API_URL"} <= set(I.CHILD_ENV_ALLOWLIST)
    assert "SAJIM_JUDGE_TOKEN" in I.CHILD_ENV_FORBIDDEN and "ANTHROPIC_API_KEY" in I.CHILD_ENV_FORBIDDEN
    assert I.CLAUDE_TOOLS.split(",") == ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Skill"]
    assert set(I.LAB_WRITABLE) == set(I.MAX_TURNS) == {"spec", "code", "review"}
    assert I.LAB_WRITABLE["review"] == ("review.json",)
    assert I.MIN_CLAUDE_VERSION == (2, 1, 259)
    assert I.LAB_PHASE == 1


def test_short_coverage_refusals_are_allowed_universe_members():
    from sajim_quant.lab import splits as S
    assert len(I.SHORT_COVERAGE_REFUSED) == 7
    for sym, tf in I.SHORT_COVERAGE_REFUSED:
        assert S.is_allowed_symbol(sym) and S.is_allowed_tf(tf)


def test_protocols_are_structural():
    class FakeExec:
        def run(self, job):
            return I.SandboxResult(files={}, exit_code=0, timed_out=False, wall_s=0.0)

    assert isinstance(FakeExec(), I.SandboxExecutor)

    class FakeStore:
        def write_once(self, relpath, data): return True
        def write(self, relpath, data): return None
        def read(self, relpath): return None
        def listdir(self, reldir): return []
        def claim_slot(self, key, value): return True
        def read_slot(self, key): return None

    assert isinstance(FakeStore(), I.LedgerStore)


def test_sim_result_net_and_interfaces_doc_present():
    eq = pd.Series([10_000.0], index=pd.DatetimeIndex(["2025-01-01"]))
    r = I.SimResult(trades=[], equity=eq, untradable_stop=0, n_signals=0, n_entry_attempts=0,
                    window_end_closes=0, halted=False)
    assert r.net == 0.0 and json.dumps(r.notes) == "{}"
    doc = (LAB_DIR / "INTERFACES.md").read_text(encoding="utf-8")
    assert f"`{I.INTERFACES_VERSION}`" in doc
    for frozen in ("sajim.lab.scorecard/1", "sajim.lab.ledger/1", "sajim.lab.sandbox_plan/1",
                   "sajim.lab.sandbox_status/1", "error_max_turns", "X-Judge-Token"):
        assert frozen in doc
