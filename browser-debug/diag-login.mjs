#!/usr/bin/env node
// diag-login.mjs — one-off diagnostic for the WT login flow (NOT shipped).
// Dismisses cookie consent, fills the form humanized, clicks submit, and
// records: network requests (method+url+status only), navigations, page
// state at the end. Never logs request bodies or field values.
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { connect } from 'puppeteer-core';

const SCRIPT_DIR = fileURLToPath(new URL('.', import.meta.url));
const WT = 'https://wundertrading.com';
const { patchBrowser, resolveConfig } = await import(pathToFileURL(join(
  SCRIPT_DIR, 'node_modules/cloakbrowser/dist/human-puppeteer/index.js')).href);

const EMAIL = process.env.WT_EMAIL, PASS = process.env.WT_PASSWORD;
if (!EMAIL || !PASS) { console.error('need WT_EMAIL/WT_PASSWORD'); process.exit(2); }

const browser = await connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
try {
  patchBrowser(browser, resolveConfig('default'));
  await new Promise(r => setTimeout(r, 300));
  const page = await browser.newPage();
  const cdp = await page.createCDPSession();
  const net = [];
  cdp.on('Network.requestWillBeSent', (e) => {
    const u = new URL(e.request.url);
    if (/wundertrading\.com|recaptcha|google/.test(u.hostname)) {
      net.push({ m: e.request.method, u: u.origin + u.pathname + (u.search ? '?' + u.search.slice(0, 60) : ''), id: e.requestId });
    }
  });
  cdp.on('Network.responseReceived', (e) => {
    const i = net.findIndex(n => n.id === e.requestId);
    if (i >= 0) net[i].s = e.response.status;
  });
  await cdp.send('Network.enable');
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) console.log('NAV →', f.url()); });
  page.on('console', (m) => { const t = m.text(); if (/error|captcha|recaptcha/i.test(t)) console.log('CONSOLE:', t.slice(0, 200)); });

  await page.goto(`${WT}/en/login`, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.bringToFront();
  // 1. dismiss cookie consent like a human would
  try {
    await page.waitForSelector('.ch2-allow-all-btn, .ch2-btn', { timeout: 6000 });
    console.log('consent banner present — accepting…');
    await page.click('.ch2-allow-all-btn');
    await new Promise(r => setTimeout(r, 1200));
  } catch { console.log('no consent banner visible'); }

  // 2. fill humanized
  await page.waitForSelector('input[type=email]', { timeout: 20000 });
  await page.click('input[type=email]');
  await page.type('input[type=email]', EMAIL);
  await new Promise(r => setTimeout(r, 900));
  await page.click('input[type=password]');
  await page.type('input[type=password]', PASS);
  await new Promise(r => setTimeout(r, 700));
  console.log('filled. clicking submit (g-recaptcha bound)…');

  // 3. click and observe
  await page.click('button.g-recaptcha, button.login-button');
  console.log('clicked. observing 60s…');
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const st = await page.evaluate(() => ({
      url: location.href,
      emailLen: (document.querySelector('input[type=email]')?.value || '').length,
      passLen: (document.querySelector('input[type=password]')?.value || '').length,
      recaptchaVisible: (() => {
        const f = [...document.querySelectorAll('iframe')].find(x => /recaptcha/.test(x.src || ''));
        return f ? { w: f.offsetWidth, h: f.offsetHeight } : null;
      })(),
      errs: [...document.querySelectorAll('.error, .invalid-feedback, [role=alert], .alert, [class*=notification]')]
        .map(e => (e.textContent || '').trim()).filter(t => t && t.length < 200),
    })).catch(() => ({ url: 'evaluate-failed' }));
    console.log(`t+${(i + 1) * 3}s`, JSON.stringify(st));
    if (/grid_bots|dashboard/.test(st.url || '')) { console.log('LOOKS AUTHED'); break; }
  }
  console.log('=== network (method url status) ===');
  for (const n of net) console.log(n.m, n.s || '…', n.u);
} finally {
  await browser.disconnect().catch(() => {});
}
