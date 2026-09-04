#!/usr/bin/env node
// wt-net-recorder.mjs — persistent CDP network recorder for the WunderTrading page.
// Logs XHR/fetch requests + response bodies to a JSONL file. Kill to stop.
import { appendFileSync } from 'node:fs';

const OUT = process.argv[2] || '/tmp/wt-net-rec.jsonl';

const targets = await fetch('http://127.0.0.1:9222/json').then(r => r.json());
const page = targets.find(t => t.type === 'page' && /wundertrading\.com/.test(t.url) && t.webSocketDebuggerUrl);
if (!page) { console.error('no WT page target'); process.exit(1); }
console.error('recording page:', page.url);

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const reqs = new Map(); // requestId -> {url, method, postData, resourceType}
ws.onmessage = async (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (!m.method) return;
  try {
    if (m.method === 'Network.requestWillBeSent') {
      const p = m.params;
      reqs.set(p.requestId, { url: p.request.url, method: p.request.method, postData: p.request.postData || null, resourceType: p.type, ts: Date.now() });
    } else if (m.method === 'Network.responseReceived') {
      const e = reqs.get(m.params.requestId);
      if (e) { e.status = m.params.response.status; e.mimeType = m.params.response.mimeType; e.type = m.params.type; }
    } else if (m.method === 'Network.loadingFinished') {
      const e = reqs.get(m.params.requestId);
      if (!e) return;
      const isApi = e.type === 'XHR' || e.type === 'Fetch' || /json/i.test(e.mimeType || '');
      const row = { ts: e.ts, method: e.method, url: e.url, status: e.status, type: e.type, mimeType: e.mimeType, postData: e.postData };
      if (isApi && (e.status || 0) < 400) {
        // try to fetch body
        const send = (method, params) => new Promise((res) => {
          const i = ++id; pending.set(i, res);
          try { ws.send(JSON.stringify({ id: i, method, params })); } catch (err) { res({ error: err }); }
          setTimeout(() => { if (pending.has(i)) { pending.delete(i); res({ error: 'timeout' }); } }, 5000);
        });
        const b = await send('Network.getResponseBody', { requestId: m.params.requestId });
        if (b?.result?.body) {
          const txt = b.result.body;
          row.body = txt.length > 20000 ? txt.slice(0, 20000) + '...<TRUNC>' : txt;
          row.base64 = !!b.result.base64Encoded;
        }
      }
      appendFileSync(OUT, JSON.stringify(row) + '\n');
      reqs.delete(m.params.requestId);
    }
  } catch (e) { console.error('handler error:', e.message); }
};
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); setTimeout(() => rej(new Error('ws open timeout')), 5000); });
await send_cmd('Network.enable');
await send_cmd('Page.enable');
console.error('recorder ready ->', OUT);
// keep alive
setInterval(() => {}, 60000);

function send_cmd(method, params = {}) {
  return new Promise((res) => {
    const i = ++id; pending.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
}
