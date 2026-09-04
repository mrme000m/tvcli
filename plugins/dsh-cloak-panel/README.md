# dsh-cloak-panel

Installable DSH Web plugin — collapsible, resizable **right-side panel** that streams the headful **CloakBrowser** (stealth Chromium) agents use while discovering / reverse-engineering backend APIs of external systems (TradingView, WunderTrading, etc.).

- **Right dock:** fixed `shell.overlay` panel, 320–860 px drag-resizable, persisted in `localStorage`, collapsible to a 36 px rail.
- **Live view:** polls `GET /cloak/screenshot` (CDP `Page.captureScreenshot` → PNG, ~1.6 s interval, ~100 kB/frame) and `GET /cloak/status`. Click-to-interact via `POST /cloak/click` (normalized `xRatio/yRatio` → `Input.dispatchMouseEvent`), URL bar → `POST /cloak/navigate`.
- **Headful source:** the same `browser-debug/launch.mjs` CloakBrowser (`--remote-debugging-port=9222`, profile `browser-debug/profile`) that `tv.mjs`/`wt.mjs` and the `bdg` CLI attach to. No extra browser — the panel is a **read-only viewport** into the agent's real session.

## Analysis

**`browser-debug/`** (`launch.mjs`, `tv.mjs`, `wt.mjs`, `bdg/`, `AGENTS.md`):
- `launch.mjs` downloads stealth-patched Chromium to `cloakbrowser/chromium-*`, launches headful (never `--headless`, macOS LaunchAgent path for Aqua activation), CDP 9222..9321, `CB_PROFILE` support, SOCKS5 relay.
- `tv.mjs` / `wt.mjs` restore TradingView / WunderTrading cookies via `Network.setCookie` over CDP WS and probe auth (`[aria-label^="Logged in as"]`, WunderTrading login CTA). WunderTrading fetch-inside-page pattern (`wt.mjs api …`) is the model for authenticated API discovery — Cloudflare fingerprint means raw HTTP 403, real-browser `fetch` is required.
- `bdg` is a daemonized CDP CLI (644 methods, `tv` subcommands for chart/studies, network/console capture). Pattern: pick `webSocketDebuggerUrl` from `http://127.0.0.1:9222/json`, drive via WS.

**DeepSeek Harness (`deepseek-ai/deepseek-harness` v0.1.1-rc.2):**
- Everything-is-a-plugin on Cordis (`cordis.patch.yml` bundle layers, `dsh.plugin add link:` via pnpm). Profile `web` = `dsh-base` + `dsh-web-app` + overlays.
- `webServer` service (`register`/`tapIndex`/`registerFallback`, exact/prefix routing, `127.0.0.1:3080`). `prime-orchestrator` demonstrates adding a fourth column via a patched `lib/layout-override.js` served at `/prime/layout-override.js` + boot-manifest rewrite (`window.__DSH_BOOT__`). Additive surfaces belong in `shell.overlay` (list slot, click-through) — no layout patch needed for a floating panel.

## Install

```sh
# from this repo
dsh plugin --profile web add "link:/workspaces/tvcli/plugins/dsh-cloak-panel"
# restart so the new bundle mounts (plugins mount at boot)
# if dsh-restart is installed:
curl -X POST http://127.0.0.1:3081/api/commands/execute -d '{"command":"restart"}'
# or manually:
pkill -f "dsh web" && nohup dsh web --port 3081 --host 127.0.0.1 --no-open > /tmp/dsh-web.log 2>&1 &
```

Hard refresh the browser (Ctrl+Shift+R) — the panel appears docked on the right.

## Use

1. Launch a headful session (once):
   ```sh
   node browser-debug/launch.mjs
   node browser-debug/tv.mjs        # TradingView, .env SESSION
   node browser-debug/wt.mjs        # WunderTrading, secrets/runtime/wt-session.env
   ```
2. Open `http://127.0.0.1:3081` — **Cloak** panel shows live screenshot, URL, `● live` / `:9222 • N targets`.
3. Navigate via the panel's address bar or **TradingView / WunderTrading / XAUUSD** quick buttons; click the screenshot to send a click into the real page (agents' discovery clicks appear in real time).
4. Collapse with `→` (rail shows `◀ CLOAK • live`); drag the left edge to resize (320–860 px).

## Endpoints (same-origin, trusted local)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/cloak/status` | `{alive, port, targets, current}` probe |
| GET | `/cloak/screenshot?targetId=` | `image/png` via `Page.captureScreenshot` (throttled 700 ms) |
| POST | `/cloak/navigate` `{url}` | `Page.navigate` |
| POST | `/cloak/click` `{x,y} or {xRatio,yRatio}` | `Input.dispatchMouseEvent` |
| POST | `/cloak/input` `{text}` | `Input.insertText` |
| POST | `/cloak/eval` `{expression}` | `Runtime.evaluate` |
| GET | `/cloak/targets` | raw `http://127.0.0.1:9222/json` |

## Notes

- No layout override — the panel is a `shell.overlay` overlay (z-index 30), so the prime-orchestrator 4-column patch coexists.
- Screenshot polling is passive; heavy work (study extraction, network capture) stays in the agent's CDP session (`bdg tv …`, `wt.mjs api …`).
- For Kanban-style fleet work, keep one `launch.mjs` per profile (`CB_PROFILE`); the panel always shows the preferred page (`wundertrading.com` > `tradingview.com` > first page).
