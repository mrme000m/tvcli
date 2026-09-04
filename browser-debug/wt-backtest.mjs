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


// ---------- DCA backtest engine (verbatim port of SPA module 909695) ----------
const FEE = 0.002; // X in the bundle

// indicator primitives (technicalindicators-style, as vendored by the SPA)
class RSI {
  constructor(period = 14) { this.period = period; this.prices = []; this.avgG = null; this.avgL = null; }
  nextValue(close) {
    this.prices.push(close);
    if (this.prices.length < this.period + 1) return undefined;
    if (this.avgG === null) {
      let g = 0, l = 0;
      for (let i = 1; i <= this.period; i++) { const d = this.prices[i] - this.prices[i - 1]; if (d >= 0) g += d; else l -= d; }
      this.avgG = g / this.period; this.avgL = l / this.period;
    } else {
      const d = close - this.prices[this.prices.length - 2];
      this.avgG = (this.avgG * (this.period - 1) + Math.max(d, 0)) / this.period;
      this.avgL = (this.avgL * (this.period - 1) + Math.max(-d, 0)) / this.period;
    }
    if (this.prices.length > this.period + 1) this.prices.shift();
    if (this.avgL === 0) return 100;
    return 100 - 100 / (1 + this.avgG / this.avgL);
  }
}
class BollingerBands {
  constructor(period = 21, stdDev = 2) { this.period = period; this.stdDev = stdDev; this.prices = []; }
  nextValue(close) {
    this.prices.push(close);
    if (this.prices.length < this.period) return undefined;
    if (this.prices.length > this.period) this.prices.shift();
    const mean = this.prices.reduce((a, b) => a + b, 0) / this.period;
    const variance = this.prices.reduce((a, b) => a + (b - mean) ** 2, 0) / this.period;
    const sd = Math.sqrt(variance);
    return { middle: mean, upper: mean + this.stdDev * sd, lower: mean - this.stdDev * sd };
  }
}
class MACD {
  constructor(fast = 3, slow = 21, signal = 9) {
    this.fast = fast; this.slow = slow; this.signal = signal;
    this.emaFast = null; this.emaSlow = null; this.emaSignal = null; this.macds = [];
  }
  #ema(prev, val, n) { const k = 2 / (n + 1); return prev === null ? val : val * k + prev * (1 - k); }
  nextValue(close) {
    this.emaFast = this.#ema(this.emaFast, close, this.fast);
    this.emaSlow = this.#ema(this.emaSlow, close, this.slow);
    if (this.emaFast === null || this.emaSlow === null) return undefined;
    const macd = this.emaFast - this.emaSlow;
    this.emaSignal = this.#ema(this.emaSignal, macd, this.signal);
    if (this.emaSignal === null || this.macds.length < 1) { this.macds.push(macd); return undefined; }
    this.macds.push(macd);
    return { macd, signal: this.emaSignal };
  }
}

// entry-signal wrappers (direction: 'long' | 'short' | 'both')
function makeIndicator(kind, direction, priceChangeDeviation) {
  if (kind === 'rsi') {
    const rsi = new RSI(14); let activate = null;
    return (candle, canEnter) => {
      const o = rsi.nextValue(candle.close);
      if (o === undefined || !canEnter) return false;
      if (activate) {
        if (activate === 'long' && o >= 25) { activate = null; return 'long'; }
        if (activate === 'short' && o <= 75) { activate = null; return 'short'; }
      } else {
        if (o < 25 && (direction === 'both' || direction === 'long')) activate = 'long';
        if (o > 75 && (direction === 'both' || direction === 'short')) activate = 'short';
      }
      return false;
    };
  }
  if (kind === 'bb') {
    const bb = new BollingerBands(21, 2.5); let activate = null;
    return (candle, canEnter) => {
      const o = bb.nextValue(candle.close);
      if (o === undefined || !canEnter) return false;
      if (activate) {
        if (activate === 'long' && o.lower < candle.close) { activate = null; return 'long'; }
        if (activate === 'short' && candle.close < o.upper) { activate = null; return 'short'; }
      } else {
        if ((direction === 'long' || direction === 'both') && o.lower > candle.close) activate = 'long';
        if ((direction === 'short' || direction === 'both') && candle.close > o.upper) activate = 'short';
      }
      return false;
    };
  }
  if (kind === 'macd') {
    const macd = new MACD(3, 21, 9); let activate = null;
    return (candle, canEnter) => {
      const o = macd.nextValue(candle.close);
      if (o === undefined || !canEnter) return false;
      if (activate) {
        let a = false;
        if (activate === 'long') a = o.signal < o.macd && o.macd < 0;
        if (activate === 'short') a = 0 < o.macd && o.macd < o.signal;
        const c = activate; activate = null;
        return a ? c : false;
      }
      let i = null;
      if ((direction === 'both' || direction === 'long') && o.macd < o.signal && o.signal < 0) i = 'long';
      if ((direction === 'both' || direction === 'short') && 0 < o.signal && o.signal < o.macd) i = 'short';
      activate = i;
      return false;
    };
  }
  if (kind === 'price_change') {
    const div = priceChangeDeviation ?? 0.02;
    return (candle, canEnter) => {
      const i = candle.open, c = candle.close;
      if (i === c || !canEnter) return false;
      let o, a;
      if (i > c) { o = (i - c) / i; a = 'long'; } else { o = (c - i) / c * 0 + (c - i) / i; a = 'short'; }
      if (o > div) return direction === 'both' ? a : (direction === a ? direction : false);
      return false;
    };
  }
  return null;
}

// DCA ladder builder (verbatim port of module 75976)
function buildDcaLadder(amountPerTrade, amountPerTradeType, requiredCurrency, currentPrice, dcaObject, balance) {
  const costAveragingType = dcaObject.costAveragingType;
  const dcaMode = dcaObject.dcaMode;
  const g = +dcaObject.dcaOrdersCount;
  let m = +dcaObject.dcaOrderPriceDeviation;
  const v = +dcaObject.dcaOrderPriceDeviationMultiplier;
  const b = +dcaObject.dcaOrderVolumeMultiplier;
  const y = [];
  const c = currentPrice;
  let h = +amountPerTrade, f;
  if (amountPerTradeType === 'percents') {
    if (requiredCurrency) { h = requiredCurrency.base / 100 * h; f = requiredCurrency.ref / 100 * +amountPerTrade; }
    if (costAveragingType === 'base') h /= c; else if (f) f *= c;
  }
  const w = amountPerTradeType !== 'percents' && costAveragingType === 'base' && false; // l===BASE&&p===BASE only for percents paths
  const x = amountPerTradeType !== 'percents' && costAveragingType === 'quote' && false;
  if (w) h /= c; else if (x) h *= c;
  let O = 0, j = 0, S = 0, A = 0, P = 0;
  let k = c, T = c;
  m = m / 100;
  for (let E = dcaMode === 'order_averaging' ? 0 : 1; O < g;) {
    const _ = O !== 0 ? m * Math.pow(v, O) : 0;
    let C = h, D = f || h;
    if (b !== 1 && O > E) {
      const N = costAveragingType === 'base' ? 'ref' : 'base';
      C = y[O - 1].long[N] * b; D = y[O - 1].short[N] * b;
    }
    k -= k * _; T += T * _;
    if (costAveragingType === 'base') {
      j += C * k; S += C; A += D * T; P += D;
      y.push({
        long: { ref: C, totalValue: j, base: C * k, price: k, deviation: S ? round2(100 * (c - j / S) / c, 2) : 0 },
        short: { ref: D, totalValue: A, base: D * T, price: T, deviation: P ? round2(100 * (A / P - c) / c, 2) : 0 },
      });
    } else {
      j += C / k; A += D / T; S += C; P += D;
      y.push({
        long: { ref: C / k, totalValue: j, base: C, price: k, deviation: S ? round2(100 * (c - j / S) / c, 2) : 0 },
        short: { ref: D / T, totalValue: A, base: D, price: T, deviation: P ? round2(100 * (A / P - c) / c, 2) : 0 },
      });
    }
    O++;
  }
  return y;
}
const round2 = (v, d) => { const f = 10 ** d; return Math.round(v * f) / f; };

// the DCA backtest engine (verbatim port of Z in module 909695)
function dcaBacktest(e) {
  const t = e.candles, r = e.takeProfit, a = e.stopLoss, i = e.direction, c = +e.amountPerTrade,
        s = e.amountPerTradeType, u = e.requiredCurrency, l = e.dcaObject, p = e.indicator,
        d = e.botStartCondition, g = e.priceChangePriceDeviation, m = e.pairCode, v = e.balance;
  const y = l.dcaOrdersCount, h = l.dcaOrderPriceDeviation,
        w = l.takeProfitTypeValue, x = l.stopLossTypeValue;
  if (!t || t.length === 0) throw new Error('No candles!');
  const A = new Date(t[0].time).toISOString();
  const P = [];
  let S;
  if (d === 'indicator') {
    switch (p) {
      case 'price_change': S = makeIndicator('price_change', i, g); break;
      case 'rsi': S = makeIndicator('rsi', i); break;
      case 'bb': S = makeIndicator('bb', i); break;
      case 'macd': S = makeIndicator('macd', i); break;
    }
  }
  const tpDec = r !== null && r !== undefined ? r / 100 : null;
  const slDec = a ? a / 100 : null;
  const hDec = h !== null && h !== undefined ? h / 100 : null;
  let T, D, N, I, G, O, L = 0, q = 0, U = 0, H = 0, Q = 0, Z = 0;
  let k = [];
  const J = () => { U = 0; H = 0; q = 0; Z = 0; Q++; T = null; D = null; N = null; G = null; I = null; k = []; };
  const $ = (e, t2, unrealized) => {
    let o = H / T * e - H;
    if (t2 === 'short') o = -o;
    return unrealized ? o : o - H * FEE - (H + o) * FEE;
  };
  const te = () => { U++; if (k[U]) { G = k[U][O].price; I = k[U][O].base; } };
  const re = (e) => { const t2 = e * tpDec; D = O === 'long' ? e + t2 : e - t2; };
  const ne = (e) => { const t2 = e * slDec; N = O === 'long' ? e - t2 : e + t2; };
  for (const [ie, ceRaw] of t.entries()) {
    const ce = { ...ceRaw, index: ie };
    const ue = ce.high, le = ce.close, fe = ce.low, pe = ce.time / 1e3;
    if (U < y && T && ((O === 'long' && fe <= G) || (O === 'short' && ue >= G))) {
      T = (H + I) / (H / T + I / G);
      if (w === 'average_price') re(T);
      if (x === 'average_price' && a) ne(T);
      H += I;
      P.push({ type: 'extra - ' + O, side: O, timestamp: pe, price: G, amount: I });
      te();
    }
    if (T && ((N && O === 'long' && fe <= N) || (N && O === 'short' && ue >= N))) {
      L += $(N, O);
      P.push({ type: 'sl - ' + O, side: O === 'short' ? 'long' : 'short', timestamp: pe, price: N });
      J();
    } else if (T && ((D && O === 'long' && ue >= D) || (D && O === 'short' && fe <= D))) {
      L += $(D, O);
      P.push({ type: 'tp - ' + O, side: O === 'short' ? 'long' : 'short', timestamp: pe, price: D });
      J();
    } else {
      const de = S ? S.nextValue(ce, !T) : i;
      if (T || !de) {
        if (T) { q = $(le, O, true); q -= H * FEE; }
      } else {
        k = buildDcaLadder(c, s, u, le, l, v);
        T = le; G = le; H = c; I = c; O = de;
        P.push({ type: 'entry - ' + O, side: O, timestamp: pe, price: le, amount: c });
        Z++;
        re(le);
        if (a) ne(le);
        te();
      }
    }
  }
  return { profit: round2(L, 2), unrealizedProfit: round2(q, 2), realizedTrades: Q, unrealizedTrades: Z, dateStarted: A, tradesArray: P, pairCode: m };
}


async function dcaRun(cfg) {
  const code = cfg.pairCode || `${cfg.exchangeCode}:${cfg.pairsCodes?.[0] || cfg.pairCode}`;
  const limit = Math.round(days * 24 * 60 / tf);
  const bars = await getBars(code, tf, limit);
  const last = bars[bars.length - 1].close;
  return dcaBacktest({
    pairCode: code, candles: bars,
    takeProfit: cfg.takeProfit ?? (cfg.takeProfits?.[0]?.priceDeviation * 100 ?? null),
    stopLoss: cfg.stopLoss ?? null,
    direction: cfg.dcaTradingType ?? 'long',
    amountPerTrade: cfg.amountPerTrade, amountPerTradeType: cfg.amountPerTradeType ?? 'base',
    requiredCurrency: { base: last, ref: last }, // price for percents conversion
    dcaObject: {
      dcaOrdersCount: cfg.extraOrderCount ?? 3,
      dcaOrderPriceDeviation: cfg.dcaObject?.dcaOrderPriceDeviation ?? cfg.extraOrderDeviation * 100 ?? 2,
      dcaOrderVolumeMultiplier: cfg.dcaObject?.dcaOrderVolumeMultiplier ?? cfg.extraOrderVolumeMultiplier ?? 1.4,
      dcaOrderPriceDeviationMultiplier: cfg.dcaObject?.dcaOrderPriceDeviationMultiplier ?? cfg.extraOrderDeviationMultiplier ?? 1,
      dcaMode: cfg.dcaObject?.dcaMode ?? (cfg.applyDcaForFirstSafetyOrder ? 'order_averaging' : 'position_averaging'),
      costAveragingType: cfg.dcaObject?.costAveragingType ?? cfg.extraOrderCostAveraging ?? 'base',
      takeProfitTypeValue: cfg.takeProfitBaseOn ?? 'average_price',
      stopLossTypeValue: cfg.stopLossBaseOn ?? 'average_price',
    },
    indicator: cfg.indicator ?? null,
    botStartCondition: cfg.entrySignalCondition ?? 'immediate',
    balance: cfg.balance ?? 10000,
  });
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
  } else if (cmd === 'dca') {
    const cfg = JSON.parse(readFileSync(rest[0], 'utf8'));
    const r = await dcaRun(cfg);
    const sum = { profit: r.profit, unrealizedProfit: r.unrealizedProfit, realizedTrades: r.realizedTrades, unrealizedTrades: r.unrealizedTrades, dateStarted: r.dateStarted, tradesCount: r.tradesArray.length, trades: r.tradesArray.slice(0, 60) };
    console.log(JSON.stringify(sum, null, 1));
  } else if (cmd === 'dca-sweep') {
    const cfg = JSON.parse(readFileSync(rest[0], 'utf8'));
    const devSpec = arg('--dev', '0.5:5:0.5');     // dcaOrderPriceDeviation sweep (%)
    const tpSpec = arg('--tp', '0.5:3:0.5');        // takeProfit sweep (%)
    const [df, dt, ds] = devSpec.split(':').map(Number);
    const [tfp, tpt, tps] = tpSpec.split(':').map(Number);
    const runs = [];
    for (let dev = df; dev <= dt + 1e-9; dev = round2(dev + ds, 2)) {
      for (let tp = tfp; tp <= tpt + 1e-9; tp = round2(tp + tps, 2)) {
        const c2 = { ...cfg, dcaObject: { ...cfg.dcaObject, dcaOrderPriceDeviation: dev }, takeProfit: tp };
        const r = await dcaRun(c2);
        runs.push({ dev, tp, profit: r.profit, unrealizedProfit: r.unrealizedProfit, realizedTrades: r.realizedTrades, tradesCount: r.tradesArray.length });
      }
    }
    const rankBy = arg('--rank-by', 'profit');
    runs.sort((a2, b2) => (b2[rankBy] ?? 0) - (a2[rankBy] ?? 0));
    console.log(JSON.stringify({ params: { devSpec, tpSpec }, best: runs[0], top10: runs.slice(0, 10) }, null, 1));
  } else {
    console.error('usage: node wt-backtest.mjs <backtest|optimize|sweep|dca|dca-sweep> <config.json> [--tf 15] [--days 31] [--step 0.2:5:0.1] [--widths ..] [--dev 0.5:5:0.5] [--tp 0.5:3:0.5] [--rank-by profit]');
    process.exit(2);
  }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
