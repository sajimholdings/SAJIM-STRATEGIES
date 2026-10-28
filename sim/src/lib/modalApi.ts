/**
 * Server-only client for the deployed SAJIM Modal API
 * (sajim_quant/cloud/modal_runner.py, app "sajim-quant-cloud").
 *
 * Jobs run detached on Modal. This module only makes short HTTP calls — start a
 * job, poll it — each retried with backoff, so a flaky laptop connection can no
 * longer kill a backtest the way the old `modal run` subprocess did.
 *
 * Import ONLY from route handlers / server code — it reads env vars and uses node:fs.
 * Config: SAJIM_MODAL_API_URL + SAJIM_API_TOKEN in sim/.env.local.
 */

import crypto from "crypto";
import fs from "fs";
import path from "path";

export type JobStatus = "queued" | "running" | "done" | "failed" | "expired";

export interface BacktestJobResult {
  success: boolean;
  kind?: "backtest";
  deploy_id?: string;
  file?: string;
  data?: unknown; // BacktestResultJSON (metadata / metrics / trades)
  error?: string;
  logs?: string;
  execution_time_ms?: number;
}

export interface NoiseJobResult {
  success: boolean;
  kind?: "noise";
  deploy_id?: string;
  file?: string;
  enriched_file?: string;
  report_file?: string;
  data?: { noise_summary?: unknown; [k: string]: unknown };
  report_md?: string;
  error?: string;
  logs?: string;
}

export interface JobResponse<R = BacktestJobResult | NoiseJobResult> {
  job_id: string;
  status: JobStatus;
  kind?: "backtest" | "noise";
  file?: string;
  result?: R;
  error?: string;
  logs?: string;
  created_at?: string;
}

export type ModalApiErrorCode = "not_configured" | "network" | "timeout" | "http" | "retryable";

export class ModalApiError extends Error {
  constructor(
    message: string,
    public readonly code: ModalApiErrorCode,
    public readonly status = 0,
    public readonly body?: unknown
  ) {
    super(message);
    this.name = "ModalApiError";
  }
}

const RETRYABLE_STATUS = new Set([429, 502, 503, 504]);
const SAFE_FILE = /^[A-Za-z0-9_.-]{1,160}\.json$/;
const RESULT_FILE = /^[A-Za-z0-9_-]{1,160}_result\.json$/;
const SAFE_JOB = /^fc-[A-Za-z0-9]{1,64}$/;
const POLL_REQUEST_MS = 60_000; // one poll; a finished payload (thousands of trades) needs some room

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function modalApiConfig(): { url: string; token: string } | null {
  const url = (process.env.SAJIM_MODAL_API_URL ?? "").trim().replace(/\/+$/, "");
  const token = (process.env.SAJIM_API_TOKEN ?? "").trim();
  return url && token ? { url, token } : null;
}

export const NOT_CONFIGURED_MESSAGE =
  "Modal API not configured: set SAJIM_MODAL_API_URL and SAJIM_API_TOKEN in sim/.env.local " +
  "(deploy with `modal deploy sajim_quant/cloud/modal_runner.py`).";

export function isValidJobId(id: string): boolean {
  return SAFE_JOB.test(id);
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
  retries?: number;
}

/** JSON request with retries on network errors / fetch timeouts / 429 / 5xx gateway errors. */
export async function modalRequest<T>(apiPath: string, opts: RequestOptions = {}): Promise<T> {
  const cfg = modalApiConfig();
  if (!cfg) throw new ModalApiError(NOT_CONFIGURED_MESSAGE, "not_configured");
  const { method = "GET", body, timeoutMs = 30_000, retries = 4 } = opts;

  let last: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(cfg.url + apiPath, {
        method,
        headers: { "Content-Type": "application/json", "X-Sajim-Token": cfg.token },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
        cache: "no-store",
      });
      const text = await res.text();
      let parsed: unknown = null;
      try {
        parsed = text ? JSON.parse(text) : null;
      } catch {
        parsed = text;
      }
      if (res.ok) return parsed as T;

      const detail =
        parsed && typeof parsed === "object"
          ? (parsed as { detail?: unknown; error?: unknown })
          : null;
      const message =
        typeof detail?.detail === "string" ? detail.detail
        : typeof detail?.error === "string" ? detail.error
        : `HTTP ${res.status}`;
      const retryable = RETRYABLE_STATUS.has(res.status);
      const err = new ModalApiError(message, retryable ? "retryable" : "http", res.status, parsed);
      if (!retryable || attempt === retries) throw err;
      last = err;
    } catch (e) {
      if (e instanceof ModalApiError && e.code === "http") throw e; // 4xx: never retry
      last = e;
      if (attempt === retries) break;
    }
    await sleep(1000 * 2 ** attempt);
  }
  if (last instanceof ModalApiError) throw last;
  throw new ModalApiError(
    `Could not reach the Modal API after ${retries + 1} attempts: ${errMsg(last)}`,
    "network"
  );
}

// Every start carries a request_id: if a retry re-sends a POST whose first response was
// lost, the API returns the job it already started instead of spawning a duplicate.

export function startBacktest(job: {
  strategy_id: string;
  symbol: string;
  timeframe: string;
  initial_balance?: number;
}): Promise<{ job_id: string; status: JobStatus; file: string }> {
  return modalRequest("/backtest", {
    method: "POST",
    body: { ...job, request_id: crypto.randomUUID() },
    timeoutMs: 60_000,
  });
}

/** `result` uploads the caller's local result JSON so the cloud diagnoses exactly that file. */
export function startNoise(body: {
  file: string;
  result?: unknown;
  symbol?: string;
}): Promise<{ job_id: string; status: JobStatus; file: string }> {
  return modalRequest("/noise", {
    method: "POST",
    body: { ...body, request_id: crypto.randomUUID() },
    timeoutMs: 180_000,
  });
}

export async function getJob<R = BacktestJobResult | NoiseJobResult>(
  jobId: string,
  opts: { timeoutMs?: number; retries?: number } = {}
): Promise<JobResponse<R>> {
  if (!isValidJobId(jobId)) throw new ModalApiError("invalid job id", "http", 400);
  return modalRequest(`/job/${jobId}`, { timeoutMs: opts.timeoutMs ?? 120_000, retries: opts.retries });
}

/** Classify a finished job by its payload — the API's ledger `kind` is best-effort. */
export function isNoiseResult(r: BacktestJobResult | NoiseJobResult | undefined): r is NoiseJobResult {
  return !!r && (r.kind === "noise" || "enriched_file" in r);
}

/**
 * Polls until the job reaches a terminal state. Network blips are tolerated until
 * `timeoutMs`; a `timeout` ModalApiError then carries the job id so it can be
 * fetched later with GET /api/run-test?job=<id>.
 */
export async function waitForJob<R = BacktestJobResult | NoiseJobResult>(
  jobId: string,
  { timeoutMs = 270_000, intervalMs = 3_000 }: { timeoutMs?: number; intervalMs?: number } = {}
): Promise<JobResponse<R>> {
  const started = Date.now();
  let lastStatus = "";
  for (;;) {
    let job: JobResponse<R> | null = null;
    // One attempt per poll, bounded by the time left, so a black-holed connection cannot
    // hold this loop past its deadline (the loop itself is the retry).
    const remaining = Math.max(5_000, timeoutMs - (Date.now() - started));
    try {
      job = await getJob<R>(jobId, { timeoutMs: Math.min(POLL_REQUEST_MS, remaining), retries: 0 });
    } catch (e) {
      if (e instanceof ModalApiError && e.code === "http") throw e; // 404 expired, 401, ...
      lastStatus = `unreachable (${errMsg(e)})`;
    }
    if (job) {
      lastStatus = job.status;
      if (job.status === "done" || job.status === "failed" || job.status === "expired") return job;
    }
    if (Date.now() - started > timeoutMs) {
      throw new ModalApiError(
        `Modal job ${jobId} is still ${lastStatus || "running"} after ${Math.round(timeoutMs / 1000)}s`,
        "timeout"
      );
    }
    await sleep(intervalMs);
  }
}

// ----------------------------------------------------------------- local persistence
// The dashboard reads result files from <repo>/data/results (canonical) and the static
// mirror sim/public/test_results; noise diagnoses from <repo>/data/noise/diagnose.

const repoRoot = () => path.resolve(process.cwd(), "..");

export function saveResultLocally(file: string, data: unknown): { full: string; mirrored: string | null } {
  if (!SAFE_FILE.test(file)) throw new ModalApiError(`refusing to write suspicious file name ${file}`, "http", 400);
  const text = JSON.stringify(data, null, 2);
  const dir = path.resolve(repoRoot(), "data", "results");
  fs.mkdirSync(dir, { recursive: true });
  const full = path.join(dir, file);
  fs.writeFileSync(full, text, "utf-8");
  let mirrored: string | null = null;
  try {
    const publicDir = path.resolve(process.cwd(), "public", "test_results");
    fs.mkdirSync(publicDir, { recursive: true });
    mirrored = path.join(publicDir, file);
    fs.writeFileSync(mirrored, text, "utf-8");
  } catch {
    mirrored = null; // the mirror is a convenience; the canonical copy is what matters
  }
  return { full, mirrored };
}

/**
 * Writes <stem>_noise_enriched.json + <stem>_noise_report.md. Names are derived here from
 * the result file (as GET /api/noise expects), never taken from the cloud response.
 */
export function saveNoiseLocally(result: NoiseJobResult, resultFile?: string): { enriched: string; report: string } {
  const file = resultFile ?? result.file ?? "";
  if (!RESULT_FILE.test(file)) {
    throw new ModalApiError(`refusing to write noise output for result name ${file}`, "http", 400);
  }
  const stem = file.replace(/\.json$/, "").replace(/_result$/, "");
  const dir = path.resolve(repoRoot(), "data", "noise", "diagnose");
  fs.mkdirSync(dir, { recursive: true });
  const enriched = path.join(dir, `${stem}_noise_enriched.json`);
  const report = path.join(dir, `${stem}_noise_report.md`);
  fs.writeFileSync(enriched, JSON.stringify(result.data ?? {}, null, 2), "utf-8");
  fs.writeFileSync(report, result.report_md ?? "", "utf-8");
  return { enriched, report };
}

// ------------------------------------------------------------- stale-deploy detection
// Mirrors sajim_quant/cloud/fingerprint.py exactly — keep the two in sync.

const MOUNTED_DIRS = [
  "strategies/backtrader-pullback-window-xauusd",
  "strategies/BEEP_MASTER_PACKAGE",
  "strategies/smc_daemon_project",
  "strategies/GoldOrb",
  "sajim_quant",
];
const CODE_SUFFIXES = new Set([".py", ".json"]);

function collectCodeFiles(root: string, rel: string, out: string[]) {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(path.join(root, rel), { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    if (e.name.startsWith(".") || e.name === "__pycache__") continue;
    const childRel = `${rel}/${e.name}`;
    if (e.isDirectory()) collectCodeFiles(root, childRel, out);
    else if (e.isFile() && CODE_SUFFIXES.has(path.extname(e.name).toLowerCase())) out.push(childRel);
  }
}

/** Fingerprint of the code `modal deploy` snapshots, computed from the working tree. */
export function localCodeFingerprint(): string | null {
  try {
    const root = repoRoot();
    const files: string[] = [];
    for (const d of MOUNTED_DIRS) collectCodeFiles(root, d, files);
    files.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)); // code-point order, like Python's sort
    const h = crypto.createHash("sha256");
    for (const rel of files) {
      h.update(Buffer.from(`${rel}\0`, "utf-8"));
      h.update(fs.readFileSync(path.join(root, rel)));
      h.update(Buffer.from("\0"));
    }
    return h.digest("hex").slice(0, 12);
  } catch {
    return null;
  }
}

/** A warning when the cloud ran a different code snapshot than the working tree, else null. */
export function staleDeployWarning(result: { deploy_id?: string } | undefined): string | null {
  const remote = result?.deploy_id;
  if (!remote || remote === "unknown") return null;
  const local = localCodeFingerprint();
  if (!local || local === remote) return null;
  return (
    `The cloud ran deploy ${remote}, but your strategies/sajim_quant code is now ${local} — ` +
    `it changed since the last deploy, so this result reflects the OLD code. Redeploy with ` +
    `\`python -X utf8 -m modal deploy sajim_quant/cloud/modal_runner.py\` and run again.`
  );
}
