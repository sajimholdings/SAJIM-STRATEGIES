'use client';
import { useEffect, useState, useRef } from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type LogEntry = {
  type: 'TRADE' | 'TRADE_RESULT' | 'INFO' | 'AI_UPDATE' | 'TICK';
  timestamp: string;
  asset: string;
  action?: string;
  price?: number;
  profit?: number;
  status?: string;
  message?: string;
  confidence?: string;
  multiplier?: number;
  fastSMA?: number;
  slowSMA?: number;
};

type AssetState = {
  price: number;
  prevPrice: number;
  velocity: number;
  acceleration: number;
  aiStatus: string;
  aiConfidence: string;
  multiplier: number;
  wins: number;
  losses: number;
  pnl: number;
  consecutiveLosses: number;
  cooldownActive: boolean;
  lastUpdate: number;
  priceHistory: number[];
};

const ASSETS = ['1HZ50V', '1HZ75V'];
const ASSET_COLORS: Record<string, { accent: string; glow: string; bg: string; border: string }> = {
  '1HZ10V': {
    accent: '#3b82f6',
    glow: 'rgba(59,130,246,0.3)',
    bg: 'linear-gradient(135deg, rgba(59,130,246,0.08) 0%, rgba(59,130,246,0.02) 100%)',
    border: 'rgba(59,130,246,0.25)',
  },
  '1HZ50V': {
    accent: '#f59e0b',
    glow: 'rgba(245,158,11,0.3)',
    bg: 'linear-gradient(135deg, rgba(245,158,11,0.08) 0%, rgba(245,158,11,0.02) 100%)',
    border: 'rgba(245,158,11,0.25)',
  },
  '1HZ75V': {
    accent: '#a855f7',
    glow: 'rgba(168,85,247,0.3)',
    bg: 'linear-gradient(135deg, rgba(168,85,247,0.08) 0%, rgba(168,85,247,0.02) 100%)',
    border: 'rgba(168,85,247,0.25)',
  },
};

const defaultAssetState = (): AssetState => ({
  price: 0,
  prevPrice: 0,
  velocity: 0,
  acceleration: 0,
  aiStatus: 'ACTIVE',
  aiConfidence: 'HIGH',
  multiplier: 1.0,
  wins: 0,
  losses: 0,
  pnl: 0,
  consecutiveLosses: 0,
  cooldownActive: false,
  lastUpdate: Date.now(),
  priceHistory: [],
});

// ---------------------------------------------------------------------------
// Sparkline SVG
// ---------------------------------------------------------------------------
function Sparkline({ data, color, width = 120, height = 32 }: { data: number[]; color: string; width?: number; height?: number }) {
  if (data.length < 2) return <div style={{ width, height }} />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} className="shrink-0">
      <defs>
        <linearGradient id={`spark-${color.replace('#','')}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Area fill */}
      <polygon
        points={`0,${height} ${points} ${width},${height}`}
        fill={`url(#spark-${color.replace('#','')})`}
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------
export default function Dashboard() {
  const [ws, setWs] = useState<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [time, setTime] = useState('');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [uptime, setUptime] = useState(0);
  const [assets, setAssets] = useState<Record<string, AssetState>>(() => {
    const m: Record<string, AssetState> = {};
    ASSETS.forEach((s) => (m[s] = defaultAssetState()));
    return m;
  });
  const bottomRef = useRef<HTMLDivElement>(null);
  const startTimeRef = useRef(Date.now());

  // Clock + uptime
  useEffect(() => {
    const t = setInterval(() => {
      setTime(new Date().toLocaleTimeString());
      setUptime(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, []);

  // WebSocket
  useEffect(() => {
    const socket = new WebSocket('ws://localhost:8080');
    socket.onopen = () => { setConnected(true); setWs(socket); };
    socket.onclose = () => { setConnected(false); setWs(null); };
    return () => socket.close();
  }, []);

  // Message handler
  useEffect(() => {
    if (!ws) return;
    const handler = (event: MessageEvent) => {
      try {
        const data: LogEntry = JSON.parse(event.data);

        if (data.type === 'TICK' && data.asset) {
          setAssets((prev) => {
            const old = prev[data.asset];
            const history = [...(old?.priceHistory || []), data.price!].slice(-60);
            return {
              ...prev,
              [data.asset]: {
                ...old,
                prevPrice: old?.price || data.price!,
                price: data.price!,
                velocity: data.fastSMA ?? 0,
                acceleration: data.slowSMA ?? 0,
                lastUpdate: Date.now(),
                priceHistory: history,
              },
            };
          });
        }

        if (data.type === 'AI_UPDATE' && data.asset) {
          setAssets((prev) => ({
            ...prev,
            [data.asset]: {
              ...prev[data.asset],
              aiStatus: data.status || 'ACTIVE',
              aiConfidence: data.confidence || 'HIGH',
              multiplier: data.multiplier ?? prev[data.asset]?.multiplier ?? 1.0,
            },
          }));
        }

        if (data.type === 'TRADE_RESULT' && data.asset) {
          const isWin = data.status === 'WIN';
          setAssets((prev) => {
            const a = prev[data.asset];
            const newConsecutive = isWin ? 0 : (a?.consecutiveLosses || 0) + 1;
            return {
              ...prev,
              [data.asset]: {
                ...a,
                wins: (a?.wins || 0) + (isWin ? 1 : 0),
                losses: (a?.losses || 0) + (isWin ? 0 : 1),
                pnl: (a?.pnl || 0) + (data.profit || 0),
                consecutiveLosses: newConsecutive,
                cooldownActive: newConsecutive >= 3,
              },
            };
          });
        }

        if (['TRADE', 'TRADE_RESULT', 'INFO', 'AI_UPDATE'].includes(data.type)) {
          setLogs((prev) => [...prev.slice(-300), data]);
        }
      } catch {}
    };
    ws.addEventListener('message', handler);
    return () => ws.removeEventListener('message', handler);
  }, [ws]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // Derived globals
  const globalPnL = Object.values(assets).reduce((s, a) => s + a.pnl, 0);
  const totalWins = Object.values(assets).reduce((s, a) => s + a.wins, 0);
  const totalLosses = Object.values(assets).reduce((s, a) => s + a.losses, 0);
  const totalTrades = totalWins + totalLosses;
  const globalWinRate = totalTrades > 0 ? ((totalWins / totalTrades) * 100).toFixed(1) : '0.0';
  const formatUptime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return `${h.toString().padStart(2,'0')}:${m.toString().padStart(2,'0')}:${sec.toString().padStart(2,'0')}`;
  };

  return (
    <div className="min-h-screen bg-[#070710] text-zinc-100 font-mono selection:bg-cyan-900">
      {/* ================================================================= */}
      {/* TOP BAR                                                           */}
      {/* ================================================================= */}
      <header className="border-b border-zinc-800/40 bg-[#0a0a14]/80 backdrop-blur-sm px-6 py-3 sticky top-0 z-50">
        <div className="flex items-center justify-between">
          {/* Left — Branding + Status */}
          <div className="flex items-center gap-5">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)] animate-pulse" />
              <h1 className="text-sm font-bold tracking-tight">
                DERIV<span className="text-emerald-400 ml-1">ENGINE</span>
              </h1>
            </div>
            <div className="h-4 w-px bg-zinc-800" />
            <div className="flex items-center gap-3 text-[11px] text-zinc-500">
              <StatusDot label="Node" ok={connected} />
              <StatusDot label="AI" ok={connected} />
              <span className="text-zinc-700">|</span>
              <span>Uptime {formatUptime(uptime)}</span>
            </div>
          </div>

          {/* Right — Clock */}
          <div className="text-sm tracking-[0.2em] text-zinc-400">{time}</div>
        </div>
      </header>

      <main className="px-6 py-5 space-y-5">
        {/* ================================================================= */}
        {/* GLOBAL METRICS BAR                                                */}
        {/* ================================================================= */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <GlobalMetricCard
            label="Session P&L"
            value={`${globalPnL >= 0 ? '+' : ''}$${globalPnL.toFixed(2)}`}
            accent={globalPnL >= 0 ? '#10b981' : '#ef4444'}
            sub={`${totalTrades} trades executed`}
          />
          <GlobalMetricCard
            label="Win Rate"
            value={`${globalWinRate}%`}
            accent="#3b82f6"
            sub={`${totalWins}W / ${totalLosses}L`}
          />
          <GlobalMetricCard
            label="Balance Impact"
            value={globalPnL >= 0 ? 'POSITIVE' : 'DRAWDOWN'}
            accent={globalPnL >= 0 ? '#10b981' : '#f59e0b'}
            sub="Relative to session start"
          />
          <GlobalMetricCard
            label="AI Regime"
            value={Object.values(assets).filter(a => a.aiStatus === 'ACTIVE').length + ' / 3 Active'}
            accent="#a855f7"
            sub="Assets cleared by Watchdog"
          />
        </div>

        {/* ================================================================= */}
        {/* ENGINE BAY — 3 Asset Telemetry Cards                              */}
        {/* ================================================================= */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {ASSETS.map((sym) => (
            <AssetCard key={sym} symbol={sym} state={assets[sym]} colors={ASSET_COLORS[sym]} />
          ))}
        </div>

        {/* ================================================================= */}
        {/* TERMINAL LEDGER                                                   */}
        {/* ================================================================= */}
        <div className="w-full h-[320px] bg-[#08080d] border border-zinc-800/30 rounded-lg flex flex-col text-[13px] overflow-hidden">
          <div className="px-4 py-2.5 border-b border-zinc-800/30 bg-[#0a0a14] text-zinc-500 text-[11px] flex justify-between items-center shrink-0">
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500/60" />
              <span className="uppercase tracking-[0.15em]">System Terminal</span>
            </div>
            <span className="text-zinc-700">{logs.length} events</span>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-0.5">
            {logs.length === 0 ? (
              <div className="text-zinc-700 text-xs">Waiting for trade executions…</div>
            ) : (
              logs.map((log, i) => <TerminalLine key={i} log={log} />)
            )}
            <div ref={bottomRef} />
          </div>
        </div>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusDot({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-1 text-[11px]">
      <div className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-500'}`} />
      <span className={ok ? 'text-zinc-400' : 'text-red-400'}>{label}</span>
    </div>
  );
}

function GlobalMetricCard({ label, value, accent, sub }: { label: string; value: string; accent: string; sub: string }) {
  return (
    <div
      className="rounded-lg border px-4 py-3 relative overflow-hidden"
      style={{
        background: `linear-gradient(135deg, ${accent}10 0%, transparent 60%)`,
        borderColor: `${accent}20`,
      }}
    >
      {/* Accent top bar */}
      <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ background: accent }} />
      <div className="text-[10px] text-zinc-500 uppercase tracking-[0.15em] mb-1">{label}</div>
      <div className="text-xl font-bold tabular-nums" style={{ color: accent }}>{value}</div>
      <div className="text-[10px] text-zinc-600 mt-1">{sub}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Asset Telemetry Card
// ---------------------------------------------------------------------------
function AssetCard({
  symbol,
  state,
  colors,
}: {
  symbol: string;
  state: AssetState;
  colors: { accent: string; glow: string; bg: string; border: string };
}) {
  const totalTrades = state.wins + state.losses;
  const winRate = totalTrades > 0 ? ((state.wins / totalTrades) * 100).toFixed(1) : '—';
  const priceUp = state.price >= state.prevPrice;
  const isActive = state.aiStatus === 'ACTIVE';

  let regimeLabel = 'UNKNOWN';
  let regimeColor = '#71717a';
  let regimeBg = 'rgba(113,113,122,0.1)';
  if (state.aiStatus === 'ACTIVE') {
    regimeLabel = 'TREND';
    regimeColor = '#10b981';
    regimeBg = 'rgba(16,185,129,0.1)';
  } else if (state.aiStatus === 'PAUSED') {
    regimeLabel = 'CHOP';
    regimeColor = '#ef4444';
    regimeBg = 'rgba(239,68,68,0.1)';
  }

  return (
    <div
      className="rounded-lg border overflow-hidden relative"
      style={{
        background: colors.bg,
        borderColor: colors.border,
      }}
    >
      {/* Accent top stripe */}
      <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ background: colors.accent }} />

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: `${colors.accent}15` }}>
        <div className="flex items-center gap-2.5">
          <div
            className="w-2 h-2 rounded-full"
            style={{
              background: isActive ? '#10b981' : '#ef4444',
              boxShadow: `0 0 8px ${isActive ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)'}`,
            }}
          />
          <h3 className="font-bold text-sm" style={{ color: colors.accent }}>{symbol}</h3>
        </div>
        <div className="flex items-center gap-2">
          {state.cooldownActive && (
            <span className="text-[9px] bg-red-500/15 text-red-400 border border-red-500/25 px-1.5 py-0.5 rounded uppercase tracking-wider">
              Cooldown
            </span>
          )}
          <span
            className="text-[9px] font-bold px-2 py-0.5 rounded uppercase tracking-wider"
            style={{ color: regimeColor, background: regimeBg, border: `1px solid ${regimeColor}30` }}
          >
            {regimeLabel}
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-3">
        {/* Price + Sparkline row */}
        <div className="flex items-end justify-between">
          <div>
            <div className="text-[9px] text-zinc-600 uppercase tracking-wider mb-0.5">Live Price</div>
            <div
              className="text-2xl font-bold tabular-nums transition-colors duration-200"
              style={{ color: priceUp ? '#10b981' : '#ef4444' }}
            >
              {state.price > 0 ? state.price.toFixed(2) : '—'}
            </div>
          </div>
          <Sparkline data={state.priceHistory} color={colors.accent} width={100} height={28} />
        </div>

        {/* Metrics grid */}
        <div className="grid grid-cols-4 gap-2 text-[11px]">
          <MiniMetric label="V" value={state.velocity.toFixed(3)} color="#fbbf24" />
          <MiniMetric label="A" value={state.acceleration.toFixed(3)} color="#22d3ee" />
          <MiniMetric label="Kelly" value={`${state.multiplier.toFixed(2)}x`} color={colors.accent} />
          <MiniMetric label="Streak" value={`${state.consecutiveLosses}L`} color={state.consecutiveLosses >= 2 ? '#ef4444' : '#71717a'} />
        </div>

        {/* PnL bar */}
        <div className="flex items-center justify-between py-2 px-3 rounded border" style={{ background: `${state.pnl >= 0 ? '#10b981' : '#ef4444'}08`, borderColor: `${state.pnl >= 0 ? '#10b981' : '#ef4444'}15` }}>
          <div className="flex items-center gap-4 text-xs">
            <div>
              <span className="text-zinc-600 text-[9px] mr-1">PnL</span>
              <span className="font-bold tabular-nums" style={{ color: state.pnl >= 0 ? '#10b981' : '#ef4444' }}>
                {state.pnl >= 0 ? '+' : ''}${state.pnl.toFixed(2)}
              </span>
            </div>
            <div>
              <span className="text-zinc-600 text-[9px] mr-1">WR</span>
              <span className="text-zinc-300 font-bold tabular-nums">{winRate}%</span>
            </div>
          </div>
          <div className="text-xs font-bold tabular-nums">
            <span style={{ color: '#10b981' }}>{state.wins}</span>
            <span className="text-zinc-700 mx-0.5">/</span>
            <span style={{ color: '#ef4444' }}>{state.losses}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function MiniMetric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="text-center">
      <div className="text-[8px] text-zinc-600 uppercase">{label}</div>
      <div className="font-bold tabular-nums text-[12px]" style={{ color }}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Terminal Line
// ---------------------------------------------------------------------------
function TerminalLine({ log }: { log: LogEntry }) {
  const timeStr = new Date(log.timestamp).toLocaleTimeString();

  if (log.type === 'TRADE') {
    return (
      <div className="flex gap-3 text-zinc-400 py-0.5">
        <span className="text-zinc-600 shrink-0 text-[11px]">{timeStr}</span>
        <span className="font-bold w-14 shrink-0 text-zinc-300">{log.asset}</span>
        <span className="text-emerald-500 w-8 shrink-0">{log.action}</span>
        <span className="text-zinc-500">@ {log.price?.toFixed(3)}</span>
      </div>
    );
  }

  if (log.type === 'TRADE_RESULT') {
    const isWin = log.status === 'WIN';
    return (
      <div className={`flex gap-3 font-bold py-0.5 ${isWin ? 'text-emerald-400' : 'text-red-500'}`}>
        <span className="shrink-0 text-[11px]">{timeStr}</span>
        <span className="w-14 shrink-0">{log.asset}</span>
        <span className="w-8 shrink-0">EXIT</span>
        <span>
          {log.profit! >= 0 ? '+' : ''}${log.profit?.toFixed(2)}
          <span className="text-[10px] ml-1 opacity-60">({log.status})</span>
        </span>
      </div>
    );
  }

  if (log.type === 'AI_UPDATE') {
    return (
      <div className="flex gap-3 text-purple-400/80 py-0.5">
        <span className="text-zinc-700 shrink-0 text-[11px]">{timeStr}</span>
        <span className="font-bold w-14 shrink-0">{log.asset}</span>
        <span className="text-purple-500">AI</span>
        <span>→ {log.status} <span className="text-zinc-600 text-[10px]">(Conf: {log.confidence})</span></span>
      </div>
    );
  }

  // INFO
  const isTrap = log.message?.includes('[TRAP]') || log.message?.includes('[BLOCKED]');
  const isPending = log.message?.includes('[PENDING]');
  let color = 'text-zinc-700';
  if (isTrap) color = 'text-orange-400/70';
  if (isPending) color = 'text-blue-400/50';

  return (
    <div className={`flex gap-3 py-0.5 ${color}`}>
      <span className="text-zinc-800 shrink-0 text-[11px]">{timeStr}</span>
      <span className="font-bold w-14 shrink-0">{log.asset}</span>
      <span className="truncate">{log.message}</span>
    </div>
  );
}
