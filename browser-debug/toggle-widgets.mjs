#!/usr/bin/env node
// toggle-widgets.mjs — toggle TradingView right-rail panels via real CDP input,
// with before/after screenshots and a 5s settle wait after each toggle.
//
// The right rail is a RADIO GROUP: the 4 panel buttons (watchlist, alerts,
// object tree, chats) are mutually exclusive. Clicking one opens its panel and
// closes the others; clicking the already-active one collapses the rail to the
// thin icon strip. State is `aria-pressed`, but the real proof is the rail
// width: ~346px = panel open, ~45px = collapsed.
//
// A synthetic .click() only flips aria-pressed; TradingView's visual toggle
// needs a real input event, so this uses Input.dispatchMouseEvent at the
// button's center.
//
// Usage:
//   node toggle-widgets.mjs                 # toggle watchlist (with shots)
//   node toggle-widgets.mjs alerts          # toggle Alerts
//   node toggle-widgets.mjs object_tree --off
//   node toggle-widgets.mjs chats --on
//   node toggle-widgets.mjs --all           # demo every panel in sequence
//   node toggle-widgets.mjs --list
//
// Screenshots land in ./shots/<name>_before.png and ./shots/<name>_after.png.

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const SHOTS_DIR = join(SCRIPT_DIR, 'shots');

const PANELS = {
  watchlist: 'base',
  base: 'base',
  alerts: 'alerts',
  object_tree: 'object_tree',
  'object-tree': 'object_tree',
  data_window: 'object_tree',
  'data-window': 'object_tree',
  chats: 'union_chats',
};
const PANEL_ORDER = ['watchlist', 'alerts', 'object_tree', 'chats'];

const args = process.argv.slice(2);
if (args.includes('--list') || args.includes('-l')) {
  for (const [k, v] of Object.entries(PANELS)) console.log(`${v.padEnd(14)} ${k}`);
  process.exit(0);
}

const all = args.includes('--all');
const force = args.includes('--on') ? 'true' : args.includes('--off') ? 'false' : null;
const names = all ? PANEL_ORDER : [args[0] || 'watchlist'];
for (const n of names) {
  if (!PANELS[n]) { console.error(`unknown widget "${n}" — use --list`); process.exit(1); }
}

// 1. live CDP port
const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) })
  .then((r) => r.ok).catch(() => false);
let port = null;
for (let p = 9222; p < 9322 && !port; p++) {
  for (let i = 0; i < 20; i++) {
    if (await alive(p)) { port = p; break; }
    await new Promise((r) => setTimeout(r, 250));
  }
}
if (!port) { console.error('no live CDP port — start the browser first: node tv.mjs'); process.exit(1); }

// 2. page target
const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
if (!page) { console.error('no page target'); process.exit(1); }

// 3. CDP
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

mkdirSync(SHOTS_DIR, { recursive: true });

const readState = async (selector) => {
  const r = await send('Runtime.evaluate', {
    expression: `JSON.stringify({
      pressed: document.querySelector('[data-name="${selector}"]')?.getAttribute('aria-pressed') ?? null,
      rail: Math.round((document.querySelector('[data-name="widgetbar-wrap"]')?.getBoundingClientRect() || {}).width || 0)
    })`,
    returnByValue: true,
  });
  return JSON.parse(r.result?.result?.value ?? '{}');
};

const screenshot = async (name) => {
  const r = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  const data = r.result?.data;
  if (!data) { console.error(`  screenshot failed for ${name}`); return null; }
  const p = join(SHOTS_DIR, name);
  writeFileSync(p, Buffer.from(data, 'base64'));
  return p;
};

const click = async (selector) => {
  const c = await send('Runtime.evaluate', {
    expression: `JSON.stringify((() => { const e = document.querySelector('[data-name="${selector}"]'); if (!e) return null; const r = e.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })())`,
    returnByValue: true,
  });
  const pos = JSON.parse(c.result?.result?.value ?? 'null');
  if (!pos) { console.error(`no element for ${selector}`); return false; }
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
  return true;
};

let idx = 0;
for (const name of names) {
  idx++;
  const selector = PANELS[name];
  const tag = all ? `${String(idx).padStart(2, '0')}_${name}` : name;
  const before = await readState(selector);

  const beforeShot = await screenshot(`${tag}_before.png`);

  let toggled = false;
  if (force && force === before.pressed) {
    console.log(`${name}: already ${before.pressed === 'true' ? 'open' : 'closed'} (rail ${before.rail}px)`);
  } else {
    toggled = await click(selector);
    console.log(`${name}: toggled — waiting 5s…`);
    await sleep(5000);
  }

  const after = await readState(selector);
  const afterShot = toggled ? await screenshot(`${tag}_after.png`) : null;

  const open = after.pressed === 'true';
  console.log(`${name}: ${before.pressed} -> ${after.pressed}  (${open ? 'OPEN' : 'CLOSED'}, rail ${before.rail}px -> ${after.rail}px)`);
  if (beforeShot) console.log(`  before: ${beforeShot}`);
  if (afterShot) console.log(`  after:  ${afterShot}`);
  console.log('');
}
process.exit(0);