// +build ignore

// Example program demonstrating programmatic usage of the tvcli agent system.
// Run with: go run examples/agent_example.go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/mrme000m/tvcli/internal/agent"
	"github.com/mrme000m/tvcli/internal/config"
)

func main() {
	// Load configuration (reads from .env)
	cfg := config.Load()

	// Check auth
	if !cfg.HasAuth() {
		fmt.Println("⚠ No authentication configured. Set SESSION, SIGNATURE, TV_USER in .env")
		fmt.Println("   Some skills may not work without authentication.")
	}

	// Configure the agent
	agentConfig := agent.AgentConfig{
		Symbol:    "OANDA:XAUUSD",
		Timeframe: "15m",
		Bars:      300,
		Skills:    []string{"bsv", "dvi", "ema-atr", "trend"},
		Presets: map[string]string{
			"bsv":    "default",
			"dvi":    "default",
			"ema-atr": "default",
			"trend":  "default",
		},
		Parallel: true,
		Timeout:  120 * time.Second,
		Debug:    false,
	}

	fmt.Printf("🚀 Starting agent analysis for %s %s...\n", agentConfig.Symbol, agentConfig.Timeframe)
	fmt.Printf("   Skills: %v\n", agentConfig.Skills)
	fmt.Println()

	// Create and run agent
	agt := agent.NewAgent(cfg, agentConfig)

	ctx := context.Background()
	result, err := agt.Run(ctx)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Agent run failed: %v\n", err)
		os.Exit(1)
	}

	// Print summary
	fmt.Println("📊 ANALYSIS COMPLETE")
	fmt.Println("====================")
	fmt.Printf("Duration:     %v\n", result.Duration.Round(time.Millisecond))
	fmt.Printf("Skills Run:   %d\n", result.Summary.TotalSkills)
	fmt.Printf("Successful:   %d\n", result.Summary.Successful)
	fmt.Printf("Failed:       %d\n", result.Summary.Failed)
	fmt.Printf("Bias Consensus: %s\n", result.Summary.BiasConsensus)
	fmt.Printf("Avg Score:    %.2f\n", result.Summary.AvgAgenticScore)
	fmt.Println()

	// Print top opportunities
	if len(result.Summary.TopOpportunities) > 0 {
		fmt.Println("🎯 TOP OPPORTUNITIES")
		fmt.Println("--------------------")
		for i, opp := range result.Summary.TopOpportunities {
			fmt.Printf("%d. %s %s [%.0f%%] R:R=%.1f\n",
				i+1, opp.Direction, opp.Setup, opp.ConfluenceScore*100, opp.RiskReward)
			if opp.Entry > 0 {
				fmt.Printf("   Entry: %.2f  SL: %.2f  TP1: %.2f  TP2: %.2f  TP3: %.2f\n",
					opp.Entry, opp.StopLoss, opp.TP1, opp.TP2, opp.TP3)
			}
			fmt.Printf("   %s\n", opp.Rationale)
			fmt.Println()
		}
	}

	// Print per-skill results
	fmt.Println("📈 PER-SKILL RESULTS")
	fmt.Println("--------------------")
	for _, sr := range result.SkillResults {
		status := "✓"
		if sr.Status == "error" {
			status = "✗"
		} else if sr.Status == "skipped" {
			status = "⊘"
		}
		fmt.Printf("%s %s (%v)\n", status, sr.SkillName, sr.Duration.Round(time.Millisecond))
		if sr.AgentResult != nil {
			if sr.AgentResult.Market.Bias != "" {
				fmt.Printf("   Bias: %s\n", sr.AgentResult.Market.Bias)
			}
			if sr.AgentResult.Conformance.AgenticScore > 0 {
				fmt.Printf("   Score: %.2f\n", sr.AgentResult.Conformance.AgenticScore)
			}
			if len(sr.AgentResult.Opportunities) > 0 {
				for _, opp := range sr.AgentResult.Opportunities {
					fmt.Printf("   → %s %s [%.0f%%]\n",
						opp.Direction, opp.Setup, opp.ConfluenceScore*100)
				}
			}
		}
		if sr.Error != "" {
			fmt.Printf("   Error: %s\n", sr.Error)
		}
		fmt.Println()
	}

	// Generate reports
	fmt.Println("📄 GENERATING REPORTS")
	fmt.Println("---------------------")

	// Markdown report
	mdReport := agent.GenerateReport(result, agent.ReportConfig{
		Title:   fmt.Sprintf("%s %s Analysis", result.Config.Symbol, result.Config.Timeframe),
		Format:  "markdown",
	})
	os.WriteFile("analysis_report.md", []byte(mdReport), 0644)
	fmt.Println("✓ Markdown report: analysis_report.md")

	// Marketing report
	marketingReport := agent.GenerateReport(result, agent.ReportConfig{
		Title:  fmt.Sprintf("%s %s Analysis", result.Config.Symbol, result.Config.Timeframe),
		Format: "marketing",
	})
	os.WriteFile("analysis_thread.txt", []byte(marketingReport), 0644)
	fmt.Println("✓ Marketing thread: analysis_thread.txt")

	// JSON output
	jsonData, _ := json.MarshalIndent(result.ToJSON(), "", "  ")
	os.WriteFile("analysis_full.json", jsonData, 0644)
	fmt.Println("✓ Full JSON: analysis_full.json")

	fmt.Println()
	fmt.Println("✅ All done! Check the generated files.")
}