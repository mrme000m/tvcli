package parsers

import (
	"fmt"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var XauScalpSkill = &skill.Skill{
	Name:     "xau-scalp",
	Synopsis: "XAUUSD Scalping Confluence Engine — all-in-one EMA+ST+RSI+Squeeze+Volume+BB",
	PineID:   "USER;a2b64849e693497d9b975abe0cab2889",
	Inputs: []skill.InputDef{
		{Name: "ema1Len", TVInputID: "in_0", Type: "int", Default: 3},
		{Name: "ema2Len", TVInputID: "in_1", Type: "int", Default: 8},
		{Name: "ema3Len", TVInputID: "in_2", Type: "int", Default: 21},
		{Name: "ema4Len", TVInputID: "in_3", Type: "int", Default: 55},
		{Name: "stAtrLen", TVInputID: "in_4", Type: "int", Default: 10},
		{Name: "stFactor", TVInputID: "in_5", Type: "float", Default: 3.0},
		{Name: "rsiLen", TVInputID: "in_6", Type: "int", Default: 14},
		{Name: "bbLen", TVInputID: "in_7", Type: "int", Default: 20},
		{Name: "bbMult", TVInputID: "in_8", Type: "float", Default: 2.0},
		{Name: "kcLen", TVInputID: "in_9", Type: "int", Default: 20},
		{Name: "kcMult", TVInputID: "in_10", Type: "float", Default: 1.5},
		{Name: "volLen", TVInputID: "in_11", Type: "int", Default: 20},
	},
	Presets: map[string]map[string]any{
		"default":  {"ema1Len": 3, "ema2Len": 8, "ema3Len": 21, "ema4Len": 55, "rsiLen": 14, "bbLen": 20, "kcLen": 20},
		"scalping": {"ema1Len": 2, "ema2Len": 5, "ema3Len": 13, "ema4Len": 34, "rsiLen": 7, "bbLen": 10, "kcLen": 10, "stAtrLen": 7},
		"swing":    {"ema1Len": 8, "ema2Len": 21, "ema3Len": 55, "ema4Len": 120, "rsiLen": 21, "bbLen": 40, "kcLen": 40, "stAtrLen": 20},
	},
	ParseOutput: parseXauScalp,
	FormatText:  formatXauScalp,
}

func parseXauScalp(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "xauusd-scalping-confluence",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	bars := historicalBars(periods)

	// Extract all signal components
	composite := toFloat(getField(last, []string{"Composite", "plot_0"}))
	emaStack := toInt(getField(last, []string{"EMA_Stack", "plot_1"}))
	stDir := toInt(getField(last, []string{"ST_Dir", "plot_2"})) / 100
	rsi := toFloat(getField(last, []string{"RSI", "plot_3"}))
	sqzMom := toFloat(getField(last, []string{"Sqz_Mom", "plot_4"}))
	squeezeOn := toFloat(getField(last, []string{"Squeeze", "plot_5"})) > 0
	sqzRelease := toFloat(getField(last, []string{"Sqz_Release", "plot_6"})) > 0
	volDelta := toFloat(getField(last, []string{"Vol_Delta", "plot_7"}))
	bbPct := toFloat(getField(last, []string{"BB_Pct", "plot_8"}))
	signalRaw := toFloat(getField(last, []string{"Signal", "plot_9"}))
	slLevel := getValidFloat(last, "SL", "plot_10")
	tpLevel := getValidFloat(last, "TP", "plot_11")
	emaSlope := toFloat(getField(last, []string{"EMA_Slope", "plot_12"}))
	volRatio := toFloat(getField(last, []string{"Vol_Ratio", "plot_13"})) / 100

	// Normalize signal: 100=strong long, 50=mild long, 0=neutral, -50=mild short, -100=strong short
	signal := 0
	if signalRaw > 75 {
		signal = 2
	} else if signalRaw > 25 {
		signal = 1
	} else if signalRaw < -75 {
		signal = -2
	} else if signalRaw < -25 {
		signal = -1
	}

	// Price
	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	if price == 0 {
		price = slLevel + 2 * (tpLevel - slLevel) / 5 // reverse from SL/TP
	}

	// Determine bias from composite score
	bias := "neutral"
	if composite > 20 { bias = "bullish" }
	if composite < -20 { bias = "bearish" }

	// Count recent signal bars for context
	bullBars := 0
	bearBars := 0
	limit := 20
	if len(bars) < limit { limit = len(bars) }
	for _, p := range bars[:limit] {
		c := toFloat(getField(p, []string{"Composite", "plot_0"}))
		if c > 0 { bullBars++ } else { bearBars++ }
	}

	// Check for squeeze release (high-probability breakout signal)
	squeezeReleases := 0
	for _, p := range bars[:50] {
		if toFloat(getField(p, []string{"Sqz_Release", "plot_6"})) > 0 {
			squeezeReleases++
		}
	}

	// Confluence breakdown: how many signals agree?
	confluence := 0
	if emaStack > 0 { confluence++ } else if emaStack < 0 { confluence-- }
	if stDir > 0 { confluence++ } else if stDir < 0 { confluence-- }
	if rsi > 55 { confluence++ } else if rsi < 45 { confluence-- }
	if sqzMom > 0 { confluence++ } else if sqzMom < 0 { confluence-- }
	if volDelta > 10 { confluence++ } else if volDelta < -10 { confluence-- }
	if bbPct > 0 { confluence++ } else if bbPct < 0 { confluence-- }

	// Agentic score
	score := 0.5
	if abs(composite) > 25 { score += 0.15 }
	if signal >= 2 || signal <= -2 { score += 0.15 }
	if confluence >= 4 || confluence <= -4 { score += 0.1 }
	if sqzRelease { score += 0.1 }
	if abs(emaSlope) > 20 { score += 0.05 }
	if score > 1.0 { score = 1.0 }

	// Structure
	structure := map[string]any{
		"composite":       composite,
		"emaStack":        emaStack,
		"stDir":           stDir,
		"rsi":             rsi,
		"sqzMom":          sqzMom,
		"squeezeOn":       squeezeOn,
		"sqzRelease":      sqzRelease,
		"volDelta":        volDelta,
		"bbPct":           bbPct,
		"signal":          signal,
		"slLevel":         slLevel,
		"tpLevel":         tpLevel,
		"emaSlope":        emaSlope,
		"volRatio":        volRatio,
		"confluence":      confluence,
		"bullBars":        bullBars,
		"bearBars":        bearBars,
		"squeezeReleases": squeezeReleases,
		"price":           price,
	}

	// Signal label
	signalLabel := "neutral"
	if signal == 2 { signalLabel = "STRONG LONG" }
	if signal == 1 { signalLabel = "mild long" }
	if signal == -1 { signalLabel = "mild short" }
	if signal == -2 { signalLabel = "STRONG SHORT" }

	// Opportunities
	opp := []skill.Opportunity{}
	if signal > 0 {
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "Scalp " + signalLabel,
			Direction: "long", Confidence: confidenceLabel(score),
			Entry: price, StopLoss: slLevel, TP1: tpLevel,
			RiskReward: 3.0 / 2.0,
			Rationale: fmt.Sprintf("Composite %.0f, EMA stack %+d, ST bullish, RSI %.0f, confluence %+d", composite, emaStack, rsi, confluence),
		})
	}
	if signal < 0 {
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "Scalp " + signalLabel,
			Direction: "short", Confidence: confidenceLabel(score),
			Entry: price, StopLoss: slLevel, TP1: tpLevel,
			RiskReward: 3.0 / 2.0,
			Rationale: fmt.Sprintf("Composite %.0f, EMA stack %+d, ST bearish, RSI %.0f, confluence %+d", composite, emaStack, rsi, confluence),
		})
	}
	if sqzRelease && signal == 0 {
		dir := "watch"
		if sqzMom > 0 { dir = "long_watch" }
		if sqzMom < 0 { dir = "short_watch" }
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "Squeeze Release Breakout Watch",
			Direction: dir, Confidence: "low",
			Rationale: fmt.Sprintf("Squeeze released, momentum %.1f — breakout pending", sqzMom),
		})
	}

	narrative := fmt.Sprintf("Signal: %s | Composite: %.0f | Confluence: %+d/7 | RSI: %.0f | Squeeze: %s",
		signalLabel, composite, confluence, rsi,
		map[bool]string{true: "ON", false: "off"}[squeezeOn])

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "xauusd-scalping-confluence",
		Market:        skill.MarketData{Bias: bias, LastPrice: price},
		Structure:     structure,
		Opportunities: opp,
		Narrative: skill.Narrative{
			MarketStructure: narrative,
			PrimaryOpp:      firstOppText(opp),
			Warnings:        []string{},
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: score},
	}
}

func formatXauScalp(result skill.SkillResult) string {
	s := result.Structure
	var sb strings.Builder
	signalLabel := "neutral"
	sig := toInt(s["signal"])
	if sig == 2 { signalLabel = "STRONG LONG" }
	if sig == 1 { signalLabel = "mild long" }
	if sig == -1 { signalLabel = "mild short" }
	if sig == -2 { signalLabel = "STRONG SHORT" }

	sb.WriteString(fmt.Sprintf("  Signal: %s (composite: %.0f, confluence: %+d/7)\\n", signalLabel, toFloat(s["composite"]), toInt(s["confluence"])))
	sb.WriteString(fmt.Sprintf("  EMA Stack: %+d | ST: %+d | RSI: %.0f | Sqz Mom: %.1f\\n",
		toInt(s["emaStack"]), toInt(s["stDir"]), toFloat(s["rsi"]), toFloat(s["sqzMom"])))
	sb.WriteString(fmt.Sprintf("  Squeeze: %s | Vol Delta: %.0f | BB %%: %.0f | EMA Slope: %.1f\\n",
		map[bool]string{true: "ON", false: "off"}[s["squeezeOn"].(bool)],
		toFloat(s["volDelta"]), toFloat(s["bbPct"]), toFloat(s["emaSlope"])))
	if toFloat(s["slLevel"]) > 0 {
		sb.WriteString(fmt.Sprintf("  Entry: %.2f | SL: %.2f | TP: %.2f | R:R = 1.5\\n",
			toFloat(s["price"]), toFloat(s["slLevel"]), toFloat(s["tpLevel"])))
	}
	sb.WriteString(fmt.Sprintf("  Recent: %d bullish / %d bearish bars (20)\\n", toInt(s["bullBars"]), toInt(s["bearBars"])))
	return sb.String()
}

func init() { skill.Register(XauScalpSkill) }
