#!/usr/bin/env node
// wt-login.mjs — WunderTrading credential login via the official CloakBrowser
// stealth driver, + cookie persistence.
//
// Companion to wt.mjs. wt.mjs RESTORES a session from
// secrets/runtime/wt-session.env; when that session is stale this tool logs
// in with WT_EMAIL/WT_PASSWORD in the headful CloakBrowser and re-exports
// fresh cookies back to the same file, in the exact format wt.mjs parses
// (WT_COOKIES_JSON + WT_PHPSESSID + WT_CF_CLEARANCE).
//
// HOW it logs in matters: the WT /en/login form is gated by an invisible
// Google reCAPTCHA (sa=submit), which scores the interaction. Raw CDP
// Runtime.evaluate form-fills + synthetic .click() produce non-trusted
// events and evaluate stack traces — a guaranteed low score, and the login
// POST never completes. So instead we drive the SAME persistent CloakBrowser
// binary that launch.mjs owns (source-level fingerprint patches) through the
// official `cloakbrowser` npm stealth driver: puppeteer-core connects to the
// live CDP endpoint and patchBrowser() swaps page.click/page.type for the
// human layer — Bézier-curve mouse movement with overshoot, per-character
// typing timing with 2% mistype-and-correct simulation, shift symbols via
// CDP Input.dispatchKeyEvent (isTrusted=true), CDP isolated-world state
// checks (no evaluate stack leaks).
//
// Usage:  node wt-login.mjs [outfile]
// Env:    WT_EMAIL, WT_PASSWORD        (required)
//         outfile default: /app/browser-debug/secrets/runtime/wt-session.env
// Needs:  node_modules/puppeteer-core + node_modules/cloakbrowser next to
//         this script (Dockerfile installs both).
//
// Flow: probe grid_bots → (already authed: export cookies, exit 0) →
//       /en/login → humanized click+type email/password → humanized submit →
//       poll auth up to ~45s (invisible reCAPTCHA scoring + Cloudflare
//       interstitials get the window) → export cookies, chmod 600, exit 0 |
//       print last probe + form errors, exit 1.
// The password never appears in any log line. We always browser.disconnect()
// (never close() — the browser belongs to launch.mjs / the entrypoint).
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { connect } from 'puppeteer-core';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const WT_ORIGIN = 'https://wundertrading.com';
const GRID_BOTS_URL = `${WT_ORIGIN}/en/trader/grid_bots`;
const LOGIN_URL = `${WT_ORIGIN}/en/login`;
const OUT_FILE = process.argv[2] || '/app/browser-debug/secrets/runtime/wt-session.env';

const WT_EMAIL = process.env.WT_EMAIL || '';
const WT_PASSWORD = process.env.WT_PASSWORD || '';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── official cloakbrowser stealth driver (human layer, puppeteer edition) ──
// Imported by absolute file path: the package "exports" map only exposes
// "."/"./puppeteer"/"./human", but patchBrowser/patchPage live here.
const HUMAN_PUPPETEER = join(SCRIPT_DIR, 'node_modules', 'cloakbrowser',
  'dist', 'human-puppeteer', 'index.js');
if (!existsSync(HUMAN_PUPPETEER)) {
  console.error('wt-login: cloakbrowser npm package missing — expected at '
    + `${HUMAN_PUPPETEER}. Install with: `
    + 'npm install cloakbrowser puppeteer-core  (in browser-debug/)');
  process.exit(2);
}
const { patchBrowser, resolveConfig } = await import(pathToFileURL(HUMAN_PUPPETEER).href);

// ── ensure headful browser is up (same as wt.mjs) ───────────────────────────
async function ensureBrowserPort() {
  const alive = (port) => fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(600) })
    .then((r) => r.ok).catch(() => false);
  let up = false;
  for (let i = 0; i < 2; i++) { if (await alive(9222)) { up = true; break; } await sleep(300); }
  if (!up) {
    spawnSync(process.execPath, [join(SCRIPT_DIR, 'launch.mjs')], { stdio: 'inherit' });
  }
  for (let p = 9222; p < 9322; p++) {
    for (let i = 0; i < 20; i++) {
      if (await alive(p)) return p;
      await sleep(250);
    }
  }
  throw new Error('no live CDP port');
}

// ── login probe (same signals as wt.mjs; read-only evaluate is fine) ────────
async function probeAuth(page) {
  const p = await page.evaluate(() => {
    const url = location.href;
    const loginCta = document.querySelector('a[href*="/login" i], button[class*="login" i], a[href*="sign-in" i]');
    const loginRedirect = /login|signin|password/i.test(url);
    const cfChallenge = !!document.querySelector('iframe[src*="challenges.cloudflare.com"]');
    return { url, title: document.title, loginCta: !!loginCta, loginRedirect, cfChallenge };
  }).catch(() => ({ url: 'evaluate-failed', title: '', loginCta: true, loginRedirect: true, cfChallenge: false }));
  // A bare Cloudflare interstitial has no login CTA and no /login URL — never
  // mistake it for "authed"; same for mid-navigation evaluate failures.
  const authed = (!p.loginCta && !p.loginRedirect && !p.cfChallenge && p.url !== 'evaluate-failed');
  return { authed, probe: p };
}

// dismiss the CookieHub consent overlay (ch2-*) — its backdrop intercepts
// clicks on the login form below it (verified live: submit only lands after
// consent is accepted)
async function dismissConsent(page) {
  try {
    await page.waitForSelector('.ch2-allow-all-btn', { timeout: 4000 });
    await page.click('.ch2-allow-all-btn');
    await sleep(1200);
    return true;
  } catch { return false; }
}

// visible form errors (wrong creds / captcha message), for diagnostics only
async function formErrors(page) {
  return page.evaluate(() => [...document.querySelectorAll(
    '.error, .invalid-feedback, [class*=error] i, [role=alert], .alert, .recaptcha-error')]
    .map((e) => (e.textContent || '').trim()).filter((t) => t && t.length < 300))
    .catch(() => []);
}

// ── cookie export (same file format wt.mjs parses) ─────────────────────────
async function persistCookies(browser, file) {
  const client = await browser.target().createCDPSession();
  let all = [];
  try {
    // Chrome >= 115: Network.getAllCookies was removed — cookies live in the
    // Storage domain now. No browserContextId = default context = all cookies.
    const r = await client.send('Storage.getCookies');
    all = r.cookies || [];
  } catch {
    const r = await client.send('Network.getAllCookies'); // older Chromium
    all = r.cookies || [];
  }
  await client.detach();
  const wt = all.filter((c) => /wundertrading\.com$/i.test((c.domain || '').replace(/^\./, '')));
  if (!wt.length) return 0;
  const slim = wt.map((c) => ({
    name: c.name, value: c.value, domain: c.domain, path: c.path || '/',
    secure: !!c.secure, httpOnly: !!c.httpOnly,
    ...(typeof c.expires === 'number' && c.expires > 0 ? { expires: c.expires } : {}),
    ...(c.sameSite && ['Strict', 'Lax', 'None'].includes(c.sameSite) ? { sameSite: c.sameSite } : {}),
  }));
  const phpsessid = wt.find((c) => c.name === 'PHPSESSID');
  const clearance = wt.find((c) => c.name === 'cf_clearance');
  const lines = [
    `WT_COOKIES_JSON=${JSON.stringify(slim)}`,
    ...(phpsessid ? [`WT_PHPSESSID=${phpsessid.value}`] : []),
    ...(clearance ? [`WT_CF_CLEARANCE=${clearance.value}`] : []),
    `WT_SESSION_SAVED_AT=${new Date().toISOString()}`,
    '',
  ];
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, lines.join('\n'));
  try { chmodSync(file, 0o600); } catch { /* best-effort */ }
  return slim.length;
}

// ── main ────────────────────────────────────────────────────────────────────
async function main() {
  if (!WT_EMAIL || !WT_PASSWORD) {
    console.error('wt-login: WT_EMAIL and WT_PASSWORD must be set');
    process.exit(2);
  }
  const port = await ensureBrowserPort();

  // Attach the stealth driver to the persistent CloakBrowser.
  const browser = await connect({
    browserURL: `http://127.0.0.1:${port}`,
    defaultViewport: null, // never resize the headful window
  });
  try {
    patchBrowser(browser, resolveConfig('default'));
    await sleep(300); // let patchBrowser's in-flight pages() pass settle

    // Prefer a patched NEW page (patchBrowser wraps newPage → guaranteed
    // humanized); fall back to an existing WT tab.
    let page = null;
    try {
      page = await browser.newPage();
    } catch { /* connected browsers without newPage perms — fall through */ }
    if (!page) {
      const pages = await browser.pages();
      page = pages.find((p) => /wundertrading\.com/.test(p.url())) || pages[0];
    }
    if (!page) throw new Error('no page target');

    // (a) already authed? → just re-export cookies
    await page.goto(GRID_BOTS_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
    let { authed, probe } = await probeAuth(page);
    if (!authed) { await sleep(5000); ({ authed, probe } = await probeAuth(page)); }
    if (authed) {
      const n = await persistCookies(browser, OUT_FILE);
      console.log(`wt-login: already authed (${probe.url}) — ${n} cookies persisted to ${OUT_FILE}`);
      process.exit(n > 0 ? 0 : 1);
    }

    // (b) login page, humanized fill + submit
    console.log('wt-login: not authed — loading login form…');
    await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.bringToFront();
    if (await dismissConsent(page)) console.log('wt-login: cookie consent dismissed');
    const emailSel = 'input[type=email], input[name*="username" i], input[id*="email" i]';
    const passSel = 'input[type=password]';
    await page.waitForSelector(emailSel, { timeout: 20000 });
    await page.waitForSelector(passSel, { timeout: 5000 });

    // Humanized: Bézier mouse to field, click, per-char typing w/ mistypes.
    await page.click(emailSel);
    await page.type(emailSel, WT_EMAIL);
    await sleep(800 + Math.random() * 700); // field switch pause
    await page.click(passSel);
    await page.type(passSel, WT_PASSWORD);
    await sleep(600 + Math.random() * 600); // human beat before submit
    console.log('wt-login: credentials entered — submitting…');
    // WT's Login button (class g-recaptcha login-button) is NOT type=submit
    const btnSel = 'button.g-recaptcha, button.login-button, button[type=submit], input[type=submit]';
    const via = await page.evaluate((s) => (document.querySelector(s) ? 'button' : 'form'), btnSel)
      .catch(() => 'form');
    if (via === 'button') {
      await page.click(btnSel); // humanized click (aim delay + curve)
    } else {
      await page.keyboard.press('Enter'); // focused password input
    }
    console.log(`wt-login: submitted via ${via} — waiting for session…`);

    // (c) poll up to ~75s; the invisible reCAPTCHA executes on submit and
    //     Cloudflare interstitials can add a LOT of seconds on datacenter
    //     IPs (az00: a login took ~4 min wall-clock behind "Just a
    //     moment…" pages — the entrypoint's `timeout 300` is the real
    //     ceiling; cfChallenge pages keep this loop waiting). Probe in
    //     place first, then confirm on grid_bots.
    let last = probe;
    let errors = [];
    for (let i = 0; i < 25; i++) {
      await sleep(3000);
      if (i === 0) errors = await formErrors(page);
      ({ authed, probe } = await probeAuth(page));
      last = probe;
      if (authed) break;
      if (probe.cfChallenge) continue; // interstitial in progress — keep waiting
      // still on /login after the first two probes → confirm on a fresh hit
      if (i >= 1 && !authed) {
        try {
          await page.goto(GRID_BOTS_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
          ({ authed, probe } = await probeAuth(page));
          last = probe;
          if (authed) break;
          await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
        } catch { /* keep polling */ }
      }
    }

    // (d)
    if (authed) {
      const n = await persistCookies(browser, OUT_FILE);
      console.log(`WT login OK — ${n} cookies persisted to ${OUT_FILE}`);
      process.exit(n > 0 ? 0 : 1);
    }
    console.error(`wt-login: AUTH FAIL — last probe: ${JSON.stringify(last)}`
      + (errors.length ? ` form errors: ${JSON.stringify(errors)}` : ''));
    process.exit(1);
  } finally {
    await browser.disconnect().catch(() => {});
  }
}

main().catch((e) => { console.error(`wt-login: ${String(e?.message || e)}`); process.exit(1); });
