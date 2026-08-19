package parsers

import (
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var SqueezeSkill = &skill.Skill{
	Name:     "squeeze",
	Synopsis: "Squeeze Momentum [LazyBear] — volatility squeeze + momentum direction",
	PineID:   "PUB;175",
	Inputs: []skill.InputDef{
		{Name: "bbLength", TVInputID: "in_0", Type: "int", Default: 20},
		{Name: "bbMult", TVInputID: "in_1", Type: "float", Default: 2.0},
		{Name: "kcLength", TVInputID: "in_2", Type: "int", Default: 20},
		{Name: "kcMult", TVInputID: "in_3", Type: "float", Default: 1.5},
		{Name: "useTrueRange", TVInputID: "in_4", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"default":  {"bbLength": 20, "bbMult": 2.0, "kcLength": 20, "kcMult": 1.5},
		"scalping": {"bbLength": 10, "bbMult": 2.0, "kcLength": 10, "kcMult": 1.5},
		"swing":    {"bbLength": 40, "bbMult": 2.0, "kcLength": 40, "kcMult": 1.5},
	},
	ParseOutput: parseSqueeze,
	FormatText:  formatSqueeze,
}

func parseSqueeze(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "squeeze-momentum",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	bars := historicalBars(periods)

	// plot_0 = momentum (linear regression value), plot_2 = squeeze state (0=on, 1=off)
	// plot_1 = momentum colorer (1=positive, 0=negative), plot_3 = squeeze colorer (6=off, 7=no_sqz, 0=on)
	momentum := toFloat(getField(last, []string{"plot_0", "Plot"}))
	sqzColorer := toInt(getField(last, []string{"plot_3", "Plot_2_colorer"}))

	// Determine squeeze status
	// sqzColorer: 0 = squeeze ON (BB inside KC), 6 = squeeze OFF (BB outside KC), 7 = no squeeze
	squeezeOn := sqzColorer == 0

	// Momentum direction: positive and rising = bullish, positive and falling = weakening
	// negative and falling = bearish, negative and rising = weakening
	momentumDir := "neutral"
	if momentum > 0 {
		momentumDir = "bullish"
	} else if momentum < 0 {
		momentumDir = "bearish"
	}

	// Count consecutive bars in squeeze state
	squeezeBars := 0
	for _, p := range bars {
		sc := toInt(getField(p, []string{"plot_3", "Plot_2_colorer"}))
		if sc == 0 {
			squeezeBars++
		} else {
			break
		}
	}

	// Count recent momentum direction
	posBars := 0
	negBars := 0
	limit := 20
	if len(bars) < limit {
		limit = len(bars)
	}
	for _, p := range bars[:limit] {
		m := toFloat(getField(p, []string{"plot_0", "Plot"}))
		if m > 0 {
			posBars++
		} else {
			negBars++
		}
	}

	// Find last squeeze release (transition from squeeze on to off)
	lastReleaseBar := -1
	for i := 1; i < len(bars); i++ {
		prev := toInt(getField(bars[i-1], []string{"plot_3", "Plot_2_colorer"}))
		curr := toInt(getField(bars[i], []string{"plot_3", "Plot_2_colorer"}))
		if prev == 0 && curr != 0 {
			lastReleaseBar = i
		}
	}

	// Determine bias
	bias := "neutral"
	if squeezeOn {
		// In squeeze - waiting for breakout
		bias = "neutral"
	} else if momentum > 0 {
		bias = "bullish"
	} else if momentum < 0 {
		bias = "bearish"
	}

	// Agentic score
	score := 0.5
	if !squeezeOn {
		score += 0.2 // Squeeze released = actionable
	}
	if abs(momentum) > 20 {
		score += 0.15 // Strong momentum
	}
	if squeezeOn && squeezeBars >= 6 {
		score += 0.15 // Extended squeeze = high breakout probability
	}
	if posBars > 15 || negBars > 15 {
		score += 0.1 // Consistent direction
	}
	if score > 1.0 {
		score = 1.0
	}

	structure := map[string]any{
		"momentum":       momentum,
		"momentumDir":    momentumDir,
		"squeezeOn":      squeezeOn,
		"squeezeBars":    squeezeBars,
		"positiveBars":   posBars,
		"negativeBars":   negBars,
		"lastReleaseBar": lastReleaseBar,
	}

	narrative := "Volatility expanding"
	if squeezeOn {
		narrative = "Volatility compressed (squeeze active) — breakout pending"
	}

	opp := []skill.Opportunity{}
	if !squeezeOn && abs(momentum) > 15 {
		dir := "long"
		if momentum < 0 {
			dir = "short"
		}
		opp = append(opp, skill.Opportunity{
			Rank:        1,
			Setup:       "Squeeze Release + Momentum",
			Direction:   dir,
			Confidence:  "medium",
			DistanceFromPrice: nil,
			IsStale:     false,
			Rationale:   "Squeeze released with strong momentum confirmation",
		})
	}
	if squeezeOn && squeezeBars >= 6 {
		opp = append(opp, skill.Opportunity{
			Rank:        1,
			Setup:       "Extended Squeeze Breakout Watch",
			Direction:   "watch",
			Confidence:  "low",
			DistanceFromPrice: nil,
			IsStale:     false,
			Rationale:   "Extended squeeze period — high breakout probability, direction pending",
		})
	}

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "squeeze-momentum",
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

func formatSqueeze(result skill.SkillResult) string {
	s := result.Structure
	momentum := toFloat(s["momentum"])
	squeezeOn, _ := s["squeezeOn"].(bool)
	sqzBars, _ := s["squeezeBars"].(int)
	posBars, _ := s["positiveBars"].(int)
	negBars, _ := s["negativeBars"].(int)

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("  Momentum: %.2f (%s)\n", momentum, s["momentumDir"]))
	if squeezeOn {
		sb.WriteString(fmt.Sprintf("  Squeeze: ACTIVE (%d bars compressed)\n", sqzBars))
		sb.WriteString("  → Breakout pending — watch for squeeze release\n")
	} else {
		sb.WriteString("  Squeeze: Released (volatility expanding)\n")
	}
	sb.WriteString(fmt.Sprintf("  Recent: %d bullish / %d bearish bars (last 20)\n", posBars, negBars))
	return sb.String()
}

func init() { skill.Register(SqueezeSkill) }
