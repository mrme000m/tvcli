#!/usr/bin/env node
// restore-session.mjs — restore wt-session cookies into a SPECIFIC CDP port
// and verify auth. Usage: node restore-session.mjs <port> <session.env>
// (wt.mjs always grabs the first live port 9222.. — this targets one browser.)
import { existsSync, readFileSync } from 'node:fs';

const PORT = process.argv[2] || '9223';
const ENV_FILE = process.argv[3] || 'secrets/runtime/wt-session-vault.env';
const WT = 'https://wundertrading.com';

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
const sess = parseEnv(ENV_FILE);
let cookies = [];
if (sess.WT_COOKIES_JSON) { try { cookies = JSON.parse(sess.WT_COOKIES_JSON); } catch {} }
if (!cookies.length && (sess.WT_PHPSESSID || sess.WT_CF_CLEARANCE)) {
  if (sess.WT_PHPSESSID) cookies.push({ name: 'PHPSESSID', value: sess.WT_PHPSESSID, domain: 'wundertrading.com', path: '/', secure: true, httpOnly: true });
  if (sess.WT_CF_CLEARANCE) cookies.push({ name: 'cf_clearance', value: sess.WT_CF_CLEARANCE, domain: '.wundertrading.com', path: '/', secure: true, httpOnly: true });
}

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
let page = targets.find(t => t.type === 'page' && /wundertrading/.test(t.url || ''));
if (!page) {
  // open a new tab via the browser target
  const bver = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(`${WT}/en/trader/grid_bots`)}`, { method: 'PUT' })).json().catch(() => null);
  page = bver;
}
if (!page) { const nt = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); page = nt.find(t => t.type === 'page'); }
if (!page) throw new Error('no page target on port ' + PORT);

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
await new Promise(r => ws.addEventListener('open', r, { once: true }));
const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });

await send('Network.enable');
await send('Page.enable');
for (const c of cookies) {
  const base = { name: c.name, value: c.value, path: c.path || '/' };
  if (c.secure) base.secure = true;
  if (c.httpOnly) base.httpOnly = true;
  if (typeof c.expires === 'number' && c.expires > 0) base.expires = c.expires;
  if ((c.domain || '').startsWith('.')) base.domain = c.domain;
  else base.url = `https://${(c.domain || 'wundertrading.com').replace(/^\./, '')}/`;
  await send('Network.setCookie', base);
}
console.log(`restored ${cookies.length} cookies`);
await send('Page.navigate', { url: `${WT}/en/trader/grid_bots` });
await new Promise(r => setTimeout(r, 8000));
const r = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => ({
  url: location.href,
  title: document.title,
  loginCta: !!document.querySelector('a[href*="/login" i]'),
})) ()` });
const v = r.result?.result?.value || {};
console.log(JSON.stringify(v));
console.log(/login|signin/i.test(v.url || '') || v.loginCta ? 'AUTH: FAIL' : 'AUTH: OK');
ws.close();
