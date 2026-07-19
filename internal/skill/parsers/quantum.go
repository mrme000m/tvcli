package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var QuantumSkill = &skill.Skill{
	Name:     "quantum",
	Synopsis: "EMA Ribbon [Krypt] — 8-layer EMA ribbon alignment",
	PineID:   "PUB;GOYNhZP4X9VEbYA54MRIYU5FPvyr5IJB",
	Inputs: []skill.InputDef{
		{Name: "len1", TVInputID: "in_0", Type: "int", Default: 5},
		{Name: "len2", TVInputID: "in_1", Type: "int", Default: 10},
		{Name: "len3", TVInputID: "in_2", Type: "int", Default: 15},
		{Name: "len4", TVInputID: "in_3", Type: "int", Default: 20},
		{Name: "len5", TVInputID: "in_4", Type: "int", Default: 25},
		{Name: "len6", TVInputID: "in_5", Type: "int", Default: 30},
		{Name: "len7", TVInputID: "in_6", Type: "int", Default: 35},
		{Name: "len8", TVInputID: "in_7", Type: "int", Default: 40},
	},
	Presets: map[string]map[string]any{
		"default":  {},
		"scalping": {"len1": 3, "len2": 5, "len3": 8, "len4": 10, "len5": 12, "len6": 15, "len7": 18, "len8": 20},
		"swing":    {"len1": 20, "len2": 40, "len3": 60, "len4": 80, "len5": 100, "len6": 120, "len7": 150, "len8": 200},
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
	ma1 := toFloat(getField(last, []string{"MA_1", "plot_0"}))
	ma2 := toFloat(getField(last, []string{"MA_2", "plot_1"}))
	ma3 := toFloat(getField(last, []string{"MA_3", "plot_2"}))
	ma4 := toFloat(getField(last, []string{"MA_4", "plot_3"}))
	ma5 := toFloat(getField(last, []string{"MA_5", "plot_4"}))
	ma6 := toFloat(getField(last, []string{"MA_6", "plot_5"}))
	ma7 := toFloat(getField(last, []string{"MA_7", "plot_6"}))
	ma8 := toFloat(getField(last, []string{"MA_8", "plot_7"}))
	price := toFloat(getField(last, []string{"Close", "close"}))
	if price == 0 {
		price = ma1
	}

	// Count aligned EMAs (all ascending = bullish, all descending = bearish)
	aligned := 0
	if ma1 > ma2 && ma2 > ma3 && ma3 > ma4 && ma4 > ma5 && ma5 > ma6 && ma6 > ma7 && ma7 > ma8 {
		aligned = 8 // fully bullish
	} else if ma1 < ma2 && ma2 < ma3 && ma3 < ma4 && ma4 < ma5 && ma5 < ma6 && ma6 < ma7 && ma7 < ma8 {
		aligned = -8 // fully bearish
	} else {
		// Count individual crossovers
		pairs := [][2]float64{{ma1, ma2}, {ma2, ma3}, {ma3, ma4}, {ma4, ma5}, {ma5, ma6}, {ma6, ma7}, {ma7, ma8}}
		for _, p := range pairs {
			if p[0] > p[1] {
				aligned++
			} else {
				aligned--
			}
		}
	}

	bias := "neutral"
	if aligned >= 6 {
		bias = "bullish"
	} else if aligned <= -6 {
		bias = "bearish"
	}

	// Ribbon spread as score
	spread := 0.0
	if ma8 > 0 {
		spread = math.Abs(ma1-ma8) / ma8 * 100
	}
	score := math.Min(spread, 5.0)

	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	if math.Abs(float64(aligned)) >= 6 {
		agenticScore += 0.2
	}
	if bias != "neutral" {
		agenticScore += 0.15
	}
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	if math.Abs(float64(aligned)) >= 6 && bias != "neutral" {
		dir := "long"
		if bias == "bearish" {
			dir = "short"
		}
		scoreNorm := score / 5.0
		if scoreNorm > 1 {
			scoreNorm = 1
		}
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "ribbon_alignment", Direction: dir,
			Confidence: confidenceLabel(scoreNorm), ConfluenceScore: round2(scoreNorm),
			Rationale: fmt.Sprintf("Alignment=%d Spread=%.1f%% %s", aligned, spread, bias),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "quantum-ribbon",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{
			"alignment": aligned, "spread": round2(spread), "score": round2(score),
			"ma1": round2(ma1), "ma2": round2(ma2), "ma3": round2(ma3), "ma4": round2(ma4),
			"ma5": round2(ma5), "ma6": round2(ma6), "ma7": round2(ma7), "ma8": round2(ma8),
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Alignment: %d/8 | Spread: %.1f%% | Bias: %s", aligned, spread, bias),
			PrimaryOpp:      primaryOppFromOpps(opps),
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatQuantum(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  EMA RIBBON [KRYPT]\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Alignment: %v/8 | Spread: %v%% | Bias: %s\n", result.Structure["alignment"], result.Structure["spread"], result.Market.Bias))
	sb.WriteString(fmt.Sprintf("  MA1: %v | MA8: %v\n", result.Structure["ma1"], result.Structure["ma8"]))
	sb.WriteString(fmt.Sprintf("  Price: %v\n", result.Market.LastPrice))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("\n  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(QuantumSkill) }
