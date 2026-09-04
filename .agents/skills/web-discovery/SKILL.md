---
name: web-discovery
description: >
  Turn ANY web platform into programmable automation. Investigate a platform's
  live UI (headful stealth CloakBrowser over CDP, real input events, vision
  confluence) and its network layer (XHR/REST, WebSocket, cookies, auth,
  Cloudflare fingerprints) with bdg, codify verified findings into
  reverse-engineered API docs, then forge reusable artifacts — platform CLI
  tools (wt.mjs/tv.mjs pattern), agent skills, dsh plugin rows, and
  prime-orchestrator fleet presets — and improve them from live usage
  feedback. Use for platform API discovery, web reverse engineering,
  generating or fixing web-platform CLIs and tools, or wiring new platform
  capabilities into the dsh/prime-agent stack.
license: MIT
metadata:
  author: tvcli-workspace
  version: "1.0"
---

# web-discovery — from live web platform to reusable agent tooling

The bdg+cloak browser system (`browser-debug/`) exists for one job: let an
agent investigate a web platform's interface UX and its corresponding
network-layer API, then codify what it finds into reverse-engineered APIs for
programmatic usage — dsh plugins and CLI tools that other agents and scripts
can use and reuse for automation. This skill is the repeatable loop for that
job, generalized from what was already proven on TradingView and
WunderTrading.

## The stack

| Piece | Role |
|---|---|
| `browser-debug/launch.mjs` | download + launch the stealth-patched CloakBrowser Chromium, headful, CDP on 9222..9321 (profile via `CB_PROFILE`) |
| `browser-debug/bdg/` | agent-friendly browser-debugger CLI: telemetry daemon over a page-level CDP target — network capture (`network list`, `network websockets`), DOM (`dom query`), console, a11y/role inference, dialogs, screenshots, arbitrary CDP (`cdp <Domain>.<Method>`) |
| `browser-debug/tv.mjs`, `wt.mjs` | forged per-platform CLIs — session restore (cookies from the vault), in-page `fetch` API calls, eval, screenshots |
| `browser-debug/vision.mjs` | Mistral vision model describing screenshots — the UI-understanding confluence check |
| `browser-debug/docs/`, `bdg/docs/` | codified findings (e.g. `bdg/docs/tv/network-protocol.md`, `message-catalog.md`) |
| `.agents/skills/tv-network` | example of a codified skill that grew out of this loop |

Attach bdg to the running browser before investigating:

```sh
node launch.mjs                                   # headful stealth browser
curl -s http://127.0.0.1:9222/json                # page webSocketDebuggerUrl
node bdg/dist/index.js --chrome-ws-url "ws://…" --no-headless "https://<platform>/"
node bdg/dist/index.js network list               # captured XHR/fetch
node bdg/dist/index.js network websockets         # WS connections
```

## The five-phase loop

### 1. SCOUT — passive recon

- Launch the headful browser (`launch.mjs`), navigate to the platform,
  attach bdg to the page target.
- Capture the baseline network surface: `network list` (XHR/fetch) and
  `network websockets` (frames). Identify: REST endpoints, a private WS
  protocol, GraphQL, telemetry noise.
- Map the UI: `dom query` for structure, a11y roles (`aria-pressed`,
  `data-name`) for state, `Page.captureScreenshot` + `vision.mjs` for visual
  state understanding.
- Probe auth: where do the session cookies come from (login page, SSO),
  which requests carry them, what is fingerprint-gated.

### 2. INVESTIGATE — exercise the UX, capture the wire

- Drive the interface with REAL input events (see gotchas) and record what
  each user action costs on the wire: `bdg network list -j` before/after
  diffs, `network websockets` frame capture.
- For session-auth XHR surfaces, replay calls from inside the page:
  `wt.mjs eval` / `wt.mjs api METHOD /path [body]` (fetch-in-page inherits
  the browser's TLS + cookies + fingerprint).
- Find the programmatic path for each UI affordance: exposed window
  objects, in-page model APIs, hotkey registries (see the tv-network skill's
  timeframe/symbol/study findings for the depth this reaches).
- Verify every candidate fact LIVE before writing it down — a fact is only
  "verified" when you observed the request/response or ran the call.

### 3. CODIFY — write the reverse-engineered API down

Follow the conventions that already exist in this repo:

- **Protocol docs** in `bdg/docs/<platform>/` (pattern:
  `network-protocol.md` for framing/endpoints/auth/lifecycle,
  `message-catalog.md` for every wire message, plus per-topic docs).
  Mark each fact as verified-live (with a date/context) vs inferred.
- **A skill** in `.agents/skills/<platform>/SKILL.md` (agentskills.io
  frontmatter, operational commands, verified facts, extension patterns,
  rules). Register it in the root `package.json` `pi.skills` list and the
  `.agents/skills/README.md` table.
- **Knowledge-base captures** via the `openknowledge` skill when an insight
  is durable across sessions.

### 4. FORGE — generate the reusable tools

- **Platform CLI** — copy `templates/platform-cli.mjs` (this skill dir),
  swap the `EDIT-ME` constants (origin, session-cookie file, default URL),
  and you get: session restore, `api` (in-page fetch), `eval`, `shot`,
  `record` (network capture → endpoint summary). Keep it zero-dependency
  Node (>=22 global WebSocket), like `wt.mjs`.
- **dsh wiring** —
  - a plugin tools row in the web profile patch (pattern: the `wt-tools`
    `cloakDir` row and the `mcp-wundertrading` MCP row written by
    `bootstrapping/python/prime_stack/stages/fleet.py`; keys come from the
    vault at provision time, never committed);
  - a fleet preset in `bootstrapping/presets/<agent>/`
    (`preset.yml` + `agent.cordis.yml`, persona + tools; the `fleet-presets`
    stage installs it with marker semantics — add the name to
    `FLEET_PRESETS` in `bootstrapping/python/prime_stack/config.py`).
- **Skill stub** for tvcli-style integration if the platform exposes
  analysis (see `pine2tool` for the pattern of registering new skills).

### 5. IMPROVE — during and after usage

- When a forged tool misbehaves, first RE-CAPTURE the live wire
  (`record` / `network list`) — platforms drift (study-type build numbers,
  input-id offsets, endpoint moves) and the doc is the thing that is wrong.
- Fix the doc/skill/preset at the drift point; propagate marker-managed
  presets by editing the vendored source in `bootstrapping/presets/` and
  re-running the `fleet-presets` stage.
- Feed new verified facts back into the skill (`refine`-style: smallest
  evidence-backed update at the exact point that failed).
- Re-verify: screenshot + `vision.mjs` diff, or in-page re-read of the
  value the tool claims to have changed.

## Verified gotchas (do not relearn these)

1. **Never pass `--headless`** to the stealth-patched Chromium — even
   `--headless=false` sets background-only mode: CDP answers but no window.
2. **Synthetic `.click()` lies** — it flips `aria-pressed` but does not move
   panels / press buttons. Use real `Input.dispatchMouseEvent` /
   `Input.dispatchKeyEvent` at element centers.
3. **Fingerprint-gated APIs** (Cloudflare "Just a moment…", PHPSESSID+
   cf_clearance surfaces): raw HTTP with valid cookies still 403s. Replay
   via fetch-in-page (the `wt.mjs` `api` command) or drive the UI.
4. **"No XHR fired" does not mean "nothing happened"** — TradingView's
   timeframe/symbol changes are pure WebSocket. Capture WS frames, not just
   requests, before concluding an action is local-only.
5. **Hotkeys need a blurred active element** — a stray input focus silently
   eats every keyboard shortcut.
6. **Secrets stay in the vault** (Bitwarden via
   `browser-debug/secrets/bw-provision.sh`, runtime files under
   `browser-debug/secrets/runtime/`). Never print or commit cookie/API-key
   values; never write them into skills, docs, or preset files.
7. **Free tiers cap things** — count the limits (e.g. TV: ≤2 user studies
   per chart) and codify them with the API, so tools degrade instead of
   failing mysteriously.

## Deliverable checklist (one platform, end to end)

- [ ] `bdg/docs/<platform>/network-protocol.md` + `message-catalog.md` (or
      equivalent) with verified-live markers
- [ ] `.agents/skills/<platform>/SKILL.md`, registered in `package.json`
      (`pi.skills`) + `.agents/skills/README.md`
- [ ] `browser-debug/<platform>.mjs` CLI (from the template) with `record`
- [ ] dsh wiring: profile patch row and/or fleet preset registered in
      `bootstrapping/presets/` + `FLEET_PRESETS`
- [ ] at least one end-to-end automation proven live (UI action → wire
      capture → replay → verified result), recorded in the skill
