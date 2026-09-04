#!/usr/bin/env node
// opencode-usage.mjs — programmatic usage/subscription limits for opencode.ai.
//
// opencode.ai is a SolidStart SPA. Its data layer is SolidStart server
// functions served over a single RPC endpoint (POST /_server), keyed by an
// X-Server-Id header; the response is a Seroval script. Auth is a single
// httpOnly `auth` cookie on opencode.ai — no localStorage, no API key, no
// Cloudflare fingerprinting, so plain fetch() works (unlike wt.mjs, which
// must run inside the headful browser).
//
// Usage:
//   node opencode-usage.mjs                # summary: usage limits + subscription
//   node opencode-usage.mjs lite|billing|usage|session   # one endpoint, raw JSON
//   node opencode-usage.mjs --raw lite|billing|usage|session
//
// Session (OPENCODE_AUTH / OPENCODE_WORKSPACE_ID) comes from
// ./secrets/runtime/opencode-session.env (vault item `opencode-session`,
// materialized by bw-provision.sh — see secrets/manifest.json), falling back
// to the process env. Never print the token.
//
// Full protocol + function-hash table: docs/opencode/subs-limits-api.md.
import vm from 'node:vm';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const BASE = 'https://opencode.ai';

// ── session env (vault materialized, env-first) ─────────────────────────────
function parseEnv(p) {
  const out = {};
  if (!existsSync(p)) return out;
  for (const raw of readFileSync(p, 'utf8').split('\n')) {
    const line = raw.trim().replace(/\r$/, '');
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const i = line.indexOf('=');
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
const sess = parseEnv(join(SCRIPT_DIR, 'secrets/runtime/opencode-session.env'));
const AUTH = process.env.OPENCODE_AUTH || sess.OPENCODE_AUTH;
const WORKSPACE = process.env.OPENCODE_WORKSPACE_ID || sess.OPENCODE_WORKSPACE_ID || 'wrk_01KRP126QT8RQH8CTD629YXEZQ';
if (!AUTH) {
  console.error('OPENCODE_AUTH not found (secrets/runtime/opencode-session.env or env)');
  process.exit(1);
}

// ── server-function table (name -> { id, args }) ────────────────────────────
const FUNCS = {
  lite:    { id: 'c7389bd0e731f80f49593e5ee53835475f4e28594dd6bd83eb229bab753498cd', args: [WORKSPACE],                    name: 'lite.subscription.get' },
  billing: { id: 'c83b78a614689c38ebee981f9b39a8b377716db85c1fd7dbab604adc02d3313d', args: [WORKSPACE],                    name: 'billing.get' },
  session: { id: '9bc4808361cdaee17059a8d3822b36ee8c9a0d93f1adc289fa1926998e3c9768', args: [WORKSPACE],                    name: 'session.get' },
  usage:   { id: '15702f3a12ff8bff357f8c2aa154a17e65b746d5f6b96adc9002c86ee0c15205', args: [WORKSPACE, 2026, 8, '+06:00'], name: 'usage.list' },
};

// Seroval arg encoding: string -> {t:1,s}, number -> {t:0,s}
function serializeArgs(args) {
  const a = args.map((v) => (typeof v === 'string' ? { t: 1, s: v } : { t: 0, s: v }));
  return JSON.stringify({ t: { t: 9, i: 0, l: args.length, a, o: 0 }, f: 31, m: [] });
}

async function call(fn) {
  const res = await fetch(`${BASE}/_server`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Server-Id': fn.id,
      'X-Server-Instance': 'server-fn:0',
      Cookie: `auth=${AUTH}`,
    },
    body: serializeArgs(fn.args),
  });
  if (!res.ok) throw new Error(`${fn.name}: HTTP ${res.status}`);
  return parseSeroval(await res.text());
}

// Response is a Seroval script: `;0x<hexlen>;(JS)`. Eval it with
// $R === self.$R === globalThis (self IS the global object), then read the
// `server-fn:0` instance's index 0.
function parseSeroval(text) {
  const js = text.replace(/^;[0-9a-fA-F]+;/, '');
  const sandbox = { Date, Map, Set, ArrayBuffer, Uint8Array, BigInt, RegExp, Error, URL };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  sandbox.$R = {};
  vm.createContext(sandbox);
  vm.runInContext(js, sandbox);
  return sandbox.$R['server-fn:0'][0];
}

const json = (v) => JSON.stringify(v, (k, x) => (x instanceof Date ? x.toISOString() : x), 2);
const usd = (n) => (n == null ? null : `$${(n / 1e8).toFixed(2)}`); // usage/totalCost are 1e-8 USD units

function summary() {
  return Promise.all([call(FUNCS.lite), call(FUNCS.billing), call(FUNCS.session), call(FUNCS.usage)])
    .then(([lite, billing, session, usage]) => {
      const pct = (u) => `${u.usagePercent}%`;
      console.log('session        ', session.isAdmin ? 'admin' : 'user', '| beta:', session.isBeta);
      console.log('subscription   ', billing.subscriptionPlan || 'none',
        billing.liteSubscriptionID ? `(lite: ${billing.liteSubscriptionID.slice(0, 16)}…)` : '');
      console.log('payment        ', `${billing.paymentMethodType} •••• ${billing.paymentMethodLast4}`,
        '| balance $' + billing.balance, '| reload $' + billing.reloadAmount + ' @ $' + billing.reloadTrigger);
      console.log('usage limits   ',
        `rolling ${pct(lite.rollingUsage)} (${usd(lite.rollingUsage.usage)}/${usd(lite.rollingUsage.limit)}, reset ${fmt(lite.rollingUsage.resetInSec)})`,
        `| weekly ${pct(lite.weeklyUsage)} (${usd(lite.weeklyUsage.usage)}/${usd(lite.weeklyUsage.limit)}, reset ${fmt(lite.weeklyUsage.resetInSec)})`,
        `| monthly ${pct(lite.monthlyUsage)} (${usd(lite.monthlyUsage.usage)}/${usd(lite.monthlyUsage.limit)}, reset ${fmt(lite.monthlyUsage.resetInSec)})`);
      console.log('usage by model (Aug):');
      for (const u of usage.usage) console.log(`  ${u.date}  ${u.model.padEnd(26)} ${usd(u.totalCost)}  [${u.plan || 'free'}]`);
      console.log('keys            ', usage.keys.map((k) => k.displayName).join(', ') || '(none)');
    });
}

function fmt(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? `${h}h${m}m` : `${m}m`;
}

const arg = process.argv[2];
if (arg === '--raw') {
  const key = process.argv[3];
  const fn = FUNCS[key];
  if (!fn) { console.error('usage: --raw lite|billing|usage|session'); process.exit(1); }
  call(fn).then((v) => console.log(json(v)));
} else if (arg && FUNCS[arg]) {
  call(FUNCS[arg]).then((v) => console.log(json(v)));
} else {
  summary().catch((e) => { console.error('error:', e.message); process.exit(1); });
}