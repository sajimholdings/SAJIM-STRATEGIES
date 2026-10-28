"""lab.cli: retried HTTP client, config, stamping, gate table, and every subcommand (fakes only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sajim_quant.lab import cli, gates
from sajim_quant.lab import interfaces as I

pytest.importorskip("sajim_quant.lab.contract")   # builder A's lint; cli imports it lazily

from _laptop_testkit import (  # noqa: E402
    LOOKAHEAD_SRC, FakeClient, make_lab_folder, make_scorecard, review, sha_of,
)

LAB = "lab_test_ema_xauusd"


# ── LabClient transport behaviour ───────────────────────────────────────────
class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        status, payload = r
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def client(transport, **kw):
    sleeps = []
    c = cli.LabClient("https://lab.example/", transport=transport, sleep=sleeps.append, **kw)
    return c, sleeps


def test_judge_header_wins_and_only_one_token_is_sent():
    t = Transport([(200, {"ok": True})])
    c, _ = client(t, lab_token="LABSECRET", judge_token="JUDGESECRET")
    c.finalize({"x": 1})
    h = t.calls[0]["headers"]
    assert h[I.HEADER_JUDGE_TOKEN] == "JUDGESECRET" and I.HEADER_LAB_TOKEN not in h and I.HEADER_API_TOKEN not in h
    t2 = Transport([(200, {"ok": True})])
    c2, _ = client(t2, lab_token="LABSECRET")
    c2.check({})
    h2 = t2.calls[0]["headers"]
    assert h2[I.HEADER_LAB_TOKEN] == "LABSECRET" and I.HEADER_JUDGE_TOKEN not in h2
    assert t2.calls[0]["url"] == "https://lab.example/lab/check"
    assert "SECRET" not in repr(c) + repr(c2)


def test_retries_on_503_and_network_but_never_on_429():
    t = Transport([(503, {"detail": "busy"}), OSError("reset"), (200, {"job_id": "fc-1"})])
    c, sleeps = client(t, lab_token="t")
    assert c.evaluate({})["job_id"] == "fc-1"
    assert sleeps == [1, 2] and len(t.calls) == 3

    t = Transport([(429, {"detail": {"code": "budget_exhausted", "message": "3/3 used", "reasons": ["r1"]}})])
    c, sleeps = client(t, lab_token="t")
    with pytest.raises(cli.LabApiError) as ei:
        c.evaluate({})
    assert ei.value.status == 429 and ei.value.code == "budget_exhausted" and ei.value.reasons == ["r1"]
    assert len(t.calls) == 1 and sleeps == []


def test_retry_exhaustion_is_a_network_error_with_capped_backoff():
    t = Transport([OSError("down")] * 7)
    c, sleeps = client(t, lab_token="t", retries=6)
    with pytest.raises(cli.LabApiError) as ei:
        c.job("fc-1")
    assert ei.value.code == "network" and cli.api_exit_code(ei.value) == cli.EXIT_NETWORK
    assert sleeps == [1, 2, 4, 8, 16, 30]


def test_wait_tolerates_blips_and_times_out():
    t = Transport([OSError("x"), (200, {"status": "running"}), (200, {"status": "done", "result": {}})])
    c, sleeps = client(t, lab_token="t", retries=0)
    seen = []
    assert c.wait("fc-1", on_change=seen.append)["status"] == "done"
    assert seen == ["unreachable", "running", "done"]

    clock = iter([0.0, 10.0, 2000.0])
    t = Transport([(200, {"status": "running"})] * 3)
    c = cli.LabClient("https://x", lab_token="t", transport=t, sleep=lambda s: None, clock=lambda: next(clock))
    with pytest.raises(cli.LabApiError) as ei:
        c.wait("fc-1", timeout=900)
    assert ei.value.code == "timeout"


def test_load_config_never_uses_the_dashboard_token(tmp_path):
    env = {"SAJIM_API_TOKEN": "dash", "SAJIM_MODAL_API_URL": "https://m", "USERPROFILE": str(tmp_path)}
    with pytest.raises(cli.LabApiError) as ei:
        cli.load_config(env)
    assert ei.value.code == "not_configured"
    (tmp_path / ".sajim").mkdir()
    (tmp_path / ".sajim" / "lab_token").write_text("filetoken\n", encoding="utf-8")
    (tmp_path / ".sajim" / "judge_token").write_text("judge\n", encoding="utf-8")
    assert cli.load_config(env) == ("https://m", "filetoken")
    env["SAJIM_LAB_TOKEN"] = "envtoken"
    env["SAJIM_API_URL"] = "https://lab/"
    assert cli.load_config(env) == ("https://lab", "envtoken")


@pytest.mark.parametrize("url,ok", [
    ("https://sajim--sajim-api.modal.run", True),
    ("http://127.0.0.1:8000", True),
    ("http://localhost:8000/", True),
    ("http://sajim--sajim-api.modal.run", False),            # a token would travel in clear text
    ("http://127.0.0.1.evil.example", False),
    ("http://localhost.evil.example:80", False),
    ("ftp://lab", False),
    ("https://user:pw@lab", False),
    ("lab.example", False),
    ("https://lab:99999", False),
])
def test_load_config_requires_https_except_loopback(url, ok):
    env = {"SAJIM_API_URL": url, "SAJIM_LAB_TOKEN": "tok", "USERPROFILE": "."}
    if ok:
        assert cli.load_config(env) == (url.rstrip("/"), "tok")
        return
    with pytest.raises(cli.LabApiError) as ei:
        cli.load_config(env)
    assert ei.value.code == "not_configured" and "tok" not in str(ei.value) and "pw" not in str(ei.value)


def test_runtime_hint_only_for_runs_stopped_for_time():
    assert cli.runtime_hint(["sandbox: acut2 timeout: generate_signals did not finish"]).startswith("RUNTIME:")
    assert cli.runtime_hint(["sandbox: nb00, nb01 budget: not started"]) is not None
    assert cli.runtime_hint(["G7 plateau not computable: 2 of 6 neighbour runs were stopped for time"])
    assert cli.runtime_hint(["DEV trial budget used (3/3) with a hard gate still failing",
                             "G3 DEV after base costs: pf 1.08 >= 1.2 failed"]) is None
    assert cli.runtime_hint([]) is None
    hint = cli.runtime_hint(["sandbox: full1 timeout: x"])
    assert f"{I.PER_RUN_TIMEOUT_S:g} s" in hint and not gates.contains_forbidden_label(hint)


# ── Files ───────────────────────────────────────────────────────────────────
def test_stamp_manifest_and_build_body(tmp_path):
    folder = make_lab_folder(tmp_path)
    keys_before = list(json.loads((folder / "manifest.json").read_text()))
    m = cli.stamp_manifest(folder)
    assert m["code_sha256"] == sha_of(folder) and m["spec_sha256"] == sha_of(folder, "spec.md")
    assert list(json.loads((folder / "manifest.json").read_text())) == keys_before
    body = cli.build_body(folder, stage="dev", kind="dev")
    assert body["request_id"] == I.derive_request_id(LAB, sha_of(folder), "dev")
    assert body["strategy_py"].encode("utf-8") == (folder / "strategy.py").read_bytes()
    assert body["stage"] == "dev" and body["manifest"]["code_sha256"] == sha_of(folder)
    chk = cli.build_body(folder, stage=None, kind="check")
    assert I.REQUEST_ID_RE.match(chk["request_id"]) and chk["request_id"] != body["request_id"]
    hold = cli.build_body(folder, stage="holdout", kind="holdout")
    assert hold["request_id"] == I.derive_request_id(LAB, sha_of(folder), "holdout")


# ── Gate table ──────────────────────────────────────────────────────────────
def test_gate_table_format_is_in_sample_and_aggregate_only():
    sha = "1a2b3c4d5e6f" + "0" * 52
    sc = make_scorecard(LAB, sha, fail="G3", trial=2, detail=True)
    text = cli.render_gate_table(sc)
    lines = text.splitlines()
    assert lines[0] == f"SAJIM LAB  {LAB}  sha 1a2b3c4d5e6f  DEV trial 2/3  [IN-SAMPLE]"
    assert lines[1] == "family holdout touches 0/3 | global lab trials 7"
    assert lines[2] == "trades 212 | net +1234.50 | PF 1.31 | max DD 7.9% | daily Sharpe 1.12   (IN-SAMPLE)"
    assert lines[3] == "GATE  NAME                      RESULT  DECISIVE"
    rows = [ln for ln in lines if ln[:3].rstrip() in {g.id for g in gates.GATES} or ln[:4].rstrip() in {g.id for g in gates.GATES}]
    assert [r.split()[0] for r in rows] == [g.id for g in gates.GATES]
    assert "G0    Integrity                 PASS    integrity_checks_passed 1 == 1" in lines
    assert "G3    DEV after base costs      FAIL    pf 1.08 >= 1.2" in lines
    assert "G4b   PSR / DSR                 --      phase 2" in lines
    assert "G8    Sealed holdout            --      runner only (judge token)" in lines
    assert "VERDICT (provisional, IN-SAMPLE): REJECT - revisions left: 1 (spec-fix or bug-fix only)" in lines
    assert "999" not in text and "nb00" not in text            # never per-neighbour detail
    final = cli.render_gate_table(make_scorecard(LAB, sha, fail="G5", final=True, trial=3))
    assert "VERDICT (final, IN-SAMPLE): REJECT" in final


# ── Commands (main with fakes) ──────────────────────────────────────────────
def run_cli(tmp_path, argv, script=None, env=None, calls=None):
    out = []
    calls = calls if calls is not None else []
    environ = {"SAJIM_LAB_ID": LAB} if env is None else env
    rc = cli.main(argv, environ=environ, root=tmp_path, out=out.append,
                  client_factory=lambda **kw: FakeClient(lab_token="t", script=script or {}, calls=calls, **kw))
    return rc, "\n".join(out), calls


def test_missing_lab_id_is_exit_2(tmp_path):
    rc, out, _ = run_cli(tmp_path, ["check"], env={})
    assert rc == 2 and "SAJIM_LAB_ID" in out


def test_check_pass_and_fail(tmp_path):
    folder = make_lab_folder(tmp_path)
    sha = sha_of(folder)
    ok = {"check": {"job_id": "fc-c1"}, "wait": {"status": "done", "result": {
        "success": True, "kind": "lab_check", "code_sha256": sha, "passed": True, "reasons": [], "checks": {}}}}
    rc, out, calls = run_cli(tmp_path, ["check"], ok)
    assert rc == 0 and f"CHECK PASS sha {sha[:12]}" in out
    body = calls[0][2][0]
    assert body["manifest"]["code_sha256"] == sha and body["stage"] == "dev"
    assert cli.load_build_log(folder)["pending"] is None
    bad = {"check": {"job_id": "fc-c2"}, "wait": {"status": "done", "result": {
        "success": True, "code_sha256": sha, "passed": False,
        "reasons": ["causality: outputs before the 40% cut changed \x1b[31m"]}}}
    rc, out, _ = run_cli(tmp_path, ["check"], bad)
    assert rc == 1 and "CHECK FAIL" in out and "- causality: outputs before the 40% cut changed ?[31m" in out


def test_local_lint_failure_is_exit_2_and_nothing_is_sent(tmp_path):
    make_lab_folder(tmp_path, src=LOOKAHEAD_SRC)
    rc, out, calls = run_cli(tmp_path, ["dev"], {})
    assert rc == 2 and "LOCAL LINT FAIL" in out and calls == []


def test_dev_saves_builder_scorecard_and_prints_table(tmp_path):
    folder = make_lab_folder(tmp_path)
    sha = sha_of(folder)
    sc = make_scorecard(LAB, sha, detail=True)
    script = {"evaluate": {"job_id": "fc-d1", "trial": 1}, "wait": {"status": "done", "result": {
        "success": True, "kind": "lab_dev", "code_sha256": sha, "stage": "dev", "scorecard": sc}}}
    rc, out, calls = run_cli(tmp_path, ["dev"], script)
    assert rc == 0 and "[IN-SAMPLE]" in out and "DEV gates passed" in out
    saved = json.loads((folder / "scorecards" / f"dev_{sha[:12]}.json").read_text())
    assert "detail" not in saved and saved["code_sha256"] == sha
    assert calls[0][2][0]["request_id"] == I.derive_request_id(LAB, sha, "dev")
    log = cli.load_build_log(folder)
    assert log["pending"] is None and log["events"][-1]["event"] == "dev"
    script["wait"]["result"]["scorecard"] = make_scorecard(LAB, sha, fail="G3")
    rc, out, _ = run_cli(tmp_path, ["dev"], script)
    assert rc == 1 and "FAIL" in out


def test_dev_budget_refusal_is_exit_4(tmp_path):
    make_lab_folder(tmp_path)
    err = cli.LabApiError("HTTP 429 budget_exhausted", status=429, code="budget_exhausted",
                          detail={"code": "budget_exhausted", "message": "", "reasons": ["3/3 trials used"]})
    rc, out, _ = run_cli(tmp_path, ["dev"], {"evaluate": err})
    assert rc == 4 and "REFUSED" in out and "3/3 trials used" in out


def test_dev_poll_timeout_keeps_pending_and_status_resumes(tmp_path):
    folder = make_lab_folder(tmp_path)
    sha = sha_of(folder)
    script = {"evaluate": {"job_id": "fc-d9", "trial": 2},
              "wait": cli.LabApiError("still running", code="timeout")}
    rc, out, _ = run_cli(tmp_path, ["dev"], script)
    assert rc == 3 and "status" in out
    pending = cli.load_build_log(folder)["pending"]
    assert pending["kind"] == "lab_dev" and pending["job_id"] == "fc-d9" and pending["code_sha256"] == sha
    script["wait"] = {"status": "done", "result": {"success": True, "kind": "lab_dev", "code_sha256": sha,
                                                   "stage": "dev", "scorecard": make_scorecard(LAB, sha, trial=2)}}
    rc, out, calls = run_cli(tmp_path, ["status"], script)
    assert rc == 0 and ("builder", "wait", ("fc-d9",)) in calls
    assert cli.load_build_log(folder)["pending"] is None
    rc, out, _ = run_cli(tmp_path, ["status"], script)
    assert rc == 0 and "no pending" in out


def test_dev_infra_failure_is_exit_5(tmp_path):
    make_lab_folder(tmp_path)
    script = {"evaluate": {"job_id": "fc-x"}, "wait": {"status": "failed", "error": "sandbox died"}}
    rc, out, _ = run_cli(tmp_path, ["dev"], script)
    assert rc == 5 and "infrastructure" in out


def test_summary_newest_scorecard_and_offline_counts(tmp_path):
    folder = make_lab_folder(tmp_path)
    rc, out, _ = run_cli(tmp_path, ["summary"])
    assert rc == 2
    a, b = "a" * 64, "b" * 64
    cli.save_scorecard(folder, make_scorecard(LAB, a, created_at="2026-10-12T10:00:00+00:00"))
    cli.save_scorecard(folder, make_scorecard(LAB, b, fail="G6", created_at="2026-10-12T11:00:00+00:00"))
    fam = {"n_trials": 2, "max_trials": 3, "holdout_touches": 0, "max_holdout_touches": 3, "n_trials_global": 9,
           "records": [{"payload": {"secret": "x"}}]}
    rc, out, _ = run_cli(tmp_path, ["summary"], {"family": fam})
    assert rc == 0 and f"sha {b[:12]}" in out and "server counts: DEV trials 2/3" in out and "records" not in out
    rc, out, _ = run_cli(tmp_path, ["summary", "--sha", a[:12]],
                         {"family": cli.LabApiError("down", code="network")})
    assert rc == 0 and f"sha {a[:12]}" in out and "offline" in out


def test_done_relints_and_checks_sha(tmp_path):
    folder = make_lab_folder(tmp_path)
    cli.stamp_manifest(folder)
    sha = sha_of(folder)
    rc, out, _ = run_cli(tmp_path, ["done", "--final", "0" * 12])
    assert rc == 2 and "not" in out
    rc, out, _ = run_cli(tmp_path, ["done", "--final", sha[:12]])
    assert rc == 0 and cli.load_build_log(folder)["final_sha"] == sha
    (folder / "strategy.py").write_text(LOOKAHEAD_SRC, encoding="utf-8")       # dirty after the fact
    rc, out, _ = run_cli(tmp_path, ["done", "--final", sha_of(folder)])
    assert rc == 2 and "REFUSED" in out


def test_review_validation(tmp_path):
    folder = make_lab_folder(tmp_path)
    sha = sha_of(folder)
    (folder / "review.json").write_text(json.dumps(review(LAB, sha)), encoding="utf-8")
    rc, out, _ = run_cli(tmp_path, ["review", "--file", "review.json"])
    assert rc == 0 and "REVIEW OK" in out
    rc, out, _ = run_cli(tmp_path, ["review", "--file", "../review.json"])
    assert rc == 2
    for bad in (review(LAB, sha, decision="DOWNGRADE"),                       # no `to`
                review(LAB, sha, decision="DOWNGRADE", to="CANDIDATE"),       # cannot upgrade
                review(LAB, sha, reason="an ELITE setup"),                    # forbidden label
                review(LAB, sha, next_tests=["a", "b", "c", "d"]),
                review(LAB, "f" * 64),                                        # wrong sha
                dict(review(LAB, sha), extra=1)):
        (folder / "review.json").write_text(json.dumps(bad), encoding="utf-8")
        rc, out, _ = run_cli(tmp_path, ["review", "--file", "review.json"])
        assert rc == 2 and "REVIEW INVALID" in out, bad
    assert cli.validate_review(review(LAB, sha, decision="DOWNGRADE", to="REJECT"), lab_id=LAB, code_sha256=sha) == []


# ── Adversarial review (D-laptop) ───────────────────────────────────────────
def _write_request(folder: Path, **over) -> None:
    req = {"schema": I.REQUEST_SCHEMA, "lab_id": LAB, "family": LAB, "description": "Buy gold above the EMA",
           "symbol": "XAUUSD", "timeframe": "M15", "created_at": "2026-10-13T09:00:00+00:00",
           "revision_notes": [], "approved_spec_sha256": None, "runner_version": "1"}
    req.update(over)
    (folder / "request.json").write_text(json.dumps(req), encoding="utf-8")


@pytest.mark.parametrize("over", [{"family": "lab_fresh_family_xauusd"}, {"symbol": "XAGUSD",
                                  "supported_symbols": ["XAGUSD"], "breadth_symbols": ["XAUUSD"]},
                                  {"timeframe": "H1"}])
def test_manifest_cannot_leave_the_requested_family_or_universe(tmp_path, over):
    """A builder-written manifest.family would claim DEV trials in a fresh family (unlimited trials);
    a changed symbol/timeframe is judged on a market the user never asked for."""
    from _laptop_testkit import manifest
    folder = make_lab_folder(tmp_path, man=manifest(LAB, **over))
    _write_request(folder)
    rc, out, calls = run_cli(tmp_path, ["dev"], {"evaluate": {"job_id": "fc-1"}})
    assert rc == 2 and "LOCAL LINT FAIL" in out and calls == []
    assert any("request.json" in p for p in cli.local_problems(folder, LAB))


def test_manifest_matching_request_lints_clean(tmp_path):
    folder = make_lab_folder(tmp_path)
    _write_request(folder)
    assert cli.local_problems(folder, LAB) == []


def test_check_does_not_overwrite_a_pending_dev_job(tmp_path):
    """Overwriting build_log.pending loses the DEV job's scorecard (`status` can no longer fetch it)."""
    folder = make_lab_folder(tmp_path)
    rc, _, _ = run_cli(tmp_path, ["dev"], {"evaluate": {"job_id": "fc-d1", "trial": 1},
                                           "wait": cli.LabApiError("still running", code="timeout")})
    assert rc == 3 and cli.load_build_log(folder)["pending"]["job_id"] == "fc-d1"
    rc, out, calls = run_cli(tmp_path, ["check"], {"check": {"job_id": "fc-c1"},
                                                   "wait": {"status": "done", "result": {"success": True}}})
    assert rc == 2 and "status" in out and calls == []
    assert cli.load_build_log(folder)["pending"]["job_id"] == "fc-d1"


def test_urllib_transport_never_follows_redirects_with_the_token():
    """A 3xx from the lab URL must not re-send X-Judge-Token / X-Lab-Token to another host."""
    import http.server
    import threading

    seen = []

    class Sink(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    redir = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (sink, redir)]
    for t in threads:
        t.start()
    try:
        c = cli.LabClient(f"http://127.0.0.1:{redir.server_port}", judge_token="JUDGESECRET", retries=0, timeout=5)
        with pytest.raises(cli.LabApiError) as ei:
            c.family(LAB)
        assert ei.value.status == 302
        assert seen == []
    finally:
        for s in (sink, redir):
            s.shutdown()
            s.server_close()
