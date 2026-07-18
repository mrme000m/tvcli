package schema

import (
	"math"
	"sort"
	"strings"
)

// classifyFromMetadata determines the semantic role of a plot using metadata clues
// (title, plot type, style type) before falling back to statistical analysis.
func classifyFromMetadata(pd PlotDef, description string) string {
	// hline is always a level
	if pd.IsHLine {
		return "level"
	}

	// color plots
	if pd.IsColor {
		return "color"
	}

	// Fill plots are bands
	if pd.PlotType == "fill" {
		return "band"
	}

	title := strings.ToLower(pd.Name)
	_ = description // available for future cross-referencing

	// Signal keywords
	signalKeywords := []string{
		"signal", "buy", "sell", "long", "short", "entry", "exit",
		"direction", "trend", "bos", "choch", "cross", "crossover",
		"crossunder", "alert", "trigger", "arrow", "shape",
		"momentum shift", "reversal",
	}
	for _, kw := range signalKeywords {
		if strings.Contains(title, kw) {
			return "signal"
		}
	}

	// Oscillator keywords
	oscillatorKeywords := []string{
		"rsi", "stoch", "cci", "mfi", "momentum", "roc",
		"williams", "tdi", "rsi", "stochastic", "oscillator",
		"macd", "macdhist", "macdsignal", "macdline",
		"atr", "adx", "di+", "di-", "plus_di", "minus_di",
	}
	for _, kw := range oscillatorKeywords {
		if strings.Contains(title, kw) {
			return "oscillator"
		}
	}

	// Band/channel keywords
	bandKeywords := []string{
		"ema", "sma", "ma", "band", "upper", "lower", "middle",
		"channel", "envelope", "keltner", "bollinger", "bb",
		"ichimoku", "tenkan", "kijun", "senkou", "supertrend",
		"psar", "parabolic", "donchian", "vwap", "vwma",
		"hma", "wma", "dema", "tema", "kama", "jurik",
	}
	for _, kw := range bandKeywords {
		if strings.Contains(title, kw) {
			return "band"
		}
	}

	// Price keywords
	priceKeywords := []string{
		"price", "open", "high", "low", "close", "vwap",
		"pivot", "support", "resistance", "s&r", "s/r",
		"level", "target", "stop", "entry_price", "exit_price",
		"typical", "weighted", "median",
	}
	for _, kw := range priceKeywords {
		if strings.Contains(title, kw) {
			return "price"
		}
	}

	// Volume keywords
	volumeKeywords := []string{
		"volume", "vol", "obv", "ad", "chaikin", "mfv",
		"delta", "cumulative", "volume_profile", "vp",
		"buying", "selling",
	}
	for _, kw := range volumeKeywords {
		if strings.Contains(title, kw) {
			return "volume"
		}
	}

	// Style-based heuristics
	if pd.StyleType == "histogram" || pd.StyleType == "columns" {
		return "metric"
	}

	// Overlay plots in a non-overlay script are likely price
	if pd.IsOverlay {
		return "price"
	}

	return "unknown"
}

// ClassifyFromStatistics classifies a field using statistical properties when
// metadata classification is unavailable or returns "unknown".
// This is the fallback used by extract.go — here as a standalone helper.
func ClassifyFromStatistics(values []float64, dominantPrice float64) string {
	if len(values) == 0 {
		return "unknown"
	}

	n := len(values)
	uniqSet := map[float64]struct{}{}
	min, max := values[0], values[0]
	sum := 0.0
	nanCount, zeroCount, intCount := 0, 0, 0

	for _, v := range values {
		if math.IsNaN(v) || math.Abs(v) > 1e90 {
			nanCount++
			continue
		}
		uniqSet[round6(v)] = struct{}{}
		if v < min {
			min = v
		}
		if v > max {
			max = v
		}
		sum += v
		if v == 0 {
			zeroCount++
		}
		if math.Abs(v-math.Round(v)) < 1e-6 {
			intCount++
		}
	}

	validCount := n - nanCount
	if validCount == 0 {
		return "unknown"
	}

	unique := len(uniqSet)
	mean := sum / float64(validCount)
	nonZeroDensity := float64(validCount-zeroCount) / float64(n)
	integerRatio := float64(intCount) / float64(validCount)
	isBoolLike := allBoolLike(values)

	// Sparse 0/1 → signal
	if isBoolLike && nonZeroDensity < 0.50 {
		return "signal"
	}

	// Constant large value → price level
	if unique == 1 && mean > 2000 && mean < 1e6 {
		return "price"
	}

	// Small integer palette → style
	if unique >= 2 && unique <= 8 && integerRatio > 0.90 && nonZeroDensity > 0.5 {
		return "style"
	}

	// Continuous with large range near price → price
	rng := max - min
	if rng > 0 && unique > 10 {
		if dominantPrice > 0 {
			diff := math.Abs(mean-dominantPrice) / dominantPrice
			if diff < 0.15 {
				return "price"
			}
		}
		if min > 2000 {
			return "price"
		}
	}

	// Bounded 0-100 → oscillator
	if (rng <= 100 && min >= 0 && max <= 100) || (rng <= 200 && min >= -100 && max <= 100) {
		return "oscillator"
	}

	return "metric"
}

// DominantPriceLevel finds the most likely current price from field statistics.
func DominantPriceLevel(fieldStats map[string]FieldStats) float64 {
	var candidates []float64
	for _, st := range fieldStats {
		if st.Mean > 100 && st.StdDev/st.Mean < 0.06 && st.Unique > 20 {
			candidates = append(candidates, st.Mean)
		}
	}
	if len(candidates) == 0 {
		return 0
	}
	sort.Float64s(candidates)
	return candidates[len(candidates)/2]
}

// FieldStats holds statistical properties of a single field.
type FieldStats struct {
	Count          int
	Unique         int
	Min            float64
	Max            float64
	Mean           float64
	StdDev         float64
	NonZeroDensity float64
	IntegerRatio   float64
	IsBoolLike     bool
	AllNaN         bool
}

// CalcFieldStats computes statistical properties for a set of values.
func CalcFieldStats(values []float64) FieldStats {
	if len(values) == 0 {
		return FieldStats{AllNaN: true}
	}

	n := len(values)
	uniqSet := map[float64]struct{}{}
	min, max := values[0], values[0]
	sum := 0.0
	zeroCount, intCount, nanCount := 0, 0, 0

	for _, v := range values {
		if math.IsNaN(v) || math.Abs(v) > 1e90 {
			nanCount++
			continue
		}
		uniqSet[round6(v)] = struct{}{}
		if v < min {
			min = v
		}
		if v > max {
			max = v
		}
		sum += v
		if v == 0 {
			zeroCount++
		}
		if math.Abs(v-math.Round(v)) < 1e-6 {
			intCount++
		}
	}

	validCount := n - nanCount
	if validCount == 0 {
		return FieldStats{Count: n, AllNaN: true}
	}

	mean := sum / float64(validCount)
	var sq float64
	for _, v := range values {
		if math.IsNaN(v) || math.Abs(v) > 1e90 {
			continue
		}
		d := v - mean
		sq += d * d
	}

	return FieldStats{
		Count:          n,
		Unique:         len(uniqSet),
		Min:            min,
		Max:            max,
		Mean:           mean,
		StdDev:         math.Sqrt(sq / float64(validCount)),
		NonZeroDensity: float64(validCount-zeroCount) / float64(n),
		IntegerRatio:   float64(intCount) / float64(validCount),
		IsBoolLike:     allBoolLike(values),
	}
}

func allBoolLike(values []float64) bool {
	for _, v := range values {
		if !math.IsNaN(v) && math.Abs(v) <= 1e90 {
			if v != 0 && v != 1 && v != -1 {
				return false
			}
		}
	}
	return len(values) > 0
}

func round6(v float64) float64 {
	return math.Round(v*1e6) / 1e6
}
