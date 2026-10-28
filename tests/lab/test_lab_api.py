"""Tests for the /lab HTTP routes and the lab_job body (sajim_quant/cloud/lab_api.py) and their
wiring into sajim_quant/cloud/modal_runner.py (INTERFACES.md sections 8 and 9).

The routes run under FastAPI's TestClient with real trusted modules: a LocalDirStore ledger,
the real lab_job body (run_lab_job), evaluate/scorecard/ledger, and the in-process executor
from the judging testkit (test scaffolding for hand-written strategies, never a boundary).
Jobs are "spawned" synchronously. Synthetic OHLC only; never loads data/**; no Modal calls.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import pandas as pd
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from sajim_quant.cloud import lab_api  # noqa: E402
from sajim_quant.lab import cli, gates, ledger, scorecard, splits  # noqa: E402
from sajim_quant.lab import interfaces as I  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from _judging_testkit import PLANTED_SRC, SPEC_MD, InProcessExecutor, dev_raw, holdout_raw, manifest  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MODAL_RUNNER = REPO / "sajim_quant" / "cloud" / "modal_runner.py"

LAB = "lab-token-for-tests-00001"
JUDGE = "judge-token-for-tests-0001"
API = "api-token-for-tests-000001"
ENV = {"SAJIM_LAB_TOKEN": LAB, "SAJIM_JUDGE_TOKEN": JUDGE, "SAJIM_API_TOKEN": API}
H_LAB = {I.HEADER_LAB_TOKEN: LAB}
H_JUDGE = {I.HEADER_JUDGE_TOKEN: JUDGE}
H_READ = {I.HEADER_API_TOKEN: API}
LAB_ID = "lab_test_strategy"
PLANTED_DATA = dict(drift=0.6, marker_every=75)
LOOKAHEAD_SRC = PLANTED_SRC.replace(b'df["volume"].rolling(48, min_periods=48).mean().shift(1)',
                                    b'df["volume"].shift(-1)')


# ── Harness ─────────────────────────────────────────────────────────────────
class FakeLoader:
    """DataLoader stand-in over one synthetic XAUUSD frame; applies end_date like DataLoader.load."""

    def __init__(self, raw: Optional[pd.DataFrame]):
        self.raw = raw
        self.calls: List[Tuple[str, str, Any]] = []

    def load(self, symbol, timeframe="H1", start_date=None, end_date=None, set_index=True):
        self.calls.append((symbol, timeframe, end_date))
        if self.raw is None or symbol != "XAUUSD":
            raise FileNotFoundError(f"no synthetic data for {symbol}")
        df = self.raw
        if end_date is not None:
            df = df[df.index <= pd.Timestamp(end_date)]
        return df.copy()


def full_raw(**kw) -> pd.DataFrame:
    """DEV (seed 11) then PURGE + HOLDOUT (seed 23), hourly, as one frame."""
    dev, ho = dev_raw(**kw), holdout_raw(**kw)
    return pd.concat([dev, ho[ho.index >= pd.Timestamp("2026-01-01")]])


Runner = Callable[[Dict[str, Any], str], Tuple[str, Any, Optional[str]]]


class Harness:
    """lab_api.build_app over a LocalDirStore, a dict job memory and a synchronous spawner."""

    def __init__(self, root: Path, *, raw: Optional[pd.DataFrame] = None, runner: Optional[Runner] = None,
                 environ: Optional[Dict[str, str]] = None, store: Optional[I.LedgerStore] = None):
        self.store = store if store is not None else ledger.LocalDirStore(root / "store")
        self.kv: Dict[str, Any] = {}
        self.jobs: Dict[str, Tuple[str, Any, Optional[str]]] = {}
        self.spawned: List[Tuple[str, Dict[str, Any]]] = []
        self.loader = FakeLoader(raw)
        self.executor = InProcessExecutor()
        self.runner = runner
        self.deps = lab_api.LabDeps(store=lambda: self.store, spawn=self.spawn, poll=self.poll,
                                    kv_get=self.kv.get, kv_put=self.kv.__setitem__,
                                    read_token_ok=lambda req: req.headers.get(I.HEADER_API_TOKEN) == API,
                                    environ=dict(ENV if environ is None else environ), deploy_id="test-deploy")
        self.client = TestClient(lab_api.build_app(self.deps))

    def spawn(self, job: Dict[str, Any]) -> str:
        job_id = lab_api.new_job_id()
        self.spawned.append((job_id, job))
        if self.runner is None:
            res = lab_api.run_lab_job(job, store=self.store, executor=self.executor, loader=self.loader,
                                      deploy_id="test-deploy", job_id=job_id)
            self.jobs[job_id] = ("done" if res.get("success") else "failed", res, res.get("error"))
        else:
            self.jobs[job_id] = self.runner(job, job_id)
        return job_id

    def poll(self, job_id: str) -> Tuple[str, Any, Optional[str]]:
        return self.jobs.get(job_id, ("expired", None, "unknown job"))

    def transport(self, method: str, url: str, headers, body: Optional[bytes], timeout: float):
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        r = self.client.request(method, path, headers=dict(headers), content=body)
        return r.status_code, r.content

    def lab_client(self, *, judge: bool = False) -> cli.LabClient:
        tok = {"judge_token": JUDGE} if judge else {"lab_token": LAB}
        return cli.LabClient("http://lab.invalid", transport=self.transport, sleep=lambda s: None, retries=0, **tok)

    def post(self, path: str, body: Any, headers: Optional[Dict[str, str]] = None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        return self.client.post(path, content=raw, headers={"Content-Type": "application/json", **(headers or {})})

    # ledger state helpers
    def slots(self) -> List[str]:
        return self.store.listdir("lab/_slots")

    def claims(self, family: str = LAB_ID) -> List[Dict[str, Any]]:
        return ledger.list_records(self.store, family, record_type="budget_claim")


def never_runs(job: Dict[str, Any], job_id: str):
    raise AssertionError("a refused request must never spawn a job")


def body(src: bytes = PLANTED_SRC, *, stage: str = splits.DEV, kind: Optional[str] = None, lab_id: str = LAB_ID,
         family: Optional[str] = None, spec: bytes = SPEC_MD, request_id: Optional[str] = None,
         **man_over: Any) -> Dict[str, Any]:
    family = family or lab_id
    kind = kind or stage
    m = manifest(src, lab_id=lab_id, family=family, spec_md=spec, **man_over)
    sha = hashlib.sha256(src).hexdigest()
    rid = request_id or (uuid.uuid4().hex if kind == "check" else I.derive_request_id(family, sha, kind))
    return {"lab_id": lab_id, "family": family, "stage": stage, "request_id": rid,
            "strategy_py": src.decode("utf-8"), "spec_md": spec.decode("utf-8"), "manifest": m,
            "client": {"cli_version": "1"}}


def detail(resp) -> Dict[str, Any]:
    d = resp.json()["detail"]
    assert set(d) == {"code", "message", "reasons"}, d
    return d


def seed_dev_pass(store: ledger.LocalDirStore, b: Dict[str, Any], *, trial: int = 1) -> str:
    """The artefacts a passing DEV job leaves behind (snapshot, scorecard, claim, done record)."""
    src = b["strategy_py"].encode("utf-8")
    sha = hashlib.sha256(src).hexdigest()
    fam, lab_id = b["family"], b["lab_id"]
    base = f"lab/{fam}/{sha[:12]}"
    store.write(f"{base}/strategy.py", src)
    store.write(f"{base}/spec.md", b["spec_md"].encode("utf-8"))
    store.write(f"{base}/manifest.json", json.dumps(b["manifest"]).encode("utf-8"))
    sc = {"schema": gates.SCORECARD_SCHEMA, "id": lab_id, "family": fam, "code_sha256": sha,
          "spec_sha256": hashlib.sha256(b["spec_md"].encode("utf-8")).hexdigest(), "sha12": sha[:12],
          "stage": splits.DEV, "symbol": b["manifest"]["symbol"], "timeframe": b["manifest"]["timeframe"],
          "trial": trial, "dev_hard_gates_passed": True, "verdict": gates.NOT_PROVEN, "final": False,
          "gates": [], "headline": {"trades": 120}, "detail": {"secret": "server only"},
          "created_at": "2026-10-13T00:00:00+00:00", "result_file": f"{lab_id}_XAUUSD_H1_dev_result.json"}
    store.write(f"{base}/dev_scorecard.json", json.dumps(sc).encode("utf-8"))
    rid = I.derive_request_id(fam, sha, "dev")
    assert ledger.claim_dev_trial(store, fam, lab_id=lab_id, request_id=rid, code_sha256=sha) == trial
    ledger.put_record(store, ledger.make_record(
        "dev_trial", family=fam, lab_id=lab_id, request_id=rid, actor="api", code_sha256=sha, stage=splits.DEV,
        payload={"trial": trial, "job_id": "fc-seeded", "status": "done", "verdict": gates.NOT_PROVEN,
                 "final": False, "dev_hard_gates_passed": True, "scorecard_path": f"{base}/dev_scorecard.json",
                 "result_file": sc["result_file"], "error": None}))
    store.write_once(f"lab/_bindings/{lab_id}.json", json.dumps({"lab_id": lab_id, "family": fam}).encode())
    return sha


# ── 1. modal_runner wiring (no deploy, no Modal calls) ──────────────────────
def test_modal_runner_parses_and_imports_locally():
    ast.parse(MODAL_RUNNER.read_text(encoding="utf-8"))
    ast.parse((REPO / "sajim_quant" / "cloud" / "lab_api.py").read_text(encoding="utf-8"))
    mr = pytest.importorskip("sajim_quant.cloud.modal_runner")
    assert hasattr(mr, "lab_job") and hasattr(mr, "build_api_app")


def test_lab_factories_build_the_modal_store_and_sandbox_executor(monkeypatch, tmp_path):
    mr = pytest.importorskip("sajim_quant.cloud.modal_runner")
    store_modal = pytest.importorskip("sajim_quant.lab.store_modal")
    sandbox_modal = pytest.importorskip("sajim_quant.lab.sandbox_modal")
    monkeypatch.setattr(mr, "RESULTS_MOUNT", str(tmp_path))          # never mkdir /results on this machine
    st = mr._lab_store()
    assert isinstance(st, store_modal.ModalStore)
    assert st.volume is mr.RESULTS_VOLUME and st.slots is mr.LAB_DICT and Path(st.root) == tmp_path
    ex = mr._lab_executor()
    assert isinstance(ex, sandbox_modal.ModalSandboxExecutor)
    assert ex.app is mr.app and ex.image is sandbox_modal.sandbox_image()


def _decorator_kwargs(fn_name: str) -> Dict[str, ast.expr]:
    tree = ast.parse(MODAL_RUNNER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "function":
                    return {k.arg: k.value for k in dec.keywords}
    raise AssertionError(f"no @app.function on {fn_name}")


def test_lab_job_has_no_secret_and_the_api_secret_requires_only_the_old_key():
    kw = _decorator_kwargs("lab_job")
    assert "secrets" not in kw                                       # the judge token never reaches lab_job
    assert "volumes" in kw and "image" in kw
    assert ast.unparse(_decorator_kwargs("api")["secrets"]) == "[API_SECRET]"
    src = MODAL_RUNNER.read_text(encoding="utf-8")
    assert 'modal.Secret.from_name("sajim-api", required_keys=["SAJIM_API_TOKEN"])' in src
    for path in (MODAL_RUNNER, REPO / "sajim_quant" / "cloud" / "lab_api.py"):     # FastAPI closures
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(n, ast.ImportFrom) and n.module == "__future__" for n in tree.body), path


def test_the_deploy_fingerprint_covers_lab_api():
    from sajim_quant.cloud.fingerprint import CODE_SUFFIXES, MOUNTED_DIRS
    rel = Path("sajim_quant/cloud/lab_api.py")
    assert rel.suffix in CODE_SUFFIXES and any(rel.as_posix().startswith(d + "/") for d in MOUNTED_DIRS)


@pytest.fixture(scope="module")
def modal_app_factory():
    mr = pytest.importorskip("sajim_quant.cloud.modal_runner")

    def make(monkeypatch, env: Dict[str, Optional[str]]) -> TestClient:
        for k in ("SAJIM_API_TOKEN", "SAJIM_LAB_TOKEN", "SAJIM_JUDGE_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            if v is not None:
                monkeypatch.setenv(k, v)
        return TestClient(mr.build_api_app())

    return make


OLD_ROUTES = [("POST", "/backtest"), ("POST", "/noise"), ("GET", "/job/fc-abc123"), ("GET", "/jobs"),
              ("GET", "/results"), ("GET", "/results/x_result.json"), ("GET", "/noise/x_result.json")]


@pytest.mark.parametrize("method,path", OLD_ROUTES)
def test_old_routes_still_require_the_sajim_token(modal_app_factory, monkeypatch, method, path):
    c = modal_app_factory(monkeypatch, ENV)
    for headers in ({}, H_LAB, H_JUDGE, {I.HEADER_API_TOKEN: "wrong"}):
        r = c.request(method, path, headers=headers, content=b"{}" if method == "POST" else None)
        assert r.status_code == 401, (headers, r.text)
        assert r.json() == {"detail": "missing or invalid X-Sajim-Token header"}
    assert c.get("/health").status_code == 200


def test_modal_app_old_routes_413_is_unchanged(modal_app_factory, monkeypatch):
    c = modal_app_factory(monkeypatch, ENV)
    r = c.post("/backtest", headers={**H_READ, "content-length": "x"}, content=b"")
    assert r.status_code == 413


def test_modal_app_lab_routes_skip_the_sajim_token_and_check_lab_config(modal_app_factory, monkeypatch):
    # lab keys absent from the secret: old routes keep working, /lab/* answers 503
    c = modal_app_factory(monkeypatch, {"SAJIM_API_TOKEN": API})
    r = c.post("/lab/check", content=b"{}", headers=H_LAB)
    assert r.status_code == 503 and detail(r)["code"] == "lab_not_configured"
    assert c.get("/lab/lab_test_strategy", headers=H_READ).status_code == 503
    # equal tokens: refused
    c = modal_app_factory(monkeypatch, {"SAJIM_API_TOKEN": API, "SAJIM_LAB_TOKEN": LAB, "SAJIM_JUDGE_TOKEN": LAB})
    assert c.post("/lab/check", content=b"{}", headers=H_LAB).status_code == 503
    # configured: no token -> 401, oversize -> 413 before auth, malformed body -> 400 (no Modal touched)
    c = modal_app_factory(monkeypatch, ENV)
    assert c.post("/lab/check", content=b"{}").status_code == 401
    big = c.post("/lab/check", content=b"x" * (I.LAB_MAX_BODY_BYTES + 1))
    assert big.status_code == 413 and detail(big)["code"] == "too_large"
    bad = c.post("/lab/check", content=b"not json", headers=H_LAB)
    assert bad.status_code == 400 and detail(bad)["code"] == "malformed"
    assert c.get("/lab/job/not-a-job", headers=H_LAB).status_code == 400


# ── 2. Tokens, scopes, 413 ──────────────────────────────────────────────────
@pytest.mark.parametrize("env", [
    {"SAJIM_API_TOKEN": API},
    {"SAJIM_API_TOKEN": API, "SAJIM_LAB_TOKEN": LAB},
    {"SAJIM_API_TOKEN": API, "SAJIM_JUDGE_TOKEN": JUDGE},
    {"SAJIM_API_TOKEN": API, "SAJIM_LAB_TOKEN": LAB, "SAJIM_JUDGE_TOKEN": LAB},
    {"SAJIM_API_TOKEN": API, "SAJIM_LAB_TOKEN": "short", "SAJIM_JUDGE_TOKEN": JUDGE},
    {"SAJIM_API_TOKEN": API, "SAJIM_LAB_TOKEN": API, "SAJIM_JUDGE_TOKEN": JUDGE},
])
def test_lab_not_configured_is_503_on_every_lab_route(tmp_path, env):
    h = Harness(tmp_path, runner=never_runs, environ=env)
    for method, path, hdr in (("POST", "/lab/check", H_LAB), ("POST", "/lab/evaluate", H_JUDGE),
                              ("POST", "/lab/finalize", H_JUDGE), ("GET", "/lab/job/fc-abc", H_LAB),
                              ("GET", f"/lab/{LAB_ID}", H_READ),
                              ("GET", f"/lab/{LAB_ID}/scorecard/0123456789ab", H_JUDGE)):
        r = h.client.request(method, path, headers=hdr, content=b"{}" if method == "POST" else None)
        assert r.status_code == 503, (path, r.text)
        assert detail(r)["code"] == "lab_not_configured"
    assert h.spawned == [] and h.slots() == []


@pytest.mark.parametrize("headers", [
    {},
    {I.HEADER_LAB_TOKEN: "wrong-lab-token-000000"},
    {I.HEADER_JUDGE_TOKEN: "wrong-judge-token-0000"},
    {I.HEADER_JUDGE_TOKEN: "wrong-judge-token-0000", I.HEADER_LAB_TOKEN: LAB},   # no silent fallback
    {I.HEADER_LAB_TOKEN: JUDGE},                                                   # a judge value in the lab header
    {I.HEADER_API_TOKEN: "wrong"},
    {"Authorization": f"Bearer {LAB}"},
])
def test_missing_or_wrong_tokens_are_401_everywhere(tmp_path, headers):
    h = Harness(tmp_path, runner=never_runs)
    for method, path in (("POST", "/lab/check"), ("POST", "/lab/evaluate"), ("POST", "/lab/finalize"),
                         ("GET", "/lab/job/fc-abc"), ("GET", f"/lab/{LAB_ID}"),
                         ("GET", f"/lab/{LAB_ID}/scorecard/0123456789ab")):
        content = json.dumps(body()).encode() if method == "POST" else None
        r = h.client.request(method, path, headers=headers, content=content)
        assert r.status_code == 401, (path, headers, r.text)
        assert detail(r)["code"] == "unauthorized"
    assert h.spawned == [] and h.slots() == []


def test_413_comes_before_auth_and_before_any_parsing(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    huge = b"{" * (I.LAB_MAX_BODY_BYTES + 1)                             # not even JSON
    for hdr in ({}, H_LAB, H_JUDGE):
        r = h.client.post("/lab/evaluate", content=huge, headers=hdr)
        assert r.status_code == 413 and detail(r)["code"] == "too_large"
    r = h.client.post("/lab/check", content=b"{}", headers={**H_LAB, "content-length": "12abc"})
    assert r.status_code == 413


def test_chunked_oversize_body_is_413_without_buffering_it_all(tmp_path):
    h = Harness(tmp_path, runner=never_runs)

    def chunks():
        for _ in range(I.LAB_MAX_BODY_BYTES // 4096 + 2):
            yield b" " * 4096

    r = h.client.post("/lab/check", content=chunks(), headers=H_LAB)     # no Content-Length
    assert r.status_code == 413 and detail(r)["code"] == "too_large"
    r = h.client.post("/lab/check", content=chunks())                      # no token: 401 first
    assert r.status_code == 401
    assert h.spawned == []


def test_strategy_source_over_the_limit_is_413(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    src = PLANTED_SRC + b"#" * gates.MAX_SOURCE_BYTES
    r = h.post("/lab/check", body(src, kind="check"), H_LAB)
    assert r.status_code == 413 and h.spawned == []


def test_other_paths_are_not_lab_paths():
    assert lab_api.is_lab_path("/lab") and lab_api.is_lab_path("/lab/check")
    assert not lab_api.is_lab_path("/labx") and not lab_api.is_lab_path("/results/lab/x")
    assert not lab_api.is_lab_path("/") and not lab_api.is_lab_path("/lab_check")


# ── 3. Every refusal happens before any claim ───────────────────────────────
def _src_with_wrong_sha():
    b = body()
    b["manifest"]["code_sha256"] = "0" * 64
    return b


def _spec_with_wrong_sha():
    b = body()
    b["spec_md"] = "# edited after stamping\n"
    return b


def _lint_failure():
    return body(LOOKAHEAD_SRC)


def _unknown_manifest_key():
    b = body()
    b["manifest"]["risk_pct"] = 0.05
    return b


def _manifest_id_mismatch():
    b = body()
    b["lab_id"] = "lab_other_strategy"
    b["request_id"] = I.derive_request_id(LAB_ID, hashlib.sha256(PLANTED_SRC).hexdigest(), "dev")
    return b


def _family_mismatch_with_manifest():
    b = body()
    b["family"] = "lab_fresh_family"
    b["request_id"] = I.derive_request_id("lab_fresh_family", hashlib.sha256(PLANTED_SRC).hexdigest(), "dev")
    return b


def _short_coverage_universe():
    return body(symbol="BTCUSD", timeframe="M5", breadth_symbols=[])


REFUSALS = [
    ("not json", lambda: b"{not json", 400, "malformed"),
    ("array", lambda: b"[1, 2]", 400, "malformed"),
    ("nan", lambda: json.dumps(body()).replace('"client": {', '"x": NaN, "client": {').encode(), 400, "malformed"),
    ("duplicate key", lambda: json.dumps(body())[:-1].encode() + b', "lab_id": "lab_other_strategy"}', 400,
     "malformed"),
    ("bad lab id", lambda: {**body(), "lab_id": "Lab-X"}, 400, "malformed"),
    ("lab id newline", lambda: {**body(), "lab_id": LAB_ID + "\n"}, 400, "malformed"),
    ("bad family", lambda: {**body(), "family": "lab_"}, 400, "malformed"),
    ("purge stage", lambda: {**body(), "stage": "purge"}, 400, "malformed"),
    ("short request id", lambda: {**body(), "request_id": "abc"}, 400, "malformed"),
    ("strategy not text", lambda: {**body(), "strategy_py": 12}, 400, "malformed"),
    ("manifest not object", lambda: {**body(), "manifest": []}, 400, "malformed"),
    ("lone surrogate", lambda: json.dumps({**body(), "spec_md": "\ud800"}).encode(), 400, "malformed"),
    ("code sha mismatch", _src_with_wrong_sha, 422, "sha_mismatch"),
    ("spec sha mismatch", _spec_with_wrong_sha, 422, "sha_mismatch"),
    ("lint", _lint_failure, 422, "contract_refused"),
    ("system manifest key", _unknown_manifest_key, 422, "contract_refused"),
    ("manifest id != lab_id", _manifest_id_mismatch, 422, "contract_refused"),
    ("manifest family != family", _family_mismatch_with_manifest, 422, "contract_refused"),
    ("short coverage universe", _short_coverage_universe, 422, "contract_refused"),
    ("dev request id not derived", lambda: body(request_id=uuid.uuid4().hex), 400, "malformed"),
]


@pytest.mark.parametrize("name,make,status,code", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_evaluate_refusals_claim_nothing(tmp_path, name, make, status, code):
    h = Harness(tmp_path, runner=never_runs)
    r = h.post("/lab/evaluate", make(), H_LAB)
    assert r.status_code == status, r.text
    assert detail(r)["code"] == code
    assert h.spawned == [] and h.slots() == [] and ledger.list_families(h.store) == []
    assert h.store.listdir("lab/_bindings") == []


def test_contract_refusal_lists_reasons(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    r = h.post("/lab/evaluate", _lint_failure(), H_LAB)
    d = detail(r)
    assert d["reasons"] and all(x.startswith(("lint:", "manifest:")) for x in d["reasons"])


def test_scope_refusals_claim_nothing(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    r = h.post("/lab/check", body(kind="check"), H_READ)
    assert r.status_code == 403 and detail(r)["code"] == "wrong_scope"
    r = h.post("/lab/evaluate", body(), H_READ)
    assert r.status_code == 403
    r = h.post("/lab/evaluate", body(stage=splits.HOLDOUT), H_LAB)
    assert r.status_code == 403 and detail(r)["code"] == "wrong_scope"
    for hdr in (H_LAB, H_READ):
        assert h.post("/lab/finalize", {"lab_id": LAB_ID}, hdr).status_code == 403
    assert h.spawned == [] and h.slots() == []


def test_holdout_without_a_dev_pass_is_412_and_claims_nothing(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    r = h.post("/lab/evaluate", body(stage=splits.HOLDOUT), H_JUDGE)
    assert r.status_code == 412 and detail(r)["code"] == "dev_not_passed"
    assert h.spawned == [] and h.slots() == []


def test_dev_budget_exhausted_is_429_before_any_job(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    for k in range(gates.MAX_DEV_TRIALS_PER_FAMILY):
        sha_k = hashlib.sha256(f"earlier {k}".encode()).hexdigest()
        ledger.claim_dev_trial(h.store, LAB_ID, lab_id=LAB_ID, code_sha256=sha_k,
                               request_id=I.derive_request_id(LAB_ID, sha_k, "dev"))
    r = h.post("/lab/evaluate", body(), H_LAB)
    assert r.status_code == 429 and detail(r)["code"] == "budget_exhausted"
    assert h.spawned == [] and ledger.count_dev_trials(h.store, LAB_ID) == gates.MAX_DEV_TRIALS_PER_FAMILY


def test_check_needs_its_own_request_id_and_dev_stage(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    sha = hashlib.sha256(PLANTED_SRC).hexdigest()
    for kind in ("dev", "holdout", "finalize"):
        r = h.post("/lab/check", body(kind="check", request_id=I.derive_request_id(LAB_ID, sha, kind)), H_LAB)
        assert r.status_code == 400, kind
    r = h.post("/lab/check", body(kind="check", stage=splits.HOLDOUT), H_JUDGE)
    assert r.status_code == 400                                       # checks never run on holdout bars
    assert h.spawned == []


# ── 4. Family binding and the holdout snapshot ──────────────────────────────
def _running(job, job_id):
    return "running", None, None


def test_lab_id_is_bound_to_its_first_family(tmp_path):
    h = Harness(tmp_path, runner=_running)
    r = h.post("/lab/evaluate", body(), H_LAB)
    assert r.status_code == 200 and r.json()["trial"] == 1
    bound = json.loads(h.store.read(f"lab/_bindings/{LAB_ID}.json"))
    assert bound["family"] == LAB_ID
    # same lab_id, a consistent body naming a fresh family (a fresh budget): refused, nothing claimed there
    src2 = PLANTED_SRC + b"\n# revision\n"
    r = h.post("/lab/evaluate", body(src2, family="lab_fresh_family"), H_LAB)
    assert r.status_code == 422 and detail(r)["code"] == "family_mismatch"
    assert ledger.count_dev_trials(h.store, "lab_fresh_family") == 0
    r = h.post("/lab/check", body(src2, kind="check", family="lab_fresh_family"), H_LAB)
    assert r.status_code == 422
    assert len(h.spawned) == 1


def test_holdout_must_match_the_dev_time_snapshot(tmp_path):
    h = Harness(tmp_path, runner=_running)
    sha = seed_dev_pass(h.store, body())
    mismatches = [body(stage=splits.HOLDOUT, max_hold_bars=48), body(stage=splits.HOLDOUT, allow_short=True),
                  body(stage=splits.HOLDOUT, warmup_bars=150), body(stage=splits.HOLDOUT, breadth_symbols=[]),
                  body(stage=splits.HOLDOUT, spec=SPEC_MD + b"\nedited after DEV\n")]
    for b in mismatches:
        r = h.post("/lab/evaluate", b, H_JUDGE)
        assert r.status_code == 412, r.text
        assert detail(r)["code"] == "dev_snapshot_mismatch" and detail(r)["reasons"]
    assert ledger.count_holdout_touches(h.store, LAB_ID) == 0
    assert not ledger.is_slot_taken(h.store, I.holdout_sha_key(sha)) and h.spawned == []
    # the matching submission is accepted and claims exactly one touch
    r = h.post("/lab/evaluate", body(stage=splits.HOLDOUT), H_JUDGE)
    assert r.status_code == 200, r.text
    assert r.json()["holdout_touch"] == 1 and r.json()["kind"] == I.HOLDOUT_RESULT_KIND
    assert ledger.count_holdout_touches(h.store, LAB_ID) == 1


def test_holdout_already_run_is_409(tmp_path):
    h = Harness(tmp_path, runner=_running)
    sha = seed_dev_pass(h.store, body())
    other = uuid.uuid4().hex
    ledger.claim_holdout(h.store, LAB_ID, sha, lab_id=LAB_ID, request_id=other)
    r = h.post("/lab/evaluate", body(stage=splits.HOLDOUT), H_JUDGE)
    assert r.status_code == 409 and detail(r)["code"] == "holdout_already_run"
    assert h.spawned == []


def test_ignored_client_fields_are_recorded_with_the_claim(tmp_path):
    h = Harness(tmp_path, runner=_running)
    b = {**body(), "start_date": "2026-01-01", "risk_pct": 5}
    assert h.post("/lab/evaluate", b, H_LAB).status_code == 200
    (claim,) = h.claims()
    assert claim["payload"]["ignored_client_fields"] == ["start_date", "risk_pct"]
    assert "start_date" not in h.spawned[0][1] and "risk_pct" not in h.spawned[0][1]


# ── 5. Dedup and respawn ────────────────────────────────────────────────────
def test_retried_dev_is_deduplicated(tmp_path):
    h = Harness(tmp_path, runner=_running)
    first = h.post("/lab/evaluate", body(), H_LAB).json()
    again = h.post("/lab/evaluate", body(), H_LAB).json()
    assert again["job_id"] == first["job_id"] and again["deduplicated"] is True and first["deduplicated"] is False
    assert again["trial"] == first["trial"] == 1
    assert len(h.spawned) == 1 and ledger.count_dev_trials(h.store, LAB_ID) == 1


def test_failed_job_is_respawned_under_the_same_claim(tmp_path):
    outcomes = iter([("failed", {"success": False, "error": "infra"}, "infra"), ("running", None, None)])
    h = Harness(tmp_path, runner=lambda job, job_id: next(outcomes))
    first = h.post("/lab/evaluate", body(), H_LAB).json()
    second = h.post("/lab/evaluate", body(), H_LAB).json()
    assert second["job_id"] != first["job_id"] and second["deduplicated"] is False
    assert second["trial"] == first["trial"] == 1
    assert len(h.spawned) == 2 and ledger.count_dev_trials(h.store, LAB_ID) == 1
    assert [s[1]["trial"] for s in h.spawned] == [1, 1]
    third = h.post("/lab/evaluate", body(), H_LAB).json()            # now running: plain dedup
    assert third["job_id"] == second["job_id"] and len(h.spawned) == 2


def _done_then_expired(h_ref: List[Harness]):
    def run(job, job_id):
        h = h_ref[0]
        sha = hashlib.sha256(job["strategy_py"].encode()).hexdigest()
        path = f"lab/{job['family']}/{sha[:12]}/dev_scorecard.json"
        h.store.write(path, json.dumps({"stage": "dev", "code_sha256": sha, "verdict": gates.REJECT,
                                        "result_file": "r.json", "detail": {"x": 1}}).encode())
        ledger.put_record(h.store, ledger.make_record(
            "dev_trial", family=job["family"], lab_id=job["lab_id"], request_id=job["request_id"], actor="api",
            code_sha256=sha, stage="dev", payload={"trial": job["trial"], "job_id": job_id, "status": "done",
                                                    "verdict": gates.REJECT, "final": False,
                                                    "dev_hard_gates_passed": False, "scorecard_path": path,
                                                    "result_file": "r.json", "error": None}))
        return "expired", None, "result expired or unknown job id"
    return run


def test_expired_job_with_a_done_record_is_never_rerun(tmp_path):
    ref: List[Harness] = []
    h = Harness(tmp_path, runner=_done_then_expired(ref))
    ref.append(h)
    first = h.post("/lab/evaluate", body(), H_LAB).json()
    again = h.post("/lab/evaluate", body(), H_LAB).json()
    assert again["job_id"] == first["job_id"] and again["deduplicated"] is True and len(h.spawned) == 1
    # the Modal result expired: GET /lab/job rebuilds it from the done record + stored scorecard
    job = h.lab_client().job(first["job_id"])
    assert job["status"] == "done" and job["kind"] == I.DEV_RESULT_KIND
    assert job["result"]["scorecard"]["verdict"] == gates.REJECT and "detail" not in job["result"]["scorecard"]
    # the job memory itself is lost too: the ledger still dedups onto the finished job
    h.kv.clear()
    lost = h.post("/lab/evaluate", body(), H_LAB).json()
    assert lost["job_id"] == first["job_id"] and lost["deduplicated"] is True and len(h.spawned) == 1
    assert ledger.count_dev_trials(h.store, LAB_ID) == 1


def test_retried_check_is_deduplicated_and_free(tmp_path):
    h = Harness(tmp_path, runner=_running)
    b = body(kind="check")
    first = h.post("/lab/check", b, H_LAB).json()
    again = h.post("/lab/check", b, H_LAB).json()
    assert first["kind"] == I.CHECK_RESULT_KIND and again["job_id"] == first["job_id"] and again["deduplicated"]
    assert "trial" not in first and h.slots() == [] and len(h.spawned) == 1


def test_job_route_validation(tmp_path):
    h = Harness(tmp_path, runner=_running)
    assert h.client.get("/lab/job/abc", headers=H_LAB).status_code == 400
    assert h.client.get("/lab/job/fc-unknown1", headers=H_LAB).status_code == 404
    h.kv["fc-backtest1"] = {"kind": "backtest", "status": "done"}       # a dashboard job: never via /lab
    assert h.client.get("/lab/job/fc-backtest1", headers=H_READ).status_code == 404


def test_spawn_failure_is_retryable_and_keeps_the_claim(tmp_path):
    calls = {"n": 0}

    def flaky(job, job_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("modal unavailable")
        return "running", None, None

    h = Harness(tmp_path, runner=flaky)
    r = h.post("/lab/evaluate", body(), H_LAB)
    assert r.status_code == 503 and detail(r)["code"] == "unavailable"
    r = h.post("/lab/evaluate", body(), H_LAB)
    assert r.status_code == 200 and r.json()["trial"] == 1
    assert ledger.count_dev_trials(h.store, LAB_ID) == 1


# ── 6. The real flow: check -> DEV -> sealed holdout -> finalize ────────────
@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    root = tmp_path_factory.mktemp("lab_api_flow")
    h = Harness(root, raw=full_raw(**PLANTED_DATA))
    folder = root / I.GENERATED_DIR / LAB_ID
    folder.mkdir(parents=True)
    (folder / "strategy.py").write_bytes(PLANTED_SRC)
    (folder / "spec.md").write_bytes(SPEC_MD)
    (folder / "manifest.json").write_text(json.dumps(manifest(PLANTED_SRC)), encoding="utf-8")
    builder, judge = h.lab_client(), h.lab_client(judge=True)
    out: Dict[str, Any] = {"h": h, "folder": folder, "builder": builder, "judge": judge}
    out["check_started"] = builder.check(cli.build_body(folder, stage=None, kind="check"))
    out["check_job"] = builder.wait(out["check_started"]["job_id"])
    out["dev_started"] = builder.evaluate(cli.build_body(folder, stage=splits.DEV, kind="dev"))
    out["dev_job"] = builder.wait(out["dev_started"]["job_id"])
    out["loads_before_holdout"] = list(h.loader.calls)                # check + DEV jobs only
    out["ho_body"] = cli.build_body(folder, stage=splits.HOLDOUT, kind="holdout")
    out["ho_started"] = judge.evaluate(out["ho_body"])
    out["ho_job"] = judge.wait(out["ho_started"]["job_id"])
    out["sha"] = hashlib.sha256(PLANTED_SRC).hexdigest()
    return out


def test_flow_check_is_free_and_passes(flow):
    h = flow["h"]
    job = flow["check_job"]
    assert job["status"] == "done" and job["kind"] == I.CHECK_RESULT_KIND
    res = job["result"]
    assert res["success"] is True and res["passed"] is True and set(res["checks"]) == set(I.CHECK_KEYS)
    recs = ledger.list_records(h.store, LAB_ID, record_type="check")
    assert len(recs) == 1 and recs[0]["payload"]["passed"] is True
    assert flow["check_started"]["deduplicated"] is False and "trial" not in flow["check_started"]


def test_flow_dev_writes_snapshot_scorecard_results_and_record(flow):
    h, sha = flow["h"], flow["sha"]
    started, job = flow["dev_started"], flow["dev_job"]
    assert started["trial"] == 1 and started["kind"] == I.DEV_RESULT_KIND
    assert job["status"] == "done"
    sc_builder = job["result"]["scorecard"]
    assert "detail" not in sc_builder and sc_builder["stage"] == splits.DEV
    assert sc_builder["label"] == splits.IN_SAMPLE_LABEL and sc_builder["verdict"] != gates.CANDIDATE
    assert sc_builder["dev_hard_gates_passed"] is True                 # the planted edge passes DEV
    base = f"lab/{LAB_ID}/{sha[:12]}"
    server = json.loads(h.store.read(f"{base}/dev_scorecard.json"))
    assert scorecard.validate_scorecard(server) == [] and "detail" in server
    assert {k: v for k, v in server.items() if k != "detail"} == sc_builder
    assert h.store.read(f"{base}/strategy.py") == PLANTED_SRC and h.store.read(f"{base}/spec.md") == SPEC_MD
    assert json.loads(h.store.read(f"{base}/manifest.json"))["code_sha256"] == sha
    payload = json.loads(h.store.read(f"results/{server['result_file']}"))
    assert payload["total_trades"] == server["headline"]["trades"] == len(payload["trades"]) > 0
    (rec,) = ledger.list_records(h.store, LAB_ID, record_type="dev_trial")
    assert rec["payload"]["job_id"] == started["job_id"] and rec["payload"]["status"] == "done"
    assert rec["payload"]["trial"] == 1 and rec["spec_sha256"] == hashlib.sha256(SPEC_MD).hexdigest()
    # DEV jobs never load bars past the end of DEV
    dev_loads = flow["loads_before_holdout"]
    assert len(dev_loads) >= 2                                          # the check job's and the DEV job's
    assert all(c[2] is not None and pd.Timestamp(c[2]) < pd.Timestamp(splits.HOLDOUT_START) for c in dev_loads)


def test_flow_holdout_is_judge_only_and_sealed_from_the_builder(flow):
    h, sha, builder, judge = flow["h"], flow["sha"], flow["builder"], flow["judge"]
    started, job = flow["ho_started"], flow["ho_job"]
    assert started["holdout_touch"] == 1 and started["kind"] == I.HOLDOUT_RESULT_KIND
    assert job["status"] == "done"
    sc = job["result"]["scorecard"]
    assert scorecard.validate_scorecard(sc) == []
    assert (sc["stage"], sc["label"], sc["final"]) == (splits.HOLDOUT, "HOLDOUT", True)
    assert sc["verdict"] in gates.VERDICTS and set(sc["holdout_split"]) == {"H_A", "H_B"}
    (rec,) = ledger.list_records(h.store, LAB_ID, record_type="holdout")
    assert rec["payload"]["touch"] == 1 and rec["payload"]["job_id"] == started["job_id"]
    for call in (lambda: builder.job(started["job_id"]), lambda: builder.scorecard(LAB_ID, sha[:12], "holdout")):
        with pytest.raises(cli.LabApiError) as e:
            call()
        assert e.value.status == 403
    assert judge.scorecard(LAB_ID, sha[:12], "holdout")["verdict"] == sc["verdict"]
    assert "detail" in judge.scorecard(LAB_ID, sha[:12], "dev")
    assert "detail" not in builder.scorecard(LAB_ID, sha[:12], "dev")
    r = h.client.get(f"/lab/{LAB_ID}/scorecard/{sha[:12]}?stage=holdout", headers=H_READ)
    assert r.status_code == 200                                         # read scope (dashboard) may look
    assert h.client.get(f"/lab/{LAB_ID}/scorecard/{sha[:12]}?stage=purge", headers=H_JUDGE).status_code == 400
    assert h.client.get(f"/lab/{LAB_ID}/scorecard/ffffffffffff", headers=H_JUDGE).status_code == 404


def test_flow_holdout_retry_spends_no_second_touch(flow):
    h, judge = flow["h"], flow["judge"]
    n = len(h.spawned)
    again = judge.evaluate(flow["ho_body"])
    assert again["deduplicated"] is True and again["job_id"] == flow["ho_started"]["job_id"]
    assert len(h.spawned) == n and ledger.count_holdout_touches(h.store, LAB_ID) == 1
    # a later manifest edit cannot reach a second holdout either: same derived request id
    b = dict(flow["ho_body"], manifest={**flow["ho_body"]["manifest"], "name": "renamed"})
    assert judge.evaluate(b)["job_id"] == flow["ho_started"]["job_id"]


def test_flow_family_summary_redacts_holdout_for_the_builder(flow):
    builder, judge, h, sha = flow["builder"], flow["judge"], flow["h"], flow["sha"]
    fam_j = judge.family(LAB_ID)
    assert (fam_j["family"], fam_j["ids"], fam_j["n_trials"], fam_j["holdout_touches"]) == (LAB_ID, [LAB_ID], 1, 1)
    assert fam_j["max_trials"] == gates.MAX_DEV_TRIALS_PER_FAMILY and fam_j["holdout_burned"] is False
    ho_entry = [s for s in fam_j["scorecards"] if s["stage"] == splits.HOLDOUT]
    assert ho_entry and "verdict" in ho_entry[0]
    fam_b = builder.family(LAB_ID)
    (ho_b,) = [s for s in fam_b["scorecards"] if s["stage"] == splits.HOLDOUT]
    assert set(ho_b) == {"sha12", "stage", "created_at"}
    for rec in fam_b["records"]:
        if rec["record_type"] in ("holdout", "review", "verdict"):
            assert set(rec) == {"record_type", "created_at", "payload"} and set(rec["payload"]) == {"touch"}
    assert any(r["record_type"] == "holdout" for r in fam_b["records"])
    assert h.client.get("/lab/lab_unknown_family", headers=H_LAB).status_code == 404
    assert h.client.get("/lab/check", headers=H_LAB).status_code == 400


def _finalize_body(flow, verdict: str, *, source: str = "scorecard", review: Optional[dict] = None) -> dict:
    return {"lab_id": LAB_ID, "family": LAB_ID, "code_sha256": flow["sha"],
            "request_id": I.derive_request_id(LAB_ID, flow["sha"], "finalize"), "verdict": verdict,
            "source": source, "review": review}


def test_flow_finalize(flow):
    h, judge, builder, sha = flow["h"], flow["judge"], flow["builder"], flow["sha"]
    ho_verdict = flow["ho_job"]["result"]["scorecard"]["verdict"]
    better = [v for v in gates.VERDICTS if gates.worse(v, ho_verdict) != v]
    for v in better:                                                   # cannot beat the holdout verdict
        with pytest.raises(cli.LabApiError) as e:
            judge.finalize(_finalize_body(flow, v))
        assert e.value.status == 422 and e.value.code == "verdict_refused"
    with pytest.raises(cli.LabApiError) as e:
        builder.finalize(_finalize_body(flow, ho_verdict))
    assert e.value.status == 403
    bad_rid = dict(_finalize_body(flow, ho_verdict), request_id=uuid.uuid4().hex)
    with pytest.raises(cli.LabApiError) as e:
        judge.finalize(bad_rid)
    assert e.value.status == 400
    bad_review = {"schema": I.REVIEW_SCHEMA, "lab_id": LAB_ID, "code_sha256": sha, "decision": "UPGRADE",
                  "reason": "x"}
    with pytest.raises(cli.LabApiError) as e:
        judge.finalize(_finalize_body(flow, ho_verdict, review=bad_review))
    assert e.value.status == 422
    assert ledger.list_records(h.store, LAB_ID, record_type="verdict") == []

    final = gates.worse(ho_verdict, gates.NOT_PROVEN)
    review = {"schema": I.REVIEW_SCHEMA, "lab_id": LAB_ID, "code_sha256": sha, "decision": "DOWNGRADE",
              "to": gates.NOT_PROVEN, "reason": "synthetic data only"}
    out = judge.finalize(_finalize_body(flow, final, source="critic", review=review))
    assert out["ok"] is True and out["verdict"] == final and len(out["records"]) == 2
    (rv,) = ledger.list_records(h.store, LAB_ID, record_type="review")
    assert rv["payload"] == {"decision": "DOWNGRADE", "to": gates.NOT_PROVEN, "reason": "synthetic data only",
                             "verdict_before": ho_verdict, "verdict_after": final}
    (vd,) = ledger.list_records(h.store, LAB_ID, record_type="verdict")
    assert vd["payload"] == {"verdict": final, "final": True, "source": "critic", "trial": 1, "holdout_touch": 1}
    # retried: same records, nothing new; a different verdict afterwards: 409
    again = judge.finalize(_finalize_body(flow, final, source="critic", review=review))
    assert sorted(again["records"]) == sorted(out["records"])
    assert len(ledger.list_records(h.store, LAB_ID, record_type="verdict")) == 1
    other = gates.REJECT if final != gates.REJECT else None
    if other:
        with pytest.raises(cli.LabApiError) as e:
            judge.finalize(_finalize_body(flow, other, source="budget"))
        assert e.value.status == 409
    # the builder sees only the touch of review/verdict records
    fam_b = builder.family(LAB_ID)
    assert {r["record_type"] for r in fam_b["records"]} >= {"review", "verdict"}


def test_finalize_without_a_dev_scorecard_is_404(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    sha = hashlib.sha256(PLANTED_SRC).hexdigest()
    b = {"lab_id": LAB_ID, "family": LAB_ID, "code_sha256": sha,
         "request_id": I.derive_request_id(LAB_ID, sha, "finalize"), "verdict": gates.REJECT,
         "source": "budget", "review": None}
    r = h.post("/lab/finalize", b, H_JUDGE)
    assert r.status_code == 404 and detail(r)["code"] == "not_found"


def test_finalize_without_holdout_cannot_be_candidate(tmp_path):
    h = Harness(tmp_path, runner=never_runs)
    sha = seed_dev_pass(h.store, body())
    b = {"lab_id": LAB_ID, "family": LAB_ID, "code_sha256": sha,
         "request_id": I.derive_request_id(LAB_ID, sha, "finalize"), "verdict": gates.CANDIDATE,
         "source": "budget", "review": None}
    r = h.post("/lab/finalize", b, H_JUDGE)
    assert r.status_code == 422 and detail(r)["code"] == "verdict_refused"
    b["verdict"] = gates.NOT_PROVEN
    r = h.post("/lab/finalize", b, H_JUDGE)
    assert r.status_code == 200 and r.json()["verdict"] == gates.NOT_PROVEN


# ── 7. The production store class behind the routes and the job ────────────
class FakeVolume:
    def __init__(self):
        self.commits = 0
        self.reloads = 0

    def commit(self):
        self.commits += 1

    def reload(self):
        self.reloads += 1


class FakeDict:
    """modal.Dict 1.6 semantics used by ModalStore: put(..., skip_if_exists=True) -> created?"""

    def __init__(self):
        self.d: Dict[str, Any] = {}

    def put(self, key, value, skip_if_exists=False):
        if skip_if_exists and key in self.d:
            return False
        self.d[key] = value
        return True

    def get(self, key, default=None):
        return self.d.get(key, default)


def test_routes_and_job_run_on_modal_store(tmp_path):
    store_modal = pytest.importorskip("sajim_quant.lab.store_modal")
    vol, slots = FakeVolume(), FakeDict()
    st = store_modal.ModalStore(vol, slots, root=tmp_path / "results_volume", reload_min_interval_s=0)
    h = Harness(tmp_path, raw=full_raw(**PLANTED_DATA), store=st)
    builder = h.lab_client()
    chk = builder.wait(builder.check(body(kind="check"))["job_id"])
    assert chk["status"] == "done" and chk["result"]["passed"] is True
    started = builder.evaluate(body())
    job = builder.wait(started["job_id"])
    assert started["trial"] == 1 and job["status"] == "done" and "detail" not in job["result"]["scorecard"]
    assert list(slots.d) == [I.trial_key(LAB_ID, 1)]                 # the budget slot lives in the Dict
    assert st.read(f"lab/_bindings/{LAB_ID}.json") is not None and vol.commits > 0 and vol.reloads > 0
    assert [r["record_type"] for r in ledger.list_records(st, LAB_ID)] == ["check", "budget_claim", "dev_trial"]
    assert builder.family(LAB_ID)["n_trials"] == 1


# ── 8. The job body on its own ──────────────────────────────────────────────
def test_run_lab_job_reports_infrastructure_failure_and_records_it(tmp_path):
    store = ledger.LocalDirStore(tmp_path / "store")
    b = body()
    job = {"kind": I.DEV_RESULT_KIND, "lab_id": LAB_ID, "family": LAB_ID, "request_id": b["request_id"],
           "strategy_py": b["strategy_py"], "spec_md": b["spec_md"], "manifest": b["manifest"], "trial": 1,
           "holdout_touch": None}
    res = lab_api.run_lab_job(job, store=store, executor=InProcessExecutor(), loader=FakeLoader(None),
                              deploy_id="t", job_id="fc-abc")
    assert res["success"] is False and "FileNotFoundError" in res["error"]
    (rec,) = ledger.list_records(store, LAB_ID, record_type="dev_trial")
    assert rec["payload"]["status"] == "failed" and rec["payload"]["job_id"] == "fc-abc"
    assert ledger.dev_pass_record(store, LAB_ID, hashlib.sha256(PLANTED_SRC).hexdigest()) is None


# ── 9. Adversarial review: dedup races, job-memory poisoning, partial writes ─
def _reentrant_runner(h_ref: List[Harness], inner_body: Dict[str, Any], scope: str, out: Dict[str, Any]):
    """The first spawn re-enters the service with a second POST for the same request id (a concurrent call)."""
    def run(job, job_id):
        if "inner" not in out:
            out["inner"] = None
            svc = lab_api.LabService(h_ref[0].deps)
            try:
                out["inner"] = svc.evaluate(scope, json.dumps(inner_body).encode("utf-8"))
            except I.LabError as e:
                out["inner"] = e
        return "running", None, None
    return run


@pytest.mark.parametrize("stage", [splits.DEV, splits.HOLDOUT])
def test_concurrent_posts_of_one_request_never_spawn_twice(tmp_path, stage):
    # Two POSTs of the same derived request id racing between the claim and the job memory write (here: the
    # second arrives while the first is spawning) used to get the same trial/touch and BOTH spawned a job, so one
    # trial bought two evaluations -- with different manifests, since the request id ignores the manifest.
    ref: List[Harness] = []
    out: Dict[str, Any] = {}
    scope = "builder" if stage == splits.DEV else "judge"
    inner = body(stage=stage, max_hold_bars=48) if stage == splits.DEV else body(stage=stage)
    h = Harness(tmp_path, runner=_reentrant_runner(ref, inner, scope, out))
    ref.append(h)
    if stage == splits.HOLDOUT:
        seed_dev_pass(h.store, body())
    r = h.post("/lab/evaluate", body(stage=stage), H_LAB if scope == "builder" else H_JUDGE)
    assert r.status_code == 200, r.text
    assert len(h.spawned) == 1, [s[1]["manifest"]["max_hold_bars"] for s in h.spawned]
    assert isinstance(out["inner"], I.LabError) and out["inner"].status == 503   # "being started; retry"
    again = h.post("/lab/evaluate", inner, H_LAB if scope == "builder" else H_JUDGE).json()  # the client's retry
    assert again["job_id"] == r.json()["job_id"] and again["deduplicated"] is True and len(h.spawned) == 1


def test_a_builder_check_cannot_hijack_the_judges_request_memory(tmp_path):
    h = Harness(tmp_path, runner=_running)
    sha = seed_dev_pass(h.store, body())
    ho_rid = I.derive_request_id(LAB_ID, sha, "holdout")
    first = h.post("/lab/evaluate", body(stage=splits.HOLDOUT), H_JUDGE).json()
    # another sha's check that names the judge's holdout request id (not in ITS own derived set)
    src2 = PLANTED_SRC + b"\n# another revision\n"
    r = h.post("/lab/check", body(src2, kind="check", request_id=ho_rid), H_LAB)
    assert r.status_code == 400 and detail(r)["code"] == "malformed"
    assert h.kv[f"req-{ho_rid}"] == first["job_id"]
    # even with the request memory overwritten, the judge's retry never starts a second holdout
    h.kv[f"req-{ho_rid}"] = "fc-somethingelse1"
    again = h.post("/lab/evaluate", body(stage=splits.HOLDOUT), H_JUDGE).json()
    assert again["job_id"] == first["job_id"] and again["deduplicated"] is True
    assert [s[1]["kind"] for s in h.spawned] == [I.HOLDOUT_RESULT_KIND]


def test_a_dashboard_request_id_is_not_overwritten_by_a_check(tmp_path):
    h = Harness(tmp_path, runner=_running)
    b = body(kind="check")
    h.kv[f"req-{b['request_id']}"] = "fc-backtestjob1"
    r = h.post("/lab/check", b, H_LAB)
    assert r.status_code == 400 and h.kv[f"req-{b['request_id']}"] == "fc-backtestjob1" and h.spawned == []


def test_job_memory_read_failure_never_spawns_a_duplicate(tmp_path):
    h = Harness(tmp_path, runner=_running)
    first = h.post("/lab/evaluate", body(), H_LAB).json()

    def broken(key, default=None):
        raise ConnectionError("dict unavailable")
    h.deps.kv_get = broken
    r = h.post("/lab/evaluate", body(), H_LAB)
    assert r.status_code == 503 and len(h.spawned) == 1 and first["trial"] == 1


def _scorecard_written_then_failed(h_ref: List[Harness]):
    def run(job, job_id):
        h = h_ref[0]
        sha = hashlib.sha256(job["strategy_py"].encode()).hexdigest()
        h.store.write(f"lab/{job['family']}/{sha[:12]}/dev_scorecard.json",
                      json.dumps({"stage": "dev", "code_sha256": sha, "verdict": gates.REJECT}).encode())
        return "failed", {"success": False, "error": "volume commit failed"}, "volume commit failed"
    return run


def test_a_job_that_failed_after_writing_its_scorecard_is_not_rerun(tmp_path):
    # The builder can read that scorecard; a respawn (with a new manifest) would be a second look for one trial.
    ref: List[Harness] = []
    h = Harness(tmp_path, runner=_scorecard_written_then_failed(ref))
    ref.append(h)
    first = h.post("/lab/evaluate", body(), H_LAB).json()
    again = h.post("/lab/evaluate", body(max_hold_bars=48), H_LAB).json()
    assert again["job_id"] == first["job_id"] and again["deduplicated"] is True and len(h.spawned) == 1


def test_run_lab_job_writes_no_scorecard_when_the_result_file_cannot_be_built(tmp_path, monkeypatch):
    from sajim_quant.lab import evaluate

    def boom(result):
        raise ValueError("Out of range float values are not JSON compliant")
    monkeypatch.setattr(evaluate, "result_payload", boom)
    store = ledger.LocalDirStore(tmp_path / "store")
    b = body()
    job = {"kind": I.DEV_RESULT_KIND, "lab_id": LAB_ID, "family": LAB_ID, "request_id": b["request_id"],
           "strategy_py": b["strategy_py"], "spec_md": b["spec_md"], "manifest": b["manifest"], "trial": 1,
           "holdout_touch": None}
    res = lab_api.run_lab_job(job, store=store, executor=InProcessExecutor(), loader=FakeLoader(full_raw(**PLANTED_DATA)),
                              deploy_id="t", job_id="fc-abc")
    assert res["success"] is False
    sha = hashlib.sha256(PLANTED_SRC).hexdigest()
    assert store.read(f"lab/{LAB_ID}/{sha[:12]}/dev_scorecard.json") is None


def test_modal_runner_wires_an_atomic_first_writer_put():
    src = MODAL_RUNNER.read_text(encoding="utf-8")
    assert "kv_put_new=lambda key, value: JOBS.put(key, value, skip_if_exists=True)" in src


def test_a_written_scorecard_blocks_a_rerun_even_after_the_job_memory_expired(tmp_path):
    ref: List[Harness] = []
    h = Harness(tmp_path, runner=_scorecard_written_then_failed(ref))
    ref.append(h)
    h.post("/lab/evaluate", body(), H_LAB)
    h.kv.clear()                                                      # the Dict's 7-day idle expiry
    r = h.post("/lab/evaluate", body(max_hold_bars=48), H_LAB)
    assert r.status_code == 409 and detail(r)["code"] == "already_evaluated" and len(h.spawned) == 1
    assert ledger.count_dev_trials(h.store, LAB_ID) == 1


def test_an_abandoned_spawn_lock_lets_the_next_attempt_start(tmp_path):
    h = Harness(tmp_path, runner=_running)
    b = body()
    sub_sha = hashlib.sha256(PLANTED_SRC).hexdigest()
    key = f"{lab_api.SPAWN_LOCK_PREFIX}{I.DEV_RESULT_KIND}-{LAB_ID}-{sub_sha}-{b['request_id']}-0"
    fresh = json.dumps({"state": "pending", "at": lab_api._utcnow().isoformat(), "nonce": "x"})
    h.kv[key] = fresh                                                  # another call is spawning right now
    r = h.post("/lab/evaluate", b, H_LAB)
    assert r.status_code == 503 and detail(r)["code"] == "unavailable" and h.spawned == []
    old = lab_api._utcnow() - lab_api.timedelta(seconds=lab_api.SPAWN_LOCK_STALE_S + 1)
    h.kv[key] = json.dumps({"state": "pending", "at": old.isoformat(), "nonce": "x"})   # that API call died
    r = h.post("/lab/evaluate", b, H_LAB)
    assert r.status_code == 200 and r.json()["trial"] == 1 and len(h.spawned) == 1
    assert json.loads(h.kv[key[:-1] + "1"])["state"] == "spawned"


class _NonExclusiveStore(ledger.LocalDirStore):
    """A volume seen from two API containers: write_once on the binding file is not exclusive across them."""

    def write_once(self, relpath: str, data: bytes) -> bool:
        if relpath.startswith(lab_api.BINDINGS_DIR + "/"):
            self.write(relpath, data)
            return True
        return super().write_once(relpath, data)


def test_family_binding_is_first_writer_wins_across_containers(tmp_path):
    # Two containers both see no binding file for a fresh lab_id and both "win" write_once: the lab_id would
    # end up with two families' DEV budgets. The atomic Dict claim decides instead.
    h = Harness(tmp_path, runner=_running, store=_NonExclusiveStore(tmp_path / "store"))
    assert h.post("/lab/evaluate", body(), H_LAB).status_code == 200
    h.store.write(f"{lab_api.BINDINGS_DIR}/{LAB_ID}.json", b"")            # the other container's view: no file
    h.store._path(f"{lab_api.BINDINGS_DIR}/{LAB_ID}.json").unlink()
    r = h.post("/lab/evaluate", body(PLANTED_SRC + b"\n# rev\n", family="lab_fresh_family"), H_LAB)
    assert r.status_code == 422 and detail(r)["code"] == "family_mismatch"
    assert ledger.count_dev_trials(h.store, "lab_fresh_family") == 0 and len(h.spawned) == 1


# ── 10. Adversarial review 2: one claim, one evaluated configuration ────────
def test_a_respawn_never_evaluates_a_different_manifest_under_one_claim(tmp_path):
    # GET /lab/job reports "failed" for any poll exception (a transient Modal client error too), so a job that is
    # still running -- or one that died without a ledger record -- looks respawnable. The respawn used to run the
    # NEW POST's manifest under the same trial: two DEV configurations (e.g. two max_hold_bars) for one trial.
    outcomes = iter([("failed", None, "ConnectionError: transient poll error"), ("running", None, None)])
    h = Harness(tmp_path, runner=lambda job, job_id: next(outcomes))
    first = h.post("/lab/evaluate", body(), H_LAB)
    assert first.status_code == 200 and first.json()["trial"] == 1
    changed = h.post("/lab/evaluate", body(max_hold_bars=48), H_LAB)
    assert changed.status_code == 409 and detail(changed)["code"] == "request_conflict", changed.text
    assert len(h.spawned) == 1 and ledger.count_dev_trials(h.store, LAB_ID) == 1
    # the same files again: an infrastructure retry under the same claim, same configuration
    same = h.post("/lab/evaluate", body(), H_LAB)
    assert same.status_code == 200 and same.json()["trial"] == 1 and len(h.spawned) == 2
    assert h.spawned[0][1]["manifest"] == h.spawned[1][1]["manifest"]


def test_a_stale_spawn_lock_from_another_manifest_blocks_a_changed_respawn(tmp_path):
    # The "spawned" write after a spawn can be lost (job memory writes are best effort); the stale "pending" lock
    # then opens the next attempt while the first job may still run. That attempt must use the same manifest.
    h = Harness(tmp_path, runner=_running)
    b = body()
    sha = hashlib.sha256(PLANTED_SRC).hexdigest()
    key = f"{lab_api.SPAWN_LOCK_PREFIX}{I.DEV_RESULT_KIND}-{LAB_ID}-{sha}-{b['request_id']}-0"
    old = lab_api._utcnow() - lab_api.timedelta(seconds=lab_api.SPAWN_LOCK_STALE_S + 1)
    h.kv[key] = json.dumps({"state": "pending", "at": old.isoformat(), "nonce": "x",
                            "manifest_sha256": lab_api.manifest_digest(b["manifest"])})
    r = h.post("/lab/evaluate", body(max_hold_bars=48), H_LAB)
    assert r.status_code == 409 and detail(r)["code"] == "request_conflict" and h.spawned == []
    r = h.post("/lab/evaluate", b, H_LAB)
    assert r.status_code == 200 and len(h.spawned) == 1


def test_every_spawn_lock_records_the_manifest_it_started(tmp_path):
    calls = {"n": 0}

    def flaky(job, job_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("modal unavailable")
        return "running", None, None

    h = Harness(tmp_path, runner=flaky)
    b = body()
    assert h.post("/lab/evaluate", b, H_LAB).status_code == 503
    assert h.post("/lab/evaluate", body(max_hold_bars=48), H_LAB).status_code == 409   # spawn_failed keeps it too
    assert h.post("/lab/evaluate", b, H_LAB).status_code == 200
    locks = [json.loads(v) for k, v in h.kv.items() if k.startswith(lab_api.SPAWN_LOCK_PREFIX)]
    assert {lk["state"] for lk in locks} == {"spawn_failed", "spawned"}
    assert {lk["manifest_sha256"] for lk in locks} == {lab_api.manifest_digest(b["manifest"])}


def test_a_request_that_failed_every_attempt_is_refused_not_retried(tmp_path):
    # After MAX_SPAWN_ATTEMPTS infrastructure failures the server never starts it again; a 503 ("retry") would
    # send lab clients into pointless retries and report a network problem instead of a refusal.
    h = Harness(tmp_path, runner=lambda job, job_id: ("failed", {"success": False, "error": "infra"}, "infra"))
    b = body()
    for _ in range(lab_api.MAX_SPAWN_ATTEMPTS):
        assert h.post("/lab/evaluate", b, H_LAB).status_code == 200
    r = h.post("/lab/evaluate", b, H_LAB)
    assert r.status_code == 409 and detail(r)["code"] == "attempts_exhausted"
    assert len(h.spawned) == lab_api.MAX_SPAWN_ATTEMPTS and ledger.count_dev_trials(h.store, LAB_ID) == 1


def test_lab_job_returns_its_result_when_the_final_commit_fails(monkeypatch):
    # ModalStore commits after every write, so lab_job's closing commit is a courtesy; when it raised (any modal
    # client error, not only RuntimeError) it replaced a finished job's result with an exception ("failed").
    mr = pytest.importorskip("sajim_quant.cloud.modal_runner")
    import modal

    class FlakyVolume:
        def reload(self):
            raise RuntimeError("open files")

        def commit(self):
            raise ConnectionError("commit response lost")

    seen: Dict[str, Any] = {}

    def fake_run(job, **kw):
        seen.update(kw)
        return {"success": True, "kind": I.CHECK_RESULT_KIND}

    monkeypatch.setattr(mr, "RESULTS_VOLUME", FlakyVolume())
    monkeypatch.setattr(mr, "_lab_store", lambda: "store")
    monkeypatch.setattr(mr, "_lab_executor", lambda: "executor")
    monkeypatch.setattr(lab_api, "run_lab_job", fake_run)
    monkeypatch.setattr(modal, "current_function_call_id", lambda: "fc-abc123")
    assert mr.lab_job.local({"kind": I.CHECK_RESULT_KIND}) == {"success": True, "kind": I.CHECK_RESULT_KIND}
    assert seen["store"] == "store" and seen["executor"] == "executor" and seen["job_id"] == "fc-abc123"
    assert str(seen["loader"].parquet_root).replace("\\", "/").endswith(mr.DATA_MOUNT)
