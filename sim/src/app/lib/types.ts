// Shapes produced by `sajim export` (sajim_quant/registry/export.py). Keep in sync with Python.

export type Tier =
  | "TIER 1: ELITE"
  | "TIER 2: VIABLE"
  | "TIER 2: MARGINAL"
  | "TIER 3: INSUFFICIENT DATA"
  | "TIER 3: UNPROFITABLE"
  | "TIER 3: TOXIC DD"
  | "UNTESTED"
  | string;

export interface MonteCarloSummary {
  num_simulations?: number;
  worst_drawdown_95_pct?: number;
  worst_drawdown_99_pct?: number;
  risk_of_ruin_20_pct?: number;
  fragility_index?: number;
  is_robust?: boolean;
  terminal_median_balance?: number;
}

export interface RegistryRun {
  run_id: string;
  strategy_id: string;
  name: string;
  folder: string;
  engine: string;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  initial_balance: number;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  net_pnl: number;
  return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  tier: Tier;
  edge_rating: string;
  description: string;
  tags: string[];
  assumptions: Record<string, unknown>;
  monte_carlo_summary: MonteCarloSummary;
  artifacts: Record<string, string>;
  source: "registry" | "legacy_manual" | string;
  primary: boolean;
  created_at: string;
}

export interface RegistryStrategy {
  strategy_id: string;
  name: string;
  folder: string;
  engine: string;
  description: string;
  runs: RegistryRun[];
  primary_run_id?: string;
  tier?: Tier;
}

export interface RegistryExport {
  last_updated: string;
  total_strategies: number;
  total_runs: number;
  strategies: RegistryStrategy[];
}

export interface LeaderboardRow {
  id: string;
  rank: number;
  strategy_id: string;
  run_id: string;
  name: string;
  folder: string;
  engine: string;
  symbol: string;
  timeframe: string;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  net_pnl: number;
  return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  tier: Tier;
  edge_rating: string;
  description: string;
  tear_sheet_path: string | null;
  has_artifacts: boolean;
  source: string;
  start_date: string;
  end_date: string;
  created_at: string;
  monte_carlo_summary: MonteCarloSummary;
}

export interface LeaderboardExport {
  last_updated: string;
  total_strategies: number;
  strategies: LeaderboardRow[];
}

export interface CorrelationExport {
  generated_at: string;
  strategies: string[];
  matrix: Record<string, Record<string, number>>;
  diversification_ratio: number | null;
  portfolio_sharpe?: number;
  portfolio_max_drawdown_pct?: number;
  note: string;
}

export interface EquityPoint {
  time: string;
  equity: number;
  drawdown_pct: number;
  trade_id?: string;
}

export interface Metrics {
  strategy_name: string;
  engine: string;
  symbol: string;
  timeframe: string;
  initial_balance: number;
  final_balance: number;
  net_profit: number;
  return_pct: number;
  cagr_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  profit_factor: number;
  expectancy: number;
  avg_trade_pnl: number;
  avg_win: number;
  avg_loss: number;
  win_loss_ratio: number;
  max_drawdown_amount: number;
  max_drawdown_pct: number;
  recovery_factor: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  avg_duration_minutes: number;
  total_commission: number;
  total_swap: number;
  avg_mae: number | null;
  avg_mfe: number | null;
  equity_series: EquityPoint[];
  monthly_returns: Record<string, Record<string, number>>;
  tier: Tier;
}

export interface MonteCarlo {
  num_simulations: number;
  num_trades: number;
  initial_balance: number;
  trade_steps: number[];
  p5_curve: number[];
  p25_curve: number[];
  p50_curve: number[];
  p75_curve: number[];
  p95_curve: number[];
  historical_max_dd_pct: number;
  expected_max_dd_median_pct: number;
  worst_drawdown_95_pct: number;
  worst_drawdown_99_pct: number;
  risk_of_ruin_10_pct: number;
  risk_of_ruin_20_pct: number;
  risk_of_ruin_30_pct: number;
  risk_of_ruin_50_pct: number;
  terminal_median_balance: number;
  terminal_p5_balance: number;
  terminal_p95_balance: number;
  fragility_index: number;
  is_robust: boolean;
}

export interface Diagnosis {
  headline: string;
  regime_classification: string;
  structural_cause: string;
  mechanics_detail: string;
  recommendation: string;
  edge_rating: string;
}

export interface RunDetail {
  run: RegistryRun;
  metrics: Metrics | null;
  monte_carlo: MonteCarlo | null;
  diagnosis: Diagnosis | null;
  tear_sheet_md: string | null;
}

export interface UniverseCombo {
  strategy_id: string;
  run_id: string;
  symbol: string;
  asset_class: string;
  timeframe: string;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  net_pnl: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  tier: Tier;
  optimal: boolean;
  status: string;
  source: string;
  diagnosis: Diagnosis | null;
  monte_carlo: MonteCarlo | null;
  rank: number;
}

export interface UniverseStrategy {
  id: string;
  name: string;
  folder: string;
  engine: string;
  tier: Tier;
  description: string;
  grid_cells: number;
  total_combinations_tested: number;
  profitable_combinations: number;
  universality_pct: number;
  coverage_pct: number;
  peak_combination: Pick<UniverseCombo, "symbol" | "timeframe" | "sharpe_ratio" | "profit_factor" | "net_pnl" | "win_rate_pct" | "max_drawdown_pct"> | null;
  worst_combination: Pick<UniverseCombo, "symbol" | "timeframe" | "sharpe_ratio" | "profit_factor" | "net_pnl" | "win_rate_pct" | "max_drawdown_pct"> | null;
  heatmap: Record<string, Record<string, number | null>>;
  ranked_combinations: UniverseCombo[];
}

export interface UniverseExport {
  generated_at: string;
  symbols: string[];
  timeframes: string[];
  strategies: UniverseStrategy[];
}

export interface RunRef {
  strategyId: string;
  runId: string;
}
