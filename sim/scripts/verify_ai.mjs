// Verifies the AI provider wiring for the SAJIM dashboard chat without a dev server.
// Reads sim/.env.local (Next.js loads this in real runs), then does one streaming
// request through the exact same provider/SDK stack the chat route uses.
// Usage: node scripts/verify_ai.mjs
import { readFileSync } from 'node:fs';

// Parse .env.local BEFORE importing the SDK — the default provider captures
// env-based settings at import time.
const env = {};
for (const line of readFileSync(new URL('../.env.local', import.meta.url), 'utf8').split('\n')) {
  const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
  if (m && !m[1].startsWith('#')) env[m[1]] = m[2].trim();
}
if (!env.OPENAI_API_KEY) {
  console.error('FAIL: OPENAI_API_KEY missing in .env.local');
  process.exit(1);
}
process.env.OPENAI_API_KEY = env.OPENAI_API_KEY;
if (env.OPENAI_BASE_URL) process.env.OPENAI_BASE_URL = env.OPENAI_BASE_URL;
const modelName = env.OPENAI_MODEL || 'gpt-4o';

const { createOpenAI } = await import('@ai-sdk/openai');
const { streamText } = await import('ai');

// Mirror what the chat route does: explicit provider settings + .chat() model.
const provider = createOpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  baseURL: process.env.OPENAI_BASE_URL,
});

console.log(`Provider baseURL: ${process.env.OPENAI_BASE_URL || '(provider default)'}`);
console.log(`Model: ${modelName} (chat completions)`);

try {
  const result = streamText({
    model: provider.chat(modelName),
    messages: [{ role: 'user', content: 'Reply with exactly: OK' }],
    system: 'You are a connectivity test harness.',
  });
  const resp = result.toTextStreamResponse();
  console.log(`HTTP status: ${resp.status}`);
  const body = await resp.text();
  console.log(`Body preview: ${body.slice(0, 600)}`);
  if (resp.status !== 200 || !body.includes('OK')) {
    console.error('FAIL: unexpected response');
    process.exit(1);
  }
  console.log('PASS: chat provider responds correctly');
} catch (e) {
  console.error('FAIL:', e.message);
  process.exit(1);
}
