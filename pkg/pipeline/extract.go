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
	Meta            Meta                 `json:"meta"`
	Classifications map[string]PlotClass `json:"classifications"`
	Last            map[string]any       `json:"last"`
	Series          []map[string]any     `json:"series,omitempty"`
	Events          []Event              `json:"events,omitempty"`
	Levels          []Level              `json:"levels,omitempty"`
	GraphicCounts   map[string]int       `json:"graphicCounts,omitempty"`
	Bias            string               `json:"bias"`
	Confidence      float64              `json:"confidence"`
	Report          *StrategySummary     `json:"strategy,omitempty"`
	Warnings        []string             `json:"warnings,omitempty"`
}

type Meta struct {
	PineID      string     `json:"pineId"`
	Symbol      string     `json:"symbol,omitempty"`
	Timeframe   string     `json:"timeframe"`
	PeriodCount int        `json:"periodCount"`
	Timestamp   int64      `json:"timestamp"`
	ScriptType  ScriptType `json:"scriptType,omitempty"` // "strategy" | "indicator" (see ScriptType)
}

type Event struct {
	Time  int64   `json:"time"`
	Field string  `json:"field"`
	Kind  string  `json:"kind"` // "buy", "sell", "alert", "state", "text"
	Value float64 `json:"value"`
	Prev  float64 `json:"prev,omitempty"`
	Text  string  `json:"text,omitempty"`
}

type Level struct {
	Field string  `json:"field"`
	Kind  string  `json:"kind"` // "support", "resistance", "band", "poc"
	Value float64 `json:"value"`
}

// Trade is a single trade from a strategy report.
type Trade struct {
	ID     string  `json:"id,omitempty"`
	Side   string  `json:"side"` // "buy" or "sell"
	Entry  float64 `json:"entry,omitempty"`
	Price  float64 `json:"price,omitempty"`
	Qty    float64 `json:"qty,omitempty"`
	Profit float64 `json:"profit,omitempty"`
}

type StrategySummary struct {
	NetProfit        float64 `json:"netProfit,omitempty"`
	NetProfitPercent float64 `json:"netProfitPercent,omitempty"`
	GrossProfit      float64 `json:"grossProfit,omitempty"`
	GrossLoss        float64 `json:"grossLoss,omitempty"`
	WinRate          float64 `json:"winRate,omitempty"`
	TotalTrades      int     `json:"totalTrades"`
	WinningTrades    int     `json:"winningTrades,omitempty"`
	LosingTrades     int     `json:"losingTrades,omitempty"`
	ProfitFactor     float64 `json:"profitFactor,omitempty"`
	MaxDrawdown      float64 `json:"maxDrawdown,omitempty"`
	MaxDDPercent     float64 `json:"maxDrawdownPercent,omitempty"`
	AvgTrade         float64 `json:"avgTrade,omitempty"`
	LargestWin       float64 `json:"largestWin,omitempty"`
	LargestLoss      float64 `json:"largestLoss,omitempty"`
	CommissionPaid   float64 `json:"commissionPaid,omitempty"`
	SharpeRatio      float64 `json:"sharpeRatio,omitempty"`
	SortinoRatio     float64 `json:"sortinoRatio,omitempty"`
	BuyHoldReturn    float64 `json:"buyHoldReturn,omitempty"`
	OpenPL           float64 `json:"openPL,omitempty"`
	Currency         string  `json:"currency,omitempty"`
	Trades           []Trade `json:"trades,omitempty"`
}

// Extract turns raw TradingView study output into clean quantitative signals.
// It accepts the parsed periods, the parsed graphic map, and the optional
// strategy report.
func Extract(pineID, symbol, timeframe string, periods []map[string]any, graphic map[string]map[string]any, strategyReport map[string]any, isStrategy ...bool) *Signals {
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
		// Still try to extract signals from graphic data (labels, boxes, lines, tables)
		gfxEvents, gfxLevels, gfxCounts := extractGraphicSignals(graphic, s.Classifications)
		s.Events = append(s.Events, gfxEvents...)
		s.Levels = append(s.Levels, gfxLevels...)
		for k, v := range gfxCounts {
			s.GraphicCounts[k] = v
		}

		// Enhance graphics-only output: classify graphic fields and extract last values.
		classifyGraphicsOnly(s.Classifications, graphic)
		extractLastFromGraphics(s.Last, graphic)

		// A script with no period data may still be a strategy: its trades/
		// performance arrive in the strategy report (du frames), so resolve
		// the script type from the report before deciding on enrichment.
		s.Meta.ScriptType = resolveScriptType(len(isStrategy) > 0 && isStrategy[0], strategyReport)
		if s.Meta.ScriptType == ScriptTypeStrategy {
			s.Events = append(s.Events, strategyEvents(strategyReport)...)
			if b := strategyBias(strategyReport); b != "" {
				s.Bias = b
			}
		}

		if len(s.Events) == 0 && len(s.Levels) == 0 {
			s.Warnings = append(s.Warnings, "no clean signals/levels extracted; indicator may be graphics-only or noise-heavy")
		} else {
			// Graphics-only scripts with extracted levels/events have moderate confidence.
			s.Confidence = 0.5
		}
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

	// Build a cleaned recent series (chronological, capped to keep payloads reasonable).
	const maxSeriesBars = 50
	s.Series = buildRawSeries(periods, fields, s.Classifications, maxSeriesBars)

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
	// Fallback: when plot-based dominantPrice is 0 (graphics-only scripts),
	// derive a representative price from graphic y-values so capLevels can
	// sort levels by proximity to the market.
	levelPrice := dominantPrice
	if levelPrice == 0 {
		levelPrice = dominantPriceFromGraphics(graphic)
	}
	s.Levels = capLevels(s.Levels, 50, levelPrice)

	// Separate strategy from indicator scripts and enrich accordingly.
	s.Meta.ScriptType = resolveScriptType(len(isStrategy) > 0 && isStrategy[0], strategyReport)
	if s.Meta.ScriptType == ScriptTypeStrategy {
		s.Events = append(s.Events, strategyEvents(strategyReport)...)
		s.Events = capEvents(s.Events, 30)
	}

	// Aggregate directional bias.
	s.Bias, s.Confidence = computeBias(s.Last, s.Events, s.Classifications)
	if b := strategyBias(strategyReport); b != "" {
		s.Bias = b
	}

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

// resolveScriptType distinguishes a strategy from an indicator (see ScriptType).
// A strategy emits signals: its trades arrive in the strategy report. An
// indicator emits only analysis output (plots/graphics) and is specialised via
// custom inputs or input templates.
//
// An explicit hint from the Pine schema declaration (IsStrategy) takes
// precedence; otherwise a non-empty strategy report (performance/trades) marks
// the run as a strategy. Scripts that produce periods but are neither are
// reported as indicators.
func resolveScriptType(isStrategy bool, report map[string]any) ScriptType {
	if isStrategy || hasStrategyReport(report) {
		return ScriptTypeStrategy
	}
	return ScriptTypeIndicator
}

// dominantPriceFromGraphics extracts a representative price from the y-values
// of drawing boxes, lines, and labels. Used as a fallback when the plot-based
// dominantPrice is 0 (graphics-only scripts with no meaningful plot data).
func dominantPriceFromGraphics(graphic map[string]map[string]any) float64 {
	var prices []float64
	collect := func(m map[string]any, keys ...string) {
		for _, item := range m {
			mm, ok := item.(map[string]any)
			if !ok {
				continue
			}
			for _, k := range keys {
				if v, ok := toFloat(mm[k]); ok && v > 0 {
					prices = append(prices, v)
				}
			}
		}
	}
	if boxes, ok := graphic["dwgboxes"]; ok {
		collect(boxes, "y1", "y2")
	}
	if lines, ok := graphic["dwglines"]; ok {
		collect(lines, "y1", "y2")
	}
	if labels, ok := graphic["dwglabels"]; ok {
		collect(labels, "y")
	}
	if len(prices) == 0 {
		return 0
	}
	// Median price
	sort.Float64s(prices)
	return prices[len(prices)/2]
}

// hasStrategyReport reports whether a strategy report carries any real payload.
func hasStrategyReport(report map[string]any) bool {
	if report == nil {
		return false
	}
	if _, ok := report["performance"].(map[string]any); ok {
		return true
	}
	if _, ok := report["trades"].([]any); ok {
		return true
	}
	if _, ok := report["settings"].(map[string]any); ok {
		return true
	}
	return false
}

// strategyEvents converts a strategy report's executed trades into directional
// buy/sell events so an agent can react to actual entries/exits rather than only
// raw indicator plots. Entry type "le" = long entry (buy), "se" = short (sell).
func strategyEvents(report map[string]any) []Event {
	tradesRaw, ok := report["trades"].([]any)
	if !ok {
		return nil
	}
	var evs []Event
	for _, t := range tradesRaw {
		tm, ok := t.(map[string]any)
		if !ok {
			continue
		}
		e, ok := tm["e"].(map[string]any)
		if !ok {
			continue
		}
		var ev Event
		tp, _ := e["tp"].(string)
		switch tp {
		case "le":
			ev.Kind = "buy"
		case "se":
			ev.Kind = "sell"
		default:
			continue
		}
		ev.Field = "strategy_trade"
		ev.Value = floatOrZero(e["p"])
		if c, ok := e["c"].(string); ok {
			ev.Field = "trade_" + c
		}
		if tmv, ok := e["tm"].(float64); ok {
			ev.Time = int64(tmv)
		}
		evs = append(evs, ev)
	}
	return evs
}

// strategyBias derives a directional read from a strategy's most recent executed
// trade side: the last long entry implies a bullish stance, a short entry a
// bearish one. Returns "" when there is no trade history to reason from.
func strategyBias(report map[string]any) string {
	tradesRaw, ok := report["trades"].([]any)
	if !ok || len(tradesRaw) == 0 {
		return ""
	}
	last, ok := tradesRaw[len(tradesRaw)-1].(map[string]any)
	if !ok {
		return ""
	}
	e, ok := last["e"].(map[string]any)
	if !ok {
		return ""
	}
	switch tp, _ := e["tp"].(string); tp {
	case "le":
		return "long"
	case "se":
		return "short"
	}
	return ""
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

			upper := strings.ToUpper(text)
			kind := "alert"
			lvlKind := ""

			switch {
			// Direct buy/sell signals
			case strings.Contains(upper, "BUY") || strings.Contains(upper, "LONG") || strings.Contains(upper, "BULL"):
				kind = "buy"
			case strings.Contains(upper, "SELL") || strings.Contains(upper, "SHORT") || strings.Contains(upper, "BEAR"):
				kind = "sell"
			// Break of resistance = bullish; break of support = bearish
			case strings.Contains(upper, "BREAK") && (strings.Contains(upper, "RES") || strings.Contains(upper, "RESIST")):
				kind = "buy"
			case strings.Contains(upper, "BREAK") && (strings.Contains(upper, "SUP") || strings.Contains(upper, "SUPPORT")):
				kind = "sell"
			// SMC structural labels (direction encoded in color; keep as alert
			// with original text so the agent can interpret the structure)
			case upper == "BOS" || upper == "CHOCH" || strings.Contains(upper, "BREAK OF STRUCTURE") || strings.Contains(upper, "CHANGE OF CHARACTER"):
				kind = "alert"
			// SMC liquidity levels
			case upper == "EQH" || strings.Contains(upper, "EQUAL HIGH") || strings.Contains(upper, "STRONG HIGH"):
				lvlKind = "resistance"
			case upper == "EQL" || strings.Contains(upper, "EQUAL LOW") || strings.Contains(upper, "WEAK LOW"):
				lvlKind = "support"
			// Standard support/resistance labels
			case strings.Contains(upper, "SUPPORT") || strings.Contains(upper, "RESISTANCE"):
				if strings.Contains(upper, "SUPPORT") {
					lvlKind = "support"
				} else {
					lvlKind = "resistance"
				}
			case strings.Contains(upper, "POC") || strings.Contains(upper, "LEVEL"):
				lvlKind = "band"
			}

			if kind != "alert" {
				counts[kind]++
			} else {
				counts["alert"]++
			}
			if len(events) < 50 {
				events = append(events, Event{
					Time:  int64(x),
					Field: "label_" + text,
					Kind:  kind,
					Value: y,
					Text:  text,
				})
			}
			if lvlKind != "" {
				levels = append(levels, Level{
					Field: "dwglabels_" + text,
					Kind:  lvlKind,
					Value: y,
				})
			}
		}
	}

	// --- Other draw types ---
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
						Field: "label_" + text,
						Kind:  kind,
						Value: y,
					})
				}
			}

		case "dwglines":
			counts["line"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				// dwglines: {x1, y1, x2, y2, w, st, ...}
				y1, y1Ok := toFloat(m["y1"])
				y2, y2Ok := toFloat(m["y2"])
				if !y1Ok || !y2Ok {
					continue
				}

				// Nearly horizontal lines → S/R levels.  Use y > 0 to include
				// all price scales (crypto, forex, equities) rather than the
				// old y > 2000 threshold which excluded low-priced assets.
				if y1 > 0 || y2 > 0 {
					avgPrice := (y1 + y2) / 2
					slope := math.Abs(y2-y1) / math.Max(math.Abs(y1), 1)
					if slope < 0.001 { // nearly flat
						levels = append(levels, Level{
							Field: "dwglines",
							Kind:  "band",
							Value: avgPrice,
						})
					}
				}
			}

		case "dwgboxes":
			counts["box"] += len(items)
			// Stacked boxes (e.g. volume-profile up/down rows) share exact price
			// edges; dedupe by value so levels isn't flooded with duplicates.
			seenBoxEdge := make(map[float64]bool)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				// dwgboxes: {x1, y1, x2, y2, t, ...}
				y1, y1Ok := toFloat(m["y1"])
				y2, y2Ok := toFloat(m["y2"])
				if !y1Ok || !y2Ok {
					continue
				}
				// Box boundaries as price levels (y > 0 to cover all price scales)
				if y1 > 0 || y2 > 0 {
					high := math.Max(y1, y2)
					low := math.Min(y1, y2)
					if !seenBoxEdge[high] {
						seenBoxEdge[high] = true
						levels = append(levels, Level{
							Field: "dwgboxes_top",
							Kind:  "resistance",
							Value: high,
						})
					}
					if !seenBoxEdge[low] {
						seenBoxEdge[low] = true
						levels = append(levels, Level{
							Field: "dwgboxes_bottom",
							Kind:  "support",
							Value: low,
						})
					}
				}
			}

		case "fill":
			counts["fill"] += len(items)

		case "table", "dwgtablecells":
			counts[drawType] += len(items)
			// Reconstruct tables into structured grids and extract labeled values.
			grids := ReconstructTables(map[string]map[string]any{drawType: items})
			if len(grids) == 0 {
				// Fallback: try full graphic for ReconstructTables (needs dwgtables too).
				grids = ReconstructTables(graphic)
			}
			for _, grid := range grids {
				for r := 0; r < grid.Rows; r++ {
					if r >= len(grid.Cells) {
						continue
					}
					row := grid.Cells[r]
					// 2-column tables: label → value pairs
					if len(row) >= 2 {
						label := strings.TrimSpace(row[0])
						valText := strings.TrimSpace(row[1])
						if label == "" || valText == "" {
							continue
						}
						// Skip NaN/na values
						upper := strings.ToUpper(valText)
						if upper == "NAN" || upper == "NA" || upper == "NULL" {
							continue
						}
						if val, err := parseFormattedNumber(valText); err == nil {
							fieldName := "table_" + strings.ReplaceAll(label, " ", "_")
							events = append(events, Event{
								Field: fieldName,
								Kind:  "state",
								Value: val,
							})
						} else {
							// Non-numeric value — store as text state
							fieldName := "table_" + strings.ReplaceAll(label, " ", "_")
							events = append(events, Event{
								Field: fieldName,
								Kind:  "text",
								Value: 0,
								Text:  valText,
							})
						}
					}
				}
			}

		case "hhists":
			// Horizontal histograms (volume profile): {priceLow, priceHigh, firstBarTime, lastBarTime, rate[]}
			counts["hhist"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				priceLow, loOk := toFloat(m["priceLow"])
				priceHigh, hiOk := toFloat(m["priceHigh"])
				if !loOk || !hiOk {
					continue
				}
				if priceHigh > 2000 || priceLow > 2000 {
					levels = append(levels, Level{
						Field: "hhists_high",
						Kind:  "resistance",
						Value: priceHigh,
					})
					levels = append(levels, Level{
						Field: "hhists_low",
						Kind:  "support",
						Value: priceLow,
					})
					// Store the histogram rate array in Last for context.
					if rates, ok := m["rate"].([]any); ok && len(rates) > 0 {
						// Store as text event for agent visibility.
						if len(events) < 20 {
							events = append(events, Event{
								Field: "hhists_profile",
								Kind:  "state",
								Value: priceHigh,
								Text:  fmt.Sprintf("%.2f-%.2f (%d bins)", priceLow, priceHigh, len(rates)),
							})
						}
					}
				}
			}

		case "horizlines":
			// Horizontal lines: {level, startIndex, endIndex, extendRight, extendLeft}
			counts["horizline"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				level, ok := toFloat(m["level"])
				if !ok {
					continue
				}
				if level > 2000 {
					levels = append(levels, Level{
						Field: "horizlines",
						Kind:  "resistance",
						Value: level,
					})
				}
			}

		case "polygons":
			// Polygons: {points: [{index, level}, ...]}
			counts["polygon"] += len(items)
			for _, item := range items {
				m, _ := item.(map[string]any)
				if m == nil {
					continue
				}
				if points, ok := m["points"].([]any); ok {
					for _, pt := range points {
						ptMap, _ := pt.(map[string]any)
						if ptMap == nil {
							continue
						}
						level, ok := toFloat(ptMap["level"])
						if ok && level > 2000 {
							levels = append(levels, Level{
								Field: "polygon_point",
								Kind:  "band",
								Value: level,
							})
						}
					}
				}
			}

		default:
			// Skip dwgtables / dwgtablecells — handled by ReconstructTables
			if drawType == "dwgtables" || drawType == "dwgtablecells" {
				continue
			}
			counts[drawType] += len(items)
		}
	}

	return events, dedupeLevels(levels), counts
}

// dedupeLevels collapses levels that describe the same price zone.
// A box's top/bottom is often emitted again as a dwglines band at the same
// price, so without this the level list carries doubled noise. Keeps the most
// specific kind: resistance/support over band. Tolerance: 0.05% of price.
func dedupeLevels(levels []Level) []Level {
	out := make([]Level, 0, len(levels))
	for _, lv := range levels {
		found := false
		for i := range out {
			if out[i].Kind == lv.Kind {
				continue
			}
			// same price zone and close price
			if math.Abs(out[i].Value-lv.Value)/math.Max(math.Abs(lv.Value), 1) < 0.0001 {
				// prefer resistance/support over band
				if lv.Kind == "resistance" || lv.Kind == "support" {
					if out[i].Kind == "band" {
						out[i] = lv
					}
				}
				found = true
				break
			}
		}
		if !found {
			out = append(out, lv)
		}
	}
	return out
}

// classifyGraphicsOnly attempts to classify graphic fields based on their values.
// Since there are no periods, we use heuristic rules on the graphic data values
// to assign semantic categories (price, signal, style, metric, snapshot).
func classifyGraphicsOnly(classifications map[string]PlotClass, graphic map[string]map[string]any) {
	if graphic == nil {
		return
	}

	// Collect all numeric values from graphic items to analyze patterns
	var allVals []float64

	extractFloatsFromCell := func(cell any) {
		m, ok := cell.(map[string]any)
		if !ok {
			return
		}
		for _, v := range m {
			if f, ok := v.(float64); ok {
				allVals = append(allVals, f)
			}
		}
	}

	// dwgtablecells: structure is map[cellID]cellData
	if cells, ok := graphic["dwgtablecells"]; ok {
		for _, cellV := range cells {
			extractFloatsFromCell(cellV)
		}
	}

	// dwglabels: structure is map[labelID]labelData
	if labels, ok := graphic["dwglabels"]; ok {
		for _, labelV := range labels {
			extractFloatsFromCell(labelV)
			if m, ok := labelV.(map[string]any); ok {
				if t, ok := m["t"].(string); ok {
					upper := strings.ToUpper(t)
					// Label-based classification
					if strings.Contains(upper, "BUY") || strings.Contains(upper, "SELL") ||
						strings.Contains(upper, "LONG") || strings.Contains(upper, "SHORT") {
						if _, exists := classifications["label"]; !exists {
							classifications["label"] = ClassSignal
						}
					}
					if strings.Contains(upper, "SUPPORT") || strings.Contains(upper, "RESISTANCE") ||
						strings.Contains(upper, "POC") || strings.Contains(upper, "LEVEL") {
						if _, exists := classifications["label"]; !exists {
							classifications["label"] = ClassPrice
						}
					}
				}
			}
		}
	}

	// Other draw types: dwglines, dwgboxes, fill, tables
	for drawType, items := range graphic {
		if drawType == "dwglabels" || drawType == "dwgtables" || drawType == "dwgtablecells" {
			continue
		}
		for _, cellV := range items {
			extractFloatsFromCell(cellV)
		}
	}

	// Enhanced: dwgboxes - extract y1/y2 as price levels
	if boxes, ok := graphic["dwgboxes"]; ok {
		for _, boxV := range boxes {
			m, ok := boxV.(map[string]any)
			if !ok {
				continue
			}
			y1, y1Ok := toFloat(m["y1"])
			y2, y2Ok := toFloat(m["y2"])
			if y1Ok && y2Ok {
				allVals = append(allVals, y1, y2)
				// Box boundaries as price levels
				if y1 > 2000 || y2 > 2000 {
					_ = math.Max(y1, y2)
					_ = math.Min(y1, y2)
					if _, exists := classifications["box"]; !exists {
						classifications["box"] = ClassPrice
					}
				}
			}
		}
	}

	// Classify based on value patterns
	if len(allVals) > 0 {
		sum := 0.0
		minVal, maxVal := allVals[0], allVals[0]
		intCount := 0
		for _, v := range allVals {
			sum += v
			if v == math.Round(v) && math.Abs(v) < 1e6 {
				intCount++
			}
			if v < minVal {
				minVal = v
			}
			if v > maxVal {
				maxVal = v
			}
		}
		mean := sum / float64(len(allVals))
		rng := maxVal - minVal
		stddev := 0.0
		if rng > 0 {
			for _, v := range allVals {
				stddev += (v - mean) * (v - mean)
			}
			stddev = math.Sqrt(stddev / float64(len(allVals)))
		}
		nonZero := 0
		for _, v := range allVals {
			if v != 0 {
				nonZero++
			}
		}
		_ = nonZero
		nonZeroDensity := float64(nonZero) / float64(len(allVals))
		integerRatio := float64(intCount) / float64(len(allVals))

		// Heuristic classifications
		// 1. Values in plausible price range (100-10000) with low variance → price level
		if mean > 100 && mean < 10000 && rng < mean*0.3 && stddev/mean < 0.2 {
			for f := range classifications {
				if classifications[f] == ClassMetric {
					classifications[f] = ClassPrice
				}
			}
		}

		// 2. Very large values (>1e6) → likely ARGB color constants → style
		if mean > 1e6 {
			for f := range classifications {
				if classifications[f] == ClassMetric {
					classifications[f] = ClassStyle
				}
			}
		}

		// 3. Single unique value → snapshot
		uniqueVals := map[float64]struct{}{}
		for _, v := range allVals {
			uniqueVals[round6(v)] = struct{}{}
		}
		if len(uniqueVals) == 1 {
			for f := range classifications {
				if classifications[f] == ClassMetric {
					classifications[f] = ClassSnapshot
				}
			}
		}
		// 4. High non-zero density with price-range values → price
		if nonZeroDensity > 0.6 && mean > 100 && mean < 10000 {
			for f := range classifications {
				if classifications[f] == ClassMetric {
					classifications[f] = ClassPrice
				}
			}
		}
		_ = nonZeroDensity
		_ = integerRatio
	}
}

// extractLastFromGraphics extracts a "last" snapshot from graphic data.
// Since there are no periods, we derive the last values from the most recent
// graphic items (e.g., the last dwgtablecell, the last dwglabel, etc.).
func extractLastFromGraphics(last map[string]any, graphic map[string]map[string]any) {
	if graphic == nil {
		return
	}

	// dwgtablecells: reconstruct tables and store structured data
	if _, ok := graphic["dwgtablecells"]; ok {
		grids := ReconstructTables(graphic)
		for _, grid := range grids {
			tableData := make(map[string]any)
			for r := 0; r < grid.Rows; r++ {
				if r >= len(grid.Cells) {
					continue
				}
				row := grid.Cells[r]
				if len(row) >= 2 {
					label := strings.TrimSpace(row[0])
					valText := strings.TrimSpace(row[1])
					if label == "" || valText == "" {
						continue
					}
					upper := strings.ToUpper(valText)
					if upper == "NAN" || upper == "NA" || upper == "NULL" {
						continue
					}
					if val, err := parseFormattedNumber(valText); err == nil {
						tableData[label] = val
					} else {
						tableData[label] = valText
					}
				}
			}
			if len(tableData) > 0 {
				last["table_"+grid.ID] = tableData
			}
		}
	}

	// dwglabels: last label price + text
	if labels, ok := graphic["dwglabels"]; ok {
		lastKey := ""
		for k := range labels {
			if k > lastKey {
				lastKey = k
			}
		}
		if labelV, ok := labels[lastKey].(map[string]any); ok {
			if v, ok := toFloat(labelV["y"]); ok {
				last["last_label_price"] = v
			}
			if text, ok := labelV["t"].(string); ok && text != "" {
				last["last_label_text"] = text
				// Detect BUY/SELL/LONG/SHORT signals from label text
				upper := strings.ToUpper(text)
				if strings.Contains(upper, "BUY") || strings.Contains(upper, "LONG") {
					last["last_label_signal"] = "buy"
				} else if strings.Contains(upper, "SELL") || strings.Contains(upper, "SHORT") {
					last["last_label_signal"] = "sell"
				}
			}
		}
	}

	// dwgboxes: last box high/low
	if boxes, ok := graphic["dwgboxes"]; ok {
		lastKey := ""
		for k := range boxes {
			if k > lastKey {
				lastKey = k
			}
		}
		if boxV, ok := boxes[lastKey].(map[string]any); ok {
			y1, y1Ok := toFloat(boxV["y1"])
			y2, y2Ok := toFloat(boxV["y2"])
			if y1Ok && y2Ok {
				last["last_box_high"] = math.Max(y1, y2)
				last["last_box_low"] = math.Min(y1, y2)
				// Box price range as a level
				last["last_box_level"] = (y1 + y2) / 2
			}
		}
	}
	// dwglines: last line values
	if lines, ok := graphic["dwglines"]; ok {
		lastKey := ""
		for k := range lines {
			if k > lastKey {
				lastKey = k
			}
		}
		if lineV, ok := lines[lastKey].(map[string]any); ok {
			y1, y1Ok := toFloat(lineV["y1"])
			y2, y2Ok := toFloat(lineV["y2"])
			if y1Ok && y2Ok {
				last["last_line_y1"] = y1
				last["last_line_y2"] = y2
				// Nearly horizontal line → price level
				if math.Abs(y1-y2) < 1 {
					last["last_horizontal_level"] = (y1 + y2) / 2
				}
			}
		}
	}
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

func capLevels(levels []Level, max int, lastPrice float64) []Level {
	// Sort by kind priority (resistance/support > band > other), then by
	// proximity to the last price so the most relevant levels survive the cap.
	sort.Slice(levels, func(i, j int) bool {
		ki, kj := kindPriority(levels[i].Kind), kindPriority(levels[j].Kind)
		if ki != kj {
			return ki < kj // lower = higher priority
		}
		// Closer to last price = higher priority. When lastPrice is 0
		// (unknown), fall back to largest absolute value (old behaviour).
		if lastPrice != 0 {
			di := math.Abs(levels[i].Value - lastPrice)
			dj := math.Abs(levels[j].Value - lastPrice)
			return di < dj
		}
		return math.Abs(levels[i].Value) > math.Abs(levels[j].Value)
	})
	if len(levels) > max {
		return levels[:max]
	}
	return levels
}

func kindPriority(kind string) int {
	switch kind {
	case "resistance", "support":
		return 0
	case "band":
		return 1
	default:
		return 2
	}
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
	// perf.all fields
	s.NetProfit = floatOrZero(all["netProfit"])
	s.NetProfitPercent = floatOrZero(all["netProfitPercent"])
	s.GrossProfit = floatOrZero(all["grossProfit"])
	s.GrossLoss = floatOrZero(all["grossLoss"])
	s.WinRate = floatOrZero(all["percentProfitable"])
	s.TotalTrades = int(floatOrZero(all["totalTrades"]))
	s.WinningTrades = int(floatOrZero(all["numberOfWiningTrades"]))
	s.LosingTrades = int(floatOrZero(all["numberOfLosingTrades"]))
	s.ProfitFactor = floatOrZero(all["profitFactor"])
	s.AvgTrade = floatOrZero(all["avgTrade"])
	s.LargestWin = floatOrZero(all["largestWinTrade"])
	s.LargestLoss = floatOrZero(all["largestLosTrade"])
	s.CommissionPaid = floatOrZero(all["commissionPaid"])
	// perf top-level fields
	s.MaxDrawdown = floatOrZero(perf["maxStrategyDrawDown"])
	s.MaxDDPercent = floatOrZero(perf["maxStrategyDrawDownPercent"])
	s.SharpeRatio = floatOrZero(perf["sharpeRatio"])
	s.SortinoRatio = floatOrZero(perf["sortinoRatio"])
	s.BuyHoldReturn = floatOrZero(perf["buyHoldReturn"])
	s.OpenPL = floatOrZero(perf["openPL"])
	// currency from report top-level
	if cur, ok := report["currency"].(string); ok {
		s.Currency = cur
	}
	// extract per-trade data
	s.Trades = extractTrades(report)
	return s
}

// extractTrades extracts individual trade records from the strategy report.
// TV sends trades as an array of objects with nested entry/exit info:
// {e: {b, c, p, tm, tp}, x: {b, c, p, tm, tp}, q, v, cp: {p, v}, tp: {p, v}, ...}
// e=entry, x=exit, q=qty, v=value, cp=cumulative P/L, tp=trade P/L
func extractTrades(report map[string]any) []Trade {
	tradesRaw, ok := report["trades"].([]any)
	if !ok || len(tradesRaw) == 0 {
		return nil
	}
	trades := make([]Trade, 0, len(tradesRaw))
	for _, t := range tradesRaw {
		tm, ok := t.(map[string]any)
		if !ok {
			continue
		}
		tr := Trade{}
		// Entry info: e = {b, c, p, tm, tp}
		if e, ok := tm["e"].(map[string]any); ok {
			tr.Entry = floatOrZero(e["p"])
			if tp, ok := e["tp"].(string); ok {
				switch tp {
				case "le":
					tr.Side = "buy"
				case "se":
					tr.Side = "sell"
				default:
					tr.Side = tp
				}
			}
			if c, ok := e["c"].(string); ok && tr.ID == "" {
				tr.ID = c
			}
		}
		// Exit info: x = {b, c, p, tm, tp}
		if x, ok := tm["x"].(map[string]any); ok {
			tr.Price = floatOrZero(x["p"])
		}
		tr.Qty = floatOrZero(tm["q"])
		// Trade profit: tp = {p, v}
		if tp, ok := tm["tp"].(map[string]any); ok {
			tr.Profit = floatOrZero(tp["v"])
		}
		trades = append(trades, tr)
	}
	return trades
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

// buildRawSeries turns raw periods into a chronological series of maps,
// keeping only non-noise fields. The result is oldest-first so consumers can
// read left-to-right.
func buildRawSeries(periods []map[string]any, fields []string, classes map[string]PlotClass, maxBars int) []map[string]any {
	n := len(periods)
	if n == 0 {
		return nil
	}
	start := 0
	if n > maxBars {
		start = n - maxBars
	}
	series := make([]map[string]any, 0, n-start)
	for i := n - 1; i >= start; i-- {
		p := periods[i]
		m := map[string]any{}
		if t, ok := toFloat(p["$time"]); ok {
			m["time"] = t
		}
		for _, f := range fields {
			if classes[f] == ClassNoise {
				continue
			}
			m[f] = cleanFloat(p[f])
		}
		series = append(series, m)
	}
	return series
}
