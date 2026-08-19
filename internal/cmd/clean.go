package cmd

import (
	"fmt"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/tradingview"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

type cleanCmd struct{ app *App }

func (c *cleanCmd) Name() string      { return "clean" }
func (c *cleanCmd) Aliases() []string { return []string{"cleanup"} }
func (c *cleanCmd) Synopsis() string  { return "Connect to TradingView WS, delete all chart sessions, and free indicator slots" }

// Run connects to TradingView's WS server, creates and immediately deletes
// multiple chart sessions to flush any stale indicator slots. This is useful
// when the study limit is hit due to leftover sessions from browser tabs or
// crashed processes.
func (c *cleanCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	iterations := flags.GetInt("iterations", 3)
	delayMs := flags.GetInt("delay", 500)

	// Pre-check auth before attempting cleanup.
	if cfg.HasAuth() {
		info := auth.FetchAuthInfo(cfg.SessionID, cfg.Signature, "", cfg.DeviceToken, auth.WithProxy(cfg.ProxyURL))
		if !info.Authenticated {
			fmt.Fprintf(env.Stderr, "⚠ Authentication check: cookies are EXPIRED.\n")
			fmt.Fprintf(env.Stderr, "  %v\n", info.Error)
			fmt.Fprintf(env.Stderr, "  Running 'clean' won't help — re-extract cookies first.\n")
			fmt.Fprintf(env.Stderr, "  Run: ./tvcli check-auth\n")
			return nil
		}
		fmt.Fprintf(env.Stderr, "✓ Auth verified: authenticated (plan=%s)\n", info.Plan)
	}

	fmt.Fprintf(env.Stderr, "Connecting to TradingView WS...\n")

	client := tradingview.NewClient(
		tradingview.WithToken(cfg.SessionID),
		tradingview.WithSignature(cfg.Signature),
			tradingview.WithDeviceToken(cfg.DeviceToken),
		tradingview.WithProxy(cfg.ProxyURL),
		tradingview.WithDebug(cfg.Debug),
	)
	if err := client.Connect(); err != nil {
		return fmt.Errorf("ws connect: %w", err)
	}
	if !client.WaitForConnected(10 * time.Second) {
		return fmt.Errorf("ws timeout — could not connect to TradingView")
	}
	defer client.Close()

	fmt.Fprintf(env.Stderr, "Connected. Cleaning up %d iterations...\n", iterations)

	for i := 0; i < iterations; i++ {
		// Create a chart session and immediately delete it.
		chart := tradingview.NewChartSession(client)
		chart.OnError(func(err error) {
			fmt.Fprintf(env.Stderr, "  Chart error: %v\n", err)
		})

		// Set a market to trigger symbol resolution (required before study add).
		symbol := flags.Get("symbol")
		if symbol == "" {
			symbol = "BINANCE:BTCUSDT"
		}
		chart.SetMarket(symbol, map[string]any{
			"timeframe": "1",
			"range":     1,
		})

		// Wait briefly for symbol to load.
		_ = chart.WaitForSymbol(5 * time.Second)

		// Remove all studies (should be 0, but just in case).
		existing := chart.GetStudies()
		if len(existing) > 0 {
			fmt.Fprintf(env.Stderr, "  [%d] Found %d existing studies, removing...\n", i+1, len(existing))
			chart.RemoveAllStudies()
		} else {
			fmt.Fprintf(env.Stderr, "  [%d] No existing studies on session %s\n", i+1, chart.GetSessionID())
		}

		// Delete the chart session to free server-side resources.
		chart.Delete()
		time.Sleep(time.Duration(delayMs) * time.Millisecond)
	}

	fmt.Fprintf(env.Stderr, "✓ Cleanup complete. Wait 5-10 seconds before running indicators.\n")

	// Close the client (sends delete_session for any remaining sessions).
	client.Close()
	return nil
}
