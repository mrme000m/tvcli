package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var SMCSkill = &skill.Skill{
	Name:     "smc",
	Synopsis: "Smart Money Concepts — BOS/CHoCH, FVG, Order Blocks",
	PineID:   "PUB;6daafb2cabe6419d98ae25229d2327f8",
	Inputs: []skill.InputDef{
		{Name: "showStructureInput", TVInputID: "in_10", Type: "bool", Default: true},
		{Name: "showSwingBullInput", TVInputID: "in_11", Type: "string", Default: "ALL"},
		{Name: "showSwingBearInput", TVInputID: "in_13", Type: "string", Default: "ALL"},
		{Name: "showInternalOrderBlocksInput", TVInputID: "in_19", Type: "bool", Default: true},
		{Name: "showSwingOrderBlocksInput", TVInputID: "in_21", Type: "bool", Default: false},
		{Name: "showFairValueGapsInput", TVInputID: "in_33", Type: "bool", Default: true},
		{Name: "fairValueGapsThresholdInput", TVInputID: "in_34", Type: "bool", Default: true},
		{Name: "showEqualHighsLowsInput", TVInputID: "in_29", Type: "bool", Default: true},
		{Name: "swingsLengthInput", TVInputID: "in_17", Type: "int", Default: 50},
	},
	ParseOutput: parseSMC,
	FormatText:  formatSMC,
}

func parseSMC(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "smart-money-concepts",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	bosCount := toFloat(getField(last, []string{"BOSCount", "bosCount"}))
	chochCount := toFloat(getField(last, []string{"CHoCHCount", "chochCount"}))
	fvgCount := toFloat(getField(last, []string{"FVGCount", "fvgCount"}))
	obCount := toFloat(getField(last, []string{"OBCount", "obCount"}))

	bias := "neutral"
	if bosCount > chochCount { bias = "bullish" }
	if chochCount > bosCount { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if bosCount > 0 || chochCount > 0 { agenticScore += 0.15 }
	if fvgCount > 0 { agenticScore += 0.1 }
	if obCount > 0 { agenticScore += 0.1 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "smart-money-concepts",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"bosCount": bosCount, "chochCount": chochCount, "fvgCount": fvgCount, "obCount": obCount},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("BOS: %.0f | CHoCH: %.0f | FVG: %.0f | OB: %.0f", bosCount, chochCount, fvgCount, obCount)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatSMC(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  SMART MONEY CONCEPTS\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  BOS: %v | CHoCH: %v\n", result.Structure["bosCount"], result.Structure["chochCount"]))
	sb.WriteString(fmt.Sprintf("  FVG: %v | OB: %v\n", result.Structure["fvgCount"], result.Structure["obCount"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(SMCSkill) }
