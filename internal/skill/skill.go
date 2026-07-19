// Package skill defines the framework for indicator-specific CLI commands.
// Each skill wraps a Pine Script indicator with typed inputs, presets,
// and structured output parsing.
package skill

import (
	"fmt"
	"strings"
	"time"
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
	LastPrice any    `json:"lastPrice,omitempty"`
	Bias      string `json:"bias,omitempty"`
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
	Status        string            `json:"status"`
	ExitCode      int               `json:"exitCode"`
	Timestamp     string            `json:"timestamp"`
	Execution     ExecutionMeta     `json:"execution"`
	AgentContext  AgentContext      `json:"agentContext"`
	Market        MarketData        `json:"market"`
	Structure     map[string]any    `json:"structure"`
	Opportunities []Opportunity     `json:"opportunities"`
	Narrative     Narrative         `json:"narrative"`
	Conformance   Conformance       `json:"conformance"`
	SchemaVersion string            `json:"schemaVersion"`
	Extra         map[string]any    `json:"-"`
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
type Skill struct {
	Name     string // CLI command name: "smc"
	Synopsis string // Short description for help text
	PineID   string // TradingView Pine Script ID
	Inputs   []InputDef
	Presets  map[string]map[string]any // "scalping" -> {inputs}

	// ParseOutput processes raw indicator data into a SkillResult.
	ParseOutput func(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) SkillResult

	// FormatText renders SkillResult as human-readable text.
	FormatText func(result SkillResult) string
}

// ToAgent converts a SkillResult into the agent-ready-v2 envelope.
func (s *Skill) ToAgent(result SkillResult, symbol, tf string, durationMs int64) AgentResult {
	return AgentResult{
		Status:   result.Status,
		ExitCode: 0,
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
