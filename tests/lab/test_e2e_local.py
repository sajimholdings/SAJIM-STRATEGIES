"""End-to-end LOCAL dry run of the Strategy Lab (P1 integration seams).

The chain under test, with every trusted piece real:

    lab.cli main (real) -> LabClient (real; in-process transport instead of HTTP)
      -> LocalLabApi (TEST FAKE: a minimal reference of the /lab routes, INTERFACES.md §9.2)
      -> evaluate.prepare_frame / check / evaluate_dev / evaluate_holdout (real)
      -> LocalSubprocessExecutor + sandbox_runner (real; NOT a security boundary)
      -> sandbox_io strict decoder, causality, SignalSimulator, robustness, scorecard (real)
      -> ledger budget slots + immutable records on LocalDirStore (real)

LocalLabApi is scaffolding only: it shows the order of operations the integrator's Modal
routes must follow (validation before any claim, claim before any job, builder_view for the
builder, holdout judge-only). It is not the production API.

Synthetic OHLC only; never loads data/**. Nothing is written inside the repo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

for _mod in ("contract", "simulator", "sandbox_io", "sandbox_local", "sandbox_runner", "causality",
             "robustness", "scorecard", "ledger", "evaluate", "cli", "run"):
    pytest.importorskip(f"sajim_quant.lab.{_mod}")

from sajim_quant.core.fx import quote_rate_series  # noqa: E402
from sajim_quant.core.instruments import get_spec  # noqa: E402
from sajim_quant.lab import cli, contract, evaluate, gates, ledger, run, scorecard, splits  # noqa: E402
from sajim_quant.lab import interfaces as I  # noqa: E402
from sajim_quant.lab.causality import CHECK_RUN_IDS  # noqa: E402
from sajim_quant.lab.sandbox_local import LocalSubprocessExecutor  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from _judging_testkit import (  # noqa: E402
    PLANTED_SRC, InProcessExecutor, LocalTimeoutExecutor, dev_raw, holdout_raw,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / ".claude" / "skills" / "lab-build" / "examples"
TEMPLATE_MANIFEST = REPO / ".claude" / "skills" / "lab-build" / "templates" / "manifest.json"

LAB_TOKEN = "e2e-lab-token-not-a-secret"
JUDGE_TOKEN = "e2e-judge-token-not-a-secret"
API_URL = "http://lab.invalid"
PLANTED_DATA = dict(drift=0.6, marker_every=75)
SPEC_MD = ("# Spec\n\n## 1. Idea\nSynthetic e2e strategy.\n\n## 8. Trials\n").encode("utf-8")


# ── Test fake: a minimal /lab API over the real trusted modules ─────────────
class Unauthorized(I.LabError):
    status, code = I.HTTP_UNAUTHORIZED, "unauthorized"


class NotFound(I.LabError):
    status, code = I.HTTP_NOT_FOUND, "not_found"


class _NoLoader:
    """quote_rate_series must not load data for a USD-quoted symbol."""

    def load(self, *a, **kw):
        raise AssertionError("the e2e test never loads data/**")


class CountingExecutor:
    """Wraps a SandboxExecutor and records each job's label and run ids."""

    def __init__(self, inner: I.SandboxExecutor):
        self.inner = inner
        self.jobs: List[Tuple[str, Tuple[str, ...]]] = []

    def run(self, job: I.SandboxJob) -> I.SandboxResult:
        self.jobs.append((job.label, tuple(r.run_id for r in job.runs)))
        return self.inner.run(job)


class LocalLabApi:
    """Synchronous in-process stand-in for the /lab routes (jobs finish inside the POST)."""

    def __init__(self, store: ledger.LocalDirStore, raw: Mapping[str, Any], executor: I.SandboxExecutor):
        self.store = store
        self.raw = dict(raw)
        self.executor = CountingExecutor(executor)
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.by_request: Dict[str, str] = {}

    # transport(method, url, headers, body, timeout) -> (status, bytes), as LabClient expects
    def transport(self, method: str, url: str, headers: Mapping[str, str], body: Optional[bytes],
                  timeout: float) -> Tuple[int, bytes]:
        try:
            out = self._route(method, url, headers, body)
            return 200, json.dumps(out, allow_nan=False).encode("utf-8")
        except I.LabError as e:
            return e.status, json.dumps({"detail": e.detail()}).encode("utf-8")

    # ── routing ──
    @staticmethod
    def _scope(headers: Mapping[str, str]) -> str:
        def ok(name: str, secret: str) -> bool:
            got = headers.get(name)
            return got is not None and hmac.compare_digest(got.encode(), secret.encode())
        if ok(I.HEADER_JUDGE_TOKEN, JUDGE_TOKEN):
            return "judge"
        if ok(I.HEADER_LAB_TOKEN, LAB_TOKEN):
            return "builder"
        raise Unauthorized("no valid lab token")

    def _route(self, method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]) -> Any:
        scope = self._scope(headers)
        parts = urlsplit(url)
        path = parts.path.strip("/").split("/")
        if method == "POST" and path == ["lab", "check"]:
            return self._post(scope, body, kind="check")
        if method == "POST" and path == ["lab", "evaluate"]:
            return self._post(scope, body, kind="evaluate")
        if method == "GET" and len(path) == 3 and path[:2] == ["lab", "job"]:
            return self._job(scope, path[2])
        if method == "GET" and len(path) == 4 and path[0] == "lab" and path[2] == "scorecard":
            stage = parse_qs(parts.query).get("stage", ["dev"])[0]
            return self._scorecard(scope, path[1], path[3], stage)
        raise NotFound(f"no route {method} {parts.path}")

    # ── POST /lab/check and /lab/evaluate (§9.2 validation order) ──
    def _submission(self, body: Optional[bytes]) -> Tuple[I.Submission, Dict[str, Any]]:
        if body is None or len(body) > I.LAB_MAX_BODY_BYTES:
            raise I.PayloadTooLarge("body too large")
        b = json.loads(body.decode("utf-8"))
        src = str(b.get("strategy_py", "")).encode("utf-8")
        if len(src) > gates.MAX_SOURCE_BYTES:
            raise I.PayloadTooLarge("strategy.py too large")
        if not (contract._LAB_ID_RE.match(str(b.get("lab_id", "")))
                and contract._LAB_ID_RE.match(str(b.get("family", "")))
                and b.get("stage") in splits.SCORED_STAGES
                and I.REQUEST_ID_RE.match(str(b.get("request_id", "")))):
            raise I.LabError("malformed submission")
        sub = I.Submission(lab_id=b["lab_id"], family=b["family"], strategy_py=src,
                           spec_md=str(b.get("spec_md", "")).encode("utf-8"), manifest=dict(b.get("manifest") or {}))
        m = sub.manifest
        if m.get("code_sha256") != sub.code_sha256 or m.get("spec_sha256") != sub.spec_sha256:
            raise I.ContractRefused("sha mismatch", code="sha_mismatch")
        problems = [i.format("strategy.py") for i in contract.lint_source(sub.strategy_py)]
        problems += contract.validate_manifest(m, strategy_py=sub.strategy_py, spec_md=sub.spec_md)
        if problems:
            raise I.ContractRefused("contract refused", reasons=problems)
        return sub, b

    def _post(self, scope: str, body: Optional[bytes], *, kind: str) -> Dict[str, Any]:
        sub, b = self._submission(body)
        stage, rid = b["stage"], b["request_id"]
        if kind == "check":
            job_kind = I.CHECK_RESULT_KIND
        elif stage == splits.DEV:
            job_kind = I.DEV_RESULT_KIND
        else:
            if scope != "judge":
                raise I.WrongScope("the holdout needs the judge token")
            job_kind = I.HOLDOUT_RESULT_KIND
        if kind != "check" and rid != I.derive_request_id(sub.family, sub.code_sha256, stage):
            raise I.LabError("request_id is not the derived id for this family/sha/stage")
        if rid in self.by_request:                                   # _dedup: same job, no new claim
            job = self.jobs[self.by_request[rid]]
            return {**job["started"], "deduplicated": True}
        trial = touch = None
        if job_kind == I.DEV_RESULT_KIND:
            trial = ledger.claim_dev_trial(self.store, sub.family, lab_id=sub.lab_id, request_id=rid,
                                           code_sha256=sub.code_sha256)
            if trial is None:
                raise I.BudgetExhausted(f"family {sub.family} has used all its DEV trials")
        elif job_kind == I.HOLDOUT_RESULT_KIND:
            dev_sc = ledger.load_dev_scorecard(self.store, sub.family, sub.code_sha256)
            if dev_sc is None or dev_sc.get("dev_hard_gates_passed") is not True:
                raise I.DevNotPassed("no passing DEV scorecard for this sha")
            touch = ledger.claim_holdout(self.store, sub.family, sub.code_sha256, lab_id=sub.lab_id,
                                         request_id=rid)
        job_id = "fc-" + uuid.uuid4().hex[:20]
        started = {"job_id": job_id, "status": "queued", "kind": job_kind, "request_id": rid,
                   "code_sha256": sub.code_sha256, "deduplicated": False}
        if kind == "evaluate":
            started.update(trial=trial, holdout_touch=touch)
        self.by_request[rid] = job_id
        self.jobs[job_id] = {"started": started, "kind": job_kind, "status": "done",
                             "result": self._run(job_kind, sub, rid, job_id, trial)}
        return started

    # ── the lab_job body (what modal_runner.lab_job must do) ──
    def _frame(self, sub: I.Submission, stage: str):
        sframe = evaluate.prepare_frame(self.raw[stage], stage, int(sub.manifest["warmup_bars"]))
        spec = get_spec(sub.manifest["symbol"])
        rates, fx_desc = quote_rate_series(spec.quote_currency, sub.manifest["timeframe"], sframe.frame.index,
                                           loader=_NoLoader())
        return sframe, rates.to_numpy(), fx_desc

    def _snapshot(self, sub: I.Submission) -> str:
        base = f"lab/{sub.family}/{sub.sha12}"
        self.store.write(f"{base}/strategy.py", sub.strategy_py)
        self.store.write(f"{base}/spec.md", sub.spec_md)
        self.store.write(f"{base}/manifest.json", json.dumps(dict(sub.manifest), indent=2).encode("utf-8"))
        return base

    def _run(self, job_kind: str, sub: I.Submission, rid: str, job_id: str, trial: Optional[int]) -> Dict[str, Any]:
        common = dict(family=sub.family, lab_id=sub.lab_id, request_id=rid, actor="api",
                      code_sha256=sub.code_sha256, spec_sha256=sub.spec_sha256)
        if job_kind == I.CHECK_RESULT_KIND:
            sframe, _, _ = self._frame(sub, splits.DEV)
            rep = evaluate.check(sub, sframe, self.executor)
            ledger.put_record(self.store, ledger.make_record(
                "check", stage=splits.DEV, payload={"passed": rep.passed, "reasons": list(rep.reasons),
                                                    "checks": dict(rep.checks)}, **common))
            return {"success": True, "kind": job_kind, "code_sha256": sub.code_sha256, "passed": rep.passed,
                    "reasons": list(rep.reasons), "checks": dict(rep.checks), "deploy_id": "e2e-local"}

        base = self._snapshot(sub)
        if job_kind == I.DEV_RESULT_KIND:
            sframe, rates, fx_desc = self._frame(sub, splits.DEV)
            ev = evaluate.evaluate_dev(sub, sframe, self.executor, quote_rate=rates, fx_desc=fx_desc,
                                       budget=ledger.budget_info(self.store, sub.family, trial=trial),
                                       deploy_id="e2e-local")
            sc_path = f"{base}/dev_scorecard.json"
        else:
            sframe, rates, fx_desc = self._frame(sub, splits.HOLDOUT)
            dev_sc = ledger.load_dev_scorecard(self.store, sub.family, sub.code_sha256)
            ev = evaluate.evaluate_holdout(sub, sframe, self.executor, quote_rate=rates, fx_desc=fx_desc,
                                           budget=ledger.budget_info(self.store, sub.family),
                                           dev_scorecard=dev_sc, deploy_id="e2e-local", store=self.store)
            sc_path = f"{base}/holdout_scorecard.json"
        sc = json.loads(scorecard.to_json_bytes(ev.scorecard))
        self.store.write(sc_path, scorecard.to_json_bytes(ev.scorecard))
        if ev.result is not None:
            self.store.write(f"results/{sc['result_file']}",
                             json.dumps(evaluate.result_payload(ev.result), allow_nan=False).encode("utf-8"))
        if job_kind == I.DEV_RESULT_KIND:
            payload = {"trial": trial, "job_id": job_id, "status": "done", "verdict": sc["verdict"],
                       "final": sc["final"], "dev_hard_gates_passed": sc["dev_hard_gates_passed"],
                       "scorecard_path": sc_path, "result_file": sc["result_file"], "error": None}
            ledger.put_record(self.store, ledger.make_record("dev_trial", stage=splits.DEV, payload=payload, **common))
        else:
            payload = {"touch": sc["holdout_touches"], "job_id": job_id, "status": "done", "verdict": sc["verdict"],
                       "holdout_trades": sc["headline"]["trades"], "scorecard_path": sc_path,
                       "result_file": sc["result_file"], "error": None}
            ledger.put_record(self.store, ledger.make_record("holdout", stage=splits.HOLDOUT, payload=payload,
                                                             **common))
        return {"success": True, "kind": job_kind, "code_sha256": sub.code_sha256, "stage": sc["stage"],
                "scorecard": sc, "result_file": sc["result_file"], "deploy_id": "e2e-local"}

    # ── GET routes ──
    def _job(self, scope: str, job_id: str) -> Dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound("unknown job")
        if job["kind"] == I.HOLDOUT_RESULT_KIND and scope != "judge":
            raise I.WrongScope("holdout jobs are judge-only")
        result = dict(job["result"])
        if scope == "builder" and "scorecard" in result:
            result["scorecard"] = scorecard.builder_view(result["scorecard"])
        return {"job_id": job_id, "status": job["status"], "kind": job["kind"], "result": result}

    def _scorecard(self, scope: str, family: str, sha12: str, stage: str) -> Dict[str, Any]:
        if stage == splits.HOLDOUT and scope != "judge":
            raise I.WrongScope("holdout scorecards are judge-only")
        name = "dev_scorecard.json" if stage == splits.DEV else "holdout_scorecard.json"
        raw = self.store.read(f"lab/{family}/{sha12}/{name}")
        if raw is None:
            raise NotFound("no such scorecard")
        sc = json.loads(raw)
        return scorecard.builder_view(sc) if scope == "builder" else sc


# ── Fixtures ────────────────────────────────────────────────────────────────
def _write_lab(root: Path, lab_id: str, src: bytes, *, max_hold_bars: int, allow_short: bool,
               warmup_bars: int, timeframe: str = "H1") -> Path:
    folder = root / I.GENERATED_DIR / lab_id
    folder.mkdir(parents=True)
    (folder / "strategy.py").write_bytes(src)
    (folder / "spec.md").write_bytes(SPEC_MD)
    man = json.loads(TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
    man.update(id=lab_id, family=lab_id, name=f"E2E {lab_id} (XAUUSD {timeframe})",
               description="synthetic end-to-end test strategy", symbol="XAUUSD", timeframe=timeframe,
               supported_symbols=["XAUUSD"], breadth_symbols=["XAGUSD"], max_hold_bars=max_hold_bars,
               allow_short=allow_short, warmup_bars=warmup_bars)
    assert contract.validate_manifest(man, require_shas=False) == []
    (folder / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    request = {"schema": I.REQUEST_SCHEMA, "lab_id": lab_id, "family": lab_id,
               "description": "synthetic end-to-end test strategy", "symbol": "XAUUSD", "timeframe": timeframe,
               "created_at": datetime.now(timezone.utc).isoformat(), "revision_notes": [],
               "approved_spec_sha256": hashlib.sha256(SPEC_MD).hexdigest(), "runner_version": "1"}
    (folder / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    return folder


class Lab:
    """One lab folder + its fake API + a way to run lab.cli subcommands against them."""

    def __init__(self, root: Path, lab_id: str, api: LocalLabApi):
        self.root, self.lab_id, self.api = root, lab_id, api
        self.folder = root / I.GENERATED_DIR / lab_id
        self.lines: List[str] = []

    def client(self, *, judge: bool = False) -> cli.LabClient:
        tok = {"judge_token": JUDGE_TOKEN} if judge else {"lab_token": LAB_TOKEN}
        return cli.LabClient(API_URL, transport=self.api.transport, sleep=lambda s: None, retries=0, **tok)

    def cli(self, *argv: str) -> int:
        self.lines.clear()
        env = {"SAJIM_LAB_ID": self.lab_id, "SAJIM_LAB_STAGE": "code"}
        return cli.main(list(argv), environ=env, root=self.root, out=self.lines.append,
                        client_factory=lambda **kw: self.client())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def sha(self) -> str:
        return hashlib.sha256((self.folder / "strategy.py").read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def example_run(tmp_path_factory):
    """ema_pullback.py through `check` then `dev`, in the real local sandbox (runs once)."""
    root = tmp_path_factory.mktemp("e2e_example")
    lab_id = "lab_ema_pullback_xauusd"
    _write_lab(root, lab_id, (EXAMPLES / "ema_pullback.py").read_bytes(), max_hold_bars=48,
               allow_short=True, warmup_bars=200)
    store = ledger.LocalDirStore(root / "store")
    # laptop per-run timeout (p1.2's 14 s is sized for a dedicated Modal sandbox; see _judging_testkit)
    api = LocalLabApi(store, {splits.DEV: dev_raw(seed=5)}, LocalTimeoutExecutor(LocalSubprocessExecutor()))
    lab = Lab(root, lab_id, api)
    out = {}
    out["check_rc"] = lab.cli("check")
    out["check_text"] = lab.text
    out["trials_after_check"] = ledger.count_dev_trials(store, lab_id)
    jobs_after_check = list(api.executor.jobs)
    out["dev_rc"] = lab.cli("dev")
    out["dev_text"] = lab.text
    out["jobs_after_check"] = jobs_after_check
    out["jobs_after_dev"] = list(api.executor.jobs)
    yield lab, store, out
    shutil.rmtree(root, ignore_errors=True)


# ── 1. One example through check -> dev ─────────────────────────────────────
def test_example_check_passes_through_the_real_sandbox(example_run):
    lab, store, out = example_run
    assert out["check_rc"] == cli.EXIT_OK, out["check_text"]
    assert f"CHECK PASS sha {lab.sha[:12]}" in out["check_text"]
    # p1.2: the check job (8 frozen runs), then the adaptive cuts after full1's own event bars
    assert out["jobs_after_check"][0] == ("check", CHECK_RUN_IDS)
    assert [label for label, _ in out["jobs_after_check"]] == ["check", "adaptive"]
    assert out["jobs_after_check"][1][1] == tuple(f"acut{i}" for i in range(1, len(out["jobs_after_check"][1][1]) + 1))
    recs = ledger.list_records(store, lab.lab_id, record_type="check")
    assert len(recs) == 1 and recs[0]["payload"]["passed"] is True
    assert all(recs[0]["payload"]["checks"][k] for k in I.CHECK_KEYS)
    assert out["trials_after_check"] == 0                           # checks are free: no trial slot


def test_example_dev_produces_a_valid_scorecard_with_a_verdict(example_run):
    lab, store, out = example_run
    sha = lab.sha
    # DEV = the check job again (full1 is the base run) + ONE job with the same adaptive cuts and
    # the plateau neighbours, nothing else (p1.2)
    new_jobs = out["jobs_after_dev"][len(out["jobs_after_check"]):]
    assert [label for label, _ in new_jobs] == ["check", "adaptive_neighbours"]
    adaptive_ids = out["jobs_after_check"][1][1]
    assert new_jobs[1][1][:len(adaptive_ids)] == adaptive_ids
    nbs = new_jobs[1][1][len(adaptive_ids):]
    assert all(rid.startswith("nb") for rid in nbs) and 1 <= len(nbs) <= 2 * gates.MAX_PARAMS
    assert len(new_jobs[1][1]) <= I.MAX_RUNS_PER_JOB

    server = json.loads(store.read(f"lab/{lab.lab_id}/{sha[:12]}/dev_scorecard.json"))
    assert scorecard.validate_scorecard(server) == []
    assert server["schema"] == "sajim.lab.scorecard/1"
    assert (server["stage"], server["label"]) == (splits.DEV, splits.IN_SAMPLE_LABEL)
    assert server["code_sha256"] == sha and server["trial"] == 1 and server["n_trials_family"] == 1
    assert server["verdict"] in gates.VERDICTS
    assert server["verdict"] != gates.CANDIDATE                       # DEV can never be CANDIDATE
    assert [g["id"] for g in server["gates"]] == [g.id for g in gates.GATES]
    assert "detail" in server and "plateau" in server["detail"] and "null" in server["detail"]
    assert server["cost_model"]["commission_model"] == gates.COMMISSION_MODEL
    assert server["cost_model"]["fx_desc"] == "quote currency USD (no conversion)"

    # the builder's copy: same scorecard minus the server-only detail
    local = json.loads((lab.folder / "scorecards" / f"dev_{sha[:12]}.json").read_text(encoding="utf-8"))
    assert "detail" not in local
    assert local == {k: v for k, v in server.items() if k != "detail"}
    expect_rc = cli.EXIT_OK if server["dev_hard_gates_passed"] else cli.EXIT_FAILED
    assert out["dev_rc"] == expect_rc, out["dev_text"]
    assert f"sha {sha[:12]}  DEV trial 1/{gates.MAX_DEV_TRIALS_PER_FAMILY}" in out["dev_text"]
    assert "[IN-SAMPLE]" in out["dev_text"] and "VERDICT" in out["dev_text"]
    assert not gates.contains_forbidden_label(out["dev_text"])

    # ledger: one claim slot + its budget_claim record, then the dev_trial record
    claims = ledger.list_records(store, lab.lab_id, record_type="budget_claim")
    assert [(c["payload"]["kind"], c["payload"]["slot"]) for c in claims] == [("trial", 1)]
    trials = ledger.list_records(store, lab.lab_id, record_type="dev_trial")
    assert len(trials) == 1 and trials[0]["payload"]["verdict"] == server["verdict"]
    assert trials[0]["code_sha256"] == sha and trials[0]["spec_sha256"] == hashlib.sha256(SPEC_MD).hexdigest()

    # the universal result file: cost-honest, same trades as the headline
    if server["headline"]["trades"]:
        payload = json.loads(store.read(f"results/{server['result_file']}"))
        assert payload["metadata"]["engine"] == gates.ENGINE
        assert payload["metadata"]["commission_model"] == gates.COMMISSION_MODEL
        assert payload["total_trades"] == server["headline"]["trades"] == len(payload["trades"])
        assert all(t["commission"] > 0 for t in payload["trades"])
        assert sum(t["net_pnl"] for t in payload["trades"]) == pytest.approx(server["headline"]["net"])

    # run.py (no holdout) rebuilds the gates from this JSON and re-derives the final DEV verdict
    assert run.gate_results_from_json(server["gates"]) == scorecard.gate_results_from_json(server["gates"])
    final = scorecard.verdict(run.gate_results_from_json(server["gates"]), splits.DEV, budget_exhausted=True,
                              holdout_burned=False)
    assert final.verdict in (gates.REJECT, gates.NOT_PROVEN)
    if not server["dev_hard_gates_passed"]:
        assert (final.verdict, final.final) == (server["verdict"], True)

    # build_log: nothing pending, the dev event recorded
    log = json.loads((lab.folder / "build_log.json").read_text(encoding="utf-8"))
    assert log["pending"] is None
    assert any(e["event"] == "dev" and e.get("status") == "done" for e in log["events"])


def test_retried_dev_request_is_deduplicated_and_uses_no_new_trial(example_run):
    lab, store, out = example_run
    n_jobs = len(lab.api.executor.jobs)
    rc = lab.cli("dev")
    assert rc == out["dev_rc"], lab.text
    assert len(lab.api.executor.jobs) == n_jobs                      # no new sandbox job
    assert ledger.count_dev_trials(store, lab.lab_id) == 1
    # the ledger's own dedup holds even without the API's request memory
    assert ledger.claim_dev_trial(store, lab.lab_id, lab_id=lab.lab_id, code_sha256=lab.sha,
                                  request_id=I.derive_request_id(lab.lab_id, lab.sha, "dev")) == 1
    assert ledger.count_dev_trials(store, lab.lab_id) == 1


def test_builder_cannot_reach_the_holdout_or_server_detail(example_run):
    lab, store, _ = example_run
    builder = lab.client()
    body = cli.build_body(lab.folder, stage=splits.HOLDOUT, kind="holdout")
    with pytest.raises(cli.LabApiError) as e:
        builder.evaluate(body)
    assert (e.value.status, e.value.code) == (403, "wrong_scope")
    assert ledger.count_holdout_touches(store, lab.lab_id) == 0
    sc = builder.scorecard(lab.lab_id, lab.sha[:12], "dev")
    assert "detail" not in sc and sc["code_sha256"] == lab.sha
    with pytest.raises(cli.LabApiError) as e:
        builder.scorecard(lab.lab_id, lab.sha[:12], "holdout")
    assert e.value.status == 403


def test_holdout_without_a_dev_pass_is_412_and_claims_nothing(example_run):
    lab, store, _ = example_run
    server = json.loads(store.read(f"lab/{lab.lab_id}/{lab.sha[:12]}/dev_scorecard.json"))
    if server["dev_hard_gates_passed"]:
        pytest.skip("the example happened to pass every hard DEV gate on this synthetic walk")
    body = cli.build_body(lab.folder, stage=splits.HOLDOUT, kind="holdout")
    with pytest.raises(cli.LabApiError) as e:
        lab.client(judge=True).evaluate(body)
    assert (e.value.status, e.value.code) == (412, "dev_not_passed")
    assert ledger.count_holdout_touches(store, lab.lab_id) == 0
    assert not ledger.is_slot_taken(store, I.holdout_sha_key(lab.sha))


# ── 2. Budget: the 4th DEV trial is refused before any job ──────────────────
def test_dev_budget_exhausted_is_429_before_any_sandbox_job(tmp_path):
    lab_id = "lab_zscore_budget_xauusd"
    _write_lab(tmp_path, lab_id, (EXAMPLES / "zscore_reversion.py").read_bytes(), max_hold_bars=96,
               allow_short=True, warmup_bars=200)
    store = ledger.LocalDirStore(tmp_path / "store")
    api = LocalLabApi(store, {splits.DEV: dev_raw(seed=5)}, InProcessExecutor())
    lab = Lab(tmp_path, lab_id, api)
    for k in range(gates.MAX_DEV_TRIALS_PER_FAMILY):                # three earlier trials of other shas
        sha_k = hashlib.sha256(f"earlier {k}".encode()).hexdigest()
        assert ledger.claim_dev_trial(store, lab_id, lab_id=lab_id, code_sha256=sha_k,
                                      request_id=I.derive_request_id(lab_id, sha_k, "dev")) == k + 1
    rc = lab.cli("dev")
    assert rc == cli.EXIT_REFUSED, lab.text
    assert "budget_exhausted" in lab.text
    assert api.executor.jobs == []                                  # refused before any sandbox job
    assert ledger.count_dev_trials(store, lab_id) == gates.MAX_DEV_TRIALS_PER_FAMILY
    assert ledger.list_records(store, lab_id, record_type="dev_trial") == []
    # a free check still works after the budget is gone (p1.2: + the adaptive-cut job when it passed)
    rc = lab.cli("check")
    assert rc in (cli.EXIT_OK, cli.EXIT_FAILED)
    labels = [label for label, _ in api.executor.jobs]
    assert labels == ["check", "adaptive"] if rc == cli.EXIT_OK else labels[0] == "check"


def test_manifest_family_drift_never_reaches_the_api(tmp_path):
    lab_id = "lab_zscore_drift_xauusd"
    folder = _write_lab(tmp_path, lab_id, (EXAMPLES / "zscore_reversion.py").read_bytes(), max_hold_bars=96,
                        allow_short=True, warmup_bars=200)
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    man["family"] = "lab_fresh_budget_xauusd"
    (folder / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    store = ledger.LocalDirStore(tmp_path / "store")
    api = LocalLabApi(store, {splits.DEV: dev_raw(seed=5)}, InProcessExecutor())
    lab = Lab(tmp_path, lab_id, api)
    assert lab.cli("dev") == cli.EXIT_LOCAL
    assert api.jobs == {} and ledger.list_families(store) == []


# ── 3. Holdout: DEV pass -> judge-only, once per sha, touches counted ───────
def test_planted_edge_dev_pass_then_one_sealed_holdout(tmp_path):
    """Planted causal edge (in-process executor for speed; the subprocess path is covered above)."""
    lab_id = "lab_planted_volume_xauusd"
    _write_lab(tmp_path, lab_id, PLANTED_SRC, max_hold_bars=12, allow_short=False, warmup_bars=100)
    store = ledger.LocalDirStore(tmp_path / "store")
    api = LocalLabApi(store, {splits.DEV: dev_raw(**PLANTED_DATA), splits.HOLDOUT: holdout_raw(**PLANTED_DATA)},
                      InProcessExecutor())
    lab = Lab(tmp_path, lab_id, api)
    assert lab.cli("check") == cli.EXIT_OK, lab.text
    assert lab.cli("dev") == cli.EXIT_OK, lab.text                   # every hard DEV gate passed
    sha = lab.sha
    assert ledger.dev_pass_record(store, lab_id, sha) is not None

    judge = lab.client(judge=True)
    body = cli.build_body(lab.folder, stage=splits.HOLDOUT, kind="holdout")
    assert body["request_id"] == I.derive_request_id(lab_id, sha, "holdout")
    started = judge.evaluate(body)
    assert started["kind"] == I.HOLDOUT_RESULT_KIND and started["holdout_touch"] == 1
    job = judge.wait(started["job_id"])
    sc = job["result"]["scorecard"]
    assert scorecard.validate_scorecard(sc) == []
    assert (sc["stage"], sc["label"], sc["final"]) == (splits.HOLDOUT, "HOLDOUT", True)
    assert sc["verdict"] == gates.CANDIDATE and sc["trial"] == 1 and sc["holdout_touches"] == 1
    assert set(sc["holdout_split"]) == {"H_A", "H_B"}
    assert ledger.count_holdout_touches(store, lab_id) == 1
    assert len(ledger.list_records(store, lab_id, record_type="holdout")) == 1

    # the builder never sees it
    with pytest.raises(cli.LabApiError) as e:
        lab.client().job(started["job_id"])
    assert e.value.status == 403

    # a retry of the same request is deduplicated: no second touch, no second job
    n_jobs = len(api.executor.inner.jobs)
    again = judge.evaluate(body)
    assert again["deduplicated"] is True and again["job_id"] == started["job_id"]
    assert len(api.executor.inner.jobs) == n_jobs
    assert ledger.count_holdout_touches(store, lab_id) == 1

    # once per sha: any other request for this sha loses the first-writer-wins slot (409)
    with pytest.raises(I.HoldoutAlreadyRun):
        ledger.claim_holdout(store, lab_id, sha, lab_id=lab_id, request_id=uuid.uuid4().hex)
    assert ledger.count_holdout_touches(store, lab_id) == 1

    # the runner's README renders from the real DEV + holdout scorecards, verdict first
    dev_sc = lab.client(judge=True).scorecard(lab_id, sha[:12], splits.DEV)
    assert "detail" in dev_sc                                         # judge scope gets the full scorecard
    readme = run.render_readme(run.ReadmeInputs(
        lab_id=lab_id, family=lab_id, name="Planted volume (XAUUSD H1)", description="synthetic",
        symbol="XAUUSD", timeframe="H1", verdict=sc["verdict"], reasons=list(sc["reasons"]), dev=dev_sc,
        holdout=sc, review=None, trials_used=dev_sc["n_trials_family"], holdout_touches=sc["holdout_touches"],
        global_trials=sc["n_trials_global"], final_sha=sha))
    assert readme.splitlines()[0].endswith(gates.CANDIDATE)
    for heading in ("## DEV (IN-SAMPLE) vs HOLDOUT", "## Gates", "## Cost model", "## Protocol"):
        assert heading in readme
    assert not gates.contains_forbidden_label(readme)

    # a changed strategy has no DEV pass: 412, nothing claimed
    (lab.folder / "strategy.py").write_bytes(PLANTED_SRC.replace(b'"default": 3.0', b'"default": 3.5'))
    cli.stamp_manifest(lab.folder)
    with pytest.raises(cli.LabApiError) as e:
        judge.evaluate(cli.build_body(lab.folder, stage=splits.HOLDOUT, kind="holdout"))
    assert e.value.status == 412
    assert ledger.count_holdout_touches(store, lab_id) == 1


def _refuse_constant(name: str):
    raise ValueError(f"non-finite JSON constant {name}")


def test_stored_scorecard_is_strict_json(example_run):
    lab, store, _ = example_run
    raw = store.read(f"lab/{lab.lab_id}/{lab.sha[:12]}/dev_scorecard.json")
    sc = json.loads(raw, parse_constant=_refuse_constant)            # no NaN / Infinity tokens
    assert np.isfinite(sc["window"]["n_bars"]) and sc["window"]["n_bars"] > 0
