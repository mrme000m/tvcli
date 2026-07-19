package parsers

import "math"

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

// barTime returns the $time field of a period as a float64 (unix seconds).
func barTime(p map[string]any) float64 {
	return toFloat(getField(p, []string{"$time", "time", "timestamp"}))
}

// isInProgress returns true if periods[0] looks like the in-progress bar.
// Heuristic: it is in-progress when a critical volume/price field is zero
// where periods[1] has a non-zero value. Pass the field name candidates
// (same form as getField). If periods has fewer than 2 entries, returns
// false (nothing to compare against).
func isInProgress(periods []map[string]any, criticalFields ...string) bool {
	if len(periods) < 2 {
		return false
	}
	cur := periods[0]
	prev := periods[1]
	for _, name := range criticalFields {
		cv := toFloat(cur[name])
		pv := toFloat(prev[name])
		if cv == 0 && pv != 0 {
			return true
		}
	}
	return false
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

func resolveBarColor(code float64) string {
	switch int(code) {
	case 0:
		return "both_above_mas"
	case 1:
		return "both_below_mas"
	case 2:
		return "mixed"
	case 3:
		return "neutral"
	default:
		return "unknown"
	}
}

func resolveBGColor(code float64) string {
	switch int(code) {
	case 4:
		return "bull"
	case 5:
		return "bear"
	case 6:
		return "mixed"
	case 7:
		return "neutral"
	default:
		return "unknown"
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
