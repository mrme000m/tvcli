#!/usr/bin/env node
// wt-backtest.mjs — programmatic Grid-bot backtest / optimize / parameter sweep.
//
// Ports the WunderTrading configurator's CLIENT-SIDE backtest engine verbatim
// from react_cabinet chunk (module 534152, verified 2026-09-04), so numbers
// match the UI's Backtest panel exactly. OHLC comes from the public :2087
// origin via fetch-in-page (Cloudflare-fingerprinted — raw HTTP gets 403).
//
// Commands (config.json = the same payload you would POST to
// /en/trader/grid_bots/upsert — see wt-grid.mjs create):
//   node wt-backtest.mjs backtest <config.json> [--tf 15] [--days 31]
//       one run; prints the UI-parity result (pnl, positions, trades...)
//   node wt-backtest.mjs optimize <config.json>
//       platform-parity sweep: gridPercentStep 1.2..3.0 step 0.01 (best totalResult)
//   node wt-backtest.mjs sweep <config.json> --step 0.2:5:0.1 [--widths 0.1,0.2,0.3]
//       agent sweep over profit-per-grid (and optional channel widths around
//       the current price); ranked by totalResult, includes gridLevels each
//
// Engine details (from the minified bundle, kept verbatim where it matters):
//   - 0.2% fee per closed grid position (the -.002 constant)
//   - intra-candle path: down candle walks [high, low, close], up candle [low, high, close]
//   - edge levels (first/last) never open positions
//   - LONG/SHORT/NEUTRAL/two_way open rules incl. the pumpProtection side-flip
//   - stopOnOutOfGrid trims leading candles outside the channel and halts on breakouts
import { readFileSync } from 'node:fs';

const [cmd, ...rest] = process.argv.slice(2);
const arg = (name, def) => { const i = rest.indexOf(name); return i >= 0 ? rest[i + 1] : def; };
const tf = parseInt(arg('--tf', '15'), 10);
const days = parseInt(arg('--days', '31'), 10);

// ---------- CDP fetch-in-page helpers (same pattern as wt-grid.mjs) ----------
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
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); res({ error: { message: method + ' timeout' } }); } }, 15000);
  });
  return { ws, send };
}

async function evalInPage(expr) {
  const { ws, send } = await connect();
  try {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.error) throw new Error(r.error.message);
    if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    return r.result?.result?.value;
  } finally { try { ws.close(); } catch {} }
}

async function getBars(code, timeframe, limit) {
  // mirrors c.A.getBars: GET :2087/ohlc -> {open, high, low, close, time, volume}
  const from = Date.now() - timeframe * 60 * 1000 * limit;
  const url = `https://wundertrading.com:2087/ohlc?code=${encodeURIComponent(code)}&from=${from}&timeframe=${timeframe}&limit=${limit}`;
  const bars = await evalInPage(`(async () => {
    const r = await fetch(${JSON.stringify(url)}, { credentials: 'omit' });
    const j = await r.json();
    return (j.data || j).map(c => ({ open: c.open, high: c.high, low: c.low, close: c.close, time: c.timestamp }));
  })()`);
  if (!Array.isArray(bars) || !bars.length) throw new Error('no candles from :2087');
  return bars;
}

async function getNotional(exchangeCode, currency) {
  const r = await evalInPage(`(async () => {
    const r = await fetch("/en/trader/grid_bots/find_notional_prices", {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json", "X-W-CSRF-Token": window.baseServerConfig.appCsrfToken },
      credentials: "include",
      body: JSON.stringify({ exchangeCode: ${JSON.stringify(exchangeCode)}, currencies: [${JSON.stringify(currency)}] }),
    });
    return await r.json();
  })()`);
  return r?.notionalPrices?.[currency]?.USD ?? 1;
}

// ---------- the engine (verbatim port of module 534152) ----------
const LONG = 'long', SHORT = 'short', NEUTRAL = 'neutral', TWO_WAY = 'two_way', INTERVAL = 'interval', INFINITE = 'infinite';

const canOpenLong = (e, t, r, n, o) => {
  let i = !e.long;
  if (n !== TWO_WAY) {
    if (o) { if (i) i = e.price > t; } else { if (i) i = e.price < t; }
    if (n === NEUTRAL) { if (i) i = e.price <= r; } else { if (i) i = n === LONG; }
  }
  return i;
};
const canOpenShort = (e, t, r, n, o) => {
  let i = !e.short;
  if (n !== TWO_WAY) {
    if (o) { if (i) i = e.price < t; } else { if (i) i = e.price > t; }
    if (n === NEUTRAL) { if (i) i = e.price > r; } else { if (i) i = n === SHORT; }
  }
  return i;
};

const round = (v, d) => { const f = 10 ** d; return Math.round(v * f) / f; };

function gridBacktest(e) {
  let f = e.candles, p = e.gridLevels ? [...e.gridLevels] : undefined;
  const m = e.amountPerTrade, v = e.midPrice, w = e.decimalsQty ?? 2, x = e.notionalPrice ?? 1, O = !!e.pumpProtection;
  const j = e.gridType ?? INTERVAL, S = e.gridMethod ?? NEUTRAL;
  const A = !!e.stopOnOutOfGrid && j === INTERVAL;
  const P = [];
  let b = e.highPrice, y = e.lowPrice, h = e.percents;
  if (p && p.length) { y = y || p[0]; b = b || p[p.length - 1]; }
  const dateStarted = new Date(f[0].time).toISOString();
  if (A) {
    for (let T = f.length - 1; T >= 0; T--) {
      const E = f[T];
      if (!(E.low > y && E.high < b)) { f = f.slice(T + 1); break; }
    }
  }
  if (f.length < 2) {
    return { dateStarted, error: 'no candles inside the channel (stopOnOutOfGrid trimmed everything)', pnl: 0, totalResult: 0, positionsLong: 0, positionsShort: 0, unrealizedPositionsLong: 0, unrealizedPositionsShort: 0 };
  }
  let _, C;
  let D = f[0].close;
  const N = {};
  const I = { long: 0, short: 0 };
  const G = { fiat: 0, percents: 0 };
  const M = f[0].high > b || f[0].low < y;
  f = f.slice(1);
  if (p) {
    const F = [];
    for (let B = 0; B < p.length; B++) {
      const z = p[B];
      if (!M && B !== p.length - 1 && D > z && D <= p[B + 1]) { _ = B + 1; C = B; }
      F.push({ price: z, long: false, short: false, trades: {} });
    }
    p = F;
  } else {
    let q = y; p = [];
    for (let U = 0; h <= (b - q) / q * 100;) {
      if (!M && U !== 0 && q >= D && p[U - 1] && p[U - 1].price < D) { _ = U; C = U - 1; }
      p.push({ price: q, long: false, short: false, trades: {} });
      U++; q *= 1 + h / 100;
    }
    p.push({ price: b, long: false, short: false, trades: {} });
  }
  const W = p.length - 2, H = p.length - 1;
  if (M) { if (y > f[0].low) { _ = 1; C = 0; } else { _ = H; C = W; } }
  const X = p[H].price, Z = p[0].price;
  const Y = {};
  const Q = (close) => {
    let t = 0;
    for (const k in N) {
      const n = N[k];
      if (n.long) t += (close - n.price) / n.price;
      if (n.short) t += -1 * (close - n.price) / n.price;
    }
    return t;
  };
  outer:
  for (const $ of f) {
    const ee = $.high > X, te = $.low < Z;
    if ((ee || te) && A) break outer;
    const re = D > $.close ? [$.high, $.low, $.close] : [$.low, $.high, $.close];
    for (const ae of re) {
      for (; D !== ae;) {
        let ie;
        if (p[C] && p[C].price >= ae && p[C].price < D) {
          ie = C; C -= 1; if (C < 0) C = 0; _ -= 1;
          if (p[ie + 1] && p[ie + 1].short) {
            const ce = p[ie + 1].price, se = -1 * (p[ie].price - ce) / ce - 0.002;
            G.percents += se; G.fiat += se * m;
            p[ie + 1].short = false; delete N[`${p[ie + 1].price}-short`]; I.short += 1;
            p[ie].trades[$.time] ||= { short: [], long: [] };
            P.push({ side: LONG, strategy: SHORT, timestamp: $.time / 1e3, price: p[ie].price });
            p[ie].trades[$.time].short.push(`CS [${_ - 1}]`);
          }
        } else if (p[_] && p[_].price <= ae && p[_].price > D) {
          ie = _; _ += 1; if (_ > H) _ = H; C += 1;
          if (p[ie - 1] && p[ie - 1].long) {
            const ue = p[ie - 1].price, le = (p[ie].price - ue) / ue - 0.002;
            G.percents += le; G.fiat += le * m;
            p[ie - 1].long = false; delete N[`${p[ie - 1].price}-long`]; I.long += 1;
            p[ie].trades[$.time] ||= { short: [], long: [] };
            P.push({ side: SHORT, strategy: LONG, timestamp: $.time / 1e3, price: p[ie].price });
            p[ie].trades[$.time].long.push(`CL [${ie}]`);
          }
        }
        if (ie) {
          const fe = ie, pe = p[fe].price;
          if (fe !== H && fe !== 0 && canOpenLong(p[fe], D, v, S, O)) {
            p[fe].long = true; N[`${p[fe].price}-long`] = { price: p[fe].price, long: true };
            p[fe].trades[$.time] ||= { short: [], long: [] };
            P.push({ side: LONG, strategy: LONG, timestamp: $.time / 1e3, price: p[fe].price });
            p[fe].trades[$.time].long.push('OL');
          }
          if (fe !== H && fe !== 0 && canOpenShort(p[fe], D, v, S, O)) {
            p[fe].short = true; N[`${p[fe].price}-short`] = { price: p[fe].price, short: true };
            p[fe].trades[$.time] ||= { short: [], long: [] };
            P.push({ side: SHORT, strategy: SHORT, timestamp: $.time / 1e3, price: p[fe].price });
            p[fe].trades[$.time].short.push('OS');
          }
          D = pe;
        } else D = ae;
      }
    }
    Y[$.time] = G.percents + Q($.close);
  }
  const ge = f[f.length - 1].close;
  const me = { fiat: 0, percents: 0 }, ve = { long: 0, short: 0 };
  for (const level of p) {
    if (level.long) { const he = (ge - level.price) / level.price; me.percents += he; me.fiat += he * m; ve.long += 1; }
    if (level.short) { const we = -1 * (ge - level.price) / level.price; me.percents += we; me.fiat += we * m; ve.short += 1; }
  }
  const xe = { percents: G.percents + me.percents, fiat: G.fiat + me.fiat };
  return {
    dateStarted,
    pnl: round(100 * G.percents, 2), pnlFiat: round(G.fiat, w), pnlUsd: x !== 1 ? round(G.fiat * x, 2) : undefined,
    unrealizedPnl: round(100 * me.percents, 2), unrealizedPnlFiat: round(me.fiat, w), unrealizedPnlUsd: x !== 1 ? round(me.fiat * x, 2) : undefined,
    totalResult: round(100 * xe.percents, 2), totalResultFiat: round(xe.fiat, w), totalResultUsd: x !== 1 ? round(xe.fiat * x, 2) : undefined,
    unrealizedPositionsLong: ve.long, unrealizedPositionsShort: ve.short,
    positionsShort: I.short, positionsLong: I.long,
    gridMethod: S, gridType: j, midPrice: v, percents: h,
    gridLevels: p.map(l => l.price), gridLevelsQty: p.length,
    tradesCount: P.length, trades: P,
  };
}

// ---------- run / optimize / sweep ----------
const summary = (r) => ({
  percents: r.percents, gridType: r.gridType, gridMethod: r.gridMethod,
  totalResult: r.totalResult, pnl: r.pnl, unrealizedPnl: r.unrealizedPnl,
  positionsLong: r.positionsLong, positionsShort: r.positionsShort,
  unrealizedPositionsLong: r.unrealizedPositionsLong, unrealizedPositionsShort: r.unrealizedPositionsShort,
  gridLevelsQty: r.gridLevelsQty, tradesCount: r.tradesCount,
});

async function buildInput(cfg, { percents, width } = {}) {
  const code = `${cfg.exchangeCode}:${cfg.pairCode}`;
  const limit = Math.round(days * 24 * 60 / tf);
  const bars = await getBars(code, tf, limit);
  const notional = await getNotional(cfg.exchangeCode, cfg.baseCurrency || 'USDC');
  let h = percents ?? cfg.gridPercentStep * 100;
  let low = cfg.lowPrice, high = cfg.highPrice, mid = cfg.midPrice;
  if (width) { // symmetric channel around the last price
    const last = bars[bars.length - 1].close;
    low = round(last * (1 - width), 8); high = round(last * (1 + width), 8); mid = round((low + high) / 2, 8);
  }
  if (cfg.gridType === INFINITE) { // platform rule: hi/lo of the window ±1%
    let hi = 0, lo = 1e12;
    for (const b of bars) { if (b.high > hi) hi = b.high; if (b.low < lo) lo = b.low; }
    high = hi * 1.01; low = lo * 0.99;
  }
  return {
    candles: bars, gridLevels: undefined,
    amountPerTrade: cfg.amountPerTrade ?? 20,
    gridType: cfg.gridType ?? INTERVAL,
    gridMethod: cfg.gridTradingType ?? cfg.gridMethod ?? NEUTRAL,
    midPrice: mid, highPrice: high, lowPrice: low,
    decimalsQty: cfg.decimalsQty ?? 4, stopOnOutOfGrid: !!cfg.stopOnOutOfGrid,
    notionalPrice: notional, pumpProtection: !!cfg.pumpProtection, percents: h,
  };
}

(async () => {
  if (cmd === 'backtest') {
    const cfg = JSON.parse(readFileSync(rest[0], 'utf8'));
    const input = await buildInput(cfg);
    const r = gridBacktest(input);
    console.log(JSON.stringify({ ...r, candles: undefined, trades: r.tradesCount > 50 ? r.trades.slice(0, 50) : r.trades }, null, 1));
  } else if (cmd === 'optimize') {
    const cfg = JSON.parse(readFileSync(rest[0], 'utf8'));
    const base = await buildInput(cfg); // bars fetched once
    const runs = [];
    for (let C = 1.2; C <= 3.0 + 1e-9; C = round(C + 0.01, 2)) {
      if (C < 0.2) continue; // platform min step
      const r = gridBacktest({ ...base, percents: C });
      runs.push({ ...summary(r), percents: C });
    }
    const rankBy = arg('--rank-by', 'totalResult'); // or pnl (realized only)
    runs.sort((a, b2) => (b2[rankBy] ?? 0) - (a[rankBy] ?? 0));
    console.log(JSON.stringify({ best: runs[0], ranked: runs.slice(0, 10) }, null, 1));
  } else if (cmd === 'sweep') {
    const cfg = JSON.parse(readFileSync(rest[0], 'utf8'));
    const stepSpec = arg('--step', '0.2:5:0.1'); // from:to:step (percents)
    const widthsSpec = arg('--widths', '');    // optional comma list of half-widths (e.g. 0.1,0.2,0.3)
    const [sf, st, ss] = stepSpec.split(':').map(Number);
    const widths = widthsSpec ? widthsSpec.split(',').map(Number) : [null];
    const runs = [];
    for (const width of widths) {
      const base = await buildInput(cfg, { width });
      for (let C = sf; C <= st + 1e-9; C = round(C + ss, 4)) {
        if (C < 0.2) continue;
        const r = gridBacktest({ ...base, percents: C });
        runs.push({ ...summary(r), percents: C, ...(width ? { halfWidth: width, lowPrice: base.lowPrice, highPrice: base.highPrice } : {}) });
      }
    }
    runs.sort((a, b2) => b2.totalResult - a.totalResult);
    console.log(JSON.stringify({ params: { timeframe: tf, days, stepSpec, widthsSpec }, best: runs[0], top10: runs.slice(0, 10) }, null, 1));
  } else {
    console.error('usage: node wt-backtest.mjs <backtest|optimize|sweep> <config.json> [--tf 15] [--days 31] [--step 0.2:5:0.1] [--widths 0.1,0.2,0.3]');
    process.exit(2);
  }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
