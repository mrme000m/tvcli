# OpenCode (opencode.ai) — usage/subscription limits API (reverse-engineered)

Verified live 2026-09-04 by replaying the workspace page's SolidStart server
functions. Every fact below was observed on the wire or replayed successfully
(no headful browser needed — the API is plain HTTP once you hold the session
cookie).

## Auth

- Single **httpOnly `auth` cookie** on `opencode.ai` (path `/`, ~561 chars,
  expires ~1 year). This alone authorizes every server function.
- A second httpOnly `provider` cookie on `auth.opencode.ai` (sameSite=None,
  secure) holds the OAuth session and is the refresh path when `auth` lapses.
- No localStorage/sessionStorage, no API key, no Cloudflare fingerprinting →
  plain `fetch` with `Cookie: auth=<token>` works directly.

## Transport

- Endpoint: **`POST https://opencode.ai/_server`** (single RPC for all server
  functions; SolidStart's serialized-server-function transport).
- Headers: `X-Server-Id: <function-hash>`, `X-Server-Instance: server-fn:0`,
  `Content-Type: application/json`, `Cookie: auth=<token>`.
- Body: Seroval-serialized args — `{"t":{"t":9,"i":0,"l":N,"a":[…],"o":0},"f":31,"m":[]}`
  where each arg is `{"t":1,"s":"string"}` or `{"t":0,"s":<number>}`.
- Response: a Seroval script `;0x<hex-len>;(JS)`, `Content-Type: text/javascript`.
  Eval it with `$R === self.$R === globalThis` and read `$R["server-fn:0"][0]`.

> **Gotcha:** the `X-Server-Id` hash is **not globally unique per function
> name** — it is generated per route bundle, so the same function exists under
> several hashes with different arg counts. The table below is the verified set
> for the workspace routes.

## Verified server functions

| Function | `X-Server-Id` | Args | Returns |
|---|---|---|---|
| `lite.subscription.get` | `c7389bd0…753498cd` | `[workspaceId]` | rolling/weekly/monthly usage + limits (the key data) |
| `billing.get` | `c83b78a6…02d3313d` | `[workspaceId]` | subscription, payment method, balance, reload, lite |
| `session.get` | `9bc48083…98e3c9768` | `[workspaceId]` | `{isAdmin, isBeta}` |
| `usage.list` | `15702f3a…ee0c15205` | `[workspaceId, year, month, tz]` | per-model cost breakdown + API keys |

Full hashes + the working client: [`opencode-usage.mjs`](../../opencode-usage.mjs).

## Usage-limit semantics (`lite.subscription.get`)

```json
{
  "mine": true, "useBalance": false, "allowTraining": true,
  "region": ["us","eu","sg","cn"],
  "rollingUsage": { "status":"ok", "resetInSec":18000,   "usagePercent":0,   "usage":0,          "limit":1200000000 },
  "weeklyUsage":  { "status":"ok", "resetInSec":208529,  "usagePercent":15,  "usage":450533599,  "limit":3000000000 },
  "monthlyUsage": { "status":"ok", "resetInSec":952381,  "usagePercent":60.8, "usage":3647919734, "limit":6000000000 }
}
```

- `usage` / `limit` / `totalCost` are integer **1e-8 USD units** (divide by
  `1e8` for dollars): monthly `6000000000` = $60.00, weekly `3000000000` =
  $30.00, rolling `1200000000` = $12.00. `usagePercent` is authoritative.
- `resetInSec` is a countdown to the next window reset.
- `billing.get`'s `balance`/`reloadAmount`/`reloadTrigger`/`monthlyLimit` are
  plain dollars (unscaled) — do not apply the 1e-8 divisor to them.

## Subscription (`billing.get`)

```json
{
  "customerID": "cus_…", "paymentMethodType": "card", "paymentMethodLast4": "4841",
  "balance": 0, "reloadAmount": 20, "reloadTrigger": 5,
  "monthlyLimit": null, "monthlyUsage": 0,
  "subscription": null, "subscriptionPlan": null,
  "lite": {}, "liteSubscriptionID": "sub_1TXMrj…"
}
```

- `subscription` / `subscriptionPlan` null = no active paid plan; usage is met
  by the **lite** tier (`liteSubscriptionID` present, auto-reload at
  `$reloadTrigger` of `$reloadAmount`).

## Session (`session.get`)

`{"isAdmin": true, "isBeta": false}` for the workspace owner.

## Per-model usage (`usage.list`)

`{usage: [{date, model, totalCost, keyId, plan}], keys: [{id, displayName, deleted}]}`
— `plan` is `"lite"` or `null` (free models). Observed models: `mimo-v2.5-free`,
`laguna-s-2.1-free`, `hy4-preview`, `kimi-k2.6`, `deepseek-v4-flash`,
`muse-spark-1.2-contributor`.

## Session storage

`OPENCODE_AUTH` / `OPENCODE_PROVIDER` / `OPENCODE_WORKSPACE_ID` /
`OPENCODE_USER_EMAIL` (+ expiry timestamps) live in the bwdev vault item
**`opencode-session`**, provisioned to
`browser-debug/secrets/runtime/opencode-session.env` by `bw-provision.sh`
(`secrets/manifest.json`). Re-sync/rotate via
`bw-provision.sh --export opencode-session opencode-session.env`.