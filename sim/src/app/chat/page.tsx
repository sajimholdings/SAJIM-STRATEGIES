'use client';

import { useChat } from '@ai-sdk/react';
import { isTextUIPart, isToolUIPart } from 'ai';
import { Bot, Send } from 'lucide-react';
import { FormEvent, useEffect, useRef, useState } from 'react';

export default function Chat() {
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
    <div className="flex flex-col h-screen bg-neutral-950 text-neutral-200">
      <header className="px-6 py-4 border-b border-neutral-800 bg-neutral-900 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-emerald-500/10 text-emerald-500 rounded-lg">
            <Bot size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">SAJIM Quant Assistant</h1>
            <p className="text-xs text-neutral-400">Agentic AI Backtesting Engine</p>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages?.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-neutral-500 space-y-4">
            <Bot size={48} className="opacity-20" />
            <p className="text-center max-w-md text-sm">
              I am ready. Ask me to scan your strategies or run a backtest on a specific asset and timeframe.
              <br/><br/>
              <em>Example: &quot;Scan my folders and test the Backtrader Gold strategy on XAUUSD M15&quot;</em>
            </p>
          </div>
        ) : (
          messages?.map(m => (
            <div key={m.id} className={`flex gap-4 max-w-3xl mx-auto ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>

              {m.role !== 'user' && (
                <div className="w-8 h-8 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0 mt-1">
                  <Bot size={18} />
                </div>
              )}

              <div className={`px-5 py-3.5 rounded-2xl max-w-[85%] text-sm leading-relaxed ${
                m.role === 'user'
                  ? 'bg-emerald-600 text-white'
                  : 'bg-neutral-800/80 border border-neutral-700/50 text-neutral-300'
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
                          <div className="p-3 bg-neutral-900/50 rounded border border-neutral-700 font-mono">
                            <span className={`font-semibold flex items-center gap-1.5 ${failed ? 'text-red-400' : 'text-emerald-400'}`}>
                              {failed ? '✗' : '✓'} {toolName} {failed ? 'failed' : 'completed'}
                            </span>
                            {output?.error && (
                              <div className="mt-1.5 text-red-400/90 whitespace-pre-wrap">{output.error}</div>
                            )}
                            {output?.summary && (
                              <div className="mt-1.5 text-neutral-400">
                                {output.summary.total_trades ?? '—'} trades · net {output.summary.net_profit?.toFixed(2) ?? '—'}
                              </div>
                            )}
                            {output?.total_strategies !== undefined && (
                              <div className="mt-1.5 text-neutral-400">{output.total_strategies} strategies discovered</div>
                            )}
                          </div>
                        ) : (
                          <div className="p-3 bg-neutral-900/50 rounded border border-neutral-700 border-dashed text-neutral-400 font-mono flex items-center gap-2">
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
        <div className="px-6 py-2 bg-red-500/10 border-t border-red-500/20 text-red-400 text-xs font-mono">
          Error: {error.message}
        </div>
      )}

      <footer className="p-4 border-t border-neutral-800 bg-neutral-900">
        <form onSubmit={onSubmit} className="max-w-3xl mx-auto relative flex items-center">
          <input
            className="w-full bg-neutral-800 border border-neutral-700 text-white rounded-full py-4 pl-6 pr-14 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500 transition-all shadow-sm text-sm"
            value={input}
            placeholder="E.g. Test the SMC Fractal Daemon on EURUSD H1..."
            onChange={e => setInput(e.target.value)}
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="absolute right-2 p-2.5 bg-emerald-500 hover:bg-emerald-400 text-white rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Send size={18} className={isLoading ? "opacity-50" : ""} />
          </button>
        </form>
      </footer>
    </div>
  );
}
