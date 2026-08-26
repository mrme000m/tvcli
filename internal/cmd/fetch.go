package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

type fetchCmd struct{ app *App }

func (c *fetchCmd) Name() string      { return "fetch" }
func (c *fetchCmd) Aliases() []string { return []string{"ohlcv"} }
func (c *fetchCmd) Synopsis() string  { return "Fetch raw OHLCV data (no indicator needed)" }

func (c *fetchCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

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
	bars := flags.GetInt("bars", 180)

	deepBars := flags.GetInt("deep", 0)
	if flags.Has("all") {
		deepBars = maxHistoryBars
	}

	initial := bars
	if initial > maxHistoryBars {
		initial = maxHistoryBars
	}
	if deepBars > 0 && deepBars < initial {
		initial = deepBars
	}
	if initial < 1 {
		initial = 1
	}

	var periods []service.OHLCVBar
	if deepBars > initial {
		fmt.Fprintf(env.Stderr, "Fetching OHLCV: %s @ %s, %d bars (backfilling to %d)\n", symbol, tf, initial, deepBars)
		periods, err = service.FetchOHLCVBarsDeep(cfg, symbol, tf, initial, deepBars)
	} else {
		fmt.Fprintf(env.Stderr, "Fetching OHLCV: %s @ %s, %d bars\n", symbol, tf, initial)
		periods, err = service.FetchOHLCVBars(cfg, symbol, tf, initial)
	}
	if err != nil {
		return fmt.Errorf("fetch: %w", err)
	}

	fmt.Fprintf(env.Stderr, "Received %d bars\n", len(periods))

	requestedBars := initial
	if deepBars > requestedBars {
		requestedBars = deepBars
	}

	symbolClean := strings.ReplaceAll(symbol, ":", "_")
	baseName := fmt.Sprintf("%s_%s_%dbars", symbolClean, tf, requestedBars)

	outDir := flags.Get("dir")
	if outDir == "" {
		outDir = "."
	}
	os.MkdirAll(outDir, 0755)

	jsonPath := filepath.Join(outDir, baseName+".json")
	csvPath := filepath.Join(outDir, baseName+".csv")

	rawPeriods := make([]map[string]any, 0, len(periods))
	for _, b := range periods {
		rawPeriods = append(rawPeriods, map[string]any{
			"time":   b.Time,
			"open":   b.Open,
			"high":   b.High,
			"low":    b.Low,
			"close":  b.Close,
			"volume": b.Volume,
		})
	}
	jsonData := map[string]any{
		"symbol":    symbol,
		"timeframe": tf,
		"bars":      requestedBars,
		"count":     len(rawPeriods),
		"fetchedAt": time.Now().UTC().Format(time.RFC3339),
		"data":      rawPeriods,
	}

	if outJSON := flags.Get("json-out"); outJSON != "" {
		jsonPath = outJSON
	}
	jsonBytes, _ := json.MarshalIndent(jsonData, "", "  ")
	if err := os.WriteFile(jsonPath, jsonBytes, 0644); err != nil {
		return fmt.Errorf("write JSON: %w", err)
	}
	fmt.Fprintf(env.Stderr, "  JSON: %s (%d bytes)\n", jsonPath, len(jsonBytes))

	if outCSV := flags.Get("csv-out"); outCSV != "" {
		csvPath = outCSV
	}
	csvFile, err := os.Create(csvPath)
	if err != nil {
		return fmt.Errorf("create CSV: %w", err)
	}
	defer csvFile.Close()

	fmt.Fprintln(csvFile, "time,open,high,low,close,volume")
	for _, bar := range periods {
		utcTime := time.Unix(int64(bar.Time), 0).UTC().Format("2006-01-02T15:04:05Z")
		fmt.Fprintf(csvFile, "%s,%.8f,%.8f,%.8f,%.8f,%.2f\n",
			utcTime, bar.Open, bar.High, bar.Low, bar.Close, bar.Volume)
	}
	csvFile.Close()
	csvInfo, _ := os.Stat(csvPath)
	fmt.Fprintf(env.Stderr, "  CSV:  %s (%d bytes)\n", csvPath, csvInfo.Size())

	fmt.Fprintf(env.Stdout, "Fetched %d bars for %s @ %s\n", len(periods), symbol, tf)
	return nil
}
