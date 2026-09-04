#!/usr/bin/env node
/**
 * hydration-check.mjs — load a URL in the headful CloakBrowser, collect
 * console/runtime errors, and report whether the page actually rendered.
 *
 * Usage: node hydration-check.mjs [URL] [TIMEOUT_MS]
 *   URL defaults to https://www.tradingview.com/login/
 *   TIMEOUT_MS defaults to 30000
 */
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const URL = process.argv[2] || 'https://www.tradingview.com/login/';
const TIMEOUT = Number(process.argv[3]) || 30000;
const PREFERRED_PORT = Number(process.env.CB_CDP_PORT) || null;

const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) })
  .then((r) => r.ok).catch(() => false);

async function findPort() {
  if (PREFERRED_PORT && await alive(PREFERRED_PORT)) return PREFERRED_PORT;
  for (let port = 9222; port < 9322; port++) {
    if (PREFERRED_PORT && port === PREFERRED_PORT) continue; // already tested above
    if (await alive(port)) return port;
  }
  console.log('No live CDP port; spawning launch.mjs...');
  spawnSync(process.execPath, [join(SCRIPT_DIR, 'launch.mjs')], { stdio: 'inherit' });
  for (let port = 9222; port < 9322; port++) {
    if (await alive(port)) return port;
  }
  throw new Error('CDP still not available after launch.mjs');
}

async function cdpCall(wsUrl, method, params = {}) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    } else {
      events.push(m);
    }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res) => {
    const i = ++id;
    pending.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const result = await send(method, params);
  ws.close();
  return { result, events };
}

async function main() {
  const port = await findPort();
  console.log(`CDP on :${port}`);
  const version = await fetch(`http://127.0.0.1:${port}/json/version`).then(r => r.json());
  const browserWs = version.webSocketDebuggerUrl;

  // Create a fresh page target.
  const created = await cdpCall(browserWs, 'Target.createTarget', { url: 'about:blank', newWindow: false, background: false });
  const targetId = created.result.result.targetId;
  console.log(`Created target ${targetId}`);

  // Resolve its page-level WS url.
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then(r => r.json());
  const pageTarget = targets.find((t) => t.id === targetId || t.targetId === targetId);
  const wsUrl = pageTarget?.webSocketDebuggerUrl;
  if (!wsUrl) throw new Error('Could not resolve page WebSocket');

  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const consoleMsgs = [];
  const exceptions = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method === 'Runtime.consoleAPICalled') {
      const { type, args } = m.params;
      consoleMsgs.push({ type, text: args.map(a => a.value ?? a.description ?? '').join(' ') });
    } else if (m.method === 'Runtime.exceptionThrown') {
      const e = m.params.exceptionDetails;
      exceptions.push({ text: e.text, source: `${e.url || ''}:${e.lineNumber}:${e.columnNumber}`, stack: e.stackTrace?.callFrames?.slice(0,5).map(f => `${f.functionName||'(anon)'} @ ${f.url}:${f.lineNumber}`) });
    }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res) => {
    const i = ++id;
    pending.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params }));
  });

  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  await send('Network.enable');

  console.log(`Navigating to ${URL} ...`);
  const start = Date.now();
  await send('Page.navigate', { url: URL });

  // Wait for load + settle.
  await new Promise(r => setTimeout(r, TIMEOUT));

  const evalExpr = (expr) => send('Runtime.evaluate', { expression: expr, returnByValue: true });

  const readyState = (await evalExpr('document.readyState')).result?.result?.value;
  const title = (await evalExpr('document.title')).result?.result?.value;
  const bodyLen = (await evalExpr('document.body?.children?.length')).result?.result?.value;
  const bodyText = (await evalExpr('document.body?.innerText?.slice(0,200)')).result?.result?.value;
  const usernameInput = (await evalExpr('document.querySelector("input[name=\\\"username\\\"], input[type=\\\"email\\\"]")?.outerHTML?.slice(0,200)')).result?.result?.value;
  const signinControl = (await evalExpr('!!document.querySelector(\'a[href*="/signin"], a[href*="sign-in"], [data-dialog-name="sign-in"], button[aria-label*="Sign in" i]\')')).result?.result?.value;
  const angular = (await evalExpr('!!window.angular')).result?.result?.value;
  const reactRoot = (await evalExpr('!!document.querySelector("#__next,") && !!document.querySelector("#__next").children.length')).result?.result?.value;
  const nextData = (await evalExpr('!!window.__NEXT_DATA__')).result?.result?.value;
  const vue = (await evalExpr('!!window.__VUE__')).result?.result?.value;
  const hydrationError = consoleMsgs.some(m => /hydrat/i.test(m.text)) || (await evalExpr('[...document.querySelectorAll("*")].some(e=>e.innerText?.toLowerCase().includes("hydration"))')).result?.result?.value;

  const elapsed = Date.now() - start;
  console.log('\n=== Hydration check ===');
  console.log(`URL:        ${URL}`);
  console.log(`Elapsed:    ${elapsed}ms`);
  console.log(`readyState: ${readyState}`);
  console.log(`title:      ${title}`);
  console.log(`body kids:  ${bodyLen}`);
  console.log(`body text:  ${bodyText || '(empty)'}`);
  console.log(`sign-in UI: ${signinControl}`);
  console.log(`inputs:     ${usernameInput || '(none)'}`);
  console.log(`frameworks: angular=${angular} next=${reactRoot} __NEXT_DATA__=${nextData} vue=${vue}`);
  console.log(`hydration strings in console/DOM: ${hydrationError}`);

  if (consoleMsgs.length) {
    console.log(`\n--- Console messages (${consoleMsgs.length}) ---`);
    for (const m of consoleMsgs.slice(0, 30)) console.log(`[${m.type}] ${m.text}`);
  }
  if (exceptions.length) {
    console.log(`\n--- Runtime exceptions (${exceptions.length}) ---`);
    for (const e of exceptions.slice(0, 10)) console.log(`${e.text} @ ${e.source}\n${e.stack?.join('\n') || ''}`);
  }

  // Capture screenshot.
  const shot = (await send('Page.captureScreenshot', { format: 'png' })).result?.data;
  const shotPath = join(SCRIPT_DIR, 'shots', `hydration-${URL.replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '').slice(0,80)}-${Date.now()}.png`);
  if (shot) {
    const { mkdirSync, writeFileSync } = await import('node:fs');
    mkdirSync(dirname(shotPath), { recursive: true });
    writeFileSync(shotPath, Buffer.from(shot, 'base64'));
    console.log(`\nScreenshot: ${shotPath}`);
  }

  ws.close();
}

await main().catch(e => { console.error(e); process.exit(1); });
