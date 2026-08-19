// Package dynparse dynamically transforms raw TradingView study output into
// semantically-named, typed data using a PineSchema compiled from metaInfo.
package pipeline

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/mrme000m/tvcli/pkg/schema"
)

// nanSentinel and toFloat are defined in extract.go (the extract version of
// toFloat is a superset — it also handles json.Number).

// TypedValue holds a cleaned value with its semantic metadata.
type TypedValue struct {
	Name     string  // semantic name from schema (e.g. "momentum", "upperBB")
	PlotType string  // histogram, line, area, cross, etc.
	Category string  // signal, price, metric, histogram, reference, colorer
	Value    float64 // cleaned numeric value (NaN sentinel → 0, isNull=true)
	IsNull   bool    // true when value was NaN sentinel or missing
}

// TypedBar is one bar's worth of named, typed values.
type TypedBar struct {
	Time   float64
	Values []TypedValue
}

// ParseResult is the output of the dynamic parser.
type ParseResult struct {
	Bars       []TypedBar
	FieldNames []string // ordered semantic field names
	Schema     *schema.PineSchema
	Warnings   []string
}

// Parse transforms raw periods (plot_N keyed) into semantically-named typed bars.
// When schema is nil, falls back to positional naming with statistical inference.
func Parse(periods []map[string]any, sch *schema.PineSchema) *ParseResult {
	r := &ParseResult{Schema: sch}

	if len(periods) == 0 {
		r.Warnings = append(r.Warnings, "no periods to parse")
		return r
	}

	if sch == nil || len(sch.Plots) == 0 {
		return parseFallback(periods, r)
	}

	// Build index map: plot_N index → PlotDecl
	plotMap := make(map[int]schema.PlotDecl)
	for _, p := range sch.Plots {
		plotMap[p.Index] = p
	}

	// Collect all field names from schema
	fieldNames := make([]string, 0, len(sch.Plots))
	for _, p := range sch.Plots {
		fieldNames = append(fieldNames, p.Name)
	}
	r.FieldNames = fieldNames

	// Parse each bar
	for _, period := range periods {
		bar := parseBarWithSchema(period, plotMap, sch)
		r.Bars = append(r.Bars, bar)
	}

	return r
}

func parseBarWithSchema(period map[string]any, plotMap map[int]schema.PlotDecl, sch *schema.PineSchema) TypedBar {
	bar := TypedBar{}

	// Extract time
	if t, ok := period["$time"].(float64); ok {
		bar.Time = t
	}

	// Find max plot index to iterate
	maxIdx := 0
	for k := range period {
		if strings.HasPrefix(k, "plot_") {
			if n, err := strconv.Atoi(strings.TrimPrefix(k, "plot_")); err == nil && n > maxIdx {
				maxIdx = n
			}
		}
	}

	// Process each plot position
	for i := 0; i <= maxIdx; i++ {
		key := fmt.Sprintf("plot_%d", i)
		rawVal, ok := period[key]
		if !ok {
			continue
		}

		v, isNum := toFloat(rawVal)
		isNull := !isNum || math.IsNaN(v) || math.Abs(v) > 1e90

		decl, hasDecl := plotMap[i]
		if !hasDecl {
			// Unknown plot — use positional name
			decl = schema.PlotDecl{
				Index: i,
				Name:  key,
			}
		}

		tv := TypedValue{
			Name:     decl.Name,
			PlotType: decl.PlotType,
			Value:    v,
			IsNull:   isNull,
		}

		// Determine category from schema + value characteristics
		tv.Category = categorizeFromSchema(decl, sch, v, isNull)

		bar.Values = append(bar.Values, tv)
	}

	return bar
}

func categorizeFromSchema(decl schema.PlotDecl, sch *schema.PineSchema, v float64, isNull bool) string {
	if isNull {
		return "noise"
	}

	// Colorer plots are style selectors, not data
	if decl.IsColorer {
		return "colorer"
	}

	pt := strings.ToLower(decl.PlotType)

	// Cross/zero-line references
	if pt == "cross" {
		return "reference"
	}

	// Histogram → typically a signal/momentum indicator
	if pt == "histogram" || pt == "columns" {
		return "histogram"
	}

	// Area/band → price levels or bands
	if pt == "area" || pt == "area_br" {
		return "band"
	}

	// Circles → markers/signals
	if pt == "circles" {
		return "signal"
	}

	// Line type — need value analysis
	if pt == "line" || pt == "step_line" || pt == "" {
		// Check if values are signal-like (sparse 0/1 or -1/0/1)
		if isSignalValue(v) {
			return "signal"
		}
		// Check if values are in price range
		if v > 1000 {
			return "price"
		}
		// Check if values are in oscillator range
		if v >= -100 && v <= 100 {
			return "oscillator"
		}
		return "metric"
	}

	return "metric"
}

func isSignalValue(v float64) bool {
	return v == 0 || v == 1 || v == -1
}

func parseFallback(periods []map[string]any, r *ParseResult) *ParseResult {
	// Discover plot indices from first period
	sample := periods[0]
	var indices []int
	for k := range sample {
		if strings.HasPrefix(k, "plot_") {
			if n, err := strconv.Atoi(strings.TrimPrefix(k, "plot_")); err == nil {
				indices = append(indices, n)
			}
		}
	}
	sort.Ints(indices)

	// Generate field names
	fieldNames := make([]string, len(indices))
	for i, idx := range indices {
		fieldNames[i] = fmt.Sprintf("plot_%d", idx)
	}
	r.FieldNames = fieldNames

	// Parse bars
	for _, period := range periods {
		bar := TypedBar{}
		if t, ok := period["$time"].(float64); ok {
			bar.Time = t
		}
		for _, idx := range indices {
			key := fmt.Sprintf("plot_%d", idx)
			rawVal := period[key]
			v, isNum := toFloat(rawVal)
			isNull := !isNum || math.IsNaN(v) || math.Abs(v) > 1e90

			bar.Values = append(bar.Values, TypedValue{
				Name:     key,
				PlotType: "",
				Category: inferCategoryFallback(v, isNull),
				Value:    v,
				IsNull:   isNull,
			})
		}
		r.Bars = append(r.Bars, bar)
	}

	return r
}

func inferCategoryFallback(v float64, isNull bool) string {
	if isNull {
		return "noise"
	}
	if v == 0 || v == 1 || v == -1 {
		return "signal"
	}
	if v > 1000 {
		return "price"
	}
	if v >= -100 && v <= 100 {
		return "oscillator"
	}
	return "metric"
}

// ToNamedMap converts parsed bars into a map[semanticName][]float64 for
// consumers that expect the old field-based API.
func (r *ParseResult) ToNamedMap() map[string][]float64 {
	out := make(map[string][]float64)
	for _, name := range r.FieldNames {
		out[name] = make([]float64, 0, len(r.Bars))
	}
	for _, bar := range r.Bars {
		for _, tv := range bar.Values {
			if !tv.IsNull {
				out[tv.Name] = append(out[tv.Name], tv.Value)
			}
		}
	}
	return out
}

// LastValues returns the most recent non-null value per field.
func (r *ParseResult) LastValues() map[string]any {
	out := make(map[string]any)
	if len(r.Bars) == 0 {
		return out
	}
	// Bars are newest-first (index 0 = latest)
	bar := r.Bars[0]
	for _, tv := range bar.Values {
		if tv.IsNull {
			out[tv.Name] = nil
		} else {
			out[tv.Name] = tv.Value
		}
	}
	return out
}

// SignalFields returns field names classified as "signal" or "histogram".
func (r *ParseResult) SignalFields() []string {
	var result []string
	for _, name := range r.FieldNames {
		for _, bar := range r.Bars {
			for _, tv := range bar.Values {
				if tv.Name == name && (tv.Category == "signal" || tv.Category == "histogram") {
					result = append(result, name)
					break
				}
			}
		}
	}
	return result
}

// PriceFields returns field names classified as "price" or "band".
func (r *ParseResult) PriceFields() []string {
	var result []string
	seen := make(map[string]bool)
	for _, bar := range r.Bars {
		for _, tv := range bar.Values {
			if (tv.Category == "price" || tv.Category == "band") && !seen[tv.Name] {
				result = append(result, tv.Name)
				seen[tv.Name] = true
			}
		}
	}
	return result
}

// Summary returns a human-readable summary of the parsed schema.
func (r *ParseResult) Summary() string {
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("Parsed %d bars, %d fields\n", len(r.Bars), len(r.FieldNames)))
	if r.Schema != nil {
		sb.WriteString(fmt.Sprintf("Schema: %s (v%s, strategy=%v)\n", r.Schema.PineID, r.Schema.Version, r.Schema.IsStrategy))
	}
	for _, name := range r.FieldNames {
		for _, bar := range r.Bars {
			for _, tv := range bar.Values {
				if tv.Name == name {
					sb.WriteString(fmt.Sprintf("  %-20s type=%-10s cat=%-12s\n", tv.Name, tv.PlotType, tv.Category))
					break
				}
			}
		}
	}
	return sb.String()
}

// helpers

// toFloat is defined in extract.go (superset that also handles json.Number).
