import { NextResponse } from "next/server";
import fs from "fs";
import { listResultEntries, resolveResultFile } from "../../../lib/resultFiles";

const KNOWN_SOURCES = ["data/results", "test_results"] as const;

/**
 * Catalog of real backtest result JSONs.
 *
 *   GET /api/results                     -> metadata list (no trade arrays)
 *   GET /api/results?file=<name>&source= -> the full parsed result JSON
 *
 * `file` must name a JSON inside one of the known result directories — any
 * other path is rejected (no traversal, no absolute paths).
 */
export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const file = searchParams.get("file");
    const source = searchParams.get("source");

    if (file) {
      const { entries } = listResultEntries();
      const entry = entries.find(
        (e) =>
          e.file === file &&
          (source === null || !KNOWN_SOURCES.includes(source as (typeof KNOWN_SOURCES)[number]) || e.source === source)
      );
      if (!entry) {
        return NextResponse.json({ success: false, error: "Result file not found in catalog" }, { status: 404 });
      }
      const full = resolveResultFile(entry);
      if (!full) {
        return NextResponse.json({ success: false, error: "Result file unreadable" }, { status: 404 });
      }
      const raw = JSON.parse(fs.readFileSync(full, "utf-8"));
      return NextResponse.json(raw, {
        headers: { "Cache-Control": "no-store" },
      });
    }

    const { entries, skipped } = listResultEntries();
    return NextResponse.json(
      { success: true, count: entries.length, skipped, entries },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (error) {
    console.error("Results API error:", error);
    return NextResponse.json({ success: false, error: "Internal Server Error" }, { status: 500 });
  }
}
