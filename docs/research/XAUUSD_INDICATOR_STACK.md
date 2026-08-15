# XAUUSD Market-Analysis Indicator Stack

Research question run through Perplexity (`deep_research` mode):

> Find 7 public TradingView Pine Scripts and 7 YouTube videos for XAUUSD (gold) market analysis. One script per major aspect: Volume Profile, Liquidity/Order Blocks, Market Structure/Breaker Blocks, Fair Value Gaps, Support/Resistance Supply-Demand Zones, Multi-Timeframe Trend/Bias, Volatility/ATR Stop-Run detection. Each should produce quantitative outputs, signals, or confidence scores for speculation.

Raw Perplexity output is saved under [`pplx-research/xauusd-tools-raw.json`](pplx-research/xauusd-tools-raw.json); this file is the synthesized, actionable version.

## Quick-reference table

| # | Aspect | Script (TradingView) | Verified Pine ID | YouTube tutorial | Scalping TF | Swing TF | Core quantitative output |
|---|--------|----------------------|------------------|------------------|-------------|----------|--------------------------|
| 1 | **Volume Profile** | [Gold VP – KIMOOO1987](https://www.tradingview.com/script/rgPgxON8-Gold-VP-Volume-Profile-Signal-Indicator/) | — (protected / source not returned) | [The Easiest Gold Volume Profile Strategy](https://www.youtube.com/watch?v=LsC2IokcYpc) | 5m–15m session profile | 4H–1D anchored to Daily/Weekly | POC, VAH, VAL, node volume |
| 2 | **Liquidity / Order Blocks** | [Advanced Order Block & Liquidity Mapping Tool – obtrading](https://www.tradingview.com/scripts/obtrading/) | — | [LuxAlgo BEST Order Block Indicator](https://www.youtube.com/watch?v=WQIOmV7vZfw) | 1m–15m kill zones | 4H–1D | Bullish/bearish OB zones, invalidation flags |
| 3 | **Market Structure / Breaker Blocks** | [Market Structure (Breakers) \[LuxAlgo\]](https://www.tradingview.com/script/a93b62eea2b0436185047236909edefc/) | `PUB;a93b62eea2b0436185047236909edefc` | [LuxAlgo SMC Breakers Tutorial](https://www.youtube.com/watch?v=RD72vtuyH5A) | 5m–15m | 1H–4H | Breaker levels + break counts |
| 4 | **Fair Value Gaps** | [XAUUSD 1H – FVG Buy/Sell Signals – Gainsv50](https://in.tradingview.com/script/69xiP1p3-XAUUSD-1H-FVG-Buy-Sell-Signals/) | `PUB;8babc273e17b46f580ecfc7144fe4106` | [FVG XAUUSD Strategy](https://www.youtube.com/watch?v=ZaXfvKXWypk) | 1H default | 4H with widened filters | Non-repainting BUY/SELL arrows + ADX filter |
| 5 | **Supply / Demand Zones** | [Supply and Demand Zones – s_b_j](https://www.tradingview.com/script/OczbdRLG-Supply-and-Demand-Zones/) | — (protected) | [Supply & Demand on Gold](https://www.youtube.com/watch?v=i6lr8YZwudY) | 5m–15m | 4H–Daily | Color-coded reaction zones |
| 6 | **Multi-Timeframe Trend / Bias** | [MTF Confluence Dashboard – PineProfits](https://www.tradingview.com/script/eVNb2Qiv-MTF-Confluence-Dashboard-Multi-Timeframe-Trend-Bias/) | `PUB;0c193275106a45e38fb9f83a56ae2d59` | [LuxAlgo All-In-One SMC/Price Action](https://www.youtube.com/watch?v=gKyn7dbHhEo) | 5m/15m/1H rows | 4H/1D/1W rows | Per-TF Up/Down table + average sentiment score |
| 7 | **Volatility / ATR / Stop-Run** | [Liquidity Sweep & ATR Envelope](https://www.tradingview.com/script/GpdOGGxJ-Liquidity-Sweep-ATR-Envelope/) | `PUB;31295372fbd24b4b8cdb63d4366925c4` | [FVG XAUUSD Strategy (mentions liquidity sweeps)](https://www.youtube.com/watch?v=ZaXfvKXWypk) | H1/M15 | H4+ with reduced pivot length | Sweep/reclaim alerts, ATR envelope breach |

## How to run them with `tvcli`

Each script can be loaded generically with `--signals --agent --json`. Scripts marked with a verified Pine ID below have been confirmed to load in the CLI.

```bash
# 3. Market Structure / Breaker Blocks
./tvcli run "PUB;a93b62eea2b0436185047236909edefc" \
  --symbol XAUUSD --tf 1h --bars 50 --signals --agent --json

# 4. Fair Value Gaps — BUY/SELL arrows
./tvcli run "PUB;8babc273e17b46f580ecfc7144fe4106" \
  --symbol XAUUSD --tf 1h --bars 50 --signals --agent --json

# 6. Multi-Timeframe Bias Dashboard
./tvcli run "PUB;0c193275106a45e38fb9f83a56ae2d59" \
  --symbol XAUUSD --tf 1h --bars 50 --signals --agent --json

# 7. Liquidity Sweep & ATR Envelope
./tvcli run "PUB;31295372fbd24b4b8cdb63d4366925c4" \
  --symbol XAUUSD --tf 1h --bars 50 --signals --agent --json
```

For scripts without a public Pine ID in the table, use the TradingView search inside the CLI to find a usable public alternative:

```bash
./tvcli search "order block liquidity XAUUSD" --limit 10
./tvcli search "supply demand zones" --limit 10
./tvcli search "Gold VP volume profile" --limit 10
```

## Existing `vp` skill

The project already has a working Volume Profile skill that uses a numeric script:

```bash
./tvcli vp --symbol XAUUSD --tf 1h --bars 50 --agent --json
./tvcli vp --symbol XAUUSD --tf 1W --bars 52 --preset weekly --agent --json
```

See [`VOLUME_PROFILE_SKILL.md`](VOLUME_PROFILE_SKILL.md) for the full write-up, including how the parser computes POC/VAH/VAL and directional opportunities.

## Notes and caveats

- **Access levels:** some Perplexity-recommended scripts are protected (`access=2`) or private, so `pinefacade` returns an empty source and the CLI cannot load them. Verified IDs in the table are public/open-source scripts that load successfully.
- **XAUUSD weekend gaps:** gold does not trade 24/7; use `BTCUSDT` for tests during closed sessions if needed.
- The Perplexity research is not financial advice; these are tooling suggestions for building a systematic analysis stack.
