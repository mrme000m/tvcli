# Binance paper profile — investigation + runbook

Date: 2026-09-04. Verified against the live, logged-in wundertrading.com
session (CloakBrowser on :9222).

## TL;DR

- WunderTrading **does not offer Binance *spot* paper trading**. Binance
  paper is **futures-only** (Binance Futures, USDT-M).
- A clean API endpoint exists and is codified in
  `execution/profiles.py`: `POST /en/trader/my-exchanges/master-api-profile/upsert`.
- Profile **`demo-bn`** was created; it resolves to exchange
  `BINANCE_FUTURES` (paper, $10,000 demo balance), not Binance spot.

## Evidence

- `GET /en/trader/my-exchanges/exchange-family-list` returns, for the
  BINANCE family, three children:
  - `BINANCE` (type `["spot"]`) — **no paper**
  - `BINANCE_DELIVERY` (type `["coinm"]`) — **no paper**
  - `BINANCE_FUTURES` (type `["usdtm", "paper"]`) — **paper available**
- The "Add exchange → Demo Account" drawer lists Binance as a demo exchange,
  but its flow is labeled "Binance Demo" and its sign-up link points at
  `binance.com/en/futures/...`. After creation, the profile lists under
  exchange `BINANCE_FUTURES`.

## API path (preferred)

```
POST /en/trader/my-exchanges/master-api-profile/upsert
Content-Type: application/json
X-W-CSRF-Token: <window.baseServerConfig.appCsrfToken>

{
  "api": "<32-hex placeholder>",
  "secret": "<32-hex placeholder>",
  "enabled": true,
  "name": "demo-bn",
  "exchangeFamily": "BINANCE",
  "paperTrading": true,
  "marginMode": "cross",
  "favorite": false,
  "tradeMode": "hedge_mode"
}
```

- `api` / `secret` are **random placeholders**. Paper profiles need no real
  exchange keys; the UI generates 32-hex strings and the backend accepts
  them. `execution/profiles.py` generates the same placeholders locally.
  **Never pass real exchange API keys here.**
- Name uniqueness is enforced: re-posting an existing name returns HTTP 400
  with violation `name` → "You have account with that name".
- Use the browser transport (`wt_browser.py api POST ...`), not raw httpx
  (raw requests hit Cloudflare 403).

Programmatic call:

```python
from execution import profiles
profiles.create_paper_profile("demo-bn", dry_run=False)
```

## Manual UI flow (fallback)

1. Open **My Exchanges** (`/en/trader/my-exchanges`).
2. Click **Add exchange**.
3. Select the **Demo Account** tab (paper).
4. Click the **Binance** tile.
5. Enter an **Account Name** (e.g. `demo-bn`).
6. Click **Connect**. The drawer shows "Your exchange connected!".

No API keys are requested at any point on the paper path.

## Safety

- Never submit real exchange API keys on the paper path (the `api`/`secret`
  fields are placeholders only).
- Do not touch the real Hyperliquid profile
  (`WunderTrading-1769861648579`); this flow creates only new paper
  profiles.
