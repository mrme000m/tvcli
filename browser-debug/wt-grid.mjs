#!/usr/bin/env node
// wt-grid.mjs — WunderTrading Grid Bot CRUD via the session-auth web API.
//
// All calls run as fetch() INSIDE the logged-in CloakBrowser page (Cloudflare
// fingerprint + PHPSESSID + X-W-CSRF-Token from baseServerConfig), so the
// browser must be up (node launch.mjs / wt.mjs) and logged in.
//
// API surface reverse-engineered 2026-09-04 — see docs/wt/grid-bot-api.md.
//
// Usage:
//   node wt-grid.mjs list [--all]                    # active (or all) grid bots + action links
//   node wt-grid.mjs analyze <EXCH:code>             # market metadata + last candle + 30d hi/lo
//   node wt-grid.mjs create <config.json>            # POST upsert (gridMarket=derivative)
//   node wt-grid.mjs stop <botCode> [stopCondition]  # default stop_only
//   node wt-grid.mjs restart <botCode>
//   node wt-grid.mjs close-all <botCode>
//   node wt-grid.mjs delete <botCode>                # requires stopped + no positions
//   node wt-grid.mjs positions <botCode>
//   node wt-grid.mjs profiles                        # exchange profiles (codes, balances, paper)
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const [cmd, ...rest] = process.argv.slice(2);

async function pageTarget() {
  const targets = await fetch('http://127.0.0.1:9222/json').then(r => r.json());
  const page = targets.find(t => t.type === 'page' && /wundertrading\.com/.test(t.url) && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no WT page target — run: node wt.mjs');
  return page;
}

async function connect() {
  const page = await pageTarget();
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); setTimeout(() => rej(new Error('ws open timeout')), 4000); });
  const send = (method, params = {}) => new Promise((res) => {
    const i = ++id; pending.set(i, res);
    try { ws.send(JSON.stringify({ id: i, method, params })); } catch (e) { res({ error: { message: String(e) } }); }
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); res({ error: { message: method + ' timeout (renderer wedged? refresh the page)' } }); } }, 15000);
  });
  return { ws, send };
}

// eval in page with the fetch-in-page pattern (CSRF for non-safe methods)
async function callApi(method, path, body, marketOrigin = false) {
  const { ws, send } = await connect();
  try {
    const expr = `(async () => {
      const method = ${JSON.stringify(method)};
      const path = ${JSON.stringify(path)};
      const body = ${body === undefined ? 'undefined' : JSON.stringify(body)};
      const headers = { 'Accept': 'application/json' };
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (!['GET', 'HEAD'].includes(method)) headers['X-W-CSRF-Token'] = window.baseServerConfig.appCsrfToken;
      try {
        const r = await fetch(path, { method, headers, credentials: 'include', ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
        const text = await r.text();
        let json = null; try { json = JSON.parse(text); } catch {}
        return { status: r.status, ok: r.ok, json: json ?? text.slice(0, 1000) };
      } catch (e) { return { status: 0, ok: false, error: String(e) }; }
    })()`;
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.error) throw new Error(r.error.message);
    const v = r.result?.result?.value;
    if (!v) throw new Error('no result: ' + JSON.stringify(r.result?.exceptionDetails || {}).slice(0, 200));
    return v;
  } finally { try { ws.close(); } catch {} }
}

// public market-data origin (no cookies, no custom headers — CORS)
async function callMarket(path) {
  const { ws, send } = await connect();
  try {
    const expr = `(async () => {
      try {
        const r = await fetch(${JSON.stringify(path)}, { credentials: 'omit' });
        return { status: r.status, ok: r.ok, json: await r.json() };
      } catch (e) { return { status: 0, ok: false, error: String(e) }; }
    })()`;
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    return r.result?.result?.value;
  } finally { try { ws.close(); } catch {} }
}

const out = (v) => console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 1));

(async () => {
  if (cmd === 'list') {
    const all = rest.includes('--all');
    const path = '/en/trader/grid_bots/grid?page=1&limit=50' + (all ? '' : '&criteria%5Bstatuses%5D%5Bvalue%5D%5B%5D=active');
    const r = await callApi('GET', path);
    if (!r.ok) throw new Error('list failed: ' + JSON.stringify(r.json).slice(0, 300));
    for (const it of (r.json?._embedded?.items || [])) {
      const b = it.resource;
      const links = Object.fromEntries(Object.entries(it.actions || {})
        .filter(([, v]) => v?.data?.link)
        .map(([k, v]) => [k, v.data.method + ' ' + v.data.link]));
      out({ code: b.code, status: b.status, pair: b.pair?.unifiedCode, exchange: b.exchange?.code,
            paperTrading: b.paperTrading, gridTradingType: b.gridTradingType, gridType: b.gridType,
            step: b.gridPercentStep, levels: b.gridLevels, high: b.highPrice, low: b.lowPrice,
            actions: links });
    }
  } else if (cmd === 'analyze') {
    const code = rest[0];
    if (!code) throw new Error('usage: analyze <EXCH:pairCode>  e.g. HYPERLIQUID_SWAP:191');
    const [market, last, hist] = await Promise.all([
      callMarket(`https://wundertrading.com:2087/market?marketCode=${code}`),
      callMarket(`https://wundertrading.com:2087/ohlc/last?code=${code}&timeframe=15`),
      callMarket(`https://wundertrading.com:2087/ohlc/low-high?code=${code}&timeframe=15&limit=2976`),
    ]);
    out({ market: market?.json, lastCandle: last?.json, thirtyDayHighLow: hist?.json });
  } else if (cmd === 'create') {
    const file = rest[0];
    if (!file) throw new Error('usage: create <config.json>');
    const body = JSON.parse(readFileSync(join(process.cwd(), file), 'utf8'));
    const gridMarket = body.gridMarketHint || 'derivative'; // profile market
    delete body.gridMarketHint;
    const r = await callApi('POST', `/en/trader/grid_bots/upsert?gridMarket=${gridMarket}`, body);
    out({ status: r.status, ok: r.ok, gridBotCode: r.json?.result?.gridBotCode, violations: r.json?.violations, message: r.json?.message || r.json?.result?.message });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'stop') {
    const [code, stopCondition = 'stop_only'] = rest;
    const r = await callApi('POST', `/en/trader/grid_bots/${code}/stop?stopCondition=${stopCondition}&awaitStartSignal=true`, {});
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'restart') {
    const r = await callApi('POST', `/en/trader/grid_bots/${rest[0]}/restart`, {});
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'close-all') {
    const r = await callApi('POST', `/en/trader/grid_bots/${rest[0]}/close-all`, {});
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'delete') {
    const r = await callApi('DELETE', `/en/trader/grid_bots/${rest[0]}/delete`);
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'positions') {
    const r = await callApi('GET', `/en/trader/grid_bots/${rest[0]}/positions/grid?page=1&limit=50`);
    out(r.json);
  } else if (cmd === 'profiles') {
    const r = await callApi('GET', '/en/trader/grid_bots/upsert');
    const profiles = r.json?.data?.exchangesProfiles || {};
    for (const [exch, accs] of Object.entries(profiles)) {
      if (!accs) continue;
      for (const [id, acc] of Object.entries(accs)) {
        out({ exchange: exch, code: id, name: acc.name_of_account, paperTrading: acc.paperTrading,
              tradeMode: acc.tradeMode, marginMode: acc.marginMode });
      }
    }
  } else {
    console.error(`usage: node wt-grid.mjs <list [--all] | analyze EXCH:code | create config.json | stop code [stopCondition] | restart code | close-all code | delete code | positions code | profiles>`);
    process.exit(2);
  }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
