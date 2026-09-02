# AGENTS.md — minimal-mjs (portable CloakBrowser launcher)

`launch.mjs` downloads + launches the stealth-patched CloakBrowser Chromium,
headful, with CDP on port 9222 (or next free). Usage: `node launch.mjs [--force]`,
profile via `CB_PROFILE=<dir>`.

## Critical gotcha: never pass `--headless`

The stealth-patched Chromium treats the **presence** of `--headless` in argv as
headless-ish — even `--headless=false` — and sets its macOS activation policy to
background-only. Symptom: CDP responds (`curl :9222/json` works) but **no window
appears**.

- ❌ `--headless=false` → `background only: true`, no window
- ✅ omit the flag entirely → `background only: false`, window shows

Headful = just **don't** pass a `--headless` flag. This is exactly what Playwright
and the official `cloakbrowser` npm/pip packages do (`launch({headless:false})`
adds nothing to argv for headless-off).

## Verification

```sh
osascript -e 'tell application "System Events" to get background only of process "Chromium"'
# false = headful window OK; true = headless-flag bug
```

Both launch paths in `launch.mjs` (raw `--direct` spawn and the macOS
LaunchAgent `open -n`) work once the flag is omitted.

## Authenticated headful launch — `tv.mjs`

`node tv.mjs` launches the headful CloakBrowser (reusing `launch.mjs`) and loads
the authenticated TradingView account from `.env`:

| env var     | cookie            |
|-------------|-------------------|
| `SESSION`   | `sessionid`       |
| `SIGNATURE` | `sessionid_sign`  |
| `DEVICE_T`  | `device_t`        |

Flow: spawns `launch.mjs` (detaches the browser), finds the live CDP port
(9222..9321), connects to the page WebSocket (Node >= 22 global `WebSocket`),
`Network.setCookie` for the 3 cookies on `.tradingview.com`, navigates to the
chart, polls `[aria-label^="Logged in as"]`. Prints `AUTH OK — logged in as: <user>`.

`.env` is generated from the tvcli root `.env` (free account). Never commit it, never print
cookie values.

## Investigating TradingView over CDP — local `bdg`

Local browser-debugger-cli lives in `bdg/` (build once: `npm install && npm run build`,
entry `bdg/dist/index.js`). Attach it to the running headful browser, then run CDP:

```sh
# 1. page WebSocket url
curl -s http://127.0.0.1:9222/json          # take webSocketDebuggerUrl of a page

# 2. attach (backgrounds a telemetry daemon; the browser keeps running)
node bdg/dist/index.js --chrome-ws-url "ws://127.0.0.1:9222/devtools/page/<id>" \
  --no-headless "https://www.tradingview.com/chart/"

# 3. inspect
node bdg/dist/index.js status
node bdg/dist/index.js cdp --search cookie   # discover methods
node bdg/dist/index.js cdp Network.getCookies --params '{"urls":["https://www.tradingview.com/"]}'
node bdg/dist/index.js cdp Runtime.evaluate --params '{"expression":"document.title","returnByValue":true}'
node bdg/dist/index.js network list          # captured requests (TV API calls)
node bdg/dist/index.js console               # console messages
node bdg/dist/index.js dom query "selector"  # DOM helpers
node bdg/dist/index.js stop                  # detach daemon
```

Account probe (same selector `tv.mjs` uses):
```sh
node bdg/dist/index.js cdp Runtime.evaluate --params \
  '{"expression":"(function(){var b=document.querySelector(\"[aria-label^=\\\"Logged in as\\\"]\");return b?b.getAttribute(\"aria-label\"):null})()","returnByValue":true}'
```

## TradingView specialization (`bdg tv` command group + docs)

The local bdg build carries a `tv` command group for TradingView network-API
+ frontend investigation (docs: `bdg/docs/tv/`):

```sh
node bdg/dist/index.js tv ws -s 8              # capture WS protocol (auth, session, studies, du)
node bdg/dist/index.js tv chart                # chart id, main series, dataSources
node bdg/dist/index.js tv studies              # studies (pine/event/other + pineIds)
node bdg/dist/index.js tv study add "RSI" --inputs '{"length": 21}'   # add ANY script w/ custom inputs
node bdg/dist/index.js tv study values         # read computed values
node bdg/dist/index.js tv study remove <id>    # remove study
node bdg/dist/index.js tv drawings             # drawings + layer capabilities
node bdg/dist/index.js network websockets      # WS connections via network collector
```

The `tv-network` skill in `.agents/skills/tv-network/` is the agent-facing
manual (operational commands, verified live facts, extension patterns). The
DSH user preset `tv-investigator` (`~/.dsh/.agent-presets/tv-investigator/`)
wires this skill + the go repo's `tvcli`/`tv-scout` skills into an agent
focused on network-API + chart-frontend investigation. Free tier: ≤2 user
studies per chart; event overlays (ESD$*) don't count and are not deduped.

## UI automation scripts (right rail, max chart area)

- `toggle-widgets.mjs` — toggle the 4 right-rail panels (radio group) with REAL
  CDP input events (synthetic `.click()` flips `aria-pressed` but doesn't move
  the panel). Takes before/after screenshots into `shots/` with a 5s settle wait.
- `max-chart.mjs` — collapse all panels so the chart fills the window: clicks
  the active rail panel button (346px → 45px), reports the chart-area gain.
  `--fullscreen` also presses Alt+Enter (registered "Maximize" hotkey).
  Left drawing toolbar (52px) + bottom OHLC bar (38px) have no collapse controls.
- Full findings: `docs/ui/right-rail.md`.

Keyboard shortcuts (verified via `_hotkeys._manager._groups[*]._actions`, keyed
by `hashFromEvent`): **no hotkeys toggle the rail panels** — button-only.
Alt+W=add symbol, Alt+A=create alert, Alt+D=opens data window (no close),
Alt+N=add text note, Alt+S=server screenshot (no local file), Alt+Enter=maximize,
Alt+G/R=go-to-date/reset-view, Alt+I/L/P=scale, Alt+C/F/H/J/T/V=drawing tools.
Screenshot the chart reliably with CDP `Page.captureScreenshot`.
Hotkeys only fire when focus is NOT on an input (blur `document.activeElement`
first — a stray input silently eats hotkeys).

## Vision tool (Mistral) — understand UI changes from screenshots

`vision.mjs` sends local screenshots to a Mistral vision model
(`mistral-small-2506` default; vision-capable: mistral-large-2512,
mistral-medium-2508, mistral-small-2506, ministral-*-2512) and returns a
description of the web interface state / what changed.

```sh
node vision.mjs shots/watchlist_after.png                 # describe one screenshot
node vision.mjs shots/before.png shots/after.png          # what changed between two
node vision.mjs --live "describe the chart"               # fresh CDP screenshot first
node vision.mjs shots/x.png --model mistral-large-2512    # override model
```

API key: `MISTRAL_API_KEY` — env var first, else falls back to
`./.env` (canonical home; TV session keys live in both .env files).
Never print/commit the key value.
