import { NextResponse } from "next/server";
import { readExport } from "../../lib/server";
import type { RegistryExport } from "../../lib/types";

export async function GET() {
  const data = readExport<RegistryExport>("registry.json");
  if (!data) {
    return NextResponse.json({ success: false, error: "registry.json not found. Run `sajim export`." }, { status: 404 });
  }
  return NextResponse.json({ success: true, ...data });
}
