# Collections

Collections are PocketBase's data model. Each maps to a SQLite table
auto-generated from the collection `name` + `fields` (columns). A single row
is a **record**.

Three collection types:

| Type | Purpose | Notes |
|---|---|---|
| **Base** | default app data (posts, orders, journal entries) | full CRUD |
| **View** | read-only, populated by a SQL `SELECT` | aggregations / custom queries |
| **Auth** | login/identity records | adds auth fields + token issuance |

## Fields

Fields are typed columns. Common types: `text`, `number`, `bool`, `date`,
`email`, `url`, `json`, `select` (single/multi), `relation` (FK to another
collection, single or multiple), `file`. Every collection also has implicit
system fields: `id` (15-char string), `created`, `updated`.

Manage collections three ways:
1. **Dashboard** — `/_/` UI edit panel.
2. **Client SDKs** — collection management endpoints (superusers only).
3. **Migrations** — Go (`go-migrations`) or JS (`js-migrations`) for
   reproducible, code-owned schema.

## View collections — the read-only aggregation trick

Useful for derived/aggregate state without writing to a base collection. A
View collection is defined by a plain SQL `SELECT` (e.g. counting posts per
category). Because it's a normal SQL view, it's read-only at the API layer.

## API rules (access control)

Each collection carries 5 rules — `listRule`, `viewRule`, `createRule`,
`updateRule`, `deleteRule` — evaluated per API action. Rules can reference the
request auth state (`@request.auth.*`) and the record (`@collection.field`).
A rule that resolves false/empty denies; leave blank to deny by default (rules
default to locked-down). See `api-rules-and-filters.md` in the docs for the
filter DSL used in both rules and `?filter=` queries.