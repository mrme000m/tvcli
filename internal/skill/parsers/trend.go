package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var TrendSkill = &skill.Skill{
	Name:     "trend",
	Synopsis: "Self-Aware Trend System — adaptive SuperTrend with TQI",
	PineID:   "PUB;0f80bcf05d544d4c98fde06faab1c976",
	Tier:        "plus",
	KnownBroken: "Heavy indicator; requires a paid TradingView tier to return data.",
	Inputs: []skill.InputDef{
		{Name: "presetInput", TVInputID: "in_0", Type: "string", Default: "Auto"},
		{Name: "atrLenInput", TVInputID: "in_1", Type: "int", Default: 13},
		{Name: "baseMultInput", TVInputID: "in_2", Type: "float", Default: 2},
		{Name: "sourceInput", TVInputID: "in_3", Type: "source", Default: "close"},
		{Name: "useTqiInput", TVInputID: "in_8", Type: "bool", Default: true},
		{Name: "useCharFlipInput", TVInputID: "in_15", Type: "bool", Default: true},
		{Name: "useAsymBandsInput", TVInputID: "in_12", Type: "bool", Default: true},
		{Name: "useStructureInput", TVInputID: "in_25", Type: "bool", Default: true},
		{Name: "useRsiInput", TVInputID: "in_27", Type: "bool", Default: true},
		{Name: "useVolInput", TVInputID: "in_32", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"auto":      {"presetInput": "Auto"},
		"default":   {"presetInput": "Default"},
		"scalping":  {"presetInput": "Scalping"},
		"swing":     {"presetInput": "Swing"},
		"crypto":    {"presetInput": "Crypto"},
	},
	ParseOutput: parseTrend,
	FormatText:  formatTrend,
}

func parseTrend(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "adaptive-supertrend-quality",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	// The script emits the SuperTrend line as plot_0. The colorer/background
	// carry the trend state, but we can infer bias directly from price vs the line.
	superTrend := toFloat(getField(last, []string{"plot_0", "SuperTrend"}))

	bias := "neutral"
	if price > superTrend { bias = "bullish" }
	if price < superTrend { bias = "bearish" }

	bars := historicalBars(periods)
	buyCount, sellCount := 0, 0
	for _, p := range bars {
		if toFloat(getField(p, []string{"Buy_Signal", "Validated_Long_Signal"})) > 0 {
			buyCount++
		}
		if toFloat(getField(p, []string{"Sell_Signal", "Validated_Short_Signal"})) > 0 {
			sellCount++
		}
	}

	agenticScore := 0.2
	if len(bars) > 0 { agenticScore += 0.2 }
	if superTrend > 0 { agenticScore += 0.2 }
	if buyCount > 0 || sellCount > 0 { agenticScore += 0.15 }
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	latestBuy := toFloat(getField(last, []string{"Buy_Signal", "Validated_Long_Signal"})) > 0
	latestSell := toFloat(getField(last, []string{"Sell_Signal", "Validated_Short_Signal"})) > 0
	if latestBuy && bias == "bullish" {
		opps = append(opps, skill.Opportunity{Rank: 1, Setup: "adaptive_supertrend", Direction: "long", Confidence: "HIGH", ConfluenceScore: 0.8, Rationale: fmt.Sprintf("Buy signal; price %.2f above SuperTrend %.2f", price, superTrend)})
	} else if latestSell && bias == "bearish" {
		opps = append(opps, skill.Opportunity{Rank: 1, Setup: "adaptive_supertrend", Direction: "short", Confidence: "HIGH", ConfluenceScore: 0.8, Rationale: fmt.Sprintf("Sell signal; price %.2f below SuperTrend %.2f", price, superTrend)})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "adaptive-supertrend-quality",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"superTrend": superTrend, "buySignals": buyCount, "sellSignals": sellCount},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("SuperTrend: %.2f | Price: %.2f | Bias: %s | Buy/Sell: %d/%d", superTrend, price, bias, buyCount, sellCount)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatTrend(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  SELF-AWARE TREND SYSTEM\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Trend: %v | TQI: %v\n", result.Structure["trendDirection"], result.Structure["tqi"]))
	sb.WriteString(fmt.Sprintf("  Regime: %v\n", result.Structure["regime"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(TrendSkill) }
