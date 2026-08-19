package schema

import (
	"fmt"
	"strings"
)

// PineSchema is the type stored on PineIndicator.Schema.
// Alias to ScriptSchema for backward compatibility.
type PineSchema = ScriptSchema

// PlotDecl is the type used by dynparse and other consumers.
// Alias to PlotDef for backward compatibility.
type PlotDecl = PlotDef

// FromMetaInfo is the entry point called from NewPineIndicator.
// Wraps BuildSchema with the metaInfo map. Returns nil for nil metaInfo.
func FromMetaInfo(pineID string, metaInfo map[string]any) *PineSchema {
	if metaInfo == nil {
		return nil
	}
	sch := BuildSchema(metaInfo, pineID)
	if sch == nil || len(sch.Plots) == 0 {
		return nil
	}
	return sch
}

// ScriptSchema is the complete self-documenting description of a Pine script's outputs.
// Built from metaInfo returned by the pine-facade /translate/ endpoint.
type ScriptSchema struct {
	PineID      string                `json:"pineId"`
	Version     string                `json:"version,omitempty"`
	Name        string                `json:"name"`
	Description string                `json:"description"`
	IsStrategy  bool                  `json:"isStrategy"` // true = strategy (emits signals); false = indicator (analysis only, see pkg/pipeline.ScriptType)
	IsOverlay   bool                  `json:"isOverlay"`
	Plots       []PlotDef             `json:"plots"`
	PlotByName  map[string]PlotDef    `json:"plotByName,omitempty"` // name → PlotDef lookup
	Palettes    map[string]PaletteDef `json:"palettes,omitempty"`   // color palettes
	Inputs      []InputDef            `json:"inputs"`
	Styles      map[string]StyleDef   `json:"styles"`
	Graphics    GraphicsProfile       `json:"graphics"`
}

// PaletteDef describes a color palette from metaInfo.palettes.
type PaletteDef struct {
	Colors     map[string]ColorEntry `json:"colors"`
	ValToIndex string                `json:"valToIndex,omitempty"`
}

// ColorEntry is a single color in a palette.
type ColorEntry struct {
	Color string `json:"color"`
}

// PlotTypeCategory returns the high-level category for a plot type string.
func PlotTypeCategory(pt string) string {
	switch pt {
	case "histogram", "columns":
		return "histogram"
	case "cross":
		return "reference"
	case "line", "step_line", "":
		return "line"
	case "area", "area_br":
		return "band"
	case "circles", "marker":
		return "marker"
	default:
		return "line"
	}
}

// IsSignalPlot returns true if the plot type is typically used for signals.
func IsSignalPlot(pt string) bool {
	switch pt {
	case "cross", "circles", "marker":
		return true
	}
	return false
}

// IsBandPlot returns true if the plot type represents a band/area.
func IsBandPlot(pt string) bool {
	switch pt {
	case "area", "area_br":
		return true
	}
	return false
}

// parsePlotIndex extracts the numeric index from a "plot_N" key.
func parsePlotIndex(key string) int {
	if len(key) > 5 && key[:5] == "plot_" {
		n := 0
		for i := 5; i < len(key); i++ {
			if key[i] >= '0' && key[i] <= '9' {
				n = n*10 + int(key[i]-'0')
			} else {
				return -1
			}
		}
		return n
	}
	// Plain numeric key
	n := 0
	for i := 0; i < len(key); i++ {
		if key[i] >= '0' && key[i] <= '9' {
			n = n*10 + int(key[i]-'0')
		} else {
			return -1
		}
	}
	return n
}

// PlotDef describes one plot column in the st[] data stream.
// The Index corresponds to plot_N in the raw period data.
type PlotDef struct {
	Index       int      `json:"index"`
	ID          string   `json:"id"`
	Name        string   `json:"name"`      // human-readable from styles.title
	StyleType   string   `json:"styleType"` // "line", "histogram", "columns", "circles"
	PlotType    string   `json:"plotType"`  // "plot", "hline", "plotshape", "fill", "bgcolor"
	IsOverlay   bool     `json:"isOverlay"`
	IsHLine     bool     `json:"isHLine"`
	IsColorer   bool     `json:"isColorer"`         // bgcolor/barcolor/plotcolor (alias for dynparse compat)
	Palette     string   `json:"palette,omitempty"` // color palette ID
	HLineValue  *float64 `json:"hLineValue,omitempty"`
	Semantic    string   `json:"semantic"`              // "price", "signal", "oscillator", "band", "level", "color", "unknown"
	IsColor     bool     `json:"isColor"`               // bgcolor/barcolor/plotcolor
	TargetStyle string   `json:"targetStyle,omitempty"` // style ID this plot references
}

// InputDef describes a user-configurable input.
type InputDef struct {
	ID       string   `json:"id"`
	Name     string   `json:"name"`
	Type     string   `json:"type"` // "integer", "float", "bool", "string", "color"
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
	Title        string `json:"title"`
	Type         string `json:"type"`                   // plottype: "line", "histogram", etc.
	ColorPalette string `json:"colorPalette,omitempty"` // palette ID
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
	// Extract version from pine.version or direct version field
	if pine, ok := metaInfo["pine"].(map[string]any); ok {
		if v, ok := pine["version"].(string); ok {
			schema.Version = v
		}
	}
	if schema.Version == "" {
		if v, ok := metaInfo["version"].(string); ok {
			schema.Version = v
		}
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
			// Handle both "type" and "plottype" keys
			if t, ok := styleMap["type"].(string); ok {
				sd.Type = t
			} else if t, ok := styleMap["plottype"].(string); ok {
				sd.Type = t
			}
			if cp, ok := styleMap["colorPalette"].(string); ok {
				sd.ColorPalette = cp
			}
			schema.Styles[pid] = sd
		}
	}

	// Parse plots — order matters! The Nth plot maps to plot_N in st[] data.
	// Handle both array format (actual API) and map format (tests).
	switch plots := metaInfo["plots"].(type) {
	case []any:
		// Array format: [{id, type, target}, ...]
		for i, pRaw := range plots {
			pMap, ok := pRaw.(map[string]any)
			if !ok {
				continue
			}
			pd := buildPlotDefFromMap(i, pMap, schema)
			schema.Plots = append(schema.Plots, pd)
		}
	case map[string]any:
		// Map format: {"plot_0": {name, ...}, "plot_1": {...}}
		// Extract numeric keys and sort them for correct ordering
		type plotEntry struct {
			key  string
			data map[string]any
		}
		var entries []plotEntry
		for k, v := range plots {
			if m, ok := v.(map[string]any); ok {
				entries = append(entries, plotEntry{key: k, data: m})
			}
		}
		// Sort by plot index
		for i := 0; i < len(entries); i++ {
			for j := i + 1; j < len(entries); j++ {
				ii := parsePlotIndex(entries[i].key)
				jj := parsePlotIndex(entries[j].key)
				if ii > jj {
					entries[i], entries[j] = entries[j], entries[i]
				}
			}
		}
		for i, entry := range entries {
			pd := buildPlotDefFromMapEntry(i, entry.key, entry.data, schema)
			schema.Plots = append(schema.Plots, pd)
		}
	}

	// Parse palettes
	if palettes, ok := metaInfo["palettes"].(map[string]any); ok {
		schema.Palettes = make(map[string]PaletteDef)
		for palID, palRaw := range palettes {
			palMap, ok := palRaw.(map[string]any)
			if !ok {
				continue
			}
			pal := PaletteDef{}
			if vti, ok := palMap["valToIndex"].(string); ok {
				pal.ValToIndex = vti
			}
			if colorsMap, ok := palMap["colors"].(map[string]any); ok {
				pal.Colors = make(map[string]ColorEntry)
				for ck, cv := range colorsMap {
					if cm, ok := cv.(map[string]any); ok {
						if c, ok := cm["color"].(string); ok {
							pal.Colors[ck] = ColorEntry{Color: c}
						}
					}
				}
			}
			schema.Palettes[palID] = pal
		}
	}

	// Build PlotByName lookup map for O(1) access by name
	if len(schema.Plots) > 0 {
		schema.PlotByName = make(map[string]PlotDef, len(schema.Plots))
		for _, p := range schema.Plots {
			if p.Name != "" {
				schema.PlotByName[p.Name] = p
			}
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

// Summary returns a human-readable summary of the schema.
func (s *ScriptSchema) Summary() string {
	if s == nil {
		return "No schema"
	}
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("Schema: %s\n", s.PineID))
	if s.Name != "" {
		sb.WriteString(fmt.Sprintf("  Name: %s\n", s.Name))
	}
	if s.Version != "" {
		sb.WriteString(fmt.Sprintf("  Version: %s\n", s.Version))
	}
	strategyLabel := "Strategy"
	if !s.IsStrategy {
		strategyLabel = "Indicator"
	}
	sb.WriteString(fmt.Sprintf("  %s: %v | Overlay: %v\n", strategyLabel, s.IsStrategy, s.IsOverlay))
	sb.WriteString(fmt.Sprintf("  Plots: %d | Inputs: %d\n", len(s.Plots), len(s.Inputs)))

	if len(s.Plots) > 0 {
		sb.WriteString("  Plot mapping:\n")
		for _, p := range s.Plots {
			extra := ""
			if p.IsHLine && p.HLineValue != nil {
				extra = fmt.Sprintf(" (hline=%.2f)", *p.HLineValue)
			}
			sb.WriteString(fmt.Sprintf("    plot_%d → %s [%s/%s] %s\n",
				p.Index, p.Name, p.PlotType, p.StyleType, extra))
		}
	}

	if len(s.Graphics.ToggleInputs) > 0 {
		sb.WriteString(fmt.Sprintf("  Graphics toggles: %v\n", s.Graphics.ToggleInputs))
	}

	// Strategy-specific info
	if s.IsStrategy {
		sb.WriteString("\n  Strategy-specific fields:\n")
		// Check for common strategy inputs
		strategyInputs := []string{"commission", "slippage", "pyramiding", "calc_on_order"}
		foundStrategyInputs := []string{}
		for _, inp := range s.Inputs {
			for _, si := range strategyInputs {
				if strings.Contains(strings.ToLower(inp.Name), strings.ToLower(si)) ||
					strings.Contains(strings.ToLower(inp.ID), strings.ToLower(si)) {
					foundStrategyInputs = append(foundStrategyInputs, inp.Name+" ("+inp.ID+")")
					break
				}
			}
		}
		if len(foundStrategyInputs) > 0 {
			sb.WriteString("    Strategy inputs detected: " + strings.Join(foundStrategyInputs, ", ") + "\n")
		} else {
			sb.WriteString("    No typical strategy inputs detected (commission, slippage, pyramiding, calc_on_order)\n")
		}
	}

	return sb.String()
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

// buildPlotDefFromMap builds a PlotDef from a plot map entry (array format).
func buildPlotDefFromMap(index int, pMap map[string]any, schema *ScriptSchema) PlotDef {
	pd := PlotDef{Index: index}
	if id, ok := pMap["id"].(string); ok {
		pd.ID = id
	}
	if pt, ok := pMap["type"].(string); ok {
		pd.PlotType = pt
	}
	if target, ok := pMap["target"].(string); ok {
		pd.TargetStyle = target
		style, found := schema.Styles[target]
		if found {
			pd.Name = sanitizeName(style.Title)
			pd.StyleType = style.Type
		} else {
			// Try looking up by plot ID as well
			style2, found2 := schema.Styles[pd.ID]
			if found2 {
				pd.Name = sanitizeName(style2.Title)
				pd.StyleType = style2.Type
			} else {
				pd.Name = pd.ID
			}
		}
	} else {
		// No target — try looking up by plot ID
		style, found := schema.Styles[pd.ID]
		if found {
			pd.Name = sanitizeName(style.Title)
			pd.StyleType = style.Type
		} else {
			pd.Name = pd.ID
		}
	}
	// Colorer plots share their parent plot's style title, which collides with
	// the data plot's name in field maps and PlotByName lookups (the colorer's
	// palette index then overwrites the real value). Suffix the name like the
	// service-layer buildPlotsMap does (parent + "_" + type) and mark the plot
	// as a colorer so classification treats it as style, not data.
	if pd.PlotType == "colorer" || pd.PlotType == "bg_colorer" {
		pd.IsColorer = true
		if pd.Name != "" {
			pd.Name = pd.Name + "_" + pd.PlotType
		}
	}
	applyPlotProperties(&pd, pMap, schema)
	return pd
}

// buildPlotDefFromMapEntry builds a PlotDef from a map-format plot entry.
func buildPlotDefFromMapEntry(index int, key string, data map[string]any, schema *ScriptSchema) PlotDef {
	pd := PlotDef{Index: index, ID: key}

	// In map format, the plot entry may have a "name" field that is the style key
	styleKey := ""
	if name, ok := data["name"].(string); ok {
		styleKey = name
	}

	if styleKey != "" {
		pd.TargetStyle = styleKey
		if style, ok := schema.Styles[styleKey]; ok {
			pd.Name = styleKey // Use the style key as name (matches test expectation)
			pd.StyleType = style.Type
		} else {
			pd.Name = styleKey
		}
	}

	// Check for plottype in the data itself, or from the style
	if pt, ok := data["plottype"].(string); ok {
		pd.PlotType = pt
	} else if styleKey != "" {
		if style, ok := schema.Styles[styleKey]; ok && style.Type != "" {
			pd.PlotType = style.Type
		}
	}

	// Check for colorPalette in the data itself, or from the style
	if cp, ok := data["colorPalette"].(string); ok {
		pd.Palette = cp
	} else if styleKey != "" {
		if style, ok := schema.Styles[styleKey]; ok && style.ColorPalette != "" {
			pd.Palette = style.ColorPalette
		}
	}

	applyPlotProperties(&pd, data, schema)
	return pd
}

// applyPlotProperties applies common plot properties (hline, color, overlay, semantic).
func applyPlotProperties(pd *PlotDef, data map[string]any, schema *ScriptSchema) {
	// Detect hline
	if pd.PlotType == "hline" {
		pd.IsHLine = true
		pd.Semantic = "level"
		if val := findHLineValue(schema, pd.ID, pd.TargetStyle); val != nil {
			pd.HLineValue = val
		}
	}

	// Detect color plots
	if pd.PlotType == "bgcolor" || pd.PlotType == "barcolor" || pd.PlotType == "plotcolor" {
		pd.IsColor = true
		pd.IsColorer = true
		pd.Semantic = "color"
	}

	// Detect colorer from palette
	if pd.Palette != "" {
		pd.IsColorer = true
	}

	// Overlay detection
	if ov, ok := data["isOverlay"].(bool); ok {
		pd.IsOverlay = ov
	} else if schema.IsOverlay {
		pd.IsOverlay = true
	}

	// Classify semantic from metadata
	if pd.Semantic == "" {
		pd.Semantic = classifyFromMetadata(*pd, schema.Description)
	}
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

func findHLineValue(sch *ScriptSchema, plotID, targetStyle string) *float64 {
	if sch == nil {
		return nil
	}
	// hline values are typically defined by input defaults
	// Look for inputs whose name matches the hline's style title
	for _, inp := range sch.Inputs {
		// Match by name similarity to the plot/style
		if inp.Name != "" && targetStyle != "" {
			normName := strings.ToLower(strings.ReplaceAll(inp.Name, " ", ""))
			normTarget := strings.ToLower(strings.ReplaceAll(targetStyle, " ", ""))
			if strings.Contains(normName, normTarget) || strings.Contains(normTarget, normName) {
				if f, ok := toFloat(inp.Default); ok {
					return &f
				}
			}
		}
		// Also check if input ID contains the plot ID
		if inp.ID != "" && plotID != "" {
			if strings.Contains(inp.ID, plotID) || strings.Contains(plotID, inp.ID) {
				if f, ok := toFloat(inp.Default); ok {
					return &f
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
