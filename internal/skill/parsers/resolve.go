package parsers

import (
	"strconv"
	"strings"

	"github.com/ch99q/tvcli/pkg/pipeline"
	"github.com/ch99q/tvcli/pkg/schema"
)

// ResolveGraphicDashboard reconstructs any dwgtable present in the graphic
// layer into a structured, row-major form. It is the script-agnostic path for
// "dashboard" indicators that emit a table instead of period/plot data. The
// returned map has:
//
//	"grids":  []map[string]any  — one per table: {cols, rows, cells}
//	"tables": int               — number of tables found
//
// ok is false when the graphic layer has no table draw types.
func ResolveGraphicDashboard(graphic map[string]map[string]any) (map[string]any, bool) {
	grids := pipeline.ReconstructTables(graphic)
	if len(grids) == 0 {
		return nil, false
	}
	converted := make([]map[string]any, 0, len(grids))
	for _, g := range grids {
		converted = append(converted, map[string]any{
			"id":    g.ID,
			"cols":  g.Cols,
			"rows":  g.Rows,
			"cells": g.Cells,
		})
	}
	return map[string]any{"grids": converted, "tables": len(grids)}, true
}

// GraphicLabels extracts every dwglabel as {text, price} keyed by label id.
// Some graphics-only scripts anchor text labels to a price (y/yl == "pr");
// when present, the price is the most meaningful numeric signal on the chart.
func GraphicLabels(graphic map[string]map[string]any) []map[string]any {
	labels, ok := graphic["dwglabels"]
	if !ok || len(labels) == 0 {
		return nil
	}
	var out []map[string]any
	for _, v := range labels {
		m, ok := v.(map[string]any)
		if !ok {
			continue
		}
		text, _ := m["t"].(string)
		out = append(out, map[string]any{
			"id":     m["id"],
			"text":   text,
			"price":  toFloat(m["y"]),
			"color":  m["tc"],
		})
	}
	return out
}

// ResolveAny picks the most meaningful part of a run response in priority
// order, so a graphics-only script is parsed without per-skill hand-coding:
//
//  1. periods present  → returns ok=false; the caller should use its schema
//     parser (this helper only resolves the graphic layer).
//  2. dwgtable present → table dashboard (ResolveGraphicDashboard).
//  3. dwglabels present → label list (GraphicLabels).
//
// It returns (data, kind, ok). kind is "table" | "labels" | "" so callers can
// branch on what was actually returned.
func ResolveAny(graphic map[string]map[string]any) (any, string, bool) {
	if dash, ok := ResolveGraphicDashboard(graphic); ok {
		return dash, "table", true
	}
	if labels := GraphicLabels(graphic); len(labels) > 0 {
		return labels, "labels", true
	}
	return nil, "", false
}

// SchemaField resolves a semantic field value from a period using the script
// schema when available, and falls back to direct period-key lookup.
//
// Candidates are tried in order:
//  1. If sch is non-nil, each name is matched (case-insensitive) against a
//     plot's human-readable Name; the matching plot's period key
//     ("plot_<Index>") is then read. This makes parsers resilient to
//     TradingView renumbering plots (plot_0 -> plot_3) as long as the plot
//     title is stable — the core fragility the hand-coded alias lists had.
//  2. Direct period-key lookup (getField) for literal keys like "Close",
//     "plot_0", "in_3".
//
// Returns nil if no candidate resolves to a non-nil value. With a nil schema
// this is exactly equivalent to getField, so parsers can adopt it unconditionally.
func SchemaField(period map[string]any, sch *schema.PineSchema, names ...string) any {
	if period == nil {
		return nil
	}
	if sch != nil {
		for _, name := range names {
			if key, ok := plotKeyForTitle(sch, name); ok {
				if v, ok := period[key]; ok && v != nil {
					return v
				}
			}
		}
	}
	return getField(period, names)
}

// SchemaFloat is SchemaField converted to float64 (0 if absent/nil).
func SchemaFloat(period map[string]any, sch *schema.PineSchema, names ...string) float64 {
	return toFloat(SchemaField(period, sch, names...))
}

// plotKeyForTitle returns the period key ("plot_<Index>") for the plot whose
// Name matches title (case-insensitive). ok is false when no match — callers
// should then try a literal-key fallback.
func plotKeyForTitle(sch *schema.PineSchema, title string) (string, bool) {
	want := strings.ToLower(strings.TrimSpace(title))
	if want == "" {
		return "", false
	}
	for _, p := range sch.Plots {
		if strings.ToLower(strings.TrimSpace(p.Name)) == want {
			return plotKey(p.Index), true
		}
	}
	return "", false
}

func plotKey(index int) string {
	return "plot_" + strconv.Itoa(index)
}
