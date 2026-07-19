package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var ICTSkill = &skill.Skill{
	Name:     "ict",
	Synopsis: "ICT Auto-Validated SMC — full ICT system with OTE zones",
	PineID:   "PUB;789a5c79bfe9443585da09e85ece73de",
	Inputs: []skill.InputDef{
		{Name: "swingLen", TVInputID: "in_0", Type: "int", Default: 10},
		{Name: "internalLen", TVInputID: "in_1", Type: "int", Default: 5},
		{Name: "showSwings", TVInputID: "in_2", Type: "bool", Default: true},
		{Name: "showStructure", TVInputID: "in_3", Type: "bool", Default: true},
		{Name: "useHTF", TVInputID: "in_6", Type: "bool", Default: true},
		{Name: "htfTimeframe", TVInputID: "in_7", Type: "timeframe", Default: 240},
		{Name: "showOB", TVInputID: "in_10", Type: "bool", Default: true},
		{Name: "showFVG", TVInputID: "in_19", Type: "bool", Default: true},
		{Name: "showBreakers", TVInputID: "in_15", Type: "bool", Default: true},
		{Name: "showOTE", TVInputID: "in_49", Type: "bool", Default: true},
		{Name: "enableSignals", TVInputID: "in_56", Type: "bool", Default: true},
		{Name: "minSigScore", TVInputID: "in_57", Type: "int", Default: 4},
	},
	ParseOutput: parseICT,
	FormatText:  formatICT,
}

func parseICT(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "ict-smc-structure",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	bosCount := toFloat(getField(last, []string{"BOSCount", "bosCount"}))
	chochCount := toFloat(getField(last, []string{"CHoCHCount", "chochCount"}))

	bias := "neutral"
	if bosCount > chochCount { bias = "bullish" }
	if chochCount > bosCount { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if bosCount > 0 || chochCount > 0 { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "ict-smc-structure",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"bosCount": bosCount, "chochCount": chochCount},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("BOS: %.0f | CHoCH: %.0f | Bias: %s", bosCount, chochCount, bias)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatICT(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  ICT AUTO-VALIDATED SMC\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  BOS: %v | CHoCH: %v\n", result.Structure["bosCount"], result.Structure["chochCount"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(ICTSkill) }
