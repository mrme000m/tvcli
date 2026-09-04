#!/usr/bin/env node
// wt.mjs — WunderTrading headful session + programmatic web-API CLI.
//
// The WunderTrading cabinet's trader XHR surface (/en/trader/…) is session-
// authenticated (PHPSESSID) AND Cloudflare-fingerprinted: raw HTTP clients
// get a "Just a moment…" 403 even with valid cookies (verified 2026-09-04).
// The programmatic way in is therefore fetch() executed INSIDE the logged-in
// headful browser page over CDP — real browser TLS + cookies, fully
// scriptable. This CLI wraps that.
//
// Usage:
//   node wt.mjs                          # launch + restore session, open grid_bots
//   node wt.mjs open <url>               # launch + restore session, open <url>
//   node wt.mjs api <METHOD> <path> [bodyJSON]   # session-auth API call via page fetch
//   node wt.mjs eval '<js expression>'    # eval JS in page (awaitPromise, JSON out)
//   node wt.mjs shot [file.png]           # screenshot (default shots/wt-<ts>.png)
//
// Session cookies: ./secrets/runtime/wt-session.env (vault item
// `wundertrading-session`, materialized by bw-provision.sh — see
// secrets/manifest.json). Never print cookie values.
//
// Examples:
//   node wt.mjs api GET /en/trader/grid_bots/presets?page=1\&limit=5
//   node wt.mjs api GET /en/trader/grid_bots/upsert | jq '.data.activeGridBots'
//   node wt.mjs eval 'document.title'
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_URL = 'https://wundertrading.com/en/trader/grid_bots';
const WT_ORIGIN = 'https://wundertrading.com';

const [cmd, ...rest] = process.argv.slice(2);
const command = (cmd && !cmd.startsWith('http') && !['open', 'api', 'eval', 'shot'].includes(cmd))
  ? null // treat as URL or default
  : cmd;
const USAGE = `usage: node wt.mjs [open <url> | api <METHOD> <path> [bodyJSON] | eval '<js>' | shot [file] | <url>]`;

// ── session env (vault materialized) ────────────────────────────────────────
function parseEnv(p) {
  const out = {};
  if (!existsSync(p)) return out;
  for (const raw of readFileSync(p, 'utf8').split('\n')) {
    const line = raw.trim().replace(/\r$/, '');
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const i = line.indexOf('=');
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
const sess = parseEnv(join(SCRIPT_DIR, 'secrets/runtime/wt-session.env'));

let cookies = [];
if (sess.WT_COOKIES_JSON) {
  try { cookies = JSON.parse(sess.WT_COOKIES_JSON); } catch { /* fall through */ }
}
if (!cookies.length && (sess.WT_PHPSESSID || sess.WT_CF_CLEARANCE)) {
  const mk = (name, value, domain) => ({ name, value, domain, path: '/', secure: true, httpOnly: true });
  if (sess.WT_PHPSESSID) cookies.push(mk('PHPSESSID', sess.WT_PHPSESSID, 'wundertrading.com'));
  if (sess.WT_CF_CLEARANCE) cookies.push(mk('cf_clearance', sess.WT_CF_CLEARANCE, '.wundertrading.com'));
}

// cookie restoration is only needed when the browser is freshly launched
// (the profile persists cookies across restarts); we always (re)assert the
// auth cookies so a stale-profile edge case cannot silently log us out.
function cookieList() { return cookies; }

// ── ensure headful browser is up ────────────────────────────────────────────
async function ensureBrowser() {
  const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(600) })
    .then((r) => r.ok).catch(() => false);
  let up = false;
  for (let i = 0; i < 2; i++) { if (await alive(9222)) { up = true; break; } await new Promise((r) => setTimeout(r, 300)); }
  if (!up) {
    spawnSync(process.execPath, [join(SCRIPT_DIR, 'launch.mjs')], { stdio: 'inherit' });
  }
  // find the live CDP port
  let port = null;
  for (let p = 9222; p < 9322 && !port; p++) {
    for (let i = 0; i < 20; i++) {
      if (await alive(p)) { port = p; break; }
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  if (!port) throw new Error('no live CDP port');
  return port;
}

// ── CDP connection ─────────────────────────────────────────────────────────
async function connectPage(port) {
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
  const wt = (t) => t.type === 'page' && t.webSocketDebuggerUrl && /wundertrading\.com/.test(t.url || '');
  const page = targets.find(wt) || targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no page target');

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
  return { page, send };
}

async function restoreSession(send) {
  await send('Network.enable');
  await send('Page.enable');
  for (const c of cookieList()) {
    const base = { name: c.name, value: c.value, path: c.path || '/' };
    if (c.secure) base.secure = true;
    if (c.httpOnly) base.httpOnly = true;
    if (c.sameSite && ['Strict', 'Lax', 'None'].includes(c.sameSite)) base.sameSite = c.sameSite;
    if (typeof c.expires === 'number' && c.expires > 0) base.expires = c.expires;
    const isDomain = (c.domain || '').startsWith('.');
    if (isDomain) base.domain = c.domain;
    else base.url = `https://${(c.domain || 'wundertrading.com').replace(/^\./, '')}/`;
    await send('Network.setCookie', base);
  }
}

async function navigate(send, url) {
  await send('Page.navigate', { url });
  await new Promise((r) => setTimeout(r, 4000));
}

// ── login probe (multi-signal) ─────────────────────────────────────────────
async function probeAuth(send) {
  const PROBE_JS = `(() => {
    const url = location.href;
    const loginCta = document.querySelector('a[href*="/login" i], button[class*="login" i], a[href*="sign-in" i]');
    const loginRedirect = /login|signin|password/i.test(url);
    return { url, title: document.title, loginCta: !!loginCta, loginRedirect };
  })()`;
  const r = await send('Runtime.evaluate', { expression: PROBE_JS, returnByValue: true });
  const p = r.result?.result?.value || {};
  const authed = (!p.loginCta && !p.loginRedirect);
  return { authed, probe: p };
}

// ── fetch-in-page API call ─────────────────────────────────────────────────
async function apiCall(send, method, path, body) {
  // ensure same-origin page context
  const loc = await send('Runtime.evaluate', { expression: 'location.href', returnByValue: true });
  const cur = loc.result?.result?.value || '';
  if (!cur.startsWith(WT_ORIGIN)) await navigate(send, `${WT_ORIGIN}/en/trader/grid_bots`);
  const isObj = (o) => Object.prototype.toString.call(o) === '[object Object]';
  const expr = `(async () => {
    const method = ${JSON.stringify(method)};
    const path = ${JSON.stringify(path)};
    const body = ${body === undefined ? 'undefined' : JSON.stringify(body)};
    try {
      const r = await fetch(path, {
        method,
        headers: { 'Accept': 'application/json', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
        credentials: 'include',
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const text = await r.text();
      let json = null; try { json = JSON.parse(text); } catch {}
      return { status: r.status, ok: r.ok, url: r.url, json, text: json ? undefined : text.slice(0, 2000) };
    } catch (e) { return { status: 0, ok: false, error: String(e) }; }
  })()`;
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  const v = r.result?.result?.value;
  if (!v) {
    const err = r.result?.exceptionDetails;
    return { status: 0, ok: false, error: err ? JSON.stringify(err).slice(0, 500) : 'no result' };
  }
  return v;
}

// ── commands ────────────────────────────────────────────────────────────────
async function main() {
  const port = await ensureBrowser();
  const { page, send } = await connectPage(port);
  await restoreSession(send);

  // default / bare URL: launch + open page + report auth
  if (!command || command.startsWith('http')) {
    const url = command || rest[0] || DEFAULT_URL;
    await navigate(send, url);
    let { authed, probe } = await probeAuth(send);
    if (!authed) { // one retry after settle
      await new Promise((r) => setTimeout(r, 5000));
      ({ authed, probe } = await probeAuth(send));
    }
    console.log(JSON.stringify(probe, null, 2));
    console.log(authed ? `\nAUTH OK — session restored (${probe.url})` : `\nAUTH FAIL — session looks stale (url: ${probe.url})`);
    await shot(send);
    process.exit(0);
  }

  if (command === 'open') {
    const url = rest[0] || DEFAULT_URL;
    await navigate(send, url);
    const { authed, probe } = await probeAuth(send);
    console.log(authed ? `AUTH OK — opened ${probe.url}` : `AUTH FAIL (${probe.url})`);
    process.exit(authed ? 0 : 1);
  }

  if (command === 'api') {
    const [method, path, bodyRaw] = rest;
    if (!method || !path) { console.error(USAGE); process.exit(2); }
    const methodUp = method.toUpperCase();
    let body;
    if (bodyRaw !== undefined && bodyRaw !== '') {
      try { body = JSON.parse(bodyRaw); } catch (e) { console.error(`body is not valid JSON: ${e.message}`); process.exit(2); }
    }
    const out = await apiCall(send, methodUp, path, body);
    if (out.json !== undefined && out.json !== null) console.log(JSON.stringify(out.json, null, 2));
    else console.log(JSON.stringify(out, null, 2));
    process.exit(out.ok ? 0 : 1);
  }

  if (command === 'eval') {
    const expr = rest.join(' ');
    if (!expr) { console.error(USAGE); process.exit(2); }
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    const v = r.result?.result?.value;
    console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 2));
    process.exit(0);
  }

  if (command === 'shot') {
    const file = rest[0];
    const p = await shot(send, file);
    console.log(p ? `screenshot: ${p}` : 'screenshot failed');
    process.exit(p ? 0 : 1);
  }
}

async function shot(send, file) {
  try {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.result?.data) {
      mkdirSync(join(SCRIPT_DIR, 'shots'), { recursive: true });
      const p = file || join(SCRIPT_DIR, 'shots', `wt-${new Date().toISOString().replace(/[:.]/g, '-')}.png`);
      writeFileSync(p, Buffer.from(s.result.data, 'base64'));
      return p;
    }
  } catch { /* non-fatal */ }
  return null;
}

main().catch((e) => { console.error(String(e?.message || e)); process.exit(1); });
