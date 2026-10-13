import { NextResponse } from "next/server";
import { readExport } from "../../lib/server";
import type { CorrelationExport } from "../../lib/types";

export async function GET() {
  const data = readExport<CorrelationExport>("correlation.json");
  if (!data) {
    return NextResponse.json({ success: false, error: "correlation.json not found. Run `sajim export`." }, { status: 404 });
  }
  return NextResponse.json({ success: true, ...data });
}
