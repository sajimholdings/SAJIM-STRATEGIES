// Server-only helpers for the API routes: locate exported JSON and the results/ registry.
import fs from "fs";
import path from "path";

/** Walk up from cwd until a directory with pyproject.toml (the repo root) is found. */
export function repoRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(dir, "pyproject.toml"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

export function resultsDir(): string {
  return process.env.SAJIM_RESULTS_DIR || path.join(repoRoot(), "results");
}

export function publicDir(): string {
  return path.join(process.cwd(), "public");
}

export function readJson<T>(file: string): T | null {
  try {
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, "utf-8")) as T;
  } catch {
    return null;
  }
}

export function readText(file: string): string | null {
  try {
    return fs.existsSync(file) ? fs.readFileSync(file, "utf-8") : null;
  } catch {
    return null;
  }
}

/** Exported JSON lives in public/ (written by `sajim export`). */
export function readExport<T>(name: string): T | null {
  return readJson<T>(path.join(publicDir(), name));
}

const SAFE = /^[A-Za-z0-9_.-]+$/;
export function safeSegment(s: string): string | null {
  return SAFE.test(s) && !s.startsWith(".") ? s : null;
}

export function runDir(strategyId: string, runId: string): string | null {
  const a = safeSegment(strategyId);
  const b = safeSegment(runId);
  if (!a || !b) return null;
  return path.join(resultsDir(), a, b);
}
