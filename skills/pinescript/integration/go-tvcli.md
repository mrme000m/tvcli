# Layer 9: Go tvcli Integration — Automation & Agent Usage

> **Prerequisite:** Layer 8 (WebSocket). Using the compiled Go binary for scripting, CI/CD, and AI agents.

---

## Binary Location

```bash
/Volumes/ExMac/code/tradingview/go/tvcli      # Pre-compiled
# Or build:
cd /Volumes/ExMac/code/tradingview/go && go build -o tvcli ./cmd/tvcli
```

---

## Configuration (`.env`)

```bash
# Required for write ops
SESSION=<sessionid cookie>
SIGNATURE=<sessionid_sign cookie>
TV_USER=<your_username>

# Optional
DEVICE_T=<device_t cookie>        # Helps auth stability
TV_TIER=free                      # free|essential|plus|premium|ultimate
PINE_FACADE_URL=https://pine-facade.tradingview.com/pine-facade
DEBUG=false
```

**Loaded automatically** from `.env` in working directory or `~/.tvcli/.env`.

---

## Command Reference

### Script Lifecycle
```bash
tvcli compile script.pine                    # Syntax check only
tvcli create script.pine --name "Name"       # Create remote
tvcli push script.pine                       # Update (by file or #ID)
tvcli push 1 --force                         # Force push unchanged
tvcli pull 1                                 # Download to local
tvcli pull USER;abc123                       # Download by Pine ID
tvcli delete 1 --yes                         # Delete remote
tvcli list                                   # Local tracked scripts
tvcli list --remote                          # Remote saved scripts
```

### Search & Discovery
```bash
tvcli search "EMA crossover" --limit 20      # Public scripts
tvcli publist --offset 0 --limit 50          # Public library
tvcli top --limit 100 --output top.json      # Top public scripts
```

### Execution
```bash
tvcli run USER;abc123 --symbol OANDA:XAUUSD --tf 15m --bars 180    # free tier max
tvcli run USER;abc123 --symbol BINANCE:BTCUSDT --tf 1h --bars 180 --signals --json
tvcli run USER;abc123 --raw --raw-out dump.json    # Full capture
tvcli run USER;abc123 --persistent --loop 30s       # Continuous
tvcli eval script.pine --signals --agent           # Run without Pine ID
tvcli eval --script 'indicator("x"); plot(close)'  # Inline source
```

> **Bar limits:** The CLI auto-caps `--bars` to the tier limit (free=180,
> essential=365, plus+=unlimited). Higher `--bars` values are silently capped.

### Data Fetching (No Indicator Needed)
```bash
tvcli fetch --symbol OANDA:XAUUSD --tf 5m --bars 180 --csv-out data.csv  # free tier max
tvcli sync --symbol OANDA:XAUUSD --tf 5m --bars 5000 --loop 5m   # Gap-fill (auto-capped per run)
```

### Utilities
```bash
tvcli check-auth --json                        # Verify cookies + tier
tvcli clean --iterations 5 --delay 500         # Free study slots
tvcli inputs USER;abc123 --json                # Inspect inputs
tvcli inputs sniper --json                     # Skill vs Pine diff
tvcli skills --json                            # List indicator skills
tvcli serve --addr :8765                       # HTTP server for agents
```

### Indicator Skills (Built-in)
```bash
tv sniper --symbol OANDA:XAUUSD --tf 15m --bars 180 --signals
tv smc --symbol BINANCE:BTCUSDT --tf 1h --json --agent
tv xau-scalp --symbol OANDA:XAUUSD --tf 1H --bars 180 --json --agent --allow-private
tv smc --help                                  # Skill-specific help
```

> **Private scripts** (USER;…): add `--allow-private` to bypass the private-script guard.
> Use `--agent` for the agent-ready v2 envelope (market, structure, opportunities, narrative).

---

## Automation Patterns

### CI/CD: Compile + Push on Git Push
```yaml
# .github/workflows/pine.yml
name: Deploy Pine Script
on:
  push:
    paths: ['*.pine']
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup tvcli
        run: |
          curl -L -o tvcli https://github.com/.../tvcli-linux-amd64
          chmod +x tvcli
      - name: Compile
        run: ./tvcli compile ${{ github.event.head_commit.modified[0] }}
        env:
          SESSION: ${{ secrets.TV_SESSION }}
          SIGNATURE: ${{ secrets.TV_SIGNATURE }}
          TV_USER: ${{ secrets.TV_USER }}
      - name: Push
        run: ./tvcli push ${{ github.event.head_commit.modified[0] }}
        env:
          SESSION: ${{ secrets.TV_SESSION }}
          SIGNATURE: ${{ secrets.TV_SIGNATURE }}
          TV_USER: ${{ secrets.TV_USER }}
```

### Scheduled Signal Generation (Cron)
```bash
# crontab: every 15 min during market hours
*/15 9-16 * * 1-5 cd /path/to/project && ./tvcli run USER;abc123 \
    --symbol OANDA:XAUUSD --tf 15m --bars 180 --signals --json \
    --out signals_$(date +\%Y\%m\%d_\%H\%M).json
```

### Agent Integration (HTTP Server)
```bash
# Terminal 1: Start server
tvcli serve --addr :8765

# Terminal 2: Agent calls
curl -X POST http://localhost:8765/compile -d '{"source": "indicator(\"x\"); plot(close)"}'
curl -X POST http://localhost:8765/run -d '{"pineId": "USER;abc123", "symbol": "OANDA:XAUUSD", "timeframe": "15", "bars": 500, "signals": true}'
curl http://localhost:8765/health
```

**Endpoints:**
| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/health` | — | `{status: "ok"}` |
| POST | `/compile` | `{source: "..."}` | CompileResult |
| POST | `/fetch` | `{symbol, timeframe, bars}` | OHLCV array |
| POST | `/clean` | `{iterations, delay}` | Clean result |
| POST | `/run` | `{pineId, symbol, timeframe, bars, signals}` | Run result |

---

## Input YAML for Parameter Sweeps

```bash
# Generate template
tvcli inputs USER;abc123 > inputs.yaml

# Edit inputs.yaml:
inputs:
  in_0: 20        # Length
  in_1: "hl2"     # Source
  in_2: "SMA"     # MA Type

# Run with custom inputs
tvcli run USER;abc123 --inputs inputs.yaml --signals --json
```

### Parameter Sweep (Multi-Run)
```bash
tvcli run USER;abc123 --multi-run --symbol OANDA:XAUUSD --tf 15m
# Outputs sweep configurations:
# length: [10, 20, 30, 50]
# source: [close, hl2, ohlc4]
# Combines all permutations
```

---

## Output Formats

### Default (Human)
```
✓ Connected
✓ Symbol resolved: OANDA:XAUUSD
✓ Study created: st_abc123
✓ Study completed
Periods: 500
Signals:
  [1] crossover: EMA Fast ↑ EMA Slow @ 2024-01-15 14:30 (2034.50)
  [2] level_cross: RSI ↓ 70 @ 2024-01-15 15:00
```

### JSON (`--json`)
```json
{
  "periods": [...],
  "signals": [...],
  "graphic": {...},
  "strategyReport": {...},
  "meta": {"pineId": "USER;abc123", "symbol": "OANDA:XAUUSD", "timeframe": "15", "bars": 500}
}
```

### Agent Envelope (`--agent`)
```json
{
  "version": "1.0",
  "timestamp": "2024-01-15T14:30:00Z",
  "pineId": "USER;abc123",
  "symbol": "OANDA:XAUUSD",
  "timeframe": "15m",
  "data": { ... },
  "signals": [...]
}
```

---

## Error Handling in Scripts

```bash
# Exit codes
tvcli compile bad.pine; echo $?   # 1 on compile error
tvcli run USER;bad --symbol X; echo $?  # 1 on runtime error

# Capture stderr
tvcli run USER;abc123 --symbol X 2>errors.log
```

**Common exit codes:**
- 0: Success
- 1: Generic error (compile, runtime, auth, network)
- 2: Usage error (bad args)

---

## Debugging

```bash
# Enable debug logging
DEBUG=true tvcli run USER;abc123 --symbol X --tf 5m

# Check auth status
tvcli check-auth --json
# {"configured": true, "authenticated": true, "pro": false,
#  "username": "vb_mrme00", "canRunStudies": true, "serverRunning": false}

# Background server management
tvcli serve --daemon         # Start in background
tvcli serve --status         # Check status + health
tvcli serve --stop            # Stop background server

# Clean stale sessions
tvcli clean --iterations 10 --delay 1000
```

---

## Performance Tips

1. **Use `--persistent` + `--loop`** for repeated runs (avoids reconnect)
2. **Cap `--bars` to tier limit** (free=180, essential=365, plus=unlimited)
3. **Run `clean` before batch jobs** to free study slots
4. **Use `--settle`** (default 1500ms) for graphics/strategy report backfill
5. **`--force-cleanup`** retries aggressively on study limit errors

---

## Next Layer

→ **Layer 10: Common Patterns** → `patterns/common.md`

Covers: Reusable Pine Script idioms — trend detection, SMC, order blocks, FVGs, session logic, MTF.