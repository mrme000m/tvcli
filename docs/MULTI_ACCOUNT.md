# Multi-Account Support

tvcli supports multiple TradingView accounts end-to-end: `pkg/account` models
accounts as data, every transport package (`pkg/tradingview`,
`pkg/pinefacade`) takes credentials as arguments, and the CLI ships an
`account` command plus a global `--account` flag for storage and switching.
Single-account mode (legacy `.env` `SESSION`/`SIGNATURE`/`DEVICE_T`) remains
the default and is unchanged when no `accounts.json` sidecar exists.

## Why multiple accounts

TradingView's free tier caps each account at **2 studies per chart** and
**2 simultaneous WebSocket connections**. A single account running a
long-lived study (or a continuous signal strategy) leaves no headroom for
one-off runs. Spreading work across several free accounts gives each role its
own study/connection budget:

| Role | Account(s) | Work |
|------|------------|------|
| `core` | 1 account | HTTP-only: search / list / compile **public** scripts (never burns a study slot) |
| `script` | 1 per custom script | One long-lived study per consolidated script, isolated from all other work |
| `signal` | 1 account | A continuously-running strategy that extracts signals over time |
| `adhoc` | N accounts, round-robin | Short-lived one-off runs, /eval, sweeps |

## Loading accounts

Two optional input shapes, both handled by `pkg/account`:

**Env-array form** (`.env` or exported):

```bash
ACCOUNT_0_NAME=core           ACCOUNT_0_ROLE=core
ACCOUNT_0_SESSION=...         ACCOUNT_0_SIGNATURE=...
ACCOUNT_0_DEVICE_T=...        ACCOUNT_0_USER=...
ACCOUNT_0_TIER=free
ACCOUNT_0_PROXY=socks5://127.0.0.1:1080

ACCOUNT_1_NAME=xau-scalp      ACCOUNT_1_ROLE=script
ACCOUNT_1_SESSION=...
```

**`accounts.json` sidecar**:

```json
{
  "default": "core",
  "accounts": {
    "core":      {"role": "core",   "sessionId": "...", "tier": "free"},
    "xau-scalp": {"role": "script", "sessionId": "...", "tier": "free",
                  "proxy": "socks5://127.0.0.1:1080"},
    "signal-runner": {"role": "signal", "sessionId": "...", "tier": "free"},
    "adhoc-1":   {"role": "adhoc",  "sessionId": "..."}
  }
}
```

**Legacy fallback:** with no `ACCOUNT_*` rows and no file, `LoadFromEnv()`
synthesizes a single `default` account from `SESSION` / `SIGNATURE` /
`TV_USER` / `DEVICE_T` / `TV_TIER` — identical to today's behavior.

```go
reg := account.LoadFromEnv()        // or account.LoadFromJSON("accounts.json")
acct := reg.DefaultAccount()        // or reg.Get("xau-scalp")
client := tradingview.NewClient(
    tradingview.WithToken(acct.SessionID),
    tradingview.WithSignature(acct.Signature),
    tradingview.WithDeviceToken(acct.DeviceToken),
)
```

Per-account tiers: `acct.Limits()` returns the same `TierLimits` struct the
CLI uses (`MaxCharts`, `MaxIndicators`, `MaxConnections`, `MaxBars`,
`CalcTimeoutSecs`), resolved from `acct.Tier` instead of the global
`TV_TIER`.

## CLI storage + switching

Accounts are stored in an `accounts.json` sidecar (0600; override the path
with `TV_ACCOUNTS_FILE`). The `account` command manages it:

```bash
tv account add core --session <id> --signature <sig> --device-t <d> --user <u> --tier free --role core
tv account add adhoc-1 --session <id> --signature <sig> --device-t <d> --tier free --role adhoc
tv account list                     # names + roles + tiers, credentials masked
tv account show core                # one account, masked
tv account use core                 # set the default account
tv account remove adhoc-1
```

Every command selects an account with the global `--account` flag (highest
priority), the `TV_ACCOUNT` env var, or the registry `default` — resolved in
`cmd/tvcli/main.go` before dispatch, which overrides the legacy `.env`
credentials for that invocation:

```bash
tv run "PUB;…" --account core
tv fetch --symbol BINANCE:BTCUSDT --account adhoc-1
TV_ACCOUNT=adhoc-1 tv backtest "STD;RSI%1Strategy"
```

The active account's `tier` also drives `config.GetTierLimits()` (via the
`activeTier` set by `Config.UseAccount`), so `run`/`backtest`/`eval`/skill
commands honor per-account limits automatically.

## Per-account egress proxy

Each account can carry its own `ProxyURL` (`ACCOUNT_N_PROXY` env key, `proxy`
in the JSON sidecar, or the legacy `TV_PROXY` env var for the single-account
case). All three transports honor it:

- **WebSocket** (`pkg/tradingview.WithProxy`): `socks5://` and `socks5h://`
  URLs dial through `golang.org/x/net/proxy` via `NetDial`; `http(s)://`
  URLs use the standard `Dialer.Proxy`.
- **Auth page fetch** (`pkg/tradingview/auth.WithProxy`): routed through
  `http.Transport{Proxy: ...}`, which natively supports socks5 and http(s).
- **Pine Facade HTTP** (`pkg/pinefacade.WithProxy`): same transport approach.

Single-account CLI usage: set `TV_PROXY=socks5://127.0.0.1:1080` in `.env`
and every WS client, auth check, and Pine Facade call in the CLI/server
routes through it. Empty proxy (the default) means direct connection, so
existing setups are unaffected.

## What is intentionally NOT implemented here

The connection **pool** (account-keyed WS clients with acquire/release,
`MaxConnections` enforcement), the intent→account **router**, and per-account
request queues are application concerns. They belong in the program that owns
the long-lived state (e.g. a worker service), built on top of this registry —
the transports are already account-parameterized, so no `pkg/*` changes are
needed to add them.

Notes for that implementation:

- A free account's `Limits().MaxConnections` (2) bounds sockets per account;
  when an account is saturated, spill to the next `adhoc` account.
- `client.AuthStatus()` (from `pkg/tradingview`) reports the auth state of
  the last `Connect()` — check `Authenticated` after connecting; expired
  cookies must be re-extracted per account.
- Private (`USER;`) scripts are owned by one TradingView user; route
  `script:<name>` intents to the account whose `UserName` owns the script,
  or the ownership precheck will fail.

## HTTP server failover

`internal/server` builds the account failover loop on top of this registry
so the QD backend's single `TVCLI_URL` keeps working when one account is
exhausted. When `tvcli serve` starts with an `accounts.json` sidecar, the
long-lived WS handlers rotate across accounts automatically:

- `/run`, `/run-skill`, `/fetch` try the **active account first** (the one
  `main.go` resolved at startup — the registry default, or `--account` /
  `TV_ACCOUNT`), then every other authenticated registry account in sorted
  order, up to `TV_FAILOVER_MAX` (default **4**).
- A failure only triggers failover when it is **account-scoped**: expired
  cookies, an auth rejection, a study/connection limit, or a WS dial
  failure (`isFailoverError` in `internal/server/failover.go`). Request-
  scoped errors (bad symbol, Pine syntax errors) stop the loop and return
  the first failure — no account swap will fix them.
- `/run-skill` **skips failover for private (`USER;`) skills** that the
  active account does not own: ownership is per-account, so a different
  account would fail the same ownership precheck. Public skills and skills
  carrying raw `Source` run under any account and fail over normally.
- Each attempt saves and deletes its own temp Pine script (`SaveNew` is
  account-scoped), and the response carries the account that served it
  (the `account` field on `/run` and `/fetch`, the `agentContext.account`
  and `execution.attempts` fields on `/run-skill`).
- `/health` reports `accounts` (registry size), `activeAccount`, and
  `failoverMax`; the new `GET /accounts` endpoint lists the registry masked
  (name/role/tier/username/hasAuth/hasProxy/default/active); `GET
  /check-auth?account=NAME` probes a specific account's auth state.

### Choosing the default account

The active account is the registry `default`, so pick a **validated**
account as the default — an expired primary adds a dead auth-precheck
(~2-3s) to every request before the loop fails over. After importing the
free accounts CSV, probe and pin a fresh one:

```bash
tv account import tv_free_accounts.csv           # 40 free adhoc accounts
tv check-auth --account sunilsutar371 --json     # verify before pinning
tv account use sunilsutar371                     # set a valid primary
```

The legacy `.env` `SESSION`/`SIGNATURE`/`DEVICE_T` account can be added as
a `core` role and kept as a last-resort failover candidate even when its
cookies are stale — the loop skips it after one dead precheck.
