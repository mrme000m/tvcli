package parsers

import (
	"strconv"
	"strings"

	"github.com/ch99q/tvcli/pkg/schema"
)

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
