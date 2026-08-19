// Package runner — multi-run engine for input sweep analysis.
// Runs the same Pine script with varied input configurations to discover
// input sensitivity, input→graphics dependencies, and consensus signals.
package runner

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/pipeline"
	"github.com/mrme000m/tvcli/pkg/schema"
)

// RunConfig specifies one execution of a script with particular inputs.
type RunConfig struct {
	Inputs map[string]any `json:"inputs"`
	Label  string         `json:"label"` // "default", "rsi_length=min", etc.
}

// MultiRunResult aggregates results from multiple runs.
type MultiRunResult struct {
	Runs             []SingleRunResult  `json:"runs"`
	InputSensitivity []InputSensitivity `json:"inputSensitivity,omitempty"`
	Consensus        SignalConsensus    `json:"consensus"`
}

// SingleRunResult holds the output of one script execution.
type SingleRunResult struct {
	Config      RunConfig          `json:"config"`
	Signals     *pipeline.Signals   `json:"signals"`
	Fields      map[string]any     `json:"fieldValues"` // current values of all named fields
	GraphicCount map[string]int    `json:"graphicCounts,omitempty"`
}

// InputSensitivity describes how much a single input affects the output.
type InputSensitivity struct {
	InputName   string  `json:"inputName"`
	DeltaField  string  `json:"deltaField"`  // which output field changed most
	Magnitude   float64 `json:"magnitude"`   // how much it changed (0-1 normalized)
	AffectedBy  string  `json:"affectedBy"`  // "value_change", "signal_flip", "graphics_toggle"
}

// SignalConsensus aggregates signal direction across all runs.
type SignalConsensus struct {
	Direction      string   `json:"direction"`       // majority direction
	Confidence     float64  `json:"confidence"`      // 0-1, how consistent
	StableInputs   []string `json:"stableInputs"`    // inputs that don't change result
	VolatileInputs []string `json:"volatileInputs"`  // inputs that flip the signal
}

// GenerateRunConfigs creates a set of run configurations from a schema.
// For each "variation-worthy" input, it generates alternative configs.
func GenerateRunConfigs(sch *schema.ScriptSchema, userOverrides map[string]any) []RunConfig {
	configs := []RunConfig{
		{Label: "default", Inputs: make(map[string]any)},
	}

	if sch == nil {
		return configs
	}

	// Apply user overrides to default config
	for k, v := range userOverrides {
		configs[0].Inputs[k] = v
	}

	for _, inp := range sch.Inputs {
		if inp.IsHidden || inp.IsFake {
			continue
		}

		switch inp.Type {
		case "bool":
			// Test both true and false (unless user already set it)
			if _, set := userOverrides[inp.ID]; set {
				continue
			}
			configs = append(configs,
				RunConfig{
					Label:  inp.ID + "=true",
					Inputs: map[string]any{inp.ID: true},
				},
				RunConfig{
					Label:  inp.ID + "=false",
					Inputs: map[string]any{inp.ID: false},
				},
			)

		case "integer", "float":
			if _, set := userOverrides[inp.ID]; set {
				continue
			}
			min, minOk := toFloat64(inp.Min)
			max, maxOk := toFloat64(inp.Max)
			def, _ := toFloat64(inp.Default)
			if !minOk || !maxOk || min >= max {
				continue
			}
			// Generate min, mid, max configs
			mid := (min + max) / 2
			if mid == def {
				// If mid equals default, use quarter points instead
				mid = min + (max-min)*0.25
			}
			configs = append(configs,
				RunConfig{
					Label:  fmt.Sprintf("%s=min(%.0f)", inp.Name, min),
					Inputs: map[string]any{inp.ID: min},
				},
				RunConfig{
					Label:  fmt.Sprintf("%s=max(%.0f)", inp.Name, max),
					Inputs: map[string]any{inp.ID: max},
				},
			)
			// Add mid point only if it differs from default
			if math.Abs(mid-def) > 0.01 {
				configs = append(configs, RunConfig{
					Label:  fmt.Sprintf("%s=mid(%.0f)", inp.Name, mid),
					Inputs: map[string]any{inp.ID: mid},
				})
			}

		case "string":
			if len(inp.Options) > 0 {
				if _, set := userOverrides[inp.ID]; set {
					continue
				}
				for _, opt := range inp.Options {
					configs = append(configs, RunConfig{
						Label:  fmt.Sprintf("%s=%s", inp.Name, opt),
						Inputs: map[string]any{inp.ID: opt},
					})
				}
			}
		}
	}

	return configs
}

// AnalyzeSensitivity compares multiple run results to determine how inputs affect output.
func AnalyzeSensitivity(runs []SingleRunResult, sch *schema.ScriptSchema) []InputSensitivity {
	if len(runs) < 2 {
		return nil
	}

	var sensitivities []InputSensitivity

	// Find the default run for comparison
	var defaultRun *SingleRunResult
	for i := range runs {
		if runs[i].Config.Label == "default" {
			defaultRun = &runs[i]
			break
		}
	}
	if defaultRun == nil {
		defaultRun = &runs[0]
	}

	// Compare each non-default run against default
	for _, run := range runs {
		if run.Config.Label == "default" {
			continue
		}

		// Find which field changed most
		bestField := ""
		bestDelta := 0.0

		for field, defVal := range defaultRun.Fields {
			runVal, ok := run.Fields[field]
			if !ok {
				continue
			}
			d := math.Abs(toFloat64Safe(defVal) - toFloat64Safe(runVal))
			if d > bestDelta {
				bestDelta = d
				bestField = field
			}
		}

		// Determine the type of effect
		effectType := "value_change"
		if bestDelta > 0 && isSignalField(bestField, sch) {
			effectType = "signal_flip"
		}

		// Check if graphics count changed
		for drawType, count := range run.GraphicCount {
			defCount := defaultRun.GraphicCount[drawType]
			if count != defCount {
				effectType = "graphics_toggle"
				bestField = drawType
				bestDelta = float64(abs(count - defCount))
			}
		}

		sensitivities = append(sensitivities, InputSensitivity{
			InputName:  extractInputName(run.Config),
			DeltaField: bestField,
			Magnitude:  normalizeMagnitude(bestDelta, defaultRun.Fields[bestField]),
			AffectedBy: effectType,
		})
	}

	return sensitivities
}

// ComputeConsensus determines the majority signal direction across runs.
func ComputeConsensus(runs []SingleRunResult) SignalConsensus {
	if len(runs) == 0 {
		return SignalConsensus{Direction: "neutral", Confidence: 0.5}
	}

	buy, sell, neutral := 0, 0, 0
	for _, run := range runs {
		switch run.Signals.Bias {
		case "long":
			buy++
		case "short":
			sell++
		default:
			neutral++
		}
	}

	total := buy + sell + neutral
	if total == 0 {
		return SignalConsensus{Direction: "neutral", Confidence: 0.5}
	}

	conf := math.Max(float64(buy), math.Max(float64(sell), float64(neutral))) / float64(total)

	direction := "neutral"
	if buy > sell && buy > neutral {
		direction = "long"
	} else if sell > buy && sell > neutral {
		direction = "short"
	}

	return SignalConsensus{
		Direction:  direction,
		Confidence: conf,
	}
}

// --- helpers ---

func toFloat64(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case string:
		var f float64
		n, _ := fmt.Sscanf(x, "%f", &f)
		if n == 1 {
			return f, true
		}
	}
	return 0, false
}

func toFloat64Safe(v any) float64 {
	f, _ := toFloat64(v)
	return f
}

func extractInputName(cfg RunConfig) string {
	// Extract the input name from the label (e.g., "RSI Length=min(10)" → "RSI Length")
	label := cfg.Label
	if idx := strings.Index(label, "="); idx > 0 {
		return label[:idx]
	}
	return label
}

func isSignalField(field string, sch *schema.ScriptSchema) bool {
	if sch == nil {
		return false
	}
	for _, p := range sch.Plots {
		if p.Name == field && p.Semantic == "signal" {
			return true
		}
	}
	return false
}

func normalizeMagnitude(delta float64, ref any) float64 {
	refVal := toFloat64Safe(ref)
	if refVal == 0 {
		return delta
	}
	return math.Abs(delta/refVal) / 100.0 // normalized to ~0-1
}

func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}
