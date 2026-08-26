package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/runner"
	"github.com/mrme000m/tvcli/pkg/tradingview"
)

type syncCmd struct{ app *App }

func (c *syncCmd) Name() string      { return "sync" }
func (c *syncCmd) Aliases() []string { return nil }
func (c *syncCmd) Synopsis() string  { return "Fetch + compress OHLCV to .json.gz (gap-fills existing)" }

func (c *syncCmd) Run(env *cli.Env) error {
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
	bars := flags.GetInt("bars", 5000)

	if bars > maxHistoryBars {
		fmt.Fprintf(env.Stderr, "Capping bars from %d to %d (history cap)\n", bars, maxHistoryBars)
		bars = maxHistoryBars
	}

	symbolClean := strings.ReplaceAll(symbol, ":", "_")
	baseName := fmt.Sprintf("%s_%s", symbolClean, tf)

	outDir := flags.Get("dir")
	if outDir == "" {
		outDir = "."
	}
	os.MkdirAll(outDir, 0755)

	filePath := flags.Get("out")
	if filePath == "" {
		filePath = filepath.Join(outDir, baseName+".json.gz")
	}

	var existing *service.OHLCVFile
	force := flags.Has("force")
	if !force {
		if f, err := service.LoadOHLCV(filePath); err == nil {
			existing = f
			fmt.Fprintf(env.Stderr, "Loaded existing: %s (%d bars, updated %s)\n",
				filePath, f.Count, f.UpdatedAt)
		}
	}

	fetchBars := bars
	if existing != nil && len(existing.Data) > 0 {
		latest := service.LastTimestamp(existing.Data)
		age := time.Now().Unix() - int64(latest)
		tfSecs := service.TimeframeSeconds(tf)
		if tfSecs > 0 {
			gapBars := int(age/int64(tfSecs)) + 10
			if gapBars < bars {
				fetchBars = bars
			}
		}
		fmt.Fprintf(env.Stderr, "Gap-fill: last bar at %s, fetching %d bars\n",
			time.Unix(int64(latest), 0).UTC().Format("2006-01-02T15:04:05Z"), fetchBars)
	}

	fmt.Fprintf(env.Stderr, "Fetching OHLCV: %s @ %s, %d bars\n", symbol, tf, fetchBars)
	start := time.Now()

	fresh, err := service.FetchOHLCVBars(cfg, symbol, tf, fetchBars)
	if err != nil {
		return fmt.Errorf("fetch: %w", err)
	}

	elapsed := time.Since(start)
	fmt.Fprintf(env.Stderr, "Received %d bars in %s\n", len(fresh), elapsed.Round(time.Millisecond))

	var merged []service.OHLCVBar
	if existing != nil {
		merged = service.MergeOHLCV(existing.Data, fresh)
		added := len(merged) - len(existing.Data)
		fmt.Fprintf(env.Stderr, "Merged: %d existing + %d new = %d total (+%d)\n",
			len(existing.Data), len(fresh), len(merged), added)
	} else {
		merged = fresh
	}

	file := &service.OHLCVFile{
		Symbol:    symbol,
		Timeframe: tf,
		Count:     len(merged),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
		Data:      merged,
	}

	if err := service.SaveOHLCV(filePath, file); err != nil {
		return fmt.Errorf("save: %w", err)
	}

	finfo, _ := os.Stat(filePath)
	sizeKB := finfo.Size() / 1024

	span := ""
	if len(merged) > 1 {
		first := time.Unix(int64(merged[0].Time), 0).UTC()
		last := time.Unix(int64(merged[len(merged)-1].Time), 0).UTC()
		d := last.Sub(first)
		span = fmt.Sprintf(", spans %s (%s to %s)", d.Round(time.Second), first.Format("2006-01-02"), last.Format("2006-01-02"))
	}

	fmt.Fprintf(env.Stderr, "\nSaved: %s (%dKB gzipped, %d bars%s)\n", filePath, sizeKB, len(merged), span)

	loopSecs := 0
	if flags.Has("loop") {
		loopStr := flags.Get("loop")
		if loopStr == "" || loopStr == "true" {
			loopSecs = 300
		} else {
			d, err := time.ParseDuration(loopStr)
			if err != nil {
				n := 0
				fmt.Sscanf(loopStr, "%d", &n)
				if n > 0 {
					loopSecs = n
				} else {
					return fmt.Errorf("invalid loop interval: %s", loopStr)
				}
			} else {
				loopSecs = int(d.Seconds())
			}
		}
		fmt.Fprintf(env.Stderr, "Loop mode: syncing every %ds\n", loopSecs)
	}

	if loopSecs == 0 {
		return nil
	}

	fmt.Fprintln(env.Stderr, "Persistent mode: WS connection stays open")
	pr := runner.NewPersistentRunner(
		[]tradingview.ClientOption{
			tradingview.WithToken(cfg.SessionID),
			tradingview.WithSignature(cfg.Signature),
			tradingview.WithDeviceToken(cfg.DeviceToken),
			tradingview.WithProxy(cfg.ProxyURL),
			tradingview.WithDebug(cfg.Debug),
		},
		cfg.Debug,
	)
	defer pr.Close()

	pr.OnDisconnected(func() {
		fmt.Fprintln(env.Stderr, "[sync] ws disconnected, will reconnect on next cycle")
	})

	for {
		time.Sleep(time.Duration(loopSecs) * time.Second)

		if f, err := service.LoadOHLCV(filePath); err == nil {
			existing = f
		}

		if err := pr.EnsureConnected(); err != nil {
			fmt.Fprintf(env.Stderr, "Reconnect error: %v\n", err)
			continue
		}

		fresh, err := service.FetchOHLCVBarsWithClient(pr.Client(), symbol, tf, bars)
		if err != nil {
			fmt.Fprintf(env.Stderr, "Loop fetch error: %v\n", err)
			continue
		}

		if existing != nil {
			merged = service.MergeOHLCV(existing.Data, fresh)
		} else {
			merged = fresh
		}

		file.Data = merged
		file.Count = len(merged)
		file.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

		if err := service.SaveOHLCV(filePath, file); err != nil {
			fmt.Fprintf(env.Stderr, "Loop save error: %v\n", err)
			continue
		}

		finfo, _ := os.Stat(filePath)
		fmt.Fprintf(env.Stderr, "[%s] Synced: %d bars, %dKB\n",
			time.Now().Format("15:04:05"), len(merged), finfo.Size()/1024)
	}
}
