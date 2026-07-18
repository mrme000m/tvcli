package tradingview

import "fmt"

type InputDef struct {
	Name       string   `json:"name"`
	Inline     string   `json:"inline"`
	InternalID string   `json:"internalID"`
	Tooltip    string   `json:"tooltip"`
	Type       string   `json:"type"`
	Value      any      `json:"value"`
	IsHidden   bool     `json:"isHidden"`
	IsFake     bool     `json:"isFake"`
	Options    []string `json:"options,omitempty"`
}

type PineIndicator struct {
	PineID          string
	PineVersion     string
	Description     string
	ShortDesc       string
	Inputs          map[string]*InputDef
	InputsOrder     []string // Preserves insertion order for deterministic color indexing
	Plots           map[string]string
	Script          string
	Type            string
	metaInfo        map[string]any
}

func NewPineIndicator(opts map[string]any) *PineIndicator {
	ind := &PineIndicator{
		Type:    "Script@tv-scripting-101!",
		Inputs:  make(map[string]*InputDef),
		Plots:   make(map[string]string),
	}

	if v, ok := opts["pineId"].(string); ok {
		ind.PineID = v
	}
	if v, ok := opts["pineVersion"].(string); ok {
		ind.PineVersion = v
	}
	if v, ok := opts["description"].(string); ok {
		ind.Description = v
	}
	if v, ok := opts["shortDescription"].(string); ok {
		ind.ShortDesc = v
	}
	if v, ok := opts["script"].(string); ok {
		ind.Script = v
	}
	if v, ok := opts["metaInfo"].(map[string]any); ok {
		ind.metaInfo = v
	}

	// Parse inputs from metaInfo if present
	if mi, ok := opts["metaInfo"].(map[string]any); ok {
		if inputsArr, ok := mi["inputs"].([]any); ok {
			for _, inpRaw := range inputsArr {
				inp, ok := inpRaw.(map[string]any)
				if !ok {
					continue
				}
				id, _ := inp["id"].(string)
				if id == "" || id == "text" || id == "pineId" || id == "pineVersion" || id == "__profile" {
					continue
				}
			def := &InputDef{
				Name: id,
				Type: "float",
				Value: inp["defval"],
			}
			if n, ok := inp["name"].(string); ok {
				def.Name = n
			}
			if t, ok := inp["type"].(string); ok {
				def.Type = t
			}
			if inline, ok := inp["inline"].(string); ok {
				def.InternalID = inline
			}
			if tooltip, ok := inp["tooltip"].(string); ok {
				def.Tooltip = tooltip
			}
			if isFake, ok := inp["isFake"].(bool); ok {
				def.IsFake = isFake
			}
			if isHidden, ok := inp["isHidden"].(bool); ok {
				def.IsHidden = isHidden
			}
				if opts, ok := inp["options"].([]any); ok {
					for _, o := range opts {
						if s, ok := o.(string); ok {
							def.Options = append(def.Options, s)
						}
					}
				}
				ind.Inputs[id] = def
				ind.InputsOrder = append(ind.InputsOrder, id)
			}
		}
	}

	return ind
}

func (ind *PineIndicator) SetOption(key string, value any) error {
	// Try direct key match
	if def, ok := ind.Inputs[key]; ok {
		def.Value = value
		return nil
	}
	// Try "in_" prefix
	if def, ok := ind.Inputs["in_"+key]; ok {
		def.Value = value
		return nil
	}
	// Try matching by inline/internalID
	for _, def := range ind.Inputs {
		if def.InternalID == key {
			def.Value = value
			return nil
		}
	}
	return fmt.Errorf("input '%s' not found", key)
}

func (ind *PineIndicator) GetInputs() map[string]any {
	result := make(map[string]any)

	// Build input index from deterministic order (matches JS Object.keys insertion order)
	inputIndex := make(map[string]int)
	for i, k := range ind.InputsOrder {
		inputIndex[k] = i
	}

	for k, v := range ind.Inputs {
		value := v.Value
		// Color inputs use their index as the value (matches JS behavior)
		if v.Type == "color" {
			value = inputIndex[k]
		}
		result[k] = map[string]any{
			"v": value,
			"f": v.IsFake,
			"t": v.Type,
		}
	}
	// Remove pineFeatures from output (JS skips it in _getInputs but keeps in inputIndex)
	delete(result, "pineFeatures")
	result["text"] = ind.Script
	if ind.PineID != "" {
		result["pineId"] = ind.PineID
	}
	if ind.PineVersion != "" {
		result["pineVersion"] = ind.PineVersion
	}
	return result
}

type BuiltinIndicator struct {
	Type    string
	Options map[string]any
}

var builtinDefaults = map[string]map[string]any{
	"Volume@tv-basicstudies-241": {
		"length":         20,
		"col_prev_close": false,
	},
	"VbPFixed@tv-basicstudies-241": {
		"rowsLayout":            "Number Of Rows",
		"rows":                  24,
		"volume":                "Up/Down",
		"vaVolume":              70,
		"subscribeRealtime":     false,
		"extendToRight":         false,
		"mapRightBoundaryToBarStartTime": true,
	},
}

func NewBuiltinIndicator(indicatorType string) *BuiltinIndicator {
	opts := make(map[string]any)
	if defaults, ok := builtinDefaults[indicatorType]; ok {
		for k, v := range defaults {
			opts[k] = v
		}
	}
	return &BuiltinIndicator{Type: indicatorType, Options: opts}
}

func (b *BuiltinIndicator) SetOption(key string, value any, force bool) {
	b.Options[key] = value
}
