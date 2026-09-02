#!/usr/bin/env node
// tv.mjs — minimal: launch headful CloakBrowser (reuses launch.mjs), inject the
// .env TradingView cookies, load the chart, report the logged-in account.
// Usage: node tv.mjs   (reads ./.env: SESSION / SIGNATURE / DEVICE_T)
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

// 1. .env
function parseEnv(p) {
  const out = {};
  for (const raw of readFileSync(p, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const i = line.indexOf('=');
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
const env = parseEnv(join(SCRIPT_DIR, '.env'));

// 2. ensure headful browser is up (launch.mjs detaches the browser, then exits)
spawnSync(process.execPath, [join(SCRIPT_DIR, 'launch.mjs')], { stdio: 'inherit' });

// 3. find the live CDP port
const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) })
  .then((r) => r.ok).catch(() => false);
let port = null;
for (let p = 9222; p < 9322 && !port; p++) {
  for (let i = 0; i < 20; i++) {
    if (await alive(p)) { port = p; break; }
    await new Promise((r) => setTimeout(r, 250));
  }
}
if (!port) throw new Error('no live CDP port');

// 4. first page target
const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
if (!page) throw new Error('no page target');

// 5. CDP over WebSocket (global in Node >= 22)
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
};
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id;
  pending.set(i, res);
  ws.send(JSON.stringify({ id: i, method, params }));
});

await send('Network.enable');
for (const [name, value] of [
  ['sessionid', env.SESSION],
  ['sessionid_sign', env.SIGNATURE],
  ['device_t', env.DEVICE_T],
]) {
  if (value) await send('Network.setCookie', { name, value, domain: '.tradingview.com', path: '/' });
}

await send('Page.navigate', { url: 'https://www.tradingview.com/chart/' });

// 6. poll for the logged-in account (same probe as visual)
const ACCOUNT_JS = `(function(){
  var b = document.querySelector('[aria-label^="Logged in as"]');
  return b ? (b.getAttribute('aria-label') || '').split('\\n')[0].replace('Logged in as ', '').trim() : null;
})()`;
let user = null;
for (let i = 0; i < 40; i++) {
  await new Promise((r) => setTimeout(r, 1000));
  const r = await send('Runtime.evaluate', { expression: ACCOUNT_JS, returnByValue: true });
  if (r.result?.result?.value) { user = r.result.result.value; break; }
}
console.log(user ? `\nAUTH OK — logged in as: ${user}` : '\nAUTH UNKNOWN — no "Logged in as" found');
process.exit(0);