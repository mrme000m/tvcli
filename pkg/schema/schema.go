package schema

import (
	"fmt"
	"strings"
)

// ScriptSchema is the complete self-documenting description of a Pine script's outputs.
// Built from metaInfo returned by the pine-facade /translate/ endpoint.
type ScriptSchema struct {
	PineID      string                `json:"pineId"`
	Name        string                `json:"name"`
	Description string                `json:"description"`
	IsStrategy  bool                  `json:"isStrategy"`
	IsOverlay   bool                  `json:"isOverlay"`
	Plots       []PlotDef             `json:"plots"`
	Inputs      []InputDef            `json:"inputs"`
	Styles      map[string]StyleDef   `json:"styles"`
	Graphics    GraphicsProfile       `json:"graphics"`
}

// PlotDef describes one plot column in the st[] data stream.
// The Index corresponds to plot_N in the raw period data.
type PlotDef struct {
	Index      int      `json:"index"`
	ID         string   `json:"id"`
	Name       string   `json:"name"`        // human-readable from styles.title
	StyleType  string   `json:"styleType"`   // "line", "histogram", "columns", "circles"
	PlotType   string   `json:"plotType"`    // "plot", "hline", "plotshape", "fill", "bgcolor"
	IsOverlay  bool     `json:"isOverlay"`
	IsHLine    bool     `json:"isHLine"`
	HLineValue *float64 `json:"hLineValue,omitempty"`
	Semantic   string   `json:"semantic"`    // "price", "signal", "oscillator", "band", "level", "color", "unknown"
	IsColor    bool     `json:"isColor"`     // bgcolor/barcolor/plotcolor
	TargetStyle string  `json:"targetStyle,omitempty"` // style ID this plot references
}

// InputDef describes a user-configurable input.
type InputDef struct {
	ID       string   `json:"id"`
	Name     string   `json:"name"`
	Type     string   `json:"type"`     // "integer", "float", "bool", "string", "color"
	Default  any      `json:"default"`
	Min      any      `json:"min,omitempty"`
	Max      any      `json:"max,omitempty"`
	Step     any      `json:"step,omitempty"`
	Options  []string `json:"options,omitempty"`
	Tooltip  string   `json:"tooltip,omitempty"`
	IsHidden bool     `json:"isHidden,omitempty"`
	IsFake   bool     `json:"isFake,omitempty"`
}

// StyleDef describes a visual style entry from metaInfo.styles.
type StyleDef struct {
	Title string `json:"title"`
	Type  string `json:"type"`
}

// GraphicsProfile summarizes what graphic types the script produces.
type GraphicsProfile struct {
	HasLines     bool     `json:"hasLines"`
	HasLabels    bool     `json:"hasLabels"`
	HasBoxes     bool     `json:"hasBoxes"`
	HasFills     bool     `json:"hasFills"`
	HasTables    bool     `json:"hasTables"`
	ToggleInputs []string `json:"toggleInputs,omitempty"` // boolean inputs that likely toggle graphics
}

// BuildSchema constructs a ScriptSchema from the metaInfo map returned by /translate/.
// This is the core introspection function — it turns opaque metadata into a structured
// description of what the script produces.
func BuildSchema(metaInfo map[string]any, pineID string) *ScriptSchema {
	if metaInfo == nil {
		return &ScriptSchema{PineID: pineID}
	}

	schema := &ScriptSchema{
		PineID: pineID,
		Styles: make(map[string]StyleDef),
	}

	// Extract script identity
	if v, ok := metaInfo["scriptIdPart"].(string); ok {
		schema.PineID = v
	}
	if v, ok := metaInfo["description"].(string); ok {
		schema.Description = v
		schema.Name = v
	}
	if v, ok := metaInfo["shortDescription"].(string); ok && v != "" {
		schema.Name = v
	}

	// Detect strategy vs indicator
	schema.IsStrategy = detectStrategy(metaInfo)
	schema.IsOverlay = detectOverlay(metaInfo)

	// Parse styles
	if styles, ok := metaInfo["styles"].(map[string]any); ok {
		for pid, styleRaw := range styles {
			styleMap, ok := styleRaw.(map[string]any)
			if !ok {
				continue
			}
			sd := StyleDef{}
			if t, ok := styleMap["title"].(string); ok {
				sd.Title = t
			}
			if t, ok := styleMap["type"].(string); ok {
				sd.Type = t
			}
			schema.Styles[pid] = sd
		}
	}

	// Parse plots — order matters! The Nth plot maps to plot_N in st[] data.
	if plotsArr, ok := metaInfo["plots"].([]any); ok {
		for i, pRaw := range plotsArr {
			pMap, ok := pRaw.(map[string]any)
			if !ok {
				continue
			}
			pd := PlotDef{
				Index: i,
			}
			if id, ok := pMap["id"].(string); ok {
				pd.ID = id
			}
			if pt, ok := pMap["type"].(string); ok {
				pd.PlotType = pt
			}
			if target, ok := pMap["target"].(string); ok {
				pd.TargetStyle = target
				// Look up the style for name and visual type
				if style, ok := schema.Styles[target]; ok {
					pd.Name = sanitizeName(style.Title)
					pd.StyleType = style.Type
				} else {
					pd.Name = pd.ID
				}
			} else {
				// No target — use the plot's own ID as name
				pd.Name = pd.ID
			}

			// Detect hline and extract value
			if pd.PlotType == "hline" {
				pd.IsHLine = true
				pd.Semantic = "level"
				// Try to find the hline value from inputs
				if val := findHLineValue(metaInfo, pd.ID, pd.TargetStyle); val != nil {
					pd.HLineValue = val
				}
			}

			// Detect color plots
			if pd.PlotType == "bgcolor" || pd.PlotType == "barcolor" || pd.PlotType == "plotcolor" {
				pd.IsColor = true
				pd.Semantic = "color"
			}

			// Overlay detection from plot metadata
			if ov, ok := pMap["isOverlay"].(bool); ok {
				pd.IsOverlay = ov
			} else if schema.IsOverlay {
				pd.IsOverlay = true
			}

			// Classify semantic from metadata before falling through to statistical
			if pd.Semantic == "" {
				pd.Semantic = classifyFromMetadata(pd, schema.Description)
			}

			schema.Plots = append(schema.Plots, pd)
		}
	}

	// Parse inputs
	if inputsArr, ok := metaInfo["inputs"].([]any); ok {
		for _, inpRaw := range inputsArr {
			inpMap, ok := inpRaw.(map[string]any)
			if !ok {
				continue
			}
			id, _ := inpMap["id"].(string)
			if id == "" || id == "text" || id == "pineId" || id == "pineVersion" || id == "__profile" || id == "pineFeatures" {
				continue
			}
			inp := InputDef{
				ID: id,
			}
			if n, ok := inpMap["name"].(string); ok {
				inp.Name = n
			} else {
				inp.Name = id
			}
			if t, ok := inpMap["type"].(string); ok {
				inp.Type = t
			}
			if d, ok := inpMap["defval"]; ok {
				inp.Default = d
			}
			if m, ok := inpMap["minval"]; ok {
				inp.Min = m
			}
			if m, ok := inpMap["maxval"]; ok {
				inp.Max = m
			}
			if s, ok := inpMap["step"]; ok {
				inp.Step = s
			}
			if opts, ok := inpMap["options"].([]any); ok {
				for _, o := range opts {
					if s, ok := o.(string); ok {
						inp.Options = append(inp.Options, s)
					}
				}
			}
			if t, ok := inpMap["tooltip"].(string); ok {
				inp.Tooltip = t
			}
			if h, ok := inpMap["isHidden"].(bool); ok {
				inp.IsHidden = h
			}
			if f, ok := inpMap["isFake"].(bool); ok {
				inp.IsFake = f
			}
			schema.Inputs = append(schema.Inputs, inp)
		}
	}

	// Detect graphics toggle inputs
	schema.Graphics.ToggleInputs = findGraphicsToggles(schema.Inputs)

	return schema
}

// PlotName returns the human-readable name for a plot at the given 0-based index.
// Returns empty string if index is out of range.
func (s *ScriptSchema) PlotName(index int) string {
	if s == nil || index < 0 || index >= len(s.Plots) {
		return ""
	}
	name := s.Plots[index].Name
	if name == "" {
		return fmt.Sprintf("plot_%d", index)
	}
	return name
}

// PlotByIndex returns the PlotDef for a given 0-based index.
func (s *ScriptSchema) PlotByIndex(index int) *PlotDef {
	if s == nil || index < 0 || index >= len(s.Plots) {
		return nil
	}
	return &s.Plots[index]
}

// HLineLevels returns all fixed horizontal line levels from the schema.
func (s *ScriptSchema) HLineLevels() []HLineLevel {
	if s == nil {
		return nil
	}
	var levels []HLineLevel
	for _, p := range s.Plots {
		if p.IsHLine && p.HLineValue != nil {
			levels = append(levels, HLineLevel{
				Name:  p.Name,
				Value: *p.HLineValue,
				Index: p.Index,
			})
		}
	}
	return levels
}

// HLineLevel is a fixed horizontal line extracted from the schema.
type HLineLevel struct {
	Name  string  `json:"name"`
	Value float64 `json:"value"`
	Index int     `json:"index"`
}

// SignalFields returns plots classified as signals.
func (s *ScriptSchema) SignalFields() []PlotDef {
	if s == nil {
		return nil
	}
	var out []PlotDef
	for _, p := range s.Plots {
		if p.Semantic == "signal" {
			out = append(out, p)
		}
	}
	return out
}

// PriceFields returns plots classified as price levels.
func (s *ScriptSchema) PriceFields() []PlotDef {
	if s == nil {
		return nil
	}
	var out []PlotDef
	for _, p := range s.Plots {
		if p.Semantic == "price" || p.Semantic == "level" {
			out = append(out, p)
		}
	}
	return out
}

// --- helpers ---

func sanitizeName(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return s
	}
	// Replace spaces with underscores, strip non-alphanumeric
	var b strings.Builder
	for _, r := range s {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '_' {
			b.WriteRune(r)
		} else if r == ' ' || r == '-' {
			b.WriteRune('_')
		}
	}
	result := b.String()
	// Collapse multiple underscores
	for strings.Contains(result, "__") {
		result = strings.ReplaceAll(result, "__", "_")
	}
	result = strings.Trim(result, "_")
	if result == "" {
		return raw
	}
	return result
}

func detectStrategy(metaInfo map[string]any) bool {
	// Check description
	if desc, ok := metaInfo["description"].(string); ok {
		lower := strings.ToLower(desc)
		if strings.Contains(lower, "strategy") {
			return true
		}
	}
	// Check inputs for strategy-specific fields
	if inputs, ok := metaInfo["inputs"].([]any); ok {
		for _, inp := range inputs {
			if m, ok := inp.(map[string]any); ok {
				if id, ok := m["id"].(string); ok {
					if strings.Contains(id, "calc_on_order") || strings.Contains(id, "pyramiding") ||
						strings.Contains(id, "commission") || strings.Contains(id, "slippage") {
						return true
					}
				}
			}
		}
	}
	return false
}

func detectOverlay(metaInfo map[string]any) bool {
	if desc, ok := metaInfo["description"].(string); ok {
		lower := strings.ToLower(desc)
		// Common overlay indicators
		overlayKeywords := []string{"moving average", "ema", "sma", "bollinger", "vwap",
			"pivot", "support", "resistance", "ichimoku", "super trend", "psar",
			"donchian", "keltner", "envelope", "ma ", " ema", " sma"}
		for _, kw := range overlayKeywords {
			if strings.Contains(lower, kw) {
				return true
			}
		}
	}
	// Check if first non-hline plot has isOverlay
	if plots, ok := metaInfo["plots"].([]any); ok {
		for _, p := range plots {
			if m, ok := p.(map[string]any); ok {
				pt, _ := m["type"].(string)
				if pt == "hline" || pt == "fill" || pt == "bgcolor" || pt == "barcolor" {
					continue
				}
				if ov, ok := m["isOverlay"].(bool); ok {
					return ov
				}
				break // only check first real plot
			}
		}
	}
	return false
}

func findHLineValue(metaInfo map[string]any, plotID, targetStyle string) *float64 {
	// hline values are typically defined by input defaults
	// Look for inputs whose name matches the hline's style title
	if inputs, ok := metaInfo["inputs"].([]any); ok {
		for _, inp := range inputs {
			m, ok := inp.(map[string]any)
			if !ok {
				continue
			}
			// Check if input name matches the style title
			name, _ := m["name"].(string)
			id, _ := m["id"].(string)
			defval := m["defval"]

			// Match by name similarity to the plot/style
			if name != "" && targetStyle != "" {
				// Normalize and compare
				normName := strings.ToLower(strings.ReplaceAll(name, " ", ""))
				normTarget := strings.ToLower(strings.ReplaceAll(targetStyle, " ", ""))
				if strings.Contains(normName, normTarget) || strings.Contains(normTarget, normName) {
					if f, ok := toFloat(defval); ok {
						return &f
					}
				}
			}
			// Also check if input ID contains the plot ID
			if id != "" && plotID != "" {
				if strings.Contains(id, plotID) || strings.Contains(plotID, id) {
					if f, ok := toFloat(defval); ok {
						return &f
					}
				}
			}
		}
	}
	return nil
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case string:
		// Try parsing
		var f float64
		n, _ := fmt.Sscanf(x, "%f", &f)
		if n == 1 {
			return f, true
		}
	}
	return 0, false
}

func findGraphicsToggles(inputs []InputDef) []string {
	var toggles []string
	for _, inp := range inputs {
		if inp.Type != "bool" || inp.IsHidden || inp.IsFake {
			continue
		}
		lower := strings.ToLower(inp.Name)
		lowerID := strings.ToLower(inp.ID)
		graphicsKeywords := []string{"show", "display", "visible", "label", "line", "box",
			"fill", "table", "signal", "alert", "arrow", "shape"}
		for _, kw := range graphicsKeywords {
			if strings.Contains(lower, kw) || strings.Contains(lowerID, kw) {
				toggles = append(toggles, inp.ID)
				break
			}
		}
	}
	return toggles
}
