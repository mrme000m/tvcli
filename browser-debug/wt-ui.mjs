#!/usr/bin/env node
// wt-ui.mjs — headful UI driver for the WunderTrading page (real CDP input events).
// Usage:
//   node wt-ui.mjs click "Create Bot"          # click element whose text matches
//   node wt-ui.mjs click-xy <x> <y>            # click at viewport coords
//   node wt-ui.mjs eval '<js expr>'            # Runtime.evaluate with timeout, JSON out
//   node wt-ui.mjs shot [file.png]             # screenshot
const [cmd, ...rest] = process.argv.slice(2);

async function pageTarget() {
  const targets = await fetch('http://127.0.0.1:9222/json').then(r => r.json());
  const page = targets.find(t => t.type === 'page' && /wundertrading\.com/.test(t.url) && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no WT page target');
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
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); res({ error: { message: method + ' timeout' } }); } }, 10000);
  });
  return { ws, send, page };
}

async function evalJs(send, expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.error) throw new Error(r.error.message);
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
  return r.result?.result?.value;
}

async function clickAt(send, x, y) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  await new Promise(r => setTimeout(r, 120));
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await new Promise(r => setTimeout(r, 60));
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
}

(async () => {
  const { ws, send } = await connect();
  try {
    if (cmd === 'click') {
      const text = rest[0];
      // find element whose visible text matches, get center, click with real events
      const found = await evalJs(send, `(() => {
        const needle = ${JSON.stringify(text)};
        const els = [...document.querySelectorAll('button, a, [role=menuitem], li, div, span')]
          .filter(e => e.children.length === 0 || e.matches('button, a, [role=menuitem], li'));
        const el = els.find(e => (e.textContent || '').trim() === needle)
          || els.find(e => (e.textContent || '').trim().startsWith(needle));
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), w: Math.round(r.width), h: Math.round(r.height) };
      })()`);
      if (!found) { console.error('element not found: ' + text); process.exit(1); }
      if (found.w === 0 && found.h === 0) { console.error('element not visible: ' + text); process.exit(1); }
      await clickAt(send, found.x, found.y);
      console.log(JSON.stringify({ clicked: text, at: found }));
    } else if (cmd === 'click-xy') {
      await clickAt(send, parseInt(rest[0]), parseInt(rest[1]));
      console.log(JSON.stringify({ clicked: [parseInt(rest[0]), parseInt(rest[1])] }));
    } else if (cmd === 'eval') {
      const v = await evalJs(send, rest.join(' '));
      console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 1));
    } else if (cmd === 'shot') {
      const r = await send('Page.captureScreenshot', { format: 'png' });
      if (r.error) { console.error('shot failed: ' + r.error.message); process.exit(1); }
      const { writeFileSync } = await import('node:fs');
      const file = rest[0] || `shots/wtui-${Date.now()}.png`;
      writeFileSync(file, Buffer.from(r.result.data, 'base64'));
      console.log('saved ' + file);
    } else {
      console.error('usage: wt-ui.mjs click <text> | click-xy <x> <y> | eval <js> | shot [file]');
      process.exit(2);
    }
  } finally { ws.close(); }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
