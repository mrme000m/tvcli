// graphics_ext.go — legacy per-script graphics matchers (preserved as
// documentation, no longer called).
//
// This file previously contained two per-script pattern matchers:
//   - findVolumeProfilePeaks: boxes sharing a left edge (specific to
//     JacobMagleby's Volume Profile layout)
//   - findLineFillZones: dwglinefills between two horizontal rails
//
// These have been replaced by the generic, topology-based approach in
// graphics_generic.go, which handles any script's graphics without
// per-script matchers. postProcessGraphics now routes to
// postProcessGraphicsGeneric. The old functions are preserved here as
// documentation of the patterns they handled, and may be useful as reference
// when adding new topology rules.
//
// Architecture: two-layer generic design
//   Layer 1 (pkg/pipeline/extract.go): flat signal extraction from all draw types
//   Layer 2 (graphics_generic.go):     topology-based structural analysis
package agent

import (
	"math"
	"strconv"
)

const profileMinStack = 3 // min boxes sharing a left edge to count as a profile

// postProcessGraphics fills GraphicSummary.VolumePeaks and Zones and
// re-classifies instance boxes/lines/labels using the generic, topology-based
// grouping in graphics_generic.go. This replaces the per-script pattern
// matchers (findVolumeProfilePeaks, findLineFillZones) with a universal
// approach that works across any script without script-specific knowledge.
//
// It must run AFTER the flat box/line/label parsers have populated analysis.
func (a *UniversalAnalyzer) postProcessGraphics(analysis *GraphicAnalysis, graphic map[string]map[string]any) {
	a.postProcessGraphicsGeneric(analysis, graphic)
}

// boxW returns the X-extent of a box graphic.
func boxW(b BoxGraphic) float64 { return math.Abs(b.X2 - b.X1) }

// findVolumeProfilePeaks groups boxes that share a common left edge (X1).
// In volume-profile bar scripts each order block renders a vertical stack of
// grid bins from a single leftTime and varies only the right edge to encode
// volume. Within the stack the widest box is the peak (POC) and the stack's
// top/bottom bound the value area (VAH/VAL).
func (a *UniversalAnalyzer) findVolumeProfilePeaks(analysis *GraphicAnalysis) {
	byLeft := map[float64][]BoxGraphic{}
	var order []float64
	for _, b := range analysis.Boxes {
		key := math.Round(b.X1)
		if _, seen := byLeft[key]; !seen {
			order = append(order, key)
		}
		byLeft[key] = append(byLeft[key], b)
	}

	profileLefts := map[float64]bool{}
	for _, left := range order {
		boxes := byLeft[left]
		n := len(boxes)
		if n < profileMinStack {
			continue
		}
		minY, maxY := math.MaxFloat64, -math.MaxFloat64
		for _, b := range boxes {
			if b.Low < minY {
				minY = b.Low
			}
			if b.High > maxY {
				maxY = b.High
			}
		}
		span := maxY - minY
		if span <= 0 {
			continue
		}
		var covered float64
		for _, b := range boxes {
			covered += b.High - b.Low
		}
		// A value-area stack tiles its price span; reject loose groups.
		if covered/span < 0.7 {
			continue
		}

		widest := boxes[0]
		for _, b := range boxes[1:] {
			if boxW(b) > boxW(widest) {
				widest = b
			}
		}
		conf := math.Min(0.5+0.08*float64(n), 0.95)
		analysis.Summary.VolumePeaks = append(analysis.Summary.VolumePeaks, VolumePeak{
			PocPrice:   (widest.High + widest.Low) / 2,
			Vah:        maxY,
			Val:        minY,
			StackCount: n,
			LeftBar:    left,
			Confidence: conf,
		})
		analysis.Summary.InferredTypes["volume_profile"] += n
		profileLefts[left] = true
	}

	// Re-classify actual boxes that belong to a profile stack so buildSummary
	// won't emit one noisy per-bin level; the stack is the POC/VAH/VAL.
	for i := range analysis.Boxes {
		if profileLefts[math.Round(analysis.Boxes[i].X1)] {
			analysis.Boxes[i].InferredType = "volume_profile"
			analysis.Boxes[i].Confidence = 0.8
		}
	}
}

// findLineFillZones resolves dwglinefills into bounded price zones. A linefill
// joins two drawing lines (line1, line2); when both are horizontal rails the
// fill brackets a zone — typically an order-block or range-box region.
func (a *UniversalAnalyzer) findLineFillZones(analysis *GraphicAnalysis, graphic map[string]map[string]any) {
	fills, ok := graphic["dwglinefills"]
	if !ok {
		return
	}
	lines, hasLines := graphic["dwglines"]
	if !hasLines {
		return
	}

	// index each raw line by its drawing id for horizontal-level lookup
	lineY := map[string]float64{}
	for id0, item := range lines {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		y1, e1 := toFloat(m["y1"])
		y2, e2 := toFloat(m["y2"])
		if e1 && e2 && math.Abs(y1-y2) < 1e-6 {
			lineY[id0] = y1
		}
	}

	for _, item := range fills {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		id1, ok1 := floatKey(m["line1"])
		id2, ok2 := floatKey(m["line2"])
		if !ok1 || !ok2 {
			continue
		}
		y1, e1 := lineY[floatID(id1)]
		y2, e2 := lineY[floatID(id2)]
		if !e1 || !e2 || y1 <= 0 || y2 <= 0 {
			continue
		}
		top, bot := math.Max(y1, y2), math.Min(y1, y2)
		analysis.Summary.Zones = append(analysis.Summary.Zones, Zone{
			Top:        top,
			Bottom:     bot,
			Mid:        (top + bot) / 2,
			Confidence: 0.75,
		})
		analysis.Summary.InferredTypes["order_block"]++
	}
}

// floatKey reads a numeric field that may be float64/int/int64.
func floatKey(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case string:
		if f, err := strconv.ParseFloat(x, 64); err == nil {
			return f, true
		}
	}
	return 0, false
}

// floatID renders a drawing id the way TradingView keys its dwg* maps
// (integer strings: "27", "432").
func floatID(v float64) string {
	if v == math.Trunc(v) {
		return strconv.FormatInt(int64(v), 10)
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}
