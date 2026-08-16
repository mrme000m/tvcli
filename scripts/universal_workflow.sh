#!/bin/bash
# tvcli Universal Analyzer Workflow Examples
# This script demonstrates various universal analyzer workflows

set -e

TVCLI="./tvcli"
OUT_DIR="./universal_output"
mkdir -p "$OUT_DIR"

echo "=========================================="
echo "tvcli Universal Analyzer Workflow Examples"
echo "=========================================="
echo ""

# Example 1: Order Block Analysis (Markdown)
echo "📝 Example 1: Order Block Analysis (Markdown)"
echo "----------------------------------------"
$TVCLI analyze "PUB;fVSb3j0I87LvTzPKrQTY5hDUEdsGdnm6" \
  --symbol OANDA:XAUUSD --tf 1h --bars 200 \
  --report --format markdown \
  --title "Order Block Analysis - XAUUSD 1H" \
  --out "$OUT_DIR/order_block_analysis.md"
echo "Saved to $OUT_DIR/order_block_analysis.md"
echo ""

# Example 2: FVG Analysis (Marketing)
echo "🐦 Example 2: FVG Analysis (Marketing Thread)"
echo "----------------------------------------"
$TVCLI analyze "PUB;ff639e15f24646fbaf19ae22ac663140" \
  --symbol BINANCE:BTCUSDT --tf 15m --bars 200 \
  --report --format marketing \
  --title "BTCUSDT FVG Analysis" \
  --out "$OUT_DIR/btc_fvg_thread.txt"
echo "Saved to $OUT_DIR/btc_fvg_thread.txt"
echo ""

# Example 3: Volume Profile Levels (JSON)
echo "📊 Example 3: Volume Profile Levels (JSON)"
echo "----------------------------------------"
$TVCLI analyze "PUB;aea729456b7a44e09661b70ce9e4e987" \
  --symbol OANDA:XAUUSD --tf 4h --bars 500 \
  --json \
  --out "$OUT_DIR/volume_profile_levels.json"
echo "Saved to $OUT_DIR/volume_profile_levels.json"
echo ""

# Example 4: Liquidity Analysis (HTML)
echo "🌐 Example 4: Liquidity Analysis (HTML Report)"
echo "----------------------------------------"
$TVCLI analyze "PUB;09ebff5ba23c452b89ea82522f2aab35" \
  --symbol OANDA:XAUUSD --tf 1h --bars 300 \
  --report --format html \
  --title "XAUUSD Liquidity Analysis" \
  --out "$OUT_DIR/liquidity_report.html"
echo "Saved to $OUT_DIR/liquidity_report.html"
echo ""

# Example 5: Market Structure Break (Order Block Detector)
echo "🔍 Example 5: Market Structure Break (Markdown)"
echo "----------------------------------------"
$TVCLI analyze "PUB;3a1fb6197f314eb2912194d70934bf7e" \
  --symbol BINANCE:BTCUSDT --tf 1h --bars 200 \
  --report --format markdown \
  --title "BTCUSDT Market Structure Break" \
  --out "$OUT_DIR/msb_analysis.md"
echo "Saved to $OUT_DIR/msb_analysis.md"
echo ""

# Example 6: Fair Value Gap with Custom Inputs
echo "⚙️ Example 6: FVG with Custom Inputs (JSON)"
echo "----------------------------------------"
$TVCLI analyze "PUB;ff639e15f24646fbaf19ae22ac663140" \
  --symbol BINANCE:BTCUSDT --tf 5m --bars 100 \
  --input.lookback=20 --input.threshold=1.5 \
  --json \
  --out "$OUT_DIR/fvg_custom_inputs.json"
echo "Saved to $OUT_DIR/fvg_custom_inputs.json"
echo ""

# Example 7: Buyside/Sellside Liquidity (Marketing)
echo "💧 Example 7: Buyside/Sellside Liquidity (Marketing)"
echo "----------------------------------------"
$TVCLI analyze "PUB;09ebff5ba23c452b89ea82522f2aab35" \
  --symbol OANDA:XAUUSD --tf 15m --bars 200 \
  --report --format marketing \
  --title "XAUUSD Buyside/Sellside Liquidity" \
  --out "$OUT_DIR/bsl_ssl_thread.txt"
echo "Saved to $OUT_DIR/bsl_ssl_thread.txt"
echo ""

# Example 8: Liquidity Swings (Markdown)
echo "📈 Example 8: Liquidity Swings (Markdown)"
echo "----------------------------------------"
$TVCLI analyze "PUB;780e612168be41d5aa300f1c16084130" \
  --symbol OANDA:XAUUSD --tf 1h --bars 200 \
  --report --format markdown \
  --title "XAUUSD Liquidity Swings" \
  --out "$OUT_DIR/liquidity_swings.md"
echo "Saved to $OUT_DIR/liquidity_swings.md"
echo ""

# Example 9: Quick Scan (Text)
echo "⚡ Example 9: Quick Scan (Text Output)"
echo "----------------------------------------"
$TVCLI analyze "PUB;fVSb3j0I87LvTzPKrQTY5hDUEdsGdnm6" \
  --symbol BINANCE:BTCUSDT --tf 1h --bars 100
echo ""

echo "=========================================="
echo "All examples completed!"
echo "Output directory: $OUT_DIR"
echo "=========================================="
ls -la "$OUT_DIR"