# bdg vision — Mistral-powered UI understanding

The `bdg vision` command group uses Mistral vision models to help agents
understand what a web page shows and, crucially, **what changed on screen
after each programmatic interaction** (click, fill, scroll, key press).

```
bdg vision describe [image]        # describe the current page (or an image file)
bdg vision compare <before> <after> # diff two screenshots, describe the UI changes
```

## Setup

- API key: resolved from the `MISTRAL_API_KEY` environment variable, or the
  `MISTRAL_API_KEY=` line of `../.env` (browser-debug/.env).
  The key is never printed or logged.
- Models (2026-08): `mistral-small-2603` (default — vision-capable, fast,
  cost-effective) and `mistral-medium-3.5` (frontier multimodal, for hard
  cases). Pixtral model IDs are retired; the current multimodal line is
  Mistral Small 4 / Medium 3.5. Use `--model <id>` to switch.
- Endpoint: `POST https://api.mistral.ai/v1/chat/completions` with
  OpenAI-compatible `image_url` content parts (`data:image/...;base64,...`).

## Interaction-change recipe

The pattern for "what did this interaction change on screen":

```bash
bdg dom screenshot /tmp/before.png
bdg dom click '[data-name="header-toolbar-quick-search"]'   # any interaction
bdg dom screenshot /tmp/after.png
bdg vision compare /tmp/before.png /tmp/after.png
```

Verified live on chart `dvv4N29P`: the compare correctly reported the added
search overlay (position, appearance, placeholder text, close button) and
noted the chart itself was unchanged.

If you want to ask a specific question instead of the default diff:

```bash
bdg vision compare /tmp/before.png /tmp/after.png \
  --prompt "Did any price or indicator values change? List them."
```

## Commands

### `vision describe [image]`

Flags: `--prompt <text>`, `--model <id>`, `--png`, `--quality <n>` (JPEG,
default 80), `-j/--json`.

Without an argument it screenshots the current viewport via CDP and
describes it. With a file argument it describes that image instead (PNG,
JPEG, WebP, GIF). The default prompt asks for the site/page, main UI
regions, visible content, and anything an automation agent should know.

### `vision compare <before> <after>`

Flags: `--prompt <text>`, `--model <id>`, `-j/--json`.

Sends both images in one request (BEFORE first) and asks the model to
enumerate added/removed/moved/restyled elements and state changes, or to say
so explicitly when nothing changed.

## Notes & limits

- Only the viewport is captured (no full-page scroll/stitch). For long pages
  use `bdg dom screenshot` with its resize options, then `vision describe
  <file>`.
- Responses are not guaranteed deterministic; for layout verification prefer
  DOM assertions (`bdg dom eval`) and use vision as the qualitative layer.
- Cost/tokens: each request is one chat completion; usage tokens are shown
  in the footer when the API reports them (`-j` includes them in `usage`).
- 429/API errors exit with a semantic error message and exit code 110
  (software error range).

## Implementation

- `src/commands/vision/mistral.ts` — key resolution, image loading, API call.
- `src/commands/vision/describe.ts`, `compare.ts`, `index.ts` — CLI wiring.
- No new dependencies (Node `fetch`); daemon unchanged, so no restart needed
  after a dist rebuild.

See also [tv/bdg-tv-guide.md](tv/bdg-tv-guide.md) for the TradingView
commands this complements.