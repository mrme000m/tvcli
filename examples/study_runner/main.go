// Command study_runner demonstrates using tvcli as a library: load the
// default TradingView account from the environment (pkg/account), fetch a
// script's compiled source from Pine Facade (pkg/pinefacade), run it over
// WebSocket (pkg/tradingview), and print the raw periods as JSON.
//
// It imports only pkg/* — the same surface any external Go program can use
// (add `github.com/mrme000m/tvcli` to go.mod, or a `replace` directive for
// local development).
//
// Usage:
//
//	go run ./examples/study_runner -pine "PUB;..." -symbol OANDA:XAUUSD -tf 1H
//	go run ./examples/study_runner -source ./my_script.pine -symbol BTCUSDT -tf 5m
//
// Requires SESSION/SIGNATURE/DEVICE_T in the environment (or .env in the
// working directory) for authenticated runs.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/mrme000m/tvcli/pkg/account"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/tradingview"
)

func main() {
	pineID := flag.String("pine", "", "Pine script ID (PUB;... or USER;...)")
	sourceFile := flag.String("source", "", "local .pine file to run instead of a Pine ID")
	symbol := flag.String("symbol", "OANDA:XAUUSD", "market symbol")
	tf := flag.String("tf", "1H", "timeframe")
	bars := flag.Int("bars", 180, "number of bars")
	settle := flag.Int("settle", 1500, "settle time in ms after data arrives")
	flag.Parse()

	if *pineID == "" && *sourceFile == "" {
		fmt.Fprintln(os.Stderr, "error: provide -pine or -source")
		flag.Usage()
		os.Exit(2)
	}

	// 1. Load the default account (legacy single-account env synthesis).
	reg := account.LoadFromEnv()
	acct := reg.DefaultAccount()
	if !acct.HasAuth() {
		fmt.Fprintln(os.Stderr, "⚠ no SESSION set — anonymous; study runs will be limited to 0 studies")
	}

	// 2. Build the indicator: from raw source or via Pine Facade (compiled IL).
	var indicator *tradingview.PineIndicator
	if *sourceFile != "" {
		src, err := os.ReadFile(*sourceFile)
		if err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		indicator = tradingview.NewPineIndicator(map[string]any{
			"pineId":      "USER;" + *sourceFile,
			"script":      string(src),
			"metaInfo":    map[string]any{"inputs": []any{}},
			"pineVersion": "1.0",
		})
	} else {
		client := pinefacade.NewClient("https://pine-facade.tradingview.com/pine-facade", acct.UserName, 30*time.Second)
		res, err := client.Get(*pineID, "last", acct.CookieHeader())
		if err != nil {
			fmt.Fprintln(os.Stderr, "error: load from Pine Facade:", err)
			os.Exit(1)
		}
		opts := map[string]any{"pineId": *pineID, "script": res.Source}
		if res.MetaInfo != nil {
			opts["metaInfo"] = res.MetaInfo
		} else {
			opts["metaInfo"] = map[string]any{"inputs": []any{}}
		}
		indicator = tradingview.NewPineIndicator(opts)
	}

	// 3. Connect a WS client and run the study.
	client := tradingview.NewClient(
		tradingview.WithToken(acct.SessionID),
		tradingview.WithSignature(acct.Signature),
		tradingview.WithDeviceToken(acct.DeviceToken),
	)
	if err := client.Connect(); err != nil {
		fmt.Fprintln(os.Stderr, "error: ws connect:", err)
		os.Exit(1)
	}
	defer client.Close()
	if !client.WaitForConnected(10 * time.Second) {
		fmt.Fprintln(os.Stderr, "error: ws timeout")
		os.Exit(1)
	}

	ch := tradingview.NewChartSession(client)
	ch.SetMarket(*symbol, map[string]any{"timeframe": *tf, "range": *bars})
	if err := ch.WaitForSymbol(15 * time.Second); err != nil {
		fmt.Fprintln(os.Stderr, "error: symbol load:", err)
		os.Exit(1)
	}

	study := ch.Study(indicator)
	done := make(chan struct{})
	var periods []map[string]any
	var graphic map[string]map[string]any
	var stratReport map[string]any
	var studyErr error
	once := sync.Once{}

	study.OnUpdate(func() {
		once.Do(func() {
			periods = study.Periods()
			graphic = study.Graphic()
			stratReport = study.StrategyReport()
			go func() {
				time.Sleep(time.Duration(*settle) * time.Millisecond)
				select {
				case done <- struct{}{}:
				default:
				}
			}()
		})
	})
	study.OnError(func(err error) {
		once.Do(func() {
			studyErr = err
			select {
			case done <- struct{}{}:
			default:
			}
		})
	})

	select {
	case <-done:
	case <-time.After(120 * time.Second):
		studyErr = fmt.Errorf("timeout waiting for study data")
	}
	study.Remove()
	ch.Delete()

	if studyErr != nil {
		fmt.Fprintln(os.Stderr, "study error:", studyErr)
		os.Exit(1)
	}

	// 4. Print the raw capture as JSON.
	out := map[string]any{
		"periods":        periods,
		"graphicTypes":   len(graphic),
		"strategyReport": stratReport != nil,
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}