// Package schema parses TradingView metaInfo into a structured PineSchema
// that bridges raw plot indices (plot_0, plot_1) to semantic names and types.
package schema

import (
	"fmt"
	"strings"
)

// PlotDecl describes a single plot declared in metaInfo.
type PlotDecl struct {
	Index     int    // 0-based position in the st.v array
	Name      string // semantic name from metaInfo (e.g. "momentum", "upperBB")
	Title     string // display title from styles (e.g. "Momentum")
	PlotType  string // histogram, line, area, cross, columns, circles, area_br, step_line
	StyleID   string // key in styles map
	Palette   string // palette name for colorer plots (empty if not a colorer)
	IsColorer bool   // true when this plot drives a color palette
}

// StyleDecl describes visual configuration for a named plot.
type StyleDecl struct {
	Title         string
	PlotType      string
	TrackPrice    bool
	HistogramBase float64
	ColorPalette  string
	LineColor     string
	LineWidth     int
}

// PaletteDecl describes a color palette used by colorer plots.
type PaletteDecl struct {
	Name      string
	Colors    map[int]string // index → hex color
	ValToExpr string         // Pine expression that maps value → color index
}

// InputDecl describes a script input with optional link to output plots.
type InputDecl struct {
	Name   string
	Type   string // int, float, bool, string, symbol, session, source, resolution
	DefVal any
	Title  string
	Group  string
}

// PineSchema is the compiled declaration of a Pine script's I/O contract.
type PineSchema struct {
	PineID    string
	Version   string
	Plots     []PlotDecl         // ordered by index
	PlotByName map[string]PlotDecl // quick lookup by semantic name
	Styles    map[string]StyleDecl
	Palettes  map[string]PaletteDecl
	Inputs    []InputDecl
	IsStrategy bool
}

// FromMetaInfo compiles a PineSchema from the raw metaInfo map returned
// by the /translate/ endpoint. Returns nil when metaInfo is empty.
func FromMetaInfo(pineID string, metaInfo map[string]any) *PineSchema {
	if metaInfo == nil {
		return nil
	}

	s := &PineSchema{
		PineID:     pineID,
		PlotByName: make(map[string]PlotDecl),
		Styles:     make(map[string]StyleDecl),
		Palettes:   make(map[string]PaletteDecl),
	}

	// Version
	if pine, ok := metaInfo["pine"].(map[string]any); ok {
		if v, ok := pine["version"].(string); ok {
			s.Version = v
		}
	}

	// Strategy detection
	if script, ok := metaInfo["script"].(map[string]any); ok {
		if calc, ok := script["calcStyle"].(string); ok {
			s.IsStrategy = calc == "strategy" || calc == "strategy_only"
		}
	}
	// Also check top-level
	if t, ok := metaInfo["type"].(string); ok {
		s.IsStrategy = t == "strategy"
	}

	// Parse plots: metaInfo.plots is a map of plot_id → {name, id}
	if plots, ok := metaInfo["plots"].(map[string]any); ok {
		s.parsePlots(plots)
	}

	// Parse styles: metaInfo.styles is a map of style_name → config
	if styles, ok := metaInfo["styles"].(map[string]any); ok {
		s.parseStyles(styles)
	}

	// Cross-reference: attach style info to plots
	s.crossReference()

	// Parse palettes: metaInfo.palettes is a map of palette_name → {colors, valToIndex}
	if palettes, ok := metaInfo["palettes"].(map[string]any); ok {
		s.parsePalettes(palettes)
	}

	// Parse inputs: metaInfo.inputs is an array
	if inputs, ok := metaInfo["inputs"].([]any); ok {
		s.parseInputs(inputs)
	}

	if len(s.Plots) == 0 && len(s.Styles) == 0 {
		return nil // no useful schema
	}

	return s
}

func (s *PineSchema) parsePlots(raw map[string]any) {
	// plots map: key is "plot_0", "plot_1", etc. or just "0", "1"
	// value is { "name": "momentum", "id": "plot_0" } or similar
	for key, val := range raw {
		pMap, ok := val.(map[string]any)
		if !ok {
			continue
		}

		name, _ := pMap["name"].(string)
		if name == "" {
			// Try "id" as fallback
			name, _ = pMap["id"].(string)
		}
		if name == "" {
			continue
		}

		idx := parsePlotIndex(key)

		decl := PlotDecl{
			Index: idx,
			Name:  name,
		}

		// Check if this plot is a colorer (has palette reference)
		if palette, ok := pMap["palette"].(string); ok && palette != "" {
			decl.Palette = palette
			decl.IsColorer = true
		}
		// Some formats use "colorPalette" or "target"
		if target, ok := pMap["target"].(string); ok && target != "" {
			decl.StyleID = target
		}

		s.Plots = append(s.Plots, decl)
		s.PlotByName[name] = decl
	}

	// Sort by index
	for i := 0; i < len(s.Plots); i++ {
		for j := i + 1; j < len(s.Plots); j++ {
			if s.Plots[j].Index < s.Plots[i].Index {
				s.Plots[i], s.Plots[j] = s.Plots[j], s.Plots[i]
			}
		}
	}
}

func (s *PineSchema) parseStyles(raw map[string]any) {
	for name, val := range raw {
		sMap, ok := val.(map[string]any)
		if !ok {
			continue
		}

		st := StyleDecl{Title: name}

		if title, ok := sMap["title"].(string); ok {
			st.Title = title
		}
		if pt, ok := sMap["plottype"].(string); ok {
			st.PlotType = pt
		}
		if tp, ok := sMap["trackPrice"].(bool); ok {
			st.TrackPrice = tp
		}
		if hb, ok := sMap["histogramBase"].(float64); ok {
			st.HistogramBase = hb
		}
		if cp, ok := sMap["colorPalette"].(string); ok {
			st.ColorPalette = cp
		}
		if lc, ok := sMap["linecolor"].(string); ok {
			st.LineColor = lc
		}
		if lw, ok := sMap["linewidth"].(float64); ok {
			st.LineWidth = int(lw)
		}

		s.Styles[name] = st
	}
}

func (s *PineSchema) parsePalettes(raw map[string]any) {
	for name, val := range raw {
		pMap, ok := val.(map[string]any)
		if !ok {
			continue
		}

		p := PaletteDecl{Name: name, Colors: make(map[int]string)}

		if colors, ok := pMap["colors"].(map[string]any); ok {
			for idxStr, cVal := range colors {
				cMap, ok := cVal.(map[string]any)
				if !ok {
					continue
				}
				if hex, ok := cMap["color"].(string); ok {
					idx := 0
					for _, ch := range idxStr {
						if ch >= '0' && ch <= '9' {
							idx = idx*10 + int(ch-'0')
						} else {
							break
						}
					}
					p.Colors[idx] = hex
				}
			}
		}

		if vti, ok := pMap["valToIndex"].(string); ok {
			p.ValToExpr = vti
		}

		s.Palettes[name] = p
	}
}

func (s *PineSchema) parseInputs(raw []any) {
	for _, inp := range raw {
		iMap, ok := inp.(map[string]any)
		if !ok {
			continue
		}

		id, _ := iMap["id"].(string)
		if id == "" || id == "text" || id == "pineId" || id == "pineVersion" || id == "__profile" {
			continue
		}

		d := InputDecl{Name: id}
		if t, ok := iMap["type"].(string); ok {
			d.Type = t
		}
		if dv, ok := iMap["defval"]; ok {
			d.DefVal = dv
		}
		if title, ok := iMap["title"].(string); ok {
			d.Title = title
		}
		if group, ok := iMap["group"].(string); ok {
			d.Group = group
		}

		s.Inputs = append(s.Inputs, d)
	}
}

func (s *PineSchema) crossReference() {
	for i, p := range s.Plots {
		// Match style by name
		if st, ok := s.Styles[p.Name]; ok {
			s.Plots[i].Title = st.Title
			s.Plots[i].PlotType = st.PlotType
			s.Plots[i].Palette = st.ColorPalette
			s.Plots[i].IsColorer = st.ColorPalette != ""
			s.Plots[i].StyleID = p.Name
		}
		// Also try StyleID if set from plot definition
		if p.StyleID != "" {
			if st, ok := s.Styles[p.StyleID]; ok {
				if s.Plots[i].Title == "" {
					s.Plots[i].Title = st.Title
				}
				if s.Plots[i].PlotType == "" {
					s.Plots[i].PlotType = st.PlotType
				}
				if s.Plots[i].Palette == "" {
					s.Plots[i].Palette = st.ColorPalette
					s.Plots[i].IsColorer = st.ColorPalette != ""
				}
			}
		}
		// Update the map too
		s.PlotByName[p.Name] = s.Plots[i]
	}
}

// PlotTypeCategory returns a high-level category for a plot type.
func PlotTypeCategory(plotType string) string {
	switch strings.ToLower(plotType) {
	case "histogram", "columns":
		return "histogram"
	case "cross":
		return "reference"
	case "line", "step_line":
		return "line"
	case "area", "area_br":
		return "band"
	case "circles":
		return "marker"
	default:
		return "line"
	}
}

// IsSignalPlot returns true when the plot type suggests a binary/discrete signal.
func IsSignalPlot(plotType string) bool {
	switch strings.ToLower(plotType) {
	case "cross", "marker", "circles":
		return true
	default:
		return false
	}
}

// IsBandPlot returns true when the plot type suggests a price band/area.
func IsBandPlot(plotType string) bool {
	switch strings.ToLower(plotType) {
	case "area", "area_br", "band":
		return true
	default:
		return false
	}
}

// parsePlotIndex extracts the numeric index from keys like "plot_0", "0", "plot_12".
func parsePlotIndex(key string) int {
	// Try "plot_N" format
	if strings.HasPrefix(key, "plot_") {
		n := 0
		for _, ch := range key[5:] {
			if ch >= '0' && ch <= '9' {
				n = n*10 + int(ch-'0')
			} else {
				break
			}
		}
		return n
	}
	// Try pure numeric
	n := 0
	for _, ch := range key {
		if ch >= '0' && ch <= '9' {
			n = n*10 + int(ch-'0')
		} else {
			return 0
		}
	}
	return n
}

// Summary returns a human-readable overview of the schema.
func (s *PineSchema) Summary() string {
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("Schema: %s (v%s, strategy=%v)\n", s.PineID, s.Version, s.IsStrategy))
	sb.WriteString(fmt.Sprintf("  Plots: %d\n", len(s.Plots)))
	for _, p := range s.Plots {
		sb.WriteString(fmt.Sprintf("    [%d] %-20s type=%-10s palette=%s\n", p.Index, p.Name, p.PlotType, p.Palette))
	}
	sb.WriteString(fmt.Sprintf("  Styles: %d\n", len(s.Styles)))
	for name, st := range s.Styles {
		sb.WriteString(fmt.Sprintf("    %-20s plottype=%s\n", name, st.PlotType))
	}
	sb.WriteString(fmt.Sprintf("  Palettes: %d\n", len(s.Palettes)))
	for name, p := range s.Palettes {
		sb.WriteString(fmt.Sprintf("    %-20s colors=%d expr=%s\n", name, len(p.Colors), p.ValToExpr))
	}
	sb.WriteString(fmt.Sprintf("  Inputs: %d\n", len(s.Inputs)))
	return sb.String()
}
