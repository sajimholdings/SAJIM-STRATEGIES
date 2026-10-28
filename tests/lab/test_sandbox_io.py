"""Trusted sandbox I/O: encoding, the strict non-pickle decoder, status parsing, events_equal."""

from __future__ import annotations

import io
import json

import numpy as np
import pytest

from sajim_quant.lab import interfaces as I
from sajim_quant.lab import sandbox_io as S
from sajim_quant.lab import sandbox_runner as R

from _sandbox_testkit import STUB_INDICATORS, make_job, synthetic_frame

N = 100


def ev(rows):
    """rows: (bar, signal, stop, tp, exit)."""
    a = np.zeros(len(rows), dtype=I.EVENTS_DTYPE)
    for i, r in enumerate(rows):
        a[i] = r
    return a


def npy(arr, version=None, allow_pickle=False) -> bytes:
    buf = io.BytesIO()
    if version is None:
        np.save(buf, arr, allow_pickle=allow_pickle)
    else:
        np.lib.format.write_array(buf, arr, version=version, allow_pickle=allow_pickle)
    return buf.getvalue()


GOOD = ev([(3, 1, 0.5, np.nan, False), (7, 0, 9.9, 9.9, True), (9, 1, 0.25, 1.0, False)])


# ── encoding ────────────────────────────────────────────────────────────────
def test_frame_to_parquet_roundtrips_to_the_frame_the_strategy_sees(tmp_path):
    df = synthetic_frame(50)
    raw = S.frame_to_parquet(df)
    (tmp_path / "f.parquet").write_bytes(raw)
    import pyarrow.parquet as pq
    table = pq.read_table(tmp_path / "f.parquet")
    assert table.column_names == ["time", "open", "high", "low", "close", "volume"]
    assert str(table.schema.field("time").type) == "timestamp[us]"
    back = R.read_frame(tmp_path / "f.parquet")
    assert back.index.name == "time" and str(back.index.dtype) == "datetime64[ns]"
    assert back.index.tz is None
    assert (back.index == df.index).all()
    assert list(back.columns) == ["open", "high", "low", "close", "volume"]
    assert all(back[c].dtype == np.float64 for c in back.columns)
    np.testing.assert_array_equal(back.to_numpy(), df.to_numpy())


def test_frame_to_parquet_rejects_bad_frames():
    df = synthetic_frame(20)
    with pytest.raises(ValueError):
        S.frame_to_parquet(df.tz_localize("UTC"))
    with pytest.raises(ValueError):
        S.frame_to_parquet(df.iloc[::-1])
    with pytest.raises(ValueError):
        S.frame_to_parquet(df.drop(columns=["volume"]))
    with pytest.raises(ValueError):
        S.frame_to_parquet(df.reset_index())


def test_plan_json_shape_and_param_types():
    runs = (I.SandboxRun("cut20", {"n": 20, "x": 2.0}, cut=20),
            I.SandboxRun("scramble", {"n": 20, "x": 2.0}, scramble=I.ScrambleSpec(50, 123)),
            I.SandboxRun("full1", {"n": np.int64(20), "x": np.float64(2.0)}))
    job = make_job(b"PARAMS = {}\n", runs, synthetic_frame(N))
    plan = json.loads(S.plan_json(job))
    assert plan["schema"] == I.PLAN_SCHEMA and plan["n_bars"] == N and plan["allow_short"] is False
    assert plan["per_run_timeout_s"] == 60 and plan["job_budget_s"] == 300 - I.RUNNER_BUDGET_MARGIN_S == 255
    # a production job (no override) carries the one per-run limit every lab job uses (p1.2)
    prod = I.SandboxJob(frame_parquet=job.frame_parquet, n_bars=N, strategy_py=b"PARAMS = {}\n",
                        indicators_py=b"", runs=runs, allow_short=False)
    assert json.loads(S.plan_json(prod))["per_run_timeout_s"] == I.PER_RUN_TIMEOUT_S == 14
    assert plan["max_events_per_run"] == N
    assert [r["run_id"] for r in plan["runs"]] == ["cut20", "scramble", "full1"]
    assert plan["runs"][0]["cut"] == 20 and plan["runs"][0]["scramble"] is None
    assert plan["runs"][1]["scramble"] == {"start": 50, "seed": 123} and plan["runs"][1]["cut"] is None
    for r in plan["runs"]:
        assert type(r["params"]["n"]) is int and type(r["params"]["x"]) is float
    text = S.plan_json(job).decode()
    assert '"n": 20' in text and '"x": 2.0' in text


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "3", None])
def test_plan_json_rejects_non_numeric_or_non_finite_params(bad):
    job = make_job(b"", (I.SandboxRun("full1", {"p": bad}),), synthetic_frame(10))
    with pytest.raises(ValueError):
        S.plan_json(job)


def test_encode_job_has_exactly_the_five_inputs():
    src = b"PARAMS = {'a': 1}\n# exact bytes \xc3\xa9\n"
    job = make_job(src, (I.SandboxRun("full1", {"a": 1}),), synthetic_frame(10))
    files = S.encode_job(job)
    assert sorted(files) == sorted(["in/frame.parquet", "in/strategy.py", "in/indicators.py",
                                    "in/plan.json", "in/sandbox_runner.py"])
    assert files["in/strategy.py"] == src
    assert files["in/indicators.py"] == STUB_INDICATORS
    assert files["in/sandbox_runner.py"] == S.runner_source() == open(R.__file__, "rb").read()
    assert files["in/frame.parquet"] == job.frame_parquet


def test_expected_output_names_and_limits():
    runs = (I.SandboxRun("cut20", {}, cut=5), I.SandboxRun("full1", {}))
    job = make_job(b"", runs, synthetic_frame(10))
    assert S.expected_output_names(job) == ["status.json", "cut20.events.npy", "full1.events.npy"]
    assert S.output_limit("status.json") == I.MAX_STATUS_BYTES
    assert S.output_limit("full1.events.npy") == I.MAX_EVENTS_FILE_BYTES


def test_indicators_source_reads_the_trusted_file_lazily():
    path = __import__("pathlib").Path(S.__file__).with_name("indicators.py")
    if not path.exists():
        with pytest.raises(FileNotFoundError):
            S.indicators_source()
    else:
        assert S.indicators_source() == path.read_bytes()


# ── decode_events: accepted ─────────────────────────────────────────────────
def test_decode_events_accepts_valid_and_normalises():
    out = S.decode_events(npy(GOOD), N, allow_short=False)
    assert out.dtype == I.EVENTS_DTYPE
    np.testing.assert_array_equal(out["bar"], [3, 7, 9])
    assert np.isnan(out["stop_dist"][1]) and np.isnan(out["tp_dist"][1])   # signal == 0 row normalised
    assert out["stop_dist"][0] == 0.5 and np.isnan(out["tp_dist"][0]) and out["tp_dist"][2] == 1.0
    assert out["exit"].tolist() == [False, True, False]


def test_decode_events_accepts_empty_and_v2_headers_and_shorts_when_allowed():
    assert len(S.decode_events(npy(np.zeros(0, I.EVENTS_DTYPE)), N, allow_short=False)) == 0
    assert len(S.decode_events(npy(GOOD, version=(2, 0)), N, allow_short=False)) == 3
    short = ev([(1, -1, 0.5, np.nan, False)])
    assert S.decode_events(npy(short), N, allow_short=True)["signal"][0] == -1


# ── decode_events: rejected ─────────────────────────────────────────────────
PICKLE_HITS = []


class _Payload:
    """Unpickling this would run code in the trusted process."""

    def __reduce__(self):
        return (PICKLE_HITS.append, ("pwned",))


def test_decode_events_never_unpickles_a_pickle_bearing_file():
    obj = np.array([_Payload(), _Payload()], dtype=object)
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(npy(obj, allow_pickle=True), N, allow_short=False)
    rec = np.zeros(1, dtype=[("bar", "<i8"), ("signal", "i1"), ("stop_dist", "O"), ("tp_dist", "<f8"), ("exit", "?")])
    rec["stop_dist"][0] = _Payload()
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(npy(rec, allow_pickle=True), N, allow_short=False)
    assert PICKLE_HITS == []


@pytest.mark.parametrize("arr", [
    np.zeros(3, dtype=np.float64),
    np.zeros(3, dtype=[("bar", ">i8"), ("signal", "i1"), ("stop_dist", "<f8"), ("tp_dist", "<f8"), ("exit", "?")]),
    np.zeros(3, dtype=np.dtype([("bar", "<i8"), ("signal", "i1"), ("stop_dist", "<f8"), ("tp_dist", "<f8"),
                                ("exit", "?")], align=True)),
    np.zeros(3, dtype=[("bar", "<i4"), ("signal", "i1"), ("stop_dist", "<f8"), ("tp_dist", "<f8"), ("exit", "?")]),
    np.zeros(3, dtype=[("signal", "i1"), ("bar", "<i8"), ("stop_dist", "<f8"), ("tp_dist", "<f8"), ("exit", "?")]),
    np.zeros(3, dtype=[("bar", "<i8"), ("signal", "i1"), ("stop_dist", "<f8"), ("tp_dist", "<f8"), ("exit", "?"),
                       ("extra", "<f8")]),
    np.zeros((3, 1), dtype=I.EVENTS_DTYPE),
], ids=["float", "big_endian_bar", "aligned", "int32_bar", "field_order", "extra_field", "2d"])
def test_decode_events_rejects_wrong_dtype_or_shape(arr):
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(npy(arr), N, allow_short=True)


@pytest.mark.parametrize("rows,why", [
    ([(1, 1, np.nan, np.nan, False)], "NaN stop"),
    ([(1, 1, np.inf, np.nan, False)], "inf stop"),
    ([(1, 1, -0.5, np.nan, False)], "negative stop"),
    ([(1, 1, 0.0, np.nan, False)], "zero stop"),
    ([(1, 1, 0.5, 0.0, False)], "zero tp"),
    ([(1, 1, 0.5, np.inf, False)], "inf tp"),
    ([(1, 1, 0.5, -1.0, False)], "negative tp"),
    ([(N, 1, 0.5, np.nan, False)], "bar == n_bars"),
    ([(-1, 1, 0.5, np.nan, False)], "negative bar"),
    ([(5, 1, 0.5, np.nan, False), (5, 0, np.nan, np.nan, True)], "duplicate bar"),
    ([(6, 1, 0.5, np.nan, False), (5, 0, np.nan, np.nan, True)], "decreasing bars"),
    ([(1, 2, 0.5, np.nan, False)], "signal 2"),
    ([(1, 0, np.nan, np.nan, False)], "empty event"),
    ([(1, -1, 0.5, np.nan, False)], "short not allowed"),
])
def test_decode_events_rejects_bad_values(rows, why):
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(npy(ev(rows)), N, allow_short=False)


def test_decode_events_rejects_non_boolean_exit_byte():
    raw = bytearray(npy(ev([(1, 0, np.nan, np.nan, True)])))
    assert raw[-1] == 1
    raw[-1] = 2
    with pytest.raises(S.SandboxDecodeError, match="exit"):
        S.decode_events(bytes(raw), N, allow_short=False)


def test_decode_events_rejects_trailing_truncated_oversize_and_bad_headers():
    good = npy(GOOD)
    for raw in (good + b"\x00", good[:-1], b"not an npy file", b"",
                b"\x93NUMPY\x03\x00" + good[8:]):
        with pytest.raises(S.SandboxDecodeError):
            S.decode_events(raw, N, allow_short=False)
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(good, 2, allow_short=False)            # k=3 events > n_bars=2 / bar out of range
    big = npy(np.zeros(I.MAX_EVENTS_FILE_BYTES // 26 + 1, dtype=I.EVENTS_DTYPE))
    with pytest.raises(S.SandboxDecodeError, match="larger"):
        S.decode_events(big, 10**9, allow_short=False)
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events("text", N, allow_short=False)  # type: ignore[arg-type]


def test_decode_events_rejects_fortran_order_and_huge_header():
    buf = io.BytesIO()
    np.lib.format.write_array_header_1_0(buf, {"descr": np.lib.format.dtype_to_descr(I.EVENTS_DTYPE),
                                               "fortran_order": True, "shape": (len(GOOD),)})
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(buf.getvalue() + GOOD.tobytes(), N, allow_short=False)
    hdr = ("{'descr': " + repr(np.lib.format.dtype_to_descr(I.EVENTS_DTYPE)) + ", 'fortran_order': False, "
           "'shape': (0,), 'pad': '" + "x" * 20000 + "'}").encode("latin1")
    hdr += b" " * ((-(len(hdr) + 13)) % 64) + b"\n"
    raw = b"\x93NUMPY\x02\x00" + len(hdr).to_bytes(4, "little") + hdr
    with pytest.raises(S.SandboxDecodeError):
        S.decode_events(raw, N, allow_short=False)


# ── decode_status ───────────────────────────────────────────────────────────
def status(**kw) -> dict:
    d = {"schema": I.STATUS_SCHEMA, "ok": True, "runs": {"full1": {"ok": True, "n_events": 3, "seconds": 0.5}},
         "errors": [], "timings": {"full1": 0.5, "_total": 1.0},
         "versions": {"python": "3.12.7", "numpy": "2.1.3", "pandas": "2.2.3", "pyarrow": "18.1.0"},
         "sensitive_env_names": []}
    d.update(kw)
    return d


def jb(d) -> bytes:
    return json.dumps(d).encode()


def test_decode_status_accepts_and_normalises():
    d = S.decode_status(jb(status(errors=[{"run_id": "cut20", "kind": "weird", "message": "café\x00" + "x" * 5000},
                                          {"run_id": None, "kind": "internal", "message": "job"}],
                                  timings={"full1": 0.5, "BAD KEY": 1, "cut20": -1, "_total": 2.0},
                                  versions={"python": "3.12☃", "numpy": 2})))
    e0 = d["errors"][0]
    assert e0["kind"] == "internal"                         # unknown kind -> internal
    assert e0["message"].startswith("caf??") and len(e0["message"]) == I.MAX_ERROR_MESSAGE_CHARS
    assert d["errors"][1]["run_id"] is None
    assert d["timings"] == {"full1": 0.5, "_total": 2.0}
    assert d["versions"] == {"python": "3.12?"}
    assert d["runs"]["full1"] == {"ok": True, "n_events": 3, "seconds": 0.5}


@pytest.mark.parametrize("raw", [
    b'{"schema": "sajim.lab.sandbox_status/1", "ok": true, "timings": {"full1": NaN}}',
    b'{"schema": "sajim.lab.sandbox_status/1", "ok": true, "timings": {"full1": Infinity}}',
    b"\xff\xfe not utf8",
    b"[1, 2]",
    b"not json",
    jb(status(schema="sajim.lab.sandbox_status/2")),
    jb(status(errors=[{"run_id": "x", "kind": "exception", "message": "m"}] * (I.MAX_ERRORS + 1))),
    jb(status(errors=["not an object"])),
    jb(status(errors=[{"run_id": "BAD ID", "kind": "exception", "message": "m"}])),
    jb(status(sensitive_env_names=["sajim_lower"])),
    jb(status(sensitive_env_names=["SAJIM_X=secret-value"])),
    jb(status(sensitive_env_names=[f"SAJIM_{i}" for i in range(65)])),
    jb(status(sensitive_env_names="SAJIM_X")),
    b" " * (I.MAX_STATUS_BYTES + 1),
], ids=lambda raw: f"case{len(raw)}")
def test_decode_status_rejects(raw):
    with pytest.raises(S.SandboxDecodeError) as exc:
        S.decode_status(raw)
    assert exc.value.kind == "status_invalid"


def test_decode_status_untrusted_ids_and_env_names_must_match_in_full():
    # `$` also matches before a trailing newline, so re.match accepted "full1\n" / "SAJIM_X\n".
    d = S.decode_status(jb(status(timings={"full1\n": 1.0, "_total": 1.0},
                                  runs={"full1\n": {"ok": True, "n_events": 1, "seconds": 1.0}})))
    assert d["timings"] == {"_total": 1.0} and d["runs"] == {}
    for bad in (status(errors=[{"run_id": "full1\n", "kind": "exception", "message": "m"}]),
                status(sensitive_env_names=["SAJIM_X\n"])):
        with pytest.raises(S.SandboxDecodeError):
            S.decode_status(jb(bad))


def test_plan_max_events_fits_in_one_events_file():
    # 26 B x MAX_FRAME_BARS > MAX_EVENTS_FILE_BYTES: with max_events_per_run = n_bars a dense
    # (e.g. exit-on-most-bars) run on a big frame passed the runner and then failed as an opaque
    # trusted-side `decode` error instead of the runner's clear `too_many_events`.
    job = I.SandboxJob(frame_parquet=b"x", n_bars=I.MAX_FRAME_BARS, strategy_py=b"", indicators_py=b"",
                       runs=(I.SandboxRun("full1", {}),), allow_short=False)
    cap = json.loads(S.plan_json(job))["max_events_per_run"]
    assert cap < I.MAX_FRAME_BARS and cap * I.EVENTS_DTYPE.itemsize > I.MAX_EVENTS_FILE_BYTES - 8192
    arr = np.zeros(cap, dtype=I.EVENTS_DTYPE)
    arr["bar"] = np.arange(cap)
    arr["exit"] = True
    arr["stop_dist"] = arr["tp_dist"] = np.nan
    raw = npy(arr)                                             # what the runner writes for `cap` events
    assert len(raw) <= I.MAX_EVENTS_FILE_BYTES
    assert len(S.decode_events(raw, I.MAX_FRAME_BARS, allow_short=False)) == cap


# ── decode_result ───────────────────────────────────────────────────────────
RUNS = (I.SandboxRun("cut50", {"a": 1}, cut=50), I.SandboxRun("full1", {"a": 1}))


def _job():
    return make_job(b"", RUNS, synthetic_frame(N))


def _res(files, *, exit_code=0, timed_out=False):
    return I.SandboxResult(files=files, exit_code=exit_code, timed_out=timed_out, wall_s=1.0)


def test_decode_result_happy_path():
    files = {"status.json": jb(status()), "cut50.events.npy": npy(GOOD), "full1.events.npy": npy(GOOD)}
    d = S.decode_result(_job(), _res(files))
    assert d.job_ok and d.ok("cut50") and d.ok("full1") and d.errors == ()
    assert d.versions["numpy"] == "2.1.3" and d.timings["_total"] == 1.0
    assert d.sensitive_env_names == ()


def test_decode_result_cut_runs_are_bounded_by_their_own_length():
    late = ev([(60, 1, 0.5, np.nan, False)])                 # valid for full (100) but not for cut50
    files = {"status.json": jb(status()), "cut50.events.npy": npy(late), "full1.events.npy": npy(late)}
    d = S.decode_result(_job(), _res(files))
    assert not d.ok("cut50") and d.ok("full1")
    assert [(e.run_id, e.kind) for e in d.errors] == [("cut50", "decode")]


def test_decode_result_reported_errors_win_over_files_and_missing_files_are_flagged():
    st = status(errors=[{"run_id": "cut50", "kind": "exception", "message": "ZeroDivisionError: x (strategy.py line 3)"}])
    files = {"status.json": jb(st), "cut50.events.npy": npy(GOOD)}
    d = S.decode_result(_job(), _res(files))
    assert "cut50" not in d.events
    kinds = {(e.run_id, e.kind) for e in d.errors}
    assert kinds == {("cut50", "exception"), ("full1", "missing_output")}
    assert d.job_ok


def test_decode_result_job_level_failures():
    d = S.decode_result(_job(), _res({}, exit_code=None, timed_out=True))
    assert {e.kind for e in d.errors if e.run_id is None} == {"timeout", "status_invalid"}
    assert not d.job_ok
    d = S.decode_result(_job(), _res({}, exit_code=1))
    assert [e.kind for e in d.errors if e.run_id is None] == ["sandbox"]
    d = S.decode_result(_job(), _res({"status.json": b"garbage", "full1.events.npy": npy(GOOD)}))
    assert [e.kind for e in d.errors if e.run_id is None] == ["status_invalid"]
    assert d.ok("full1")                                      # per-run decoding still happens; job_ok is False
    assert not d.job_ok
    st = status(errors=[{"run_id": "ghost", "kind": "exception", "message": "m"}])
    d = S.decode_result(_job(), _res({"status.json": jb(st), "cut50.events.npy": npy(GOOD),
                                      "full1.events.npy": npy(GOOD)}))
    assert [(e.run_id, e.kind) for e in d.errors] == [(None, "internal")]


def test_decode_result_bad_events_become_decode_errors_and_never_raise():
    obj = np.array([_Payload()], dtype=object)
    files = {"status.json": jb(status()), "cut50.events.npy": npy(obj, allow_pickle=True),
             "full1.events.npy": b"\x93NUMPY garbage"}
    d = S.decode_result(_job(), _res(files))
    assert [(e.run_id, e.kind) for e in d.errors] == [("cut50", "decode"), ("full1", "decode")]
    assert d.events == {} and PICKLE_HITS == []


# ── events_equal ────────────────────────────────────────────────────────────
def test_events_equal_nan_aware_and_atol():
    a = S.decode_events(npy(GOOD), N, allow_short=False)
    assert S.events_equal(a, a.copy()) == (True, None)
    b = a.copy()
    b["stop_dist"][0] += 1e-12
    assert S.events_equal(a, b) == (True, None)
    b["stop_dist"][2] += 1e-6
    assert S.events_equal(a, b) == (False, 9)
    c = a.copy()
    c["tp_dist"][0] = 2.0                                      # NaN vs number
    assert S.events_equal(a, c) == (False, 3)
    d = a.copy()
    d["exit"][1] = False
    d["signal"][1] = 1
    d["stop_dist"][1] = 1.0
    assert S.events_equal(a, d) == (False, 7)


def test_events_equal_different_lengths_reports_first_differing_bar():
    a = ev([(3, 1, 0.5, np.nan, False), (9, 1, 0.5, np.nan, False)])
    b = ev([(3, 1, 0.5, np.nan, False), (5, 1, 0.5, np.nan, False), (9, 1, 0.5, np.nan, False)])
    assert S.events_equal(a, b) == (False, 5)
    assert S.events_equal(a[:1], a) == (False, 9)
    e = np.zeros(0, I.EVENTS_DTYPE)
    assert S.events_equal(e, e) == (True, None)
    assert S.events_equal(e, a) == (False, 3)
    c = b.copy()
    c["signal"][0] = -1                                        # common bar 3 differs before only-in-b bar 5
    assert S.events_equal(a, c) == (False, 3)
