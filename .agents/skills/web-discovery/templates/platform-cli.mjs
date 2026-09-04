#!/usr/bin/env node
// platform-cli.mjs — web-discovery template: forge a reusable CLI for ANY
// web platform from the live headful CloakBrowser session.
//
// COPY this file to browser-debug/<platform>.mjs, edit the EDIT-ME block,
// then verify live (see the web-discovery skill, phase FORGE). Zero npm
// deps, Node >= 22 (global WebSocket). Session cookies come from the vault
// materialized runtime file — never commit values.
//
// Usage (after editing):
//   node <platform>.mjs                          # launch + restore session, open DEFAULT_URL
//   node <platform>.mjs open <url>              # launch + restore session, open <url>
//   node <platform>.mjs api <METHOD> <path> [bodyJSON]   # session-auth API call via page fetch
//   node <platform>.mjs eval '<js expression>'  # eval JS in page (awaitPromise, JSON out)
//   node <platform>.mjs shot [file.png]         # screenshot
//   node <platform>.mjs record [seconds] [--out file.json]  # capture network while you (or the
//                                                # agent) drive the UI; endpoint summary + dump

import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

// ── EDIT-ME: platform constants ────────────────────────────────────────────
const ORIGIN = 'https://example.com';                 // EDIT-ME platform origin
const DEFAULT_URL = `${ORIGIN}/`;                     // EDIT-ME useful landing page
const SESSION_ENV = join(SCRIPT_DIR, 'secrets/runtime/<platform>-session.env'); // EDIT-ME vault runtime file
// cookie restoration shape — EDIT-ME to the platform's session cookies
// (either <PREFIX>_COOKIES_JSON: JSON [{name,value,domain,...}] or per-cookie
//  <PREFIX>_<COOKIE> entries listed in cookieKeys below)
const COOKIE_KEYS = [];                              // EDIT-ME e.g. ['PHPSESSID', 'cf_clearance']
const LOGIN_URL_MARKERS = [/login/i, /signin/i];      // EDIT-ME URL shapes that mean "logged out"
// ── end EDIT-ME ────────────────────────────────────────────────────────────

const [cmd, ...rest] = process.argv.slice(2);
const command = (cmd && !cmd.startsWith('http') && !['open', 'api', 'eval', 'shot', 'record'].includes(cmd))
  ? null
  : cmd;
const USAGE = `usage: node <platform>.mjs [open <url> | api <METHOD> <path> [bodyJSON] | eval '<js>' | shot [file] | record [seconds] [--out file] | <url>]`;

// ── session env ─────────────────────────────────────────────────────────────
function parseEnv(p) {
  const out = {};
  if (!existsSync(p)) return out;
  for (const raw of readFileSync(p, 'utf-8').split('\n')) {
    const line = raw.trim().replace(/\r$/, '');
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const i = line.indexOf('=');
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
const sess = parseEnv(SESSION_ENV);
let cookies = [];
if (sess.COOKIES_JSON) { try { cookies = JSON.parse(sess.COOKIES_JSON); } catch { /* fall through */ } }
for (const k of COOKIE_KEYS) if (sess[k]) cookies.push({ name: k, value: sess[k], domain: new URL(ORIGIN).hostname, path: '/', secure: true });
const cookieList = () => cookies;

// ── ensure headful browser is up (never pass --headless!) ──────────────────
async function ensureBrowser() {
  const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(600) })
    .then((r) => r.ok).catch(() => false);
  let up = false;
  for (let i = 0; i < 2; i++) { if (await alive(9222)) { up = true; break; } await new Promise((r) => setTimeout(r, 300)); }
  if (!up) spawnSync(process.execPath, [join(SCRIPT_DIR, 'launch.mjs')], { stdio: 'inherit' });
  let port = null;
  for (let p = 9222; p < 9322 && !port; p++) {
    for (let i = 0; i < 20 && !port; i++) { if (await alive(p)) { port = p; break; } await new Promise((r) => setTimeout(r, 250)); }
  }
  if (!port) throw new Error('no live CDP port');
  return port;
}

// ── CDP connection ─────────────────────────────────────────────────────────
async function connectPage(port) {
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
  const host = new URL(ORIGIN).hostname.replace(/^www\./, '');
  const mine = (t) => t.type === 'page' && t.webSocketDebuggerUrl && t.url && t.url.includes(host);
  const page = targets.find(mine) || targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no page target');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const events = [];            // network events for `record`
  let recording = false;
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (recording && m.method) events.push(m);
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  return { page, send, events, setRecording: (v) => { recording = v; } };
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
    if ((c.domain || '').startsWith('.')) base.domain = c.domain;
    else base.url = `https://${(c.domain || new URL(ORIGIN).hostname).replace(/^\./, '')}/`;
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
    const loginRedirect = ${JSON.stringify(LOGIN_URL_MARKERS.map(String))}.some((s) => new RegExp(s.slice(1, -1), 'i').test(url));
    return { url, title: document.title, loginCta: !!loginCta, loginRedirect };
  })()`;
  const r = await send('Runtime.evaluate', { expression: PROBE_JS, returnByValue: true });
  const p = r.result?.result?.value || {};
  return { authed: (!p.loginCta && !p.loginRedirect), probe: p };
}

// ── fetch-in-page API call (inherits TLS + cookies + fingerprint) ───────────
async function apiCall(send, method, path, body) {
  const loc = await send('Runtime.evaluate', { expression: 'location.href', returnByValue: true });
  const cur = loc.result?.result?.value || '';
  if (!cur.startsWith(ORIGIN)) await navigate(send, DEFAULT_URL);
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
  return r.result?.result?.value || { status: 0, ok: false, error: 'no result' };
}

// ── network recording → endpoint discovery ──────────────────────────────────
async function record(send, setRecording, getEvents, seconds, outFile) {
  setRecording(true); // events collector now stores Network.*
  await send('Network.enable');
  console.error(`recording ${seconds}s of network traffic — drive the UI now (or script it via another wt-style CLI)…`);
  await new Promise((r) => setTimeout(r, seconds * 1000));
  setRecording(false);
  const reqs = new Map();   // requestId -> {url, method, type, status}
  for (const m of getEvents()) {
    if (m.method === 'Network.requestWillBeSent') {
      const p = m.params;
      reqs.set(p.requestId, { url: p.request.url, method: p.request.method, type: p.type, initiator: p.initiator?.type });
    } else if (m.method === 'Network.responseReceived') {
      const e = reqs.get(m.params.requestId);
      if (e) { e.status = m.params.response.status; e.mimeType = m.params.response.mimeType; }
    }
  }
  const byEndpoint = new Map();
  for (const r of reqs.values()) {
    let u; try { u = new URL(r.url); } catch { continue; }
    const key = `${r.method} ${u.origin}${u.pathname}`;
    const e = byEndpoint.get(key) || { count: 0, statuses: [], types: new Set() };
    e.count++;
    if (r.status && !e.statuses.includes(r.status)) e.statuses.push(r.status);
    if (r.type) e.types.add(r.type);
    byEndpoint.set(key, e);
  }
  const rows = [...byEndpoint.entries()].sort((a, b) => b[1].count - a[1].count);
  for (const [key, e] of rows) {
    console.log(`${String(e.count).padStart(4)}x  ${key}  [${e.statuses.join(',') || '?'}] ${[...e.types].join(',')}`);
  }
  const dump = { capturedAt: new Date().toISOString(), seconds, endpoints: rows.map(([key, e]) => ({ endpoint: key, count: e.count, statuses: e.statuses, types: [...e.types] })), requests: [...reqs.values()] };
  mkdirSync(dirname(outFile), { recursive: true });
  writeFileSync(outFile, JSON.stringify(dump, null, 2));
  console.error(`\n${rows.length} endpoints, ${reqs.size} requests → ${outFile}`);
}

// ── commands ────────────────────────────────────────────────────────────────
async function main() {
  const port = await ensureBrowser();
  const { send, setRecording, events } = await connectPage(port);
  await restoreSession(send);

  if (!command || command.startsWith('http')) {
    const url = command || rest[0] || DEFAULT_URL;
    await navigate(send, url);
    let { authed, probe } = await probeAuth(send);
    if (!authed) { await new Promise((r) => setTimeout(r, 5000)); ({ authed, probe } = await probeAuth(send)); }
    console.log(JSON.stringify(probe, null, 2));
    console.log(authed ? `\nAUTH OK — session restored (${probe.url})` : `\nAUTH FAIL — session looks stale (url: ${probe.url})`);
    process.exit(authed ? 0 : 1);
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
    let body;
    if (bodyRaw !== undefined && bodyRaw !== '') {
      try { body = JSON.parse(bodyRaw); } catch (e) { console.error(`body is not valid JSON: ${e.message}`); process.exit(2); }
    }
    const out = await apiCall(send, method.toUpperCase(), path, body);
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
    const p = await shot(send, rest[0]);
    console.log(p ? `screenshot: ${p}` : 'screenshot failed');
    process.exit(p ? 0 : 1);
  }

  if (command === 'record') {
    let seconds = 30;
    const rest2 = [...rest];
    const outIdx = rest2.indexOf('--out');
    const outFile = outIdx >= 0 ? rest2[outIdx + 1] : null;
    if (outIdx >= 0) rest2.splice(outIdx, 2);
    if (rest2[0] && /^\d+$/.test(rest2[0])) seconds = parseInt(rest2[0], 10);
    await record(send, setRecording, () => events, seconds,
      outFile || join(SCRIPT_DIR, 'dumps', `${new URL(ORIGIN).hostname.replace(/^www\./, '')}-net-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
    process.exit(0);
  }

  console.error(USAGE);
  process.exit(2);
}

async function shot(send, file) {
  try {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.result?.data) {
      mkdirSync(join(SCRIPT_DIR, 'shots'), { recursive: true });
      const p = file || join(SCRIPT_DIR, 'shots', `shot-${new Date().toISOString().replace(/[:.]/g, '-')}.png`);
      writeFileSync(p, Buffer.from(s.result.data, 'base64'));
      return p;
    }
  } catch { /* non-fatal */ }
  return null;
}

main().catch((e) => { console.error(String(e?.message || e)); process.exit(1); });
