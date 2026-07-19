package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var VGapsSkill = &skill.Skill{
	Name:     "vgaps",
	Synopsis: "Volume Gaps & Imbalances — zero-volume voids and order flow",
	PineID:   "PUB;ff1a0136336340f38e908eeb12ea33aa",
	Inputs: []skill.InputDef{
		{Name: "prd", TVInputID: "in_0", Type: "int", Default: 200},
		{Name: "rows", TVInputID: "in_1", Type: "int", Default: 50},
		{Name: "src", TVInputID: "in_2", Type: "source", Default: "hlc3"},
		{Name: "width", TVInputID: "in_3", Type: "int", Default: 100},
		{Name: "sum_sections", TVInputID: "in_7", Type: "int", Default: 20},
		{Name: "sum_panel_w", TVInputID: "in_8", Type: "int", Default: 40},
		{Name: "sum_gap_x", TVInputID: "in_9", Type: "int", Default: 4},
		{Name: "delta_min_frac", TVInputID: "in_15", Type: "float", Default: 0.2},
	},
	Presets: map[string]map[string]any{
		"default":  {"prd": 200, "rows": 50},
		"scalping": {"prd": 100, "rows": 30},
		"swing":    {"prd": 400, "rows": 80},
	},
	ParseOutput: parseVGaps,
	FormatText:  formatVGaps,
}

func parseVGaps(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "trend-following-gap-rejection",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close", "plot_0"}))
	gapCount := toFloat(getField(last, []string{"GapCount", "gapCount", "Gaps", "plot_1"}))

	bias := "neutral"
	if gapCount > 0 { bias = "bullish" } else if gapCount < 0 { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(gapCount) > 0 { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "trend-following-gap-rejection",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"gapCount": gapCount, "price": price},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Gaps: %.0f | Price: %.2f", gapCount, price)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatVGaps(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  VOLUME GAPS & IMBALANCES\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Gaps: %v\n", result.Structure["gapCount"]))
	sb.WriteString(fmt.Sprintf("  Price: %v | Bias: %s\n", result.Market.LastPrice, result.Market.Bias))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(VGapsSkill) }
