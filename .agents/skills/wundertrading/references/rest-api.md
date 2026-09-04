# WunderTrading REST API — HMAC auth + endpoints

Base URL: `https://wundertrading.com/open_api/…`
Verified live (2026-09-03): all endpoints below return 200 with the signing
recipe in this file.

## HMAC request signing

Headers:

| Header | Required | Value |
|---|---|---|
| `X-API-Key` | yes | cabinet API key (same pair as MCP) |
| `X-Signature` | yes | `Base64(HMAC_SHA256(secret, payload))` |
| `X-Timestamp` | yes | **milliseconds** (13 digits) |
| `X-Recv-Window` | no | max request age in ms, default 10000 |

**Payload** = five newline-joined fields:

```
HTTP_METHOD + "\n" + PATH_WITH_QUERY + "\n" + TIMESTAMP + "\n" + RECV_WINDOW + "\n" + BODY
```

- METHOD uppercase; PATH includes the query string exactly as sent; BODY is
  the exact serialized JSON bytes (empty string for GET).
- **The `RECV_WINDOW` field must equal the `X-Recv-Window` header value
  verbatim.** If you omit the header, sign with an **empty** 4th field
  (two consecutive `\n` before the body) — the default `10000` does NOT go
  into the payload. (Verified: header `60000`+payload `"60000"` → 200;
  no header+payload `""` → 200; no header+payload `"10000"` → 401.)
- Body must match what is sent byte-for-byte (send the same string you signed).

### Verified bash signing function

```bash
WT_BASE="https://wundertrading.com"

wt_req() { # wt_req METHOD PATH_WITH_QUERY [JSON_BODY]
  local METHOD="$1" P="$2" BODY="${3:-}"
  local TS RW SIG
  TS=$(python3 -c 'import time; print(int(time.time()*1000))')   # ms
  RW="60000"                                                     # must match header
  SIG=$(printf '%s\n%s\n%s\n%s\n%s' "$METHOD" "$P" "$TS" "$RW" "$BODY" \
        | openssl dgst -sha256 -hmac "$WUN_SECRET_KEY" -binary | base64)
  curl -sS -X "$METHOD" "$WT_BASE$P" \
    -H "X-API-Key: $WUN_API_KEY" -H "X-Signature: $SIG" \
    -H "X-Timestamp: $TS" -H "X-Recv-Window: $RW" \
    ${BODY:+-H "Content-Type: application/json" -d "$BODY"}
}

export WUN_API_KEY="..." WUN_SECRET_KEY="..."
wt_req GET "/open_api/api_profiles?limit=10"
```

**Python `httpx` without browser:** `python .agents/skills/wundertrading/scripts/wt_httpx.py open_api GET "/open_api/api_profiles?limit=10"`
(pure `httpx` HMAC, no browser, no Cloudflare challenge — verified live).

**Gotchas (all observed live):**

- **macOS `date +%s%3N` is broken** (BSD date has no `%N` — it appends a
  literal `N`, producing an invalid timestamp and a misleading
  `401 Invalid signature`). Use `python3 -c 'import time; print(int(time.time()*1000))'` or node.
- Error mapping is coarse: bad key → `Invalid API key`; bad signature OR
  unparseable timestamp → `Invalid signature`; stale-but-well-formed
  timestamp → `Expired request`. Signature is checked **after** the
  timestamp freshness check.
- Never send the secret key itself in a request; sign with it only.

## Endpoints (from the official docs)

| Endpoint | Permission | Weight |
|---|---|---|
| `GET /open_api/exchanges` | — | 1 |
| `GET /open_api/markets?exchanges=CODE[,CODE…]` | — | 1 |
| `GET /open_api/api_profiles` (+query filters) | — | 1 |
| `GET /open_api/strategies/live` | `strategy:read` | — |
| `GET /open_api/strategies/history` | `strategy:read` | 10 |
| `GET /open_api/strategies/{id}` | `strategy:read` | — |
| `GET /open_api/strategies/{id}/orders` | `strategy:read` | — |
| `POST /open_api/strategies/trade` | `strategy:write` | 1 |
| `PATCH /open_api/strategies/trade` (edit) | `strategy:write` | — |
| `PUT /open_api/strategies/{id}/market_enter` | `strategy:write` | — |
| `POST /open_api/strategies/{id}/swing` | `strategy:write` | — |
| `DELETE /open_api/strategies/{id}/cancel` | `strategy:write` | — |
| `DELETE /open_api/strategies/{id}/market_close` | `strategy:write` | — |

Query-filter names mirror the MCP tool arguments (`exchanges`, `apiProfiles`,
`statuses`, `page`, `limit`). Response envelopes use `{"items": […],
"pagination": {"page", "limit", "total", "pages"}}` (verified on
`api_profiles`, `strategies/live`). POST/PATCH bodies use the same field
names as `place_strategy_trade` / `edit_trade_strategy` (see `mcp-tools.md`).

## Rate limits (live-verified headers)

- **API key:** 1200 tokens/min. **IP:** 2400 tokens/min AND 400 requests/10 s
  — breaching the latter triggers a **5-minute ban**.
- Token/weight system: each request costs weight by complexity (documented
  weights: history = 10, trade = 1).
- Every response carries `RateLimit-Limit`, `RateLimit-Remaining`,
  `RateLimit-Reset` (ms epoch). Watch `Remaining` in loops; back off near 0.

## When REST vs MCP

- **MCP** (via configured client tools `mcp__wundertrading__*` or the curl
  recipe in `mcp-tools.md`) is the primary interface — schemas live with the
  tools, and trade tools carry their own safety rails.
- **REST** is for scripting/automation where MCP is unavailable: bulk export
  jobs, cron monitors, CI pipelines. Same account, same keys, same limits.
