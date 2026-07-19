// extract_schema.go — schema-guided extraction layer.
// ExtractWithSchema uses PineSchema ground-truth types instead of
// statistical inference when available, falling back to Extract() for any
// field the schema doesn't cover.
package pipeline

import (
	"math"
	"strings"

	"github.com/ch99q/tvcli/pkg/schema"
)

// ExtractWithSchema is the schema-guided version of Extract.
// When sch is non-nil, plot classifications come from metaInfo declarations
// rather than statistical heuristics. Falls back to Extract() for any field
// the schema doesn't cover.
func ExtractWithSchema(pineID, symbol, timeframe string, parsed *ParseResult, graphic map[string]map[string]any, strategyReport map[string]any) *Signals {
	sch := parsed.Schema

	s := &Signals{
		Meta: Meta{
			PineID:      pineID,
			Symbol:      symbol,
			Timeframe:   timeframe,
			PeriodCount: len(parsed.Bars),
			Timestamp:   0, // set by caller if needed
		},
		Classifications: make(map[string]PlotClass),
		Last:            make(map[string]any),
		GraphicCounts:   make(map[string]int),
		Warnings:        []string{},
	}

	if len(parsed.Bars) == 0 {
		s.Warnings = append(s.Warnings, "no bars to extract")
		s.Bias = "neutral"
		return s
	}

	// Build field stats from typed bars
	fields := uniqueStrings(parsed.FieldNames)
	fieldStats := calcStatsFromTyped(parsed.Bars, fields)

	// Classify using schema ground-truth first, then fall back to stats.
	for _, f := range fields {
		st := fieldStats[f]
		if st.allNaN || st.allZero {
			s.Classifications[f] = ClassNoise
			continue
		}

		// Schema ground-truth classification
		if sch != nil {
			if decl, ok := sch.PlotByName[f]; ok {
				s.Classifications[f] = classifyFromSchema(decl, sch, st)
				continue
			}
		}

		// Fallback to statistical classification
		s.Classifications[f] = classify(st, len(parsed.Bars), dominantPriceLevel(fieldStats))
	}

	// Extract last bar snapshot
	lastBar := parsed.LastValues()
	for _, f := range fields {
		if s.Classifications[f] == ClassNoise {
			continue
		}
		if v, ok := lastBar[f]; ok {
			s.Last[f] = v
		}
	}

	// Build a cleaned recent series (chronological, capped to keep payloads reasonable).
	const maxSeriesBars = 50
	s.Series = buildTypedSeries(parsed.Bars, fields, s.Classifications, maxSeriesBars)

	// Detect events from signal plots
	s.Events = detectEventsFromTyped(parsed.Bars, fields, s.Classifications)

	// Extract price levels
	dominant := dominantPriceLevel(fieldStats)
	s.Levels = extractLevelsFromTyped(parsed.Bars, fields, s.Classifications, dominant)

	// Graphics
	gfxEvents, gfxLevels, gfxCounts := extractGraphicSignals(graphic, s.Classifications)
	s.Events = append(s.Events, gfxEvents...)
	s.Levels = append(s.Levels, gfxLevels...)
	for k, v := range gfxCounts {
		s.GraphicCounts[k] = v
	}

	// Cap
	s.Events = capEvents(s.Events, 30)
	s.Levels = capLevels(s.Levels, 10)

	// Bias
	s.Bias, s.Confidence = computeBias(s.Last, s.Events, s.Classifications)

	// Strategy report
	s.Report = extractReport(strategyReport)

	if len(s.Events) == 0 && len(s.Levels) == 0 {
		s.Warnings = append(s.Warnings, "no clean signals/levels extracted; indicator may be graphics-only or noise-heavy")
	}

	return s
}

// classifyFromSchema uses the schema declaration to determine the PlotClass.
func classifyFromSchema(decl schema.PlotDecl, sch *schema.PineSchema, st stats) PlotClass {
	pt := strings.ToLower(decl.PlotType)

	// Colorer → style
	if decl.IsColorer {
		return ClassStyle
	}

	switch pt {
	case "cross":
		// Zero-line / reference line — noise for signals
		return ClassNoise
	case "histogram", "columns":
		// Histogram values: check if signal-like or metric
		if st.isBoolLike {
			return ClassSignal
		}
		return ClassMetric
	case "area", "area_br":
		return ClassPrice
	case "circles", "marker":
		return ClassSignal
	case "line", "step_line", "":
		// Line plots need value analysis
		if st.isBoolLike {
			return ClassSignal
		}
		if st.unique == 1 && st.mean > 2000 && st.mean < 1e6 {
			return ClassPrice
		}
		if st.unique >= 2 && st.unique <= 8 && st.integerRatio > 0.90 && st.nonZeroDensity > 0.5 {
			return ClassStyle
		}
		if dominantPrice := dominantPriceLevel(nil); dominantPrice > 0 && st.isAround(dominantPrice) {
			return ClassPrice
		}
		if st.min > 2000 {
			return ClassPrice
		}
		return ClassMetric
	default:
		return ClassMetric
	}
}

// calcStatsFromTyped computes stats from TypedBar slices.
func calcStatsFromTyped(bars []TypedBar, fields []string) map[string]stats {
	out := make(map[string]stats, len(fields))
	for _, f := range fields {
		var vals []float64
		nanCount, zeroCount := 0, 0
		for _, bar := range bars {
			v, found := findValue(bar, f)
			if !found {
				nanCount++
				continue
			}
			if math.IsNaN(v) || math.Abs(v) > 1e90 {
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
			out[f] = stats{count: len(bars), allNaN: true}
			continue
		}

		uniqSet := map[float64]struct{}{}
		min, max := vals[0], vals[0]
		sum := 0.0
		intCount := 0
		for _, v := range vals {
			uniqSet[round6(v)] = struct{}{}
			if v < min {
				min = v
			}
			if v > max {
				max = v
			}
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
			count:          len(bars),
			allNaN:         nanCount == len(bars),
			allZero:        n > 0 && zeroCount == len(bars),
			unique:         len(uniqSet),
			min:            min,
			max:            max,
			mean:           mean,
			stddev:         stddev,
			nonZeroDensity: float64(n-zeroCount) / float64(len(bars)),
			integerRatio:   float64(intCount) / float64(n),
			isBoolLike:     isBoolLike(vals),
		}
	}
	return out
}

func findValue(bar TypedBar, name string) (float64, bool) {
	for _, tv := range bar.Values {
		if tv.Name == name {
			if tv.IsNull {
				return 0, false
			}
			return tv.Value, true
		}
	}
	return 0, false
}

// detectEventsFromTyped detects state-change events from typed signal bars.
func detectEventsFromTyped(bars []TypedBar, fields []string, classes map[string]PlotClass) []Event {
	var events []Event
	// Sort bars oldest-first for event detection
	sorted := make([]TypedBar, len(bars))
	copy(sorted, bars)
	for i := 0; i < len(sorted)/2; i++ {
		j := len(sorted) - 1 - i
		sorted[i], sorted[j] = sorted[j], sorted[i]
	}

	for _, f := range fields {
		if classes[f] != ClassSignal {
			continue
		}
		var prevVal float64
		prevSet := false
		for _, bar := range sorted {
			v, found := findValue(bar, f)
			if !found {
				continue
			}
			if prevSet && prevVal == 0 && v != 0 {
				events = append(events, Event{
					Time:  int64(bar.Time),
					Field: f,
					Kind:  signalKind(v),
					Value: v,
					Prev:  prevVal,
				})
			}
			prevVal = v
			prevSet = true
		}
	}
	// Sort descending by time
	for i := 0; i < len(events)/2; i++ {
		j := len(events) - 1 - i
		events[i], events[j] = events[j], events[i]
	}
	return events
}

// extractLevelsFromTyped extracts price levels from typed bars.
func extractLevelsFromTyped(bars []TypedBar, fields []string, classes map[string]PlotClass, dominant float64) []Level {
	var levels []Level
	for _, f := range fields {
		if classes[f] != ClassPrice && classes[f] != ClassSnapshot {
			continue
		}
		// Get last non-null value
		var lastVal float64
		var found bool
		for _, bar := range bars {
			v, ok := findValue(bar, f)
			if ok {
				lastVal = v
				found = true
			}
		}
		if !found {
			continue
		}

		kind := "band"
		if dominant > 0 {
			if lastVal > dominant*1.005 {
				kind = "resistance"
			} else if lastVal < dominant*0.995 {
				kind = "support"
			}
		}

		levels = append(levels, Level{Field: f, Kind: kind, Value: lastVal})
	}
	// Sort by absolute value descending
	for i := 0; i < len(levels)/2; i++ {
		j := len(levels) - 1 - i
		if math.Abs(levels[i].Value) < math.Abs(levels[j].Value) {
			levels[i], levels[j] = levels[j], levels[i]
		}
	}
	return levels
}

// uniqueStrings returns the first occurrence of each string, preserving order.
func uniqueStrings(in []string) []string {
	seen := make(map[string]bool, len(in))
	out := make([]string, 0, len(in))
	for _, s := range in {
		if seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// buildTypedSeries turns the parsed bars into a chronological series of maps,
// keeping only non-noise fields. The result is oldest-first so consumers can
// read left-to-right.
func buildTypedSeries(bars []TypedBar, fields []string, classes map[string]PlotClass, maxBars int) []map[string]any {
	n := len(bars)
	if n == 0 {
		return nil
	}
	start := 0
	if n > maxBars {
		start = n - maxBars
	}
	series := make([]map[string]any, 0, n-start)
	for i := n - 1; i >= start; i-- {
		bar := bars[i]
		m := map[string]any{"time": bar.Time}
		for _, f := range fields {
			if classes[f] == ClassNoise {
				continue
			}
			if v, ok := findValue(bar, f); ok {
				m[f] = v
			}
		}
		series = append(series, m)
	}
	return series
}
