#!/usr/bin/env node
// max-chart.mjs — collapse all widget panels so the chart occupies the window.
//
// What it collapses:
//   - Right rail (watchlist/alerts/object tree/chats): the 4 panel buttons form a
//     radio group; clicking the active one collapses the rail (346px -> 45px).
//   - Bottom widget panels (replay/pine/trading): closed if any are active.
// What it can't collapse:
//   - Left drawing toolbar (52px) and bottom OHLC status bar (38px) are permanent
//     chrome in this layout — no collapse controls exist.
//
// Options:
//   node max-chart.mjs                 # collapse panels, save before/after shots
//   node max-chart.mjs --fullscreen    # also press Alt+Enter ("Maximize" hotkey)
//   node max-chart.mjs --no-shots      # skip screenshots
//
// Screenshots land in ./shots/maxchart_before.png / maxchart_after.png

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const SHOTS_DIR = join(SCRIPT_DIR, 'shots');

const RAIL_PANELS = ['base', 'alerts', 'object_tree', 'union_chats'];
const BOTTOM_BUTTONS = ['replay-button', 'trading-panel-button', 'pine-editor-button'];

const args = process.argv.slice(2);
const fullscreen = args.includes('--fullscreen');
const noShots = args.includes('--no-shots');

// --- connect to live CDP port + page target (same as toggle-widgets.mjs) ---
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
const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json());
const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
if (!page) { console.error('no page target'); process.exit(1); }

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

// --- helpers ---
const state = async () => JSON.parse((await send('Runtime.evaluate', {
  expression: `JSON.stringify((() => {
    const rail = document.querySelector('[data-name="widgetbar-wrap"]')?.getBoundingClientRect() || {};
    const center = document.querySelector('[class*="layout__area--center"]')?.getBoundingClientRect() || {};
    const bottom = document.querySelector('[class*="layout__area--bottom"]')?.getBoundingClientRect() || {};
    const pressed = [...document.querySelectorAll('[data-name]')].filter(e => e.getAttribute('aria-pressed') === 'true').map(e => e.dataset.name);
    const leftW = Math.round((document.querySelector('[class*="drawingToolbar"]')?.getBoundingClientRect() || {}).width || 0);
    return {
      rail: Math.round(rail.width || 0),
      left: leftW,
      center: { x: Math.round(center.x || 0), y: Math.round(center.y || 0), w: Math.round(center.width || 0), h: Math.round(center.height || 0) },
      bottomH: Math.round(bottom.height || 0),
      pressed,
    };
  })())`,
  returnByValue: true,
})).result?.result?.value ?? '{}');

const click = async (selector) => {
  const c = JSON.parse((await send('Runtime.evaluate', {
    expression: `JSON.stringify((() => { const e = document.querySelector('[data-name="${selector}"]'); if (!e) return null; const r = e.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })())`,
    returnByValue: true,
  })).result?.result?.value ?? 'null');
  if (!c) return false;
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x, y: c.y, button: 'left', clickCount: 1 });
  return true;
};

const key = (type, key, code, vk, mods) => send('Input.dispatchKeyEvent', { type, key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods });

const screenshot = async (name) => {
  const r = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  const data = r.result?.data;
  if (!data) return null;
  const p = join(SHOTS_DIR, name);
  writeFileSync(p, Buffer.from(data, 'base64'));
  return p;
};

// --- before ---
const before = await state();
const area = before.center;
console.log(`before: rail ${before.rail}px  left ${before.left}px  chart ${area.w}x${area.h}  bottom ${before.bottomH}px  open: [${before.pressed.join(', ') || 'none'}]`);
const beforeShot = noShots ? null : await screenshot('maxchart_before.png');

// --- collapse right rail: click the active panel button (radio group) ---
let collapsed = false;
for (const sel of RAIL_PANELS) {
  if (before.pressed.includes(sel)) {
    await click(sel);
    await sleep(600);
    collapsed = true;
    break;
  }
}

// --- close any open bottom widget panel ---
for (const sel of BOTTOM_BUTTONS) {
  const p = await state();
  if (p.pressed.includes(sel)) { await click(sel); await sleep(400); }
}

// --- optional fullscreen via the registered Maximize hotkey ---
if (fullscreen) {
  await key('rawKeyDown', 'Alt', 'AltLeft', 18, 1);
  await key('rawKeyDown', 'Enter', 'Enter', 13, 1);
  await key('keyUp', 'Enter', 'Enter', 13, 1);
  await key('keyUp', 'Alt', 'AltLeft', 18, 0);
  await sleep(800);
}

// --- after ---
const after = await state();
console.log(`after:  rail ${after.rail}px  left ${after.left}px  chart ${after.center.w}x${after.center.h}  bottom ${after.bottomH}px  open: [${after.pressed.join(', ') || 'none'}]`);
if (after.center.w > area.w || after.center.h > area.h) {
  console.log(`chart area gain: +${after.center.w - area.w}px wide, +${after.center.h - area.h}px tall`);
} else {
  console.log('chart already maximized (no panel was open)');
}
if (!collapsed && before.rail === 45) console.log('note: right rail was already collapsed');
if (fullscreen) console.log('note: Alt+Enter Maximize pressed — toggle browser/menu chrome via the same shortcut');

const afterShot = noShots ? null : await screenshot('maxchart_after.png');
if (beforeShot) console.log(`before: ${beforeShot}`);
if (afterShot) console.log(`after:  ${afterShot}`);
process.exit(0);