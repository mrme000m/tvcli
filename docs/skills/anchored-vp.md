# Anchored VP — Anchored Clusters Volume Profile

**Pine ID:** `PUB;92974e0a3cfb481eaf058cdab9f925a3`
**Workflow:** `anchored-clusters-vp`
**Category:** Volume Profile

**Status:** Graphics-only script — no period data. Reports `no_data`. Errors on BTCUSD (TradingView-side).

## Description

K-means cluster-based anchored volume profile. Groups volume into clusters and identifies POC levels. Outputs graphics (boxes, labels) rather than period data, making it unsuitable for CLI extraction.

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `kInput` | `in_3` | int | 5 | **K-means clusters** |
| `iters` | `in_4` | int | 50 | K-means iterations |
| `rowsInput` | `in_5` | int | 20 | Profile resolution |
| `vpWidth` | `in_6` | int | 40 | Display width |
| `showDots` | `in_8` | bool | true | Show cluster dots |
