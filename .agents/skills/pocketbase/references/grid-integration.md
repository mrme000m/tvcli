# PocketBase as the grid-autonomy event-driven persistence layer

This is the concrete mapping between the grid-autonomy daemon's current
file-based state and a PocketBase backend, plus the go/no-go decision. Read
this **before** proposing a rewrite.

## Current persistence (what we'd be replacing)

The daemon is deliberately **stdlib-only** (`http.server`, `subprocess`,
`json`) and single-threaded. State lives under `state/`:

| Artifact | Written by | Shape | Write style |
|---|---|---|---|
| `state.json` | `daemon.py` (`save_state`) | `{slots, active_bots, committed, cooldowns_until, reliability, journal[], …}` | full atomic replace (`os.replace` tmp) |
| `decisions.jsonl` | `agents/reflect.py` (`_append_json_line`) | one JSON line per decision, `sort_keys` | `O_APPEND` + `flock` (when available) |
| `reliability.json` | `execution/reliability_grid.py` (`save`) | archetype stats ledger | atomic replace |
| `market_map-{market}.json`, `market_meta.json` | `execution/resolve.py` | 24h market cache | atomic replace |
| `reports/*.json`, `reports/*.md` | `agents/reflect.py` (`write_run_card`) | run cards | plain write |

The in-memory **journal** (`state["journal"]`) is the event log — every
`log(state, {kind, msg, at, …})` call appends and caps at 200. It is the
natural seam: each `log(...)` call is already an *event* with a `kind` and a
timestamp; it's just not persisted independently or pushed anywhere.

## Proposed PocketBase model

Map each artifact to a **collection**. Collection names become the realtime
topics, so `log()` calls map to record creates that fire SSE.

| Current artifact / call | PocketBase collection | Record fields (rough) |
|---|---|---|
| `log(state, event)` journal | `journal` | `kind`, `msg`, `at`, `slot`, `cycle`, extra JSON |
| `decisions.jsonl` line | `decisions` | `decision_id`, `venue`, `symbol`, `regime`, `decision`, `slot`, `outcome`, `created` |
| `reliability.json` | `reliability` | `archetype`, `samples`, stats… |
| `state["active_bots"]` | `bots` | `slot`, `code`, `status`, `spec`, `created` |
| `state["slots"]` | `slots` | `slot`, plan fields… |
| `market_map` / `market_meta` | `market_cache` (or `view`) | `market`, `symbol`, `cached_at`, payload |
| `reports/*.md` | `run_cards` (with a `file` field) | `ts`, `kind`, `md` (file upload) |
| aggregate reliability stats | **View collection** | SQL `SELECT` over `reliability` |

### The event-driven seam

1. **Write path** — a thin adapter (`pbclient.py`) replaces `save_state` /
   `_append_json_line` / `reliability_grid.save` with
   `pb.collection(...).create/update()`. No change to the daemon's control
   flow; just the persistence sink. Records written → SSE events emitted.
2. **Fan-out hooks** — a `pb_hooks/main.pb.js` binds
   `onRecordAfterCreateSuccess` / `onRecordAfterUpdateSuccess` on `decisions`
   and `journal` to (a) enrich, (b) write derived records, (c) publish custom
   `grid.*` topics via `subscriptionsBroker()` for non-CRUD signals.
3. **Read/observe path** — dashboards or a monitoring service subscribe to
   `journal`/`decisions`/`bots` collections and get a live feed; `?filter=`
   + View collections replace the ad-hoc grep/JSON reads.

### Why realtime matters here

Today, the only way to "watch" the daemon is polling `state.json` or tailing
`daemon.log`. With PocketBase, every guard-veto / rotation / adopt / adjust
becomes a `journal` create that streams to any subscriber in real time — an
event-driven observability layer for free, because record CRUD *is* the event
stream.

## Custom code required (the "consider integrating + adding custom code" part)

PocketBase gives schema + CRUD + SSE + auth out of the box. What we'd add:

1. **`pbclient.py`** — a stdlib `urllib`/`http.client` client (to match the
   daemon's "no third-party deps" stance) wrapping `Authorization` token auth
   and the REST record endpoints. (The JS SDK is fine for dashboards; the
   daemon is Python and currently has zero pip deps — a small hand-rolled
   client keeps that property.)
2. **`pb_hooks/main.pb.js`** — fan-out/validation hooks, custom `grid.*`
   topics, and any enrichment (e.g. attach resolved outcome to a decision when
   its `outcome` field is set).
3. **Migration script** — a one-shot importer that reads the existing
   `state/*.json[l]` files into the collections (so history isn't lost).
4. **Auth/API-key provisioning** — an API key per daemon instance, scoped by
   collection rules.

## Go/no-go decision

PocketBase is a good fit **if** you want, in priority order:

- a **live, queryable** view of daemon state (dashboard + `?filter=`) instead
  of grepping JSON files;
- **realtime** event push (SSE) to a UI or second service;
- a **schema** on top of state that's currently a loose dict/JSONL blob;
- server-side **hooks** to fan out / enrich events.

It is **not** the right layer **if**:

- the daemon must stay a single process with **zero** moving parts (PocketBase
  adds a long-running second server + binary dependency);
- you cannot accept a **pre-1.0** dependency in a trading path (PocketBase's
  own docs say "not recommended for production-critical applications yet");
- the state is truly just a checkpoint file and realtime/queryability buys
  nothing.

**Recommended posture:** keep the file layer as the daemon's *system of
record* (it's atomic, stdlib-only, and already correct), and run PocketBase as
a **read-only projection + realtime fan-out** that the daemon *writes through*
via `pbclient.py` as a side channel. That gets the event-driven/observability
win without making the trading path depend on a pre-1.0 server. Exchange-side
order truth stays with WunderTrading; PocketBase mirrors and broadcasts it.

## Minimal boot sequence

```bash
# 1. run PocketBase (once)
./pocketbase serve                                   # -> create superuser / api key

# 2. define collections via a JS migration (or the dashboard)
#    pb_hooks/main.pb.js

# 3. one-shot import of existing state
python3 pbclient.py import --dir state/

# 4. wire the daemon's persistence sink
#    (replace save_state / _append_json_line / reliability_grid.save
#     with pbclient calls, or add a write-through hook that mirrors files)
```