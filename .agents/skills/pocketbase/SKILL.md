---
name: pocketbase
description: PocketBase (pb) — open-source Go backend with embedded SQLite, realtime SSE subscriptions, built-in auth, admin dashboard, and Go/JavaScript extension hooks. Use when asked to stand up a local backend, model data as collections/records, wire realtime/event-driven persistence, add auth, or evaluate PocketBase as the persistence layer for an existing system (e.g. the grid-autonomy trading daemon). Covers how it works, when to use Go vs JS hooks, and concrete integration recipes.
---

# PocketBase

PocketBase is a single-binary open-source backend: **embedded SQLite** (via
`pb_data/`), **realtime subscriptions** over SSE, **built-in auth**
(password/OTP/OAuth2), an **admin dashboard UI** (`/_/`), and a **REST-ish
Web API** — extensible in **Go** (as a framework) or **JavaScript** (via an
embedded goja engine). One `./pocketbase serve` process replaces a database +
auth service + admin panel + realtime broker.

> **Status caveat:** pre-1.0.0. PocketBase's own docs say it is **NOT**
> recommended for production-critical apps yet unless you are fine reading the
> changelog and applying occasional manual migrations. For a trading daemon,
> treat it as an internal ops/observability layer, not the system of record for
> exchange-side order truth.

## What PocketBase is, in one mental model

| Concept | PocketBase term | Under the hood |
|---|---|---|
| Table | **Collection** (Base / View / Auth) | SQLite table, auto-created from collection `name` + `fields` |
| Row | **Record** | SQLite row |
| Schema | Collection `fields` | typed columns (text/number/bool/date/file/relation/json/…) |
| Query | `?filter=` on REST, or SDK `getList` | SQL `WHERE` via a filter DSL |
| Access control | **API rules** (`listRule`, `viewRule`, `createRule`, `updateRule`, `deleteRule`) | evaluated per-request |
| Event stream | **Realtime** (`pb.realtime.subscribe`) | SSE; fires on record create/update/delete |
| Server logic | **Hooks** (`pb_hooks/*.pb.js` or Go `app.On…().Bind()`) | server-side lifecycle handlers |
| Login | **Auth collection** + `Authorization` token | stateless JWT-ish token in header |

Two files/dirs appear next to the binary: `pb_data/` (SQLite + uploads + logs)
and `pb_hooks/` (your JS hooks). Collections/records can be managed from the
Dashboard, via the client SDKs (superusers only), or programmatically via
Go/JS migrations.

## How to run it

```bash
# download prebuilt binary (linux x64 / arm64; macOS/others on GitHub releases)
./pocketbase serve          # first run prints an installer URL -> create superuser
./pocketbase superuser create EMAIL PASS   # or create superuser manually

# default routes
http://127.0.0.1:8090/_/    # admin dashboard
http://127.0.0.1:8090/api/  # REST API base
```

Client SDKs: `pocketbase` (JS/browser), `pocketbase` (Dart/Flutter), plus
community Python/Go. Minimal JS client:

```js
import PocketBase from 'pocketbase';
const pb = new PocketBase('http://127.0.0.1:8090');
```

## Extend with Go vs JavaScript — how to choose

PocketBase is *one* project with **two extension surfaces**, and they are
roughly equivalent (the JS API is derived from the Go API — Go camelCase
becomes JS `camelCase`, Go errors become thrown JS exceptions).

- **Go** (`docs/go-overview`): use PocketBase as a package inside your own
  `main.go` — full control, compile-time safety, custom portable binary. Right
  when you want a single deployable that embeds the whole backend, or need
  concurrency/performance.
- **JavaScript** (`docs/js-overview`): drop `*.pb.js` files into `pb_hooks/`
  next to the **prebuilt** binary (v0.17+). No rebuild — the process
  auto-reloads on file change (UNIX). Right for rapid iteration, and for the
  grid-autonomy integration where the trading logic already lives in Python
  and you want a *thin* server-side glue layer without a Go toolchain.

Both expose the same hooks (model/record lifecycle, request, realtime,
mailer, cron) and the same `$app` global.

## Event hooks — the event-driven surface

Hooks are the server-side equivalent of a pub/sub bus. They fire on model
lifecycle, API requests, and realtime. Full catalog in
`references/event-hooks.md`; the load-bearing pattern:

- **Persisted-success hooks** are the safe ones to listen on for
  "actually committed" events: `onRecordAfterCreateSuccess`,
  `onRecordAfterUpdateSuccess`, `onRecordAfterDeleteSuccess`. The `*Execute`
  and `*Request` variants fire *before* the wrapping DB transaction commits.
- Every hook handler calls `e.next()` to continue the chain; throwing (or
  not calling `e.next()`) **stops** execution.
- Scope a hook to specific collections with trailing args:
  `onRecordAfterCreateSuccess(handler, "decisions", "slots")`.

```js
// pb_hooks/main.pb.js
onRecordAfterUpdateSuccess((e) => {
  console.log("record updated:", e.record.get("id"));
  e.next();
}, "decisions");
```

## Realtime — subscriptions over SSE

PocketBase **automatically** emits realtime events for record
create/update/delete (and OAuth2 redirects). Clients subscribe by collection
or topic:

```js
await pb.realtime.subscribe('decisions', (e) => console.log(e));
await pb.realtime.subscribe('example',  (e) => console.log(e)); // custom topic
```

Server side you can **publish custom messages** to subscribers via
`$app.subscriptionsBroker().clients()` — this is the escape hatch to push
arbitrary events (not just record CRUD) through the same SSE channel:

```js
const message = new SubscriptionMessage({ name: "grid.tick", data: JSON.stringify({...}) });
const clients = $app.subscriptionsBroker().clients();
for (let clientId in clients) {
  if (clients[clientId].hasSubscription("grid.tick")) clients[clientId].send(message);
}
```

Details + the `onRealtimeConnectRequest` / `onRealtimeSubscribeRequest` /
`onRealtimeMessageSend` hooks: `references/realtime.md`.

## Collections, records, auth

- **Collections** — 3 types: Base (data), View (read-only SQL `SELECT`),
  Auth (login/identity). Backed by auto-generated SQLite tables.
  `references/collections.md`.
- **Records CRUD** — REST `GET/POST/PATCH/DELETE /api/collections/{name}/records`,
  with `?filter=`, `?sort=`, `?page=`, `?perPage=`, and `?expand=` for relations.
  `references/records-api.md`.
- **Auth** — stateless: client sends `Authorization: <token>`. Password auth
  is `pb.collection('users').authWithPassword(email, pass)`, then the token
  lives in `pb.authStore.token`. API-key (non-user) auth for machine clients,
  OTP, and OAuth2 also available. `references/auth.md`.

## Recipes / when to reach for it

- **Need a quick local backend with a UI to inspect data** → run the binary,
  use the dashboard at `/_/`.
- **Need realtime push of DB changes** → record CRUD already emits SSE; add
  custom topics via the broker for non-CRUD events.
- **Need server-side validation/enrichment** → JS hooks in `pb_hooks/`, no
  rebuild.
- **Need to ship the backend as part of your own binary** → Go framework path.

## Integration with the grid-autonomy daemon

The grid-autonomy system currently persists state as flat JSON/JSONL files
(`state/state.json`, `state/decisions.jsonl`, `state/reliability.json`,
`state/market_map-*.json`, `state/market_meta.json`) with an in-memory journal
capped at 200 entries. PocketBase can become the **event-driven persistence
layer** behind that, without rewriting the Python daemon:

- Map each JSON/JSONL artifact to a **collection** (see
  `references/grid-integration.md` for the exact schema mapping).
- The daemon's `log(state, event)` journal becomes **record creates** on a
  `journal` collection → realtime SSE pushes every decision/veto/rotation to
  any dashboard.
- `decisions.jsonl` append → `onRecordAfterCreateSuccess` hook on `decisions`
  can fan out (enrich, score, notify).
- `reliability.json` → a `reliability` collection with a View collection for
  aggregate stats.

Full mapping, proposed schema, and a **decision table on whether PocketBase
is the right layer here** (vs. staying on SQLite files, or a message queue):
`references/grid-integration.md`. Read that before proposing a rewrite — the
current file layer is intentionally stdlib-only and atomic; PocketBase adds a
long-running server + SSE realtime + queryability, and you trade off a moving
dependency (pre-1.0) for those.

## References

- [references/collections.md](references/collections.md) — collections, fields, View/Auth types
- [references/records-api.md](references/records-api.md) — REST CRUD, filters, expand
- [references/auth.md](references/auth.md) — auth token, password/OTP/API-key/OAuth2
- [references/event-hooks.md](references/event-hooks.md) — full hook catalog (model/record/request/realtime/mailer/cron)
- [references/realtime.md](references/realtime.md) — SSE subscriptions, broker, custom topics
- [references/grid-integration.md](references/grid-integration.md) — mapping the grid-autonomy state to PocketBase + go/no-go decision