/**
 * Cross-site request guard for routes that spend cloud compute or write files.
 *
 * The dashboard has no login, so any page open in the same browser could otherwise
 * POST to http://localhost:3001/api/run-test (a "simple" text/plain request needs no
 * CORS preflight) and start billed Modal jobs. Browsers mark such requests with
 * Sec-Fetch-Site / Origin; same-origin dashboard calls and curl/scripts pass.
 */

import { NextResponse } from "next/server";

export function rejectCrossSite(request: Request): NextResponse | null {
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") {
    return NextResponse.json({ success: false, error: "Cross-site request rejected" }, { status: 403 });
  }
  const origin = request.headers.get("origin");
  if (origin) {
    let originHost = "";
    try {
      originHost = new URL(origin).host;
    } catch {
      // unparseable Origin ("null" from sandboxed frames, etc.) is treated as cross-site
    }
    if (!originHost || originHost !== request.headers.get("host")) {
      return NextResponse.json({ success: false, error: "Cross-site request rejected" }, { status: 403 });
    }
  }
  return null;
}
