package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

// LiqSweepSkill wraps the public "Institutional Liquidity Sweep & Volume Breakout [SMC]"
// Pine Script. It exposes 0/1 sweep-shape events per bar and uses the recent sweep
// distribution to produce a directional bias and a high-confidence opportunity
// when the latest closed bar fires a sweep.
var LiqSweepSkill = &skill.Skill{
	Name:     "liq-sweep",
	Synopsis: "Institutional Liquidity Sweep & Volume Breakout — SMC sweep detection",
	PineID:   "PUB;b9372355c2e6483f952ca49a21d2ebbb",
	Inputs: []skill.InputDef{
		{Name: "swingLookback", TVInputID: "in_0", Type: "int", Default: 20},
		{Name: "volumeMultiplier", TVInputID: "in_1", Type: "float", Default: 1.5},
		{Name: "showLabels", TVInputID: "in_2", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"default":  {"swingLookback": 20, "volumeMultiplier": 1.5},
		"scalping": {"swingLookback": 10, "volumeMultiplier": 1.2},
		"swing":    {"swingLookback": 50, "volumeMultiplier": 2.0},
	},
	ParseOutput: parseLiqSweep,
	FormatText:  formatLiqSweep,
}

func parseLiqSweep(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{
			Status:    "no_data",
			Workflow:  "liquidity-sweep",
			Narrative: skill.Narrative{MarketStructure: "No data"},
		}
	}

	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	if price == 0 {
		price = latestGraphicPrice(graphic)
	}
	bullNow := toFloat(getField(last, []string{"Bullish_Sweep_Shape"})) == 1
	bearNow := toFloat(getField(last, []string{"Bearish_Sweep_Shape"})) == 1

	bullCount, bearCount := 0, 0
	for _, p := range historicalBars(periods) {
		if toFloat(getField(p, []string{"Bullish_Sweep_Shape"})) == 1 {
			bullCount++
		}
		if toFloat(getField(p, []string{"Bearish_Sweep_Shape"})) == 1 {
			bearCount++
		}
	}

	bias := "neutral"
	if bullCount > bearCount {
		bias = "bullish"
	} else if bearCount > bullCount {
		bias = "bearish"
	}

	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	total := bullCount + bearCount
	if total > 0 {
		agenticScore += 0.2
		dominant := math.Max(float64(bullCount), float64(bearCount))
		agenticScore += 0.2 * (dominant / float64(total))
	}
	if bullNow || bearNow {
		agenticScore += 0.15
	}
	agenticScore = math.Min(agenticScore, 0.99)

	opps := make([]skill.Opportunity, 0)
	if bullNow {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "liquidity_sweep",
			Direction:       "long",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.75),
			Rationale:       "Bullish liquidity sweep detected with volume breakout",
		})
	} else if bearNow {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "liquidity_sweep",
			Direction:       "short",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.75),
			Rationale:       "Bearish liquidity sweep detected with volume breakout",
		})
	}

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "liquidity-sweep",
		Market:   skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{
			"bullSweeps":     bullCount,
			"bearSweeps":     bearCount,
			"totalSweeps":    total,
			"latestSweep":    latestSweepLabel(bullNow, bearNow),
			"sweepDominance": sweepDominance(bullCount, bearCount),
			"price":          price,
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Sweeps: bull=%d bear=%d | Bias: %s", bullCount, bearCount, bias),
			PrimaryOpp:      primaryOpp(opps),
			Warnings:        []string{},
		},
		Validation:    skill.Validation{Passed: true},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func latestSweepLabel(bull, bear bool) string {
	if bull {
		return "bullish"
	}
	if bear {
		return "bearish"
	}
	return "none"
}

func sweepDominance(bull, bear int) string {
	if bull > bear {
		return "bullish"
	}
	if bear > bull {
		return "bearish"
	}
	return "neutral"
}

func primaryOpp(opps []skill.Opportunity) string {
	if len(opps) == 0 {
		return ""
	}
	o := opps[0]
	return fmt.Sprintf("%s %s (%s)", o.Direction, o.Setup, o.Confidence)
}

func formatLiqSweep(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  LIQUIDITY SWEEP & VOLUME BREAKOUT\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Bull sweeps: %v\n", result.Structure["bullSweeps"]))
	sb.WriteString(fmt.Sprintf("  Bear sweeps: %v\n", result.Structure["bearSweeps"]))
	sb.WriteString(fmt.Sprintf("  Latest:      %v\n", result.Structure["latestSweep"]))
	sb.WriteString(fmt.Sprintf("  Bias:        %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(LiqSweepSkill) }
