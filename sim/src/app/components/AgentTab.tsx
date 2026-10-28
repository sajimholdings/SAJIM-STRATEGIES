'use client';

import { useChat } from '@ai-sdk/react';
import { isTextUIPart, isToolUIPart } from 'ai';
import { Bot, Send } from 'lucide-react';
import { FormEvent, useEffect, useRef, useState } from 'react';

export default function AgentTab() {
  const [input, setInput] = useState('');

  // AI SDK v7: default transport targets /api/chat; tool loops are bounded
  // server-side via stopWhen: stepCountIs(10).
  const { messages, sendMessage, status, error } = useChat();
  const isLoading = status === 'submitted' || status === 'streaming';

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!input.trim()) return;
    const value = input;
    setInput('');
    sendMessage({ text: value });
  };

  return (
    <div className="flex flex-col h-[75vh] bg-slate-900/40 border border-slate-800/80 rounded-xl overflow-hidden shadow-2xl">
      <header className="px-6 py-4 border-b border-slate-800 bg-slate-900/80 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-emerald-500/10 text-emerald-400 rounded-lg border border-emerald-500/20">
            <Bot size={20} />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white tracking-tight flex items-center gap-2">
                DeepSeek Backtest Agent <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">ONLINE</span>
            </h2>
            <p className="text-[11px] text-slate-400">Autonomous strategy scanning and CLI execution</p>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages?.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500 space-y-4">
            <Bot size={48} className="opacity-20 text-emerald-500" />
            <p className="text-center max-w-md text-sm text-slate-400">
              I am ready to run tests via the Python engine.
              <br/><br/>
              <em>Example: &quot;Scan my folders and test the Backtrader Gold strategy on XAUUSD M15&quot;</em>
            </p>
          </div>
        ) : (
          messages?.map(m => (
            <div key={m.id} className={`flex gap-4 max-w-3xl mx-auto ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>

              {m.role !== 'user' && (
                <div className="w-8 h-8 rounded-full bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 flex items-center justify-center shrink-0 mt-1 shadow-[0_0_10px_rgba(16,185,129,0.2)]">
                  <Bot size={16} />
                </div>
              )}

              <div className={`px-5 py-3.5 rounded-2xl max-w-[85%] text-[13px] leading-relaxed shadow-sm ${
                m.role === 'user'
                  ? 'bg-emerald-600/90 text-white border border-emerald-500/50'
                  : 'bg-slate-800/80 border border-slate-700/50 text-slate-300'
              }`}>
                {m.parts.map((part, i) => {
                  if (isTextUIPart(part)) {
                    return (
                      <div key={i} className="whitespace-pre-wrap">
                        {part.text}
                      </div>
                    );
                  }

                  if (isToolUIPart(part)) {
                    const toolName = part.type.startsWith('tool-')
                      ? part.type.slice('tool-'.length)
                      : part.type;
                    const finished = part.state === 'output-available' || part.state === 'output-error';
                    const failed = part.state === 'output-error';
                    const output = part.state === 'output-available'
                      ? (part.output as { summary?: { total_trades?: number; net_profit?: number }; error?: string; total_strategies?: number } | undefined)
                      : undefined;

                    return (
                      <div key={`${part.toolCallId}-${i}`} className="mt-3 text-xs">
                        {finished ? (
                          <div className="p-3 bg-slate-900/80 rounded border border-slate-700/80 font-mono shadow-inner">
                            <span className={`font-semibold flex items-center gap-1.5 ${failed ? 'text-red-400' : 'text-emerald-400'}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${failed ? 'bg-red-400' : 'bg-emerald-400'}`}></span>
                              {failed ? '✗' : '✓'} {toolName} {failed ? 'failed' : 'completed'}
                            </span>
                            {output?.error && (
                              <div className="mt-1.5 text-red-400/90 whitespace-pre-wrap">{output.error}</div>
                            )}
                            {output?.summary && (
                              <div className="mt-1.5 text-slate-400">
                                {output.summary.total_trades ?? '—'} trades · net {output.summary.net_profit?.toFixed(2) ?? '—'}
                              </div>
                            )}
                            {output?.total_strategies !== undefined && (
                              <div className="mt-1.5 text-slate-400">{output.total_strategies} strategies discovered</div>
                            )}
                          </div>
                        ) : (
                          <div className="p-3 bg-slate-900/50 rounded border border-slate-700/50 border-dashed text-slate-400 font-mono flex items-center gap-2">
                            <span className="animate-spin text-emerald-500">⚙</span>
                            Executing {toolName}...
                          </div>
                        )}
                      </div>
                    );
                  }

                  return null;
                })}
              </div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </main>

      {error && (
        <div className="px-6 py-3 bg-red-500/10 border-t border-red-500/20 text-red-300 text-xs font-mono space-y-1">
          <div className="font-semibold text-red-400">API Notice: {error.message}</div>
          {error.message.toLowerCase().includes('balance') || error.message.includes('402') ? (
            <div className="text-slate-400 text-[11px]">
              The configured API provider returned Insufficient Balance (HTTP 402). Top up credit at <span className="text-emerald-400 underline">platform.deepseek.com</span> or set an active key in <code className="bg-slate-800 px-1 py-0.5 rounded text-amber-300">sim/.env.local</code>.
            </div>
          ) : null}
        </div>
      )}

      <footer className="p-4 border-t border-slate-800 bg-slate-900/60 backdrop-blur-md">
        <form onSubmit={onSubmit} className="max-w-3xl mx-auto relative flex items-center">
          <input
            className="w-full bg-slate-950/80 border border-slate-700/80 text-white rounded-xl py-3.5 pl-5 pr-14 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500/80 transition-all shadow-inner text-sm placeholder:text-slate-500"
            value={input}
            placeholder="Instruct the agent (e.g. run a test on EURUSD)..."
            onChange={e => setInput(e.target.value)}
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="absolute right-2 p-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed shadow-md shadow-emerald-900/20"
          >
            <Send size={16} className={isLoading ? "opacity-50" : ""} />
          </button>
        </form>
      </footer>
    </div>
  );
}
