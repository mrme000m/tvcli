package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var MTFSkill = &skill.Skill{
	Name:     "mtf",
	Synopsis: "XAUUSD Multi-Timeframe Trend Dashboard",
	PineID:   "PUB;d1ad30c0261f49f297357f8aa2a7854a",
	KnownBroken: "XAUUSD-specific dashboard; results are meaningless on other symbols.",
	Inputs: []skill.InputDef{
		{Name: "show_M15", TVInputID: "in_0", Type: "bool", Default: true},
		{Name: "show_M30", TVInputID: "in_1", Type: "bool", Default: true},
		{Name: "show_H1", TVInputID: "in_2", Type: "bool", Default: true},
		{Name: "show_H4", TVInputID: "in_3", Type: "bool", Default: true},
		{Name: "show_D1", TVInputID: "in_4", Type: "bool", Default: true},
		{Name: "fastLength", TVInputID: "in_5", Type: "int", Default: 10},
		{Name: "slowLength", TVInputID: "in_6", Type: "int", Default: 20},
		{Name: "rsiLength", TVInputID: "in_7", Type: "int", Default: 14},
	},
	ParseOutput: parseMTF,
	FormatText:  formatMTF,
}

func parseMTF(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "xauusd-mtf-trend",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	overallBias := toFloat(getField(last, []string{"OverallBias", "overallBias", "Bias"}))

	bias := "neutral"
	if overallBias > 0 { bias = "bullish" } else if overallBias < 0 { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(overallBias) > 0 { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "xauusd-mtf-trend",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"overallBias": overallBias, "price": price},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("MTF Bias: %s | Price: %.2f", bias, price)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatMTF(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  XAUUSD MTF TREND DASHBOARD\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Overall Bias: %v\n", result.Structure["overallBias"]))
	sb.WriteString(fmt.Sprintf("  Price: %v | Bias: %s\n", result.Market.LastPrice, result.Market.Bias))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(MTFSkill) }
