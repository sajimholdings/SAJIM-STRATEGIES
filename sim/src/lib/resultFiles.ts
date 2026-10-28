/**
 * Server-only catalog of real backtest result files.
 *
 * The CLI (python -m sajim_quant test) writes to <repo>/data/results/; the
 * dashboard also keeps copies under sim/public/test_results/. This module
 * enumerates both, parses each file with the universal schema parser, and
 * returns compact metadata (no trade arrays) plus a safe path resolver.
 *
 * Import ONLY from route handlers / server code — this module uses node:fs.
 */

import fs from "fs";
import path from "path";
import { summarizeResultEntry, type ResultEntry } from "./results";

const RESULT_FILE_RE = /\.json$/i;

/** Candidate directories, checked in order (repo-root data/results first). */
export function resultDirs(): { dir: string; source: ResultEntry["source"] }[] {
  return [
    { dir: path.resolve(process.cwd(), "..", "data", "results"), source: "data/results" },
    { dir: path.resolve(process.cwd(), "data", "results"), source: "data/results" },
    { dir: path.resolve(process.cwd(), "public", "test_results"), source: "test_results" },
  ];
}

/** Full path for a catalogued entry, or null when it does not resolve safely. */
export function resolveResultFile(entry: ResultEntry): string | null {
  const bySource: Record<ResultEntry["source"], string[]> = {
    "data/results": [
      path.resolve(process.cwd(), "..", "data", "results"),
      path.resolve(process.cwd(), "data", "results"),
    ],
    test_results: [path.resolve(process.cwd(), "public", "test_results")],
  };
  for (const dir of bySource[entry.source] ?? []) {
    const full = path.join(dir, entry.file);
    if (full.startsWith(dir + path.sep) && fs.existsSync(full)) return full;
  }
  return null;
}

/**
 * Enumerate all real result JSONs with their metadata summaries.
 * Unparseable files are skipped (and counted) rather than failing the list.
 *
 * Deduplicated by file name. The CLI writes each result to <repo>/data/results
 * and then mirrors it into sim/public/test_results, so every result exists
 * twice; listing both made the leaderboard show every strategy twice and gave
 * the correlation matrix a perfect r=1 against each strategy's own copy. The
 * directory order in resultDirs() decides the winner (repo-root data/results
 * first), so the canonical copy wins and the mirror is dropped.
 */
export function listResultEntries(): { entries: ResultEntry[]; skipped: string[] } {
  const entries: ResultEntry[] = [];
  const skipped: string[] = [];
  const seen = new Set<string>();
  for (const { dir, source } of resultDirs()) {
    let files: string[] = [];
    try {
      files = fs.readdirSync(dir);
    } catch {
      continue; // directory does not exist — nothing to list
    }
    for (const f of files) {
      if (!RESULT_FILE_RE.test(f)) continue;
      const key = f.toLowerCase();
      if (seen.has(key)) continue; // already catalogued from a higher-priority dir
      const full = path.join(dir, f);
      try {
        const raw = JSON.parse(fs.readFileSync(full, "utf-8"));
        const entry = summarizeResultEntry(raw);
        if (!entry) {
          if (!skipped.includes(f)) skipped.push(f);
          continue;
        }
        entries.push({ ...entry, file: f, source });
        seen.add(key);
      } catch {
        if (!skipped.includes(f)) skipped.push(f);
      }
    }
  }
  // Deterministic order: newest first (by end_date), then file name.
  entries.sort((a, b) => {
    const ea = a.end_date ?? "";
    const eb = b.end_date ?? "";
    if (ea !== eb) return ea < eb ? 1 : -1;
    return a.file.localeCompare(b.file);
  });
  return { entries, skipped };
}

/** The first entry that exists on disk, or null. */
export function findResultEntry(file: string, source: ResultEntry["source"]): ResultEntry | null {
  const { entries } = listResultEntries();
  return entries.find((e) => e.file === file && e.source === source) ?? null;
}
