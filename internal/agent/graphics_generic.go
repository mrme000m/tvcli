// graphics_generic.go — Generic, topology-based graphics analysis.
//
// This module replaces the per-script pattern matchers in graphics_ext.go
// with a universal approach that groups graphic elements by their geometric
// topology and infers semantics from group properties — not from hardcoded
// script-specific layouts.
//
// Pipeline (runs after the flat box/line/label parsers in universal.go):
//
//  1. Normalize colors: detect whether bc/ci are small indices (5,6,7) or
//     full RGBA integers (4278190085) by analyzing the value distribution.
//     Normalize to a common representation so downstream logic doesn't need
//     to know which encoding a script uses.
//
//  2. Build topology: group boxes by shared geometric features:
//     - Shared left edge (x1)  → volume-profile stacks or anchored zones.
//     - Shared right edge (x2) → extension-to-right zones.
//     - Narrow width (x2-x1 ≤ 3) → FVG/gap boxes.
//     - Wide extension (x2 >> x1, x2 = last bar) → active zone boxes.
//
//  3. Associate elements: match boxes to their bounding lines (top/bottom
//     rails), match linefills to their line pairs, and match labels to
//     nearby boxes/lines by coordinate proximity.
//
//  4. Infer group semantics: classify each group from its geometry + text +
//     color + element counts:
//     - Many boxes sharing x1, varying x2 (width ∝ volume) → volume_profile.
//     - Narrow boxes (1-3 bars) with % text → fvg (fair value gap).
//     - Wide boxes with c ≠ 0 extending right → order_block / zone.
//     - Vertical lines (x1==x2) → session / sweep markers.
//     - Horizontal dashed lines extending right → liquidity / breaker levels.
//     - Labels with text like LS/HS/BOS/CHOCH → structural markers.
//
//  5. Emit structured output: zones, levels, POC/VAH/VAL, structural markers,
//     and reclassified boxes/lines with correct inferred types and confidence.

package agent

import (
	"math"
	"sort"
	"strconv"
	"strings"
)

// ---------------------------------------------------------------------------
// Generic topology types
// ---------------------------------------------------------------------------

// GraphicGroup is a cluster of related graphic elements that form a single
// structural feature (e.g., a volume-profile stack, an order-block zone, a
// set of FVG gaps). Groups are built from topology, not from script-specific
// pattern matching.
type GraphicGroup struct {
	Type       string  // "volume_profile", "order_block", "fvg", "liquidity", "session", "zone", "breaker"
	Boxes      []int   // indices into GraphicAnalysis.Boxes
	Lines      []int   // indices into GraphicAnalysis.Lines
	Labels     []int   // indices into GraphicAnalysis.Labels
	PriceLow   float64
	PriceHigh  float64
	LeftBar    float64
	RightBar   float64
	Confidence float64
	Properties map[string]any // arbitrary detected properties
}

// LineFillGraphic represents a dwglinefill entry linking two lines.
type LineFillGraphic struct {
	ID    string
	Line1 string // drawing ID of first line
	Line2 string // drawing ID of second line
	Color int
}

// colorEncoding describes how a script encodes colors in the bc/ci fields.
type colorEncoding int

const (
	colorUnknown colorEncoding = iota
	colorIndex                   // small integers (5,6,7) — input color references
	colorRGBA                    // full 32-bit RGBA integers
)

// ---------------------------------------------------------------------------
// 1. Color normalization
// ---------------------------------------------------------------------------

// detectColorEncoding determines whether box border colors (bc) and line
// colors (ci) are small indices or full RGBA integers by checking the value
// range. Scripts like LuxAlgo use indices (5,6,7,8) while BigBeluga uses
// full RGBA (e.g., 1291845640).
func detectColorEncoding(analysis *GraphicAnalysis) colorEncoding {
	var maxVal float64
	for _, b := range analysis.Boxes {
		if float64(b.BorderColor) > maxVal {
			maxVal = float64(b.BorderColor)
		}
		if float64(b.FillColor) > maxVal {
			maxVal = float64(b.FillColor)
		}
	}
	for _, l := range analysis.Lines {
		if float64(l.Color) > maxVal {
			maxVal = float64(l.Color)
		}
	}
	if maxVal > 256 {
		return colorRGBA
	}
	return colorIndex
}

// ---------------------------------------------------------------------------
// 2. Topology grouping
// ---------------------------------------------------------------------------

// buildBoxTopology groups boxes by geometric features and returns a list of
// groups. Each group represents one structural feature.
func buildBoxTopology(analysis *GraphicAnalysis) []GraphicGroup {
	if len(analysis.Boxes) == 0 {
		return nil
	}

	var groups []GraphicGroup

	// --- Group A: Volume-profile stacks (boxes sharing a left edge) ---
	stacks := groupBoxesByLeftEdge(analysis.Boxes)
	for _, stack := range stacks {
		if len(stack.indices) < profileMinStack {
			continue
		}
		grp := buildVolumeProfileGroup(analysis, stack)
		if grp != nil {
			groups = append(groups, *grp)
		}
	}

	// Track which boxes are already claimed by a higher-priority group.
	claimed := make(map[int]bool)
	for _, g := range groups { // groups so far = volume-profile stacks
		for _, idx := range g.Boxes {
			claimed[idx] = true
		}
	}

	// --- Group B: Narrow FVG/gap boxes (width 1-3 bars) ---
	narrowBoxes := findNarrowBoxes(analysis.Boxes)
	// Exclude boxes already in a volume-profile stack.
	var unclaimedNarrow []int
	for _, idx := range narrowBoxes {
		if !claimed[idx] {
			unclaimedNarrow = append(unclaimedNarrow, idx)
		}
	}
	if len(unclaimedNarrow) > 0 {
		grp := GraphicGroup{
			Type:       "fvg",
			Boxes:      unclaimedNarrow,
			Confidence: 0.7,
		}
		grp.computeBounds(analysis)
		// Boost confidence if text contains % signs (FVG gap indicators)
		for _, idx := range unclaimedNarrow {
			if strings.Contains(analysis.Boxes[idx].Text, "%") {
				grp.Confidence = 0.85
				break
			}
		}
		groups = append(groups, grp)
		for _, idx := range unclaimedNarrow {
			claimed[idx] = true
		}
	}

	// --- Group C: Wide extension zones (boxes extending far right) ---
	wideBoxes := findWideExtensionBoxes(analysis.Boxes)
	var unclaimedWide []int
	for _, idx := range wideBoxes {
		if !claimed[idx] {
			unclaimedWide = append(unclaimedWide, idx)
		}
	}
	if len(unclaimedWide) > 0 {
		grp := GraphicGroup{
			Type:       "order_block",
			Boxes:      unclaimedWide,
			Confidence: 0.75,
		}
		grp.computeBounds(analysis)
		groups = append(groups, grp)
		for _, idx := range unclaimedWide {
			claimed[idx] = true
		}
	}

	// --- Group D: Remaining ungrouped boxes → generic zones ---
	var rest []int
	for i := range analysis.Boxes {
		if !claimed[i] {
			rest = append(rest, i)
		}
	}
	if len(rest) > 0 {
		grp := GraphicGroup{
			Type:       "zone",
			Boxes:      rest,
			Confidence: 0.4,
		}
		grp.computeBounds(analysis)
		groups = append(groups, grp)
	}

	return groups
}

// boxStack is a group of boxes sharing a common left edge.
type boxStack struct {
	leftX   float64
	indices []int // indices into GraphicAnalysis.Boxes
}

// groupBoxesByLeftEdge clusters boxes that share the same x1 (left edge).
func groupBoxesByLeftEdge(boxes []BoxGraphic) []boxStack {
	byLeft := map[float64][]int{}
	var order []float64
	for i, b := range boxes {
		key := math.Round(b.X1)
		if _, seen := byLeft[key]; !seen {
			order = append(order, key)
		}
		byLeft[key] = append(byLeft[key], i)
	}
	var stacks []boxStack
	for _, left := range order {
		stacks = append(stacks, boxStack{leftX: left, indices: byLeft[left]})
	}
	return stacks
}

// buildVolumeProfileGroup creates a volume-profile group from a stack of
// boxes sharing a left edge, where each box's width (x2-x1) encodes volume.
func buildVolumeProfileGroup(analysis *GraphicAnalysis, stack boxStack) *GraphicGroup {
	boxes := make([]BoxGraphic, len(stack.indices))
	for i, idx := range stack.indices {
		boxes[i] = analysis.Boxes[idx]
	}

	// Compute price span
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
		return nil
	}

	// Check tiling: boxes should cover most of the price span
	var covered float64
	for _, b := range boxes {
		covered += b.High - b.Low
	}
	if covered/span < 0.7 {
		return nil
	}

	// Find widest box (POC)
	widestIdx := stack.indices[0]
	widestWidth := boxW(analysis.Boxes[widestIdx])
	for _, idx := range stack.indices[1:] {
		w := boxW(analysis.Boxes[idx])
		if w > widestWidth {
			widestWidth = w
			widestIdx = idx
		}
	}

	pocBox := analysis.Boxes[widestIdx]
	conf := math.Min(0.5+0.08*float64(len(stack.indices)), 0.95)

	return &GraphicGroup{
		Type:       "volume_profile",
		Boxes:      stack.indices,
		PriceLow:   minY,
		PriceHigh:  maxY,
		LeftBar:    stack.leftX,
		Confidence: conf,
		Properties: map[string]any{
			"stackCount":  len(stack.indices),
			"pocPrice":    (pocBox.High + pocBox.Low) / 2,
			"pocWidth":    widestWidth,
			"vah":         maxY,
			"val":         minY,
			"coverage":   covered / span,
		},
	}
}

// findNarrowBoxes returns indices of boxes with width ≤ 3 bars (gap/FVG boxes).
func findNarrowBoxes(boxes []BoxGraphic) []int {
	var out []int
	for i, b := range boxes {
		w := math.Abs(b.X2 - b.X1)
		if w >= 1 && w <= 3 {
			out = append(out, i)
		}
	}
	return out
}

// findWideExtensionBoxes returns indices of boxes that extend far to the
// right (x2 much larger than x1), indicating active zones/order blocks.
func findWideExtensionBoxes(boxes []BoxGraphic) []int {
	if len(boxes) == 0 {
		return nil
	}
	// Find the max x2 (likely the last bar / right edge)
	maxX2 := 0.0
	for _, b := range boxes {
		if b.X2 > maxX2 {
			maxX2 = b.X2
		}
	}
	var out []int
	for i, b := range boxes {
		w := math.Abs(b.X2 - b.X1)
		if w > 5 && b.X2 >= maxX2*0.8 {
			out = append(out, i)
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// 3. Element association
// ---------------------------------------------------------------------------

// associateBoxesWithLines matches boxes to their bounding lines (top and
// bottom rails). In many scripts each box has two associated lines at the
// same y1/y2 coordinates.
func associateBoxesWithLines(analysis *GraphicAnalysis) map[int][]int {
	// map box index → line indices that match box boundaries
	result := make(map[int][]int)
	for bi, box := range analysis.Boxes {
		for li, line := range analysis.Lines {
			// Match if line y1 == box top or box bottom, and line x range overlaps
			boxTop := math.Max(box.Y1, box.Y2)
			boxBot := math.Min(box.Y1, box.Y2)
			lineY := (line.Y1 + line.Y2) / 2
			if math.Abs(lineY-boxTop) < 1e-6 || math.Abs(lineY-boxBot) < 1e-6 {
				// Check x overlap
				if line.X1 >= math.Min(box.X1, box.X2)-1 && line.X2 <= math.Max(box.X1, box.X2)+10 {
					result[bi] = append(result[bi], li)
				}
			}
		}
	}
	return result
}

// parseLineFills extracts dwglinefill entries from the raw graphic map and
// resolves them to their line indices.
func parseLineFills(graphic map[string]map[string]any) []LineFillGraphic {
	fills, ok := graphic["dwglinefills"]
	if !ok {
		return nil
	}
	var out []LineFillGraphic
	for _, item := range fills {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		id, _ := m["id"].(float64)
		l1, _ := floatKey(m["line1"])
		l2, _ := floatKey(m["line2"])
		ci, _ := floatKey(m["ci"])
		out = append(out, LineFillGraphic{
			ID:    floatID(id),
			Line1: floatID(l1),
			Line2: floatID(l2),
			Color: int(ci),
		})
	}
	return out
}

// ---------------------------------------------------------------------------
// 4. Group semantics inference
// ---------------------------------------------------------------------------

// buildLineTopology groups lines by geometric features.
func buildLineTopology(analysis *GraphicAnalysis) []GraphicGroup {
	var groups []GraphicGroup

	// Vertical lines (x1 ≈ x2) → session/sweep markers
	var vertLines, horizLines, otherLines []int
	for i, l := range analysis.Lines {
		if math.Abs(l.X1-l.X2) < 1e-6 {
			vertLines = append(vertLines, i)
		} else if l.IsHorizontal {
			horizLines = append(horizLines, i)
		} else {
			otherLines = append(otherLines, i)
		}
	}

	if len(vertLines) > 0 {
		grp := GraphicGroup{Type: "session", Lines: vertLines, Confidence: 0.7}
		grp.computeBounds(analysis)
		groups = append(groups, grp)
	}

	// Horizontal lines → support/resistance/liquidity levels
	if len(horizLines) > 0 {
		// Sub-classify by style: dashed+extend → liquidity/breaker; solid → support/resistance
		var dashed []int
		var solid []int
		for _, idx := range horizLines {
			if analysis.Lines[idx].Style == "dsh" || analysis.Lines[idx].Style == "dash" {
				dashed = append(dashed, idx)
			} else {
				solid = append(solid, idx)
			}
		}
		if len(dashed) > 0 {
			grp := GraphicGroup{Type: "liquidity", Lines: dashed, Confidence: 0.7}
			grp.computeBounds(analysis)
			groups = append(groups, grp)
		}
		if len(solid) > 0 {
			grp := GraphicGroup{Type: "support_resistance", Lines: solid, Confidence: 0.6}
			grp.computeBounds(analysis)
			groups = append(groups, grp)
		}
	}

	if len(otherLines) > 0 {
		grp := GraphicGroup{Type: "trendline", Lines: otherLines, Confidence: 0.5}
		grp.computeBounds(analysis)
		groups = append(groups, grp)
	}

	return groups
}

// buildLabelTopology groups labels by text content into semantic categories.
func buildLabelTopology(analysis *GraphicAnalysis) []GraphicGroup {
	if len(analysis.Labels) == 0 {
		return nil
	}
	// Group by normalized text
	byText := map[string][]int{}
	for i, l := range analysis.Labels {
		key := normalizeLabelText(l.Text)
		byText[key] = append(byText[key], i)
	}

	var groups []GraphicGroup
	for text, indices := range byText {
		grp := GraphicGroup{
			Type:       inferLabelGroupType(text),
			Labels:     indices,
			Confidence: 0.8,
			Properties: map[string]any{"text": text},
		}
		grp.computeBounds(analysis)
		groups = append(groups, grp)
	}
	return groups
}

// normalizeLabelText uppercases and trims label text.
func normalizeLabelText(text string) string {
	return strings.ToUpper(strings.TrimSpace(text))
}

// inferLabelGroupType maps normalized label text to a semantic type.
func inferLabelGroupType(text string) string {
	switch {
	case strings.Contains(text, "BUY") || strings.Contains(text, "LONG") || strings.Contains(text, "BULL"):
		return "buy_signal"
	case strings.Contains(text, "SELL") || strings.Contains(text, "SHORT") || strings.Contains(text, "BEAR"):
		return "sell_signal"
	case text == "BOS" || strings.Contains(text, "BREAK OF STRUCTURE"):
		return "bos"
	case text == "CHOCH" || strings.Contains(text, "CHANGE OF CHARACTER"):
		return "choch"
	case strings.Contains(text, "LIQUID") || strings.Contains(text, "LS") || strings.Contains(text, "HS"):
		return "liquidity_sweep"
	case strings.Contains(text, "OB") || strings.Contains(text, "ORDER BLOCK"):
		return "order_block"
	case strings.Contains(text, "FVG") || strings.Contains(text, "FAIR VALUE"):
		return "fvg"
	case strings.Contains(text, "POC") || strings.Contains(text, "POINT OF CONTROL"):
		return "poc"
	case strings.Contains(text, "VAH"):
		return "vah"
	case strings.Contains(text, "VAL"):
		return "val"
	default:
		return "text"
	}
}

// ---------------------------------------------------------------------------
// 5. Main entry point: postProcessGraphicsGeneric
// ---------------------------------------------------------------------------

// postProcessGraphicsGeneric is the generic replacement for
// postProcessGraphics. It uses topology-based grouping instead of
// per-script pattern matchers.
func (a *UniversalAnalyzer) postProcessGraphicsGeneric(analysis *GraphicAnalysis, graphic map[string]map[string]any) {
	// 1. Detect color encoding (for diagnostics, not yet used to change behavior)
	enc := detectColorEncoding(analysis)
	_ = enc // available for future color-based classification

	// 2. Build box topology groups
	boxGroups := buildBoxTopology(analysis)

	// 3. Associate boxes with their bounding lines
	boxLineMap := associateBoxesWithLines(analysis)

	// 4. Build line topology groups
	lineGroups := buildLineTopology(analysis)

	// 5. Build label topology groups
	labelGroups := buildLabelTopology(analysis)

	// 6. Parse line fills (for zone reconstruction)
	fills := parseLineFills(graphic)

	// 7. Apply inferred types back to individual elements based on group membership
	a.applyGroupTypes(analysis, boxGroups, lineGroups, labelGroups, boxLineMap)

	// 8. Recover volume-profile peaks from box groups
	a.recoverVolumePeaksGeneric(analysis, boxGroups)

	// 9. Recover zones from line fills and box groups
	a.recoverZonesGeneric(analysis, fills, boxGroups)

	// 10. Recover breaker blocks from dashed extended lines
	a.recoverBreakersGeneric(analysis, lineGroups)
}

// applyGroupTypes reclassifies individual boxes/lines/labels based on the
// groups they belong to, overriding the flat heuristics from inferBoxType etc.
func (a *UniversalAnalyzer) applyGroupTypes(analysis *GraphicAnalysis, boxGroups, lineGroups, labelGroups []GraphicGroup, boxLineMap map[int][]int) {
	// Apply box group types
	for _, g := range boxGroups {
		for _, idx := range g.Boxes {
			if idx >= 0 && idx < len(analysis.Boxes) {
				analysis.Boxes[idx].InferredType = g.Type
				analysis.Boxes[idx].Confidence = g.Confidence
			}
		}
	}

	// Apply line group types
	for _, g := range lineGroups {
		for _, idx := range g.Lines {
			if idx >= 0 && idx < len(analysis.Lines) {
				switch g.Type {
				case "session":
					analysis.Lines[idx].InferredType = "session"
					analysis.Lines[idx].Confidence = 0.8
				case "liquidity":
					analysis.Lines[idx].InferredType = "liquidity"
					analysis.Lines[idx].Confidence = 0.7
				case "support_resistance":
					// Keep existing if already set to something specific
					if analysis.Lines[idx].InferredType == "" || analysis.Lines[idx].InferredType == "horizontal_level" {
						analysis.Lines[idx].InferredType = "support_resistance"
						analysis.Lines[idx].Confidence = 0.6
					}
				case "trendline":
					if analysis.Lines[idx].InferredType == "" {
						if analysis.Lines[idx].Slope > 0 {
							analysis.Lines[idx].InferredType = "trendline_up"
						} else {
							analysis.Lines[idx].InferredType = "trendline_down"
						}
						analysis.Lines[idx].Confidence = 0.5
					}
				}
			}
		}
	}

	// Apply label group types
	for _, g := range labelGroups {
		for _, idx := range g.Labels {
			if idx >= 0 && idx < len(analysis.Labels) {
				analysis.Labels[idx].InferredType = g.Type
				analysis.Labels[idx].Confidence = g.Confidence
			}
		}
	}

	// Reclassify boxes that have associated lines with specific types
	for boxIdx, lineIndices := range boxLineMap {
		if boxIdx >= len(analysis.Boxes) {
			continue
		}
		for _, li := range lineIndices {
			if li >= len(analysis.Lines) {
				continue
			}
			line := analysis.Lines[li]
			// If a box has a dashed extended line, it's a breaker block
			if line.Style == "dsh" && line.Extend == "r" {
				analysis.Boxes[boxIdx].InferredType = "breaker"
				analysis.Boxes[boxIdx].Confidence = 0.8
				break
			}
			// If a box has a solid non-extended line, it's an active order block
			if line.Style == "sol" && line.Extend == "n" {
				if analysis.Boxes[boxIdx].InferredType == "zone" || analysis.Boxes[boxIdx].InferredType == "price_zone" {
					analysis.Boxes[boxIdx].InferredType = "order_block"
					analysis.Boxes[boxIdx].Confidence = 0.75
				}
			}
		}
	}

	// Rebuild inferred type counts
	analysis.Summary.InferredTypes = make(map[string]int)
	for _, b := range analysis.Boxes {
		analysis.Summary.InferredTypes[b.InferredType]++
	}
	for _, l := range analysis.Lines {
		analysis.Summary.InferredTypes[l.InferredType]++
	}
	for _, l := range analysis.Labels {
		analysis.Summary.InferredTypes[l.InferredType]++
	}
}

// recoverVolumePeaksGeneric extracts POC/VAH/VAL from volume-profile groups.
func (a *UniversalAnalyzer) recoverVolumePeaksGeneric(analysis *GraphicAnalysis, groups []GraphicGroup) {
	for _, g := range groups {
		if g.Type != "volume_profile" {
			continue
		}
		stackCount, _ := g.Properties["stackCount"].(int)
		pocPrice, _ := g.Properties["pocPrice"].(float64)
		vah, _ := g.Properties["vah"].(float64)
		val, _ := g.Properties["val"].(float64)

		analysis.Summary.VolumePeaks = append(analysis.Summary.VolumePeaks, VolumePeak{
			PocPrice:   pocPrice,
			Vah:        vah,
			Val:        val,
			StackCount: stackCount,
			LeftBar:    g.LeftBar,
			Confidence: g.Confidence,
		})
	}
}

// recoverZonesGeneric extracts price zones from line fills and box groups.
func (a *UniversalAnalyzer) recoverZonesGeneric(analysis *GraphicAnalysis, fills []LineFillGraphic, boxGroups []GraphicGroup) {
	// From line fills: resolve to actual line Y values.
	// findLineYByID matches the drawing IDs stored in LineGraphic.ID.
	for _, fill := range fills {
		// Find lines by their drawing IDs
		y1, ok1 := findLineYByID(analysis, fill.Line1)
		y2, ok2 := findLineYByID(analysis, fill.Line2)
		if !ok1 || !ok2 || y1 <= 0 || y2 <= 0 {
			continue
		}
		top, bot := math.Max(y1, y2), math.Min(y1, y2)
		analysis.Summary.Zones = append(analysis.Summary.Zones, Zone{
			Top:        top,
			Bottom:     bot,
			Mid:        (top + bot) / 2,
			Confidence: 0.75,
		})
	}

	// From order_block box groups: each box is a zone
	for _, g := range boxGroups {
		if g.Type != "order_block" {
			continue
		}
		for _, idx := range g.Boxes {
			if idx >= len(analysis.Boxes) {
				continue
			}
			box := analysis.Boxes[idx]
			top := math.Max(box.Y1, box.Y2)
			bot := math.Min(box.Y1, box.Y2)
			analysis.Summary.Zones = append(analysis.Summary.Zones, Zone{
				Top:        top,
				Bottom:     bot,
				Mid:        (top + bot) / 2,
				LeftBar:    math.Min(box.X1, box.X2),
				RightBar:   math.Max(box.X1, box.X2),
				Confidence: box.Confidence,
			})
		}
	}
}

// recoverBreakersGeneric identifies breaker blocks from dashed extended lines.
func (a *UniversalAnalyzer) recoverBreakersGeneric(analysis *GraphicAnalysis, lineGroups []GraphicGroup) {
	for _, g := range lineGroups {
		if g.Type != "liquidity" {
			continue
		}
		// Dashed extended horizontal lines that form pairs (top/bottom of a zone)
		// can indicate breaker blocks
		for _, idx := range g.Lines {
			if idx >= len(analysis.Lines) {
				continue
			}
			line := analysis.Lines[idx]
			if line.Style == "dsh" && line.Extend == "r" && line.IsHorizontal {
				// This is a potential breaker/liquidity level
				analysis.Summary.InferredTypes["breaker"]++
			}
		}
	}
}

// ---------------------------------------------------------------------------
// Helper methods
// ---------------------------------------------------------------------------

// computeBounds calculates the price and time range of a group.
func (g *GraphicGroup) computeBounds(analysis *GraphicAnalysis) {
	g.PriceLow = math.MaxFloat64
	g.PriceHigh = -math.MaxFloat64
	g.LeftBar = math.MaxFloat64
	g.RightBar = -math.MaxFloat64

	for _, idx := range g.Boxes {
		if idx < 0 || idx >= len(analysis.Boxes) {
			continue
		}
		b := analysis.Boxes[idx]
		g.PriceLow = math.Min(g.PriceLow, b.Low)
		g.PriceHigh = math.Max(g.PriceHigh, b.High)
		g.LeftBar = math.Min(g.LeftBar, math.Min(b.X1, b.X2))
		g.RightBar = math.Max(g.RightBar, math.Max(b.X1, b.X2))
	}
	for _, idx := range g.Lines {
		if idx < 0 || idx >= len(analysis.Lines) {
			continue
		}
		l := analysis.Lines[idx]
		yMin := math.Min(l.Y1, l.Y2)
		yMax := math.Max(l.Y1, l.Y2)
		g.PriceLow = math.Min(g.PriceLow, yMin)
		g.PriceHigh = math.Max(g.PriceHigh, yMax)
		g.LeftBar = math.Min(g.LeftBar, math.Min(l.X1, l.X2))
		g.RightBar = math.Max(g.RightBar, math.Max(l.X1, l.X2))
	}
	for _, idx := range g.Labels {
		if idx < 0 || idx >= len(analysis.Labels) {
			continue
		}
		l := analysis.Labels[idx]
		g.PriceLow = math.Min(g.PriceLow, l.Y)
		g.PriceHigh = math.Max(g.PriceHigh, l.Y)
		g.LeftBar = math.Min(g.LeftBar, l.X)
		g.RightBar = math.Max(g.RightBar, l.X)
	}
}

// findLineYByID finds a horizontal line's Y value by its drawing ID.
// Since analysis.Lines stores ID as string, we match directly.
func findLineYByID(analysis *GraphicAnalysis, id string) (float64, bool) {
	for _, l := range analysis.Lines {
		if l.ID == id && l.IsHorizontal {
			return l.AvgPrice, true
		}
	}
	return 0, false
}

// ID_int is a helper to allow LineGraphic to expose its numeric ID.
// Since LineGraphic.ID is already a string, we provide a no-op.
// This is kept for API compatibility but not used in the main flow.
func (l LineGraphic) ID_int() float64 {
	if f, err := strconv.ParseFloat(l.ID, 64); err == nil {
		return f
	}
	return 0
}

// sortGroupsByConfidence sorts groups by confidence (descending).
func sortGroupsByConfidence(groups []GraphicGroup) {
	sort.Slice(groups, func(i, j int) bool {
		return groups[i].Confidence > groups[j].Confidence
	})
}
