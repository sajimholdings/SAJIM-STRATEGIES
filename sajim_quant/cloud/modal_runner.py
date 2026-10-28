"""
SAJIM Quant — Modal cloud execution engine
==========================================
One deployed Modal app ("sajim-quant-cloud") runs backtests and noise diagnoses in
serverless containers and exposes them over HTTP, so the dashboard (local or hosted)
never holds a long-lived connection open — a dropped laptop link no longer kills a run.

  POST /backtest   {strategy_id, symbol, timeframe, initial_balance?} -> {job_id, file}
  POST /noise      {file, result?, symbol?}                           -> {job_id, file}
  GET  /job/{id}   -> {status: running|done|failed|expired, result?, error?, ...}
  GET  /jobs       -> recent job ledger
  GET  /results    -> result files stored in the sajim-results volume
  GET  /results/{file}        full result JSON
  GET  /noise/{file}?summary=1  enriched noise JSON (or just its noise_summary)
  GET  /health     (no auth)
  /lab/*           Strategy Lab (sajim_quant/cloud/lab_api.py, INTERFACES.md §9): check / evaluate /
                   job / family / scorecard / finalize, run by the lab_job function

Every route except /health and /lab/* needs the header  X-Sajim-Token: <SAJIM_API_TOKEN>
(Modal secret "sajim-api"; the same value lives in sim/.env.local). /lab/* takes X-Lab-Token
(SAJIM_LAB_TOKEN, builder scope), X-Judge-Token (SAJIM_JUDGE_TOKEN, judge scope) or
X-Sajim-Token (read scope); the two lab keys are optional in the secret (503 until both are set).

Storage (Modal Volumes, sibling mount points):
  sajim-market-data -> /root/data/parquet/<SYMBOL>/<TF>.parquet   (DataLoader / sajim_quant.noise default)
  sajim-results     -> /results/results/*_result.json, /results/noise/diagnose/*

Deploy (and re-deploy after editing strategies/ or sajim_quant/ — mounts are snapshotted):
  modal deploy sajim_quant/cloud/modal_runner.py
Clients: sajim_quant/cloud/client.py (Python CLI) · sim/src/lib/modalApi.ts (dashboard)
Debug only — ephemeral app that keeps a connection open for the whole run:
  modal run sajim_quant/cloud/modal_runner.py::main --strategy smc_daemon_live --symbol XAUUSD --tf H1
"""

# No `from __future__ import annotations`: FastAPI must see real annotation objects for
# the closures inside api() (string annotations there resolve against module globals,
# where `Request` is not imported, and silently become query parameters).
import hmac
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import modal
import modal.exception

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sajim_quant.cloud.modal")

APP_NAME = "sajim-quant-cloud"
app = modal.App(APP_NAME)

REPO_DIR = Path(__file__).resolve().parent.parent.parent

# Fingerprint of the code snapshotted below (see fingerprint.py). Computed locally at
# deploy time, read back from the image env inside containers; returned with every job
# so callers can warn when the cloud runs older code than the working tree.
if modal.is_local():
    if str(REPO_DIR) not in sys.path:
        sys.path.insert(0, str(REPO_DIR))
    from sajim_quant.cloud.fingerprint import code_fingerprint
    DEPLOY_ID = code_fingerprint(REPO_DIR)
else:
    DEPLOY_ID = os.environ.get("SAJIM_DEPLOY_ID", "unknown")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "numpy>=1.26.0",
        "pandas>=2.2.0",
        "polars>=0.20.0",
        "pyarrow>=15.0.0",
        "backtrader>=1.9.78.123",
        "python-dotenv>=1.0.0",
        "scipy>=1.12.0",
        "yfinance>=0.2.40",   # BEEP's data_loader imports it at module level
        "fastapi[standard]",  # the HTTP API below; Modal serves the ASGI app itself
    )
    .env({"SAJIM_DEPLOY_ID": DEPLOY_ID})
    # add_local_dir layers must be the last build steps (copy=False mounts).
    .add_local_dir(
        REPO_DIR / "strategies" / "backtrader-pullback-window-xauusd",
        remote_path="/root/strategies/backtrader-pullback-window-xauusd",
    )
    .add_local_dir(REPO_DIR / "strategies" / "BEEP_MASTER_PACKAGE", remote_path="/root/strategies/BEEP_MASTER_PACKAGE")
    .add_local_dir(REPO_DIR / "strategies" / "smc_daemon_project", remote_path="/root/strategies/smc_daemon_project")
    .add_local_dir(REPO_DIR / "strategies" / "GoldOrb", remote_path="/root/strategies/GoldOrb")
    .add_local_dir(REPO_DIR / "sajim_quant", remote_path="/root/sajim_quant")
)

# Market data: sync after a local Dukascopy download (from PowerShell — Git Bash
# rewrites a leading "/" into a Windows path):
#   modal volume put --force sajim-market-data data/parquet /
DATA_VOLUME = modal.Volume.from_name("sajim-market-data")
VOLUME_MOUNT = "/root/data"
DATA_MOUNT = "/root/data/parquet"  # where DataLoader and sajim_quant.noise look by default

# Outputs. Volume paths are relative to the volume root (= RESULTS_MOUNT in containers).
RESULTS_VOLUME = modal.Volume.from_name("sajim-results", create_if_missing=True)
RESULTS_MOUNT = "/results"
RESULTS_DIR = f"{RESULTS_MOUNT}/results"
NOISE_DIR = f"{RESULTS_MOUNT}/noise/diagnose"

JOBS = modal.Dict.from_name("sajim-jobs", create_if_missing=True)  # job ledger; entries expire after 7 idle days
# Only SAJIM_API_TOKEN is required, so a deploy before the lab keys exist keeps the old routes up.
# The same secret may also hold SAJIM_LAB_TOKEN / SAJIM_JUDGE_TOKEN; /lab/* answers 503 until both
# are set (and differ). The secret is attached to api() only — never to lab_job or a sandbox.
API_SECRET = modal.Secret.from_name("sajim-api", required_keys=["SAJIM_API_TOKEN"])

# Strategy Lab (sajim_quant/lab/INTERFACES.md §2.4, §7, §9). Budget slots live in their own Dict;
# the ledger, snapshots and scorecards live under /results/lab on the results volume. Untrusted
# strategy code runs only in a fresh no-network modal.Sandbox on sandbox_modal.sandbox_image()
# (pinned numpy/pandas/pyarrow, no modal, no sajim_quant, no secrets/volumes) — see _lab_executor.
LAB_DICT = modal.Dict.from_name("sajim-lab", create_if_missing=True)

# Strategies deployed in the image. MT5 EAs need the Windows terminal and are not runnable here.
STRATEGY_FILES = {
    "sunrise_ogle_xauusd": Path("/root/strategies/backtrader-pullback-window-xauusd/src/strategy/sunrise_ogle_xauusd.py"),
    "beep_vacuum_hunter": Path("/root/strategies/BEEP_MASTER_PACKAGE/src/beep_engine.py"),
    "beep_baseline_follower": Path("/root/strategies/BEEP_MASTER_PACKAGE/src/beep_engine.py"),
    "smc_daemon_live": Path("/root/strategies/smc_daemon_project/smc_daemon.py"),
}
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9_]{2,12}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9_.-]{1,160}\.json$")
SAFE_JOB = re.compile(r"^fc-[A-Za-z0-9]{1,64}$")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_BODY_BYTES = 50_000_000  # largest legitimate body is a ~2 MB result upload on POST /noise


def result_file_name(strategy_id: str, symbol: str, timeframe: str) -> str:
    return f"{strategy_id}_{symbol}_{timeframe}_result.json"


def _json_safe(obj: Any) -> Any:
    """NaN/Infinity are not JSON; JSON.parse in the dashboard would throw on them."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ledger_update(job_id: str, **fields: Any) -> None:
    """Best-effort job ledger; never let bookkeeping break a request."""
    try:
        entry = JOBS.get(job_id) or {}
        entry.update(fields)
        JOBS.put(job_id, entry)
    except Exception as e:  # noqa: BLE001
        logger.warning("job ledger update failed for %s: %s", job_id, e)


# ----------------------------------------------------------------------------- jobs
@app.function(
    image=image,
    volumes={VOLUME_MOUNT: DATA_VOLUME, RESULTS_MOUNT: RESULTS_VOLUME},
    timeout=600,
    cpu=4.0,
    memory=8192,
)
def run_cloud_strategy(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs one strategy backtest against the parquet volume, stores the normalized
    result JSON in the results volume and returns it.
    """
    sys.path.insert(0, "/root")

    strategy_id = job.get("strategy_id", "sunrise_ogle_xauusd")
    symbol = str(job.get("symbol", "XAUUSD")).upper()
    timeframe = str(job.get("timeframe", "M5")).upper()
    initial_balance = float(job.get("initial_balance", 10000.0))
    file_name = result_file_name(strategy_id, symbol, timeframe)
    t0 = time.time()

    strat_path = STRATEGY_FILES.get(strategy_id)
    if strat_path is None:
        return {
            "success": False,
            "provider": "modal_cloud",
            "kind": "backtest",
            "deploy_id": DEPLOY_ID,
            "file": file_name,
            "error": f"'{strategy_id}' cannot run on Modal (deployed: {', '.join(STRATEGY_FILES)}). "
                     f"MT5 strategies need a Windows host.",
            "logs": "",
        }

    try:
        from sajim_quant.analytics import calculate_metrics

        if strategy_id == "sunrise_ogle_xauusd" or "backtrader" in strategy_id:
            from sajim_quant.__main__ import _load_backtrader_class
            from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
            adapter = BacktraderAdapter(strategy_class=_load_backtrader_class(strat_path), strategy_name=strategy_id)
        elif "vacuum" in strategy_id:
            from sajim_quant.adapters.python.beep_adapter import BEEPVacuumAdapter
            adapter = BEEPVacuumAdapter(strategy_name=strategy_id)
        elif "baseline" in strategy_id or "beep" in strategy_id:
            from sajim_quant.adapters.python.beep_adapter import BEEPBaselineAdapter
            adapter = BEEPBaselineAdapter(strategy_name=strategy_id)
        elif "smc" in strategy_id:
            from sajim_quant.adapters.python.smc_adapter import SMCAdapter
            adapter = SMCAdapter(strategy_name=strategy_id)
        else:
            from sajim_quant.__main__ import _load_backtrader_class
            from sajim_quant.adapters.python.backtrader_adapter import BacktraderAdapter
            adapter = BacktraderAdapter(strategy_class=_load_backtrader_class(strat_path), strategy_name=strategy_id)

        result = adapter.run(symbol=symbol, timeframe=timeframe, initial_balance=initial_balance)
        elapsed = time.time() - t0
        metrics = calculate_metrics(result) if result.total_trades > 0 else None

        # Canonical BacktestResultJSON (metadata / metrics / trades) the dashboard reads as-is.
        payload = _json_safe({
            "metadata": result.metadata.to_dict(),
            "total_trades": result.total_trades,
            "metrics": (
                {k: v for k, v in metrics.__dict__.items() if k not in ("equity_series", "monthly_returns")}
                if metrics is not None else {}
            ),
            "trades": [t.to_dict() for t in result.trades],
        })

        _write_json(f"{RESULTS_DIR}/{file_name}", payload)
        RESULTS_VOLUME.commit()  # make it visible to the API container and other jobs now

        return {
            "success": True,
            "provider": "modal_cloud",
            "kind": "backtest",
            "deploy_id": DEPLOY_ID,
            "file": file_name,
            "execution_time_ms": int(elapsed * 1000),
            "cloud_container_id": os.environ.get("MODAL_CONTAINER_ID", "modal-serverless-worker"),
            "data": payload,
            "logs": f"[Modal Cloud Worker]: {strategy_id} {symbol} {timeframe} -> {len(result.trades)} trades "
                    f"in {elapsed:.2f}s (initial balance {initial_balance:,.0f}); stored {file_name}",
        }
    except Exception as e:  # noqa: BLE001
        import traceback
        return {
            "success": False,
            "provider": "modal_cloud",
            "kind": "backtest",
            "deploy_id": DEPLOY_ID,
            "file": file_name,
            "error": str(e),
            "logs": f"[Modal Cloud Error]: {e}\n{traceback.format_exc()}",
        }


@app.function(
    image=image,
    volumes={VOLUME_MOUNT: DATA_VOLUME, RESULTS_MOUNT: RESULTS_VOLUME},
    timeout=600,
    cpu=2.0,
    memory=4096,
)
def run_noise_diagnosis(file: str, symbol: str | None = None) -> Dict[str, Any]:
    """
    Tags every trade of <results volume>/results/<file> with the noise state at entry
    (sajim_quant.noise, HAR-RV on the volume's M5 data) and stores the enriched JSON +
    markdown report under noise/diagnose/.
    """
    sys.path.insert(0, "/root")
    stem = file.removesuffix(".json").removesuffix("_result")
    try:
        from sajim_quant.noise.diagnose import diagnose_result, render_markdown

        try:
            RESULTS_VOLUME.reload()  # see results committed by other containers
        except RuntimeError as e:
            logger.warning("results volume reload skipped: %s", e)
        src = f"{RESULTS_DIR}/{file}"
        if not os.path.exists(src):
            return {"success": False, "kind": "noise", "deploy_id": DEPLOY_ID, "file": file, "error": f"{file} is not in the results volume"}
        with open(src, encoding="utf-8") as f:
            result = json.load(f)

        enriched, summary = diagnose_result(result, symbol=symbol, data_root=DATA_MOUNT)
        enriched["noise_summary"] = summary
        enriched = _json_safe(enriched)
        report_md = render_markdown(summary)

        enriched_file = f"{stem}_noise_enriched.json"
        report_file = f"{stem}_noise_report.md"
        _write_json(f"{NOISE_DIR}/{enriched_file}", enriched)
        with open(f"{NOISE_DIR}/{report_file}", "w", encoding="utf-8") as f:
            f.write(report_md)
        RESULTS_VOLUME.commit()

        return {
            "success": True,
            "kind": "noise",
            "deploy_id": DEPLOY_ID,
            "file": file,
            "enriched_file": enriched_file,
            "report_file": report_file,
            "data": enriched,
            "report_md": report_md,
            "logs": f"[Modal Noise Worker]: {summary.get('trades_diagnosed')}/{summary.get('trades_total')} "
                    f"trades diagnosed for {file}",
        }
    except FileNotFoundError as e:  # e.g. "No M5 data at ... Available symbols: ..."
        return {"success": False, "kind": "noise", "deploy_id": DEPLOY_ID, "file": file, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"success": False, "kind": "noise", "deploy_id": DEPLOY_ID, "file": file, "error": str(e), "logs": traceback.format_exc()}


# ------------------------------------------------------------------------ strategy lab
# The two factories below are the only places that construct the lab's Modal-backed classes.
def _lab_store():
    """LedgerStore on the results volume (root /results), budget slots in the sajim-lab Dict."""
    from sajim_quant.lab.store_modal import ModalStore
    return ModalStore(RESULTS_VOLUME, LAB_DICT, root=RESULTS_MOUNT)


def _lab_executor():
    """A fresh no-network modal.Sandbox per sandbox job, on the pinned sandbox image (INTERFACES §2.4)."""
    from sajim_quant.lab.sandbox_modal import ModalSandboxExecutor, sandbox_image
    return ModalSandboxExecutor(app, sandbox_image())


@app.function(
    image=image,
    volumes={VOLUME_MOUNT: DATA_VOLUME, RESULTS_MOUNT: RESULTS_VOLUME},
    timeout=1800,  # DEV = two sandbox jobs of <= 300 s each + data load + simulator-only robustness
    cpu=2.0,
    memory=8192,
)
def lab_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    One Strategy Lab check / DEV / holdout evaluation (trusted side; INTERFACES §8). Holds NO
    API secret. The submitted strategy never runs here: evaluate.* hands it to a fresh
    modal.Sandbox and decodes the sparse events with sandbox_io's strict decoder. Writes the
    DEV-time code snapshot, the scorecard, results/<result_file> and the §7.2 ledger record.
    """
    sys.path.insert(0, "/root")
    from sajim_quant.cloud.lab_api import run_lab_job
    from sajim_quant.data.loader import DataLoader

    try:
        RESULTS_VOLUME.reload()  # see the claims and DEV scorecards the API committed
    except Exception as e:  # noqa: BLE001 — ModalStore reloads again on a read miss
        logger.warning("results volume reload skipped: %s: %s", type(e).__name__, e)
    try:
        return run_lab_job(job, store=_lab_store(), executor=_lab_executor(),
                           loader=DataLoader(parquet_root=DATA_MOUNT), deploy_id=DEPLOY_ID,
                           job_id=modal.current_function_call_id())
    finally:
        # Best effort: ModalStore already committed after every write. An exception raised here would
        # replace the finished job's result (status "failed" for a job that ran).
        try:
            RESULTS_VOLUME.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("results volume commit after lab job: %s: %s", type(e).__name__, e)


# ------------------------------------------------------------------------------ API
@app.function(
    image=image,
    volumes={RESULTS_MOUNT: RESULTS_VOLUME},
    secrets=[API_SECRET],
    scaledown_window=300,  # stay warm between the dashboard's 3-second polls
    timeout=120,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="sajim-api")
def api():
    return build_api_app()


def build_api_app():
    """The FastAPI app behind api(). A plain function so tests can build it without Modal."""
    if "/root" not in sys.path and not modal.is_local():
        sys.path.insert(0, "/root")
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, Response

    from sajim_quant.cloud import lab_api

    def _token_ok(request: Request) -> bool:
        expected = os.environ.get("SAJIM_API_TOKEN", "")
        got = request.headers.get("x-sajim-token", "")
        if not got:
            bearer = request.headers.get("authorization", "")
            got = bearer[7:] if bearer.lower().startswith("bearer ") else ""
        # Compare bytes: compare_digest raises TypeError on non-ASCII str (headers decode as latin-1).
        return bool(expected and got) and hmac.compare_digest(got.encode("latin-1", "replace"),
                                                              expected.encode("utf-8"))

    def _auth(request: Request) -> None:  # defence in depth; the middleware below runs first
        if not _token_ok(request):
            raise HTTPException(status_code=401, detail="missing or invalid X-Sajim-Token header")

    def _dedup(request_id: Any) -> tuple[str | None, str | None]:
        """(validated request id, job id already started for it) — makes retried POSTs idempotent."""
        if request_id in (None, ""):
            return None, None
        rid = str(request_id)
        if not SAFE_REQUEST_ID.fullmatch(rid):
            raise HTTPException(400, "request_id must be 8-64 chars of [A-Za-z0-9_-]")
        try:
            return rid, JOBS.get(f"req-{rid}")
        except Exception as e:  # noqa: BLE001
            logger.warning("request-id lookup failed for %s: %s", rid, e)
            return rid, None

    def _remember(rid: str | None, job_id: str) -> None:
        if rid:
            try:
                JOBS.put(f"req-{rid}", job_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("request-id store failed for %s: %s", rid, e)

    def _reload_results() -> None:
        try:
            RESULTS_VOLUME.reload()
        except RuntimeError as e:  # another request may hold a file open; serve the current view
            logger.warning("results volume reload skipped: %s", e)

    def _catalog() -> Dict[str, Dict[str, Any]]:
        try:
            with open("/root/sajim_quant/core/strategy_catalog.json", encoding="utf-8") as f:
                return {c["id"]: c for c in json.load(f)}
        except Exception as e:  # noqa: BLE001
            logger.warning("catalog unavailable: %s", e)
            return {}

    def _poll(job_id: str) -> tuple[str, Any, str | None]:
        """One non-blocking status check of a spawned job: (status, result, error)."""
        fc = modal.FunctionCall.from_id(job_id)
        try:
            res = fc.get(timeout=0)
        except TimeoutError:  # builtin TimeoutError == not finished yet
            return "running", None, None
        except modal.exception.OutputExpiredError:
            return "expired", None, "result expired or unknown job id"
        except modal.exception.NotFoundError:
            return "expired", None, f"unknown job id {job_id}"
        except modal.exception.FunctionTimeoutError as e:
            return "failed", None, f"job exceeded its time limit: {e}"
        except Exception as e:  # noqa: BLE001 — the job raised, or a Modal internal failure
            return "failed", None, f"{type(e).__name__}: {e}"
        if isinstance(res, dict) and res.get("success"):
            return "done", res, None
        error = res.get("error", "job reported failure") if isinstance(res, dict) else "job returned no payload"
        return "failed", res, error

    web = FastAPI(title="SAJIM Quant cloud API", docs_url=None, redoc_url=None, openapi_url=None)

    # Strategy Lab routes (/lab/*): their own tokens and scopes (INTERFACES §9), see lab_api.py.
    lab_deps = lab_api.LabDeps(
        store=_lab_store,
        spawn=lambda job: lab_job.spawn(job).object_id,
        poll=_poll,
        kv_get=JOBS.get,
        kv_put=JOBS.put,
        # Atomic first-writer-wins (modal 1.6: Dict.put(..., skip_if_exists=True) -> created): the spawn locks
        # that keep two concurrent POSTs of one request from starting two jobs under one claim.
        kv_put_new=lambda key, value: JOBS.put(key, value, skip_if_exists=True),
        read_token_ok=_token_ok,
        environ=os.environ,
        deploy_id=DEPLOY_ID,  # volume visibility: ModalStore.refresh() (throttled reload) before reads
    )

    @web.middleware("http")
    async def _guard(request: Request, call_next):
        # Reject before routing: FastAPI reads and parses a route's JSON body before
        # running its dependencies, so Depends(_auth) alone lets anyone make us buffer
        # arbitrary bodies without a token.
        if request.url.path != "/health":
            if lab_api.is_lab_path(request.url.path):
                # /lab/* never needs X-Sajim-Token (the builder must not hold it): 503 until the
                # lab tokens are configured, 413 over LAB_MAX_BODY_BYTES; each route then checks
                # X-Lab-Token / X-Judge-Token / X-Sajim-Token and answers 401 when none is valid.
                refusal = lab_api.guard_response(request, lab_deps)
                if refusal is not None:
                    return refusal
                return await call_next(request)
            if not _token_ok(request):
                return JSONResponse({"detail": "missing or invalid X-Sajim-Token header"}, status_code=401)
            length = request.headers.get("content-length", "")
            if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
                return JSONResponse({"detail": f"request body over {MAX_BODY_BYTES} bytes"}, status_code=413)
        return await call_next(request)

    @web.get("/health")
    def health():
        return {"ok": True, "app": APP_NAME, "deploy_id": DEPLOY_ID, "modal": modal.__version__, "time": _now()}

    @web.post("/backtest", dependencies=[Depends(_auth)])
    def backtest(payload: dict):
        strategy_id = str(payload.get("strategy_id", "")).strip()
        symbol = str(payload.get("symbol", "XAUUSD")).strip().upper()
        timeframe = str(payload.get("timeframe", "M5")).strip().upper()
        try:
            initial_balance = float(payload.get("initial_balance", 10000.0))
        except (TypeError, ValueError):
            raise HTTPException(400, "initial_balance must be a number")
        if not SAFE_ID.fullmatch(strategy_id):
            raise HTTPException(400, "invalid strategy_id")
        if not SAFE_SYMBOL.fullmatch(symbol):
            raise HTTPException(400, "invalid symbol")
        if timeframe not in TIMEFRAMES:
            raise HTTPException(400, f"timeframe must be one of {', '.join(TIMEFRAMES)}")
        if not (0 < initial_balance <= 1e8):
            raise HTTPException(400, "initial_balance must be in (0, 100000000]")

        catalog = _catalog()
        entry = catalog.get(strategy_id)
        if entry is not None and entry.get("run_adapter") == "mt5":
            raise HTTPException(422, f"{strategy_id} is an MT5 Expert Advisor — it needs the Windows "
                                     f"MetaTrader terminal and cannot run on Modal.")
        if strategy_id not in STRATEGY_FILES:
            raise HTTPException(404, f"'{strategy_id}' is not deployed on Modal. Runnable: {', '.join(STRATEGY_FILES)}")

        file = result_file_name(strategy_id, symbol, timeframe)
        rid, existing = _dedup(payload.get("request_id"))
        if existing:  # a retried POST whose first response was lost
            return {"job_id": existing, "status": "queued", "file": file, "deduplicated": True}

        job = {"strategy_id": strategy_id, "symbol": symbol, "timeframe": timeframe, "initial_balance": initial_balance}
        fc = run_cloud_strategy.spawn(job)
        job_id = fc.object_id
        _remember(rid, job_id)
        _ledger_update(job_id, kind="backtest", status="queued", created_at=_now(), file=file,
                       deploy_id=DEPLOY_ID, **job)
        return {"job_id": job_id, "status": "queued", "file": file}

    @web.post("/noise", dependencies=[Depends(_auth)])
    def noise(payload: dict):
        file = str(payload.get("file", "")).strip()
        symbol = payload.get("symbol") or None
        if not SAFE_FILE.fullmatch(file):
            raise HTTPException(400, "file must be a bare *.json result file name")
        if symbol is not None and not SAFE_SYMBOL.fullmatch(str(symbol).upper()):
            raise HTTPException(400, "invalid symbol")

        result = payload.get("result")
        if result is not None:
            # Upload path: the caller's local result becomes the volume's copy.
            if not isinstance(result, dict) or not isinstance(result.get("trades"), list) \
                    or not isinstance(result.get("metadata"), dict):
                raise HTTPException(400, "result must be a BacktestResult JSON with metadata and trades")
            _write_json(f"{RESULTS_DIR}/{file}", result)
            try:
                RESULTS_VOLUME.commit()
            except RuntimeError as e:
                # commit() reloads afterwards, which fails while another request holds a volume
                # file open — the commit itself has usually landed. The job reloads and reports
                # clearly if the file really is missing.
                logger.warning("results volume commit/reload after upload: %s", e)
        else:
            _reload_results()
            if not os.path.exists(f"{RESULTS_DIR}/{file}"):
                raise HTTPException(404, f"{file} is not in the results volume; POST it in `result`")

        rid, existing = _dedup(payload.get("request_id"))
        if existing:
            return {"job_id": existing, "status": "queued", "file": file, "deduplicated": True}
        fc = run_noise_diagnosis.spawn(file, str(symbol).upper() if symbol else None)
        job_id = fc.object_id
        _remember(rid, job_id)
        _ledger_update(job_id, kind="noise", status="queued", created_at=_now(), file=file, deploy_id=DEPLOY_ID)
        return {"job_id": job_id, "status": "queued", "file": file}

    @web.get("/job/{job_id}", dependencies=[Depends(_auth)])
    def job_status(job_id: str):
        if not SAFE_JOB.fullmatch(job_id):
            raise HTTPException(400, "invalid job id")
        status, result, error = _poll(job_id)
        info: Dict[str, Any] = {}
        try:
            info = JOBS.get(job_id) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("job ledger read failed for %s: %s", job_id, e)
        if status == "expired" and not info:
            raise HTTPException(404, error)
        # Only jobs this API started get ledger updates (keeps made-up ids out of /jobs).
        if info and status in ("done", "failed", "expired") and info.get("status") != status:
            _ledger_update(job_id, status=status, finished_at=_now(), error=error)
        out: Dict[str, Any] = {"job_id": job_id, "status": status, **{k: v for k, v in info.items() if k != "status"}}
        if error:
            out["error"] = error
        if result is not None:
            out["result"] = result
            if isinstance(result, dict) and result.get("logs"):
                out["logs"] = result["logs"]
        return out

    @web.get("/jobs", dependencies=[Depends(_auth)])
    def jobs(limit: int = 50):
        items = []
        try:
            for k, v in JOBS.items():
                if isinstance(v, dict) and not str(k).startswith("req-"):
                    items.append({"job_id": k, **v})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"job ledger unavailable: {e}")
        items.sort(key=lambda j: str(j.get("created_at", "")), reverse=True)
        return {"jobs": items[: max(1, min(limit, 500))]}

    @web.get("/results", dependencies=[Depends(_auth)])
    def results():
        _reload_results()
        out = []
        if os.path.isdir(RESULTS_DIR):
            for name in sorted(os.listdir(RESULTS_DIR)):
                if name.endswith(".json"):
                    st = os.stat(f"{RESULTS_DIR}/{name}")
                    out.append({"file": name, "size": st.st_size,
                                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
        return {"results": out}

    @web.get("/results/{file}", dependencies=[Depends(_auth)])
    def result_file(file: str):
        if not SAFE_FILE.fullmatch(file):
            raise HTTPException(400, "invalid file name")
        _reload_results()
        path = f"{RESULTS_DIR}/{file}"
        if not os.path.exists(path):
            raise HTTPException(404, f"{file} not found")
        with open(path, "rb") as f:
            return Response(content=f.read(), media_type="application/json")

    @web.get("/noise/{file}", dependencies=[Depends(_auth)])
    def noise_file(file: str, summary: int = 0):
        if not SAFE_FILE.fullmatch(file):
            raise HTTPException(400, "invalid file name")
        _reload_results()
        stem = file.removesuffix(".json").removesuffix("_result")
        path = f"{NOISE_DIR}/{stem}_noise_enriched.json"
        if not os.path.exists(path):
            raise HTTPException(404, f"no noise diagnosis stored for {file}")
        if summary:
            with open(path, encoding="utf-8") as f:
                return {"file": file, "summary": json.load(f).get("noise_summary")}
        with open(path, "rb") as f:
            return Response(content=f.read(), media_type="application/json")

    lab_api.install(web, lab_deps)  # POST /lab/check|evaluate|finalize, GET /lab/job/{id}, /lab/{family}[...]
    return web


# ----------------------------------------------------------- debug entrypoints (ephemeral)
def _cache_result_locally(file_name: str, data: Dict[str, Any]) -> Path:
    results_dir = REPO_DIR / "data" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / file_name
    out_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    public_dir = REPO_DIR / "sim" / "public" / "test_results"  # static mirror the dashboard also reads
    if public_dir.parent.exists():
        public_dir.mkdir(parents=True, exist_ok=True)
        (public_dir / file_name).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return out_file


@app.local_entrypoint()
def main(strategy: str = "sunrise_ogle_xauusd", symbol: str = "XAUUSD", tf: str = "M5", balance: float = 10000.0):
    """Debug runner: executes in a fresh ephemeral app and caches the result locally."""
    print(f"[DISPATCH] Dispatching to Modal Cloud: {strategy} on {symbol} ({tf})...")
    res = run_cloud_strategy.remote({"strategy_id": strategy, "symbol": symbol, "timeframe": tf, "initial_balance": balance})
    print(f"[OK] Cloud Execution Response: success={res.get('success')}")
    if res.get("success"):
        data = res.get("data", {})
        print(f"  Container: {res.get('cloud_container_id')}")
        print(f"  Execution Time: {res.get('execution_time_ms')}ms")
        print(f"  Harvested Trades: {len(data.get('trades', []))}")
        print(f"  Net Profit: ${data.get('metrics', {}).get('net_profit', 0):,.2f}")
        print(f"  Cached Cloud Result: {_cache_result_locally(res['file'], data)}")
    else:
        print(f"  Error: {res.get('error')}")
        print(f"  Logs: {res.get('logs')}")


@app.local_entrypoint()
def noise(result: str, out_dir: str = "", symbol: str = ""):
    """
    Debug runner: modal run sajim_quant/cloud/modal_runner.py::noise --result data/results/X_result.json
    Uploads the file to the results volume, diagnoses it and writes the enriched JSON + report locally.
    """
    path = Path(result)
    print(f"[DISPATCH] Noise diagnosis on Modal: {path.name}")
    with RESULTS_VOLUME.batch_upload(force=True) as batch:
        batch.put_file(str(path), f"/results/{path.name}")
    res = run_noise_diagnosis.remote(path.name, symbol or None)
    print(f"[OK] Cloud Noise Response: success={res.get('success')}")
    if not res.get("success"):
        print(f"  Error: {res.get('error')}")
        if res.get("logs"):
            print(res["logs"])
        return
    out = Path(out_dir) if out_dir else REPO_DIR / "data" / "noise" / "diagnose"
    out.mkdir(parents=True, exist_ok=True)
    (out / res["enriched_file"]).write_text(json.dumps(res["data"], indent=2, default=str), encoding="utf-8")
    (out / res["report_file"]).write_text(res["report_md"], encoding="utf-8")
    print(f"  Wrote: {out / res['enriched_file']}")
