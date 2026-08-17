# Pine Script Skill — Progressive Reference

> **Start here.** This skill provides progressively discoverable information on writing, compiling, deploying, and running Pine Scripts on TradingView — from core language basics to backend API integration with the Go `tvcli` project.

## Quick Start (30 seconds)

```bash
# 1. Check auth (required for write ops)
tvcli check-auth

# 2. Compile a script (validates syntax without saving)
tvcli compile my-indicator.pine

# 3. Create & push to TradingView
tvcli create my-indicator.pine --name "My Indicator"
# ...edit locally...
tvcli push 1                      # Push by local ID (from tvcli list)

# 4. Run on live data
tvcli run USER;abc123 --symbol OANDA:XAUUSD --tf 15m --bars 180 --signals  # free tier max
```

---

## Progressive Discovery Layers

| Layer | File | Audience | Time to Read |
|-------|------|----------|--------------|
| **0. Essentials** | `core/essentials.md` | First-time users | 5 min |
| **1. Language Core** | `core/language.md` | Pine Script writers | 15 min |
| **2. Type System** | `core/types.md` | Advanced logic | 10 min |
| **3. Execution Model** | `runtime/execution.md` | Debugging/optimization | 10 min |
| **4. Indicators vs Strategies** | `core/indicator-vs-strategy.md` | Architecture decisions | 5 min |
| **5. Inputs & Settings** | `core/inputs.md` | UX/configuration | 10 min |
| **6. Plots & Visuals** | `core/plots.md` | Chart output | 10 min |
| **7. Backend API (Pine Facade)** | `api/pine-facade.md` | CLI/automation authors | 15 min |
| **8. WebSocket Runtime** | `api/websocket.md` | Real-time execution | 15 min |
| **9. Go tvcli Integration** | `integration/go-tvcli.md` | Go project users | 10 min |
| **10. Common Patterns** | `patterns/common.md` | Reusable idioms | 15 min |
| **11. Debugging & Errors** | `runtime/debugging.md` | Troubleshooting | 10 min |
| **12. Reference Index** | `reference/index.md` | Lookup | — |

**Read in order** — each layer assumes knowledge of previous ones. Jump to any layer when you hit a wall.

---

## When to Use What

| Task | Start At Layer |
|------|----------------|
| "I want to write my first indicator" | 0 → 1 → 5 → 6 |
| "My script won't compile" | 0 → 11 |
| "How do I push scripts from CI/CD?" | 7 → 9 |
| "Run my indicator on live data headless" | 8 → 9 |
| "Extract signals programmatically" | 9 → 10 |
| "Understand study limit errors" | 8 → 11 |
| "Port a TradingView indicator to Go" | 7 → 8 → 9 |

---

## Key Files in This Workspace

```
/Volumes/ExMac/code/tradingview/go/
├── tvcli                    # Compiled Go binary (run `./tvcli`)
├── cmd/tvcli/main.go        # CLI entry point
├── internal/cmd/            # All CLI commands
│   ├── compile.go           # Syntax validation (Pine Facade /translate_light)
│   ├── create.go            # Create new script (Pine Facade /save/new)
│   ├── push.go              # Update script (Pine Facade /save/next)
│   ├── run.go               # WebSocket execution + signal extraction
│   └── check_auth.go        # Cookie validation
├── pkg/pinefacade/          # HTTP Pine Facade API client
│   ├── client.go            # Compile, SaveNew, SaveNext, Delete, Get, Search
│   ├── types.go             # CompileResult, CompileError, Position, ScriptResult
│   ├── util.go              # PineID extraction, SHA256, timeframe normalization
│   └── search.go            # Public script search
├── pkg/tradingview/         # WebSocket real-time API client
│   ├── client.go            # WS connection, auth token, protocol framing
│   ├── chart.go             # Chart session, symbol resolution, series
│   ├── study.go             # Study creation, data processing, graphics, strategy reports
│   ├── indicator.go         # PineIndicator, BuiltinIndicator, input handling
│   └── protocol.go          # ~m~ framing parser/formatter
└── skills/pinescript/       # THIS SKILL (progressive reference)
```

---

## Authentication Prerequisites

**Required for write operations** (create, push, delete):
```bash
# .env file (auto-loaded)
SESSION=<sessionid cookie from browser>
SIGNATURE=<sessionid_sign cookie from browser>
TV_USER=<your TradingView username>
TV_TIER=free|essential|plus|premium|ultimate  # affects bars/connections/indicators
```

**Extract cookies:**
1. Log into tradingview.com
2. DevTools → Application → Cookies → tradingview.com
3. Copy `sessionid` → `SESSION`, `sessionid_sign` → `SIGNATURE`

---

## Common Pitfalls (Quick Reference)

| Symptom | Cause | Fix |
|---------|-------|-----|
| "study limit" on run | Free tier: max 2 indicators, 180 bars; stale sessions | `tvcli clean --iterations 5` or wait 2 min |
| Compile succeeds but run fails | `translate_light` ≠ full runtime validation | Check runtime errors in `run --raw` output |
| PineID not found | Script never saved, or wrong format | Use `tvcli create` first; PineID format: `USER;xxxxxx` |
| WebSocket disconnects | No auth token (anonymous) | Set SESSION+SIGNATURE; free tier = 0 studies anon |
| Timeframe ignored | Wrong format (e.g., "15m" vs "15") | Use `NormalizeTimeframe("15m")` → "15" |
| Symbol not found | Missing exchange prefix | Use `OANDA:XAUUSD` not `XAUUSD` (auto-mapped in util) |

---

## Next Step

Read **Layer 0: Essentials** → `core/essentials.md`