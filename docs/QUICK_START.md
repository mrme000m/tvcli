# Quick Start Guide

## 1. Build

```bash
go build -o tvcli ./cmd/tvcli
```

## 2. Configure Authentication

Create a `.env` file in the project root:

```bash
cat > .env <<EOF
SESSION=your_sessionid_cookie
SIGNATURE=your_sessionid_sign_cookie
TV_USER=your_tradingview_username
DEVICE_T=your_device_t_cookie
TV_TIER=free
EOF
```

**How to get cookies:**
1. Open https://www.tradingview.com/chart/ in your browser (must be the /chart/ page)
2. Open DevTools (F12) → Application → Cookies → `https://www.tradingview.com`
3. Copy these 4 cookie values:
   - `sessionid` → `SESSION`
   - `sessionid_sign` → `SIGNATURE`
   - `device_t` → `DEVICE_T`
4. Your TradingView username → `TV_USER`
5. Set `TV_TIER` to match your subscription (free, essential, plus, premium, ultimate)

## 3. Fetch Market Data (No Auth Needed)

```bash
# Get 50 bars of BTC/USDT 1-hour candles
./tvcli fetch --symbol BINANCE:BTCUSDT --tf 1H --bars 50

# Save to JSON
./tvcli fetch --symbol BINANCE:BTCUSDT --tf 5m --bars 100 --json-out btc.json

# Gold
./tvcli fetch --symbol OANDA:XAUUSD --tf 15m --bars 50
```

## 4. Run Any Pine Script

### From a .pine file
```bash
# Compile only (syntax check)
./tvcli eval my_script.pine --compile-only

# Run and get extracted signals as JSON
./tvcli eval my_script.pine --signals --json --symbol BINANCE:BTCUSDT --tf 1H

# Run and get agent-ready output
./tvcli eval my_script.pine --signals --agent --json --symbol BINANCE:BTCUSDT --tf 1H

# Run with raw output (debugging)
./tvcli eval my_script.pine --signals --raw --symbol BINANCE:BTCUSDT --tf 1H

# Pass Pine inputs
./tvcli eval my_script.pine --signals --json --symbol BTCUSDT --tf 5m length=20 src=close
```

### From inline source
```bash
./tvcli eval --script '//@version=5\nindicator("Test", overlay=true)\nplot(close)' --signals --json
```

### Run a pre-published script by Pine ID
```bash
./tvcli run "PUB;6daafb2cabe6419d98ae25229d2327f8" --signals --agent --json --symbol BTCUSDT --tf 1H
```

## 5. Run a Built-in Skill

```bash
# List all skills
./tvcli skills

# Run SMC (Smart Money Concepts) with agent output
./tvcli smc --symbol BINANCE:BTCUSDT --tf 1H --agent --json

# Use a preset
./tvcli sniper --symbol BTCUSDT --tf 5m --preset scalping --agent --json

# Override individual inputs
./tvcli dvi --symbol BTCUSDT --tf 1H --input length_volatility=20 --agent --json

# Bypass custom parser, use generic extractor
./tvcli dvi --symbol BTCUSDT --tf 1H --signals --agent --json

# Show the Pine metaInfo schema without running
./tvcli run "PUB;ff1a0136336340f38e908eeb12ea33aa" --schema
```

## 6. Search for Scripts

```bash
# Search TradingView's public library
./tvcli search "RSI divergence" --limit 10 --json

# Fetch top scripts
./tvcli top --limit 50 --output top.json
```

## 7. Manage Scripts

```bash
# List local tracked scripts
./tvcli list

# List remote saved scripts
./tvcli list --remote

# Create a new remote script
./tvcli create my_script.pine --name "My Indicator"

# Pull a remote script
./tvcli pull "PUB;ff1a0136336340f38e908eeb12ea33aa"

# Push updates
./tvcli push my_script.pine --force

# Delete
./tvcli delete "USER;abc123" --yes
```

## 8. Free Account Tips

Free accounts are limited to 2 indicators per chart. The CLI handles this:

```bash
# Clean stale chart sessions before running
./tvcli clean --iterations 3

# The run/eval commands auto-clean before and after
./tvcli smc --symbol BTCUSDT --tf 1H --force-cleanup --json
```

If you hit study limit errors:
- Run `./tvcli clean` to free slots
- Wait 5-10 seconds after cleaning
- Use `--force-cleanup` flag for automatic retry

## 9. Start the HTTP Server

```bash
./tvcli serve --addr :8765

# Health check
curl http://localhost:8765/health

# Compile a script
curl -X POST http://localhost:8765/compile \
  -H "Content-Type: application/json" \
  -d '{"source":"//@version=5\nindicator(\"Test\")\nplot(close)"}'

# Fetch OHLCV
curl -X POST http://localhost:8765/fetch \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BINANCE:BTCUSDT","tf":"1H","bars":50}'
```

## 10. Debug

```bash
# Enable debug logging
TW_DEBUG=1 ./tvcli eval script.pine --signals --raw --symbol BTCUSDT --tf 1H

# Dump raw WS output to file
./tvcli run "PUB;6daafb2cabe6419d98ae25229d2327f8" --raw-out raw.json --symbol BTCUSDT --tf 1H

# Inspect Pine inputs
./tvcli inputs "PUB;6daafb2cabe6419d98ae25229d2327f8" --json
```

## Common Timeframes

| Short | Full |
|-------|------|
| `1m` | 1 minute |
| `5m` | 5 minutes |
| `15m` | 15 minutes |
| `1H` / `1h` | 1 hour |
| `4H` / `4h` | 4 hours |
| `1D` / `1d` | 1 day |
| `1W` / `1w` | 1 week |

## Common Symbols

| Market | Format | Examples |
|--------|--------|---------|
| Crypto | `EXCHANGE:PAIR` | `BINANCE:BTCUSDT`, `BYBIT:ETHUSDT` |
| Forex | `BROKER:PAIR` | `OANDA:XAUUSD`, `FXCM:EURUSD` |
| Stocks | `EXCHANGE:TICKER` | `NASDAQ:AAPL`, `NYSE:TSLA` |

Shorthand (auto-resolved): `BTCUSDT` → `BINANCE:BTCUSDT`, `XAUUSD` → `OANDA:XAUUSD`
