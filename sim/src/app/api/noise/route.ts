import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { listResultEntries, resolveResultFile } from "../../../lib/resultFiles";
import {
  ModalApiError,
  NOT_CONFIGURED_MESSAGE,
  modalApiConfig,
  saveNoiseLocally,
  startNoise,
  waitForJob,
  type JobResponse,
  type NoiseJobResult,
} from "../../../lib/modalApi";
import { rejectCrossSite } from "../../../lib/requestGuard";

// Runs on Modal (data in the sajim-market-data volume) as a detached job that this
// route starts and polls: container start plus the walk-forward HAR-RV refit.
export const maxDuration = 300;
const DIAGNOSE_TIMEOUT_MS = 270_000;

/**
 * Noise (volatility-regime) diagnosis for one backtest result.
 *
 *   GET  /api/noise?file=<name>&source=<src>  -> cached summary, or { cached: false }
 *   POST /api/noise { file, source }          -> uploads the result to the Modal API, runs the
 *        diagnosis there as a detached job and polls it (compute stays off this machine)
 *
 * Only files in the results catalog are accepted (same guard as /api/results).
 * The response is the compact noise_summary — never the enriched trades array.
 */

const repoRoot = () => path.resolve(process.cwd(), "..");
const diagnoseDir = () => path.resolve(repoRoot(), "data", "noise", "diagnose");

function lookup(file: string | null, source: string | null) {
  if (!file) return null;
  const { entries } = listResultEntries();
  const entry = entries.find((e) => e.file === file && (source === null || e.source === source));
  if (!entry) return null;
  const full = resolveResultFile(entry);
  return full ? { entry, full } : null;
}

/** Mirrors diagnose_file(): <stem minus "_result">_noise_enriched.json */
function enrichedPath(resultFile: string) {
  const stem = path.basename(resultFile, path.extname(resultFile)).replace(/_result$/, "");
  return path.join(diagnoseDir(), `${stem}_noise_enriched.json`);
}

/** Cached summary, only if it is at least as new as the result it describes. */
function readCached(resultFull: string) {
  const p = enrichedPath(resultFull);
  if (!fs.existsSync(p)) return null;
  if (fs.statSync(p).mtimeMs < fs.statSync(resultFull).mtimeMs) return null;
  try {
    const raw = JSON.parse(fs.readFileSync(p, "utf-8"));
    return raw.noise_summary ?? null;
  } catch {
    return null;
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const hit = lookup(searchParams.get("file"), searchParams.get("source"));
  if (!hit) {
    return NextResponse.json({ success: false, error: "Result file not found in catalog" }, { status: 404 });
  }
  const summary = readCached(hit.full);
  return NextResponse.json(
    summary ? { success: true, cached: true, summary } : { success: true, cached: false },
    { headers: { "Cache-Control": "no-store" } }
  );
}

export async function POST(request: Request) {
  const crossSite = rejectCrossSite(request);
  if (crossSite) return crossSite;
  let body: { file?: string; source?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ success: false, error: "Invalid JSON body" }, { status: 400 });
  }
  const hit = lookup(body.file ?? null, body.source ?? null);
  if (!hit) {
    return NextResponse.json({ success: false, error: "Result file not found in catalog" }, { status: 404 });
  }

  if (!modalApiConfig()) {
    return NextResponse.json({ success: false, error: NOT_CONFIGURED_MESSAGE }, { status: 503 });
  }
  // Upload the local result so the cloud diagnoses exactly this file (it may never have run on Modal).
  let raw: unknown;
  try {
    raw = JSON.parse(fs.readFileSync(hit.full, "utf-8"));
  } catch {
    return NextResponse.json({ success: false, error: "Result file unreadable" }, { status: 404 });
  }

  let jobId = "";
  let job: JobResponse<NoiseJobResult>;
  try {
    const started = await startNoise({ file: hit.entry.file, result: raw });
    jobId = started.job_id;
    job = await waitForJob<NoiseJobResult>(jobId, { timeoutMs: DIAGNOSE_TIMEOUT_MS });
  } catch (e: unknown) {
    const code = e instanceof ModalApiError ? e.code : null;
    const message = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      {
        success: false,
        job_id: jobId || undefined,
        error:
          code === "timeout"
            ? `${message}. It keeps running on Modal (job ${jobId}); the panel picks it up when it finishes.`
            : `Could not run the noise diagnosis on Modal: ${message}`,
      },
      { status: code === "timeout" ? 504 : code === "not_configured" ? 503 : 502 }
    );
  }

  const result = job.result;
  if (job.status !== "done" || !result?.success || !result.data) {
    // e.g. "No M5 data at /root/data/parquet/1HZ100V/M5.parquet. Available symbols: ..."
    return NextResponse.json(
      {
        success: false,
        job_id: jobId,
        error: `Noise diagnosis failed on Modal: ${job.error ?? result?.error ?? "no result payload"}`,
      },
      { status: 422 }
    );
  }
  saveNoiseLocally(result, hit.entry.file); // data/noise/diagnose/<stem>_noise_enriched.json + _noise_report.md

  const summary = readCached(hit.full);
  if (!summary) {
    return NextResponse.json(
      { success: false, error: "Diagnosis ran but produced no summary file" },
      { status: 500 }
    );
  }
  return NextResponse.json({ success: true, cached: false, summary, job_id: jobId });
}
