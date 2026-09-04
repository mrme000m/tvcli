# Event Hooks

Hooks are the server-side extension point — effectively an in-process pub/sub
bus. They exist in **Go** (`app.On…().Bind(handler)`) and **JavaScript**
(`pb_hooks/*.pb.js`). The JS API is derived from Go: Go camelCase →
JS `camelCase`, Go errors → thrown JS exceptions. Every handler must call
`e.next()` to continue; throwing (or skipping `e.next()`) **stops** execution.

Hook family overview (JS names; Go equivalents in `go-event-hooks`):

## Model hooks (fire for Record, Collection, Log — any DB model)

Lifecycle, in order:

- Create: `onModelCreate` → `onModelValidate` (skipped by `saveNoValidate()`) → `onModelCreateExecute`
- Update: `onModelUpdate` → `onModelValidate` → `onModelUpdateExecute`
- Delete: `onModelDelete` → internal checks → `onModelDeleteExecute`

**Critical nuance:** the `*Execute` hooks (and code *after* `e.next()` in the
earlier hooks) run **before the wrapping DB transaction commits**. To listen
for *actually-persisted* events, bind the success/error variants:

- `onModelAfterCreateSuccess` / `onModelAfterCreateError`
- `onModelAfterUpdateSuccess` / `onModelAfterUpdateError`
- `onModelAfterDeleteSuccess` / `onModelAfterDeleteError`

## Record proxy hooks (convenience over Model hooks)

Same lifecycle, prefixed `onRecord*` and already typed to records — no manual
assertion. Scope to specific collections with trailing string args:

```js
onRecordAfterUpdateSuccess((e) => {
  console.log("persisted:", e.record.get("id"));
  e.next();
}, "decisions", "slots");   // only these collections
```

Also `onRecordEnrich` — fires whenever a record is enriched (builtin
responses, realtime serialization, `apis.enrichRecord`); use to redact/add
computed fields.

## Request hooks (only when the matching API endpoint is hit)

- `onRecordsListRequest`, `onRecordViewRequest`, `onRecordCreateRequest`,
  `onRecordUpdateRequest`, `onRecordDeleteRequest`
- `onRecordAuthRequest` / `onRecordAuthRefreshRequest` /
  `onRecordAuthWithPasswordRequest` / `onRecordAuthWithOTPRequest`
- `onBatchRequest` (fires per-batch + the corresponding per-record request hooks)
- `onFileDownloadRequest`, `onFileTokenRequest`

`e.record` / `e.collection` / `e.records` / `e.result` give you the payload;
you can mutate `e.result` to reshape responses.

## Realtime hooks

- `onRealtimeConnectRequest` — SSE connection established (post-`e.next()`
  code runs after disconnect).
- `onRealtimeSubscribeRequest` — client updates its subscriptions; `e.client`,
  `e.subscriptions` allow validation/modification.
- `onRealtimeMessageSend` — before an SSE message is sent to a client
  (`e.client`, `e.message`).

## Others

Mailer hooks (`onMailerSend` etc.), cron hooks (`onCron`), and collection
model hooks (`onCollection*` proxies) round out the catalog — see the docs'
`js-event-hooks` / `go-event-hooks` pages for the exhaustive list.

## Practical guidance

- **"Did it actually commit?"** → use `onRecordAfter*Success` hooks, not
  `onRecord*`/`onRecord*Execute`.
- **Side effects / fan-out** (notify, score, enrich, write a derived record)
  → put them in an `After*Success` hook so they only run on real commits.
- **Validation / rejection** → put in `onRecord*Request` (throw to reject).
- **Reshape what clients see** → `onRecordEnrich` (safe for realtime too).