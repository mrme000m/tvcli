#!/bin/bash
# tvcli Agent Workflow Examples
# This script demonstrates various agent workflows for analysis and marketing

set -e

TVCLI="./tvcli"
OUT_DIR="./agent_output"
mkdir -p "$OUT_DIR"

echo "=========================================="
echo "tvcli Agent Workflow Examples"
echo "=========================================="
echo ""

# Example 1: Quick Market Scan (JSON)
echo "📊 Example 1: Quick Market Scan (JSON)"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv,dvi,ema-atr \
  --symbol OANDA:XAUUSD \
  --tf 15m \
  --bars 200 \
  --json \
  --out "$OUT_DIR/quick_scan.json"
echo "Saved to $OUT_DIR/quick_scan.json"
echo ""

# Example 2: Comprehensive Analysis (Markdown)
echo "📝 Example 2: Comprehensive Analysis (Markdown)"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv,dvi,ema-atr,sr-breaks,trend \
  --symbol OANDA:XAUUSD \
  --tf 1h \
  --bars 500 \
  --report \
  --format markdown \
  --title "Gold (XAUUSD) - 1H Comprehensive Analysis" \
  --out "$OUT_DIR/gold_1h_analysis.md"
echo "Saved to $OUT_DIR/gold_1h_analysis.md"
echo ""

# Example 3: Social Media Thread (Marketing)
echo "🐦 Example 3: Social Media Thread (Marketing)"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv,dvi,trend \
  --symbol BINANCE:BTCUSDT \
  --tf 1h \
  --bars 300 \
  --report \
  --format marketing \
  --title "BTCUSDT Hourly Analysis" \
  --out "$OUT_DIR/btc_thread.txt"
echo "Saved to $OUT_DIR/btc_thread.txt"
echo ""

# Example 4: Scalping Setup with Presets
echo "⚡ Example 4: Scalping Setup (with Presets)"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv,liq-sweep,order-flow \
  --symbol BINANCE:BTCUSDT \
  --tf 5m \
  --bars 200 \
  --preset scalping \
  --json \
  --out "$OUT_DIR/btc_scalping.json"
echo "Saved to $OUT_DIR/btc_scalping.json"
echo ""

# Example 5: Multi-Timeframe Swing Analysis
echo "📈 Example 5: Multi-Timeframe Swing Analysis"
echo "----------------------------------------"
$TVCLI agent \
  --skills trend,mtf,xau-trend,golden \
  --symbol OANDA:XAUUSD \
  --tf 4h \
  --bars 300 \
  --report \
  --format markdown \
  --title "Gold Swing Analysis - Multi-Timeframe" \
  --out "$OUT_DIR/gold_swing_4h.md"
echo "Saved to $OUT_DIR/gold_swing_4h.md"
echo ""

# Example 6: HTML Report for Web Publishing
echo "🌐 Example 6: HTML Report for Web Publishing"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv,dvi,ema-atr,trend \
  --symbol OANDA:XAUUSD \
  --tf 1h \
  --bars 500 \
  --report \
  --format html \
  --title "XAUUSD Professional Analysis" \
  --out "$OUT_DIR/xauusd_report.html"
echo "Saved to $OUT_DIR/xauusd_report.html"
echo ""

# Example 7: Sequential Run (for rate limiting)
echo "🔄 Example 7: Sequential Run (Rate Limit Safe)"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv,dvi,ema-atr,sr-breaks,trend,smc \
  --symbol OANDA:XAUUSD \
  --tf 15m \
  --bars 200 \
  --sequential \
  --timeout 180 \
  --json \
  --out "$OUT_DIR/sequential_full.json"
echo "Saved to $OUT_DIR/sequential_full.json"
echo ""

# Example 8: Custom Inputs
echo "⚙️ Example 8: Custom Input Overrides"
echo "----------------------------------------"
$TVCLI agent \
  --skills bsv \
  --symbol OANDA:XAUUSD \
  --tf 5m \
  --bars 100 \
  --input.volMaLen=20 \
  --json \
  --out "$OUT_DIR/custom_inputs.json"
echo "Saved to $OUT_DIR/custom_inputs.json"
echo ""

echo "=========================================="
echo "All examples completed!"
echo "Output directory: $OUT_DIR"
echo "=========================================="
ls -la "$OUT_DIR"