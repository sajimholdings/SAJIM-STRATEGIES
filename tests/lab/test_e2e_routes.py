"""End-to-end Strategy Lab run through the REAL HTTP routes of the deployed app (P1 part 2 integration).

The chain under test:

    lab.cli main (real) -> LabClient (real; a TestClient-backed transport instead of urllib)
      -> modal_runner.build_api_app() (real: the /lab middleware branch, lab_api routes, LabDeps wiring)
      -> lab_job.spawn(job).object_id  (the ONLY faked hop: runs the real lab_job body in-process)
      -> modal_runner.lab_job.local -> lab_api.run_lab_job (real) -> real evaluate / causality (adaptive
         cuts) / simulator / robustness / scorecard
      -> LocalSubprocessExecutor + the real sandbox_runner (laptop stand-in for the Modal sandbox;
         NOT a security boundary) - or, in the seam test, the REAL _lab_executor() factory's
         ModalSandboxExecutor over a fake modal.Sandbox.create that runs the real SETUP / runner / FINISH
      -> the REAL _lab_store() factory: ModalStore over an in-memory Dict and a no-op volume

Modal itself is replaced in-process: JOBS / LAB_DICT (dicts with skip_if_exists), RESULTS_VOLUME
(reload/commit counters), RESULTS_MOUNT (a temp dir), modal.FunctionCall (polls the in-process
results), modal.current_function_call_id, and DataLoader (synthetic OHLC that applies end_date
like the real loader). Nothing is deployed and no Modal call is made. Synthetic OHLC only; never
loads data/**; nothing is written inside the repo.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import pandas as pd
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
mr = pytest.importorskip("sajim_quant.cloud.modal_runner")
from fastapi.testclient import TestClient  # noqa: E402

import modal  # noqa: E402
import modal.exception  # noqa: E402
from sajim_quant.data import loader as loader_mod  # noqa: E402
from sajim_quant.lab import cli, contract, gates, ledger, sandbox_modal, scorecard, splits  # noqa: E402
from sajim_quant.lab import interfaces as I  # noqa: E402
from sajim_quant.lab.causality import CHECK_RUN_IDS  # noqa: E402
from sajim_quant.lab.sandbox_local import LocalSubprocessExecutor  # noqa: E402
from sajim_quant.lab.store_modal import ModalStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from _judging_testkit import PLANTED_SRC, LocalTimeoutExecutor, dev_raw, holdout_raw  # noqa: E402
from _modal_fakes import FakeDict, FakeSandbox, FakeVolume, real_local_runner  # noqa: E402

# lab_job.local() warns that a local run has no mounted volumes; the store root is a temp dir instead.
pytestmark = pytest.mark.filterwarnings("ignore:The lab_job function is executing locally:UserWarning")

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_MANIFEST = REPO / ".claude" / "skills" / "lab-build" / "templates" / "manifest.json"

API_TOKEN = "routes-api-token-not-a-secret-01"
LAB_TOKEN = "routes-lab-token-not-a-secret-01"
JUDGE_TOKEN = "routes-judge-token-not-a-secret-1"
LAB_ID = "lab_planted_routes_xauusd"
OTHER_FAMILY = "lab_other_family_xauusd"
PLANTED_DATA = dict(drift=0.6, marker_every=75)
SPEC_MD = "# Spec\n\n## 1. Idea\nSynthetic e2e routes strategy.\n\n## 8. Trials\n".encode("utf-8")


# ── Modal, replaced in-process (the code under test stays real) ─────────────
def full_raw(**kw) -> pd.DataFrame:
    """DEV (seed 11) then PURGE + HOLDOUT (seed 23), hourly, as one XAUUSD frame."""
    dev, ho = dev_raw(**kw), holdout_raw(**kw)
    return pd.concat([dev, ho[ho.index >= pd.Timestamp("2026-01-01")]])


class SyntheticLoader:
    """DataLoader stand-in (same constructor and load signature); applies end_date like DataLoader.load."""

    calls: List[Tuple[str, str, Any]] = []
    raw: Optional[pd.DataFrame] = None

    def __init__(self, parquet_root=None, csv_root=None):
        self.parquet_root = parquet_root

    def load(self, symbol, timeframe="H1", start_date=None, end_date=None, set_index=True):
        type(self).calls.append((symbol, timeframe, end_date))
        if self.raw is None or symbol != "XAUUSD":
            raise FileNotFoundError(f"no synthetic data for {symbol}")
        df = self.raw
        if end_date is not None:
            df = df[df.index <= pd.Timestamp(end_date)]
        return df.copy()


class CountingExecutor:
    """Wraps a SandboxExecutor and records each job's label and run ids."""

    def __init__(self, inner: I.SandboxExecutor):
        self.inner = inner
        self.jobs: List[Tuple[str, Tuple[str, ...]]] = []

    def run(self, job: I.SandboxJob) -> I.SandboxResult:
        self.jobs.append((job.label, tuple(r.run_id for r in job.runs)))
        return self.inner.run(job)


class FakeCloud:
    """
    Patches the Modal objects modal_runner touches, then builds the REAL api app.

    `lab_job.spawn(job)` runs the real `lab_job.local(job)` synchronously under a fresh
    "fc-..." id (so a POST returns after its job finished), and `modal.FunctionCall.from_id(id)
    .get(timeout=0)` hands back that stored return value, exactly what _poll expects.
    """

    def __init__(self, mp: pytest.MonkeyPatch, root: Path, *, raw: Optional[pd.DataFrame],
                 executor_factory=None):
        self.root = root
        self.jobs_dict, self.lab_dict, self.volume = FakeDict(), FakeDict(), FakeVolume()
        self.results: Dict[str, Any] = {}
        self.spawned: List[Tuple[str, Dict[str, Any]]] = []
        self.current: Optional[str] = None
        real_lab_job, real_executor_factory = mr.lab_job, mr._lab_executor
        cloud = self

        class _SpawnedFunction:
            def spawn(self, job: Dict[str, Any]):
                job_id = "fc-" + uuid.uuid4().hex[:24]
                cloud.spawned.append((job_id, job))
                cloud.current = job_id
                try:
                    cloud.results[job_id] = real_lab_job.local(job)
                except Exception as e:  # noqa: BLE001 - the job raised: FunctionCall.get re-raises it
                    cloud.results[job_id] = e
                finally:
                    cloud.current = None
                return SimpleNamespace(object_id=job_id)

        class _FunctionCall:
            def __init__(self, job_id: str):
                self.job_id = job_id

            @classmethod
            def from_id(cls, job_id: str):
                return cls(job_id)

            def get(self, timeout=None):
                if self.job_id not in cloud.results:
                    raise modal.exception.NotFoundError(f"unknown call {self.job_id}")
                res = cloud.results[self.job_id]
                if isinstance(res, Exception):
                    raise res
                return res

        SyntheticLoader.calls = []
        SyntheticLoader.raw = raw
        mp.setattr(mr, "JOBS", self.jobs_dict)
        mp.setattr(mr, "LAB_DICT", self.lab_dict)
        mp.setattr(mr, "RESULTS_VOLUME", self.volume)
        mp.setattr(mr, "RESULTS_MOUNT", str(root / "results_volume"))
        mp.setattr(mr, "lab_job", _SpawnedFunction())
        self.executor = CountingExecutor((executor_factory or (lambda real: LocalTimeoutExecutor(
            LocalSubprocessExecutor())))(real_executor_factory))
        mp.setattr(mr, "_lab_executor", lambda: self.executor)
        mp.setattr(modal, "FunctionCall", _FunctionCall)
        mp.setattr(modal, "current_function_call_id", lambda: cloud.current)
        mp.setattr(loader_mod, "DataLoader", SyntheticLoader)
        for k, v in (("SAJIM_API_TOKEN", API_TOKEN), ("SAJIM_LAB_TOKEN", LAB_TOKEN),
                     ("SAJIM_JUDGE_TOKEN", JUDGE_TOKEN)):
            mp.setenv(k, v)
        self.client = TestClient(mr.build_api_app())

    # transport(method, url, headers, body, timeout) -> (status, bytes), as LabClient expects
    def transport(self, method: str, url: str, headers, body: Optional[bytes], timeout: float):
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        r = self.client.request(method, path, headers=dict(headers), content=body)
        return r.status_code, r.content

    def lab_client(self, *, judge: bool = False) -> cli.LabClient:
        tok = {"judge_token": JUDGE_TOKEN} if judge else {"lab_token": LAB_TOKEN}
        return cli.LabClient("https://sajim-api.invalid", transport=self.transport, sleep=lambda s: None,
                             retries=0, **tok)

    def store(self) -> ModalStore:
        """The store the routes and jobs use, built by the real factory (same root, same Dict)."""
        st = mr._lab_store()
        assert isinstance(st, ModalStore) and st.slots is self.lab_dict and st.volume is self.volume
        return st


def write_lab(root: Path, lab_id: str = LAB_ID) -> Path:
    folder = root / I.GENERATED_DIR / lab_id
    folder.mkdir(parents=True)
    (folder / "strategy.py").write_bytes(PLANTED_SRC)
    (folder / "spec.md").write_bytes(SPEC_MD)
    man = json.loads(TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
    man.update(id=lab_id, family=lab_id, name="Planted routes (XAUUSD H1)", description="synthetic routes test",
               symbol="XAUUSD", timeframe="H1", supported_symbols=["XAUUSD"], breadth_symbols=["XAGUSD"],
               max_hold_bars=12, allow_short=False, warmup_bars=100)
    assert contract.validate_manifest(man, require_shas=False) == []
    (folder / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    request = {"schema": I.REQUEST_SCHEMA, "lab_id": lab_id, "family": lab_id,
               "description": "synthetic routes test", "symbol": "XAUUSD", "timeframe": "H1",
               "created_at": datetime.now(timezone.utc).isoformat(), "revision_notes": [],
               "approved_spec_sha256": hashlib.sha256(SPEC_MD).hexdigest(), "runner_version": "1"}
    (folder / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    return folder


def run_cli(cloud: FakeCloud, root: Path, *argv: str, lab_id: str = LAB_ID) -> Tuple[int, str]:
    lines: List[str] = []
    rc = cli.main(list(argv), environ={"SAJIM_LAB_ID": lab_id, "SAJIM_LAB_STAGE": "code"}, root=root,
                  out=lines.append, client_factory=lambda **kw: cloud.lab_client())
    return rc, "\n".join(lines)


def refused(call) -> cli.LabApiError:
    with pytest.raises(cli.LabApiError) as e:
        call()
    return e.value


# ── The whole flow, once (real subprocess sandbox; about 50 child runs) ─────
@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e_routes")
    folder = write_lab(root)
    sha = hashlib.sha256(PLANTED_SRC).hexdigest()
    with pytest.MonkeyPatch.context() as mp:
        cloud = FakeCloud(mp, root, raw=full_raw(**PLANTED_DATA))
        store = cloud.store()
        builder, judge = cloud.lab_client(), cloud.lab_client(judge=True)
        out: Dict[str, Any] = {"cloud": cloud, "root": root, "folder": folder, "sha": sha, "store": store}

        out["check"] = run_cli(cloud, root, "check")
        out["jobs_after_check"] = list(cloud.executor.jobs)
        out["trials_after_check"] = ledger.count_dev_trials(store, LAB_ID)

        out["dev"] = run_cli(cloud, root, "dev")
        out["jobs_after_dev"] = list(cloud.executor.jobs)
        out["spawned_after_dev"] = len(cloud.spawned)
        out["loads_before_holdout"] = list(SyntheticLoader.calls)

        out["dev_retry"] = run_cli(cloud, root, "dev")                  # dedup: no job, no trial
        out["jobs_after_dev_retry"] = list(cloud.executor.jobs)
        out["spawned_after_dev_retry"] = len(cloud.spawned)

        # the builder token never reaches the holdout
        ho_body = cli.build_body(folder, stage=splits.HOLDOUT, kind="holdout")
        out["builder_holdout"] = refused(lambda: builder.evaluate(ho_body))

        # a lab_id is bound to its first family (a cli bypass naming another family is refused)
        other = cli.build_body(folder, stage=splits.DEV, kind="dev")
        other.update(family=OTHER_FAMILY, manifest={**other["manifest"], "family": OTHER_FAMILY},
                     request_id=I.derive_request_id(OTHER_FAMILY, sha, splits.DEV))
        out["family_bypass"] = refused(lambda: builder.evaluate(other))

        # a holdout must be what DEV judged (DEV-time manifest snapshot)
        drift = dict(ho_body, manifest={**ho_body["manifest"], "max_hold_bars": 13})
        out["snapshot_drift"] = refused(lambda: judge.evaluate(drift))
        out["touches_after_refusals"] = ledger.count_holdout_touches(store, LAB_ID)
        out["slot_after_refusals"] = ledger.is_slot_taken(store, I.holdout_sha_key(sha))
        out["jobs_before_holdout"] = list(cloud.executor.jobs)
        out["spawned_before_holdout"] = len(cloud.spawned)

        out["ho_started"] = judge.evaluate(ho_body)
        out["ho_job"] = judge.wait(out["ho_started"]["job_id"])
        out["jobs_after_holdout"] = list(cloud.executor.jobs)
        out["ho_retry"] = judge.evaluate(ho_body)
        out["spawned_after_ho_retry"] = len(cloud.spawned)
        out["jobs_after_ho_retry"] = list(cloud.executor.jobs)
        out["builder_ho_job"] = refused(lambda: builder.job(out["ho_started"]["job_id"]))
        out["builder_ho_scorecard"] = refused(lambda: builder.scorecard(LAB_ID, sha[:12], splits.HOLDOUT))

        verdict = out["ho_job"]["result"]["scorecard"]["verdict"]
        fin = {"lab_id": LAB_ID, "family": LAB_ID, "code_sha256": sha,
               "request_id": I.derive_request_id(LAB_ID, sha, "finalize"), "verdict": verdict,
               "source": "scorecard", "review": None}
        out["builder_finalize"] = refused(lambda: builder.finalize(fin))
        out["finalize"] = judge.finalize(fin)
        out["family_builder"] = builder.family(LAB_ID)
        out["dev_sc_builder"] = builder.scorecard(LAB_ID, sha[:12], splits.DEV)
        out["dev_sc_judge"] = judge.scorecard(LAB_ID, sha[:12], splits.DEV)
        out["old_route_with_lab_token"] = cloud.client.get("/jobs", headers={I.HEADER_LAB_TOKEN: LAB_TOKEN})
        out["lab_route_without_token"] = cloud.client.get(f"/lab/{LAB_ID}")
        out["lab_route_read_token"] = cloud.client.get(f"/lab/{LAB_ID}", headers={I.HEADER_API_TOKEN: API_TOKEN})
    # every patch is undone here, before the other tests in this module run (the store keeps its fakes)
    yield out
    shutil.rmtree(root, ignore_errors=True)


def test_check_through_the_routes_runs_the_check_and_adaptive_jobs_and_is_free(flow):
    rc, text = flow["check"]
    assert rc == cli.EXIT_OK, text
    assert f"CHECK PASS sha {flow['sha'][:12]}" in text
    jobs = flow["jobs_after_check"]
    assert [label for label, _ in jobs] == ["check", "adaptive"]
    assert jobs[0][1] == CHECK_RUN_IDS
    assert jobs[1][1] and all(r.startswith("acut") for r in jobs[1][1])
    assert flow["trials_after_check"] == 0
    (rec,) = ledger.list_records(flow["store"], LAB_ID, record_type="check")
    assert rec["payload"]["passed"] is True and set(rec["payload"]["checks"]) == set(I.CHECK_KEYS)
    assert rec["deploy_id"] == mr.DEPLOY_ID                         # run by modal_runner.lab_job


def test_dev_through_the_routes_uses_one_trial_and_two_sandbox_jobs(flow):
    rc, text = flow["dev"]
    assert rc == cli.EXIT_OK, text                                   # the planted edge passes every hard DEV gate
    assert "[IN-SAMPLE]" in text and not gates.contains_forbidden_label(text)
    new = flow["jobs_after_dev"][len(flow["jobs_after_check"]):]
    assert [label for label, _ in new] == ["check", "adaptive_neighbours"]
    adaptive = flow["jobs_after_check"][1][1]
    assert new[1][1][:len(adaptive)] == adaptive                    # the same seeded cuts as the free check
    assert all(r.startswith("nb") for r in new[1][1][len(adaptive):]) and len(new[1][1]) <= I.MAX_RUNS_PER_JOB
    store, sha = flow["store"], flow["sha"]
    assert ledger.count_dev_trials(store, LAB_ID) == 1
    slots = flow["cloud"].lab_dict.data                              # budget slots live in the sajim-lab Dict
    assert [k for k in slots if k.startswith("trial:")] == [I.trial_key(LAB_ID, 1)]
    base = f"lab/{LAB_ID}/{sha[:12]}"
    server = json.loads(store.read(f"{base}/dev_scorecard.json"))
    assert scorecard.validate_scorecard(server) == []
    assert server["dev_hard_gates_passed"] is True and server["verdict"] != gates.CANDIDATE
    assert server["deploy_id"] == mr.DEPLOY_ID and server["trial"] == 1
    assert store.read(f"{base}/strategy.py") == PLANTED_SRC and store.read(f"{base}/spec.md") == SPEC_MD
    payload = json.loads(store.read(f"results/{server['result_file']}"))
    assert payload["total_trades"] == server["headline"]["trades"] > 0
    # store-relative results/<file> is <RESULTS_MOUNT>/results/<file>, i.e. RESULTS_DIR: GET /results/{file} serves it
    assert (flow["root"] / "results_volume" / "results" / server["result_file"]).is_file()
    assert flow["cloud"].volume.commits > 0                          # ModalStore commits after its writes
    local = json.loads((flow["folder"] / "scorecards" / f"dev_{sha[:12]}.json").read_text(encoding="utf-8"))
    assert local == {k: v for k, v in server.items() if k != "detail"}
    # DEV jobs (check + dev) never load bars past the end of DEV
    loads = flow["loads_before_holdout"]
    assert loads and all(c[2] is not None and pd.Timestamp(c[2]) < pd.Timestamp(splits.HOLDOUT_START) for c in loads)


def test_retried_dev_is_deduplicated_through_the_routes(flow):
    rc, text = flow["dev_retry"]
    assert rc == flow["dev"][0], text
    assert flow["jobs_after_dev_retry"] == flow["jobs_after_dev"]
    assert flow["spawned_after_dev_retry"] == flow["spawned_after_dev"]
    assert ledger.count_dev_trials(flow["store"], LAB_ID) == 1


def test_builder_token_is_refused_everything_holdout(flow):
    for key in ("builder_holdout", "builder_ho_job", "builder_ho_scorecard", "builder_finalize"):
        assert (flow[key].status, flow[key].code) == (403, "wrong_scope"), key
    assert "detail" not in flow["dev_sc_builder"] and "detail" in flow["dev_sc_judge"]
    (ho,) = [s for s in flow["family_builder"]["scorecards"] if s["stage"] == splits.HOLDOUT]
    assert set(ho) == {"sha12", "stage", "created_at"}


def test_family_binding_and_snapshot_refusals_claim_nothing(flow):
    fb = flow["family_bypass"]
    assert (fb.status, fb.code) == (422, "family_mismatch")
    assert ledger.count_dev_trials(flow["store"], OTHER_FAMILY) == 0
    assert ledger.list_records(flow["store"], OTHER_FAMILY) == []
    sd = flow["snapshot_drift"]
    assert (sd.status, sd.code) == (412, "dev_snapshot_mismatch")
    assert any("max_hold_bars" in r for r in sd.reasons)
    assert flow["touches_after_refusals"] == 0 and flow["slot_after_refusals"] is False
    assert flow["jobs_before_holdout"] == flow["jobs_after_dev"]     # no refused request ran a job
    assert flow["spawned_before_holdout"] == flow["spawned_after_dev"]


def test_judge_holdout_through_the_routes_is_candidate_once(flow):
    started, job = flow["ho_started"], flow["ho_job"]
    assert started["holdout_touch"] == 1 and started["deduplicated"] is False
    assert job["status"] == "done"
    sc = job["result"]["scorecard"]
    assert scorecard.validate_scorecard(sc) == []
    assert (sc["stage"], sc["label"], sc["final"]) == (splits.HOLDOUT, "HOLDOUT", True)
    assert sc["verdict"] == gates.CANDIDATE and sc["holdout_touches"] == 1 and sc["trial"] == 1
    assert set(sc["holdout_split"]) == {"H_A", "H_B"}
    # the holdout ran the check job and the adaptive cuts on the holdout bars (two sandbox jobs)
    new = flow["jobs_after_holdout"][len(flow["jobs_before_holdout"]):]
    assert [label for label, _ in new] == ["check", "adaptive"]
    (rec,) = ledger.list_records(flow["store"], LAB_ID, record_type="holdout")
    assert rec["payload"]["status"] == "done" and rec["payload"]["job_id"] == started["job_id"]
    # retried: same job, no second touch, no new sandbox job
    again = flow["ho_retry"]
    assert again["deduplicated"] is True and again["job_id"] == started["job_id"]
    assert flow["spawned_after_ho_retry"] == flow["spawned_before_holdout"] + 1
    assert flow["jobs_after_ho_retry"] == flow["jobs_after_holdout"]
    assert ledger.count_holdout_touches(flow["store"], LAB_ID) == 1
    assert I.holdout_sha_key(flow["sha"]) in flow["cloud"].lab_dict.data


def test_finalize_records_the_verdict(flow):
    out = flow["finalize"]
    assert out["ok"] is True and out["verdict"] == gates.CANDIDATE
    (vd,) = ledger.list_records(flow["store"], LAB_ID, record_type="verdict")
    assert vd["payload"]["verdict"] == gates.CANDIDATE and vd["payload"]["holdout_touch"] == 1


def test_route_auth_through_the_real_middleware(flow):
    assert flow["old_route_with_lab_token"].status_code == 401       # a lab token never opens the old routes
    assert flow["lab_route_without_token"].status_code == 401
    r = flow["lab_route_read_token"]
    assert r.status_code == 200 and r.json()["n_trials"] == 1 and r.json()["holdout_touches"] == 1


# ── Budget: the 4th DEV trial is 429 through the routes, before any job ─────
def test_fourth_dev_trial_is_429_before_any_job(tmp_path):
    folder = write_lab(tmp_path)
    sha = hashlib.sha256((folder / "strategy.py").read_bytes()).hexdigest()
    with pytest.MonkeyPatch.context() as mp:
        cloud = FakeCloud(mp, tmp_path, raw=None)
        store = cloud.store()
        for k in range(gates.MAX_DEV_TRIALS_PER_FAMILY):              # three earlier trials of other shas
            sha_k = hashlib.sha256(f"earlier {k}".encode()).hexdigest()
            assert ledger.claim_dev_trial(store, LAB_ID, lab_id=LAB_ID, code_sha256=sha_k,
                                          request_id=I.derive_request_id(LAB_ID, sha_k, "dev")) == k + 1
        rc, text = run_cli(cloud, tmp_path, "dev")
        assert rc == cli.EXIT_REFUSED, text
        assert "budget_exhausted" in text
        assert cloud.spawned == [] and cloud.executor.jobs == [] and SyntheticLoader.calls == []
        assert ledger.count_dev_trials(store, LAB_ID) == gates.MAX_DEV_TRIALS_PER_FAMILY
        assert ledger.list_records(store, LAB_ID, record_type="dev_trial") == []
        assert not ledger.is_slot_taken(store, I.trial_key(LAB_ID, gates.MAX_DEV_TRIALS_PER_FAMILY + 1))
        assert sha not in json.dumps(ledger.list_records(store, LAB_ID))


# ── The M <-> R seam: the real _lab_executor() factory's ModalSandboxExecutor ─
def test_check_through_the_real_sandbox_executor_factory(tmp_path):
    """POST /lab/check -> lab_job -> _lab_executor() -> ModalSandboxExecutor -> (fake) Sandbox.create."""
    write_lab(tmp_path)
    created: List[Dict[str, Any]] = []
    sandboxes: List[FakeSandbox] = []

    def fake_create(*args: Any, **kw: Any) -> FakeSandbox:
        assert not args
        created.append(dict(kw))
        sb = FakeSandbox(tmp_path / f"sb{len(sandboxes)}" / "tmp", runner=real_local_runner())
        sandboxes.append(sb)
        return sb

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(modal.Sandbox, "create", fake_create)
        cloud = FakeCloud(mp, tmp_path, raw=full_raw(**PLANTED_DATA),
                          executor_factory=lambda real: LocalTimeoutExecutor(real()))
        real = cloud.executor.inner.inner
        assert isinstance(real, sandbox_modal.ModalSandboxExecutor)
        assert real.app is mr.app and real.image is sandbox_modal.sandbox_image()
        rc, text = run_cli(cloud, tmp_path, "check")
    assert rc == cli.EXIT_OK, text
    assert [label for label, _ in cloud.executor.jobs] == ["check", "adaptive"]
    assert len(created) == 2 and all(sb.terminated == 1 for sb in sandboxes)       # one fresh sandbox per job
    for kw in created:
        assert tuple(kw) == sandbox_modal.CREATE_KWARGS and kw["block_network"] is True
        assert kw["app"] is mr.app and kw["image"] is sandbox_modal.sandbox_image()
        assert not {"secrets", "volumes", "network_file_systems", "env"} & set(kw)
    for sb in sandboxes:
        assert [op for op in sb.ops() if op != "write"] == ["setup", "exec_runner", "finish", "terminate"]


# ── Constants that must agree across the part-2 seams ───────────────────────
def _function_kwargs(fn_name: str) -> Dict[str, Any]:
    import ast
    tree = ast.parse((REPO / "sajim_quant" / "cloud" / "modal_runner.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "function":
                    return {k.arg: k.value for k in dec.keywords}
    raise AssertionError(f"no @app.function on {fn_name}")


def test_constants_agree_across_the_seams():
    import ast

    from sajim_quant.cloud import lab_api
    from sajim_quant.lab import store_modal

    src = (REPO / "sajim_quant" / "cloud" / "modal_runner.py").read_text(encoding="utf-8")
    assert lab_api.SAFE_JOB.pattern == mr.SAFE_JOB.pattern
    assert f'modal.Dict.from_name("{store_modal.SLOTS_DICT_NAME}"' in src                 # LAB_DICT
    assert f'modal.Volume.from_name("{store_modal.RESULTS_VOLUME_NAME}"' in src           # RESULTS_VOLUME
    assert store_modal.RESULTS_ROOT == mr.RESULTS_MOUNT
    assert mr.RESULTS_DIR == f"{mr.RESULTS_MOUNT}/{lab_api.RESULTS_PREFIX}"   # GET /results/{file} serves lab results
    # the sandbox executor accepts the whole job timeout, so the runner's job budget never outlives its exec
    ex = sandbox_modal.ModalSandboxExecutor(object(), object())
    assert ex.timeout >= I.JOB_TIMEOUT_S and ex.cpu == I.SANDBOX_CPU and ex.memory == I.SANDBOX_MEMORY_MB
    # lab_job outlives its two sandbox jobs (check + adaptive, or check + adaptive_neighbours) plus loading
    # and the simulator-only robustness work
    lab_job_timeout = ast.literal_eval(_function_kwargs("lab_job")["timeout"])
    per_sandbox = I.JOB_TIMEOUT_S + sandbox_modal.SANDBOX_LIFETIME_MARGIN_S
    assert lab_job_timeout >= 2 * per_sandbox + 300
    # an abandoned spawn lock is only taken over after the API request that wrote it has timed out
    assert lab_api.SPAWN_LOCK_STALE_S > ast.literal_eval(_function_kwargs("api")["timeout"])
