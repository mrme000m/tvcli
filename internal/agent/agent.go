// Package agent provides orchestration for running multiple TradingView skills
// and aggregating their results into comprehensive market analysis.
package agent

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

// AgentConfig configures an agent run.
type AgentConfig struct {
	Symbol          string            // Market symbol (e.g., "OANDA:XAUUSD")
	Timeframe       string            // Timeframe (e.g., "5m")
	Bars            int               // Number of bars
	Skills          []string          // Skill names to run (empty = all)
	Presets         map[string]string // Skill name -> preset name
	Inputs          map[string]string // Global inputs applied to all skills
	Parallel        bool              // Run skills in parallel
	Timeout         time.Duration     // Per-skill timeout
	Debug           bool
	ValidateInputs  bool              // Validate inputs against skill schemas before running
	ListInputsOnly  bool              // Only list available inputs, don't run skills
}

// SkillResult holds the result of running one skill.
type SkillResult struct {
	SkillName string
	Status    string            // "ok", "error", "no_data", "skipped"
	Error     string            // Error message if Status == "error"
	Duration  time.Duration     // Execution time
	Result    *skill.SkillResult // Parsed skill result
	AgentResult *skill.AgentResult // Agent-ready envelope
}

// AgentResult holds the aggregated results of an agent run.
type AgentResult struct {
	Config      AgentConfig
	Timestamp   time.Time
	Duration    time.Duration
	SkillResults []SkillResult
	Summary     AgentSummary
}

// AgentSummary provides a high-level summary across all skills.
type AgentSummary struct {
	TotalSkills    int
	Successful     int
	Failed         int
	Skipped        int
	BiasConsensus  string // "bullish", "bearish", "neutral", "mixed"
	TopOpportunities []skill.Opportunity
	AllWarnings    []string
	AvgAgenticScore float64
}

// DefaultAgentConfig returns a sensible default configuration.
func DefaultAgentConfig() AgentConfig {
	return AgentConfig{
		Symbol:    "OANDA:XAUUSD",
		Timeframe: "5m",
		Bars:      500,
		Parallel:  true,
		Timeout:   120 * time.Second,
	}
}

// Agent orchestrates multiple skill runs.
type Agent struct {
	cfg    *config.Config
	config AgentConfig
}

// NewAgent creates a new agent with the given config.
func NewAgent(cfg *config.Config, config AgentConfig) *Agent {
	if config.Symbol == "" {
		config.Symbol = "OANDA:XAUUSD"
	}
	if config.Timeframe == "" {
		config.Timeframe = "5m"
	}
	if config.Bars == 0 {
		config.Bars = 500
	}
	if config.Timeout == 0 {
		config.Timeout = 120 * time.Second
	}
	return &Agent{cfg: cfg, config: config}
}

// Run executes all configured skills and returns aggregated results.
func (a *Agent) Run(ctx context.Context) (*AgentResult, error) {
	start := time.Now()

	// Determine which skills to run
	skillsToRun := a.config.Skills
	if len(skillsToRun) == 0 {
		// Run all registered skills
		for _, s := range skill.All() {
			skillsToRun = append(skillsToRun, s.Name)
		}
	}

	// Normalize symbol (needed for validation)
	symbol, err := pinefacade.ValidateSymbol(a.config.Symbol)
	if err != nil {
		return nil, fmt.Errorf("invalid symbol: %w", err)
	}

	// Handle ListInputsOnly mode - just return skill inputs without running
	if a.config.ListInputsOnly {
		return a.runListInputs(symbol, skillsToRun, start)
	}

	// Prepare results slice
	results := make([]SkillResult, len(skillsToRun))

	if a.config.Parallel {
		a.runParallel(ctx, symbol, skillsToRun, results)
	} else {
		a.runSequential(ctx, symbol, skillsToRun, results)
	}

	// Build summary
	summary := a.buildSummary(results)

	return &AgentResult{
		Config:       a.config,
		Timestamp:    start,
		Duration:     time.Since(start),
		SkillResults: results,
		Summary:      summary,
	}, nil
}

// runListInputs returns skill input definitions without running the skills.
func (a *Agent) runListInputs(symbol string, skillsToRun []string, start time.Time) (*AgentResult, error) {
	results := make([]SkillResult, len(skillsToRun))

	for i, skillName := range skillsToRun {
		s := skill.Get(skillName)
		if s == nil {
			results[i] = SkillResult{
				SkillName: skillName,
				Status:    "error",
				Error:     "skill not found",
				Duration:  0,
			}
			continue
		}

		// Build inputs to show defaults
		inputs := a.buildInputs(s)

		results[i] = SkillResult{
			SkillName: skillName,
			Status:    "ok",
			Duration:  0,
			Result:    &skill.SkillResult{Status: "ok"},
		}
		// We'll use the inputs built above for display
		_ = inputs
	}

	summary := a.buildSummary(results)

	return &AgentResult{
		Config:       a.config,
		Timestamp:    start,
		Duration:     time.Since(start),
		SkillResults: results,
		Summary:      summary,
	}, nil
}

// runParallel runs skills concurrently.
func (a *Agent) runParallel(ctx context.Context, symbol string, skillsToRun []string, results []SkillResult) {
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4) // Limit concurrency to 4

	for i, skillName := range skillsToRun {
		wg.Add(1)
		sem <- struct{}{}
		go func(idx int, name string) {
			defer wg.Done()
			defer func() { <-sem }()
			results[idx] = a.runSkill(ctx, symbol, name)
		}(i, skillName)
	}
	wg.Wait()
}

// runSequential runs skills one at a time.
func (a *Agent) runSequential(ctx context.Context, symbol string, skillsToRun []string, results []SkillResult) {
	for i, skillName := range skillsToRun {
		results[i] = a.runSkill(ctx, symbol, skillName)
	}
}

// runSkill executes a single skill and returns its result.
func (a *Agent) runSkill(ctx context.Context, symbol, skillName string) SkillResult {
	start := time.Now()
	s := skill.Get(skillName)
	if s == nil {
		return SkillResult{
			SkillName: skillName,
			Status:    "error",
			Error:     "skill not found",
			Duration:  time.Since(start),
		}
	}

	// Check if skill is known broken
	if s.KnownBroken != "" {
		return SkillResult{
			SkillName: skillName,
			Status:    "skipped",
			Error:     s.KnownBroken,
			Duration:  time.Since(start),
		}
	}

	// Build inputs: defaults + preset + global overrides
	inputs := a.buildInputs(s)

	// Run the skill
	res, err := service.RunScript(ctx, a.cfg, service.RunRequest{
		PineID:       s.PineID,
		Symbol:       symbol,
		Timeframe:    a.config.Timeframe,
		Bars:         a.config.Bars,
		Inputs:       inputs,
		ReservedKeys: reservedSkillKeys,
		SettleMs:     1500,
		ForceCleanup: false,
		CalcTimeout:  a.config.Timeout,
		Debug:        a.config.Debug,
	})
	duration := time.Since(start)

	if err != nil {
		return SkillResult{
			SkillName: skillName,
			Status:    "error",
			Error:     err.Error(),
			Duration:  duration,
		}
	}

	// Parse output
	var result skill.SkillResult
	if s.ParseWithSchema != nil {
		result = s.ParseWithSchema(res.Periods, res.Graphic, res.Indicator.Schema, a.config.Timeframe, symbol, a.config.Inputs)
	} else {
		result = s.ParseOutput(res.Periods, res.Graphic, a.config.Timeframe, symbol, a.config.Inputs)
	}
	if result.Status == "" {
		result.Status = "ok"
	}
	if result.Workflow == "" {
		result.Workflow = skillName
	}

	// Convert to agent-ready format
	agentResult := s.ToAgent(result, symbol, a.config.Timeframe, duration.Milliseconds())

	return SkillResult{
		SkillName:   skillName,
		Status:      result.Status,
		Duration:    duration,
		Result:      &result,
		AgentResult: &agentResult,
	}
}

// buildInputs merges defaults, preset, and global overrides for a skill.
func (a *Agent) buildInputs(s *skill.Skill) map[string]string {
	inputs := make(map[string]string)

	// 1. Defaults
	for _, inp := range s.Inputs {
		if inp.Default != nil {
			inputs[inp.TVInputID] = fmt.Sprintf("%v", inp.Default)
		}
	}

	// 2. Preset
	if presetName, ok := a.config.Presets[s.Name]; ok {
		if preset, ok := s.Presets[presetName]; ok {
			nameToTV := make(map[string]string)
			for _, inp := range s.Inputs {
				nameToTV[inp.Name] = inp.TVInputID
			}
			for k, v := range preset {
				tvID := nameToTV[k]
				if tvID == "" {
					tvID = k
				}
				inputs[tvID] = fmt.Sprintf("%v", v)
			}
		}
	}

	// 3. Global overrides
	for k, v := range a.config.Inputs {
		inputs[k] = v
	}

	// 4. Validate against schema if requested
	if a.config.ValidateInputs {
		inputs = a.validateAndConvertInputs(inputs, s)
	}

	return inputs
}

// validateAndConvertInputs validates user inputs against skill's input definitions.
func (a *Agent) validateAndConvertInputs(inputs map[string]string, s *skill.Skill) map[string]string {
	if len(s.Inputs) == 0 {
		return inputs // No schema, pass through as-is
	}

	// Build lookup: TVInputID, Name -> InputDef
	byID := make(map[string]*skill.InputDef)
	for i := range s.Inputs {
		inp := &s.Inputs[i]
		byID[inp.TVInputID] = inp
		if inp.Name != "" && inp.Name != inp.TVInputID {
			byID[inp.Name] = inp
		}
	}

	validated := make(map[string]string)
	for key, val := range inputs {
		inpDef, ok := byID[key]
		if !ok {
			// Unknown input, pass through (could be a valid input not in our static list)
			validated[key] = val
			continue
		}

		// Type conversion and validation
		converted, err := convertInputValue(val, inpDef)
		if err != nil {
			fmt.Fprintf(os.Stderr, "⚠ Input '%s' for skill %s: %v\n", key, s.Name, err)
			continue
		}

		// Use the canonical TV input ID
		validated[inpDef.TVInputID] = converted
	}

	return validated
}

// buildSummary creates a high-level summary from all skill results.
func (a *Agent) buildSummary(results []SkillResult) AgentSummary {
	summary := AgentSummary{
		TotalSkills: len(results),
		AllWarnings: []string{},
		TopOpportunities: []skill.Opportunity{},
	}

	var totalScore float64
	var scoredSkills int
	bullishCount, bearishCount, neutralCount := 0, 0, 0

	// Collect all opportunities
	allOpps := []skill.Opportunity{}
	for _, r := range results {
		switch r.Status {
		case "ok":
			summary.Successful++
			if r.AgentResult != nil {
				totalScore += r.AgentResult.Conformance.AgenticScore
				scoredSkills++

				// Track bias
				switch r.AgentResult.Market.Bias {
				case "bullish":
					bullishCount++
				case "bearish":
					bearishCount++
				default:
					neutralCount++
				}

				// Collect opportunities
				allOpps = append(allOpps, r.AgentResult.Opportunities...)

				// Collect warnings
				summary.AllWarnings = append(summary.AllWarnings, r.AgentResult.Narrative.Warnings...)
			}
		case "error":
			summary.Failed++
		case "skipped":
			summary.Skipped++
		}
	}

	// Determine consensus bias
	if bullishCount > bearishCount && bullishCount > neutralCount {
		summary.BiasConsensus = "bullish"
	} else if bearishCount > bullishCount && bearishCount > neutralCount {
		summary.BiasConsensus = "bearish"
	} else if bullishCount == bearishCount && bullishCount > 0 {
		summary.BiasConsensus = "mixed"
	} else {
		summary.BiasConsensus = "neutral"
	}

	// Average agentic score
	if scoredSkills > 0 {
		summary.AvgAgenticScore = totalScore / float64(scoredSkills)
	}

	// Sort opportunities by confluence score and take top 5
	// (simple bubble sort for small slice)
	for i := 0; i < len(allOpps)-1; i++ {
		for j := i + 1; j < len(allOpps); j++ {
			if allOpps[i].ConfluenceScore < allOpps[j].ConfluenceScore {
				allOpps[i], allOpps[j] = allOpps[j], allOpps[i]
			}
		}
	}
	if len(allOpps) > 5 {
		allOpps = allOpps[:5]
	}
	summary.TopOpportunities = allOpps

	return summary
}

// reservedSkillKeys mirrors the list in skillcmd.go
var reservedSkillKeys = []string{
	"symbol", "tf", "timeframe", "bars", "json", "agent", "out",
	"raw", "raw-out", "signals", "settle", "force-cleanup", "persistent",
	"loop", "verbose", "preset", "help", "h", "v",
	"allow-private", "verify-access",
}

// FormatText renders the agent result as human-readable text.
func FormatText(result *AgentResult) string {
	var sb strings.Builder
	sb.WriteString("\n")
	sb.WriteString(strings.Repeat("=", 70) + "\n")
	sb.WriteString("  MULTI-SKILL MARKET ANALYSIS\n")
	sb.WriteString(strings.Repeat("=", 70) + "\n\n")

	sb.WriteString(fmt.Sprintf("  Symbol:     %s\n", result.Config.Symbol))
	sb.WriteString(fmt.Sprintf("  Timeframe:  %s\n", result.Config.Timeframe))
	sb.WriteString(fmt.Sprintf("  Bars:       %d\n", result.Config.Bars))
	sb.WriteString(fmt.Sprintf("  Duration:   %v\n", result.Duration.Round(time.Millisecond)))
	sb.WriteString(fmt.Sprintf("  Timestamp:  %s\n", result.Timestamp.Format(time.RFC3339)))
	sb.WriteString("\n")

	// Summary
	sb.WriteString("  ─── SUMMARY ───\n")
	sb.WriteString(fmt.Sprintf("  Total Skills:    %d\n", result.Summary.TotalSkills))
	sb.WriteString(fmt.Sprintf("  Successful:      %d\n", result.Summary.Successful))
	sb.WriteString(fmt.Sprintf("  Failed:          %d\n", result.Summary.Failed))
	sb.WriteString(fmt.Sprintf("  Skipped:         %d\n", result.Summary.Skipped))
	sb.WriteString(fmt.Sprintf("  Bias Consensus:  %s\n", strings.ToUpper(result.Summary.BiasConsensus)))
	sb.WriteString(fmt.Sprintf("  Avg Agentic Score: %.2f\n", result.Summary.AvgAgenticScore))
	sb.WriteString("\n")

	// Top Opportunities
	if len(result.Summary.TopOpportunities) > 0 {
		sb.WriteString("  ─── TOP OPPORTUNITIES ───\n")
		for i, opp := range result.Summary.TopOpportunities {
			sb.WriteString(fmt.Sprintf("  #%d %s %s [%s] score=%.2f\n",
				i+1, strings.ToUpper(opp.Direction), opp.Setup, opp.Confidence, opp.ConfluenceScore))
			if opp.Rationale != "" {
				sb.WriteString(fmt.Sprintf("      %s\n", opp.Rationale))
			}
			if opp.Entry > 0 {
				sb.WriteString(fmt.Sprintf("      Entry: %.2f  SL: %.2f", opp.Entry, opp.StopLoss))
				if opp.TP1 > 0 {
					sb.WriteString(fmt.Sprintf("  TP1: %.2f  TP2: %.2f  TP3: %.2f", opp.TP1, opp.TP2, opp.TP3))
				}
				if opp.RiskReward > 0 {
					sb.WriteString(fmt.Sprintf("  R:R=%.1f", opp.RiskReward))
				}
				sb.WriteString("\n")
			}
		}
		sb.WriteString("\n")
	}

	// Per-skill details
	sb.WriteString("  ─── PER-SKILL RESULTS ───\n")
	for _, r := range result.SkillResults {
		status := strings.ToUpper(r.Status)
		if r.Status == "ok" {
			status = "✓ OK"
		} else if r.Status == "error" {
			status = "✗ ERROR"
		} else if r.Status == "skipped" {
			status = "⊘ SKIPPED"
		}
		sb.WriteString(fmt.Sprintf("  %s  %s (%v)\n", status, r.SkillName, r.Duration.Round(time.Millisecond)))
		if r.Status == "error" {
			sb.WriteString(fmt.Sprintf("      Error: %s\n", r.Error))
		} else if r.Status == "skipped" {
			sb.WriteString(fmt.Sprintf("      Reason: %s\n", r.Error))
		} else if r.AgentResult != nil {
			if r.AgentResult.Market.Bias != "" {
				sb.WriteString(fmt.Sprintf("      Bias: %s\n", r.AgentResult.Market.Bias))
			}
			if len(r.AgentResult.Opportunities) > 0 {
				for _, opp := range r.AgentResult.Opportunities {
					sb.WriteString(fmt.Sprintf("      → %s %s [%s] score=%.2f\n",
						strings.ToUpper(opp.Direction), opp.Setup, opp.Confidence, opp.ConfluenceScore))
				}
			}
		}
	}
	sb.WriteString("\n")

	// Warnings
	if len(result.Summary.AllWarnings) > 0 {
		sb.WriteString("  ─── WARNINGS ───\n")
		seen := make(map[string]bool)
		for _, w := range result.Summary.AllWarnings {
			if !seen[w] {
				sb.WriteString(fmt.Sprintf("  ⚠ %s\n", w))
				seen[w] = true
			}
		}
		sb.WriteString("\n")
	}

	sb.WriteString(strings.Repeat("=", 70) + "\n")
	return sb.String()
}

// convertInputValue converts and validates a single input value.
func convertInputValue(val string, inp *skill.InputDef) (string, error) {
	switch inp.Type {
	case "integer", "int":
		if _, err := strconv.Atoi(val); err != nil {
			return "", fmt.Errorf("expected integer, got %q", val)
		}
		return val, nil
	case "float":
		if _, err := strconv.ParseFloat(val, 64); err != nil {
			return "", fmt.Errorf("expected float, got %q", val)
		}
		return val, nil
	case "bool":
		lower := strings.ToLower(val)
		if lower == "true" || lower == "1" || lower == "yes" || lower == "on" {
			return "true", nil
		}
		if lower == "false" || lower == "0" || lower == "no" || lower == "off" {
			return "false", nil
		}
		return "", fmt.Errorf("expected boolean, got %q", val)
	case "string":
		// skill.InputDef doesn't have Options field, just validate as string
		return val, nil
	case "color":
		return val, nil
	default:
		return val, nil
	}
}

// ToJSON serializes the agent result to JSON-compatible map.
func (r *AgentResult) ToJSON() map[string]any {
	skills := make([]map[string]any, len(r.SkillResults))
	for i, sr := range r.SkillResults {
		m := map[string]any{
			"skill":    sr.SkillName,
			"status":   sr.Status,
			"duration": sr.Duration.Milliseconds(),
		}
		if sr.Error != "" {
			m["error"] = sr.Error
		}
		if sr.AgentResult != nil {
			m["result"] = sr.AgentResult
		}
		skills[i] = m
	}

	return map[string]any{
		"config": map[string]any{
			"symbol":     r.Config.Symbol,
			"timeframe":  r.Config.Timeframe,
			"bars":       r.Config.Bars,
			"skills":     r.Config.Skills,
			"presets":    r.Config.Presets,
			"parallel":   r.Config.Parallel,
		},
		"timestamp":    r.Timestamp.Format(time.RFC3339),
		"duration_ms":  r.Duration.Milliseconds(),
		"summary": map[string]any{
			"total_skills":     r.Summary.TotalSkills,
			"successful":       r.Summary.Successful,
			"failed":           r.Summary.Failed,
			"skipped":          r.Summary.Skipped,
			"bias_consensus":   r.Summary.BiasConsensus,
			"avg_agentic_score": r.Summary.AvgAgenticScore,
			"top_opportunities": r.Summary.TopOpportunities,
			"warnings":         r.Summary.AllWarnings,
		},
		"skills": skills,
	}
}