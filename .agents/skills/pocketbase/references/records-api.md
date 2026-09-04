# Records API (REST)

REST-ish CRUD over `/api/collections/{collection}/records`.

- **List/Search** — `GET .../records?filter=&sort=&page=&perPage=&expand=`
  returns a paginated list. `filter` uses the PocketBase filter DSL
  (operators like `=`, `!=`, `~`, `>`, `<`, `&&`, `||`, `()`; e.g.
  `status = "active" && created > "2026-01-01"`).
- **View** — `GET .../records/{id}`; `?expand=` pulls in related records.
- **Create** — `POST .../records` with a JSON body.
- **Update** — `PATCH .../records/{id}`.
- **Delete** — `DELETE .../records/{id}`.
- **Batch** — `POST /api/batch` for multiple ops in one request (transactional).

## Realtime tie-in

Record create/update/delete are exactly the events that get pushed over SSE.
So "write a record" == "emit an event" — the persistence layer *is* the event
bus for CRUD-shaped state.

## Auth header

Requests are authenticated by `Authorization: <token>` (or a per-request
`?token=` for realtime SSE). See `auth.md`.

## Key SDK shapes

```js
const pb = new PocketBase('http://127.0.0.1:8090');
await pb.collection('decisions').create({ kind: 'veto', msg: '...' });
const list = await pb.collection('decisions').getList(1, 50, {
  filter: 'kind = "veto"', sort: '-created',
});
await pb.collection('decisions').update(id, { resolved: true });
await pb.collection('decisions').delete(id);
```

Server-side (JS hooks), use `$app` instead of an HTTP client:

```js
const records = $app.findRecordsByFilter("decisions", 'kind = "veto"', "-created", 50, 0);
const rec = $app.findRecordById("decisions", "RECORD_ID");
```