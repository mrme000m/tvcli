#!/usr/bin/env node
/**
 * launch.mjs — portable CloakBrowser launcher (self-contained, no npm deps).
 *
 * 1. Resolves the newest free-tier build available for this platform
 *    (GitHub Releases API, same logic as the official cloakbrowser package).
 * 2. Downloads it into THIS folder (<folder>/cloakbrowser/chromium-<version>/…)
 *    so the whole thing stays portable; reuses an existing local copy.
 * 3. Launches it HEADFUL with CDP (remote debugging) on the default port
 *    9222, or the next free port if 9222 is taken.
 *
 * Usage:  node launch.mjs [--force]     # --force = re-download the binary
 * Env:    CB_PROFILE=<dir>              # user-data-dir (default <folder>/profile)
 */

import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import {
  chmodSync, closeSync, createWriteStream, existsSync, mkdirSync, openSync, readFileSync,
  readdirSync, renameSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// Platform
// ---------------------------------------------------------------------------

function platformTag() {
  const map = {
    'darwin-arm64': 'darwin-arm64',
    'darwin-x64': 'darwin-x64',
    'linux-x64': 'linux-x64',
    'linux-arm64': 'linux-arm64',
    'win32-x64': 'windows-x64',
  };
  const tag = map[`${process.platform}-${process.arch}`];
  if (!tag) throw new Error(`unsupported platform: ${process.platform} ${process.arch}`);
  return tag;
}

// Fallback when the GitHub API is unreachable (versions the package ships for).
const FALLBACK_VERSIONS = {
  'darwin-arm64': '145.0.7632.109.2',
  'darwin-x64': '145.0.7632.109.2',
  'linux-x64': '146.0.7680.177.5',
  'linux-arm64': '146.0.7680.177.3',
  'windows-x64': '146.0.7680.177.5',
};

const TAG = platformTag();
const EXT = TAG.startsWith('windows') ? '.zip' : '.tar.gz';
const ARCHIVE = `cloakbrowser-${TAG}${EXT}`;

const GITHUB_API = 'https://api.github.com/repos/CloakHQ/cloakbrowser/releases?per_page=10';
const PRIMARY_BASE = 'https://cloakbrowser.dev';
const GITHUB_BASE = 'https://github.com/CloakHQ/cloakbrowser/releases/download';

// ---------------------------------------------------------------------------
// Version resolution ("latest" free build with an asset for this platform)
// ---------------------------------------------------------------------------

async function latestVersion() {
  try {
    const res = await fetch(GITHUB_API, { headers: { 'User-Agent': 'cloakbrowser-portable' } });
    if (!res.ok) throw new Error(`GitHub API ${res.status}`);
    const releases = await res.json();
    for (const r of releases) {
      const tag = r.tag_name || '';
      if (!tag.startsWith('chromium-v') || r.draft) continue;
      const names = new Set((r.assets || []).map((a) => a.name));
      if (names.has(ARCHIVE)) return tag.slice('chromium-v'.length);
    }
    throw new Error('no release with a binary for this platform');
  } catch (err) {
    console.warn(`  (version lookup failed: ${err.message}; using bundled fallback)`);
    return FALLBACK_VERSIONS[TAG];
  }
}

// ---------------------------------------------------------------------------
// Download + extract into <SCRIPT_DIR>/cloakbrowser/chromium-<version>/
// ---------------------------------------------------------------------------

function binaryPath(version) {
  const dir = join(SCRIPT_DIR, 'cloakbrowser', `chromium-${version}`);
  if (TAG.startsWith('darwin')) return { dir, bin: join(dir, 'Chromium.app', 'Contents', 'MacOS', 'Chromium') };
  if (TAG.startsWith('windows')) return { dir, bin: join(dir, 'chrome.exe') };
  return { dir, bin: join(dir, 'chrome') };
}

async function download(url, dest) {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
  const total = Number(res.headers.get('content-length') || 0);
  let got = 0, lastPct = -1;
  const ws = createWriteStream(dest);
  for await (const chunk of res.body) {
    ws.write(chunk);
    got += chunk.length;
    if (total > 0) {
      const pct = Math.floor((got / total) * 100);
      if (pct >= lastPct + 10) { lastPct = pct; console.log(`  ${pct}% (${Math.round(got / 1048576)} MB)`); }
    }
  }
  await new Promise((resolve, reject) => ws.end((e) => (e ? reject(e) : resolve())));
  console.log(`  downloaded ${Math.round(got / 1048576)} MB`);
}

async function checksum(version, file) {
  for (const base of [PRIMARY_BASE, GITHUB_BASE]) {
    try {
      const res = await fetch(`${base}/chromium-v${version}/SHA256SUMS`, { redirect: 'follow' });
      if (!res.ok) continue;
      const text = await res.text();
      for (const line of text.split('\n')) {
        const [hash, name] = line.trim().split(/\s+/);
        if (name === ARCHIVE) {
          const actual = createHash('sha256').update(file).digest('hex');
          if (actual !== hash.toLowerCase()) throw new Error('SHA-256 mismatch');
          console.log('  checksum verified (SHA-256)');
          return;
        }
      }
    } catch {}
  }
  console.warn('  (SHA256SUMS unavailable — skipping checksum verification)');
}

async function ensureBinary(version, force) {
  const { dir, bin } = binaryPath(version);
  if (!force && existsSync(bin)) {
    console.log(`  using local copy: ${bin}`);
    return bin;
  }
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });

  const tmp = join(dir, `..`, `.download-${version}${EXT}`);
  const urls = [
    `${PRIMARY_BASE}/chromium-v${version}/${ARCHIVE}`,
    `${GITHUB_BASE}/chromium-v${version}/${ARCHIVE}`,
  ];
  let ok = false;
  for (const url of urls) {
    try {
      console.log(`  fetching ${url}`);
      await download(url, tmp);
      ok = true;
      break;
    } catch (err) {
      console.warn(`  (failed: ${err.message})`);
    }
  }
  if (!ok) throw new Error(`could not download ${ARCHIVE} for chromium-v${version}`);

  await checksum(version, readFileSync(tmp));

  console.log(`  extracting to ${dir}`);
  spawnSyncTar(['-xf', tmp, '-C', dir]);

  // Flatten a single wrapping directory, but never a .app bundle.
  const entries = readdirSync(dir).filter((n) => !n.startsWith('.'));
  if (entries.length === 1) {
    const only = join(dir, entries[0]);
    if (statSync(only).isDirectory() && !entries[0].endsWith('.app')) {
      for (const item of readdirSync(only)) renameSync(join(only, item), join(dir, item));
      rmSync(only, { recursive: true });
    }
  }
  rmSync(tmp, { force: true });

  if (!TAG.startsWith('windows')) chmodSync(bin, 0o755);
  if (TAG.startsWith('darwin')) { // remove quarantine xattrs so Gatekeeper doesn't block it
    const r = spawnSync('xattr', ['-cr', dir], { stdio: 'inherit' });
    if (r.status !== 0) throw new Error(`xattr -cr failed (status ${r.status})`);
  }
  if (!existsSync(bin)) throw new Error(`binary missing after extraction: ${bin}`);
  return bin;
}

function spawnSyncTar(args) {
  const r = spawnSync('tar', args, { stdio: 'inherit' });
  if (r.status !== 0) throw new Error(`tar ${args[0]} failed (status ${r.status})`);
}

// ---------------------------------------------------------------------------
// CDP port: default 9222, else the next free port
// ---------------------------------------------------------------------------

function cdpAlive(port) {
  return fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) })
    .then((r) => r.ok)
    .catch(() => false);
}

function portFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

async function pickPort() {
  for (let port = 9222; port < 9322; port++) {
    if (await cdpAlive(port)) continue; // already a debugging session — don't clobber
    if (await portFree(port)) return port;
  }
  throw new Error('no free port found in 9222..9321');
}

async function printSession(port, pid) {
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then((r) => r.json()).catch(() => []);
  const pages = targets.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  console.log('\n=== CDP ready ===');
  console.log(`cdp:   http://127.0.0.1:${port}`);
  if (pid) console.log(`pid:   ${pid}`);
  for (const p of pages) console.log(`page:  ${p.title || '(new tab)'}  ->  ${p.webSocketDebuggerUrl}`);
  console.log('\nattach bdg (page-level WS required, browser-level WS fails):');
  for (const p of pages.slice(0, 1)) {
    console.log(`  node ./bdg/dist/index.js --chrome-ws-url ${p.webSocketDebuggerUrl}`);
  }
  console.log(`  curl http://127.0.0.1:${port}/json   # list all targets`);
}

// ---------------------------------------------------------------------------
// Launch
// ---------------------------------------------------------------------------

async function main() {
  const force = process.argv.includes('--force');
  // US SOCKS5 proxy (same IPVanish creds the remote CBM at clk.mrme.tech uses).
  // Pass --proxy <socks5://user:pass@host:port> or set CB_PROXY. Chromium applies
  // this via --proxy-server; the stealth-patched CloakBrowser handles SOCKS5
  // user/pass auth embedded in the URL (plain Chromium ignores SOCKS5 creds).
  const proxyIdx = process.argv.indexOf('--proxy');
  const proxy = (proxyIdx !== -1 ? process.argv[proxyIdx + 1] : process.env.CB_PROXY) || '';
  console.log(`CloakBrowser portable — platform ${TAG}${proxy ? ` (proxy: ${proxy.replace(/:[^:@/]*@/, ':***@')})` : ''}`);

  const version = await latestVersion();
  console.log(`latest free build for ${TAG}: chromium-v${version}`);
  const bin = await ensureBinary(version, force);

  // Idempotent re-runs: if a CDP session is already up, attach to it instead
  // of launching a second instance against the same profile dir. Pass --new
  // (or CB_NEW_INSTANCE=1) to force a fresh browser even when another is alive.
  const forceNew = process.argv.includes('--new') || process.env.CB_NEW_INSTANCE === '1';
  if (!forceNew) {
    for (let port = 9222; port < 9322; port++) {
      if (await cdpAlive(port)) {
        await printSession(port, null);
        console.log(`\nCDP already running on :${port} — attached above (no new launch).`);
        console.log('Pass --new to launch a second instance with a different CB_PROFILE.');
        process.exit(0);
      }
    }
  }

  const port = await pickPort();
  const profile = process.env.CB_PROFILE || join(SCRIPT_DIR, 'profile');

  // Fresh-profile mode: wipe the profile before launch. Useful when a stale or
  // corrupted profile is causing render/hydration issues (set CB_FRESH_PROFILE=1).
  // For normal session persistence leave it unset so cookies/localStorage survive.
  if (process.env.CB_FRESH_PROFILE === '1') {
    console.log(`  fresh profile requested — removing ${profile}`);
    rmSync(profile, { recursive: true, force: true });
  }

  mkdirSync(profile, { recursive: true });

  // Stale-profile recovery (containers / hard kills): a Chromium process from
  // a previous container generation leaves SingletonLock / SingletonSocket /
  // SingletonCookie behind, and the next launch aborts with "The profile
  // appears to be in use by another Chromium process" — CDP never comes up.
  // We just proved no live CDP session exists (loop above), so any lock
  // present here is stale: remove it.
  for (const f of ['SingletonLock', 'SingletonSocket', 'SingletonCookie']) {
    rmSync(join(profile, f), { force: true });
  }

  // Resolve the proxy into the actual --proxy-server value. Authed SOCKS5 →
  // local relay; everything else → passed through verbatim.
  let proxyArg = '';
  if (proxy) {
    const m = proxy.match(/^(socks5h?):\/\/([^:@/]+):[^:@/]+@([^:/]+)(:\d+)?/i);
    if (m) {
      // SOCKS5 with inline credentials → spawn the local no-auth relay.
      let relayPort;
      for (let p = 11080; p < 11180; p++) if (await portFree(p)) { relayPort = p; break; }
      if (!relayPort) throw new Error('no free port for SOCKS5 relay (11080..11179)');
      const relayChild = spawn(process.execPath, [join(SCRIPT_DIR, 'socks-relay.mjs'), String(relayPort), proxy], {
        stdio: ['ignore', 'pipe', 'pipe'], detached: true,
      });
      relayChild.stderr.pipe(createWriteStream(join(profile, 'socks-relay.stderr.log')));
      relayChild.stdout.pipe(createWriteStream(join(profile, 'socks-relay.stdout.log')));
      relayChild.unref();
      // Wait for the relay to start listening before handing off to Chromium.
      for (let i = 0; i < 40; i++) {
        if (!(await portFree(relayPort))) break; // listening => not free
        await new Promise((r) => setTimeout(r, 250));
      }
      proxyArg = `socks5://127.0.0.1:${relayPort}`;
      console.log(`  SOCKS5 auth relay on 127.0.0.1:${relayPort} -> ${m[3]}${m[4] || ':1080'}`);
    } else {
      proxyArg = proxy;
    }
  }

  // Extra Chromium args passed after a literal `--` separator.
  const dashIdx = process.argv.indexOf('--');
  const extraChromeArgs = dashIdx !== -1 ? process.argv.slice(dashIdx + 1) : [];

  // Tuning: match the official CloakBrowser wrapper defaults as closely as
  // possible when launching the binary directly. These flags are what make
  // modern client-side apps (React/Next.js, TradingView, WunderTrading, etc.)
  // hydrate and render reliably inside a Docker/Xvfb environment.
  const useSandbox = process.env.CB_SANDBOX === '1';
  const ignoreGpuBlocklist = process.env.CB_IGNORE_GPU_BLOCKLIST !== '0';
  const disableShm = process.env.CB_DISABLE_DEV_SHM_USAGE !== '0';
  const fingerprint = process.env.CB_FINGERPRINT || String(10000 + Math.floor(Math.random() * 90000));
  const platform = TAG.startsWith('darwin') ? 'macos' : 'windows';
  const locale = process.env.CB_LOCALE || process.env.LANG?.split('.')[0];
  const timezone = process.env.CB_TIMEZONE || process.env.TZ;

  const args = [
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    // The binary is intended for automation; disable the sandbox for the same
    // reason the official wrapper does. Opt back in with CB_SANDBOX=1.
    ...(useSandbox ? [] : ['--no-sandbox', '--disable-setuid-sandbox']),
    // /dev/shm is tiny in containers (64 MB in this Codespace) and Chromium's
    // renderer can OOM/wedge on large hydrated apps. Use disk-backed tmp instead.
    ...(disableShm ? ['--disable-dev-shm-usage'] : []),
    // Headful inside Docker/Xvfb presents a software GPU. Without this, Chromium
    // blocks WebGL and pages that depend on it (charts, maps, WebGL backgrounds)
    // can fail client-side hydration while CDP itself looks healthy. The official
    // wrapper adds this for headed launches and on Windows.
    ...(ignoreGpuBlocklist ? ['--ignore-gpu-blocklist'] : []),
    // Prevent the browser from throttling timers/net when the window is not
    // focused. Agents drive it over CDP; a throttled page can stall hydration.
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    // Keep the browser from asking to save passwords / use the OS keychain.
    '--password-store=basic',
    '--use-mock-keychain',
    ...(['linux', 'win32'].includes(process.platform) ? ['--disable-features=LockProfileCookieDatabase'] : []),
    ...(proxyArg ? [`--proxy-server=${proxyArg}`] : []),
    `--fingerprint=${fingerprint}`,
    `--fingerprint-platform=${platform}`,
    ...(locale ? [`--lang=${locale}`, `--fingerprint-locale=${locale}`] : []),
    ...(timezone ? [`--fingerprint-timezone=${timezone}`] : []),
    ...(process.env.CB_STORAGE_QUOTA ? [`--fingerprint-storage-quota=${process.env.CB_STORAGE_QUOTA}`] : []),
    ...(process.env.CB_WEBRTC_IP ? [`--fingerprint-webrtc-ip=${process.env.CB_WEBRTC_IP}`] : []),
    `--user-data-dir=${profile}`,
    ...(process.env.CB_WINDOW_SIZE ? [`--window-size=${process.env.CB_WINDOW_SIZE}`] : process.env.CB_START_MAXIMIZED === '1' ? ['--start-maximized'] : ['--window-size=1600,1000']),
    '--no-first-run',
    ...extraChromeArgs,
  ];
  console.log(`launching headful on CDP port ${port}...`);
  console.log(`  ${bin}`);
  console.log(`  profile: ${profile}`);

  let stderrLog, childPid;
  if (process.argv.includes('--direct') || !TAG.startsWith('darwin')) {
    // Raw spawn (debugging / non-macOS). NOTE: on macOS this registers the app
    // as BackgroundOnly — process + CDP work but NO window appears.
    // stderr goes DIRECTLY to the log file (not through a launcher pipe): once
    // launch.mjs exits, a piped stderr's read end closes and the child's next
    // stderr write SIGPIPE-kills Chromium — browsers died minutes after launch
    // with no visible cause whenever the launcher had already exited.
    stderrLog = join(profile, 'browser.stderr.log');
    const errFd = openSync(stderrLog, 'a');
    const child = spawn(bin, args, { stdio: ['ignore', 'ignore', errFd], detached: true });
    childPid = child.pid;
    child.on('error', (e) => { console.error(`spawn failed: ${e.message}`); process.exit(1); });
    child.unref();
    closeSync(errFd);
  } else {
    // Launch through the GUI (Aqua) domain via a one-shot LaunchAgent. Raw
    // spawn / `open` from a non-GUI shell registers the app BackgroundOnly and
    // it never gets a window; bootstrapping into gui/<uid> gives the app a real
    // Aqua session, so the window actually appears on the display.
    const app = join(SCRIPT_DIR, 'cloakbrowser', `chromium-${version}`, 'Chromium.app');
    const uid = process.getuid ? process.getuid() : 501;
    const label = `local.cbm.cloakbrowser.${Date.now()}`;
    const plist = join(tmpdir(), `${label}.plist`);
    const flags = args.map((a) => `    <string>${a}</string>`).join('\n');
    writeFileSync(plist, `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-n</string>
    <string>${app}</string>
    <string>--args</string>
${flags}
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
`);
    console.log(`  launching via GUI-domain LaunchAgent (${label})`);
    const b = spawnSync('launchctl', ['bootstrap', `gui/${uid}`, plist], { stdio: 'pipe' });
    if (b.status !== 0) throw new Error(`launchctl bootstrap failed: ${b.stderr}`);
    rmSync(plist, { force: true });
  }

  // The REAL port Chrome bound lives in DevToolsActivePort (Chrome auto-advances
  // if the requested port got taken between our probe and the bind).
  const activePortFile = join(profile, 'DevToolsActivePort');
  let actual = port;
  for (let i = 0; i < 60; i++) {
    if (existsSync(activePortFile)) {
      const line = readFileSync(activePortFile, 'utf8').split('\n')[0].trim();
      if (line) actual = Number(line);
      break;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  for (let i = 0; i < 20; i++) {
    if (await cdpAlive(actual)) break;
    if (i === 19) {
      console.error(`CDP not reachable on :${actual}${stderrLog ? ` (stderr: ${stderrLog})` : ''}`);
      console.error('The LaunchAgent may have failed to start the app — check `launchctl print gui/$(id -u)` for errors.');
      process.exit(1);
    }
    await new Promise((r) => setTimeout(r, 500));
  }

  await printSession(actual, childPid);

  // Browser is detached — this launcher can exit; keep the window alive.
  setTimeout(() => process.exit(0), 150);
}

await main().catch((err) => { console.error(`error: ${err.message}`); process.exit(1); });