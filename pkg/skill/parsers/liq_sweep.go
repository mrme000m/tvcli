package parsers

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

// sweepLevel captures a labelled sweep drawn on the chart. Not every script
// emits these, so the parser gracefully degrades when the graphic layer is
// absent or empty.
type sweepLevel struct {
	kind   string
	price  float64
	barIdx float64
	id     float64
}

// sweepCluster is a price level that was swept more than once, indicating a
// repeatedly-defended institutional level.
type sweepCluster struct {
	price float64
	bull  int
	bear  int
}

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

	histBars := historicalBars(periods)

	// Raw counts for backward compatibility.
	bullCount, bearCount := 0, 0
	for _, p := range histBars {
		if toFloat(getField(p, []string{"Bullish_Sweep_Shape"})) == 1 {
			bullCount++
		}
		if toFloat(getField(p, []string{"Bearish_Sweep_Shape"})) == 1 {
			bearCount++
		}
	}

	// Time-decay weighted counts over the full closed-bar history. Older sweeps
	// still contribute, but with rapidly decaying weight, so recent structure
	// dominates the bias.
	wBull, wBear := weightedSweepCounts(histBars)

	// Use weighted counts for bias so recent sweeps dominate.
	bias := "neutral"
	if wBull > wBear {
		bias = "bullish"
	} else if wBear > wBull {
		bias = "bearish"
	}

	// Latest sweep: report the most recent sweep across all closed bars, not
	// only the single last bar. This fixes the contradictory "6 total sweeps
	// but latest=none" reporting and tells the caller how stale the signal is.
	recentKind, barsAgo := recentPeriodSweep(histBars)
	latestSweep := recentKind
	if latestSweep == "none" && (bullNow || bearNow) {
		latestSweep = latestSweepLabel(bullNow, bearNow)
		barsAgo = 0
	}

	// Extract sweep levels from the graphic layer and compute nearest to price.
	sweepLevels := extractSweepLevels(graphic)
	nearest, nearestType, nearestDist := nearestSweepLevel(sweepLevels, price)

	// Liquidity map: horizontal swing lines split above/below current price.
	liqAbove, liqBelow, nearestAbove, nearestBelow := extractLiquidityMap(graphic, price)

	// Sweep flow: full label distribution plus repeated-level clusters.
	flowBull, flowBear, sweepClusters := aggregateSweepFlow(sweepLevels, price)

	wTotal := wBull + wBear
	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	if wTotal > 0 {
		agenticScore += 0.2
		dominant := math.Max(wBull, wBear)
		agenticScore += 0.2 * (dominant / wTotal)
	}
	if bullNow || bearNow {
		agenticScore += 0.15
	}
	if nearest > 0 && math.Abs(nearestDist) < 0.02*price {
		agenticScore += 0.05
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

	sweepLevelsJSON := make([]map[string]any, 0, len(sweepLevels))
	for i, sl := range sweepLevels {
		if i >= 10 {
			break
		}
		sweepLevelsJSON = append(sweepLevelsJSON, map[string]any{
			"kind":    sl.kind,
			"price":   round2(sl.price),
			"barIdx":  int(sl.barIdx),
		})
	}

	sweepClustersJSON := make([]map[string]any, 0, 5)
	for i, c := range sweepClusters {
		if i >= 5 {
			break
		}
		sweepClustersJSON = append(sweepClustersJSON, map[string]any{
			"price": c.price,
			"bull":  c.bull,
			"bear":  c.bear,
			"total": c.bull + c.bear,
		})
	}

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "liquidity-sweep",
		Market:   skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{
			"bullSweeps":          bullCount,
			"bearSweeps":          bearCount,
			"totalSweeps":         bullCount + bearCount,
			"weightedBullSweeps":  round2(wBull),
			"weightedBearSweeps":  round2(wBear),
			"weightedDominance":   sweepDominanceFloat(wBull, wBear),
			"latestSweep":         latestSweep,
			"barsSinceLastSweep":  barsAgo,
			"sweepDominance":      sweepDominance(bullCount, bearCount),
			"price":               price,
			"nearestSweepPrice":   round2(nearest),
			"nearestSweepType":    nearestType,
			"nearestSweepDistance": round2(nearestDist),
			"sweepLevels":         sweepLevelsJSON,
			"liquidityAbove":      liqAbove,
			"liquidityBelow":      liqBelow,
			"liquidityAboveCount": len(liqAbove),
			"liquidityBelowCount": len(liqBelow),
			"nearestLiquidityAbove": round2(nearestAbove),
			"nearestLiquidityBelow": round2(nearestBelow),
			"liquidityImbalance":  liquidityImbalance(len(liqAbove), len(liqBelow)),
			"sweepFlowBull":       flowBull,
			"sweepFlowBear":       flowBear,
			"sweepFlowNet":        flowBull - flowBear,
			"sweepClusters":       sweepClustersJSON,
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Sweeps: bull=%d bear=%d (weighted %.1f/%.1f) | Flow: %d bull / %d bear | Pools: %d above / %d below | Latest: %s (%d bars ago) | Bias: %s",
				bullCount, bearCount, wBull, wBear, flowBull, flowBear, len(liqAbove), len(liqBelow), latestSweep, barsAgo, bias),
			PrimaryOpp: primaryOpp(opps),
			Warnings:   []string{},
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

// sweepLookback resolves the swingLookback input from CLI args, falling back
// to the supplied default.
func sweepLookback(args map[string]string, defaultLookback int) int {
	for _, key := range []string{"in_0", "swingLookback", "swing_lookback"} {
		if v := args[key]; v != "" {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				return n
			}
		}
	}
	return defaultLookback
}

// weightedSweepCounts returns time-decay-weighted bull/bear sweep counts over
// all closed bars. The most recent closed bar has weight 1.0; each older bar
// loses 5% until a floor of 0.25. This prevents stale sweeps from drowning out
// current structure while still preserving some historical context.
//
// `bars` is sorted newest-first (periods[1:] from the raw feed), so index 0 is
// the latest closed bar.
func weightedSweepCounts(bars []map[string]any) (float64, float64) {
	bull, bear := 0.0, 0.0
	for i := 0; i < len(bars); i++ {
		weight := math.Max(0.25, 1.0-0.05*float64(i))
		p := bars[i]
		if toFloat(getField(p, []string{"Bullish_Sweep_Shape"})) == 1 {
			bull += weight
		}
		if toFloat(getField(p, []string{"Bearish_Sweep_Shape"})) == 1 {
			bear += weight
		}
	}
	return bull, bear
}

// recentPeriodSweep returns the most recent sweep type across all closed bars
// and how many bars ago it occurred. `bars` is sorted newest-first.
func recentPeriodSweep(bars []map[string]any) (string, int) {
	for i := 0; i < len(bars); i++ {
		p := bars[i]
		if toFloat(getField(p, []string{"Bullish_Sweep_Shape"})) == 1 {
			return "bullish", i
		}
		if toFloat(getField(p, []string{"Bearish_Sweep_Shape"})) == 1 {
			return "bearish", i
		}
	}
	return "none", -1
}

// extractSweepLevels parses drawing labels like "BULLISH SWEEP" / "BEARISH SWEEP"
// from the graphic layer and returns them sorted newest-first.
func extractSweepLevels(graphic map[string]map[string]any) []sweepLevel {
	labels, ok := graphic["dwglabels"]
	if !ok || len(labels) == 0 {
		return nil
	}
	out := make([]sweepLevel, 0, len(labels))
	for _, v := range labels {
		item, ok := v.(map[string]any)
		if !ok {
			continue
		}
		text := strings.ToLower(strings.TrimSpace(toString(item["t"])))
		var kind string
		switch {
		case strings.Contains(text, "bullish"):
			kind = "bullish"
		case strings.Contains(text, "bearish"):
			kind = "bearish"
		default:
			continue
		}
		out = append(out, sweepLevel{
			kind:   kind,
			price:  toFloat(item["y"]),
			barIdx: toFloat(item["x"]),
			id:     toFloat(item["id"]),
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].id > out[j].id })
	return out
}

// nearestSweepLevel returns the sweep level closest to the current price.
func nearestSweepLevel(levels []sweepLevel, price float64) (float64, string, float64) {
	if len(levels) == 0 || price == 0 {
		return 0, "", 0
	}
	nearest := levels[0]
	minDist := math.Abs(levels[0].price - price)
	for i := 1; i < len(levels); i++ {
		d := math.Abs(levels[i].price - price)
		if d < minDist {
			minDist = d
			nearest = levels[i]
		}
	}
	return nearest.price, nearest.kind, nearest.price - price
}

// extractLiquidityMap parses the script's horizontal swing lines (dwglines)
// and splits them into levels above and below the current price, each sorted
// nearest-first. These are the swing highs/lows that act as liquidity pools.
func extractLiquidityMap(graphic map[string]map[string]any, price float64) (above, below []float64, nearestAbove, nearestBelow float64) {
	lines, ok := graphic["dwglines"]
	if !ok || len(lines) == 0 || price == 0 {
		return nil, nil, 0, 0
	}
	above = make([]float64, 0, len(lines))
	below = make([]float64, 0, len(lines))
	for _, v := range lines {
		item, ok := v.(map[string]any)
		if !ok {
			continue
		}
		lvl := toFloat(item["y1"])
		if lvl == 0 {
			continue
		}
		if lvl > price {
			above = append(above, lvl)
		} else if lvl < price {
			below = append(below, lvl)
		}
	}
	sort.Float64s(above)
	sort.Sort(sort.Reverse(sort.Float64Slice(below)))
	if len(above) > 0 {
		nearestAbove = above[0]
	}
	if len(below) > 0 {
		nearestBelow = below[0]
	}
	return above, below, nearestAbove, nearestBelow
}

// aggregateSweepFlow counts every sweep label by direction and clusters sweeps
// that repeatedly hit the same price level (a defended institutional level).
func aggregateSweepFlow(levels []sweepLevel, price float64) (bull, bear int, clusters []sweepCluster) {
	for _, l := range levels {
		if l.kind == "bullish" {
			bull++
		} else if l.kind == "bearish" {
			bear++
		}
	}
	if len(levels) == 0 || price == 0 {
		return bull, bear, nil
	}

	tol := math.Max(1.0, price*0.001)
	sorted := append([]sweepLevel(nil), levels...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].price < sorted[j].price })

	type acc struct {
		sum, n     float64
		bull, bear int
	}
	accs := make([]acc, 0)
	for _, l := range sorted {
		if l.price == 0 {
			continue
		}
		if len(accs) == 0 || l.price-accs[len(accs)-1].sum/accs[len(accs)-1].n > tol {
			a := acc{sum: l.price, n: 1}
			if l.kind == "bullish" {
				a.bull = 1
			} else {
				a.bear = 1
			}
			accs = append(accs, a)
		} else {
			a := &accs[len(accs)-1]
			a.sum += l.price
			a.n++
			if l.kind == "bullish" {
				a.bull++
			} else {
				a.bear++
			}
		}
	}

	for _, a := range accs {
		if a.bull+a.bear >= 2 {
			clusters = append(clusters, sweepCluster{
				price: round2(a.sum / a.n),
				bull:  a.bull,
				bear:  a.bear,
			})
		}
	}
	sort.Slice(clusters, func(i, j int) bool {
		return clusters[i].bull+clusters[i].bear > clusters[j].bull+clusters[j].bear
	})
	return bull, bear, clusters
}

// liquidityImbalance names the side with more resting liquidity pools, i.e. the
// side price is more likely to be magnetised toward.
func liquidityImbalance(above, below int) string {
	if above > below {
		return "above"
	}
	if below > above {
		return "below"
	}
	return "balanced"
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

func sweepDominanceFloat(bull, bear float64) string {
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
	sb.WriteString(fmt.Sprintf("  Weighted:    %.1f / %.1f\n", result.Structure["weightedBullSweeps"], result.Structure["weightedBearSweeps"]))
	sb.WriteString(fmt.Sprintf("  Flow:        %v bull / %v bear (net %v)\n", result.Structure["sweepFlowBull"], result.Structure["sweepFlowBear"], result.Structure["sweepFlowNet"]))
	sb.WriteString(fmt.Sprintf("  Pools:       %v above / %v below (%s)\n", result.Structure["liquidityAboveCount"], result.Structure["liquidityBelowCount"], result.Structure["liquidityImbalance"]))
	sb.WriteString(fmt.Sprintf("  Liq nearest: %.2f above / %.2f below\n", result.Structure["nearestLiquidityAbove"], result.Structure["nearestLiquidityBelow"]))
	sb.WriteString(fmt.Sprintf("  Latest:      %v (%d bars ago)\n", result.Structure["latestSweep"], result.Structure["barsSinceLastSweep"]))
	sb.WriteString(fmt.Sprintf("  Nearest:     %v @ %.2f\n", result.Structure["nearestSweepType"], result.Structure["nearestSweepPrice"]))
	if clusters, ok := result.Structure["sweepClusters"].([]map[string]any); ok && len(clusters) > 0 {
		sb.WriteString("  Repeated sweep levels:\n")
		for _, c := range clusters {
			sb.WriteString(fmt.Sprintf("    %.2f  (bull %v / bear %v)\n", c["price"], c["bull"], c["bear"]))
		}
	}
	sb.WriteString(fmt.Sprintf("  Bias:        %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// toString safely coerces an interface{} to string.
func toString(v any) string {
	if v == nil {
		return ""
	}
	switch s := v.(type) {
	case string:
		return s
	case float64:
		return fmt.Sprintf("%g", s)
	case int:
		return fmt.Sprintf("%d", s)
	default:
		return fmt.Sprintf("%v", s)
	}
}

func init() { skill.Register(LiqSweepSkill) }
