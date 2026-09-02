# TradingView studies & indicators

How studies (indicators, strategies, event overlays, volume profiles) are
represented in the frontend model, named on the wire, and fed data — plus
the free-tier limits.

## Where studies live in the page

The active chart widget is reachable from page JS:

```js
window._exposed_chartWidgetCollection.activeChartWidget._value   // internal ChartWidget
window._exposed_chartWidgetCollection.activeChartWidget._value._modelWV._value
```

The model (`_modelWV._value`) exposes `dataSources()`, `panes()`,
`mainSeries()`. `dataSources()` returns every data source on the chart —
price series, studies, event overlays, and bookkeeping sources. **There is
no reliable REST endpoint to list a chart's studies by slug**; enumerate
them from `dataSources()` instead (this is exactly what `bdg tv studies`
does).

## Classification

`bdg tv studies` classifies each source:

| kind | Detection | Examples (chart `dvv4N29P`, 2026-08-21) |
| --- | --- | --- |
| `pine` | model's `isTVScriptStrategy` / `isTVScript` flags | `E7gFVY` RSI Strategy |
| `event` | id prefix `ESD$` and/or linked-to-series event source | `ESD$TV_DIVIDENDS` Dividends, `ESD$TV_SPLITS` Splits, `ESD$TV_EARNINGS` Earnings, `ESD$TV_ROLLDATES` roll dates |
| `other` | everything else non-bookkeeping | `5bRAOh` Fixed range volume profile |

Bookkeeping sources to ignore when counting studies
(`NON_STUDY_SOURCE_IDS` in `src/commands/tv/scripts.ts`):

```
_seriesId                      # the main price series
PublishedChartsTimeline        # "Ideas on chart" timeline
futures_contract_expiration
latestUpdates
ChartEventsSource
```

plus any source whose `name` is `"Crosshair"`.

Live result on the reference chart: 6 studies = 1 pine strategy + 4 event
overlays + 1 volume profile, alongside 6 non-study sources.

## studyType strings on the wire

`create_study`'s 5th parameter is a versioned type string:
`<Script>@<library>-<build>`. Observed live:

| studyType | What it is |
| --- | --- |
| `Script@tv-scripting-101!` | Arbitrary Pine script (go client's default) |
| `StrategyScript$STD;RSI%1Strategy@tv-scripting` | The built-in RSI Strategy |
| `Dividends@tv-basicstudies-276` | Dividends event overlay |
| `Splits@tv-basicstudies-276` | Splits event overlay |
| `Earnings@tv-basicstudies-276` | Earnings event overlay |
| `BarSetContinuousRollDates@tv-corestudies-44` | Continuous-futures roll dates |
| `Seasonals@tv-basicstudies-238!` | Seasonals (transient, TV-internal) |

`@tv-basicstudies-<n>` / `@tv-corestudies-<n>` = compiled "basic"/"core"
study libraries; `@tv-scripting-101!` = user Pine engine. The build number
changes over time — capture it live rather than hardcoding.

## Pine input encoding

`create_study`'s 6th parameter maps each script input name to
(reference: `go/pkg/tradingview/indicator.go`):

```json
{ "<input-name>": { "v": <value>, "f": <isFake>, "t": <type> } }
```

- `v` — the input value,
- `f` — boolean "fake" flag,
- `t` — input type code.

The go client builds this from its indicator metadata (`GetInputs()`).

## Data delivery: du payloads per study

After `study_completed`, the server streams `du` (alias
`timescale_update`) frames whose `p[1]` map has one key per study/series id.
Each study entry contains:

- `st` — status array,
- `ns.d` — a JSON string holding the study's update. For Pine scripts it is
  a `graphicsCmds`-style command stream (`{"v":[nodeId,type,args...],...}`);
  for strategies it additionally carries the strategy report (trades,
  equity, statistics).
- `t` — source-type tag (e.g. `"s1_st1"`).

The go client keys on `data[1][studyID]` (`study.go onData`) and feeds the
per-study entry to the study's processor — the same structure `bdg tv ws`
uses to report `du series keys`.

## Free-tier limits

- Free accounts are capped at **2 user-added studies per chart**. The
  reference chart `dvv4N29P` sits exactly at 2/2: RSI Strategy + Fixed range
  volume profile.
- **Event overlays (ESD$*) do not count** against the cap — a free chart can
  show 2 user studies plus all four event overlays (6 sources total, as
  observed).
- If the socket authenticates as anonymous (`unauthorized_user_token`
  fallback — see [network-protocol.md](network-protocol.md)), user studies
  are effectively disallowed (0). Missing `device_t` cookie is the usual
  cause.
- The transient **Seasonals** study (`st1`, created then removed during
  layout restore) is TV-internal and does not count either.