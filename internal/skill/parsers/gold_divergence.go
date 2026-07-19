package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

// GoldDivergenceSkill wraps a public RSI-based divergence Pine Script focused on
// gold/XAUUSD. It surfaces regular bullish/bearish divergences and the current
// RSI value; price is not emitted as a plot, so the parser reports the RSI and
// divergence counts instead.
var GoldDivergenceSkill = &skill.Skill{
	Name:     "gold-divergence",
	Synopsis: "Gold RSI divergence — bullish/bearish divergences with RSI",
	PineID:   "PUB;779d25a800b242cf9e2ecbe6f350c366",
	Inputs: []skill.InputDef{
		{Name: "rsiLength", TVInputID: "in_0", Type: "int", Default: 14},
		{Name: "showDivergence", TVInputID: "in_5", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"default":  {"rsiLength": 14, "showDivergence": true},
		"scalping": {"rsiLength": 7, "showDivergence": true},
		"swing":    {"rsiLength": 21, "showDivergence": true},
	},
	ParseOutput: parseGoldDivergence,
	FormatText:  formatGoldDivergence,
}

// sentinelValue marks "no divergence" in the Pine Script output.
const divergenceSentinel = 1e100

func isRealDivergenceValue(v float64) bool {
	return !math.IsNaN(v) && v != divergenceSentinel
}

func parseGoldDivergence(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{
			Status:    "no_data",
			Workflow:  "gold-divergence",
			Narrative: skill.Narrative{MarketStructure: "No data"},
		}
	}

	last := latestClosed(periods)
	rsi := toFloat(getField(last, []string{"RSI", "plot_0"}))
	bullNow := isRealDivergenceValue(toFloat(getField(last, []string{"Bullish_Divergence", "plot_1"})))
	bearNow := isRealDivergenceValue(toFloat(getField(last, []string{"Bearish_Divergence", "plot_2"})))

	bullDivs, bearDivs := 0, 0
	for _, p := range historicalBars(periods) {
		if isRealDivergenceValue(toFloat(getField(p, []string{"Bullish_Divergence", "plot_1"}))) {
			bullDivs++
		}
		if isRealDivergenceValue(toFloat(getField(p, []string{"Bearish_Divergence", "plot_2"}))) {
			bearDivs++
		}
	}

	bias := "neutral"
	if bullDivs > bearDivs {
		bias = "bullish"
	} else if bearDivs > bullDivs {
		bias = "bearish"
	}

	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	if rsi > 0 {
		agenticScore += 0.1
	}
	total := bullDivs + bearDivs
	if total > 0 {
		agenticScore += 0.15
		dominant := math.Max(float64(bullDivs), float64(bearDivs))
		agenticScore += 0.15 * (dominant / float64(total))
	}
	if bullNow || bearNow {
		agenticScore += 0.15
	}
	agenticScore = math.Min(agenticScore, 0.99)

	opps := make([]skill.Opportunity, 0)
	if bullNow {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "rsi_divergence",
			Direction:       "long",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.72),
			Rationale:       "Bullish RSI divergence detected",
		})
	} else if bearNow {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "rsi_divergence",
			Direction:       "short",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.72),
			Rationale:       "Bearish RSI divergence detected",
		})
	}

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "gold-divergence",
		Market:   skill.MarketData{LastPrice: 0, Bias: bias},
		Structure: map[string]any{
			"rsi":              round2(rsi),
			"bullDivergences":  bullDivs,
			"bearDivergences":  bearDivs,
			"totalDivergences": total,
			"latestDivergence": latestSpikeLabel(bullNow, bearNow),
			"divergenceBias":   sweepDominance(bullDivs, bearDivs),
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("RSI: %.2f | Divergences: bull=%d bear=%d | Bias: %s", rsi, bullDivs, bearDivs, bias),
			PrimaryOpp:      primaryOpp(opps),
			Warnings:        []string{},
		},
		Validation:    skill.Validation{Passed: true},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatGoldDivergence(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  GOLD DIVERGENCE — RSI\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  RSI:              %.2f\n", result.Structure["rsi"]))
	sb.WriteString(fmt.Sprintf("  Bull divergences: %v\n", result.Structure["bullDivergences"]))
	sb.WriteString(fmt.Sprintf("  Bear divergences: %v\n", result.Structure["bearDivergences"]))
	sb.WriteString(fmt.Sprintf("  Latest:           %v\n", result.Structure["latestDivergence"]))
	sb.WriteString(fmt.Sprintf("  Bias:             %s\n", result.Market.Bias))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(GoldDivergenceSkill) }
