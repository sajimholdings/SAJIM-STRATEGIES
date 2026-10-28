/**
 * Shapes of the strategy-universe matrix JSON produced by
 * `sajim_quant.analytics.real_matrix.RealMatrixBuilder`
 * (`python -m sajim_quant matrix`).
 *
 * Every field is optional because a node reports honestly when it could not be
 * evaluated: `status` carries NO_DATA, PENDING_ADAPTER or ERROR and the metric
 * fields are then absent or null. Ratios are null rather than 0 when their
 * denominator is zero (profit factor with no losing trades, Calmar on a run too
 * short to annualize), so render them as "n/a" instead of coercing to a number.
 */

import type { MonteCarloPayload } from "./results";

/** Automated qualitative rationale attached to a node by the diagnostics engine. */
export type NodeDiagnosis = {
  headline?: string;
  regime_classification?: string;
  structural_cause?: string;
  mechanics_detail?: string;
  recommendation?: string;
  edge_rating?: string;
};

/** One strategy x symbol x timeframe cell. */
export type UniverseNode = {
  strategy_id?: string;
  strategy_name?: string;
  symbol: string;
  timeframe: string;
  /** OPTIMAL | ELITE | VIABLE | MARGINAL | DEGRADED | TOXIC_DD | NO_DATA | PENDING_ADAPTER | ERROR */
  status?: string;
  data_source?: string;
  asset_class?: string;
  rank?: number;
  trades?: number;
  win_rate_pct?: number;
  profit_factor?: number | null;
  net_pnl?: number;
  max_drawdown_pct?: number;
  max_drawdown_pct_closed?: number | null;
  drawdown_includes_intratrade?: boolean;
  sharpe_ratio?: number;
  sortino_ratio?: number | null;
  calmar_ratio?: number | null;
  cagr_pct?: number | null;
  span_days?: number | null;
  expectancy?: number;
  avg_win?: number;
  avg_loss?: number;
  winning_trades?: number;
  losing_trades?: number;
  optimal?: boolean;
  /** RunMetadata.notes — the execution assumptions this node was priced under. */
  assumptions?: string;
  error?: string | null;
  from_cache?: boolean;
  diagnosis?: NodeDiagnosis;
  monte_carlo?: MonteCarloPayload;
};

/** One strategy's full sweep across the symbol x timeframe grid. */
export type UniverseStrategy = {
  id: string;
  name: string;
  adapter?: string;
  folder?: string;
  engine?: string;
  tier?: string;
  description?: string;
  source?: string;
  total_combinations_tested?: number;
  profitable_combinations?: number;
  universality_pct?: number;
  peak_combination?: UniverseNode | null;
  worst_combination?: UniverseNode | null;
  heatmap?: Record<string, Record<string, number>>;
  ranked_combinations?: UniverseNode[];
};

/** The `/api/strategy-universe` response envelope. */
export type UniverseResponse = {
  success: boolean;
  generated_at?: string;
  /** "REAL_BACKTEST" for a genuine build. Anything else deserves scrutiny. */
  build_mode?: string | null;
  total_nodes?: number | null;
  completed_nodes?: number | null;
  symbols?: string[];
  timeframes?: string[];
  strategies: UniverseStrategy[];
  error?: string;
};
