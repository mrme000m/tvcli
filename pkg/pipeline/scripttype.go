package pipeline

// ScriptType is the top-level kind of a Pine Script. There are exactly two
// major types, and they behave differently in this pipeline:
//
//   - ScriptTypeIndicator — a Pine `indicator(...)` (or legacy `study(...)`).
//     It emits plots and/or drawings for ANALYSIS ONLY. It never places
//     orders and produces no strategy report. Because an indicator is a
//     generic analysis function, it must be specialised at run time through
//     custom inputs (skill.InputDef) or named input templates
//     (skill.Presets) — e.g. the same profile script can run as a "scalping"
//     or a "swing" preset.
//
//   - ScriptTypeStrategy — a Pine `strategy(...)`. A strategy is an
//     executable trading model: it EMITS SIGNALS as orders/trades that
//     arrive in the strategy report (performance + trades). The pipeline
//     converts those trades into directional buy/sell events for the agent.
//     Strategies are configured by their own strategy properties
//     (commission, slippage, pyramiding, ...), not by skill inputs.
type ScriptType string

const (
	// ScriptTypeIndicator marks a Pine indicator: analysis output only, no
	// orders/signals, specialised via custom inputs or input templates.
	ScriptTypeIndicator ScriptType = "indicator"

	// ScriptTypeStrategy marks a Pine strategy: emits signals as a strategy
	// report whose trades become buy/sell events.
	ScriptTypeStrategy ScriptType = "strategy"
)

// IsStrategy reports whether the script is a strategy (emits signals) rather
// than an indicator (analysis only).
func (t ScriptType) IsStrategy() bool { return t == ScriptTypeStrategy }

// IsIndicator reports whether the script is an indicator (analysis only)
// rather than a strategy (emits signals).
func (t ScriptType) IsIndicator() bool { return t == ScriptTypeIndicator }

// String implements fmt.Stringer.
func (t ScriptType) String() string { return string(t) }
