# tv-visual — standalone headful TradingView channel (CloakBrowser + CDP)

A self-contained **visual channel** to TradingView. Unlike `tvcli` (headless,
server-side WebSocket/Pine-Facade) this channel drives the **real
tradingview.com chart in a headful stealth-Chromium browser** via Chrome
DevTools Protocol, injecting your session cookies, so the agent gets **eyes**
(screenshots, rendered indicator values, Pine drawings) and **hands**
(switching symbols/timeframes, adding/removing indicators) on the actual UI —
for a human to *see* the constructed chart.

## Design

- **Standalone** — the only browser dependency is [`cloakbrowser`](https://github.com/CloakHQ/CloakBrowser)
  (a drop-in Playwright replacement). No `bdg` / `browser-debugger-cli` import.
- **Headful** — `launch_persistent_context(user_data_dir, headless=False)`,
  session cookies persist across runs.
- **Self-installing** — `tvvisual install` (or first launch) downloads the
  stealth Chromium binary via `ensure_binary()`.
- **CDP** — `context.new_cdp_session(page)` → `Runtime.evaluate` / `Page.captureScreenshot`.

## Install

```bash
pip install -e .        # pulls cloakbrowser
tvvisual install        # downloads the stealth Chromium (~once)
```

`cloakbrowser.launch(headless=False)` already enables `--ignore-gpu-blocklist`
and the other Playwright/headful defaults, so the chart should hydrate inside
Xvfb. If you instead drive the shared `browser-debug/launch.mjs` CloakBrowser
for tvvisual work, use that launcher — it now sets the same robust flags for
the repo's CDP/x11vnc stack.

## Usage

Session cookies (`SESSION`, `SIGNATURE`, `DEVICE_T`) are read from the
environment or a `.env` file via `--env`.

```bash
# Open the chart, add indicators, dump state, screenshot, keep visible 30s
tvvisual open OANDA:XAUUSD --tf 15 --ind "Relative Strength Index" --state --shot chart.png --hold 30 --env ../.env

# Read chart state / bars / indicator values / Pine drawings
tvvisual state   --env ../.env
tvvisual ohlcv --summary --env ../.env
tvvisual studies --env ../.env
tvvisual lines   --filter "NY Levels" --env ../.env
tvvisual labels  --env ../.env
tvvisual boxes   --env ../.env
tvvisual tables  --env ../.env

# Screenshot
tvvisual shot --region chart --out chart.png --env ../.env
```

## Scout + recipes (progressive self-instrumentation)

`tvvisual scout` is how an agent *learns* the live UI instead of guessing
selectors; `tvvisual recipe` turns verified step-lists into repeatable,
configurable artifacts.

```bash
# Dump the live window.TradingViewApi surface / interactive DOM map into the KB
tvvisual scout surface --env ../.env
tvvisual scout dom     --env ../.env

# Record a one-off JS probe (dated, appended to probes.jsonl)
tvvisual scout probe monaco-shape --js 'document.querySelectorAll(".monaco-editor").length'

# List / run / verify / promote recipes
tvvisual recipe list
tvvisual recipe run xau-scalp --env ../.env --param '{"rsi": 50.0, "composite": 33.0}'
tvvisual scout verify [name]          # re-run KB recipes, refresh health
tvvisual scout save ./my-recipe.json  # promote into the KB store

# Generate a standalone Python script from a verified recipe
tvvisual recipe codegen xau-scalp
```

Knowledge base: `~/.tvvisual/scout/` (`surface.json`, `dom.json`,
`recipes.json`, `probes.jsonl`, `generated/`, `layouts/`). Packaged,
version-controlled recipes: `visual/recipes/*.json` (`xau-scalp`,
`chart-snapshot`). Recipe schema: `params` + `steps[]` of
`{action, args, required}`; args support `$params.key` references; `file` /
`path` args resolve relative to the recipe file. Available actions: open,
timeframe, symbol, hide_chrome, show_chrome, zoom_bars, add_indicator,
add_indicator_search, search_indicators, remove_indicator, draw,
clear_drawings, pine_new, pine_set, pine_compile, pine_smart_compile,
pine_errors, pine_save, screenshot, validate, state, studies, study_values,
tables, lines, eval, sleep, account, relogin, layout_export, layout_list,
layout_switch.

`layout export` (`Chart.layout_export`, recipe action `layout_export`)
captures the COMPLETE chart configuration — resolution, symbol, and
`content.charts[].panes[].sources[]` with every study's exact
inputs/overrides/styles plus all drawings — to
`~/.tvvisual/scout/layouts/`. Note: `relogin` reloads the chart and restores
the account's saved layout, so recipes must relogin BEFORE `open(symbol, tf)`.

The `xau-scalp` recipe opens the chart, forces the profile onto the `.env`
account (`relogin`), adds the owned "XAU Scalp Engine v2" user script from the
indicators dialog's My scripts section (Pine upload is tvcli's job:
`tvcli create`/`push`), screenshots the chart (`visual/xau-scalp-latest.png`),
and optionally validates on-chart study values against tvcli-computed numbers
(vision-model confluence).

## Notes

- Requires a valid TradingView session (cookies in `.env`).
- `window.TradingViewApi` is TradingView's internal, undocumented web-app API —
  it can change without notice.
- The `--ind` indicator names must match TradingView's dialog titles (full
  names, e.g. "Relative Strength Index").
- **Blank chart / hydration failure:** in the container, the most common cause
  is Chromium's GPU blocklist disabling WebGL inside Xvfb. The cloakbrowser
  wrapper and `browser-debug/launch.mjs` both pass `--ignore-gpu-blocklist` by
  default. If a stale profile wedges, start fresh with
  `CB_FRESH_PROFILE=1 node browser-debug/launch.mjs`, or diagnose with
  `node browser-debug/hydration-check.mjs '<url>' 30000`.