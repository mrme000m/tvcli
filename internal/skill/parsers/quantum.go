package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var QuantumSkill = &skill.Skill{
	Name:     "quantum",
	Synopsis: "Quantum Ribbon Lite — 5-layer EMA ribbon alignment",
	PineID:   "PUB;91e003af510345f299e5846773538206",
	Inputs: []skill.InputDef{
		{Name: "i_sensitivity", TVInputID: "in_0", Type: "int", Default: 5},
		{Name: "i_stop_distance", TVInputID: "in_1", Type: "string", Default: "Normal"},
		{Name: "i_target_rr", TVInputID: "in_2", Type: "string", Default: "2R"},
		{Name: "i_show_table", TVInputID: "in_3", Type: "bool", Default: true},
	},
	ParseOutput: parseQuantum,
	FormatText:  formatQuantum,
}

func parseQuantum(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "quantum-ribbon",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	ribbonState := toFloat(getField(last, []string{"RibbonState", "ribbonState", "State"}))

	bias := "neutral"
	if ribbonState > 0 { bias = "bullish" } else if ribbonState < 0 { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(ribbonState) > 0 { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "quantum-ribbon",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"ribbonState": ribbonState, "price": price, "bias": bias},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Ribbon State: %.0f | Bias: %s", ribbonState, bias)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatQuantum(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  QUANTUM RIBBON\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Ribbon State: %v\n", result.Structure["ribbonState"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(QuantumSkill) }
