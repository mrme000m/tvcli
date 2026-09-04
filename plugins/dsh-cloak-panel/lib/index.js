/**
 * dsh-cloak-panel — host plugin
 * Exposes /cloak/* HTTP routes that proxy the headful CloakBrowser CDP
 * (browser-debug/launch.mjs, port 9222..9321) to the web GUI.
 * No auth: same-origin via DSH webServer (trusted local).
 * @module dsh-cloak-panel
 */
export const name = 'dsh-cloak-panel'
export const inject = []

const CDP_START = 9222
const CDP_END = 9322
const DEFAULT_PROFILE = process.env.CB_PROFILE || ''
const CLOAK_DIR = process.env.CLOAK_DIR || '/workspaces/tvcli/browser-debug'

// ---------- CDP helpers ----------

async function cdpAlive(port) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) })
    return r.ok
  } catch { return false }
}

async function findCdpPort() {
  for (let p = CDP_START; p < CDP_END; p++) if (await cdpAlive(p)) return p
  return null
}

async function getTargets(port) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json`, { signal: AbortSignal.timeout(1500) })
    if (!r.ok) return []
    return await r.json()
  } catch { return [] }
}

function pickTarget(targets) {
  if (!targets.length) return null
  // Prefer visible page with content, prefer wundertrading/tradingview if present
  const pages = targets.filter(t => t.type === 'page' && t.webSocketDebuggerUrl)
  if (!pages.length) return null
  const wt = pages.find(t => /wundertrading\.com/i.test(t.url || ''))
  if (wt) return wt
  const tv = pages.find(t => /tradingview\.com/i.test(t.url || ''))
  if (tv) return tv
  return pages[0]
}

// WS helper
function connectWs(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url)
    let id = 0
    const pending = new Map()
    const timer = setTimeout(() => reject(new Error('WS connect timeout')), 4000)
    ws.onopen = () => { clearTimeout(timer); resolve({ ws, send }) }
    ws.onerror = (e) => { clearTimeout(timer); reject(e) }
    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data)
        if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
      } catch {}
    }
    ws.onclose = () => { for (const [, cb] of pending) cb({ error: { message: 'WS closed' } }) }
    function send(method, params = {}) {
      return new Promise((res) => {
        const i = ++id
        pending.set(i, res)
        try { ws.send(JSON.stringify({ id: i, method, params })) } catch (e) { pending.delete(i); res({ error: { message: String(e) } }) }
      })
    }
  })
}

async function cdpCall(target, method, params) {
  const { ws, send } = await connectWs(target.webSocketDebuggerUrl)
  try { return await send(method, params) } finally { try { ws.close() } catch {} }
}

async function captureScreenshot(target, opts = {}) {
  const { ws, send } = await connectWs(target.webSocketDebuggerUrl)
  try {
    const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false, ...opts })
    if (r.error) throw new Error(r.error.message || JSON.stringify(r.error))
    return r.result?.data || null
  } finally { try { ws.close() } catch {} }
}

// Throttle screenshots: at most 1 per 800ms per process
let lastShotAt = 0
let lastShotBase64 = null

// ---------- route handler helpers ----------
function json(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) })
  res.end(body)
}
async function readJson(req) {
  let raw = ''
  for await (const c of req) raw += c
  if (!raw) return {}
  try { return JSON.parse(raw) } catch { return {} }
}

// ---------- plugin apply ----------
export function apply(ctx) {
  const log = ctx.logger || console

  // Host service for programmatic use
  ctx.provide('cloakPanel', {
    findCdpPort,
    getTargets,
    captureScreenshot,
  })

  // Register HTTP routes under /cloak/*
  ctx.inject(['webServer'], (webCtx) => {
    const server = webCtx.get('webServer')
    if (!server) return

    const routes = []

    // GET /cloak/status
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/status',
      handler: async (req, res) => {
        if (req.method !== 'GET') { res.writeHead(405); res.end(); return }
        const port = await findCdpPort()
        if (port === null) {
          json(res, 200, { alive: false, port: null, targets: [], hint: 'No CDP session. Run: node browser-debug/launch.mjs (or node browser-debug/tv.mjs / wt.mjs) from the workspace.' })
          return
        }
        const targets = await getTargets(port)
        const cur = pickTarget(targets)
        json(res, 200, {
          alive: true, port, targets,
          current: cur ? { id: cur.id, title: cur.title, url: cur.url, type: cur.type, ws: !!cur.webSocketDebuggerUrl } : null,
          profile: DEFAULT_PROFILE || null,
        })
      }
    }))

    // GET /cloak/screenshot?targetId=...&w=...  (PNG binary)
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/screenshot',
      handler: async (req, res) => {
        if (req.method !== 'GET') { res.writeHead(405); res.end(); return }
        const url = new URL(req.url || '/cloak/screenshot', 'http://x')
        const targetId = url.searchParams.get('targetId')
        const port = await findCdpPort()
        if (port === null) { json(res, 503, { ok: false, error: 'CDP offline' }); return }
        const targets = await getTargets(port)
        let target = targetId ? targets.find(t => t.id === targetId) : pickTarget(targets)
        if (!target) target = pickTarget(targets)
        if (!target?.webSocketDebuggerUrl) { json(res, 404, { ok: false, error: 'no page target' }); return }
        // throttle
        const now = Date.now()
        if (now - lastShotAt < 700 && lastShotBase64) {
          const buf = Buffer.from(lastShotBase64, 'base64')
          res.writeHead(200, { 'content-type': 'image/png', 'content-length': buf.length, 'cache-control': 'no-store' })
          res.end(buf)
          return
        }
        try {
          const b64 = await captureScreenshot(target)
          if (!b64) { json(res, 500, { ok: false, error: 'empty screenshot' }); return }
          lastShotAt = now; lastShotBase64 = b64
          const buf = Buffer.from(b64, 'base64')
          res.writeHead(200, { 'content-type': 'image/png', 'content-length': buf.length, 'cache-control': 'no-store' })
          res.end(buf)
        } catch (e) {
          log.warn(`cloak screenshot failed: ${e?.message || e}`)
          json(res, 500, { ok: false, error: String(e?.message || e).slice(0, 400) })
        }
      }
    }))

    // POST /cloak/navigate  { url, targetId? }
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/navigate',
      handler: async (req, res) => {
        if (req.method !== 'POST') { res.writeHead(405); res.end(); return }
        const body = await readJson(req)
        const url = String(body.url || '').trim()
        if (!url) { json(res, 400, { ok: false, error: 'url required' }); return }
        const port = await findCdpPort()
        if (port === null) { json(res, 503, { ok: false, error: 'CDP offline' }); return }
        const targets = await getTargets(port)
        let target = body.targetId ? targets.find(t => t.id === body.targetId) : pickTarget(targets)
        if (!target?.webSocketDebuggerUrl) { json(res, 404, { ok: false, error: 'no target' }); return }
        try {
          const r = await cdpCall(target, 'Page.navigate', { url })
          json(res, 200, { ok: true, result: r.result || null })
        } catch (e) { json(res, 500, { ok: false, error: String(e?.message || e).slice(0, 400) }) }
      }
    }))

    // POST /cloak/click  { x, y, xRatio, yRatio, targetId? }  click in viewport coords
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/click',
      handler: async (req, res) => {
        if (req.method !== 'POST') { res.writeHead(405); res.end(); return }
        const body = await readJson(req)
        const port = await findCdpPort()
        if (port === null) { json(res, 503, { ok: false, error: 'CDP offline' }); return }
        const targets = await getTargets(port)
        let target = body.targetId ? targets.find(t => t.id === body.targetId) : pickTarget(targets)
        if (!target?.webSocketDebuggerUrl) { json(res, 404, { ok: false, error: 'no target' }); return }
        let { x, y, xRatio, yRatio } = body
        // ratio mode: client sends normalized coords to avoid viewport-size coupling
        if ((xRatio !== undefined || yRatio !== undefined)) {
          // need layout metrics to translate
          try {
            const { ws, send } = await connectWs(target.webSocketDebuggerUrl)
            try {
              const m = await send('Page.getLayoutMetrics')
              const w = m.result?.cssContentSize?.width || m.result?.layoutViewport?.clientWidth || 1280
              const h = m.result?.cssContentSize?.height || m.result?.layoutViewport?.clientHeight || 800
              x = Math.round((xRatio ?? 0) * w)
              y = Math.round((yRatio ?? 0) * h)
            } finally { ws.close() }
          } catch { x = Math.round((xRatio ?? 0) * 1280); y = Math.round((yRatio ?? 0) * 800) }
        }
        x = Number(x) || 0; y = Number(y) || 0
        try {
          const r1 = await cdpCall(target, 'Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
          await new Promise(r => setTimeout(r, 50))
          const r2 = await cdpCall(target, 'Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
          json(res, 200, { ok: true, x, y, r1: r1.result, r2: r2.result })
        } catch (e) { json(res, 500, { ok: false, error: String(e?.message || e).slice(0, 400) }) }
      }
    }))

    // POST /cloak/input  { text, targetId? }  inserts text
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/input',
      handler: async (req, res) => {
        if (req.method !== 'POST') { res.writeHead(405); res.end(); return }
        const body = await readJson(req)
        const text = String(body.text || '')
        if (!text) { json(res, 400, { ok: false, error: 'text required' }); return }
        const port = await findCdpPort()
        if (port === null) { json(res, 503, { ok: false, error: 'CDP offline' }); return }
        const targets = await getTargets(port)
        let target = body.targetId ? targets.find(t => t.id === body.targetId) : pickTarget(targets)
        if (!target?.webSocketDebuggerUrl) { json(res, 404, { ok: false, error: 'no target' }); return }
        try {
          const r = await cdpCall(target, 'Input.insertText', { text })
          json(res, 200, { ok: true, result: r.result })
        } catch (e) { json(res, 500, { ok: false, error: String(e?.message || e).slice(0, 400) }) }
      }
    }))

    // POST /cloak/eval  { expression, awaitPromise?, targetId? }
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/eval',
      handler: async (req, res) => {
        if (req.method !== 'POST') { res.writeHead(405); res.end(); return }
        const body = await readJson(req)
        const expr = String(body.expression || '')
        if (!expr) { json(res, 400, { ok: false, error: 'expression required' }); return }
        const port = await findCdpPort()
        if (port === null) { json(res, 503, { ok: false, error: 'CDP offline' }); return }
        const targets = await getTargets(port)
        let target = body.targetId ? targets.find(t => t.id === body.targetId) : pickTarget(targets)
        if (!target?.webSocketDebuggerUrl) { json(res, 404, { ok: false, error: 'no target' }); return }
        try {
          const r = await cdpCall(target, 'Runtime.evaluate', { expression: expr, awaitPromise: !!body.awaitPromise, returnByValue: true })
          if (r.error) { json(res, 500, { ok: false, error: r.error.message }); return }
          json(res, 200, { ok: true, result: r.result?.result?.value ?? r.result })
        } catch (e) { json(res, 500, { ok: false, error: String(e?.message || e).slice(0, 500) }) }
      }
    }))

    // GET /cloak/targets
    routes.push(server.register({
      kind: 'exact',
      path: '/cloak/targets',
      handler: async (req, res) => {
        if (req.method !== 'GET') { res.writeHead(405); res.end(); return }
        const port = await findCdpPort()
        if (port === null) { json(res, 200, { alive: false, targets: [] }); return }
        const targets = await getTargets(port)
        json(res, 200, { alive: true, port, targets })
      }
    }))

    ctx.effect(() => () => { for (const d of routes) try { d() } catch {} }, 'cloak-panel routes')
    log.info('cloak-panel: mounted /cloak/* routes')
  })
}
