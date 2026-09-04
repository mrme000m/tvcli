#!/usr/bin/env node
// wt-bots.mjs — unified WunderTrading bot CRUD across ALL bot types:
// Signal Bot, Grid Bot, DCA Bot, Market Neutral Bot, Multi-Pair Grid Bot.
//
// All calls run as fetch() INSIDE the logged-in CloakBrowser page (session +
// X-W-CSRF-Token). Surfaces verified live 2026-09-04 on the demo-hype paper
// profile — see docs/wt/grid-bot-api.md + docs/wt/bot-types-api.md.
//
// Usage:
//   node wt-bots.mjs <type> list [--all]      # type: signal|grid|dca|mn|mp
//   node wt-bots.mjs <type> create <cfg.json> # POST the type's upsert
//   node wt-bots.mjs <type> stop <code>       # pause/stop (stopCondition=stop_only)
//   node wt-bots.mjs <type> start <code>      # restart/switch-pause back on
//   node wt-bots.mjs <type> close-all <code>
//   node wt-bots.mjs <type> delete <code>     # requires stopped + no positions
//   node wt-bots.mjs <type> positions <code>
//   node wt-bots.mjs init <type>              # GET the type's upsert init data
//
// Type map (endpoints differ subtly per type — handled here):
//   signal → /en/trader/signal_bots          (upsert?code=, grid list, actions HAL)
//   grid   → /en/trader/grid_bots            (upsert?gridMarket=&code=, stop, restart)
//   dca    → /en/trader/dca_bots             (switch-pause?stopCondition=)
//   mn     → /en/trader/market_neutral       (pause, positions/grid/{code})
//   mp     → /en/trader/multi_pair_grid_bot  (switch-pause?stopCondition=)
import { readFileSync } from 'node:fs';

const TYPES = {
  signal: {
    list: (p) => `/en/trader/signal_bots/grid?${p}`,
    create: (cfg) => ({ path: '/en/trader/signal_bots/upsert', body: cfg }),
    init: '/en/trader/signal_bots/upsert',
    stop: (c) => ({ method: 'POST', path: `/en/trader/signal_bots/${c}/stop?stopCondition=stop_only&awaitStartSignal=true` }),
    start: (c) => ({ method: 'POST', path: `/en/trader/signal_bots/${c}/start` }),
    positions: (c) => `/en/trader/signal_bots/${c}/positions/grid`,
  },
  grid: {
    list: (p) => `/en/trader/grid_bots/grid?${p}`,
    create: (cfg) => ({ path: `/en/trader/grid_bots/upsert?gridMarket=${cfg.gridMarketHint || 'derivative'}`, body: (() => { const b = { ...cfg }; delete b.gridMarketHint; return b; })() }),
    init: '/en/trader/grid_bots/upsert',
    stop: (c) => ({ method: 'POST', path: `/en/trader/grid_bots/${c}/stop?stopCondition=stop_only&awaitStartSignal=true` }),
    start: (c) => ({ method: 'POST', path: `/en/trader/grid_bots/${c}/restart` }),
    positions: (c) => `/en/trader/grid_bots/${c}/positions/grid`,
  },
  dca: {
    list: (p) => `/en/trader/dca_bots/grid?${p}`,
    create: (cfg) => ({ path: '/en/trader/dca_bots/upsert', body: cfg }),
    init: '/en/trader/dca_bots/upsert',
    stop: (c) => ({ method: 'POST', path: `/en/trader/dca_bots/${c}/switch-pause?stopCondition=stop_only&awaitStartSignal=true` }),
    start: (c) => ({ method: 'POST', path: `/en/trader/dca_bots/${c}/switch-pause` }),
    positions: (c) => `/en/trader/dca_bots/${c}/positions/grid`,
  },
  mn: {
    list: () => '/en/trader/market_neutral/grid',
    create: (cfg) => ({ path: '/en/trader/market_neutral/upsert', body: cfg }),
    init: '/en/trader/market_neutral/upsert',
    stop: (c) => ({ method: 'POST', path: `/en/trader/market_neutral/${c}/pause` }),
    start: (c) => ({ method: 'POST', path: `/en/trader/market_neutral/${c}/pause` }),
    positions: (c) => `/en/trader/market_neutral/positions/grid/${c}`,
  },
  mp: {
    list: () => '/en/trader/multi_pair_grid_bot/grid?limit=200',
    create: (cfg) => ({ path: '/en/trader/multi_pair_grid_bot/upsert', body: cfg }),
    init: '/en/trader/multi_pair_grid_bot/upsert',
    stop: (c) => ({ method: 'POST', path: `/en/trader/multi_pair_grid_bot/${c}/switch-pause?stopCondition=stop_only&awaitStartSignal=true` }),
    start: (c) => ({ method: 'POST', path: `/en/trader/multi_pair_grid_bot/${c}/switch-pause` }),
    positions: (c) => `/en/trader/multi_pair_grid_bot/positions/grid/${c}`,
  },
};

async function connect() {
  const targets = await fetch('http://127.0.0.1:9222/json').then(r => r.json());
  const page = targets.find(t => t.type === 'page' && /wundertrading\.com/.test(t.url) && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no WT page target — run: node wt.mjs');
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

async function callApi(method, path, body) {
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
        return { status: r.status, ok: r.ok, json: json ?? text.slice(0, 1500) };
      } catch (e) { return { status: 0, ok: false, error: String(e) }; }
    })()`;
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.error) throw new Error(r.error.message);
    const v = r.result?.result?.value;
    if (!v) throw new Error('no result: ' + JSON.stringify(r.result?.exceptionDetails || {}).slice(0, 200));
    return v;
  } finally { try { ws.close(); } catch {} }
}

const out = (v) => console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 1));

const [typeArg, cmd, ...rest] = process.argv.slice(2);
const T = TYPES[typeArg];
if (!T) { console.error('unknown type: ' + typeArg + ' (use signal|grid|dca|mn|mp)'); process.exit(2); }

(async () => {
  if (cmd === 'list') {
    const all = rest.includes('--all');
    const p = typeArg === 'mn' || typeArg === 'mp' ? '' : (all ? 'page=1&limit=50' : 'page=1&limit=50&criteria%5Bstatuses%5D%5Bvalue%5D%5B%5D=active');
    const r = await callApi('GET', T.list(p));
    if (!r.ok) throw new Error('list failed: ' + JSON.stringify(r.json).slice(0, 300));
    const items = r.json?._embedded?.items || [];
    for (const it of items) {
      const b = it.resource || it;
      const links = Object.fromEntries(Object.entries(it.actions || {})
        .filter(([, v]) => v?.data?.link)
        .map(([k, v]) => [k, v.data.method + ' ' + v.data.link]));
      out({ id: b.id, code: b.code, status: b.status, paperTrading: b.paperTrading,
            pair: b.pair?.unifiedCode || b.pairCode, type: b.dcaTradingType || b.gridTradingType || b.type || b.riskLevel,
            actions: links });
    }
  } else if (cmd === 'init') {
    const r = await callApi('GET', T.init);
    out(r.json?.data || r.json);
  } else if (cmd === 'create') {
    const cfg = JSON.parse(readFileSync(rest[0], 'utf8'));
    const { path, body } = T.create(cfg);
    const r = await callApi('POST', path, body);
    out({ status: r.status, ok: r.ok, result: r.json?.result || r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'stop' || cmd === 'start') {
    const a = (cmd === 'stop' ? T.stop : T.start)(rest[0]);
    const r = await callApi(a.method, a.path, {});
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'close-all') {
    const base = { signal: 'signal_bots', grid: 'grid_bots', dca: 'dca_bots', mn: 'market_neutral', mp: 'multi_pair_grid_bot' }[typeArg];
    const r = await callApi('POST', `/en/trader/${base}/${rest[0]}/close-all`, {});
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'delete') {
    const base = { signal: 'signal_bots', grid: 'grid_bots', dca: 'dca_bots', mn: 'market_neutral', mp: 'multi_pair_grid_bot' }[typeArg];
    let id = rest[0];
    if (typeArg === 'signal' && !/^\d+$/.test(id)) {
      // signal bots delete by NUMERIC id — resolve code -> id from the list
      const lr = await callApi('GET', T.list('page=1&limit=200'));
      const items = lr.json?._embedded?.items || [];
      const found = items.find(it => (it.resource || it).code === id);
      if (!found) throw new Error('signal bot not found: ' + id);
      id = (found.resource || found).id;
    }
    const r = await callApi('DELETE', `/en/trader/${base}/${id}/delete`);
    out({ status: r.status, ok: r.ok, body: r.json });
    if (!r.ok) process.exit(1);
  } else if (cmd === 'positions') {
    const r = await callApi('GET', T.positions(rest[0]));
    out(r.json);
  } else {
    console.error(`usage: node wt-bots.mjs <signal|grid|dca|mn|mp> <list [--all] | init | create cfg.json | stop code | start code | close-all code | delete code | positions code>`);
    process.exit(2);
  }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
