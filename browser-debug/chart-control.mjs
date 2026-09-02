#!/usr/bin/env node
// chart-control.mjs — symbol / X-scaling / Y-scaling / studies on the live chart session.
//
// Talks to the running headful browser over CDP (start it: node tv.mjs) and drives
// the chart widget API + real input events. No npm deps.
//
// Usage:
//   node chart-control.mjs status                 # symbol, interval, scale mode, studies
//   node chart-control.mjs symbol                 # current symbol
//   node chart-control.mjs symbol PEPPERSTONE:BTCUSD   # change symbol
//   node chart-control.mjs interval 1h            # X scaling: timeframe (1m/5m/15m/30m/1h/2h/4h/1D/1W or minutes)
//   node chart-control.mjs zoom in|out [times]    # X scaling: zoom via mouse wheel
//   node chart-control.mjs scale log|linear       # Y scaling: toggle log scale button (real input)
//   node chart-control.mjs scale auto             # Y scaling: toggle auto-scale button
//   node chart-control.mjs fit                    # Y scaling: reset both scales (GUIResetScales)
//   node chart-control.mjs studies                # list studies on the chart session
//
// Mechanics (verified live):
//   - symbol:   chartWidget.setSymbol(ticker)   (getSymbol() reads it back)
//   - X scale:  chartWidget.setResolution(minutes) — 60/120/30 all verified;
//               zoom = real mouse wheel over the chart pane
//   - Y scale:  "Toggle log scale" / "Toggle auto scale" price-scale buttons at the
//               pane's bottom-right — synthetic .click() does NOT work, real
//               Input.dispatchMouseEvent does (same as the right-rail panels)
//   - studies:  indicator legend DOM, [class*="legend"] [class*="study"] items;
//               hidden state = isHidden class

const TF = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '1D': 1440, '1W': 10080 };
const toMinutes = (s) => {
  s = String(s).toLowerCase();
  if (TF[s]) return TF[s];
  const m = s.match(/^(\d+)([mhdw])$/);
  if (!m) return parseInt(s, 10) || null;
  return { m: 1, h: 60, d: 1440, w: 10080 }[m[2]] * parseInt(m[1], 10);
};
const fmt = (min) => (min >= 1440 ? `${min / 1440}D` : min >= 60 ? `${min / 60}h` : `${min}m`);

const [cmd, arg] = process.argv.slice(2);
if (!cmd) { console.error('usage: see header of this file'); process.exit(1); }

// --- connect (same pattern as max-chart.mjs) ---
const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) }).then((r) => r.ok).catch(() => false);
let port = null;
for (let p = 9222; p < 9322 && !port; p++) {
  for (let i = 0; i < 20; i++) { if (await alive(p)) { port = p; break; } await new Promise((r) => setTimeout(r, 250)); }
}
if (!port) { console.error('no live CDP port — start the browser first: node tv.mjs'); process.exit(1); }
const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
if (!page) { console.error('no page target'); process.exit(1); }

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const raw = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true });
  if (r.result?.exceptionDetails) return `ERR: ${r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text}`;
  return r.result?.result?.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const CW = 'window.TradingViewApi._activeChartWidgetWV.value()._chartWidget';

// --- helpers ---
const click = async (x, y) => {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }); await sleep(350);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
};

// Buttons are always in the DOM at fixed order: [0] = A (auto scale), [1] = L (log scale).
// Only the text/title renders on hover; className (`_activated`) and order are stable.
const scaleBtns = async () => JSON.parse(await raw(`JSON.stringify((() => {
  const b = [...document.querySelectorAll('[class*="priceScaleModeButton"]')].filter(e => e.className.includes('priceScaleModeButton-'));
  return b.map((e, i) => {
    const r = e.getBoundingClientRect();
    return { i, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), on: e.className.includes('_activated') };
  });
})())`) ?? '[]');

const scaleBtn = async (label) => { const b = await scaleBtns(); return b[label === 'L' ? 1 : 0] ?? null; };

const scaleState = async () => { const b = await scaleBtns(); return { log: b[1]?.on ?? null, auto: b[0]?.on ?? null }; };

const studies = () => raw(`JSON.stringify([...document.querySelectorAll('[class*="legend" i] [class*="study"]')]
  .map(e => ({ name: (e.innerText || e.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
               hidden: e.classList.contains('isHidden') }))
  .filter(x => x.name))`);

// --- commands ---
if (cmd === 'symbol') {
  if (!arg) { console.log('symbol:', await raw(`${CW}.getSymbol()`)); process.exit(0); }
  await raw(`${CW}.setSymbol(${JSON.stringify(arg)}); 1`);
  await sleep(2500);
  console.log('symbol →', await raw(`${CW}.getSymbol()`));
} else if (cmd === 'interval') {
  if (!arg) { console.log('interval:', fmt(parseInt(await raw(`${CW}.getResolution()`), 10))); process.exit(0); }
  const min = toMinutes(arg);
  if (!min) { console.error(`bad interval "${arg}" — use 1m/5m/15m/30m/1h/2h/4h/1D/1W or minutes`); process.exit(1); }
  await raw(`${CW}.setResolution(${min}); 1`);
  await sleep(1500);
  console.log(`interval → ${fmt(parseInt(await raw(`${CW}.getResolution()`), 10))}`);
} else if (cmd === 'zoom') {
  const dir = arg === 'in' ? -1 : arg === 'out' ? 1 : null;
  if (!dir) { console.error('usage: zoom in|out [times]'); process.exit(1); }
  const times = parseInt(process.argv[4] || '1', 10);
  const c = JSON.parse(await raw(`JSON.stringify((() => { const r = document.querySelector('[class*="layout__area--center"]')?.getBoundingClientRect(); return r ? { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) } : null; })())`) ?? 'null');
  if (!c) { console.error('no chart pane'); process.exit(1); }
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x, y: c.y });
  for (let i = 0; i < times; i++) {
    await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: c.x, y: c.y, deltaX: 0, deltaY: 300 * dir });
    await sleep(200);
  }
  console.log(`zoomed ${arg} ×${times}`);
} else if (cmd === 'scale') {
  const mode = arg === 'log' || arg === 'linear' ? 'L' : arg === 'auto' ? 'A' : null;
  if (!mode) { console.error('usage: scale log|linear|auto'); process.exit(1); }
  const want = arg === 'log' ? true : arg === 'linear' ? false : null; // auto = toggle
  const before = await scaleState();
  const btn = await scaleBtn(mode);
  if (!btn) { console.error('scale button not found'); process.exit(1); }
  if (want !== null && before.log === want) { console.log(`already ${arg}`); process.exit(0); }
  await click(btn.x, btn.y);
  await sleep(800);
  const after = await scaleState();
  console.log(`scale: log=${after.log} auto=${after.auto}  (was log=${before.log} auto=${before.auto})`);
} else if (cmd === 'fit') {
  await raw(`${CW}.GUIResetScales(); 1`);
  await sleep(600);
  console.log('scales reset (fit content)');
} else if (cmd === 'studies') {
  const s = JSON.parse(await studies());
  console.log(`studies on chart session (${s.length}):`);
  for (const st of s) console.log(`  ${st.hidden ? '[hidden] ' : ''}${st.name}`);
} else if (cmd === 'status') {
  const sym = await raw(`${CW}.getSymbol()`);
  const res = fmt(parseInt(await raw(`${CW}.getResolution()`), 10));
  const sc = JSON.parse(await scaleState());
  const st = JSON.parse(await studies());
  console.log(`symbol:    ${sym}`);
  console.log(`interval:  ${res}   (X scale)`);
  console.log(`scale:     log=${sc.log} auto=${sc.auto}   (Y scale)`);
  console.log(`studies:   ${st.map((s) => `${s.hidden ? 'hidden ' : ''}${s.name}`).join(', ') || 'none'}`);
} else {
  console.error(`unknown command "${cmd}"`); process.exit(1);
}
process.exit(0);