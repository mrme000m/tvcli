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
			Narrative: skill.Narrative{MarketStructure: "No data", Warnings: []string{"No period data"}}}
	}
	last := latestClosed(periods)

	// The Quantum Ribbon script exposes the first EMA as "Plot" and the rest as
	// "Plot_2" ... "Plot_10". Buy/Sell/Stop/Target are sparse 0/1 signal flags.
	price := toFloat(getField(last, []string{"Plot", "plot"}))
	if price == 0 {
		price = toFloat(getField(last, []string{"plot_0"}))
	}
	buySignal := toFloat(getField(last, []string{"Buy_Signal", "buySignal", "plot_15"})) != 0
	sellSignal := toFloat(getField(last, []string{"Sell_Signal", "sellSignal", "plot_16"})) != 0
	stopHit := toFloat(getField(last, []string{"Stop_Hit", "stopHit", "plot_17"})) != 0
	targetHit := toFloat(getField(last, []string{"Target_Hit", "targetHit", "plot_18"})) != 0

	// Ribbon alignment: collect the named EMA levels if present.
	ribbon := map[string]float64{}
	for _, k := range []string{"Plot", "Plot_2", "Plot_3", "Plot_4", "Plot_5", "Plot_6", "Plot_7", "Plot_8", "Plot_9", "Plot_10"} {
		if v := toFloat(getField(last, []string{k})); v != 0 {
			ribbon[k] = v
		}
	}

	bias := "neutral"
	if buySignal {
		bias = "bullish"
	} else if sellSignal {
		bias = "bearish"
	}

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if price > 0 { agenticScore += 0.15 }
	if len(ribbon) > 1 { agenticScore += 0.15 }
	if buySignal || sellSignal { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	opps := []skill.Opportunity{}
	if buySignal || sellSignal {
		dir := "long"
		if sellSignal { dir = "short" }
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "quantum_ribbon_signal", Direction: dir,
			Confidence: "HIGH", ConfluenceScore: 0.8,
			Rationale: fmt.Sprintf("Quantum ribbon %s signal. Price=%.2f", dir, price),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "quantum-ribbon",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{
			"price": price, "bias": bias,
			"buySignal": buySignal, "sellSignal": sellSignal,
			"stopHit": stopHit, "targetHit": targetHit,
			"ribbon": ribbon,
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Price: %.2f | Bias: %s | Ribbon lines: %d", price, bias, len(ribbon)),
			PrimaryOpp:      primaryOppFromOpps(opps),
			Warnings:        []string{},
		},
		Validation:  skill.Validation{Passed: true, Warnings: []string{}},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
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
