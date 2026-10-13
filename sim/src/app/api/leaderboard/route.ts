import { NextResponse } from "next/server";
import { readExport } from "../../lib/server";
import type { LeaderboardExport } from "../../lib/types";

export async function GET() {
  const data = readExport<LeaderboardExport>("leaderboard.json");
  if (!data) {
    return NextResponse.json(
      { success: false, error: "leaderboard.json not found in public/. Run `sajim export` from the repo root." },
      { status: 404 },
    );
  }
  return NextResponse.json({ success: true, ...data });
}
