package parsers

import (
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var ChoppinessSkill = &skill.Skill{
	Name:     "choppiness",
	Synopsis: "Choppiness Index — market regime detection (trending vs choppy)",
	PineID:   "PUB;116",
	Inputs: []skill.InputDef{
		{Name: "length", TVInputID: "in_0", Type: "int", Default: 14},
		{Name: "doAverage", TVInputID: "in_1", Type: "bool", Default: true},
		{Name: "avgLength", TVInputID: "in_2", Type: "int", Default: 4},
		{Name: "extremeChop", TVInputID: "in_3", Type: "float", Default: 61.8},
		{Name: "midline", TVInputID: "in_4", Type: "float", Default: 50.0},
		{Name: "trending", TVInputID: "in_5", Type: "float", Default: 38.2},
	},
	Presets: map[string]map[string]any{
		"default":  {"length": 14, "avgLength": 4},
		"scalping": {"length": 7, "avgLength": 3},
		"swing":    {"length": 28, "avgLength": 8},
	},
	ParseOutput: parseChoppiness,
	FormatText:  formatChoppiness,
}

func parseChoppiness(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "choppiness-index",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	bars := historicalBars(periods)

	// plot_0 = CHOP, plot_1 = CHOP average (EMA)
	chop := toFloat(getField(last, []string{"plot_0"}))
	chopAvg := toFloat(getField(last, []string{"plot_1"}))

	// Thresholds (default: >61.8 = choppy, <38.2 = trending)
	extremeChop := 61.8
	trending := 38.2
	if v := args["in_3"]; v != "" { extremeChop = toFloat(v) }
	if v := args["in_5"]; v != "" { trending = toFloat(v) }

	// Regime classification
	regime := "transitional"
	if chop > extremeChop { regime = "choppy" }
	if chop < trending { regime = "trending" }

	// Trend direction proxy: is CHOP rising or falling?
	chopPrev := chop
	if len(bars) > 5 {
		chopPrev = toFloat(getField(bars[5], []string{"plot_0"}))
	}
	chopDirection := "flat"
	if chop > chopPrev + 1 { chopDirection = "rising" }
	if chop < chopPrev - 1 { chopDirection = "falling" }

	// Regime transition detection
	prevRegime := "transitional"
	if chopPrev > extremeChop { prevRegime = "choppy" }
	if chopPrev < trending { prevRegime = "trending" }
	regimeChange := regime != prevRegime

	// Count bars in current regime
	regimeBars := 0
	for _, p := range bars {
		c := toFloat(getField(p, []string{"plot_0"}))
		r := "transitional"
		if c > extremeChop { r = "choppy" }
		if c < trending { r = "trending" }
		if r == regime { regimeBars++ } else { break }
	}

	// Bias: trending = follow trend direction, choppy = mean-reversion
	bias := "neutral"
	if regime == "trending" {
		// Use CHOP direction: falling = trend strengthening
		if chopDirection == "falling" { bias = "trending" }
	}

	// Agentic score
	score := 0.5
	if regime == "trending" { score += 0.2 } // Clear regime = more actionable
	if regime == "choppy" { score += 0.15 }  // Choppy = avoid trend strategies
	if regimeChange { score += 0.15 }         // Regime shift = important signal
	if regimeBars > 10 { score += 0.05 }      // Stable regime
	if score > 1.0 { score = 1.0 }

	structure := map[string]any{
		"chop":          chop,
		"chopAvg":       chopAvg,
		"regime":        regime,
		"chopDirection": chopDirection,
		"regimeChange":  regimeChange,
		"regimeBars":   regimeBars,
		"extremeChop":   extremeChop,
		"trending":      trending,
	}

	strategy := "mean-reversion"
	if regime == "trending" { strategy = "trend-following" }
	if regime == "transitional" { strategy = "caution" }

	narrative := fmt.Sprintf("Market: %s (CHOP=%.1f) — use %s strategies", regime, chop, strategy)
	if regimeChange {
		narrative = fmt.Sprintf("Regime shift to %s (CHOP=%.1f) — adjust strategy", regime, chop)
	}

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "choppiness-index",
		Market:        skill.MarketData{Bias: bias},
		Structure:     structure,
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{
			MarketStructure: narrative,
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: score},
	}
}

func formatChoppiness(result skill.SkillResult) string {
	s := result.Structure
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("  CHOP: %.1f (avg: %.1f) → %s\n",
		toFloat(s["chop"]), toFloat(s["chopAvg"]), s["regime"]))
	sb.WriteString(fmt.Sprintf("  Direction: %s | Bars in regime: %d\n",
		s["chopDirection"], toInt(s["regimeBars"])))
	if regimeChange, _ := s["regimeChange"].(bool); regimeChange {
		sb.WriteString("  ⚡ Regime shift detected — adjust strategy\n")
	}
	return sb.String()
}

func init() { skill.Register(ChoppinessSkill) }
