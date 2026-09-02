# TradingView frontend drawing capabilities

What the chart frontend exposes (and hides) for creating and managing
drawing objects — trendlines, shapes, text, etc. ("line tools" in TV
terminology) — and how drawings are persisted.

## The two widget APIs

```js
const cwc = window._exposed_chartWidgetCollection;
cwc.activeChartWidget._value          // "exposed" ChartWidget
cwc.activeChartWidget._value._modelWV._value   // internal chart model
```

`window._exposed_chartWidgetCollection.activeChartWidget` is a *reduced*
API surface intended for the widget host page. Its `_value` is the internal
ChartWidget instance, and **drawing constructors like `createShape` /
`createMultipointShape` are not exposed on it** — they exist only in the
non-widget chart internals. Agents should not assume they can call them
from page JS.

What IS available and verified live (chart `dvv4N29P`, `bdg tv drawings`):

- `._modelWV._value` model with `dataSources()`, `panes()`, `mainSeries()`.
- `executeActionById(actionId)` — runs chart actions (including the
  drawing-tool actions) exactly as toolbar buttons do.
- `_lineToolsSynchronizer` — the internal registry that holds the chart's
  drawing objects ("line tools").

## How drawings are stored & saved

- Drawings are managed by the **line-tools synchronizer**
  (`_lineToolsSynchronizer`) on the chart widget.
- Persistence goes through `_saveChartService` over **REST** — the layout
  save endpoint — *not* over the chart data WebSocket. The data WS carries
  price/study streams only.
- Consequence for agents: drawing changes are observable in bdg's *network*
  collection (REST POST to the save/layout service) and in the frontend
  model, but never appear as `~m~` frames on the data socket.

## What agents can do today

1. **Inspect** — `bdg tv drawings` lists drawings on the active chart plus
   the drawing-layer capabilities (`lineToolSynchronizer` presence,
   `executeActionById` availability). On the reference chart it reported
   `0 drawing(s)` with both capabilities present.
2. **Trigger UI actions** — call `executeActionById("…")` from page JS for
   drawing-related actions (activate a drawing tool, apply templates,
   undo/redo, remove drawings). This is the supported path: it goes through
   the same code the toolbar buttons use.
3. **Read the model** — after drawings exist, the synchronizer/model
   reflect them; enumerate them in-page (extend `DRAWINGS_SCRIPT` in
   `src/commands/tv/scripts.ts` to dump tool metadata — points, price/time
   coords, options).

## Boundaries / caveats

- The internal drawing constructors are not on the exposed widget; rely on
  `executeActionById` + model reads rather than calling internals directly.
  Internal API names shift between TV releases.
- The chart must be in an interactive state (not a static/embedded view)
  for drawing actions to apply.
- Drawings persist only if the layout autosaves; REST save may be skipped
  when not logged in — verify via the network collector.
- Volume-profile and event overlays (ESD$*) are *sources*, not drawings —
  don't confuse them with line tools when counting "drawings".