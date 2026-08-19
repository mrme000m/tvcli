package parsers

import (
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var IchimokuSkill = &skill.Skill{
	Name:     "ichimoku",
	Synopsis: "Ichimoku Cloud (CM Enhanced V5) — trend, support/resistance via cloud",
	PineID:   "PUB;664",
	Inputs: []skill.InputDef{
		{Name: "tenkanLen", TVInputID: "in_0", Type: "int", Default: 9},
		{Name: "kijunLen", TVInputID: "in_1", Type: "int", Default: 26},
		{Name: "senkouBLen", TVInputID: "in_2", Type: "int", Default: 52},
		{Name: "displacement", TVInputID: "in_3", Type: "int", Default: 26},
		{Name: "showTenkan", TVInputID: "in_4", Type: "bool", Default: true},
		{Name: "showKijun", TVInputID: "in_5", Type: "bool", Default: true},
		{Name: "showChinkou", TVInputID: "in_6", Type: "bool", Default: true},
		{Name: "showCloud", TVInputID: "in_7", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"default":  {"tenkanLen": 9, "kijunLen": 26, "senkouBLen": 52, "displacement": 26},
		"scalping": {"tenkanLen": 5, "kijunLen": 15, "senkouBLen": 30, "displacement": 15},
		"swing":    {"tenkanLen": 20, "kijunLen": 60, "senkouBLen": 120, "displacement": 52},
	},
	ParseOutput: parseIchimoku,
	FormatText:  formatIchimoku,
}

func parseIchimoku(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "ichimoku-cloud",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)

	// plot_0 = Tenkan-Sen (9), plot_1 = Kijun-Sen (26), plot_2 = Chinkou Span
	// plot_3 = Senkou Span A (cloud top), plot_5 = Senkou Span B (cloud bottom)
	// These have above/below variants with colorers
	tenkan := toFloat(getField(last, []string{"plot_0", "Tenkan_Sen_9_Period"}))
	kijun := toFloat(getField(last, []string{"plot_1", "Kinjun_Sen_26_Period"}))
	chinkou := toFloat(getField(last, []string{"plot_2", "Chinkou_Span_Lagging_Line"}))

	// Cloud: Senkou A and B — use whichever is populated (above or below variant)
	// TradingView uses 1e+100 as a sentinel for inactive plot variants; filter those.
	spanA := getValidFloat(last, "Senkou_Span_A_26_Period_Above_Span_B_Cloud", "Senkou_Span_A_26_Period_Below_Span_B_Cloud", "Senkou_Span_A_26_Period_Cloud", "plot_3", "plot_11")
	spanB := getValidFloat(last, "Senkou_Span_B_52_Period_Above_Span_A_Cloud", "Senkou_Span_B_52_Period_Below_Span_A_Cloud", "Senkou_Span_B_52_Period_Cloud", "plot_5", "plot_13")

	// Cloud color: green (bullish) when A > B, red (bearish) when A < B
	cloudTop := max(spanA, spanB)
	cloudBottom := min(spanA, spanB)
	cloudBullish := spanA > spanB

	// Get price
	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	if price == 0 {
		price = chinkou // fallback
	}

	// Trend assessment
	// 1. Price vs Cloud: above cloud = bullish, below = bearish, inside = neutral
	// 2. Tenkan vs Kijun: TK cross (Tenkan > Kijun = bullish)
	// 3. Chinkou vs Price: Chinkou above price = bullish
	priceVsCloud := "above"
	if price < cloudBottom {
		priceVsCloud = "below"
	} else if price >= cloudBottom && price <= cloudTop {
		priceVsCloud = "inside"
	}

	tkCross := "bullish"
	if tenkan < kijun {
		tkCross = "bearish"
	}

	chinkouPos := "above"
	if chinkou < price {
		chinkouPos = "below"
	}

	// Overall bias
	bias := "neutral"
	bullScore := 0
	if priceVsCloud == "above" { bullScore += 2 }
	if priceVsCloud == "below" { bullScore -= 2 }
	if tkCross == "bullish" { bullScore++ } else { bullScore-- }
	if chinkouPos == "above" { bullScore++ } else { bullScore-- }
	if cloudBullish { bullScore++ } else { bullScore-- }

	if bullScore >= 2 { bias = "bullish" }
	if bullScore <= -2 { bias = "bearish" }

	// Cloud thickness as support/resistance strength
	cloudThickness := cloudTop - cloudBottom
	cloudPct := 0.0
	if price > 0 { cloudPct = (cloudThickness / price) * 100 }

	// Agentic score
	score := 0.5
	if priceVsCloud == "above" || priceVsCloud == "below" { score += 0.15 }
	if bullScore >= 3 || bullScore <= -3 { score += 0.15 }
	if cloudPct < 0.5 { score += 0.1 } // Thin cloud = easier breakout
	if abs(tenkan-kijun) > 0 { score += 0.05 }
	if score > 1.0 { score = 1.0 }

	structure := map[string]any{
		"tenkan":        tenkan,
		"kijun":         kijun,
		"chinkou":       chinkou,
		"spanA":         spanA,
		"spanB":         spanB,
		"cloudTop":      cloudTop,
		"cloudBottom":   cloudBottom,
		"cloudBullish":  cloudBullish,
		"cloudThickness": cloudThickness,
		"cloudPct":      cloudPct,
		"priceVsCloud":  priceVsCloud,
		"tkCross":       tkCross,
		"chinkouPos":    chinkouPos,
		"bullScore":     bullScore,
	}

	narrative := fmt.Sprintf("Price %s cloud, TK cross %s", priceVsCloud, tkCross)

	opp := []skill.Opportunity{}
	if priceVsCloud == "above" && tkCross == "bullish" {
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "Ichimoku Bullish Alignment", Direction: "long",
			Confidence: "medium", Rationale: "Price above cloud with bullish TK cross",
		})
	}
	if priceVsCloud == "below" && tkCross == "bearish" {
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "Ichimoku Bearish Alignment", Direction: "short",
			Confidence: "medium", Rationale: "Price below cloud with bearish TK cross",
		})
	}

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "ichimoku-cloud",
		Market:        skill.MarketData{Bias: bias},
		Structure:     structure,
		Opportunities: opp,
		Narrative: skill.Narrative{
			MarketStructure: narrative,
			PrimaryOpp:      firstOppText(opp),
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: score},
	}
}

func formatIchimoku(result skill.SkillResult) string {
	s := result.Structure
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("  Tenkan-Sen: %.2f | Kijun-Sen: %.2f\n", toFloat(s["tenkan"]), toFloat(s["kijun"])))
	sb.WriteString(fmt.Sprintf("  Chinkou Span: %.2f (%s price)\n", toFloat(s["chinkou"]), s["chinkouPos"]))
	cloudBullish, _ := s["cloudBullish"].(bool)
	sb.WriteString(fmt.Sprintf("  Cloud: %.2f - %.2f (%s, %.2f%% thick)\n",
		toFloat(s["cloudBottom"]), toFloat(s["cloudTop"]),
		map[bool]string{true: "bullish", false: "bearish"}[cloudBullish],
		toFloat(s["cloudPct"])))
	sb.WriteString(fmt.Sprintf("  Price %s cloud | TK cross: %s | Score: %+d\n",
		s["priceVsCloud"], s["tkCross"], toInt(s["bullScore"])))
	return sb.String()
}

func init() { skill.Register(IchimokuSkill) }
