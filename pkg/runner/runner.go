package runner

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/ch99q/tvcli/pkg/pipeline"
	"github.com/ch99q/tvcli/pkg/schema"
)

type RunOptions struct {
	PineID      string
	Symbol      string
	Timeframe   string
	Bars        int
	Inputs      map[string]string
	JSON        bool
	Agent       bool
	Verbose     bool
	OutputFile  string
	Session     string
	Signature   string
	Schema      *schema.PineSchema // Optional schema for dynamic parsing
}

type RunResult struct {
	Signals         []Signal          `json:"signals,omitempty"`
	Extracted       *pipeline.Signals  `json:"extracted,omitempty"`
	Narrative       Narrative         `json:"narrative"`
	Validation      Validation        `json:"validation"`
	AgenticScore    float64           `json:"agenticScore"`
	NumericalData   NumericalData     `json:"numericalData"`
	StrategyMetrics *StrategyMetrics  `json:"strategyMetrics,omitempty"`
	GraphicData     GraphicData       `json:"graphicData"`
	Dashboard       Dashboard         `json:"dashboard"`
	Meta            RunMeta           `json:"meta"`
	LastBar         map[string]any    `json:"lastBar,omitempty"`
}

type RunMeta struct {
	PineID      string `json:"pineId"`
	ScriptName  string `json:"scriptName"`
	Timeframe   string `json:"timeframe"`
	DurationMs  int64  `json:"durationMs"`
	PeriodCount int    `json:"periodCount"`
}

type NumericalData struct {
	Count     int                        `json:"count"`
	Fields    []string                   `json:"fields"`
	FieldMeta map[string]FieldMeta       `json:"fieldMeta"`
	LastBar   map[string]any             `json:"lastBar,omitempty"`
}

type FieldMeta struct {
	Category    string  `json:"category"`
	UniqueCount int     `json:"uniqueCount"`
	NullCount   int     `json:"nullCount"`
	Min         float64 `json:"min,omitempty"`
	Max         float64 `json:"max,omitempty"`
	Avg         float64 `json:"avg,omitempty"`
	Current     any     `json:"current"`
}

type StrategyMetrics struct {
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
}

type GraphicData struct {
	Summary   map[string]int `json:"summary"`
	ItemCount int            `json:"itemCount"`
}

type Dashboard struct {
	Fields map[string]any `json:"fields"`
}

type Narrative struct {
	MarketStructure  string   `json:"marketStructure"`
	PrimaryOpp       string   `json:"primaryOpportunity"`
	Warnings         []string `json:"warnings"`
}

type Validation struct {
	Passed   bool     `json:"passed"`
	Warnings []string `json:"warnings"`
}

type Signal struct {
	Rank       int     `json:"rank"`
	Direction  string  `json:"direction"`
	Entry      float64 `json:"entry"`
	StopLoss   float64 `json:"stopLoss"`
	TakeProfit float64 `json:"takeProfit"`
	RiskReward float64 `json:"riskReward"`
	Confidence string  `json:"confidence"`
}

// ExtractSignals runs the script-agnostic signal extractor on the raw data.
func ExtractSignals(periods []map[string]any, graphic map[string]map[string]any, strategyReport map[string]any, tf string, pineID string, symbol string, sch *schema.PineSchema) *pipeline.Signals {
	if sch != nil && len(sch.Plots) > 0 {
		parsed := pipeline.Parse(periods, sch)
		return pipeline.ExtractWithSchema(pineID, symbol, tf, parsed, graphic, strategyReport)
	}
	return pipeline.Extract(pineID, symbol, tf, periods, graphic, strategyReport)
}

func ParseOutput(periods []map[string]any, graphic map[string]map[string]any, strategyReport map[string]any, tf string, pineID string, sch *schema.PineSchema) *RunResult {
	start := time.Now()

	// Use dynamic parser when schema is available
	var parsed *pipeline.ParseResult
	var numData *NumericalData
	var extracted *pipeline.Signals

	if sch != nil && len(sch.Plots) > 0 {
		// Schema-guided path: rename plot_N → semantic names, classify from metaInfo
		parsed = pipeline.Parse(periods, sch)
		numData = numericalDataFromTyped(parsed)
		extracted = pipeline.ExtractWithSchema(pineID, "", tf, parsed, graphic, strategyReport)
	} else {
		// Fallback: statistical inference (original path)
		numData = extractNumericalData(periods)
		extracted = pipeline.Extract(pineID, "", tf, periods, graphic, strategyReport)
	}

	stratMetrics := extractStrategyMetrics(strategyReport)
	graphicInt := extractGraphicIntelligence(graphic)
	dashboard := extractDashboard(graphic)
	intelligence := buildIntelligence(numData, graphicInt)

	result := &RunResult{
		NumericalData:   *numData,
		Extracted:       extracted,
		StrategyMetrics: stratMetrics,
		GraphicData:     *graphicInt,
		Dashboard:       *dashboard,
		Meta: RunMeta{
			PineID:      pineID,
			ScriptName:  "Generic Indicator",
			Timeframe:   tf,
			DurationMs:  time.Since(start).Milliseconds(),
			PeriodCount: len(periods),
		},
	}

	// Extract last bar
	if len(periods) > 0 {
		result.LastBar = periods[0]
	}

	// Build signals from intelligence
	result.Signals = generateSignals(intelligence, numData)
	result.Narrative = generateNarrative(intelligence, numData)
	result.Validation = validateOutput(numData, graphicInt)
	result.AgenticScore = computeAgenticScore(intelligence, numData, graphicInt)

	return result
}

func extractNumericalData(periods []map[string]any) *NumericalData {
	if len(periods) == 0 {
		return &NumericalData{FieldMeta: make(map[string]FieldMeta)}
	}

	// Find numerical fields from first period
	var fields []string
	sample := periods[0]
	for k, v := range sample {
		if k == "$time" || k == "timestamp" || k == "datetime" {
			continue
		}
		if _, ok := v.(float64); ok {
			fields = append(fields, k)
		}
	}

	fieldMeta := make(map[string]FieldMeta)
	for _, f := range fields {
		var nonNull []float64
		for _, p := range periods {
			if v, ok := p[f].(float64); ok && !math.IsNaN(v) && math.Abs(v) < 1e99 {
				nonNull = append(nonNull, v)
			}
		}
		meta := FieldMeta{Category: inferCategory(f, nonNull)}
		if len(nonNull) > 0 {
			min, max := nonNull[0], nonNull[0]
			sum := 0.0
			for _, v := range nonNull {
				if v < min {
					min = v
				}
				if v > max {
					max = v
				}
				sum += v
			}
			meta.Min = min
			meta.Max = max
			meta.Avg = sum / float64(len(nonNull))
			meta.Current = nonNull[0]
		}
		meta.UniqueCount = len(nonNull)
		meta.NullCount = len(periods) - len(nonNull)
		fieldMeta[f] = meta
	}

	return &NumericalData{
		Count:     len(periods),
		Fields:    fields,
		FieldMeta: fieldMeta,
	}
}

// numericalDataFromTyped converts pipeline.TypedBar output to NumericalData.
func numericalDataFromTyped(parsed *pipeline.ParseResult) *NumericalData {
	if len(parsed.Bars) == 0 {
		return &NumericalData{FieldMeta: make(map[string]FieldMeta)}
	}

	fieldMeta := make(map[string]FieldMeta)
	for _, name := range parsed.FieldNames {
		var nonNull []float64
		for _, bar := range parsed.Bars {
			for _, tv := range bar.Values {
				if tv.Name == name && !tv.IsNull {
					nonNull = append(nonNull, tv.Value)
				}
			}
		}

		// Category comes from the dynamic parser's classification
		category := "metric"
		for _, bar := range parsed.Bars {
			for _, tv := range bar.Values {
				if tv.Name == name {
					category = tv.Category
					break
				}
			}
			if category != "metric" {
				break
			}
		}

		meta := FieldMeta{Category: category}
		if len(nonNull) > 0 {
			min, max := nonNull[0], nonNull[0]
			sum := 0.0
			for _, v := range nonNull {
				if v < min {
					min = v
				}
				if v > max {
					max = v
				}
				sum += v
			}
			meta.Min = min
			meta.Max = max
			meta.Avg = sum / float64(len(nonNull))
			meta.Current = nonNull[0]
		}
		meta.UniqueCount = len(nonNull)
		meta.NullCount = len(parsed.Bars) - len(nonNull)
		fieldMeta[name] = meta
	}

	return &NumericalData{
		Count:     len(parsed.Bars),
		Fields:    parsed.FieldNames,
		FieldMeta: fieldMeta,
	}
}

func inferCategory(field string, values []float64) string {
	lower := strings.ToLower(field)
	if strings.Contains(lower, "color") || strings.Contains(lower, "colour") {
		return "colorer"
	}
	signalKeywords := []string{"signal", "direction", "trend", "bos", "choch", "buy", "sell", "long", "short"}
	for _, kw := range signalKeywords {
		if strings.Contains(lower, kw) {
			return "signal"
		}
	}
	if strings.Contains(lower, "open") || strings.Contains(lower, "high") || strings.Contains(lower, "low") || strings.Contains(lower, "close") || strings.Contains(lower, "price") {
		return "price"
	}
	if strings.Contains(lower, "volume") || strings.Contains(lower, "vol") {
		return "volume"
	}
	if len(values) > 0 {
		min, max := values[0], values[0]
		for _, v := range values {
			if v < min {
				min = v
			}
			if v > max {
				max = v
			}
		}
		range_ := max - min
		if (range_ <= 100 && min >= 0 && max <= 100) || (range_ <= 200 && min >= -100 && max <= 100) {
			return "oscillator"
		}
	}
	return "continuous"
}

func extractStrategyMetrics(report map[string]any) *StrategyMetrics {
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

	m := &StrategyMetrics{}
	// perf.all fields
	if v, ok := all["netProfit"].(float64); ok {
		m.NetProfit = v
	}
	if v, ok := all["netProfitPercent"].(float64); ok {
		m.NetProfitPercent = v
	}
	if v, ok := all["grossProfit"].(float64); ok {
		m.GrossProfit = v
	}
	if v, ok := all["grossLoss"].(float64); ok {
		m.GrossLoss = v
	}
	if v, ok := all["percentProfitable"].(float64); ok {
		m.WinRate = v
	}
	if v, ok := all["totalTrades"].(float64); ok {
		m.TotalTrades = int(v)
	}
	if v, ok := all["numberOfWiningTrades"].(float64); ok {
		m.WinningTrades = int(v)
	}
	if v, ok := all["numberOfLosingTrades"].(float64); ok {
		m.LosingTrades = int(v)
	}
	if v, ok := all["profitFactor"].(float64); ok {
		m.ProfitFactor = v
	}
	if v, ok := all["avgTrade"].(float64); ok {
		m.AvgTrade = v
	}
	if v, ok := all["largestWinTrade"].(float64); ok {
		m.LargestWin = v
	}
	if v, ok := all["largestLosTrade"].(float64); ok {
		m.LargestLoss = v
	}
	if v, ok := all["commissionPaid"].(float64); ok {
		m.CommissionPaid = v
	}
	// perf top-level fields
	if v, ok := perf["maxStrategyDrawDown"].(float64); ok {
		m.MaxDrawdown = v
	}
	if v, ok := perf["maxStrategyDrawDownPercent"].(float64); ok {
		m.MaxDDPercent = v
	}
	if v, ok := perf["sharpeRatio"].(float64); ok {
		m.SharpeRatio = v
	}
	if v, ok := perf["sortinoRatio"].(float64); ok {
		m.SortinoRatio = v
	}
	if v, ok := perf["buyHoldReturn"].(float64); ok {
		m.BuyHoldReturn = v
	}
	if v, ok := perf["openPL"].(float64); ok {
		m.OpenPL = v
	}
	// currency from report top-level
	if cur, ok := report["currency"].(string); ok {
		m.Currency = cur
	}
	return m
}

func extractGraphicIntelligence(graphic map[string]map[string]any) *GraphicData {
	summary := make(map[string]int)
	count := 0
	for drawType, items := range graphic {
		summary[drawType] = len(items)
		count += len(items)
	}
	return &GraphicData{Summary: summary, ItemCount: count}
}

func extractDashboard(graphic map[string]map[string]any) *Dashboard {
	return &Dashboard{Fields: make(map[string]any)}
}

func buildIntelligence(numData *NumericalData, graphicInt *GraphicData) map[string]any {
	return map[string]any{
		"summary":    fmt.Sprintf("Script produced %d numerical fields across %d bars.", len(numData.Fields), numData.Count),
		"confidence": 50,
		"recommendation": "neutral",
	}
}

func generateSignals(intelligence map[string]any, numData *NumericalData) []Signal {
	// Placeholder — in full implementation, this detects crossovers and signals
	return nil
}

func generateNarrative(intelligence map[string]any, numData *NumericalData) Narrative {
	warnings := []string{}
	if len(numData.Fields) == 0 {
		warnings = append(warnings, "No numerical fields detected")
	}
	return Narrative{
		MarketStructure: fmt.Sprintf("%d fields across %d bars", len(numData.Fields), numData.Count),
		Warnings:        warnings,
	}
}

func validateOutput(numData *NumericalData, graphicInt *GraphicData) Validation {
	warnings := []string{}
	if numData.Count == 0 && graphicInt.ItemCount == 0 {
		warnings = append(warnings, "No data returned")
	}
	return Validation{
		Passed:   numData.Count > 0 || graphicInt.ItemCount > 0,
		Warnings: warnings,
	}
}

func computeAgenticScore(intelligence map[string]any, numData *NumericalData, graphicInt *GraphicData) float64 {
	score := 0.2
	// Base score for having any data
	if numData.Count > 0 {
		score += 0.2
	}
	if graphicInt.ItemCount > 0 {
		score += 0.15
	}
	// Additional scoring based on data quality
	if numData != nil {
		// Diversity of fields indicates richer analysis
		if len(numData.Fields) >= 3 {
			score += 0.1
		}
		if len(numData.Fields) >= 5 {
			score += 0.1
		}
		// Presence of key trading fields boosts score
		tradingFields := []string{"open", "high", "low", "close", "volume"}
		tradingFieldCount := 0
		for _, f := range numData.Fields {
			for _, tf := range tradingFields {
				if f == tf {
					tradingFieldCount++
					break
				}
			}
		}
		if tradingFieldCount >= 2 {
			score += 0.1
		}
		if tradingFieldCount >= 4 {
			score += 0.1
		}
	}
	if graphicInt != nil && graphicInt.ItemCount > 0 {
		// Graphic diversity (different draw types) indicates richer analysis
		if len(graphicInt.Summary) >= 3 {
			score += 0.1
		}
		if len(graphicInt.Summary) >= 5 {
			score += 0.1
		}
	}
	return math.Min(score, 0.99)
}

func FormatResults(result *RunResult, jsonOutput bool) string {
	if jsonOutput {
		b, _ := json.MarshalIndent(result, "", "  ")
		return string(b)
	}

	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString(fmt.Sprintf("  GENERIC INDICATOR — %s\n", result.Meta.ScriptName))
	sb.WriteString("======================================================================\n\n")

	sb.WriteString("SUMMARY\n")
	sb.WriteString(fmt.Sprintf("  %s\n", result.Narrative.MarketStructure))
	sb.WriteString(fmt.Sprintf("  Recommendation: %s\n\n", strings.ToUpper(result.Meta.ScriptName)))

	sb.WriteString("NUMERICAL FIELDS\n")
	sb.WriteString(fmt.Sprintf("  Total: %d fields across %d bars\n", len(result.NumericalData.Fields), result.NumericalData.Count))
	for _, f := range result.NumericalData.Fields[:min(10, len(result.NumericalData.Fields))] {
		meta := result.NumericalData.FieldMeta[f]
		sb.WriteString(fmt.Sprintf("  %s: %s | current=%v | min=%.2f | max=%.2f\n", f, meta.Category, meta.Current, meta.Min, meta.Max))
	}

	if result.StrategyMetrics != nil {
		m := result.StrategyMetrics
		sb.WriteString("\nSTRATEGY METRICS\n")
		sb.WriteString(fmt.Sprintf("  Trades: %d | Win Rate: %.1f%% | Profit Factor: %.2f | Net: %.2f",
			m.TotalTrades, m.WinRate*100, m.ProfitFactor, m.NetProfit))
		if m.Currency != "" {
			sb.WriteString(" " + m.Currency)
		}
		sb.WriteString("\n")
		if m.SharpeRatio != 0 || m.SortinoRatio != 0 {
			sb.WriteString(fmt.Sprintf("  Sharpe: %.2f | Sortino: %.2f", m.SharpeRatio, m.SortinoRatio))
		}
		if m.MaxDrawdown != 0 || m.MaxDDPercent != 0 {
			sb.WriteString(fmt.Sprintf(" | MaxDD: %.2f", m.MaxDrawdown))
			if m.MaxDDPercent != 0 {
				sb.WriteString(fmt.Sprintf(" (%.1f%%)", m.MaxDDPercent))
			}
		}
		if m.BuyHoldReturn != 0 {
			sb.WriteString(fmt.Sprintf(" | Buy&Hold: %.2f", m.BuyHoldReturn))
		}
		if m.SharpeRatio != 0 || m.SortinoRatio != 0 || m.MaxDrawdown != 0 || m.BuyHoldReturn != 0 {
			sb.WriteString("\n")
		}
		if m.WinningTrades > 0 || m.LosingTrades > 0 {
			sb.WriteString(fmt.Sprintf("  Wins: %d | Losses: %d", m.WinningTrades, m.LosingTrades))
		}
		if m.AvgTrade != 0 {
			sb.WriteString(fmt.Sprintf(" | Avg: %.2f", m.AvgTrade))
		}
		if m.GrossProfit != 0 || m.GrossLoss != 0 {
			sb.WriteString(fmt.Sprintf(" | Gross P/L: %.2f / %.2f", m.GrossProfit, m.GrossLoss))
		}
		if m.WinningTrades > 0 || m.LosingTrades > 0 || m.AvgTrade != 0 || m.GrossProfit != 0 {
			sb.WriteString("\n")
		}
	}

	sb.WriteString(fmt.Sprintf("\nMETA\n  pineId: %s\n  Duration: %dms\n", result.Meta.PineID, result.Meta.DurationMs))

	return sb.String()
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
