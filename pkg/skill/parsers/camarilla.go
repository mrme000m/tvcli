package parsers

import (
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var CamarillaSkill = &skill.Skill{
	Name:     "camarilla",
	Synopsis: "Camarilla Pivot Points V2 — 8 daily support/resistance levels",
	PineID:   "PUB;ZiLxYyT9JQ9gmWO4ml5z9HZIYffMpISu",
	Inputs: []skill.InputDef{
		{Name: "resolution", TVInputID: "in_0", Type: "string", Default: "D"},
		{Name: "width", TVInputID: "in_1", Type: "int", Default: 1},
	},
	Presets: map[string]map[string]any{
		"default":  {"resolution": "D"},
		"weekly":   {"resolution": "W"},
		"monthly":  {"resolution": "M"},
	},
	ParseOutput: parseCamarilla,
	FormatText:  formatCamarilla,
}

func parseCamarilla(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "camarilla-pivots",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)

	// plot_0=H1, plot_1=H2, plot_2=H3, plot_3=H4 (resistance)
	// plot_4=L1, plot_5=L2, plot_6=L3, plot_7=L4 (support)
	h1 := toFloat(getField(last, []string{"H1", "plot_0"}))
	h2 := toFloat(getField(last, []string{"H2", "plot_1"}))
	h3 := toFloat(getField(last, []string{"H3", "plot_2"}))
	h4 := toFloat(getField(last, []string{"H4", "plot_3"}))
	l1 := toFloat(getField(last, []string{"L1", "plot_4"}))
	l2 := toFloat(getField(last, []string{"L2", "plot_5"}))
	l3 := toFloat(getField(last, []string{"L3", "plot_6"}))
	l4 := toFloat(getField(last, []string{"L4", "plot_7"}))

	// Get price
	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	if price == 0 {
		price = (h1 + l1) / 2 // fallback to midpoint
	}

	// Determine which level price is closest to
	levels := []struct{ name string; val float64; kind string }{
		{"H4", h4, "resistance"}, {"H3", h3, "resistance"},
		{"H2", h2, "resistance"}, {"H1", h1, "resistance"},
		{"L1", l1, "support"}, {"L2", l2, "support"},
		{"L3", l3, "support"}, {"L4", l4, "support"},
	}

	closestLevel := ""
	closestDist := 1e9
	closestKind := ""
	for _, l := range levels {
		dist := abs(price - l.val)
		if dist < closestDist {
			closestDist = dist
			closestLevel = l.name
			closestKind = l.kind
		}
	}

	// Price position relative to levels
	priceZone := "between H1/L1"
	if price > h3 { priceZone = "above H3 (extended bullish)" }
	if price > h4 { priceZone = "above H4 (extreme bullish)" }
	if price < l3 { priceZone = "below L3 (extended bearish)" }
	if price < l4 { priceZone = "below L4 (extreme bearish)" }

	// Bias: above H1 = bullish, below L1 = bearish
	bias := "neutral"
	if price > h1 { bias = "bullish" }
	if price < l1 { bias = "bearish" }

	// Agentic score
	score := 0.55
	if price > h3 || price < l3 { score += 0.15 } // Extended move
	if closestDist/price < 0.001 { score += 0.15 } // Near a level
	if price > h1 || price < l1 { score += 0.1 }
	if score > 1.0 { score = 1.0 }

	structure := map[string]any{
		"H4": h4, "H3": h3, "H2": h2, "H1": h1,
		"L1": l1, "L2": l2, "L3": l3, "L4": l4,
		"price":          price,
		"closestLevel":   closestLevel,
		"closestKind":    closestKind,
		"closestDist":    closestDist,
		"priceZone":      priceZone,
	}

	opp := []skill.Opportunity{}
	if closestDist/price < 0.002 {
		dir := "long"
		if closestKind == "resistance" { dir = "short" }
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "Camarilla " + closestLevel + " Test",
			Direction: dir, Confidence: "medium",
			DistanceFromPrice: closestDist,
			Rationale: fmt.Sprintf("Price near %s level (%.2f)", closestLevel, closestDist),
		})
	}

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "camarilla-pivots",
		Market:        skill.MarketData{Bias: bias, LastPrice: price},
		Structure:     structure,
		Opportunities: opp,
		Narrative: skill.Narrative{
			MarketStructure: priceZone,
			PrimaryOpp:      firstOppText(opp),
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: score},
	}
}

func formatCamarilla(result skill.SkillResult) string {
	s := result.Structure
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("  Resistance: H4=%.2f  H3=%.2f  H2=%.2f  H1=%.2f\n",
		toFloat(s["H4"]), toFloat(s["H3"]), toFloat(s["H2"]), toFloat(s["H1"])))
	sb.WriteString(fmt.Sprintf("  Price: %.2f  →  %s\n", toFloat(s["price"]), s["priceZone"]))
	sb.WriteString(fmt.Sprintf("  Support:    L1=%.2f  L2=%.2f  L3=%.2f  L4=%.2f\n",
		toFloat(s["L1"]), toFloat(s["L2"]), toFloat(s["L3"]), toFloat(s["L4"])))
	if s["closestLevel"] != "" {
		sb.WriteString(fmt.Sprintf("  Nearest: %s (%.2f away)\n", s["closestLevel"], toFloat(s["closestDist"])))
	}
	return sb.String()
}

func init() { skill.Register(CamarillaSkill) }
