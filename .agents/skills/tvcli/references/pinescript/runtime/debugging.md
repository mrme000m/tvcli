# Layer 11: Debugging & Errors — Troubleshooting Guide

> **Prerequisite:** Layer 10 (Patterns). Diagnose compile errors, runtime failures, WebSocket issues, and auth problems.

---

## Error Classification

| Phase | Tool | Typical Errors |
|-------|------|----------------|
| **Compile** | `tvcli compile` / `/translate_light` | Syntax, type mismatch, undeclared |
| **Save** | `tvcli create/push` / `/save/new/next` | Permissions, quota, invalid Pine ID |
| **Runtime** | `tvcli run` / WebSocket | Division by zero, array OOB, study limit |
| **Auth** | `tvcli check-auth` | Expired cookies, wrong tier |

---

## Compile Errors (HTTP `/translate_light`)

### Common Messages

| Error | Cause | Fix |
|-------|-------|-----|
| `Undeclared identifier 'x'` | Variable used before declaration | Declare with `x = ...` first |
| `Type mismatch: float vs int` | Implicit conversion not allowed | Cast: `float(myInt)` |
| `Cannot call 'ta.sma' with 'series string'` | Wrong argument type | Use `input.source()` not `input.string()` |
| `Line 10: Mismatched input 'end' expecting 'if'` | Missing `end` or bad indentation | Check block structure |
| `Script uses too many local variables` | > 1000 locals | Refactor into functions |
| `Loop iteration limit exceeded` | Loop exceeds the execution budget (500ms per loop per bar) | Reduce iterations or use built-ins |

**tvcli output** (`compile.go:55-66`): Shows first 5 errors with line/column.

---

## Runtime Errors (WebSocket)

### Study Error (`study_error` message)
```
study_error: [chartId, studyId, "script error", "Division by zero at line 45"]
```

**Common runtime errors:**
| Error | Cause | Fix |
|-------|-------|-----|
| `Division by zero` | `x / y` where y=0 | `y != 0 ? x / y : na` |
| `Index out of bounds` | `array.get(arr, i)` where i >= size | Check `i < array.size(arr)` |
| `Cannot call 'request.security' with 'lookahead_on'` | Backtest lookahead bias | Use `lookahead_off` |
| `Script calculation timeout` | Exceeds execution-time budget (500ms per loop per bar + total) | Optimize loops, use built-ins |
| `Maximum drawings exceeded` | > max_labels_count | Increase limit or clean old |

**Debug with `--raw`:**
```bash
tvcli run USER;abc123 --raw --raw-out debug.json
# Inspect debug.json for periods, graphics, strategyReport
```

---

## WebSocket Connection Issues

### "study limit" / "maximum number of studies reached"
```
study_error: "study limit: maximum 2 studies allowed"
```

**Causes:**
1. Free tier anonymous = 0 studies
2. Stale sessions from previous runs
3. Too many indicators on chart

**Fixes:**
```bash
# 1. Ensure auth
tvcli check-auth

# 2. Clean stale sessions
tvcli clean --iterations 10 --delay 1000

# 3. Wait 2 minutes for server cleanup
# 4. Use --force-cleanup flag
tvcli run USER;abc123 --force-cleanup
```

### Authentication Failed
```
⚠ Authentication failed: invalid session
The WS connection will use an unauthorized token.
TradingView limits unauthorized sessions to 0 studies
```

**Fix:**
1. Re-extract cookies from browser (DevTools → Cookies)
2. Update `.env` with fresh `SESSION`, `SIGNATURE`
3. Verify: `tvcli check-auth --json`

### Symbol Not Found / No Data
```
symbol_error: "Symbol not found: XAUUSD"
```

**Fix:** Use exchange prefix
```bash
# Wrong
tvcli run USER;abc123 --symbol XAUUSD

# Correct (auto-mapped by tvcli)
tvcli run USER;abc123 --symbol OANDA:XAUUSD
tvcli run USER;abc123 --symbol BINANCE:BTCUSDT
```

### Timeout Waiting for Symbol
```
Error: timeout waiting for symbol load
```

**Fixes:**
- Increase timeout: `--timeout 30000` (internal)
- Check symbol format
- Market may be closed (forex weekends, crypto 24/7)

---

## `check-auth` Diagnostics

```bash
tvcli check-auth --json
```

**Output:**
```json
{
  "valid": true,
  "tier": "free",
  "studies_limit": 2,
  "charts_limit": 1,
  "connections_limit": 2,
  "bars_limit": 180,
  "calc_timeout": 20,
  "error": null
}
```

**If `valid: false`:**
```json
{
  "valid": false,
  "error": "SESSION cookie expired or missing"
}
```

**Action:** Re-extract cookies from browser.

---

## Pine ID Issues

### "no pineId found. Use 'create' first"
```bash
tvcli push script.pine
# Error: no pineId found
```

**Cause:** Script never created, or `@pineId` comment missing.

**Fix:**
```bash
tvcli create script.pine --name "My Script"
# Then
tvcli push script.pine
```

### Pine ID Format
```bash
# Valid formats
USER;abc123def456
PUB;xyz789
STD;MA
# tvcli normalizes: USER;USER;abc → USER;abc
```

---

## Debugging Checklist

### Before Running
- [ ] `tvcli compile script.pine` — passes
- [ ] `tvcli check-auth --json` — `"valid": true`
- [ ] Symbol format: `EXCHANGE:SYMBOL`
- [ ] Timeframe valid for symbol

### Run Fails
- [ ] `tvcli clean --iterations 5` — clears stale sessions
- [ ] `tvcli run --raw --raw-out debug.json` — captures full data
- [ ] Check `debug.json` for `study_error` or empty `periods`
- [ ] Verify tier limits: `--bars` not exceeding tier

### Silent Failures (No Output)
- [ ] Add `--settle 3000` — wait for graphics/backfill
- [ ] Check `study_completed` received in raw output
- [ ] Verify `create_study` sent (debug logs)

---

## Getting Help

```bash
tvcli --help              # All commands
tvcli run --help          # Run options
tvcli sniper --help       # Skill-specific
tvcli check-auth --json   # Machine-readable auth status
```

**Debug logs:** `DEBUG=true tvcli run ...` — shows WebSocket frames.

---

## Next Layer

→ **Layer 12: Reference Index** → `reference/index.md`

Complete lookup: all functions, types, endpoints, tvcli commands, and skill parsers.