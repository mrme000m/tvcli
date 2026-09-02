# Right rail widgets — TradingView chart UI

The TradingView chart's right-side panels are toggled by DOM buttons whose
on/off state lives in `aria-pressed`. Discovered via local bdg against the
authenticated headful CloakBrowser (`node bdg/dist/index.js cdp Runtime.evaluate …`).

## Mechanism

- The 4 panel buttons form a **radio group** (mutually exclusive): clicking one
  opens its panel and closes the others; clicking the already-active one
  collapses the whole rail to the thin icon strip.
- State: `aria-pressed` — `"true"` = that panel is open, `"false"` = closed.
- Visual proof: the rail width (`[data-name="widgetbar-wrap"]` bounding rect)
  is **~346px when a panel is open** and **~45px when collapsed**.
- **A synthetic `.click()` only flips `aria-pressed`**; it does not reliably move
  the panel. Toggle with a real input event via CDP `Input.dispatchMouseEvent`
  at the button center — this is what `toggle-widgets.mjs` does.

## Rail panels (mutually exclusive)

| Friendly name | `data-name` | aria-label |
|---|---|---|
| watchlist (`base`) | `base` | "Watchlist, details, and news" |
| alerts | `alerts` | "Alerts" |
| object tree / data window | `object_tree` | "Object tree and data window" |
| chats | `union_chats` | "Chats" |

## Dialog buttons (open floating dialogs, NOT rail panels)

`data-name`: `screener-dialog-button` (Screeners), `pine-dialog-button` (Pine),
`calendar-dialog-button` (Calendars), `community-hub-button` (Community),
`notifications-button` (Notifications). These open dialogs — not covered by
`toggle-widgets.mjs`.

## Watchlist specifics

- The watchlist lives inside the `base` bar ("Watchlist, details, and news").
- Toggle the whole bar: `[data-name="base"]`.
  - Verified: real click flipped `aria-pressed` `true→false` and collapsed the
    bar 346px → 45px; a second click restored it.
- Watchlist tab inside the bar: `[data-name="watchlists-button"]`
  (label = active watchlist name, e.g. `KucoinIchiV1`).
  Sibling tabs: `add-symbol-button`, `advanced-view-button`, `settings-button`.

## Toggle via bdg (real input)

```sh
node toggle-widgets.mjs watchlist   # toggle the watchlist bar (real input click)
node toggle-widgets.mjs alerts      # toggle Alerts
node toggle-widgets.mjs object_tree --off
node toggle-widgets.mjs chats --on
node toggle-widgets.mjs --list
```

Raw CDP equivalent (what the script does):
```sh
# read state + rail width
node bdg/dist/index.js cdp Runtime.evaluate --params \
  '{"expression":"JSON.stringify({pressed:document.querySelector(\"[data-name=\\\"base\\\"]\")?.getAttribute(\"aria-pressed\"),rail:Math.round(document.querySelector(\"[data-name=\\\"widgetbar-wrap\\\"]\")?.getBoundingClientRect().width||0)})","returnByValue":true}'
```
`pressed` = `"true"` open / `"false"` closed; `rail` = 346 (open) vs 45 (collapsed).

## Keyboard shortcuts — findings (verified via hotkey registry + dispatch)

The chart's hotkey registry (`chartWidget._hotkeys._manager._groups[*]._actions`,
keyed by `hashFromEvent` values) has **no shortcuts that toggle the rail panels
open/closed** — those are button-only (use `toggle-widgets.mjs`). The Alt
shortcuts that look related actually do different things:

| Combo | Registered action | Effect |
|---|---|---|
| `Alt+W` | Add symbol to watchlist | opens add-symbol dialog |
| `Alt+A` | Create (Alerts group) | opens create-alert dialog |
| `Alt+D` | Show a data window widget | **opens** `object_tree` panel (no close) |
| `Alt+N` | Add text note | adds a note on the chart |
| `Alt+S` | Screenshot server | server-side snapshot, no visible UI |
| `Alt+Enter` | Maximize | chart-widget fullscreen (`toggleFullscreen`) |
| `Alt+G` / `Alt+R` | Go to date / Reset chart view | |
| `Alt+I/L/P` | Invert / Logarithmic / Percent scale | |
| `Alt+C/F/H/J/T/V` | draw crossline/fib/h-line/h-ray/trendline/v-line | |

Notes:
- Hotkeys only fire when focus is **not** on an input (`isNativeUIInteraction`
  guard). `document.activeElement` must be blurred first.
- Screenshot of the chart: use CDP `Page.captureScreenshot` (what
  `toggle-widgets.mjs` / `max-chart.mjs` do) — reliable local PNG. `Alt+S` is
  registered but produces no local file.

## Max chart area

`node max-chart.mjs` collapses the right rail (clicks the active panel button,
346px → 45px) and reports the chart-area gain
(`[class*="layout__area--center"]` rect, e.g. 1194×795 → 1495×795). The left
drawing toolbar (52px) and bottom OHLC status bar (38px) are permanent chrome
in this layout — no collapse controls exist. `--fullscreen` additionally presses
`Alt+Enter` (registered Maximize hotkey).