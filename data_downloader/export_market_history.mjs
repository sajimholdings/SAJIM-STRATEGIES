// export_market_history.mjs
//
// Turns the owner's Dukascopy CSVs (data/dukascopy/{SYMBOL}/{TF}.csv, written by
// download_all.py: bid prices, UTC wall-clock time, columns time,open,high,low,close,volume)
// into the Sajim Traders chart test's history files, and uploads them to the private
// Supabase Storage bucket `market-history` at {INSTRUMENT}/{TF}.json.
//
// Output matches NoNotion scripts/seed-market-history.mjs (buildFile):
//   {"instrument","tf","source","from","to","decimals","candles":[[t,o,h,l,c], ...]}
//   t = candle open time, epoch ms UTC; ascending, no duplicates; prices rounded to `decimals`.
// Saturday/Sunday UTC candles are dropped (the rules reject any window containing one).
//
// No dependencies: uploads with fetch() to the Storage REST API (same call supabase-js makes,
// upsert on). Env: SUPABASE_SERVICE_ROLE_KEY (required to upload, never printed) and
// NEXT_PUBLIC_SUPABASE_URL (defaults to the Sajim project).
//
// Usage (from F:\Tete\SAJIM STRATEGIES):
//   node data_downloader/export_market_history.mjs --no-upload
//   node data_downloader/export_market_history.mjs
// Options: --src data/dukascopy  --out data/market-history  --only EURUSD,USDJPY  --tf M5,H1  --no-upload

import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const DEFAULT_URL = "https://uzpzrgellteuwwqdrmke.supabase.co";
const BUCKET = "market-history";
const SOURCE = "dukascopy bid (owner files)";
const MAX_BYTES = 25 * 1024 * 1024;
const KEEP_MS = 2 * 365 * 24 * 3600 * 1000; // trim to the latest ~2 years if a file is too big

// Chart test instruments (apps/dashboard/src/config/assets.json) the owner has data for.
// NAS100 has no local file (US500 is a different index), so it is left out.
const INSTRUMENTS = { EURUSD: 5, GBPUSD: 5, USDJPY: 3, XAUUSD: 2, US30: 2, BTCUSD: 2 };
const TF_MS = { M5: 5 * 60_000, M15: 15 * 60_000, H1: 60 * 60_000 };

function parseArgs(argv) {
  const args = { src: "data/dukascopy", out: "data/market-history", only: null, tf: null, upload: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--src") args.src = argv[++i];
    else if (a === "--out") args.out = argv[++i];
    else if (a === "--only") args.only = argv[++i].split(",").map((s) => s.trim().toUpperCase());
    else if (a === "--tf") args.tf = argv[++i].split(",").map((s) => s.trim().toUpperCase());
    else if (a === "--no-upload") args.upload = false;
    else throw new Error(`unknown argument: ${a}`);
  }
  return args;
}

const round = (x, d) => Number(x.toFixed(d));
const day = (t) => new Date(t).toISOString().slice(0, 10);

function parseCsv(text, tfMs, decimals) {
  const stats = { rows: 0, weekend: 0, duplicate: 0, misaligned: 0, badOhlc: 0 };
  const byTime = new Map();
  const lines = text.split(/\r?\n/);
  if (!/^time,open,high,low,close/i.test(lines[0])) throw new Error(`unexpected header: ${lines[0]}`);
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line) continue;
    stats.rows++;
    const [ts, o, h, l, c] = line.split(",");
    const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(ts);
    if (!m) throw new Error(`bad time on line ${i + 1}: ${ts}`);
    const t = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
    if (t % tfMs !== 0) { stats.misaligned++; continue; }
    const wd = new Date(t).getUTCDay();
    if (wd === 0 || wd === 6) { stats.weekend++; continue; }
    if (byTime.has(t)) { stats.duplicate++; continue; }
    const bar = [t, round(+o, decimals), round(+h, decimals), round(+l, decimals), round(+c, decimals)];
    if (bar.slice(1).some((x) => !Number.isFinite(x) || x <= 0)) throw new Error(`bad price on line ${i + 1}`);
    if (bar[2] < Math.max(bar[1], bar[4]) || bar[3] > Math.min(bar[1], bar[4])) { stats.badOhlc++; continue; }
    byTime.set(t, bar);
  }
  const candles = [...byTime.values()].sort((a, b) => a[0] - b[0]);
  return { candles, stats };
}

function buildFile(instrument, tf, decimals, candles) {
  return {
    instrument, tf, source: SOURCE,
    from: day(candles[0][0]), to: day(candles[candles.length - 1][0]),
    decimals, candles,
  };
}

async function upload(baseUrl, key, objectPath, body) {
  const res = await fetch(`${baseUrl}/storage/v1/object/${BUCKET}/${objectPath}`, {
    method: "POST",
    headers: {
      apikey: key, Authorization: `Bearer ${key}`,
      "Content-Type": "application/json", "x-upsert": "true", "cache-control": "max-age=3600",
    },
    body,
  });
  if (!res.ok) throw new Error(`upload ${objectPath} failed: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
}

async function listBucket(baseUrl, key, prefix) {
  const res = await fetch(`${baseUrl}/storage/v1/object/list/${BUCKET}`, {
    method: "POST",
    headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ prefix, limit: 100, offset: 0 }),
  });
  if (!res.ok) throw new Error(`list ${prefix} failed: HTTP ${res.status}`);
  return res.json();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const baseUrl = (process.env.NEXT_PUBLIC_SUPABASE_URL || DEFAULT_URL).replace(/\/+$/, "");
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (args.upload && !key) throw new Error("SUPABASE_SERVICE_ROLE_KEY is not set (or pass --no-upload)");

  const names = Object.keys(INSTRUMENTS).filter((n) => !args.only || args.only.includes(n));
  const tfs = Object.keys(TF_MS).filter((t) => !args.tf || args.tf.includes(t));
  const done = [];

  for (const instrument of names) {
    for (const tf of tfs) {
      const decimals = INSTRUMENTS[instrument];
      const csv = path.join(args.src, instrument, `${tf}.csv`);
      const { candles: all, stats } = parseCsv(await readFile(csv, "utf8"), TF_MS[tf], decimals);
      let candles = all;
      let json = JSON.stringify(buildFile(instrument, tf, decimals, candles));
      if (json.length > MAX_BYTES) {
        const cut = candles[candles.length - 1][0] - KEEP_MS;
        candles = candles.filter((b) => b[0] >= cut);
        json = JSON.stringify(buildFile(instrument, tf, decimals, candles));
      }
      const objectPath = `${instrument}/${tf}.json`;
      const outFile = path.join(args.out, instrument, `${tf}.json`);
      await mkdir(path.dirname(outFile), { recursive: true });
      await writeFile(outFile, json);
      const mb = (json.length / 1048576).toFixed(1);
      console.log(
        `${objectPath.padEnd(16)} ${String(candles.length).padStart(6)} candles  ${day(candles[0][0])} → ` +
        `${day(candles[candles.length - 1][0])}  ${mb} MB  (dropped: weekend ${stats.weekend}, dup ${stats.duplicate}, ` +
        `misaligned ${stats.misaligned}, bad OHLC ${stats.badOhlc})`,
      );
      if (args.upload) {
        await upload(baseUrl, key, objectPath, json);
        console.log(`  uploaded ${BUCKET}/${objectPath}`);
      }
      done.push(objectPath);
    }
  }

  if (args.upload) {
    console.log(`\nBucket ${BUCKET} now holds:`);
    for (const instrument of names) {
      const items = await listBucket(baseUrl, key, instrument);
      for (const it of items) console.log(`  ${instrument}/${it.name}  ${((it.metadata?.size ?? 0) / 1048576).toFixed(1)} MB`);
    }
  }
  console.log(`\n${done.length} files ${args.upload ? "written and uploaded" : `written to ${args.out} (not uploaded)`}.`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
