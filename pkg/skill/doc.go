// Package skill is the indicator-skill system: a global registry of named
// Pine scripts (each with inputs, presets, and an output parser) plus the
// SkillResult / AgentResult types that give every skill a uniform,
// agent-friendly output envelope.
//
// Sub-package parsers registers 19+ per-script parsers via init() — import
// them for their side effects:
//
//	import (
//	    "github.com/mrme000m/tvcli/pkg/skill"
//	    _ "github.com/mrme000m/tvcli/pkg/skill/parsers" // register all skills
//	)
//
//	sk := skill.Get("xau-scalp")
//	result := sk.ParseOutput(periods, graphic, tf, symbol, nil)
//
// Each registered skill carries its TradingView Pine ID and input defaults,
// so a program can run any skill with just the registry + pkg/tradingview.
package skill