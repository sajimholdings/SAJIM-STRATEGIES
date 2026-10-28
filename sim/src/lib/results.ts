/**
 * SAJIM Quant — result-JSON parsing & series builders.
 *
 * The dashboard renders ONLY data derived from real backtest result JSONs
 * (produced by `python -m sajim_quant test` and written to
 * sim/public/test_results/*.json). Nothing in this module synthesizes or
 * fabricates numbers — every builder is a pure function of the input trades.
 */

export type TradeRecord = {
  trade_id?: string;
  strategy_name?: string;
  engine?: string;
  symbol?: string;
  timeframe?: string;
  direction?: string;
  entry_time?: string | null;
  entry_price?: number | null;
  position_size?: number | null;
  exit_time?: string | null;
  exit_price?: number | null;
  exit_reason?: string | null;
  gross_pnl?: number | null;
  net_pnl?: number | null;
  commission?: number | null;
  swap_cost?: number | null;
  return_pct?: number | null;
  mae?: number | null;
  mfe?: number | null;
  duration_seconds?: number | null;
  custom_fields?: Record<string, unknown> | null;
};

export type ResultMetrics = {
  initial_balance?: number;
  final_balance?: number;
  net_profit?: number;
  return_pct?: number;
  cagr_pct?: number;
  total_trades?: number;
  winning_trades?: number;
  losing_trades?: number;
  win_rate_pct?: number;
  profit_factor?: number;
  expectancy?: number;
  avg_trade_pnl?: number;
  max_drawdown_amount?: number;
  max_drawdown_pct?: number;
  recovery_factor?: number;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  calmar_ratio?: number;
  max_consecutive_wins?: number;
  max_consecutive_losses?: number;
  avg_duration_minutes?: number;
  total_commission?: number;
  total_swap?: number;
  [key: string]: unknown;
};

export type ResultMetadata = {
  strategy_name?: string;
  engine?: string;
  symbol?: string;
  timeframe?: string;
  start_date?: string | null;
  end_date?: string | null;
  initial_balance?: number | null;
  currency?: string | null;
  notes?: string | null;
  created_at?: string | null;
  [key: string]: unknown;
};

export type BacktestResultJSON = {
  metadata: ResultMetadata;
  metrics: ResultMetrics;
  trades: TradeRecord[];
};

export type EquityPoint = {
  index: number;
  /** ISO entry timestamp of the trade that produced this point (null for the start point). */
  time: string | null;
  equity: number;
};

export type DrawdownPoint = {
  index: number;
  time: string | null;
  /** Drawdown as a percentage of the running peak (0 = at peak). */
  ddPct: number;
  /** Drawdown in absolute currency (always >= 0). */
  ddAbs: number;
};

export type MonthlyBucket = {
  /** YYYY-MM of the trades' exit month. */
  month: string;
  pnl: number;
  trades: number;
  wins: number;
};

export type PnlBucket = {
  /** Human-readable PnL range label, e.g. "$0 – $500". */
  range: string;
  count: number;
  pnl: number;
};

/** Monte Carlo stress payload shape as emitted by the real matrix builder. */
export type MonteCarloPayload = {
  trade_steps?: number[];
  p5_curve?: number[];
  p25_curve?: number[];
  p50_curve?: number[];
  p75_curve?: number[];
  p95_curve?: number[];
  worst_drawdown_95_pct?: number;
  worst_drawdown_99_pct?: number;
  risk_of_ruin_20_pct?: number;
  risk_of_ruin_10_pct?: number;
  fragility_index?: number;
  is_robust?: boolean;
  terminal_p95_balance?: number;
  terminal_median_balance?: number;
  terminal_p5_balance?: number;
};

export type FanChartPoint = {
  step: number;
  label: string;
  p5?: number;
  p25?: number;
  p50?: number;
  p75?: number;
  p95?: number;
};

// ---------------------------------------------------------------------------
// Parsing (defensive — the JSON comes from a subprocess, so treat it as untrusted)
// ---------------------------------------------------------------------------

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function str(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}

export function parseTrade(raw: unknown): TradeRecord | null {
  if (!isRecord(raw)) return null;
  return {
    trade_id: str(raw.trade_id),
    strategy_name: str(raw.strategy_name),
    engine: str(raw.engine),
    symbol: str(raw.symbol),
    timeframe: str(raw.timeframe),
    direction: str(raw.direction),
    entry_time: str(raw.entry_time) ?? null,
    entry_price: num(raw.entry_price) ?? null,
    position_size: num(raw.position_size) ?? null,
    exit_time: str(raw.exit_time) ?? null,
    exit_price: num(raw.exit_price) ?? null,
    exit_reason: str(raw.exit_reason) ?? null,
    gross_pnl: num(raw.gross_pnl) ?? null,
    net_pnl: num(raw.net_pnl) ?? null,
    commission: num(raw.commission) ?? null,
    swap_cost: num(raw.swap_cost) ?? null,
    return_pct: num(raw.return_pct) ?? null,
    mae: num(raw.mae) ?? null,
    mfe: num(raw.mfe) ?? null,
    duration_seconds: num(raw.duration_seconds) ?? null,
    custom_fields: isRecord(raw.custom_fields) ? raw.custom_fields : null,
  };
}

export function parseResultJSON(raw: unknown): BacktestResultJSON | null {
  if (!isRecord(raw)) return null;
  const metadata = isRecord(raw.metadata) ? (raw.metadata as ResultMetadata) : {};
  const metrics = isRecord(raw.metrics) ? (raw.metrics as ResultMetrics) : {};
  const tradesRaw = Array.isArray(raw.trades) ? raw.trades : [];
  const trades = tradesRaw
    .map(parseTrade)
    .filter((t): t is TradeRecord => t !== null && t.net_pnl != null);
  if (trades.length === 0) return null;
  return { metadata, metrics, trades };
}

// ---------------------------------------------------------------------------
// Series builders (pure)
// ---------------------------------------------------------------------------

function timeOf(t: TradeRecord): number {
  const ts = t.entry_time ? Date.parse(t.entry_time) : NaN;
  return Number.isNaN(ts) ? Number.POSITIVE_INFINITY : ts;
}

/**
 * Equity curve from closed trades, starting at initialBalance.
 * Trades are sorted by entry time; the first point (index 0) is the starting
 * balance, and each subsequent point is the equity after that trade's net PnL.
 */
export function buildEquityCurve(
  trades: TradeRecord[],
  initialBalance: number
): EquityPoint[] {
  const sorted = [...trades].sort((a, b) => timeOf(a) - timeOf(b));
  const points: EquityPoint[] = [
    { index: 0, time: null, equity: initialBalance },
  ];
  let equity = initialBalance;
  for (const t of sorted) {
    equity += t.net_pnl ?? 0;
    points.push({
      index: points.length,
      time: t.entry_time ?? null,
      equity,
    });
  }
  return points;
}

/**
 * Drawdown series from an equity curve (running-peak method).
 */
export function buildDrawdownSeries(equity: EquityPoint[]): DrawdownPoint[] {
  let peak = -Infinity;
  return equity.map((p) => {
    peak = Math.max(peak, p.equity);
    const ddAbs = peak - p.equity;
    const ddPct = peak > 0 ? (ddAbs / peak) * 100 : 0;
    return { index: p.index, time: p.time, ddPct, ddAbs };
  });
}

/**
 * Monthly PnL buckets by exit month (YYYY-MM), sorted chronologically.
 */
export function buildMonthlyPnl(trades: TradeRecord[]): MonthlyBucket[] {
  const byMonth = new Map<string, MonthlyBucket>();
  for (const t of trades) {
    const ts = t.exit_time ? Date.parse(t.exit_time) : NaN;
    if (Number.isNaN(ts)) continue;
    const d = new Date(ts);
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    const bucket = byMonth.get(key) ?? { month: key, pnl: 0, trades: 0, wins: 0 };
    bucket.pnl += t.net_pnl ?? 0;
    bucket.trades += 1;
    if ((t.net_pnl ?? 0) > 0) bucket.wins += 1;
    byMonth.set(key, bucket);
  }
  return [...byMonth.values()].sort((a, b) => a.month.localeCompare(b.month));
}

/**
 * Equal-weight diversification ratio from a pairwise correlation matrix:
 * DR = n / sqrt(sum of all matrix entries).
 * Identical strategies -> 1 (no benefit); orthogonal strategies -> sqrt(n).
 * Returns null when the matrix is empty or malformed.
 */
export function computeDiversificationRatio(
  corr: Record<string, Record<string, number | null>>
): number | null {
  const rows = Object.values(corr);
  if (rows.length === 0) return null;
  let sum = 0;
  for (const row of rows) {
    for (const v of Object.values(row)) {
      if (typeof v !== "number" || !Number.isFinite(v)) return null;
      sum += v;
    }
  }
  if (sum <= 0) return null;
  return rows.length / Math.sqrt(sum);
}

/**
 * Histogram of per-trade net PnL. Returns `bins` buckets spanning
 * [min, max]; when all trades have identical PnL, a single bucket is returned.
 */
export function buildPnlHistogram(trades: TradeRecord[], bins = 12): PnlBucket[] {
  const pnls = trades.map((t) => t.net_pnl ?? 0);
  if (pnls.length === 0) return [];
  const min = Math.min(...pnls);
  const max = Math.max(...pnls);
  if (min === max) {
    return [{ range: fmtMoney(min), count: pnls.length, pnl: pnls.reduce((s, v) => s + v, 0) }];
  }
  const width = (max - min) / bins;
  const buckets: PnlBucket[] = Array.from({ length: bins }, (_, i) => ({
    range: `${fmtMoney(min + i * width)} – ${fmtMoney(min + (i + 1) * width)}`,
    count: 0,
    pnl: 0,
  }));
  for (const p of pnls) {
    const idx = Math.min(bins - 1, Math.floor((p - min) / width));
    buckets[idx].count += 1;
    buckets[idx].pnl += p;
  }
  return buckets;
}

function fmtMoney(v: number): string {
  const sign = v < 0 ? "-$" : "$";
  return `${sign}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/**
 * Monte Carlo fan-chart points from a real MC payload.
 * Returns null when the payload has no trade steps (never fabricates
 * placeholder curves — callers must render a "not stress-tested" state).
 */
export function buildFanChart(mc: MonteCarloPayload | null | undefined): FanChartPoint[] | null {
  const steps = mc?.trade_steps;
  if (!mc || !Array.isArray(steps) || steps.length === 0) return null;
  const pick = (arr: number[] | undefined, i: number) =>
    Array.isArray(arr) && typeof arr[i] === "number" ? arr[i] : undefined;
  return steps.map((step, i) => ({
    step,
    label: `T+${step}`,
    p5: pick(mc.p5_curve, i),
    p25: pick(mc.p25_curve, i),
    p50: pick(mc.p50_curve, i),
    p75: pick(mc.p75_curve, i),
    p95: pick(mc.p95_curve, i),
  }));
}

// ---------------------------------------------------------------------------
// Chart-ready composites
// ---------------------------------------------------------------------------

/**
 * One shot: parse a result JSON and produce everything the tear-sheet charts
 * need, or null when the JSON has no usable trades.
 */
export function analyzeResult(raw: unknown, initialBalance?: number) {
  const result = parseResultJSON(raw);
  if (!result) return null;
  const initial =
    initialBalance ??
    result.metrics.initial_balance ??
    result.metadata.initial_balance ??
    0;
  const equity = buildEquityCurve(result.trades, initial);
  const drawdown = buildDrawdownSeries(equity);
  const monthly = buildMonthlyPnl(result.trades);
  return { result, equity, drawdown, monthly };
}

// ---------------------------------------------------------------------------
// Result catalog (Phase 3) — metadata matching + real correlations
// ---------------------------------------------------------------------------

/** Compact metadata for one result file — no trade arrays (LLM/UI payload hygiene). */
export interface ResultEntry {
  file: string;
  source: "data/results" | "test_results";
  strategy_name: string;
  engine: string;
  symbol: string;
  timeframe: string;
  start_date?: string;
  end_date?: string;
  initial_balance?: number;
  final_balance?: number;
  total_trades: number;
  net_profit?: number;
  sharpe_ratio?: number;
  profit_factor?: number;
  win_rate_pct?: number;
  max_drawdown_pct?: number;
}

/** Case/punctuation-tolerant key for matching names across naming conventions. */
export function normalizeKey(s: string | null | undefined): string {
  return (s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

export interface MatchTarget {
  name: string;
  symbol: string;
  timeframe: string;
  engine?: string;
}

/**
 * Score how well a catalog entry matches a leaderboard strategy row.
 * 1.0 = name + symbol + timeframe all agree. Returns null below 0.6 so
 * callers can fall back to filename heuristics for entries the catalog missed.
 */
export function scoreResultMatch(entry: ResultEntry, target: MatchTarget): number | null {
  const entryName = normalizeKey(entry.strategy_name);
  const targetName = normalizeKey(target.name);
  let score = 0;
  if (entryName && targetName && (entryName === targetName || entryName.includes(targetName) || targetName.includes(entryName))) {
    score += 0.4;
  }
  if (entry.symbol && target.symbol && entry.symbol.toUpperCase() === target.symbol.toUpperCase()) {
    score += 0.3;
  }
  const targetTfs = (target.timeframe ?? "")
    .split(/[/\-_]/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  const entryTfs = (entry.timeframe ?? "")
    .split(/[/\-_]/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  if (entryTfs.some((e) => targetTfs.includes(e)) || targetTfs.some((t) => entryTfs.includes(t))) {
    score += 0.3;
  }
  return score >= 0.6 ? score : null;
}

/** Best catalog entry for a strategy row, or null when nothing matches. */
export function bestResultMatch(
  entries: ResultEntry[],
  target: MatchTarget
): { entry: ResultEntry; score: number } | null {
  let best: { entry: ResultEntry; score: number } | null = null;
  for (const e of entries) {
    const s = scoreResultMatch(e, target);
    if (s !== null && (best === null || s > best.score)) {
      best = { entry: e, score: s };
    }
  }
  return best;
}

/** Pearson correlation of two aligned numeric series (null when <3 points or zero variance). */
export function computePairwiseCorrelation(a: number[], b: number[]): number | null {
  if (!Array.isArray(a) || !Array.isArray(b)) return null;
  const n = Math.min(a.length, b.length);
  if (n < 3) return null;
  const ax = a.slice(0, n);
  const bx = b.slice(0, n);
  const mean = (xs: number[]) => xs.reduce((s, v) => s + v, 0) / n;
  const ma = mean(ax);
  const mb = mean(bx);
  let num = 0;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i++) {
    num += (ax[i] - ma) * (bx[i] - mb);
    da += (ax[i] - ma) ** 2;
    db += (bx[i] - mb) ** 2;
  }
  if (da === 0 || db === 0) return null;
  const r = num / Math.sqrt(da * db);
  return Number.isFinite(r) ? r : null;
}

/**
 * Monthly PnL series keyed by month — the alignment unit for real
 * cross-strategy correlation (only months both strategies traded count).
 */
export function monthlyPnlMap(monthly: MonthlyBucket[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const b of monthly) m.set(b.month, b.pnl);
  return m;
}

/** Summarize a parsed result JSON into a ResultEntry (no trade payload). */
export function summarizeResultEntry(raw: unknown): ResultEntry | null {
  const parsed = parseResultJSON(raw);
  if (!parsed) return null;
  const md = parsed.metadata;
  const mx = parsed.metrics;
  return {
    file: "",
    source: "data/results",
    strategy_name: md.strategy_name ?? "",
    engine: md.engine ?? "",
    symbol: md.symbol ?? "",
    timeframe: md.timeframe ?? "",
    start_date: md.start_date ?? undefined,
    end_date: md.end_date ?? undefined,
    initial_balance: (mx.initial_balance ?? md.initial_balance) ?? undefined,
    final_balance: mx.final_balance ?? undefined,
    total_trades: parsed.trades.length,
    net_profit: mx.net_profit ?? undefined,
    sharpe_ratio: mx.sharpe_ratio ?? undefined,
    profit_factor: mx.profit_factor ?? undefined,
    win_rate_pct: mx.win_rate_pct ?? undefined,
    max_drawdown_pct: mx.max_drawdown_pct ?? undefined,
  };
}
