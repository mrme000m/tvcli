// Package skill defines the framework for indicator-specific CLI commands.
//
// Skills wrap Pine Script INDICATORS (not strategies). The two Pine types are
// fundamentally different in this pipeline:
//
//   - indicator — emits plots/graphics for analysis only; it never places
//     orders. An indicator is a generic analysis function, so each skill
//     specialises it with typed Inputs (custom inputs) and Presets (named
//     input templates) and a parser that turns its output into structure.
//
//   - strategy — an executable model that emits signals as orders/trades in a
//     strategy report. Strategies are NOT wrapped as skills; they run through
//     the generic pipeline path, which converts the strategy report into
//     directional buy/sell events (see pkg/pipeline.ScriptType).
package skill

import (
	"fmt"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/pkg/schema"
)

// InputDef describes one configurable input for a Pine indicator.
type InputDef struct {
	Name      string // JS variable name: "atrLenInput"
	TVInputID string // TradingView input ID: "in_0"
	Type      string // "int" | "float" | "bool" | "string"
	Default   any    // default value
}

// FlagName converts the JS variable name to a kebab-case CLI flag.
func (d InputDef) FlagName() string {
	return CamelToKebab(d.Name)
}

// ReservedFlags are CLI flag names consumed by the skill command itself,
// not passed through to the indicator.
var ReservedFlags = map[string]bool{
	"symbol": true, "tf": true, "timeframe": true, "bars": true,
	"json": true, "agent": true, "out": true, "raw": true, "raw-out": true,
	"signals": true, "settle": true, "force-cleanup": true,
	"persistent": true, "loop": true, "verbose": true, "preset": true,
	"help": true, "h": true, "v": true,
}

// SkillResult is the common output from a skill's ParseOutput function.
type SkillResult struct {
	Status        string         `json:"status"`
	Workflow      string         `json:"workflow"`
	Market        MarketData     `json:"market"`
	Structure     map[string]any `json:"structure"`
	Opportunities []Opportunity  `json:"opportunities"`
	Narrative     Narrative      `json:"narrative"`
	Validation    Validation     `json:"validation"`
	Conformance   Conformance    `json:"conformance"`
	Raw           map[string]any `json:"raw,omitempty"`
}

type MarketData struct {
	LastPrice   any    `json:"lastPrice,omitempty"`
	Bias        string `json:"bias,omitempty"`
	LastBarTime any    `json:"lastBarTime,omitempty"` // epoch s of the last bar in the feed (last CLOSED bar)
}

type Opportunity struct {
	Rank              int     `json:"rank"`
	Setup             string  `json:"setup"`
	Direction         string  `json:"direction"`
	Confidence        string  `json:"confidence"`
	ConfluenceScore   float64 `json:"confluenceScore"`
	DistanceFromPrice any     `json:"distanceFromPrice"`
	IsStale           bool    `json:"isStale"`
	Rationale         string  `json:"rationale,omitempty"`
	// Optional structured trade levels (ScalpQuant v2 and similar composite
	// skills). Filled when the underlying Pine script emits dynamic TP/SL
	// plots. Other parsers leave them at zero / omitempty.
	Entry      float64 `json:"entry,omitempty"`
	StopLoss   float64 `json:"stopLoss,omitempty"`
	TP1        float64 `json:"tp1,omitempty"`
	TP2        float64 `json:"tp2,omitempty"`
	TP3        float64 `json:"tp3,omitempty"`
	RiskReward float64 `json:"riskReward,omitempty"`
}

type Narrative struct {
	MarketStructure string   `json:"marketStructure"`
	PrimaryOpp      string   `json:"primaryOpportunity"`
	Warnings        []string `json:"warnings"`
	Watchlist       []string `json:"watchlist,omitempty"`
}

type Validation struct {
	Passed   bool     `json:"passed"`
	Warnings []string `json:"warnings"`
}

type Conformance struct {
	HasValidData bool    `json:"hasValidData"`
	AgenticScore float64 `json:"agenticScore"`
}

// AgentResult is the agent-ready-v2 envelope wrapping a SkillResult.
type AgentResult struct {
	Status        string         `json:"status"`
	ExitCode      int            `json:"exitCode"`
	Timestamp     string         `json:"timestamp"`
	Execution     ExecutionMeta  `json:"execution"`
	AgentContext  AgentContext   `json:"agentContext"`
	Market        MarketData     `json:"market"`
	Structure     map[string]any `json:"structure"`
	Opportunities []Opportunity  `json:"opportunities"`
	Narrative     Narrative      `json:"narrative"`
	Conformance   Conformance    `json:"conformance"`
	SchemaVersion string         `json:"schemaVersion"`
	Extra         map[string]any `json:"-"`
}

type ExecutionMeta struct {
	DurationMs int64 `json:"durationMs"`
	Attempts   int   `json:"attempts"`
}

type AgentContext struct {
	Workflow     string `json:"workflow"`
	ModelVersion string `json:"modelVersion"`
	Symbol       string `json:"symbol"`
	Timeframe    string `json:"timeframe"`
	HTFTimeframe string `json:"htfTimeframe,omitempty"`
}

// Skill defines a complete indicator CLI command.
//
// A Skill is always an INDICATOR wrapper, never a strategy: indicators emit
// analysis output (plots/graphics) only, so they are specialised at run time
// through custom inputs and named input templates (see Inputs and Presets).
// Strategies, which emit signals via a strategy report, are handled by the
// generic pipeline path instead (see pkg/pipeline.ScriptType).
type Skill struct {
	Name     string // CLI command name: "smc"
	Synopsis string // Short description for help text
	PineID   string // TradingView Pine Script ID
	Inputs   []InputDef
	Presets  map[string]map[string]any // named input templates: "scalping" -> {inputName: value}

	// Tier is the minimum TradingView subscription tier needed for this script
	// to return data (e.g. "essential", "plus"). Empty means it works on free.
	// Informational: not yet used to hard-gate execution.
	Tier string
	// Category groups skills for listing/discovery. Inferred from Name when
	// empty (see EffectiveCategory).
	Category string
	// RequiresGraphic is true when the script emits only graphic drawings and
	// no period/plot data; the parser must read the graphic layer.
	RequiresGraphic bool
	// KnownBroken documents a known issue (wrong PineID, no period data on some
	// symbols, paid-tier requirement). Skills are still registered but flagged
	// so agents can avoid or handle them explicitly.
	KnownBroken string
	// Source is the raw Pine Script source code for private (USER;) scripts
	// where the Pine Facade returns incomplete metaInfo. When set, the skill
	// command bypasses Pine Facade's LoadIndicator and uses this source
	// directly (like the eval command does).
	Source string

	// ParseOutput processes raw indicator data into a SkillResult.
	ParseOutput func(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) SkillResult

	// ParseWithSchema is the schema-aware alternative to ParseOutput. When set,
	// the command layer prefers it and passes the script's PineSchema so the
	// parser can resolve plot names from metaInfo instead of guessing
	// plot_N indices. Falls back to ParseOutput when nil.
	ParseWithSchema func(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) SkillResult

	// FormatText renders SkillResult as human-readable text.
	FormatText func(result SkillResult) string
}

// EffectiveCategory returns the explicit Category, or one inferred from the
// skill name when Category is empty. Used for grouping in `skills` listings.
func (s *Skill) EffectiveCategory() string {
	if s.Category != "" {
		return s.Category
	}
	n := strings.ToLower(s.Name)
	switch {
	case strings.Contains(n, "trend"), strings.Contains(n, "mtf"):
		return "trend"
	case strings.Contains(n, "smc"), strings.Contains(n, "ict"),
		strings.Contains(n, "liq"), strings.Contains(n, "order"),
		strings.Contains(n, "swing"):
		return "smc"
	case strings.Contains(n, "vp"), strings.Contains(n, "vgaps"),
		strings.Contains(n, "anchored"), strings.Contains(n, "bsv"),
		strings.Contains(n, "dvi"):
		return "volume"
	case strings.Contains(n, "sr"), strings.Contains(n, "support"):
		return "levels"
	default:
		return "other"
	}
}

// ToAgent converts a SkillResult into the agent-ready-v2 envelope.
func (s *Skill) ToAgent(result SkillResult, symbol, tf string, durationMs int64) AgentResult {
	return AgentResult{
		Status:    result.Status,
		ExitCode:  0,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Execution: ExecutionMeta{
			DurationMs: durationMs,
			Attempts:   1,
		},
		AgentContext: AgentContext{
			Workflow:     result.Workflow,
			ModelVersion: "agent-ready-v2",
			Symbol:       symbol,
			Timeframe:    tf,
		},
		Market:        result.Market,
		Structure:     result.Structure,
		Opportunities: result.Opportunities,
		Narrative:     result.Narrative,
		Conformance:   result.Conformance,
		SchemaVersion: "agent-ready-v2.0.0",
	}
}

// CamelToKebab converts camelCase to kebab-case.
func CamelToKebab(s string) string {
	var out []rune
	for i, r := range s {
		if r >= 'A' && r <= 'Z' {
			if i > 0 {
				out = append(out, '-')
			}
			out = append(out, r+32)
		} else {
			out = append(out, r)
		}
	}
	return string(out)
}

// CoerceValue converts a string value to the target type.
func CoerceValue(val string, typ string) (any, error) {
	switch typ {
	case "bool":
		lower := strings.ToLower(val)
		return lower == "true" || lower == "1" || lower == "yes", nil
	case "int":
		n := 0
		_, err := fmt.Sscanf(val, "%d", &n)
		if err != nil {
			return nil, fmt.Errorf("invalid int: %s", val)
		}
		return n, nil
	case "float":
		f := 0.0
		_, err := fmt.Sscanf(val, "%f", &f)
		if err != nil {
			return nil, fmt.Errorf("invalid float: %s", val)
		}
		return f, nil
	case "string":
		return val, nil
	default:
		return val, nil
	}
}
