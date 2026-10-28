import { NextResponse } from "next/server";
import { execFile } from "child_process";
import fs from "fs";
import path from "path";
import util from "util";
import {
  ModalApiError,
  NOT_CONFIGURED_MESSAGE,
  getJob,
  isNoiseResult,
  isValidJobId,
  modalApiConfig,
  saveNoiseLocally,
  saveResultLocally,
  staleDeployWarning,
  startBacktest,
  waitForJob,
  type BacktestJobResult,
  type JobResponse,
  type NoiseJobResult,
} from "../../../lib/modalApi";
import { rejectCrossSite } from "../../../lib/requestGuard";

const execFilePromise = util.promisify(execFile);

// Modal jobs are started and polled (never held open); give the route room for a long backtest.
export const maxDuration = 300;
const MODAL_WAIT_MS = 270_000;

// Load unified strategy catalog
function getCatalogItem(strategyId: string) {
  const candidatePaths = [
    path.resolve(process.cwd(), "..", "sajim_quant", "core", "strategy_catalog.json"),
    path.resolve(process.cwd(), "sajim_quant", "core", "strategy_catalog.json"),
  ];

  for (const p of candidatePaths) {
    if (fs.existsSync(p)) {
      try {
        const catalog: { id: string; source_file?: string; run_adapter?: string }[] =
          JSON.parse(fs.readFileSync(p, "utf-8"));
        return catalog.find((c) => c.id === strategyId);
      } catch (err) {
        console.error("Failed to parse strategy_catalog.json:", err);
      }
    }
  }
  return null;
}

export async function POST(request: Request) {
  const crossSite = rejectCrossSite(request);
  if (crossSite) return crossSite;
  try {
    const body = await request.json();
    const strategy_id = body.strategy_id || body.strategyId;
    const {
      symbol = "XAUUSD",
      timeframe = "M5",
      initial_balance = 10000,
      mode = "real_backtest",
      provider = "modal"
    } = body;

    // ── 1. Strict Input Validation (Defense in Depth) ──────────────────────────
    if (!strategy_id || typeof strategy_id !== "string" || !/^[a-zA-Z0-9_-]+$/.test(strategy_id)) {
      return NextResponse.json({ success: false, error: "Invalid or missing strategy_id" }, { status: 400 });
    }

    if (!symbol || typeof symbol !== "string" || !/^[a-zA-Z0-9_]{2,12}$/.test(symbol)) {
      return NextResponse.json({ success: false, error: "Invalid symbol format" }, { status: 400 });
    }

    const cleanSymbol = symbol.toUpperCase();

    if (!timeframe || typeof timeframe !== "string" || !/^(M1|M5|M15|M30|H1|H4|D1)$/i.test(timeframe)) {
      return NextResponse.json({ success: false, error: "Invalid timeframe. Supported: M1, M5, M15, M30, H1, H4, D1" }, { status: 400 });
    }

    const cleanTf = timeframe.toUpperCase();

    const numericBalance = Number(initial_balance);
    if (isNaN(numericBalance) || numericBalance <= 0 || numericBalance > 100000000) {
      return NextResponse.json({ success: false, error: "initial_balance must be a positive number up to 100,000,000" }, { status: 400 });
    }

    if (provider !== "modal" && provider !== "local") {
      return NextResponse.json({ success: false, error: "provider must be 'modal' or 'local'" }, { status: 400 });
    }

    if (mode !== "real_backtest" && mode !== "load_existing") {
      return NextResponse.json({ success: false, error: "mode must be 'real_backtest' or 'load_existing'" }, { status: 400 });
    }

    const repoRoot = path.resolve(process.cwd(), "..");
    const resultsDirs = [
      path.resolve(repoRoot, "data", "results"),
      path.resolve(process.cwd(), "data", "results"),
      path.resolve(process.cwd(), "public", "test_results"),
    ];

    /**
     * Locate the result file for exactly this strategy + symbol + timeframe.
     *
     * There used to be a second pass that returned ANY file whose name contained
     * the strategy id. Combined with both exec paths swallowing their errors,
     * asking for beep_vacuum_hunter on GBPUSD H1 — which has no result — handed
     * back beep_vacuum_hunter_XAUUSD_M5_result.json tagged
     * "local_backtest_completed", so the cockpit rendered XAUUSD M5 numbers under
     * a GBPUSD H1 heading. A missing result must read as missing.
     */
    const findExistingResult = () => {
      for (const dir of resultsDirs) {
        if (!fs.existsSync(dir)) continue;
        const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json"));
        // Exact name, as both the CLI (--name <id>) and modal_runner write it. Substring
        // matching picked ..._M15_result.json for an M1 request.
        const expected = `${strategy_id}_${cleanSymbol}_${cleanTf}_result.json`.toLowerCase();
        const exactMatch = files.find((f) => f.toLowerCase() === expected);
        if (exactMatch) return path.join(dir, exactMatch);
      }
      return null;
    };

    // If load_existing mode, immediately return the file if found
    if (mode === "load_existing") {
      const existing = findExistingResult();
      if (existing) {
        const raw = JSON.parse(fs.readFileSync(existing, "utf-8"));
        return NextResponse.json({
          success: true,
          mode: "cached_real_result",
          file: path.basename(existing),
          data: raw,
        });
      } else {
        return NextResponse.json({
          success: false,
          error:
            `No benchmark result exists for ${strategy_id} on ${cleanSymbol} ${cleanTf}. ` +
            `Run the backtest to produce one — results from other symbols or ` +
            `timeframes are deliberately not substituted.`,
        }, { status: 404 });
      }
    }

    // ── 2. Resolve Strategy from Unified SSOT Catalog ────────────────────────
    const catalogItem = getCatalogItem(strategy_id);
    const sourceRelPath = catalogItem?.source_file || `strategies/${strategy_id}/strategy.py`;
    const fullStrategyPath = path.resolve(repoRoot, sourceRelPath);

    if (provider === "modal" && catalogItem?.run_adapter === "mt5") {
      return NextResponse.json({
        success: false,
        error:
          `${strategy_id} is an MT5 Expert Advisor — it needs the Windows MetaTrader terminal ` +
          `and cannot run on Modal. Use the Local engine (or a Windows host).`,
      }, { status: 422 });
    }

    const startTime = Date.now();
    let runnerLogs = "";

    // ── 3. Execute ───────────────────────────────────────────────────────────
    if (provider === "modal") {
      // The job runs detached on Modal (sajim_quant/cloud/modal_runner.py). This route
      // only starts it and polls over short HTTP calls, so a dropped connection cannot
      // kill the run the way the old `modal run` subprocess did.
      if (!modalApiConfig()) {
        return NextResponse.json({ success: false, error: NOT_CONFIGURED_MESSAGE }, { status: 503 });
      }
      let jobId = "";
      let job: JobResponse<BacktestJobResult>;
      try {
        const started = await startBacktest({
          strategy_id,
          symbol: cleanSymbol,
          timeframe: cleanTf,
          initial_balance: numericBalance,
        });
        jobId = started.job_id;
        runnerLogs = `[Modal] job ${jobId} started: ${strategy_id} ${cleanSymbol} ${cleanTf} (balance ${numericBalance})\n`;
        job = await waitForJob<BacktestJobResult>(jobId, { timeoutMs: MODAL_WAIT_MS });
      } catch (e: unknown) {
        const code = e instanceof ModalApiError ? e.code : null;
        const message = e instanceof Error ? e.message : String(e);
        return NextResponse.json({
          success: false,
          error:
            code === "timeout"
              ? `${message}. It keeps running on Modal — fetch it later with GET /api/run-test?job=${jobId}.`
              : `Could not run on Modal: ${message}. Nothing was run locally.`,
          execution_time_ms: Date.now() - startTime,
          job_id: jobId || undefined,
          logs: runnerLogs,
        }, { status: code === "timeout" ? 504 : code === "not_configured" ? 503 : 502 });
      }

      const result = job.result;
      runnerLogs += result?.logs ?? job.logs ?? "";
      if (job.status !== "done" || !result?.success || !result.data) {
        // A cloud failure is reported as a failure — never silently re-run locally.
        return NextResponse.json({
          success: false,
          error:
            `Modal backtest for ${strategy_id} (${cleanSymbol} ${cleanTf}) failed: ` +
            `${job.error ?? result?.error ?? "no result payload"}. Nothing was run locally.`,
          execution_time_ms: Date.now() - startTime,
          job_id: jobId,
          logs: runnerLogs,
        }, { status: 422 });
      }

      const file = result.file ?? `${strategy_id}_${cleanSymbol}_${cleanTf}_result.json`;
      const saved = saveResultLocally(file, result.data); // canonical data/results copy + static mirror
      runnerLogs += `\n[Modal] result saved to ${saved.full}`;
      const warning = staleDeployWarning(result);
      if (warning) runnerLogs = `[WARNING] ${warning}\n\n${runnerLogs}`;
      return NextResponse.json({
        success: true,
        mode: "modal_cloud_execution",
        execution_time_ms: Date.now() - startTime,
        file,
        job_id: jobId,
        warning: warning ?? undefined,
        logs: runnerLogs,
        data: result.data,
      });
    }

    let localFailed = false;
    if (provider === "local") {
      // Local execution engine
      try {
        const pythonBin = process.env.PYTHON_BIN || "python";
        const localStrategyArg = fs.existsSync(fullStrategyPath) ? fullStrategyPath : strategy_id;
        const localArgs = [
          "-X", "utf8",
          "-m", "sajim_quant", "test",
          "--strategy", localStrategyArg,
          "--symbol", cleanSymbol,
          "--tf", cleanTf,
          "--balance", String(numericBalance),
          // Without --name the CLI names the file after the source stem (test_baseline_...,
          // smc_daemon_...) and findExistingResult never finds what the run produced.
          "--name", strategy_id
        ];

        const { stdout, stderr } = await execFilePromise(pythonBin, localArgs, {
          cwd: repoRoot,
          env: {
            ...process.env,
            PYTHONUTF8: "1",
            PYTHONIOENCODING: "utf-8"
          },
          timeout: 180000
        });
        runnerLogs += (runnerLogs ? "\n\n" : "") + stdout + (stderr ? `\n[STDERR]: ${stderr}` : "");
      } catch (execErr: unknown) {
        const err = execErr as { stdout?: string; stderr?: string; message?: string };
        localFailed = true;
        runnerLogs +=
          (runnerLogs ? "\n\n" : "") +
          `[LOCAL EXECUTION FAILED]: ${err.message ?? "unknown error"}\n` +
          (err.stdout ?? "") +
          (err.stderr ? `\n[STDERR]: ${err.stderr}` : "");
      }
    }

    const elapsed = Date.now() - startTime;

    // Look for the result file this run should have produced.
    const resultFile = findExistingResult();
    if (localFailed) {
      // The subprocess errored. Any file sitting there predates this run, so
      // reporting it as a completed backtest would be a lie.
      return NextResponse.json({
        success: false,
        error:
          `Backtest for ${strategy_id} (${cleanSymbol} ${cleanTf}) failed to execute. ` +
          `No new result was produced.`,
        execution_time_ms: elapsed,
        stale_result_available: Boolean(resultFile),
        logs: runnerLogs,
      }, { status: 422 });
    }

    // The file must have been written by this run, not left over from an earlier one.
    if (resultFile && fs.existsSync(resultFile) && fs.statSync(resultFile).mtimeMs >= startTime - 1000) {
      const resultData = JSON.parse(fs.readFileSync(resultFile, "utf-8"));
      // Ensure file is mirrored to public/test_results for immediate static web serving
      try {
        const publicDir = path.resolve(process.cwd(), "public", "test_results");
        if (!fs.existsSync(publicDir)) fs.mkdirSync(publicDir, { recursive: true });
        const publicDest = path.join(publicDir, path.basename(resultFile));
        fs.copyFileSync(resultFile, publicDest);
      } catch {}

      return NextResponse.json({
        success: true,
        mode: "local_backtest_completed",
        execution_time_ms: elapsed,
        file: path.basename(resultFile),
        logs: runnerLogs,
        data: resultData
      });
    }

    // Fail honestly with clear diagnostics
    return NextResponse.json({
      success: false,
      error: `Strategy execution did not produce a valid benchmark result.`,
      execution_time_ms: elapsed,
      logs: runnerLogs
    }, { status: 422 });

  } catch (error: unknown) {
    console.error("Run test API error:", error);
    const message = error instanceof Error ? error.message : "Internal Server Error";
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}

/**
 * GET /api/run-test?job=<Modal job id>
 * Status of a job started by POST — useful after a 504 timeout. A finished backtest
 * (or noise diagnosis) is saved locally exactly as the POST paths do.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const jobId = searchParams.get("job") ?? "";
  if (!isValidJobId(jobId)) {
    return NextResponse.json({ success: false, error: "job must be a Modal job id (fc-...)" }, { status: 400 });
  }
  if (!modalApiConfig()) {
    return NextResponse.json({ success: false, error: NOT_CONFIGURED_MESSAGE }, { status: 503 });
  }
  try {
    const job = await getJob(jobId);
    const result = job.result;
    if (job.status === "done" && result?.success) {
      if (isNoiseResult(result)) {
        const noise: NoiseJobResult = result;
        const saved = saveNoiseLocally(noise);
        return NextResponse.json({
          success: true, status: job.status, job_id: jobId, kind: job.kind, file: job.file,
          saved: saved.enriched, summary: noise.data?.noise_summary ?? null,
        });
      }
      const backtest = result as BacktestJobResult;
      const file = backtest.file ?? job.file;
      if (backtest.data && file) {
        const saved = saveResultLocally(file, backtest.data);
        return NextResponse.json({
          success: true, status: job.status, job_id: jobId, kind: job.kind ?? "backtest",
          file, saved: saved.full, data: backtest.data,
        });
      }
    }
    const failed = job.status === "failed" || job.status === "expired";
    return NextResponse.json(
      {
        success: !failed, status: job.status, job_id: jobId, kind: job.kind, file: job.file,
        error: job.error ?? result?.error,
      },
      { status: failed ? 422 : 200 }
    );
  } catch (e: unknown) {
    const status = e instanceof ModalApiError && e.status === 404 ? 404 : 502;
    return NextResponse.json({ success: false, error: e instanceof Error ? e.message : String(e) }, { status });
  }
}
