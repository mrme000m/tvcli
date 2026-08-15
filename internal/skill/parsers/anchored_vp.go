package parsers

import (
	"fmt"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
	"github.com/ch99q/tvcli/pkg/schema"
)

var AnchoredVPSkill = &skill.Skill{
	Name:     "anchored-vp",
	Synopsis: "Anchored Volume Profile — k-means clusters and POC levels",
	PineID:   "PUB;92974e0a3cfb481eaf058cdab9f925a3",
	// Graphics-only: emits no period/plot data, so the parser reads the
	// graphic layer (cluster boxes, POC line, labels).
	RequiresGraphic: true,
	KnownBroken:    "Graphics-only; returned an empty graphic on OANDA:XAUUSD and BINANCE:BTCUSDT (likely anchor-input / symbol dependent). Parser handles the table/labels when present.",
	Inputs: []skill.InputDef{
		{Name: "kInput", TVInputID: "in_3", Type: "int", Default: 5},
		{Name: "iters", TVInputID: "in_4", Type: "int", Default: 50},
		{Name: "rowsInput", TVInputID: "in_5", Type: "int", Default: 20},
		{Name: "vpWidth", TVInputID: "in_6", Type: "int", Default: 40},
		{Name: "showDots", TVInputID: "in_8", Type: "bool", Default: true},
	},
	ParseOutput:     parseAnchoredVP,
	ParseWithSchema: parseAnchoredVPSchema,
	FormatText:      formatAnchoredVP,
}

func parseAnchoredVP(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseAnchoredVPSchema(periods, graphic, nil, tf, symbol, args)
}

func parseAnchoredVPSchema(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) > 0 {
		last := latestClosed(periods)
		price := SchemaFloat(last, sch, "Close", "close")
		poc := SchemaFloat(last, sch, "POC", "poc", "PointOfControl")

		bias := "neutral"
		if price > 0 && poc > 0 {
			if price > poc { bias = "bullish" } else { bias = "bearish" }
		}

		agenticScore := 0.2
		if poc > 0 { agenticScore += 0.2 }
		if agenticScore > 0.99 { agenticScore = 0.99 }

		return skill.SkillResult{
			Status: "ok", Workflow: "anchored-clusters-vp",
			Market: skill.MarketData{LastPrice: price, Bias: bias},
			Structure: map[string]any{"poc": poc, "price": price, "bias": bias},
			Opportunities: []skill.Opportunity{},
			Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("POC: %.2f | Price: %.2f | Bias: %s", poc, price, bias)},
			Validation:    skill.Validation{Passed: true},
			Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
		}
	}

	// Graphics-only path: the volume profile is drawn as cluster boxes, a POC
	// line, and labels. Surface any dashboard table or label-derived levels.
	if dash, ok := ResolveGraphicDashboard(graphic); ok {
		labels := GraphicLabels(graphic)
		price := latestGraphicPrice(graphic)
		return skill.SkillResult{
			Status: "ok", Workflow: "anchored-clusters-vp",
			Market: skill.MarketData{LastPrice: price},
			Structure: map[string]any{
				"graphic": dash,
				"labels":  labels,
				"price":   price,
			},
			Opportunities: []skill.Opportunity{},
			Narrative:     skill.Narrative{MarketStructure: fmt.Sprintf("Anchored VP: %d table(s), %d label(s)", dash["tables"], len(labels))},
			Validation:    skill.Validation{Passed: true},
			Conformance:   skill.Conformance{HasValidData: true, AgenticScore: 0.5},
		}
	}
	return skill.SkillResult{Status: "no_data", Workflow: "anchored-clusters-vp",
		Narrative: skill.Narrative{MarketStructure: "No data (no periods and no graphic)"}}
}

func formatAnchoredVP(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  ANCHORED VOLUME PROFILE\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  POC: %v\n", result.Structure["poc"]))
	sb.WriteString(fmt.Sprintf("  Price: %v | Bias: %s\n", result.Market.LastPrice, result.Market.Bias))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(AnchoredVPSkill) }
