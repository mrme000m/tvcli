# Multi-Account Support

tvcli supports multiple TradingView accounts at the **library level**:
`pkg/account` models accounts as data, and every transport package
(`pkg/tradingview`, `pkg/pinefacade`) already takes credentials as arguments
instead of reading globals. Wiring a pool of accounts into the CLI/server is
an **optional** follow-on — single-account mode remains the default and is
unchanged.

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

ACCOUNT_1_NAME=xau-scalp      ACCOUNT_1_ROLE=script
ACCOUNT_1_SESSION=...
```

**`accounts.json` sidecar**:

```json
{
  "default": "core",
  "accounts": {
    "core":      {"role": "core",   "sessionId": "...", "tier": "free"},
    "xau-scalp": {"role": "script", "sessionId": "...", "tier": "free"},
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