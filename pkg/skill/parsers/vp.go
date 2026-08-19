package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

// VPSkill wraps the numeric "Fixed Range Volume Profile Zones" public indicator.
//
// Unlike the older graphics-only fixed-range script, this Pine script exposes
// the levels as regular plot values in periods[]:
//   POC, VAH, VAL, Max_Price, Min_Price, Above_VAH_Buffer, Below_VAL_Buffer.
//
// Recommended usage from the video:
//   - Use a weekly timeframe for the institutional bias.
//   - Draw / set the range from a recent swing low to swing high.
//   - 70% value area is the default for most volume-profile work.
//
// CLI examples:
//   ./tvcli vp --symbol BTCUSDT --tf 1W --bars 52 --preset weekly --agent --json
//   ./tvcli vp --symbol BTCUSDT --tf 1h  --bars 48 --preset intraday --agent --json
var VPSkill = &skill.Skill{
	Name:     "vp",
	Synopsis: "Volume Profile Zones — numeric POC, VAH, VAL, buffers",
	PineID:   "PUB;a4e251b831084685afecaa9192f2a3c5",
	Inputs: []skill.InputDef{
		{Name: "lookback", TVInputID: "in_0", Type: "int", Default: 30},
		{Name: "percentile", TVInputID: "in_1", Type: "int", Default: 30},
		{Name: "upperBuffer", TVInputID: "in_2", Type: "float", Default: 95},
		{Name: "lowerBuffer", TVInputID: "in_3", Type: "float", Default: 5},
	},
	Presets: map[string]map[string]any{
		"weekly":   {"lookback": 52, "percentile": 30, "upperBuffer": 95, "lowerBuffer": 5},
		"daily":    {"lookback": 30, "percentile": 30, "upperBuffer": 95, "lowerBuffer": 5},
		"intraday": {"lookback": 24, "percentile": 30, "upperBuffer": 95, "lowerBuffer": 5},
		"scalping": {"lookback": 12, "percentile": 30, "upperBuffer": 95, "lowerBuffer": 5},
	},
	ParseOutput: parseVP,
	FormatText:  formatVP,
}

func parseVP(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	last := latestClosed(periods)
	if last == nil && len(periods) > 0 {
		last = periods[0]
	}
	if last == nil {
		return skill.SkillResult{
			Status:   "no_data",
			Workflow: "volume-profile",
			Narrative: skill.Narrative{
				MarketStructure: "No period data received",
				Warnings:        []string{"Volume Profile script returned no bars"},
			},
		}
	}

	// The script embeds the underlying chart OHLC as plotcandle_0_ohlc_* fields,
	// so we can read the current closing price directly from the indicator output.
	price := toFloat(getField(last, []string{"plotcandle_0_ohlc_close", "Close", "close"}))
	poc := toFloat(getField(last, []string{"POC"}))
	vah := toFloat(getField(last, []string{"VAH"}))
	val := toFloat(getField(last, []string{"VAL"}))
	maxPrice := toFloat(getField(last, []string{"Max_Price"}))
	minPrice := toFloat(getField(last, []string{"Min_Price"}))
	aboveVAH := toFloat(getField(last, []string{"Above_VAH_Buffer"})) != 0
	belowVAL := toFloat(getField(last, []string{"Below_VAL_Buffer"})) != 0

	if poc == 0 || vah == 0 || val == 0 {
		return skill.SkillResult{
			Status:   "no_data",
			Workflow: "volume-profile",
			Narrative: skill.Narrative{
				MarketStructure: "Volume Profile levels missing",
				Warnings:        []string{"POC/VAH/VAL fields were not present in the response"},
			},
		}
	}

	// Bias relative to the value area.
	bias := "neutral"
	if price > 0 {
		switch {
		case aboveVAH || price > vah:
			bias = "bullish-breakout"
		case belowVAL || price < val:
			bias = "bearish-breakout"
		case price > poc:
			bias = "bullish"
		case price < poc:
			bias = "bearish"
		}
	}

	// Mean-reversion distance gives a rough confidence score.
	score := 0.55
	if price > 0 {
		if price < val || price > vah {
			score += 0.2
		}
		if aboveVAH || belowVAL {
			score += 0.1
		}
	}
	score = math.Min(score, 0.95)

	var opps []skill.Opportunity
	if price > 0 && (price < val || belowVAL) {
		dist := ((val - price) / price) * 100
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "vp_mean_reversion_long",
			Direction:       "long",
			Confidence:      confidenceLabel(score),
			ConfluenceScore: round2(score),
			DistanceFromPrice: round2(dist),
			Rationale:       fmt.Sprintf("price %.2f is below VAL %.2f — target POC %.2f then VAH %.2f", price, val, poc, vah),
		})
	}
	if price > 0 && (price > vah || aboveVAH) {
		dist := ((price - vah) / price) * 100
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "vp_mean_reversion_short",
			Direction:       "short",
			Confidence:      confidenceLabel(score),
			ConfluenceScore: round2(score),
			DistanceFromPrice: round2(dist),
			Rationale:       fmt.Sprintf("price %.2f is above VAH %.2f — target POC %.2f then VAL %.2f", price, vah, poc, val),
		})
	}

	// Always surface the structural levels.
	opps = append(opps, skill.Opportunity{
		Rank:            2,
		Setup:           "vp_levels",
		Direction:       bias,
		Confidence:      confidenceLabel(score - 0.1),
		ConfluenceScore: round2(score - 0.1),
		Rationale:       fmt.Sprintf("POC=%.2f VAH=%.2f VAL=%.2f range[%.2f-%.2f]", poc, vah, val, minPrice, maxPrice),
	})

	narrative := skill.Narrative{
		MarketStructure: fmt.Sprintf("POC: %.2f | VAH: %.2f | VAL: %.2f | Range: %.2f - %.2f", poc, vah, val, minPrice, maxPrice),
		PrimaryOpp:      primaryOppFromOpps(opps),
	}

	agenticScore := 0.4
	if poc > 0 {
		agenticScore += 0.15
	}
	if vah > 0 && val > 0 {
		agenticScore += 0.15
	}
	if price > 0 && (price < val || price > vah) {
		agenticScore += 0.25
	}
	if aboveVAH || belowVAL {
		agenticScore += 0.1
	}
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "volume-profile",
		Market: skill.MarketData{
			LastPrice: price,
			Bias:      bias,
		},
		Structure: map[string]any{
			"poc":            poc,
			"vah":            vah,
			"val":            val,
			"maxPrice":       maxPrice,
			"minPrice":       minPrice,
			"rangeMid":       round2((maxPrice + minPrice) / 2),
			"aboveVAHBuffer": aboveVAH,
			"belowVALBuffer": belowVAL,
			"bias":           bias,
		},
		Opportunities: opps,
		Narrative:     narrative,
		Validation:    skill.Validation{Passed: true},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatVP(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  VOLUME PROFILE ZONES (numeric)\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  POC: %.2f  |  VAH: %.2f  |  VAL: %.2f\n",
		result.Structure["poc"], result.Structure["vah"], result.Structure["val"]))
	sb.WriteString(fmt.Sprintf("  Range: %.2f - %.2f  |  Price: %.2f  |  Bias: %s\n",
		result.Structure["minPrice"], result.Structure["maxPrice"], result.Market.LastPrice, result.Market.Bias))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(VPSkill) }
