# Console UI audit — 2026-09-08

Scope: read-only audit of the grid-autonomy mission console
(`agents/grid-autonomy/console/`), all 7 views + global chrome, against
the live deployment at `http://grid-autonomy:8798/`.

Method: full read of `static/app.js` (3285 L), `static/index.html` (314 L),
`static/mobile.js` (279 L), `static/styles.css` (1724 L),
`static/responsive.css` (280 L), the render-relevant parts of `server.py`
(routes, `llm_health`, payload builders), plus GET-only curls of all 20
documented API endpoints (payload shapes diffed against every `bot.X` /
payload reference in app.js). One live cold-cache measurement of
`/api/llm/health` was taken (63.7 s; nvidia 34.1 s, openrouter 28.0 s,
cf 0.9 s, mistral 0.6 s). No POST endpoints were called; no files were
modified except this report.

## Summary (counts per severity)

| Severity | Count | Theme |
|---|---|---|
| P0 | 1 | JS bug that wipes real data on resize/orientation change |
| P1 | 8 | Mobile degradation, 70s data blackout, polling destroys UI state, a11y failures |
| P2 | 19 | Dead code paths, unsurfaced data, polish, consistency |

All five known findings handed in were verified and are detailed below
(P1-1 … P1-4 carry them forward; finding 5 is folded into P1-1).

---

## P0 — broken on mobile / data never renders / JS errors

### P0-1. [fleet] The PnL timeline chart is wiped to a false "no history" state on every resize / phone rotation
- **What breaks:** rotate a phone (or resize a desktop window) while the
  Fleet view is open → the canvas immediately repaints the empty-state
  message `no pnl-snapshot history (daemon pre-restart?)` — even though 104
  snapshots exist — and stays that way for up to 30 s until the next
  `loadPnlTimeline` poll (`tick % 6`, app.js:3263) repaints it.
- **Root cause:** `mobile.js:236` calls
  `window.drawPnlChart(window.lastPnlPoints || [])`. But app.js:211 declares
  `let lastPnlPoints = null;` — a top-level `let` creates a global *lexical*
  binding, **not** a `window` property. `window.lastPnlPoints` is therefore
  always `undefined`, so the redraw is always `drawPnlChart([])`.
  (`window.drawPnlChart` itself works — function declarations *do* attach to
  `window` — which is why the bug hides: the call succeeds, it just draws
  the wrong thing.)
- **Fix:** in `mobile.js` (one-liner, no app.js change needed — classic
  scripts share the global lexical environment):
  ```js
  // mobile.js:235-237
  if (typeof window.drawPnlChart === "function") {
    window.drawPnlChart(typeof lastPnlPoints === "undefined" ? [] : lastPnlPoints);
  }
  ```
  Belt-and-braces alternative [needs-app.js-hook]: assign
  `window.lastPnlPoints = lastPnlPoints` in `loadPnlTimeline`
  (app.js:1572-1575). If a new component is preferred, move the resize
  redraw into `console/static/components/pnl-chart-resize.js` and add one
  `<script>` tag to index.html — but the mobile.js one-liner is the
  smallest, safest change.
- **Blast radius:** tiny — one expression in mobile.js; no render code
  touched. Verification: `drawPnlChart` itself is correct (DPR-aware,
  container-measured width).

---

## P1 — degraded UX on mobile / misleading or stale data / a11y failures

### P1-1. [fleet] "LLM brains" card: 60–70 s placeholder after boot, no fetch timeout, request pile-up, stale-overwrite race *(known finding 1 + 5, verified)*
- **What breaks:** on a cold cache the card shows the static placeholder
  (index.html:105) for the full provider-ping round-trip — **measured live:
  63.7 s** (nvidia 34.1 s + openrouter 28.0 s dominate; server
  `llm_health()` server.py:1148-1242, subprocess `timeout=180`).
  During that window:
  - `renderLlmBrains()` is fired by **every** 5 s poll tick
    (app.js:244 → 1172-1215) with **no in-flight dedupe** — ~13 parallel
    `/api/llm/health` requests pile up, and because the server only caches
    *after* the first subprocess completes, each request spawns its own
    `provider.py --ping` subprocess (server-side amplification; the separate
    worker is fixing the server, this documents the frontend contract).
  - The `fetch` in `api()` (app.js:111-120) has **no AbortSignal / timeout**
    — if the subprocess hangs, the UI hangs on stale content for up to 180 s
    with no loading indicator. `#llm-brains-at` stays "—" and there is no
    "pinging…" state on refreshes.
  - Late responses overwrite earlier ones by arrival order (each caller does
    `box.innerHTML = …` after its own `await`), so an older cold response
    can clobber a newer warm one.
- **Fix (frontend only):** new file
  `console/static/components/llm-health.js` owning a singleton ping
  lifecycle: module-level `inflight` promise (dedupe, like `chartInflight`
  in app.js:663-676), `AbortController` with a 15 s client timeout, a
  "pinging…" state while in flight, and a monotonic guard (ignore a
  response whose `at` is older than the last rendered one). Load with one
  `<script>` tag in index.html.
  [needs-app.js-hook] app.js must call the component instead of the inline
  `renderLlmBrains()` body — smallest hook: keep `renderLlmBrains()` as a
  1-line shim at app.js:1172 that delegates to `window.LlmHealth.render()`.
- **Blast radius:** isolated card; no other view depends on
  `/api/llm/health`.

### P1-2. [fleet] Market modal chart: width baked at open, never repaints on resize/orientation change *(known finding 2, verified)*
- **What breaks:** `openMarketModal` (app.js:736) computes
  `W = Math.max(320, Math.min(window.innerWidth * 0.9, 900))` once at line
  742; all geometry — `RX = 62` gutter (line 743), `X(i)` (line 768),
  channel-label text positions `x = W - RX + 6` (line 786) — is baked into
  the `viewBox`. The SVG has CSS `width:100%` (styles.css:1651-1661), so it
  scales, but it **scales as a picture of a chart sized for the old
  viewport**: open on a 360 px phone then rotate to landscape → the whole
  chart balloons ~2.2× (labels ~20 px, gutter grows from 19% to 19% of a
  now-oversized viewBox, chart becomes disproportionately tall);
  open on desktop then shrink the window → labels shrink toward
  illegibility. No `resize`/`orientationchange` listener exists.
- **Fix:** new file `console/static/components/market-chart.js` that owns
  the modal chart: render into a container with `width: 100%`, measure
  `container.clientWidth` at paint time (same pattern as `drawPnlChart`
  app.js:1509-1526), and re-paint on a debounced
  `resize` + `orientationchange` while the modal is open (copy the
  Escape/backdrop cleanup pattern at app.js:836-845 for listener teardown).
  [needs-app.js-hook] `openMarketModal` must delegate its `paint()` to the
  component (or be moved wholesale — it is self-contained apart from
  `chartCache`/`fetchChart`, which can stay in app.js and be reached via
  `window.fetchChart`… note `fetchChart` is a top-level `function`, so it
  already attaches to `window` and is reachable without edits).
- **Blast radius:** only the market modal; slot-card sparklines are
  unaffected (verified fine, see verification notes).

### P1-3. [global] Two different mobile breakpoints coexist: 720 px (styles.css) vs 760 px (responsive.css + mobile.js) *(known finding 3, verified)*
- **What breaks:** between **721–760 px** the two layers disagree:
  responsive.css's ≤760 tier applies (bnav, card mode, chips wrap, 1-col
  fleet grid, 16 px inputs), but styles.css's ≤720 tier
  (styles.css:1376-1384, 977) does **not** — so `.slot-metrics` stays
  4-column (styles.css:445-450) instead of the intended 2-column, `.field`
  stays the 3-column `minmax(150px,1fr) 150px 1fr` grid
  (styles.css:805-810) instead of collapsing, and `.llm-prov-fields` stays
  2-column. It renders acceptably at ~740 px (cards are full-width there),
  but it is a mixed-tier no-man's-land that behaves like neither target,
  and it is a maintenance trap: `MQ_MOBILE` in mobile.js:23, the responsive
  760/430 tiers, and the styles.css 720/1180/1120/980/900 tiers are five
  hand-synced numbers with nothing enforcing agreement.
- **Fix:** consolidate in **responsive.css** (the declared owner of the
  mobile layer): move the four rules from the styles.css ≤720 block
  (`.topbar`, `.slot-metrics`, `.field`, `.f-range` hide) and the
  `.llm-prov-fields` rule (styles.css:977) into the ≤760 block of
  responsive.css (or re-emit them at ≤760), then delete the duplicated
  ≤720 blocks from styles.css. Keep 430/720 content but a single boundary
  at 760. No app.js change.
- **Blast radius:** CSS-only, 721–760 px band only; desktop untouched.

### P1-4. [global] Bottom-nav labels ellipsize on ≤430 px phones *(known finding 4, verified)*
- **What breaks:** 7 bnav buttons on a 360 px viewport get
  `(360 − 8 padding − 12 gaps) / 7 ≈ 48.5 px` each, minus `6px 2px` button
  padding → ~44 px of label space. At 9 px IBM Plex Mono with 0.05 em
  letter-spacing (responsive.css:246-268) uppercase labels need
  ~5.9 px/char: "RELIABILITY" (11 ch) ≈ 64 px, "DECISIONS" ≈ 53 px,
  "OPTIMIZER" ≈ 53 px — all clip to "RELIABILIT…" via the
  `text-overflow: ellipsis` on `.bnav-label` (responsive.css:266-270).
  Icons disambiguate, but three of seven tabs lose their text.
- **Fix:** in mobile.js add a short-label map next to `BNAV_ICONS`
  (mobile.js:25-33): `{ "tab-fleet":"FLEET", "tab-decisions":"DECS",
  "tab-reports":"CARDS", "tab-optimizer":"OPT", "tab-reliability":"REL",
  "tab-config":"CFG", "tab-logs":"LOGS" }` (fall back to the tab text),
  and/or in responsive.css allow the label to wrap to two lines
  (`white-space: normal; line-height: 1.15; font-size: 8px`) inside the
  48 px-tall button. Either file is fair game (not app.js).
- **Blast radius:** bottom nav only.

### P1-5. [decisions/optimizer] 20 s polls silently destroy expanded rows
- **What breaks:** while the Decisions view is visible, `loadDecisions()`
  runs every 4th tick (app.js:3266) and `renderDecisions()` (app.js:1618)
  rewrites `#dec-body.innerHTML` — an operator reading an expanded
  evidence row (`tr.dec-detail`, app.js:1790-1812) has it **collapse every
  20 s**. Same for the Optimizer view: `loadOptimizer` every 4th tick
  (app.js:3265) re-renders `#opt-pending-body` (app.js:2161-2191), closing
  any expanded grouped-rec history row. The filter input and sort survive
  (globals + static input), but expansion state does not, and mid-read
  reflows also reset in-table scroll for tall tables.
- **Fix:** new file `console/static/components/expand-state.js` that keeps a
  `Set` of open decision ids / rec-group gkeys in a module global, exposes
  `isOpen(id)` / `toggle(id)`, and re-applies open state after render.
  [needs-app.js-hook] minimal hooks: in `renderDecisions` after the
  `innerHTML` write, re-expand rows found in the set (3-4 lines); same in
  `renderOptimizer` for `tr.rec-group` (`det.hidden = false`). Alternative
  without hooks: render expansion via `hidden` + event delegation instead
  of node insertion so re-renders preserve DOM… still an app.js render
  change; the component + small hook is cleaner.
- **Blast radius:** decisions + optimizer render tails only.

### P1-6. [decisions] Decision rows are not keyboard accessible; `aria-expanded` missing on all expandables
- **What breaks:** `tr.dec-row` (app.js:1654) is click-only — no
  `role="button"`, no `tabindex`, no keydown handler (the `#dec-body`
  delegate at app.js:1814 handles clicks only). Contrast `tr.rel-row`
  (app.js:2547) which does all three correctly. Keyboard users cannot open
  decision evidence at all. Additionally the copy affordance
  `.dec-id` span (app.js:1665) carries `role="button" tabindex="0"` but
  has **no Enter/Space handler**, so focusing it and pressing Enter does
  nothing. No `aria-expanded` anywhere (dec rows, rel rows, rec groups,
  run-card kinds chips) — screen readers get no expansion state.
- **Fix:** [needs-app.js-hook] mirror the rel-row pattern onto dec-row
  emit (app.js:1654): `role="button" tabindex="0"` + keydown Enter/Space in
  the existing delegate; add `aria-expanded` toggling in the expand/collapse
  branches (app.js:1814-1860 for decisions, 2588-2660 for reliability, 2181-2191 for
  rec groups). Styling for `:focus-visible` already exists globally
  (styles.css:59-63) — no CSS needed.
- **Blast radius:** decisions render + one delegate; low risk.

### P1-7. [all views] No persistent stale/error state when the console backend or daemon is down
- **What breaks:** each view's fail path is a transient toast
  (`loadDecisions` app.js:1585, `loadReports` 1861, `loadReliability`
  2503, `loadConfig` 2662, `loadLlm` 2764) or a silent return
  (`loadOverview` → `renderStatusbar(null)` app.js:219 shows one chip;
  `loadLogs` app.js:2981 returns silently). After the toast fades (4.2 s),
  the view keeps showing **stale data with no marker**: no "last updated"
  overlay, no dimming, no per-view banner. An operator landing on the
  Decisions tab during an outage sees a plausible-looking ledger with no
  hint it froze. The Fleet view is the only one with daemon-down banners
  (app.js:984-1012), and those cover `daemon.running=false`, not
  "console backend unreachable".
- **Fix:** new file `console/static/components/stale-guard.js`: watches
  each view's load result (exposed via a tiny event or a
  `data-stale-since` attribute), and renders a fixed
  `banner--warn` strip ("view stale since HH:MM — backend unreachable")
  at the top of the active view. CSS for `.stale-banner` in responsive.css.
  [needs-app.js-hook] each `load*` catch must ping the guard
  (`window.StaleGuard?.fail(viewName)` / `.ok(viewName)`) — one line per
  catch, seven call sites.
- **Blast radius:** additive; no render logic changes.

### P1-8. [decisions/optimizer] Table sorting is unreachable on mobile
- **What breaks:** card mode (`table.ledger.ledger--cards`) hides `thead`
  (responsive.css:101). All sort affordances live in `th.dec-sort`
  (app.js:1632-1649) — so on ≤760 px there is **no way to sort the
  decision ledger at all**, and the header labels that give the card
  `data-label`s their meaning are generated fine, but the interactive
  header row is gone entirely.
- **Fix:** new file `console/static/components/card-sort.js`: when card mode
  is active, inject a small `<select>` above the table listing the sortable
  columns (labels borrowed from the hidden `thead`), driving the existing
  `decSort` binding (top-level `let` at app.js:1580 is writable from other
  classic scripts, and `renderDecisions` is `window`-reachable, so **no
  app.js hook needed** for the decisions table). Style the select in
  responsive.css. Re-run the injection from the same MutationObserver pass
  mobile.js already uses.
- **Blast radius:** additive UI above one table family.

---

## P2 — polish, consistency, minor overflow

1. **[fleet] `.slot-exits` has no CSS rule.** app.js:405 emits
   `class="slot-exits"` (`exitProfileHTML`, app.js:389-408) — grep of both
   stylesheets finds nothing. The inline `style` on the inner span carries
   the typography, but the container gets no card padding/border, so the
   TP/SL/trail strip sits flush against the card edge, unlike its styled
   siblings `.slot-tvcli` (styles.css:493), `.slot-po` (502), `.slot-opt`
   (511). Fix: add a `.slot-exits { padding: 6px 14px 0; }` rule — ideally
   in a new `console/static/components/slot-extras.css` loaded after
   styles.css rather than growing styles.css.
2. **[fleet] Dead badge path: `obs.exit_queued`.** app.js:484 gates the
   "exit queued" badge on `observed.exit_queued`; `grep exit_queued
   server.py` finds nothing and no live payload carries it — the badge can
   never render. Either remove the branch or wire the daemon field.
3. **[fleet] `estimateCloseBy` reads `bot.take_profit_pct`, which the
   server never sends.** app.js:99; the overview sends `take_profit_usd`
   (server.py:967). The fallback to `config_digest.take_profit_pct` works,
   but the per-bot override is dead and the direct `take_profit_usd`
   (an absolute target) is arguably the more accurate input.
4. **[optimizer] `free_slots` renders as a blank count.** The ctl payload
   sends `capital.free_slots` as a **list**; app.js:2228 renders
   `esc(cap.free_slots ?? "—")` → `String([]) === ""`, so the Capital row
   shows "·  free slot(s)" with a hole (same for the run-card alias at
   app.js:1985). Fix: `cap.free_slots.length` with an array guard.
5. **[reports] Markdown tables get none of the table machinery.**
   `renderMarkdown` (app.js:2007) emits a bare `<table>` (app.js:2020) — no `ledger` class, no `.table-wrap`. On mobile there is
   neither card mode nor a scroll container: wide run-card tables squeeze
   to unreadable column widths (they do wrap, so nothing is clipped).
   Fix: emit `class="ledger"` + wrap in `<div class="table-wrap">` in the
   one template line.
6. **[reports] Run-card detail state survives a tab round-trip.**
   `openRunCard` (app.js:1892) hides `#rc-list` and shows `#rc-detail`
   (1896-1898), but `loadReports` (app.js:1858) never resets them —
   navigate to another tab and back and you land on the stale detail, list
   still hidden. One 3-line reset in `loadReports` [needs-app.js-hook].
7. **[fleet] Full fleet re-render every 5 s tick.** `renderFleet`
   (app.js:926) rebuilds the whole slot board (`board.innerHTML = ""`,
   app.js:928) each poll: any button/link mid-tap is replaced under the
   finger, focus is dropped, and the `.ladder .cursor` 0.9 s transition
   (styles.css:384) never actually plays because nodes are recreated.
   Suggest change-detection (skip render when a cheap digest of
   `ov`+`st` is unchanged).
8. **[global] Modals have no focus trap and no background scroll lock.**
   `confirmDialog` (app.js:151-182) and `openMarketModal` (app.js:736) do
   focus the primary button and handle Escape — good — but Tab walks out
   into the page behind the backdrop, and the page behind scrolls.
9. **[global] Tabs lack arrow-key navigation** (no roving tabindex in the
   `tablist`, index.html:33-42), and at ≤760 px the top tab strip and the
   bottom nav are both visible (responsive.css keeps `.tabs` on screen) —
   duplicate navigation surfaces.
10. **[global] `esc()` paired with `textContent` double-escapes.**
    app.js:302-303 (`#wt-account`) and app.js:3277-3281 (`#footnote`) run
    `esc()` on strings assigned via `textContent` — a mode or account
    label containing `&` would display literally as `&amp;`. Latent only
    (current values are dash-separated words).
11. **[config] `.kv-row .k { min-width: 220px }` (styles.css:860) is
    cramped on ≤430 px** — the value column gets ~80 px after the fixed
    key width. Suggest `min-width: min(220px, 55%)` in the ≤430 tier.
12. **[global] Critical info lives only in `title` tooltips**, which
    don't exist on touch: the veto-strip chip explanations (app.js:1447-1484),
    the exit-profile fields (app.js:389-408), the metric-label caveats
    ("proj /24h", "~ret/yr", app.js:536-549), "worst avg". Consider
    long-press or tap-to-toggle detail rows on mobile.
13. **[fleet/optimizer] Nine live journal kinds have no `k--*` color
    rule:** `pnl-snapshot`, `heartbeat`, `market-brief`, `recenter`,
    `optimizer-idle`, `screen`, `re-analysis`, `position-optimizer-sweep`,
    `demo-cap-veto` (vs the defined set at styles.css:650-655) — they
    render in default ink, so error-adjacent entries (e.g.
    `position-optimizer-sweep` with failures) don't stand out.
14. **[decisions] Deep-link expansion skips the cohort fetch.**
    `openDecisionFromSlot` (app.js:593-611) expands evidence directly
    from the in-memory row, bypassing the `/api/decisions/<id>` cohort
    lookup that the click path performs (app.js:1821-1846) — the
    "Cohort (same …)" block is silently absent when arriving via the slot
    card link.
15. **[logs] Invalid grep regex fails silently.** `loadLogs`'s only error
    path is `catch (e) { return; }` (app.js:2981) — a malformed regex
    looks like "nothing matched" with no feedback.
16. **[global] Google Fonts CDN dependency** (index.html:12-14). The
    console is otherwise fully offline-capable (inline canvas chart, no
    other external assets); an air-gapped deployment silently falls back
    to system fonts. Consider self-hosting the two families.
17. **[data-flow] Available-but-unsurfaced data** (all verified present
    in live payloads, none rendered anywhere): per-slot capital sleeves
    `ov.ctl.status.slots[].balance / max_commitment / venue_sleeve`;
    `bot.screen_score`, `bot.harvest_net_pct_24h`, `bot.expected_fills_24h`
    on the slot card; `decisions[].risk_multipliers`;
    recommendation `atr_pct / price / spread_pct / tvcli_structure /
    exit_profile`; `demo_cap.per_profile`; optimizer `rep.refill`;
    swap-log `caveats`; `state.observe_error_sweeps`. Whole endpoints
    unused by the UI: `/api/observe`, `/api/journal`, `/api/state`.
    The per-slot sleeve numbers are the most operator-relevant miss.
18. **[global] Landscape phone (≈740×360):** the ≤760 tier applies, and
    the statusbar chips (8+ chips, now wrap-enabled) + sticky topbar +
    tab strip + fixed bnav consume roughly half the 360 px height before
    any content. Suggest collapsing the statusbar to 1–2 summary chips
    ("daemon ok · ♥ 100") with an expandable detail at ≤760 px and short
    heights (`(max-height: 420px)`).
19. **[logs] `.logbox { max-height: 65vh }` (styles.css:1065)** uses
    `vh`, which overflows by the iOS Safari toolbar height; `65dvh`
    (the pattern responsive.css already uses for modals) is the safer unit.

---

## Verification notes (checked, already fine)

- **Sparklines:** W=220/H=48 viewBox with CSS `width:100%; height:48px`
  (styles.css:1626) + `preserveAspectRatio="none"` + `vector-effect:
  non-scaling-stroke` — stretch correctly at any card width; re-render is
  idempotent per data epoch (`dataset.at` guard, app.js:692-694).
- **PnL canvas geometry:** container-driven width with computed-padding
  subtraction, `Math.max(240, …)` clamp, correct 2× DPR handling
  (`setTransform(dpr,…)`), and `canvas.width` change detection — all
  correct apart from the P0 data-passing bug.
- **Empty first-boot paths:** every `[]`.map render has a fallback
  (`dec-body` empty-note app.js:1679, `rc-list` 1884, `rel-body` 2518,
  `opt-pending-body` 2179, feed 1046, screen 1055, PnL empty-state text);
  no `undefined.map` throws found.
- **Fail-soft ctl panels:** `renderFastOptimizer`/`renderPositionAnalysis`/
  `renderDataSources` all degrade to "offline" cards when `/api/status` or
  `/api/optimizer` is down; `/api/status` returns 200+`error` and app.js
  nulls it correctly (app.js:221-224).
- **Server threading:** `ThreadingHTTPServer` (server.py:2259) — a
  blocked `/api/llm/health` does not head-of-line-block the other API
  routes (the frontend pile-up in P1-1 is still real).
- **Card-mode robustness:** `decorateTable` skips `colSpan>1` rows
  (empty-state/detail rows render as full-width blocks, correct), nested
  `.rec-detail-table`/`.rel-detail-table` inside `td[colspan]` render
  fine, and the debounced MutationObserver re-decorates after every poll
  repaint.
- **Logs polling UX:** append-only diff with anchor line, at-bottom
  stickiness with 40 px threshold, follow/grep/lines controls preserved
  across renders — well designed.
- **XSS hygiene:** `esc()` applied consistently in every innerHTML
  interpolation of server data I traced (including attribute contexts);
  `toast` and `el()` text-node paths are safe; markdown renderer escapes
  cell content before inline formatting.
- **a11y basics present:** real `tablist`/`tab`/`tabpanel` roles with
  `aria-selected` and `aria-controls` (index.html:33-42); global
  `:focus-visible` outline (styles.css:59-63); `aria-live` on statusbar,
  PnL header, veto strip, toasts; `prefers-reduced-motion` honored
  (styles.css:1265-1271); canvas/SVG charts carry `role="img"` +
  `aria-label`; bnav syncs `aria-current`.
- **Tablets 768–1120 px:** `.fleet-layout` collapses at 1120,
  `.config-layout` at 980, `.dec-ev-grid` at 900, `.pnl-header-grid` at
  1180 — no fixed-min grids overflow in that band; big tables sit in
  `.table-wrap` with the scroll-hint affordance (mobile.js applies hints
  at all widths above 760).
- **Landscape ≤760 phones:** modal `max-height: calc(100dvh - 48px)` +
  inner scroll handles the short viewport; safe-area insets and the iOS
  16 px input zoom guard are all in place (responsive.css:55, 128).
- **Live payload joins:** every other `bot.X`/`st.X` reference in app.js
  matches the live server keys (bots merge `ctl/status` observed over
  state observed correctly at app.js:941-948; `pnl.bots[slot]`
  projections, `demo_cap.total`, `heartbeat.{score,checks,nudges}`,
  `market_brief`, `screen.{top,score_history,run_card_stem}`,
  `swap-log.{trackers,swaps,last_arbiter}` all verified against real
  JSON).


---

## Fix wave 2 (2026-09-08)

Scope: the remaining P2 items + four NEW frontend components as separate
files (modal focus trap, tab arrow-nav, touch tooltips, capital
utilization rail). styles.css untouched; all new CSS lives in
`components/components.css` (one rule added to `responsive.css`). app.js
grew only by the P2-7 digest-skip inside `renderFleet` (+~66 lines incl.
comments) and 6 one-line hooks — every new component is its own file,
self-contained, idempotent and fail-soft. Verified with `node --check`,
a DOM-stub harness driving `renderFleet`/`confirmDialog`/`ModalFocus`/
`TouchTips`/`TabNav` against the live `/api/overview` + `/api/status`
payloads, and the full offline suite (857 tests OK after the concurrent
sizing/apply regressions landed, 8 skipped).

Closed in this wave:

- **P2-1** — `.slot-exits { padding: 6px 14px 0; }` added to
  `components/components.css` (TP/SL/trail strip no longer flush against
  the card edge).
- **P2-5** — `renderMarkdown` now emits
  `<div class="table-wrap"><table class="ledger">…` — run-card tables
  get the scroll container, the ledger styling, and mobile card mode
  (mobile.js picks up `table.ledger` automatically).
- **P2-7** — `renderFleet` digest-skip: a digest of the merged bots +
  slots + daemon state (plus a 1-minute time bucket so "held 2d 3h"
  footers and the stale-cycle banner still refresh) skips the whole
  board rebuild when nothing changed. On rebuild: the focused card
  control gets focus handed back, and each `.ladder .cursor` restarts at
  its previous top then hops to the new one on the next frame — the
  0.9 s transition (styles.css) finally plays. ExpandState (P1-5)
  unaffected: slot cards carry no expandable rows; the skip path is what
  preserves in-flight interactions.
- **P2-8** — NEW `components/modal-focus.js`: Tab/Shift+Tab wrap inside
  the open modal, page scroll locked while open, focus restored to the
  pre-modal element on close. One hook line each in `confirmDialog`
  (app.js) and `openModal` (components/market-chart.js).
- **P2-9** — NEW `components/tab-nav.js`: roving tabindex (selected tab
  0, others −1) + Left/Right/Home/End select-and-focus, driven through
  each tab's own click handler. AND the top tab strip is now hidden at
  ≤760 px (`body.has-bnav .tabs` in responsive.css — mobile.js marks
  the body once the bnav is actually built, so a JS failure degrades
  back to the top strip). bnav buttons remain plain Tab-reachable
  buttons, so keyboard users lose nothing.
- **P2-12** — NEW `components/touch-tips.js`: on coarse-pointer phones
  (≤760 px), the first tap on an element with a non-empty `title`
  shows that title in a small floating chip (`.touch-tip` in
  components.css) and suppresses the click; the next tap activates.
  Opt-out via `data-notip` on the element or an ancestor; any other
  tap clears the armed state; capture-phase so delegated handlers are
  covered; keyboard/mouse paths untouched.
- **P2-13** — the nine unstyled journal kinds got palette-semantic
  colors (components.css): `pnl-snapshot`/`heartbeat`/`market-brief`/
  `screen`/`re-analysis` neutral ink-soft (telemetry), `recenter`
  teal (positive action), `demo-cap-veto`/`optimizer-idle` amber
  (capacity/attention), `position-optimizer-sweep` violet (advisory
  lane, like adjust/cycle).
- **P2-15** — the invalid-grep toast now names the pattern and the
  regex error (`invalid grep pattern: … — …`) instead of a bare label.
- **B2 (from P2-17)** — NEW `components/capital-rail.js`: fleet
  capital-utilization rail rendered as the first card of the Fleet view
  rail — (a) portfolio line committed/ceiling · idle (+%) · proj/24h ·
  ~ret/yr, (b) per-slot rows slot · venue · balance · max commitment ·
  committed · share-of-venue-sleeve bar (+ "dyn" badge for dynamic
  slots), (c) paper-bot cap with per-profile headroom + venue plan
  caps, (d) the honest static why-idle note (tier/risk-multiplier
  discounts + 15% cash buffer + plan caps). Owns its DOM + CSS, keeps
  its own digest (repaints on change only), one hook line in
  `loadOverview`. Responsive: wraps at ≤760, two-line slot rows ≤430.

Verified already closed by the previous wave (no change needed here):
P2-4 (free_slots length guard), P2-6 (loadReports resets the run-card
detail state), P2-10 (textContent double-escape), P2-11 (`.kv-row .k`
min-width in the ≤430 tier), P2-14 (deep-link cohort fetch),
P2-19 (`.logbox` 65dvh in responsive.css). P2-2's dead badge branch is
documented in code as awaiting a server field contract.

Intentionally skipped:

- **P2-16** — self-hosting the two Google Font families. The console is
  otherwise fully offline-capable and the CDN link already degrades to
  system fonts; acceptable skip, revisit if an air-gapped deployment
  becomes real.
- **P2-2 / P2-3** — dead data paths (`observed.exit_queued`,
  `bot.take_profit_pct`) need daemon/server field changes owned by the
  other worker; the dead branches are now commented as such in code.
- **P2-18** — the landscape-phone statusbar/tab-strip collapse shipped
  with the wave-1 landscape tier (`max-height: 480px` rules in
  responsive.css); the remaining suggestion (collapsing the statusbar
  to 1–2 summary chips at all short heights) left as-is — the current
  chip wrap is acceptable at 740×360.
