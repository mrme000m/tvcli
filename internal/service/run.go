package service

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/tradingview"
)

// RunRequest is the input to RunScript.
type RunRequest struct {
	PineID       string // already resolved (caller handles metadb lookup)
	Symbol       string // already normalized via pinefacade.ValidateSymbol
	Timeframe    string
	Bars         int
	Inputs       map[string]string // applied to the indicator via SetOption
	ReservedKeys []string          // keys in Inputs to skip (reserved flag names)
	SettleMs     int               // 0 → 1500
	ForceCleanup bool
	CalcTimeout  time.Duration // 0 → 120s
	Debug        bool
}

// RunResult is the raw output of one indicator run.
type RunResult struct {
	Indicator      *tradingview.PineIndicator
	Periods        []map[string]any
	Graphic        map[string]map[string]any
	StrategyReport map[string]any
}

// LoadIndicator fetches the script source + metaInfo from Pine Facade and
// builds a *tradingview.PineIndicator with the given inputs applied.
// Reserved keys are skipped (those are CLI flag names, not script inputs).
func LoadIndicator(cfg *config.Config, pineID string, inputs map[string]string, reserved []string) (*tradingview.PineIndicator, error) {
	pineClient := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	indResult, err := pineClient.Get(pineID, "last", cfg.CookieHeaderOrEmpty())
	if err != nil {
		return nil, fmt.Errorf("load indicator: %w", err)
	}
	if indResult.Source == "" {
		return nil, fmt.Errorf("indicator returned empty source for %s", pineID)
	}

	indicatorOpts := map[string]any{
		"pineId": pineID,
		"script": indResult.Source,
	}
	if indResult.MetaInfo != nil {
		indicatorOpts["metaInfo"] = indResult.MetaInfo
		if pine, ok := indResult.MetaInfo["pine"].(map[string]any); ok {
			if v, ok := pine["version"].(string); ok {
				indicatorOpts["pineVersion"] = v
			}
		}
	} else {
		indicatorOpts["metaInfo"] = map[string]any{"inputs": []any{}}
	}

	indicator := tradingview.NewPineIndicator(indicatorOpts)

	if cfg.Debug {
		fmt.Fprintf(os.Stderr, "[debug] source length: %d, metaInfo present: %v\n",
			len(indResult.Source), indResult.MetaInfo != nil)
		if indResult.MetaInfo != nil {
			if inputsArr, ok := indResult.MetaInfo["inputs"].([]any); ok {
				fmt.Fprintf(os.Stderr, "[debug] metaInfo.inputs count: %d\n", len(inputsArr))
				for _, inp := range inputsArr {
					if m, ok := inp.(map[string]any); ok {
						id, _ := m["id"].(string)
						if id == "" || id == "text" || id == "pineId" || id == "pineVersion" || id == "pineFeatures" || id == "__profile" || id == "__fast_calc" {
							continue
						}
						fmt.Fprintf(os.Stderr, "[debug] input: %s (type=%s defval=%v)\n",
							id, m["type"], m["defval"])
					}
				}
			}
		}
	}

	skip := make(map[string]bool, len(reserved))
	for _, k := range reserved {
		skip[k] = true
	}
	for k, v := range inputs {
		if skip[k] {
			continue
		}
		if err := indicator.SetOption(k, v); err != nil {
			fmt.Fprintf(os.Stderr, "⚠ Input '%s': %v\n", k, err)
		}
	}
	return indicator, nil
}

// RunScript connects a fresh WS client, loads the indicator, runs the study
// with retry on study-limit errors, and returns the raw periods, graphic,
// and strategy report. The caller formats the output.
//
// All informational messages go to os.Stderr; errors are returned.
// The ctx is reserved for future cancellation (the WS client currently
// ignores it, but the signature is ready).
//
// ponytail: ctx is accepted but not yet wired into the WS client. When the
// client gains cancellation support, thread it through.
func RunScript(ctx context.Context, cfg *config.Config, req RunRequest) (*RunResult, error) {
	_ = ctx

	settleMs := req.SettleMs
	if settleMs <= 0 {
		settleMs = 1500
	}
	calcTimeout := req.CalcTimeout
	if calcTimeout == 0 {
		calcTimeout = 120 * time.Second
	}

	indicator, err := LoadIndicator(cfg, req.PineID, req.Inputs, req.ReservedKeys)
	if err != nil {
		return nil, err
	}
	fmt.Fprintf(os.Stderr, "Indicator loaded: %d inputs defined\n", len(indicator.Inputs))

	// Connect fresh — helper that reconnects a client.
	var client tradingview.Client
	connectFresh := func() error {
		if client != nil {
			client.Close()
		}
		client = tradingview.NewClient(
			tradingview.WithToken(cfg.SessionID),
			tradingview.WithSignature(cfg.Signature),
			tradingview.WithDeviceToken(cfg.DeviceToken),
			tradingview.WithDebug(cfg.Debug),
		)
		if err := client.Connect(); err != nil {
			return fmt.Errorf("ws connect: %w", err)
		}
		if !client.WaitForConnected(10 * time.Second) {
			return fmt.Errorf("ws timeout")
		}
		return nil
	}

	if err := connectFresh(); err != nil {
		return nil, err
	}
	defer func() {
		if client != nil {
			client.Close()
		}
	}()

	createChart := func() (*tradingview.ChartSession, error) {
		ch := tradingview.NewChartSession(client)
		ch.OnError(func(err error) {
			fmt.Fprintf(os.Stderr, "Chart error: %v\n", err)
		})
		ch.SetMarket(req.Symbol, map[string]any{
			"timeframe": pinefacade.NormalizeTimeframe(req.Timeframe),
			"range":     req.Bars,
		})
		if err := ch.WaitForSymbol(15 * time.Second); err != nil {
			return nil, fmt.Errorf("symbol load: %w", err)
		}
		if cfg.Debug {
			info := ch.GetSymbolInfo()
			fmt.Fprintf(os.Stderr, "[debug] chart session %s loaded, symbol info: %v\n", ch.GetSessionID(), info)
		}
		return ch, nil
	}

	chart, err := createChart()
	if err != nil {
		return nil, err
	}
	defer func() {
		if chart != nil {
			chart.RemoveAllStudies()
			chart.Delete()
		}
	}()

	var periods []map[string]any
	var graphicData map[string]map[string]any
	var stratReport map[string]any

	isStudyLimitError := func(err error) bool {
		if err == nil {
			return false
		}
		msg := err.Error()
		return strings.Contains(msg, "maximum number of studies") ||
			strings.Contains(msg, "too many") ||
			strings.Contains(msg, "study limit")
	}

	maxAttempts := 3
	if req.ForceCleanup {
		maxAttempts = 5
	}

	// Pre-cleanup: fresh session to clear stale state.
	fmt.Fprintf(os.Stderr, "🧹 Pre-cleanup: fresh session...\n")
	chart.RemoveAllStudies()
	chart.Delete()
	time.Sleep(500 * time.Millisecond)
	if err := connectFresh(); err != nil {
		return nil, fmt.Errorf("pre-cleanup reconnect: %w", err)
	}
	chart, err = createChart()
	if err != nil {
		return nil, fmt.Errorf("pre-cleanup chart recreate: %w", err)
	}

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if existing := chart.GetStudies(); len(existing) > 0 {
			fmt.Fprintf(os.Stderr, "🧹 Cleaning %d existing study/studies on this session...\n", len(existing))
			chart.RemoveAllStudies()
			time.Sleep(500 * time.Millisecond)
		}

		if existing := chart.GetStudies(); len(existing) > 0 {
			fmt.Fprintf(os.Stderr, "📊 Session has %d existing study/studies: %v\n", len(existing), existing)
		} else {
			fmt.Fprintf(os.Stderr, "📊 Session has no existing studies\n")
		}

		study := chart.Study(indicator)

		done := make(chan struct{}, 1)
		var studyErr error
		once := sync.Once{}

		// Safe close: multiple goroutines (OnUpdate settle, OnReady, OnError)
		// may signal done; use a buffered channel with non-blocking send to
		// avoid double-close panics.
		signalDone := func() {
			select {
			case done <- struct{}{}:
			default:
			}
		}

		study.OnUpdate(func() {
			once.Do(func() {
				periods = study.Periods()
				graphicData = study.Graphic()
				stratReport = study.StrategyReport()
				// Some scripts (e.g. volume profile, boxes, tables) produce no
				// numeric periods but still emit graphic/strategy data. Treat any
				// non-empty payload as a sign the study is alive.
				if len(periods) > 0 || len(graphicData) > 0 || len(stratReport) > 0 {
					go func() {
						timer := time.NewTimer(time.Duration(settleMs) * time.Millisecond)
						defer timer.Stop()
						select {
						case <-done:
						case <-timer.C:
							signalDone()
						}
					}()
				}
			})
		})
		study.OnError(func(err error) {
			once.Do(func() {
				studyErr = err
			})
			signalDone()
		})
		study.OnReady(func() {
			// Graphics-only scripts (e.g. anchored-vp) may never emit period
			// data, so the OnUpdate settle timer never starts. Signal done
			// when the study finishes so the final snapshot is taken.
			// Small delay lets any pending du/timescale_update messages arrive
			// before the runner takes the final snapshot.
			go func() {
				time.Sleep(200 * time.Millisecond)
				signalDone()
			}()
		})

		select {
		case <-done:
		case <-time.After(calcTimeout):
			studyErr = fmt.Errorf("timeout after %s waiting for study data", calcTimeout)
		}

		// Final snapshot after settle / timeout.
		periods = study.Periods()
		graphicData = study.Graphic()
		stratReport = study.StrategyReport()

		if studyErr == nil && (len(periods) > 0 || len(graphicData) > 0 || len(stratReport) > 0) {
			study.Remove()
			fmt.Fprintf(os.Stderr, "✓ Study data received (%d periods, %d graphic types)\n", len(periods), len(graphicData))
			break
		}

		study.Remove()

		if isStudyLimitError(studyErr) && attempt < maxAttempts {
			fmt.Fprintf(os.Stderr, "⚠ Study limit hit (attempt %d/%d). Reconnecting in %ds...\n", attempt, maxAttempts, attempt*3)
			chart.RemoveAllStudies()
			chart.Delete()
			time.Sleep(time.Duration(attempt*3) * time.Second)
			if err := connectFresh(); err != nil {
				return nil, fmt.Errorf("reconnect: %w", err)
			}
			chart, err = createChart()
			if err != nil {
				return nil, fmt.Errorf("chart recreate: %w", err)
			}
			continue
		}

		if studyErr != nil {
			return nil, fmt.Errorf("study error: %w", studyErr)
		}
		break
	}

	return &RunResult{
		Indicator:      indicator,
		Periods:        periods,
		Graphic:        graphicData,
		StrategyReport: stratReport,
	}, nil
}
