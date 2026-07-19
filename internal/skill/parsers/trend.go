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
	price := toFloat(getField(last, []string{"Close", "close"}))
	trendDir := toFloat(getField(last, []string{"TrendDirection", "trendDirection", "Trend"}))
	tqi := toFloat(getField(last, []string{"TQI", "tqi", "QualityIndex"}))
	regime := getField(last, []string{"Regime", "regime"})

	bias := "neutral"
	if trendDir > 0 { bias = "bullish" } else if trendDir < 0 { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(trendDir) > 0 { agenticScore += 0.2 }
	if math.Abs(tqi) > 0.5 { agenticScore += 0.15 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "adaptive-supertrend-quality",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"trendDirection": trendDir, "tqi": tqi, "regime": regime},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Trend: %s | TQI: %.2f | Regime: %v", bias, tqi, regime)},
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
