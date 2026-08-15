package parsers

import (
	"math"
	"strconv"
	"strings"
)

// latestClosed returns the most recent CLOSED bar from periods.
//
// periods is sorted newest-first (see ChartStudy.Periods). periods[0] is the
// bar currently forming — its volume / signal fields are partial or zero
// until the bar closes. For analysis we want the most recent CLOSED bar,
// which is periods[1]. We fall back to periods[0] only when there is no
// older bar (e.g. very short history or snapshot at a bar boundary).
//
// Parsers that genuinely want the live/in-progress value (e.g. live EMA,
// live SuperTrend line) should read periods[0] directly and document that.
func latestClosed(periods []map[string]any) map[string]any {
	if len(periods) == 0 {
		return nil
	}
	if len(periods) > 1 {
		return periods[1]
	}
	return periods[0]
}

// historicalBars returns periods with the in-progress bar (periods[0])
// dropped, so aggregations (dominance ratios, cross counts, etc.) are
// computed over closed bars only. Returns periods as-is if there is only
// one bar or none.
func historicalBars(periods []map[string]any) []map[string]any {
	if len(periods) <= 1 {
		return periods
	}
	return periods[1:]
}

func getField(obj map[string]any, names []string) any {
	for _, n := range names {
		if v, ok := obj[n]; ok && v != nil {
			return v
		}
	}
	return nil
}

func toFloat(v any) float64 {
	if v == nil {
		return 0
	}
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case bool:
		if n {
			return 1
		}
		return 0
	default:
		return 0
	}
}

func biasFromDominance(r float64) string {
	if r > 0.2 {
		return "bullish"
	}
	if r < -0.2 {
		return "bearish"
	}
	return "neutral"
}

func confidenceLabel(score float64) string {
	if score >= 0.80 {
		return "STRONG"
	}
	if score >= 0.60 {
		return "HIGH"
	}
	if score >= 0.40 {
		return "MED"
	}
	return "LOW"
}

func round2(f float64) float64 {
	return math.Round(f*100) / 100
}

// parseNumeric parses a numeric string (e.g. a table-cell "6" or "-4") into a
// float64. Returns 0 when the string is empty or not numeric. Used to read
// graphic-table cells, which TradingView sends as text.
func parseNumeric(s string) float64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	if v, err := strconv.ParseFloat(s, 64); err == nil {
		return v
	}
	return 0
}

// latestGraphicPrice extracts the most recent label price from the graphic layer.
// Some scripts do not emit a Close plot; every drawing label anchored to price
// carries its y-coordinate as the price (raw TV data sets yl == "pr").
func latestGraphicPrice(graphic map[string]map[string]any) float64 {
	labels, ok := graphic["dwglabels"]
	if !ok || len(labels) == 0 {
		return 0
	}
	var latestID, price float64
	for _, v := range labels {
		item, ok := v.(map[string]any)
		if !ok {
			continue
		}
		id := toFloat(item["id"])
		if id > latestID {
			latestID = id
			price = toFloat(item["y"])
		}
	}
	return price
}
