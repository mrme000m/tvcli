package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var GoldenSkill = &skill.Skill{
	Name:     "golden",
	Synopsis: "Golden Rule Strategy — multi-TF weekly/daily/4H alignment",
	PineID:   "PUB;6daafb2cabe6419d98ae25229d2327f8",
	Inputs: []skill.InputDef{
		{Name: "showStructureInput", TVInputID: "in_10", Type: "bool", Default: true},
		{Name: "showFairValueGapsInput", TVInputID: "in_33", Type: "bool", Default: true},
		{Name: "showInternalOrderBlocksInput", TVInputID: "in_19", Type: "bool", Default: true},
		{Name: "swingsLengthInput", TVInputID: "in_17", Type: "int", Default: 50},
	},
	ParseOutput: parseGolden,
	FormatText:  formatGolden,
}

func parseGolden(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "golden-rule-strategy",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close", "plot_3"}))
	verdict := getField(last, []string{"Verdict", "verdict"})
	if verdict == nil {
		return skill.SkillResult{Status: "no_data", Workflow: "golden-rule-strategy",
			Narrative: skill.Narrative{MarketStructure: "No Verdict field; Pine script does not match Golden Rule expectation", Warnings: []string{"Verdict field missing — indicator is not the expected Golden Rule script"}}}
	}

	bias := "neutral"
	if verdict == "PASS" || verdict == "BULLISH" { bias = "bullish" }
	if verdict == "FAIL" || verdict == "BEARISH" { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if verdict != nil { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "golden-rule-strategy",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"verdict": verdict, "price": price},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Verdict: %v | Bias: %s", verdict, bias)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatGolden(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  GOLDEN RULE STRATEGY\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Verdict: %v\n", result.Structure["verdict"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(GoldenSkill) }
