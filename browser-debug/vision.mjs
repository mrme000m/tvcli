#!/usr/bin/env node
// vision.mjs — describe TradingView UI screenshots with a Mistral vision model.
//
// Reads MISTRAL_API_KEY from the environment (export it or put it in your shell rc).
// Local PNGs are base64-encoded and sent to https://api.mistral.ai/v1/chat/completions.
//
// Usage:
//   node vision.mjs shots/maxchart_after.png                 # describe one screenshot
//   node vision.mjs shots/before.png shots/after.png         # what changed between two
//   node vision.mjs --live "describe the chart interface"    # grab a fresh CDP screenshot first
//   node vision.mjs shots/x.png --model mistral-large-2512   # override model
//
// Default model: mistral-small-2506 (Small 3.2, cheap + strong at UI description).
// Vision-capable: mistral-large-2512, mistral-medium-2508, mistral-small-2506,
// ministral-14b-2512, ministral-8b-2512, ministral-3b-2512.

import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

// API key: env var first, then falls back to ./.env (browser-debug/.env is the
// canonical home for MISTRAL_API_KEY in this workspace).
const keyFromEnv = (p) => { try { return readFileSync(p, 'utf8').split('\n').find((l) => l.startsWith('MISTRAL_API_KEY='))?.slice('MISTRAL_API_KEY='.length).trim(); } catch { return undefined; } };
const apiKey = process.env.MISTRAL_API_KEY || keyFromEnv(join(SCRIPT_DIR, '.env'));
if (!apiKey) { console.error('MISTRAL_API_KEY not set — export it or add it to browser-debug/.env'); process.exit(1); }

const MODEL = 'mistral-small-2506';
const args = process.argv.slice(2);
const modelIdx = args.indexOf('--model');
const model = modelIdx >= 0 ? args[modelIdx + 1] : MODEL;
if (modelIdx >= 0) args.splice(modelIdx, 2);

const live = args[0] === '--live';
if (live) args.shift();
const paths = args.filter((a) => existsSync(a));
const question = args.filter((a) => !existsSync(a)).join(' ');
const prompt = question.trim() || (paths.length > 1
  ? 'These are before/after screenshots of a web interface around a programmatic interaction. What changed between them, and what is the current UI state?'
  : 'Describe the web interface in this screenshot.');



// --- optional live CDP screenshot (same connection pattern as the other scripts) ---
let localPaths = [...paths];
if (live) {
  const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) }).then((r) => r.ok).catch(() => false);
  let port = null;
  for (let p = 9222; p < 9322 && !port; p++) {
    for (let i = 0; i < 20 && !port; i++) { if (await alive(p)) { port = p; break; } await new Promise((r) => setTimeout(r, 250)); }
  }
  if (!port) { console.error('no live CDP port — start the browser first: node tv.mjs'); process.exit(1); }
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
  const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) { console.error('no page target'); process.exit(1); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const r = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  if (!r.result?.data) { console.error('screenshot failed'); process.exit(1); }
  const f = join(tmpdir(), `vision_live_${Date.now()}.png`);
  writeFileSync(f, Buffer.from(r.result.data, 'base64'));
  localPaths = [f];
}

if (!localPaths.length) { console.error('usage: vision.mjs <image.png> [image2.png] [question] | --live [question] | --model <id>'); process.exit(1); }

// --- build message: text + one image per path ---
const content = [{ type: 'text', text: prompt }];
for (const p of localPaths) {
  const b64 = readFileSync(p).toString('base64');
  content.push({ type: 'image_url', image_url: `data:image/png;base64,${b64}` });
}

// --- call Mistral ---
const res = await fetch('https://api.mistral.ai/v1/chat/completions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
  body: JSON.stringify({ model, messages: [{ role: 'user', content }], max_tokens: 700 }),
});
if (!res.ok) { console.error(`API ${res.status}: ${(await res.text()).slice(0, 300)}`); process.exit(1); }
const data = await res.json();
console.log(data.choices?.[0]?.message?.content?.trim() || '(empty response)');