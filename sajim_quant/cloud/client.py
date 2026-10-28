"""
HTTP client for the deployed SAJIM Modal API (see modal_runner.py).

Jobs run detached on Modal; this only makes short, retried HTTP calls (start + poll),
so it survives a flaky connection. Results are written to the same local paths the
dashboard reads (data/results, sim/public/test_results, data/noise/diagnose).

  python -m sajim_quant.cloud.client backtest --strategy beep_vacuum_hunter --symbol XAUUSD --tf M5
  python -m sajim_quant.cloud.client noise --result data/results/beep_vacuum_hunter_XAUUSD_M5_result.json
  python -m sajim_quant.cloud.client job <job_id>
  python -m sajim_quant.cloud.client results | jobs | health

Config: SAJIM_MODAL_API_URL and SAJIM_API_TOKEN from the environment, else sim/.env.local.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import uuid
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = REPO_DIR / "sim" / ".env.local"
SAFE_FILE = re.compile(r"^[A-Za-z0-9_.-]{1,160}\.json$")
RESULT_FILE = re.compile(r"^[A-Za-z0-9_-]{1,160}_result\.json$")
RETRYABLE_STATUS = {429, 502, 503, 504}
TERMINAL = ("done", "failed", "expired")


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0, code: str = "http"):
        super().__init__(message)
        self.status = status
        self.code = code  # http | network | timeout | not_configured


def _config() -> tuple[str, str]:
    url, token = os.environ.get("SAJIM_MODAL_API_URL", ""), os.environ.get("SAJIM_API_TOKEN", "")
    if (not url or not token) and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k.strip() == "SAJIM_MODAL_API_URL" and not url:
                url = v
            elif k.strip() == "SAJIM_API_TOKEN" and not token:
                token = v
    if not url or not token:
        raise ApiError("SAJIM_MODAL_API_URL / SAJIM_API_TOKEN not set (environment or sim/.env.local). "
                       "Deploy with `modal deploy sajim_quant/cloud/modal_runner.py` first.", code="not_configured")
    return url.rstrip("/"), token


def request(method: str, path: str, body: Any = None, *, timeout: float = 60, retries: int = 4) -> Any:
    """JSON request with retries on network errors and 429/502/503/504."""
    url, token = _config()
    data = json.dumps(body, default=str).encode("utf-8") if body is not None else None
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Sajim-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                msg = parsed.get("detail") or parsed.get("error") or raw
            except ValueError:
                msg = raw or f"HTTP {e.code}"
            err = ApiError(f"HTTP {e.code}: {msg}", e.code, "retryable" if e.code in RETRYABLE_STATUS else "http")
            if err.code == "http" or attempt == retries:
                raise err from None
            last = err
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                http.client.HTTPException, ValueError) as e:
            last = ApiError(f"could not reach the Modal API: {e}", 0, "network")
            if attempt == retries:
                break
        time.sleep(min(30, 2 ** attempt))
    raise last or ApiError("request failed", 0, "network")


def start_backtest(strategy_id: str, symbol: str, timeframe: str, initial_balance: float = 10000.0) -> dict:
    return request("POST", "/backtest", {"strategy_id": strategy_id, "symbol": symbol, "timeframe": timeframe,
                                          "initial_balance": initial_balance, "request_id": uuid.uuid4().hex})


def start_noise(file: str, result: dict | None = None, symbol: str | None = None) -> dict:
    body: dict[str, Any] = {"file": file, "request_id": uuid.uuid4().hex}
    if result is not None:
        body["result"] = result
    if symbol:
        body["symbol"] = symbol
    return request("POST", "/noise", body, timeout=180)


def get_job(job_id: str) -> dict:
    return request("GET", f"/job/{job_id}", timeout=120)


def wait_job(job_id: str, *, timeout: float = 900, interval: float = 3,
             on_change: Callable[[str], None] | None = None) -> dict:
    """Polls until the job is done/failed/expired. Network blips are tolerated until `timeout`."""
    t0 = time.time()
    last_status = None
    while True:
        try:
            job = get_job(job_id)
        except ApiError as e:
            if e.code == "http":
                raise
            job = {"status": "unreachable", "error": str(e)}
        status = job.get("status")
        if status != last_status and on_change:
            on_change(status or "?")
            last_status = status
        if status in TERMINAL:
            return job
        if time.time() - t0 > timeout:
            raise ApiError(f"job {job_id} still {status} after {int(timeout)}s; check later with "
                           f"`python -m sajim_quant.cloud.client job {job_id}`", 0, "timeout")
        time.sleep(interval)


# ------------------------------------------------------------------ local persistence
def save_result(file: str, data: dict) -> Path:
    if not SAFE_FILE.match(file):
        raise ApiError(f"refusing to write suspicious file name {file!r}")
    out = REPO_DIR / "data" / "results" / file
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, default=str)
    out.write_text(text, encoding="utf-8")
    public = REPO_DIR / "sim" / "public" / "test_results"
    if public.parent.exists():
        public.mkdir(parents=True, exist_ok=True)
        (public / file).write_text(text, encoding="utf-8")
    return out


def save_noise(res: dict, result_file: str | None = None) -> Path:
    """Writes <stem>_noise_enriched.json + <stem>_noise_report.md, names derived from the result file."""
    file = result_file or str(res.get("file", ""))
    if not RESULT_FILE.match(file):
        raise ApiError(f"refusing to write noise output for suspicious result name {file!r}")
    stem = file.removesuffix(".json").removesuffix("_result")
    out = REPO_DIR / "data" / "noise" / "diagnose"
    out.mkdir(parents=True, exist_ok=True)
    enriched = out / f"{stem}_noise_enriched.json"
    enriched.write_text(json.dumps(res["data"], indent=2, default=str), encoding="utf-8")
    (out / f"{stem}_noise_report.md").write_text(res.get("report_md", ""), encoding="utf-8")
    return enriched


def stale_deploy_warning(res: dict) -> str | None:
    """Warns when the cloud ran a different snapshot of strategies/ + sajim_quant/ than the working tree."""
    remote = res.get("deploy_id")
    if not remote or remote == "unknown":
        return None
    try:
        from sajim_quant.cloud.fingerprint import code_fingerprint
        local = code_fingerprint(REPO_DIR)
    except Exception:  # noqa: BLE001 — a warning must never break a run
        return None
    if local == remote:
        return None
    return (f"WARNING: the cloud ran deploy {remote} but your working tree is {local} — strategy or engine "
            f"code changed since the last deploy. Redeploy: python -X utf8 -m modal deploy sajim_quant/cloud/modal_runner.py")


# ----------------------------------------------------------------------------- CLI
def _say(msg: str) -> None:
    print(msg, flush=True)


def cmd_backtest(a: argparse.Namespace) -> int:
    started = start_backtest(a.strategy, a.symbol.upper(), a.tf.upper(), a.balance)
    _say(f"[DISPATCH] Modal job {started['job_id']}: {a.strategy} {a.symbol.upper()} {a.tf.upper()}")
    job = wait_job(started["job_id"], timeout=a.timeout, on_change=lambda s: _say(f"  status: {s}"))
    res = job.get("result") or {}
    _say(f"[OK] Cloud Execution Response: success={job['status'] == 'done'}")
    if job["status"] != "done":
        _say(f"  Error: {job.get('error') or res.get('error')}")
        if res.get("logs"):
            _say(res["logs"])
        return 1
    data = res["data"]
    path = save_result(res.get("file") or started["file"], data)
    warning = stale_deploy_warning(res)
    if warning:
        _say(f"  {warning}")
    _say(f"  Execution Time: {res.get('execution_time_ms')}ms")
    _say(f"  Harvested Trades: {len(data.get('trades', []))}")
    _say(f"  Net Profit: ${data.get('metrics', {}).get('net_profit', 0) or 0:,.2f}")
    _say(f"  Cached Cloud Result: {path}")
    return 0


def cmd_noise(a: argparse.Namespace) -> int:
    path = Path(a.result)
    if not path.is_absolute():
        path = REPO_DIR / path
    payload = json.loads(path.read_text(encoding="utf-8"))
    started = start_noise(path.name, payload, a.symbol or None)
    _say(f"[DISPATCH] Modal noise job {started['job_id']}: {path.name}")
    job = wait_job(started["job_id"], timeout=a.timeout, on_change=lambda s: _say(f"  status: {s}"))
    res = job.get("result") or {}
    _say(f"[OK] Cloud Noise Response: success={job['status'] == 'done'}")
    if job["status"] != "done":
        _say(f"  Error: {job.get('error') or res.get('error')}")
        return 1
    out = save_noise(res, path.name)
    warning = stale_deploy_warning(res)
    if warning:
        _say(f"  {warning}")
    s = res["data"].get("noise_summary", {})
    _say(f"  Diagnosed: {s.get('trades_diagnosed')}/{s.get('trades_total')} trades")
    _say(f"  Wrote: {out}")
    return 0


def cmd_job(a: argparse.Namespace) -> int:
    job = get_job(a.job_id)
    res = job.pop("result", None)
    if res and a.save and job.get("status") == "done":
        kind = res.get("kind") or ("noise" if "enriched_file" in res else job.get("kind"))
        if kind == "noise":
            _say(f"  Wrote: {save_noise(res)}")
        elif res.get("data"):
            _say(f"  Wrote: {save_result(res.get('file') or job.get('file'), res['data'])}")
    if res:
        job["result_keys"] = sorted(res.keys())
    print(json.dumps(job, indent=2, default=str))
    return 0 if job.get("status") in ("done", "running", "queued") else 1


def cmd_simple(path: str) -> Callable[[argparse.Namespace], int]:
    def run(_: argparse.Namespace) -> int:
        print(json.dumps(request("GET", path), indent=2))
        return 0
    return run


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    ap = argparse.ArgumentParser(prog="sajim_quant.cloud.client", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="run a backtest on Modal and cache the result locally")
    b.add_argument("--strategy", required=True, help="catalog id, e.g. beep_vacuum_hunter")
    b.add_argument("--symbol", default="XAUUSD")
    b.add_argument("--tf", default="M5")
    b.add_argument("--balance", type=float, default=10000.0)
    b.add_argument("--timeout", type=float, default=900, help="seconds to wait for the job")
    b.set_defaults(fn=cmd_backtest)

    n = sub.add_parser("noise", help="noise-diagnose a local result JSON on Modal")
    n.add_argument("--result", required=True, help="path to a *_result.json")
    n.add_argument("--symbol", default="", help="override the noise-data symbol")
    n.add_argument("--timeout", type=float, default=900)
    n.set_defaults(fn=cmd_noise)

    j = sub.add_parser("job", help="show a job's status (add --save to store a finished result locally)")
    j.add_argument("job_id")
    j.add_argument("--save", action="store_true")
    j.set_defaults(fn=cmd_job)

    for name, path in (("results", "/results"), ("jobs", "/jobs"), ("health", "/health")):
        sub.add_parser(name).set_defaults(fn=cmd_simple(path))

    a = ap.parse_args()
    try:
        sys.exit(a.fn(a))
    except ApiError as e:
        sys.exit(f"error: {e}")
    except FileNotFoundError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
