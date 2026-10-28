import { createOpenAI } from '@ai-sdk/openai';
import { stepCountIs, streamText, tool } from 'ai';
import { z } from 'zod';
import { exec } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs/promises';
import {
  NOT_CONFIGURED_MESSAGE,
  modalApiConfig,
  saveResultLocally,
  startBacktest,
  waitForJob,
  type BacktestJobResult,
} from '../../../lib/modalApi';
import { rejectCrossSite } from '../../../lib/requestGuard';

const execAsync = promisify(exec);

// Backtests can take minutes (e.g. sunrise_ogle on XAUUSD M5 measured ~4 min),
// so the route budget must exceed the subprocess timeout below.
export const maxDuration = 300;

// Kill a runaway backtest subprocess after this many seconds.
const BACKTEST_TIMEOUT_MS = 240_000;

// Shapes of the JSON payloads read from disk by the tools.
type CatalogEntry = {
  name: string;
  source_file: string;
  engine_type: string;
  symbol_hint: string;
  timeframe_hint: string;
};
type TradeRow = { net_pnl?: number; [key: string]: unknown };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function POST(req: Request) {
  const crossSite = rejectCrossSite(req);
  if (crossSite) return crossSite;
  const { messages } = await req.json();

  // Provider-agnostic: works with OpenAI or any OpenAI-compatible API (e.g. DeepSeek).
  // Configure via sim/.env.local:
  //   OPENAI_API_KEY   — your provider key
  //   OPENAI_BASE_URL  — e.g. https://api.deepseek.com/v1 for DeepSeek
  //   OPENAI_MODEL     — e.g. 'deepseek-chat' for DeepSeek (defaults to 'gpt-4o')
  // NOTE: .chat() targets the Chat Completions API, which OpenAI-compatible
  // providers (DeepSeek) implement; the default provider model uses OpenAI's
  // Responses API, which they do not.
  const provider = createOpenAI({
    apiKey: process.env.OPENAI_API_KEY,
    baseURL: process.env.OPENAI_BASE_URL,
  });
  const modelName = process.env.OPENAI_MODEL || 'gpt-4o';

  const result = streamText({
    model: provider.chat(modelName),
    messages,
    // Allow up to 10 model steps so multi-tool loops complete (scan → backtest →
    // analysis). v7 defaults to stepCountIs(1), which cuts the stream off right
    // after the first tool executes.
    stopWhen: stepCountIs(10),
    system: `You are SAJIM Quant, an elite AI quantitative researcher and trading assistant.
You help the user backtest trading strategies, analyze performance metrics, and diagnose issues.
You have access to tools that can scan for strategies and run backtests directly on the user's machine.
When you run a backtest, wait for the result, read the JSON output, and provide a comprehensive analysis of the Sharpe ratio, max drawdown, and win rate.
Be highly critical of toxic drawdowns (like martingale grids) and overfitting.`,
    tools: {
      scan_strategies: tool({
        description: 'Scans the workspace and lists all available trading strategies that can be backtested.',
        parameters: z.object({
          force: z.boolean().optional().describe('Force a fresh rescan of the directories'),
        }),
        // @ts-expect-error - Vercel AI SDK typing mismatch
        execute: async () => {
          try {
            console.log("Scanning strategies...");
            // Run the python scan command
            await execAsync('python -m sajim_quant scan', {
              cwd: path.resolve(process.cwd(), '../'), // Go up to SAJIM STRATEGIES root
              timeout: 120_000,
            });

            // Read the generated catalog
            const catalogPath = path.resolve(process.cwd(), '../data/discovered_strategies.json');
            const data = await fs.readFile(catalogPath, 'utf-8');
            const parsed = JSON.parse(data) as CatalogEntry[];
            // Return only essential details to save context length
            return {
              total_strategies: parsed.length,
              strategies: parsed.map((s) => ({ name: s.name, source: s.source_file, engine: s.engine_type, symbol: s.symbol_hint, tf: s.timeframe_hint })).slice(0, 50),
              note: parsed.length > 50 ? "List truncated to top 50 to save context." : ""
            };
          } catch (error) {
            return { error: errorMessage(error) };
          }
        },
      }),
      
      run_backtest: tool({
        description: 'Runs a historical backtest on Modal cloud for a catalog strategy (by catalog id or source_file path), symbol, and timeframe. MT5 Expert Advisors cannot run on Modal.',
        parameters: z.object({
          strategy_path: z.string().describe('Catalog id (e.g. smc_daemon_live, beep_vacuum_hunter, sunrise_ogle_xauusd, beep_baseline_follower) or its source_file path'),
          symbol: z.string().describe('The trading asset symbol (e.g. XAUUSD, EURUSD)'),
          timeframe: z.string().describe('The timeframe to test on (e.g. M15, H1)'),
        }),
        // @ts-expect-error - Vercel AI SDK typing mismatch
        execute: async ({ strategy_path, symbol, timeframe }) => {
          try {
            // SECURITY: these strings come from the model and are interpolated into a
            // shell command. Strictly whitelist characters and reject path traversal
            // or absolute paths before anything reaches exec.
            const SAFE_NAME = /^[A-Za-z0-9_]+$/;
            const SAFE_REL_PATH = /^[A-Za-z0-9_.\-/ ]+$/;
            if (!SAFE_REL_PATH.test(strategy_path) || strategy_path.includes('..') || path.isAbsolute(strategy_path)) {
              return { error: `Invalid strategy_path "${strategy_path}". Must be a relative path inside the workspace.` };
            }
            if (!SAFE_NAME.test(symbol) || !SAFE_NAME.test(timeframe)) {
              return { error: `Invalid symbol/timeframe. Only alphanumeric and underscore characters are allowed.` };
            }

            // Backtests run on Modal (data in the sajim-market-data volume) to keep
            // compute off this machine. The runner takes a catalog id, so map the path.
            const rootPath = path.resolve(process.cwd(), '../');
            const catalog = JSON.parse(
              await fs.readFile(path.join(rootPath, 'sajim_quant/core/strategy_catalog.json'), 'utf-8')
            ) as { id: string; source_file?: string; run_adapter?: string }[];
            const norm = (p: string) => p.replace(/\\/g, '/').replace(/^\.\//, '').toLowerCase();
            const entry = catalog.find((c) => c.id === strategy_path || (c.source_file && norm(c.source_file) === norm(strategy_path)));
            if (!entry) {
              return {
                error: `"${strategy_path}" is not in the strategy catalog, so it cannot be dispatched to Modal.`,
                runnable_on_modal: catalog.filter((c) => c.run_adapter !== 'mt5').map((c) => ({ id: c.id, source_file: c.source_file })),
              };
            }
            if (entry.run_adapter === 'mt5') {
              return { error: `${entry.id} is an MT5 Expert Advisor — it needs the Windows MetaTrader terminal and cannot run on Modal. Ask the user to run it from the dashboard's Local engine.` };
            }

            console.log(`Running backtest on Modal: ${entry.id} on ${symbol} ${timeframe}`);
            if (!modalApiConfig()) return { error: NOT_CONFIGURED_MESSAGE };
            // Detached Modal job, started + polled over HTTP (same path as the dashboard's Run button).
            const started = await startBacktest({ strategy_id: entry.id, symbol, timeframe });
            const job = await waitForJob<BacktestJobResult>(started.job_id, { timeoutMs: BACKTEST_TIMEOUT_MS });
            const res = job.result;
            if (job.status !== 'done' || !res?.success || !res.data) {
              return {
                error: `Modal backtest failed: ${job.error ?? res?.error ?? 'no result payload'}. Nothing was run locally.`,
                job_id: started.job_id,
              };
            }
            const file = res.file ?? `${entry.id}_${symbol}_${timeframe}_result.json`;
            saveResultLocally(file, res.data); // data/results + sim/public/test_results, like the dashboard
            const parsed = res.data as { metadata?: unknown; metrics?: Record<string, unknown>; trades?: TradeRow[] };

            // AI context optimization: remove the massive trades array or summarize it
            const tradeCount = parsed.trades ? parsed.trades.length : 0;
            const netProfit = parsed.trades
              ? parsed.trades.reduce((sum: number, t: TradeRow) => sum + (t.net_pnl ?? 0), 0)
              : 0;

            // Send back metadata, summary metrics, and only the first/last few trades if any
            return {
              metadata: parsed.metadata,
              summary: {
                total_trades: tradeCount,
                net_profit: netProfit,
              },
              metrics: parsed.metrics || {},
              note: tradeCount > 0
                ? "Detailed trades array omitted to save context window. The backtest completed successfully."
                : "The backtest completed with 0 trades. Check the strategy inputs, data availability (data/parquet), and date range.",
            };
          } catch (error) {
            console.error("Backtest Error:", error);
            return { error: errorMessage(error) };
          }
        },
      }),
    },
  });

  return result.toUIMessageStreamResponse();
}
