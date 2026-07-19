package pipeline

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Sentinel used by TradingView for "no value" plot slots.
const nanSentinel = 1e100

// PlotClass describes the semantic role of a plot column.
type PlotClass string

const (
	ClassNoise    PlotClass = "noise"    // NaN, all-zero, or otherwise unusable
	ClassPrice    PlotClass = "price"    // price levels, bands, support/resistance
	ClassStyle    PlotClass = "style"    // color/line-style integer selectors
	ClassSignal   PlotClass = "signal"   // sparse 0/1 or -1/0/1 event flags
	ClassMetric   PlotClass = "metric"   // continuous non-price indicators (volume, delta, etc.)
	ClassSnapshot PlotClass = "snapshot" // single-period current-state values
)

// Signals is the cleaned, script-agnostic output for trade automation.
type Signals struct {
	Meta           Meta                  `json:"meta"`
	Classifications map[string]PlotClass  `json:"classifications"`
	Last           map[string]any        `json:"last"`
	Series         []map[string]any      `json:"series,omitempty"`
	Events         []Event               `json:"events,omitempty"`
	Levels         []Level               `json:"levels,omitempty"`
	GraphicCounts  map[string]int        `json:"graphicCounts,omitempty"`
	Bias           string                `json:"bias"`
	Confidence     float64               `json:"confidence"`
	Report         *StrategySummary      `json:"strategy,omitempty"`
	Warnings       []string              `json:"warnings,omitempty"`
}

type Meta struct {
	PineID      string `json:"pineId"`
	Symbol      string `json:"symbol,omitempty"`
	Timeframe   string `json:"timeframe"`
	PeriodCount int    `json:"periodCount"`
	Timestamp   int64  `json:"timestamp"`
}

type Event struct {
	Time  int64   `json:"time"`
	Field string  `json:"field"`
	Kind  string  `json:"kind"`  // "buy", "sell", "alert", "state"
	Value float64 `json:"value"`
	Prev  float64 `json:"prev,omitempty"`
}

type Level struct {
	Field string  `json:"field"`
	Kind  string  `json:"kind"` // "support", "resistance", "band", "poc"
	Value float64 `json:"value"`
}

type StrategySummary struct {
	NetProfit    float64 `json:"netProfit,omitempty"`
	WinRate      float64 `json:"winRate,omitempty"`
	TotalTrades  int     `json:"totalTrades"`
	ProfitFactor float64 `json:"profitFactor,omitempty"`
	MaxDrawdown  float64 `json:"maxDrawdown,omitempty"`
}

// Extract turns raw TradingView study output into clean quantitative signals.
// It accepts the parsed periods, the parsed graphic map, and the optional
// strategy report.
func Extract(pineID, symbol, timeframe string, periods []map[string]any, graphic map[string]map[string]any, strategyReport map[string]any) *Signals {
	s := &Signals{
		Meta: Meta{
			PineID:      pineID,
			Symbol:      symbol,
			Timeframe:   timeframe,
			PeriodCount: len(periods),
			Timestamp:   time.Now().UTC().Unix(),
		},
		Classifications: make(map[string]PlotClass),
		Last:            make(map[string]any),
		GraphicCounts:   make(map[string]int),
		Warnings:        []string{},
	}

	if len(periods) == 0 {
		s.Warnings = append(s.Warnings, "no periods received")
		s.Bias = "neutral"
		return s
	}

	fields := fieldNames(periods[0])
	fieldStats := calcStats(periods, fields)

	// Try to detect a dominant price level for BTC/forex context first.
	dominantPrice := dominantPriceLevel(fieldStats)

	// Classify every non-noise field.
	for _, f := range fields {
		st := fieldStats[f]
		if st.allNaN || st.allZero {
			s.Classifications[f] = ClassNoise
			continue
		}
		s.Classifications[f] = classify(st, len(periods), dominantPrice)
	}

	// Re-classify continuous fields that clearly track the dominant price as price levels.
	for _, f := range fields {
		if s.Classifications[f] == ClassNoise {
			continue
		}
		st := fieldStats[f]
		if dominantPrice > 0 && st.isAround(dominantPrice) && st.unique > 5 && st.nonZeroDensity > 0.5 {
			s.Classifications[f] = ClassPrice
		}
	}

	// Default output is decision-snapshot only. The full cleaned series is left
	// unpopulated to keep payloads small; consumers can rebuild it from the
	// classifications if they need bar-by-bar data.

	// Last useful snapshot.
	last := periods[0]
	for _, f := range fields {
		if s.Classifications[f] == ClassNoise {
			continue
		}
		s.Last[f] = cleanFloat(last[f])
	}

	// Detect state-change events from signal plots.
	s.Events = detectEvents(periods, fields, s.Classifications)

	// Extract actionable price levels from plots.
	s.Levels = extractLevels(periods, fields, s.Classifications, dominantPrice)

	// Reduce graphic labels/boxes into signals/levels as well.
	gfxEvents, gfxLevels, gfxCounts := extractGraphicSignals(graphic, s.Classifications)
	s.Events = append(s.Events, gfxEvents...)
	s.Levels = append(s.Levels, gfxLevels...)
	for k, v := range gfxCounts {
		s.GraphicCounts[k] = v
	}

	// Keep payloads small: newest events, largest absolute levels.
	s.Events = capEvents(s.Events, 30)
	s.Levels = capLevels(s.Levels, 10)

	// Aggregate directional bias.
	s.Bias, s.Confidence = computeBias(s.Last, s.Events, s.Classifications)

	// Strategy report.
	s.Report = extractReport(strategyReport)

	if len(s.Events) == 0 && len(s.Levels) == 0 {
		s.Warnings = append(s.Warnings, "no clean signals/levels extracted; indicator may be graphics-only or noise-heavy")
	}

	return s
}

// JSON returns compact JSON.
func (s *Signals) JSON() ([]byte, error) {
	return json.MarshalIndent(s, "", "  ")
}

// Compact returns a one-line summary for terminal output.
func (s *Signals) Compact() string {
	var parts []string
	parts = append(parts, "pineId="+s.Meta.PineID)
	parts = append(parts, s.Meta.Timeframe+" bars="+strconv.Itoa(s.Meta.PeriodCount))
	parts = append(parts, "bias="+s.Bias+" confidence="+strconv.FormatFloat(s.Confidence, 'f', 2, 64))
	if s.Report != nil {
		parts = append(parts, "trades="+strconv.Itoa(s.Report.TotalTrades)+" win="+strconv.FormatFloat(s.Report.WinRate*100, 'f', 1, 64)+"%")
	}
	if len(s.Levels) > 0 {
		lvlParts := []string{}
		for _, l := range s.Levels[:minInt(3, len(s.Levels))] {
			lvlParts = append(lvlParts, l.Field+"="+strconv.FormatFloat(l.Value, 'f', 2, 64))
		}
		parts = append(parts, "levels=["+strings.Join(lvlParts, ", ")+"]")
	}
	if len(s.Events) > 0 {
		parts = append(parts, "events="+strconv.Itoa(len(s.Events)))
	}
	if len(s.Warnings) > 0 {
		parts = append(parts, "warnings="+strings.Join(s.Warnings, "; "))
	}
	return strings.Join(parts, " | ")
}

type stats struct {
	count          int
	allNaN         bool
	allZero        bool
	unique         int
	min            float64
	max            float64
	mean           float64
	stddev         float64
	nonZeroDensity float64
	integerRatio   float64 // fraction of values that are whole numbers
	isBoolLike     bool
}

func calcStats(periods []map[string]any, fields []string) map[string]stats {
	out := make(map[string]stats, len(fields))
	for _, f := range fields {
		var vals []float64
		nanCount, zeroCount := 0, 0
		for _, p := range periods {
			v, ok := toFloat(p[f])
			if !ok || math.IsNaN(v) {
				nanCount++
				continue
			}
			if math.Abs(v) > 1e90 { // NaN sentinel and cousins
				nanCount++
				continue
			}
			if v == 0 {
				zeroCount++
			}
			vals = append(vals, v)
		}
		n := len(vals)
		if n == 0 {
			out[f] = stats{count: len(periods), allNaN: true}
			continue
		}
	
		uniqSet := map[float64]struct{}{}
		min, max := vals[0], vals[0]
		sum := 0.0
		intCount := 0
		for _, v := range vals {
			uniqSet[round6(v)] = struct{}{}
			if v < min { min = v }
			if v > max { max = v }
			sum += v
			if math.Abs(v-math.Round(v)) < 1e-6 {
				intCount++
			}
		}
		mean := sum / float64(n)
		var sq float64
		for _, v := range vals {
			d := v - mean
			sq += d * d
		}
		stddev := math.Sqrt(sq / float64(n))
	
		out[f] = stats{
			count:          len(periods),
			allNaN:         nanCount == len(periods),
			allZero:        n > 0 && zeroCount == len(periods),
			unique:         len(uniqSet),
			min:            min,
			max:            max,
			mean:           mean,
			stddev:         stddev,
			nonZeroDensity: float64(n-zeroCount) / float64(len(periods)),
			integerRatio:   float64(intCount) / float64(n),
			isBoolLike:     isBoolLike(vals),
		}
	}
	return out
}

func classify(st stats, n int, dominantPrice float64) PlotClass {
	// Boolean flags: {-1,0,1} values are signals regardless of density.
	if st.isBoolLike {
		return ClassSignal
	}

	// Constant large value → likely a fixed price level/band reference.
	// Values above 1e6 are almost always packed ARGB color constants, not prices.
	if st.unique == 1 && st.mean > 2000 && st.mean < 1e6 {
		return ClassPrice
	}

	// Small integer stable palette → style/color selector.
	if st.unique >= 2 && st.unique <= 8 && st.integerRatio > 0.90 && st.nonZeroDensity > 0.5 {
		return ClassStyle
	}

	// Single-bar snapshot indicators: many fields, one period.
	if n == 1 {
		return ClassSnapshot
	}

	// Continuous values with large dynamic range, not bool-like, and reasonably near price scale.
	rng := st.max - st.min
	if rng > 0 && st.unique > 10 && st.stddev/rng < 0.4 {
		if dominantPrice > 0 && st.isAround(dominantPrice) {
			return ClassPrice
		}
		// Without a dominant reference, accept only if values are in a plausible absolute price range.
		if st.min > 2000 {
			return ClassPrice
		}
	}

	return ClassMetric
}

func dominantPriceLevel(fieldStats map[string]stats) float64 {
	// Use stable continuous fields with high absolute mean and low volatility
	// relative to that mean — typical of price series or slow-moving bands.
	var candidates []float64
	for _, st := range fieldStats {
		if st.mean > 100 && st.stddev/st.mean < 0.06 && st.unique > 20 {
			candidates = append(candidates, st.mean)
		}
	}
	if len(candidates) == 0 {
		return 0
	}
	sort.Float64s(candidates)
	return candidates[len(candidates)/2]
}

func (st stats) isAround(level float64) bool {
	if level == 0 {
		return false
	}
	return math.Abs(st.mean-level)/level < 0.15
}

func detectEvents(periods []map[string]any, fields []string, classes map[string]PlotClass) []Event {
	var events []Event
	sortPeriods(periods)
	for _, f := range fields {
		if classes[f] != ClassSignal {
			continue
		}
		for i := 1; i < len(periods); i++ {
			cur, okCur := toFloat(periods[i][f])
			prev, okPrev := toFloat(periods[i-1][f])
			// Ignore transitions from/to NaN sentinels.
			if !okCur || math.Abs(cur) > 1e90 || !okPrev || math.Abs(prev) > 1e90 {
				continue
			}
			if prev == 0 && cur != 0 {
				t, _ := toFloat(periods[i]["$time"])
				events = append(events, Event{
					Time:  int64(t),
					Field: f,
					Kind:  signalKind(cur),
					Value: cur,
					Prev:  prev,
				})
			}
		}
	}
	// Sort descending by time (newest first).
	sort.Slice(events, func(i, j int) bool { return events[i].Time > events[j].Time })
	return events
}

func signalKind(v float64) string {
	if v > 0 {
		return "buy"
	}
	if v < 0 {
		return "sell"
	}
	return "alert"
}

func extractLevels(periods []map[string]any, fields []string, classes map[string]PlotClass, dominant float64) []Level {
	var levels []Level
	for _, f := range fields {
		if classes[f] != ClassPrice && classes[f] != ClassSnapshot {
			continue
		}
		st := calcSingleField(periods, f)
		if st.count == 0 {
			continue
		}

		val := st.last

		// Skip snapshot color/ARGB constants (very large integer-ish values) from levels.
		if classes[f] == ClassSnapshot && val > 1e6 && math.Abs(val-math.Round(val)) < 1e-6 {
			continue
		}

		kind := "band"
		if classes[f] == ClassSnapshot && len(periods) == 1 {
			kind = "snapshot"
		} else if dominant > 0 {
			if val > dominant*1.005 {
				kind = "resistance"
			} else if val < dominant*0.995 {
				kind = "support"
			}
		}

		// Avoid duplicating identical price/snapshot entries for the same field.
		levels = append(levels, Level{Field: f, Kind: kind, Value: val})
	}
	sort.Slice(levels, func(i, j int) bool { return math.Abs(levels[i].Value) > math.Abs(levels[j].Value) })
	return levels
}

type singleStats struct {
	count int
	last  float64
	mean  float64
}

func calcSingleField(periods []map[string]any, f string) singleStats {
	var vals []float64
	for _, p := range periods {
		v, ok := toFloat(p[f])
		if !ok || math.Abs(v) > 1e90 {
			continue
		}
		vals = append(vals, v)
	}
	if len(vals) == 0 {
		return singleStats{}
	}
	sum := 0.0
	for _, v := range vals {
		sum += v
	}
	return singleStats{count: len(vals), last: vals[len(vals)-1], mean: sum / float64(len(vals))}
}

func computeBias(last map[string]any, events []Event, classes map[string]PlotClass) (string, float64) {
	buy, sell := 0, 0
	for _, e := range events {
		switch e.Kind {
		case "buy":
			buy++
		case "sell":
			sell++
		}
	}
	// Also inspect signal last values: positive = buy, negative = sell.
	for f, c := range classes {
		if c != ClassSignal {
			continue
		}
		v, ok := toFloat(last[f])
		if !ok {
			continue
		}
		if v > 0 {
			buy++
		} else if v < 0 {
			sell++
		}
	}
	total := buy + sell
	if total == 0 {
		return "neutral", 0.5
	}
	conf := math.Max(float64(buy), float64(sell)) / float64(total)
	if buy > sell {
		return "long", conf
	}
	if sell > buy {
		return "short", conf
	}
	return "neutral", conf
}

// extractGraphicSignals reduces TradingView graphic objects into events/levels.
// Handles drawing labels (dwglabels) with text like "BUY", "SELL", "BOS", etc.
// It returns events, levels, and a counter map so the caller can summarize
// repetitive labels instead of emitting one event per bar.
func extractGraphicSignals(graphic map[string]map[string]any, classes map[string]PlotClass) (events []Event, levels []Level, counts map[string]int) {
	counts = make(map[string]int)
	if graphic == nil {
		return nil, nil, counts
	}

	// --- Drawing labels: {t:TEXT, x:time_index, y:price, yl:price_unit} ---
	if labels, ok := graphic["dwglabels"]; ok {
		for _, item := range labels {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			text, _ := m["t"].(string)
			if text == "" {
				continue
			}
			y, yOk := toFloat(m["y"])
			if !yOk {
				continue
			}
			x, xOk := toFloat(m["x"])
			if !xOk {
				x = 0
			}

			kind := "alert"
			upper := strings.ToUpper(text)
			switch {
			case strings.Contains(upper, "BUY") || strings.Contains(upper, "LONG"):
				kind = "buy"
			case strings.Contains(upper, "SELL") || strings.Contains(upper, "SHORT"):
				kind = "sell"
			}

			counts[kind]++
			if len(events) < 20 {
				events = append(events, Event{
					Time:  int64(x),
					Field: "dwglabels",
					Kind:  kind,
					Value: y,
				})
			}

			// Structural level labels (supports/resistances, POC, etc.)
			if strings.Contains(upper, "SUPPORT") || strings.Contains(upper, "RESISTANCE") || strings.Contains(upper, "POC") || strings.Contains(upper, "LEVEL") {
				lvlKind := "band"
				if strings.Contains(upper, "SUPPORT") {
					lvlKind = "support"
				} else if strings.Contains(upper, "RESISTANCE") {
					lvlKind = "resistance"
				}
				levels = append(levels, Level{
					Field: "dwglabels_" + text,
					Kind:  lvlKind,
					Value: y,
				})
			}
		}
	}

	// --- Labels (standard TradingView label graphics) ---
	for drawType, items := range graphic {
		if drawType == "dwglabels" {
			continue // already handled above
		}

		switch drawType {
		case "label":
			counts["label"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				text, _ := m["text"].(string)
				if text == "" {
					if t, ok := m["t"].(string); ok {
						text = t
					}
				}
				if text == "" {
					continue
				}
				y, yOk := toFloat(m["y"])
				if !yOk {
					continue
				}
				x, _ := toFloat(m["x"])

				upper := strings.ToUpper(text)
				kind := "alert"
				switch {
				case strings.Contains(upper, "BUY") || strings.Contains(upper, "LONG") || strings.Contains(upper, "BULL"):
					kind = "buy"
				case strings.Contains(upper, "SELL") || strings.Contains(upper, "SHORT") || strings.Contains(upper, "BEAR"):
					kind = "sell"
				}

				if len(events) < 20 {
					events = append(events, Event{
						Time:  int64(x),
						Field: "label",
						Kind:  kind,
						Value: y,
					})
				}
			}

		case "line":
			counts["line"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				// Lines have coords: [{x, y}, {x, y}] — extract price levels
				coords, ok := m["coords"].([]any)
				if !ok || len(coords) < 2 {
					continue
				}
				p1, ok1 := coords[0].(map[string]any)
				p2, ok2 := coords[1].(map[string]any)
				if !ok1 || !ok2 {
					continue
				}
				y1, y1Ok := toFloat(p1["y"])
				y2, y2Ok := toFloat(p2["y"])
				if !y1Ok || !y2Ok {
					continue
				}

				// Nearly horizontal lines → S/R levels
				if y1 > 2000 || y2 > 2000 { // price-scale values
					avgPrice := (y1 + y2) / 2
					slope := math.Abs(y2-y1) / math.Max(math.Abs(y1), 1)
					if slope < 0.001 { // nearly flat
						levels = append(levels, Level{
							Field: "graphic_line",
							Kind:  "band",
							Value: avgPrice,
						})
					}
				}
			}

		case "box":
			counts["box"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				coords, ok := m["coords"].([]any)
				if !ok || len(coords) < 2 {
					continue
				}
				p1, ok1 := coords[0].(map[string]any)
				p2, ok2 := coords[1].(map[string]any)
				if !ok1 || !ok2 {
					continue
				}
				y1, y1Ok := toFloat(p1["y"])
				y2, y2Ok := toFloat(p2["y"])
				if !y1Ok || !y2Ok {
					continue
				}
				// Box boundaries as price levels
				if y1 > 2000 || y2 > 2000 {
					high := math.Max(y1, y2)
					low := math.Min(y1, y2)
					levels = append(levels, Level{
						Field: "graphic_box_top",
						Kind:  "resistance",
						Value: high,
					})
					levels = append(levels, Level{
						Field: "graphic_box_bottom",
						Kind:  "support",
						Value: low,
					})
				}
			}

		case "fill":
			counts["fill"] += len(items)

		case "table":
			counts["table"] += len(items)
			// Tables often contain dashboard values — extract text fields
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				// Table cells may have text with formatted values
				if text, ok := m["text"].(string); ok && text != "" {
					// Try to parse numeric values from table text
					if val, err := parseFormattedNumber(text); err == nil {
						events = append(events, Event{
							Field: "table_" + strings.ReplaceAll(text, " ", "_"),
							Kind:  "state",
							Value: val,
						})
					}
				}
			}
		}
	}

	return events, levels, counts
}

// parseFormattedNumber attempts to extract a numeric value from formatted text
// like "72.5%", "$1,234.56", "RSI: 72.5", etc.
func parseFormattedNumber(text string) (float64, error) {
	// Strip common prefixes/suffixes
	cleaned := text
	for _, prefix := range []string{"$", "€", "£", "¥", "RSI:", "EMA:", "SMA:", "MACD:", "ATR:", "Vol:"} {
		cleaned = strings.ReplaceAll(cleaned, prefix, "")
	}
	cleaned = strings.ReplaceAll(cleaned, "%", "")
	cleaned = strings.ReplaceAll(cleaned, ",", "")
	cleaned = strings.TrimSpace(cleaned)

	var val float64
	n, _ := fmt.Sscanf(cleaned, "%f", &val)
	if n == 1 {
		return val, nil
	}
	return 0, fmt.Errorf("not a number: %s", text)
}

func capEvents(events []Event, max int) []Event {
	sort.Slice(events, func(i, j int) bool { return events[i].Time > events[j].Time })
	if len(events) > max {
		return events[:max]
	}
	return events
}

func capLevels(levels []Level, max int) []Level {
	sort.Slice(levels, func(i, j int) bool { return math.Abs(levels[i].Value) > math.Abs(levels[j].Value) })
	if len(levels) > max {
		return levels[:max]
	}
	return levels
}

func extractReport(report map[string]any) *StrategySummary {
	if report == nil {
		return nil
	}
	perf, ok := report["performance"].(map[string]any)
	if !ok {
		return nil
	}
	all, ok := perf["all"].(map[string]any)
	if !ok {
		all = perf
	}
	s := &StrategySummary{}
	s.NetProfit = floatOrZero(all["netProfit"])
	s.WinRate = floatOrZero(all["percentProfitable"])
	s.TotalTrades = int(floatOrZero(all["totalTrades"]))
	s.ProfitFactor = floatOrZero(all["profitFactor"])
	s.MaxDrawdown = floatOrZero(all["maxDrawdown"])
	return s
}

// --- helpers ---

func fieldNames(sample map[string]any) []string {
	var out []string
	for k := range sample {
		if k == "$time" {
			continue
		}
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool {
		// plot_N numeric sort
		n := extractPlotNum(out[i])
		m := extractPlotNum(out[j])
		if n == m {
			return out[i] < out[j]
		}
		return n < m
	})
	return out
}

func extractPlotNum(s string) int {
	if strings.HasPrefix(s, "plot_") {
		if n, err := strconv.Atoi(strings.TrimPrefix(s, "plot_")); err == nil {
			return n
		}
	}
	return 9999
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case json.Number:
		f, err := x.Float64()
		return f, err == nil
	}
	return 0, false
}

func cleanFloat(v any) any {
	f, ok := toFloat(v)
	if !ok {
		return v
	}
	if math.Abs(f) > 1e90 || math.IsNaN(f) {
		return nil
	}
	return f
}

func isBoolLike(vals []float64) bool {
	for _, v := range vals {
		if v != 0 && v != 1 && v != -1 {
			return false
		}
	}
	return len(vals) > 0
}

func round6(v float64) float64 {
	return math.Round(v*1e6) / 1e6
}

func sortPeriods(periods []map[string]any) {
	sort.Slice(periods, func(i, j int) bool {
		vi, _ := toFloat(periods[i]["$time"])
		vj, _ := toFloat(periods[j]["$time"])
		return vi < vj
	})
}

func floatOrZero(v any) float64 {
	f, _ := toFloat(v)
	return f
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
