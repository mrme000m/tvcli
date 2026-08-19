// Package pipeline turns raw study output (periods, graphics, strategy
// reports) into structured trading intelligence: signal events, order blocks,
// levels, and narratives. The extractor is script-agnostic — it works from
// TradingView's generic drawing types (boxes, lines, labels, tables,
// histograms) with no per-script code.
//
// Two entry points:
//
//	pipeline.Extract(...)             — flat extraction from raw periods/graphics
//	pipeline.ExtractWithSchema(...)   — schema-guided extraction (see pkg/schema)
//
// pkg/runner.ParseOutput combines both, and pkg/skill parses register
// per-script refinements on top.
package pipeline