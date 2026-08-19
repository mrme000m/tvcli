package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/schema"
)

// OrderFlowSkill wraps the "Volume Spike / Order Flow" public Pine Script.
// It exposes 0/1 volume-spike alert events per bar and summarises the recent
// bullish/bearish spike balance into a directional bias.
var OrderFlowSkill = &skill.Skill{
	Name:     "order-flow",
	Synopsis: "Volume spike order flow — bullish/bearish participation spikes",
	PineID:   "PUB;7uP2LNPDc8I150lUzs3Aqa5ju9usF0ZN",
	Inputs: []skill.InputDef{
		{Name: "vmaLength", TVInputID: "in_0", Type: "int", Default: 20},
		{Name: "volumeMultiplier", TVInputID: "in_1", Type: "int", Default: 500},
		{Name: "coinMaLength", TVInputID: "in_2", Type: "int", Default: 5},
		{Name: "showSells", TVInputID: "in_3", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"default":  {"vmaLength": 20, "volumeMultiplier": 500, "coinMaLength": 5, "showSells": true},
		"scalping": {"vmaLength": 10, "volumeMultiplier": 300, "coinMaLength": 5, "showSells": true},
		"swing":    {"vmaLength": 50, "volumeMultiplier": 700, "coinMaLength": 5, "showSells": true},
	},
	ParseOutput:     parseOrderFlow,
	ParseWithSchema: parseOrderFlowSchema,
	FormatText:      formatOrderFlow,
}

func parseOrderFlow(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	// ParseOutput has no schema available; delegate with a nil schema. A nil
	// schema makes SchemaField/SchemaFloat behave exactly like the old
	// getField lookup, so behavior is unchanged until ParseWithSchema runs.
	return parseOrderFlowSchema(periods, graphic, nil, tf, symbol, args)
}

func parseOrderFlowSchema(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{
			Status:    "no_data",
			Workflow:  "order-flow",
			Narrative: skill.Narrative{MarketStructure: "No data"},
		}
	}

	last := latestClosed(periods)

	bullNow := SchemaFloat(last, sch, "Buy", "bell", "plot_0", "plot_2") == 1
	bearNow := SchemaFloat(last, sch, "Sell", "sell", "plot_1", "plot_3") == 1

	bullSpikes, bearSpikes := 0, 0
	for _, p := range historicalBars(periods) {
		if SchemaFloat(p, sch, "Buy", "bell", "plot_0", "plot_2") == 1 {
			bullSpikes++
		}
		if SchemaFloat(p, sch, "Sell", "sell", "plot_1", "plot_3") == 1 {
			bearSpikes++
		}
	}

	bias := "neutral"
	if bullSpikes > bearSpikes {
		bias = "bullish"
	} else if bearSpikes > bullSpikes {
		bias = "bearish"
	}

	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	total := bullSpikes + bearSpikes
	if total > 0 {
		agenticScore += 0.2
		dominant := math.Max(float64(bullSpikes), float64(bearSpikes))
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
			Setup:           "volume_spike",
			Direction:       "long",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.75),
			Rationale:       "Bullish volume participation spike detected",
		})
	} else if bearNow {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "volume_spike",
			Direction:       "short",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.75),
			Rationale:       "Bearish volume participation spike detected",
		})
	}

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "order-flow",
		Market:   skill.MarketData{LastPrice: 0, Bias: bias},
		Structure: map[string]any{
			"bullSpikes":     bullSpikes,
			"bearSpikes":     bearSpikes,
			"totalSpikes":    total,
			"latestSpike":    latestSpikeLabel(bullNow, bearNow),
			"spikeDominance": sweepDominance(bullSpikes, bearSpikes),
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Volume spikes: bull=%d bear=%d | Bias: %s", bullSpikes, bearSpikes, bias),
			PrimaryOpp:      primaryOpp(opps),
			Warnings:        []string{},
		},
		Validation:    skill.Validation{Passed: true},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func latestSpikeLabel(bull, bear bool) string {
	if bull {
		return "bullish"
	}
	if bear {
		return "bearish"
	}
	return "none"
}

func formatOrderFlow(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  ORDER FLOW — VOLUME SPIKES\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Bull spikes: %v\n", result.Structure["bullSpikes"]))
	sb.WriteString(fmt.Sprintf("  Bear spikes: %v\n", result.Structure["bearSpikes"]))
	sb.WriteString(fmt.Sprintf("  Latest:      %v\n", result.Structure["latestSpike"]))
	sb.WriteString(fmt.Sprintf("  Bias:        %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// REMOVED: low signal quality (score 0.4)
// func init() { skill.Register(OrderFlowSkill) }
