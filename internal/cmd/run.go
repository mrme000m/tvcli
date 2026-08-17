package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/metadb"
	"github.com/ch99q/tvcli/internal/service"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/runner"
	"github.com/ch99q/tvcli/pkg/tradingview"
)

// runMu serializes `tv run` invocations — only one study can be active per
// TradingView subscription, so concurrent runs would hit the study limit.
var runMu sync.Mutex

// resolvePineID validates the positional argument and optionally maps a
// metadb alias to a real Pine ID. It catches the common zsh/bash mistake of
// typing PUB;<uuid> without quotes, where the shell splits on the semicolon.
func resolvePineID(pineID string, cfg *config.Config) (string, error) {
	if !strings.Contains(pineID, ";") {
		// Unquoted PUB;xxx becomes just "PUB" in the shell.
		if strings.EqualFold(pineID, "PUB") || strings.EqualFold(pineID, "USER") ||
			strings.EqualFold(pineID, "STD") || strings.EqualFold(pineID, "INDIC") {
			return "", fmt.Errorf(`Pine ID %q looks incomplete; the semicolon must be quoted in your shell

Example: ./tvcli run "PUB;your-script-id"`, pineID)
		}
	}
	if !pinefacade.LooksLikePineID(pineID) {
		store, _ := metadb.Load(cfg)
		if entry := store.Get(pineID); entry != nil {
			return entry.PineID, nil
		}
	}
	return pineID, nil
}

type runCmd struct{ app *App }

func (c *runCmd) Name() string      { return "run" }
func (c *runCmd) Aliases() []string { return nil }
func (c *runCmd) Synopsis() string  { return "Run script with chart session" }

func (c *runCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf(`usage: run "<pineId>" [--symbol X] [--tf 5m] [--bars 500] ...`)
	}

	if flags.Has("persistent") || flags.Has("loop") {
		return c.runPersistent(env)
	}

	runMu.Lock()
	defer runMu.Unlock()

	limits := config.GetTierLimits()
	forceCleanup := flags.Has("force-cleanup") || flags.Has("cleanup")

	// Pre-check auth before wasting time on a study that will fail.
	if err := PreCheckAuth(cfg); err != nil {
		return err
	}

	fmt.Fprintf(env.Stderr, "Tier: %s (max %d charts, %d indicators/chart, %ds calc)\n",
		os.Getenv("TV_TIER"), limits.MaxCharts, limits.MaxIndicators, limits.CalcTimeoutSecs)
	if forceCleanup {
		fmt.Fprintln(env.Stderr, "⚠ Force cleanup mode: will aggressively try to free studies")
	}

	pineID, err := resolvePineID(flags.Positional[0], cfg)
	if err != nil {
		return err
	}

	symbol := flags.Get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		return fmt.Errorf("invalid symbol: %v\n\nUse --symbol EXCHANGE:SYMBOL (e.g. OANDA:XAUUSD, BINANCE:BTCUSDT)", err)
	}
	symbol = normalizedSymbol

	tf := flags.Get("tf")
	if tf == "" {
		tf = flags.Get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.GetInt("bars", 500)

	if limits.MaxBars > 0 && bars > limits.MaxBars {
		fmt.Fprintf(env.Stderr, "⚠ Capping bars from %d to %d (tier limit)\n", bars, limits.MaxBars)
		bars = limits.MaxBars
	}

	fmt.Fprintf(env.Stdout, "Running %s\n", pineID)
	fmt.Fprintf(env.Stdout, "  Symbol: %s @ %s, range=%d\n", symbol, tf, bars)

	if !forceCleanup {
		fmt.Fprintf(env.Stderr, "\n⚠ If this fails with 'study limit' error, your TradingView web UI likely has\n")
		fmt.Fprintf(env.Stderr, "  indicators loaded that count against your %d indicator limit.\n", limits.MaxIndicators)
		fmt.Fprintf(env.Stderr, "  Close charts in your browser or use --force-cleanup to retry.\n\n")
	}

	if flags.Has("schema") {
		indicator, err := service.LoadIndicator(cfg, pineID, collectInputs(flags, 1, ReservedRunKeys), ReservedRunKeys)
		if err != nil {
			return fmt.Errorf("failed to load indicator: %w", err)
		}
		if indicator.Schema != nil {
			fmt.Fprintln(env.Stdout, indicator.Schema.Summary())
			if flags.Has("json") {
				b, _ := json.MarshalIndent(indicator.Schema, "", "  ")
				fmt.Fprintln(env.Stdout, string(b))
			}
		} else {
			fmt.Fprintf(env.Stderr, "No schema available for %s (metaInfo had no plots/styles)\n", pineID)
		}
		return nil
	}

	calcTimeout := time.Duration(limits.CalcTimeoutSecs) * time.Second
	start := time.Now()
	res, err := service.RunScript(context.Background(), cfg, service.RunRequest{
		PineID:       pineID,
		Symbol:       symbol,
		Timeframe:    tf,
		Bars:         bars,
		Inputs:       collectInputs(flags, 1, ReservedRunKeys),
		ReservedKeys: ReservedRunKeys,
		SettleMs:     flags.GetInt("settle", 1500),
		ForceCleanup: forceCleanup,
		CalcTimeout:  calcTimeout,
		Debug:        cfg.Debug,
	})
	if err != nil {
		return fmt.Errorf("run: %w", err)
	}

	indicator := res.Indicator
	periods := res.Periods
	graphicData := res.Graphic
	stratReport := res.StrategyReport

	if execRaw(env, map[string]any{
		"pineId":         pineID,
		"symbol":         symbol,
		"timeframe":      tf,
		"bars":           bars,
		"periodCount":    len(periods),
		"periods":        periods,
		"graphic":        graphicData,
		"strategyReport": stratReport,
	}, flags) {
		return nil
	}

	durationMs := time.Since(start).Milliseconds()

	if flags.Has("signals") {
		signals := runner.ExtractSignals(periods, graphicData, stratReport, tf, pineID, symbol, indicator.Schema)
		if flags.Has("agent") {
			workflow := pineID
			if indicator.Schema != nil && indicator.Schema.Name != "" {
				workflow = indicator.Schema.Name
			}
			emitJSON(env, signalsToAgent(signals, workflow, symbol, tf, durationMs), flags.Get("out"))
		} else if flags.Has("json") {
			emitJSON(env, signals, flags.Get("out"))
		} else {
			emitText(env, signals.Compact(), flags.Get("out"))
		}
		return nil
	}

	if flags.Has("multi-run") || flags.Has("sweep") {
		configs := runner.GenerateRunConfigs(indicator.Schema, nil)
		fmt.Fprintf(env.Stderr, "\n📊 Multi-Run: %d configurations generated\n\n", len(configs))
		for i, c := range configs {
			fmt.Fprintf(env.Stderr, "  %2d. %s\n", i+1, c.Label)
			if len(c.Inputs) > 0 {
				for k, v := range c.Inputs {
					fmt.Fprintf(env.Stderr, "      %s = %v\n", k, v)
				}
			}
		}
		if flags.Has("json") {
			b, _ := json.MarshalIndent(configs, "", "  ")
			fmt.Fprintln(env.Stdout, string(b))
		}
		if outFile := flags.Get("out"); outFile != "" {
			b, _ := json.MarshalIndent(configs, "", "  ")
			os.WriteFile(outFile, b, 0644)
			fmt.Fprintf(env.Stdout, "✓ Saved: %s\n", outFile)
		}
		return nil
	}

	result := runner.ParseOutput(periods, graphicData, stratReport, tf, pineID, indicator.Schema)
	output := runner.FormatResults(result, flags.Has("json"))
	fmt.Fprintln(env.Stdout, output)

	if outFile := flags.Get("out"); outFile != "" {
		os.WriteFile(outFile, []byte(output), 0644)
		fmt.Fprintf(env.Stdout, "✓ Saved: %s\n", outFile)
	}
	return nil
}

func (c *runCmd) runPersistent(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf(`persistent run requires a pineId (remember to quote it, e.g. "PUB;...")`)
	}

	pineID, err := resolvePineID(flags.Positional[0], cfg)
	if err != nil {
		return err
	}

	symbol := flags.Get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		return fmt.Errorf("invalid symbol: %v", err)
	}
	symbol = normalizedSymbol

	tf := flags.Get("tf")
	if tf == "" {
		tf = flags.Get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.GetInt("bars", 500)

	limits := config.GetTierLimits()
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		bars = limits.MaxBars
	}

	indicator, err := service.LoadIndicator(cfg, pineID, collectInputs(flags, 1, ReservedRunKeys), ReservedRunKeys)
	if err != nil {
		return fmt.Errorf("failed to load indicator: %w", err)
	}

	settleMs := flags.GetInt("settle", 1500)
	if settleMs <= 0 {
		settleMs = 1500
	}
	calcTimeout := time.Duration(limits.CalcTimeoutSecs) * time.Second
	if calcTimeout == 0 {
		calcTimeout = 60 * time.Second
	}

	pr := runner.NewPersistentRunner(
		[]tradingview.ClientOption{
			tradingview.WithToken(cfg.SessionID),
			tradingview.WithSignature(cfg.Signature),
			tradingview.WithDeviceToken(cfg.DeviceToken),
			tradingview.WithDebug(cfg.Debug),
		},
		cfg.Debug,
	)
	defer pr.Close()

	fmt.Fprintln(env.Stderr, "Persistent mode: WS connection will stay open")
	fmt.Fprintf(env.Stderr, "Running %s on %s @ %s (%d bars)\n", pineID, symbol, tf, bars)

	loopInterval := 0
	if flags.Has("loop") {
		loopStr := flags.Get("loop")
		if loopStr == "" || loopStr == "true" {
			loopInterval = 300
		} else {
			d, err := time.ParseDuration(loopStr)
			if err != nil {
				n := 0
				fmt.Sscanf(loopStr, "%d", &n)
				if n > 0 {
					loopInterval = n
				} else {
					return fmt.Errorf("invalid loop interval: %s (use e.g. 30s, 5m, 1h)", loopStr)
				}
			} else {
				loopInterval = int(d.Seconds())
			}
		}
		fmt.Fprintf(env.Stderr, "Loop mode: re-running every %ds\n", loopInterval)
	}

	runCount := 0
	for {
		runCount++
		if loopInterval > 0 {
			fmt.Fprintf(env.Stderr, "\n--- Run #%d ---\n", runCount)
		}

		result, err := pr.Run(runner.RunOnceOptions{
			PineID:      pineID,
			Symbol:      symbol,
			Timeframe:   tf,
			Bars:        bars,
			Indicator:   indicator,
			SettleMs:    settleMs,
			CalcTimeout: calcTimeout,
			Debug:       cfg.Debug,
		})
		if err != nil {
			fmt.Fprintf(env.Stderr, "Run error: %v\n", err)
			if loopInterval == 0 {
				return fmt.Errorf("run failed: %w", err)
			}
			fmt.Fprintf(env.Stderr, "Retrying in %ds...\n", loopInterval)
			time.Sleep(time.Duration(loopInterval) * time.Second)
			continue
		}

		if flags.Has("signals") {
			if result.Extracted != nil {
				if flags.Has("json") {
					b, _ := json.MarshalIndent(result.Extracted, "", "  ")
					fmt.Fprintln(env.Stdout, string(b))
				} else {
					fmt.Fprintln(env.Stdout, result.Extracted.Compact())
				}
			} else {
				fmt.Fprintln(env.Stderr, "No signals extracted")
			}
		} else if flags.Has("json") {
			b, _ := json.MarshalIndent(result, "", "  ")
			fmt.Fprintln(env.Stdout, string(b))
		} else {
			output := runner.FormatResults(result, false)
			fmt.Fprintln(env.Stdout, output)
		}

		if outFile := flags.Get("out"); outFile != "" {
			b, _ := json.MarshalIndent(result, "", "  ")
			os.WriteFile(outFile, b, 0644)
			fmt.Fprintf(env.Stderr, "✓ Saved: %s\n", outFile)
		}

		if loopInterval == 0 {
			break
		}

		fmt.Fprintf(env.Stderr, "Next run in %ds...\n", loopInterval)
		time.Sleep(time.Duration(loopInterval) * time.Second)
	}
	return nil
}
