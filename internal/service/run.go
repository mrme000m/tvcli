package service

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/tradingview"
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
	Source       string // raw Pine source; when set, bypasses Pine Facade LoadIndicator
}

// RunResult is the raw output of one indicator run.
type RunResult struct {
	Indicator      *tradingview.PineIndicator
	Periods        []map[string]any
	Graphic        map[string]map[string]any
	StrategyReport map[string]any
}

// PreCheckScriptOwnership verifies a private (USER;) Pine script is still
// owned by the current TradingView user BEFORE a run is attempted. A USER;
// ID that is absent from the session's saved-script list belongs to a
// different account (or was deleted) and would otherwise fail mid-run with an
// opaque "no source / status 401" study error. Public (PUB;) scripts are
// never blocked; transient listing failures also pass so a valid script is
// not wrongly refused.
func PreCheckScriptOwnership(cfg *config.Config, pineID string) error {
	if pinefacade.AccessFromPineID(pineID) != "private" {
		return nil
	}
	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond, pinefacade.WithProxy(cfg.ProxyURL))
	owned, err := client.UserOwnsScript(pineID, cfg.CookieHeaderOrEmpty())
	if err != nil {
		return nil // best-effort: never block on a transient listing failure
	}
	if !owned {
		return fmt.Errorf(
			"private script %s is not among the current user's saved scripts — it belongs to a different account or was deleted; re-upload with `tvcli create <file.pine>` and update the skill's PineID", pineID)
	}
	return nil
}

// LoadIndicator fetches the script source + metaInfo from Pine Facade and
// builds a *tradingview.PineIndicator with the given inputs applied.
// Reserved keys are skipped (those are CLI flag names, not script inputs).
func LoadIndicator(cfg *config.Config, pineID string, inputs map[string]string, reserved []string) (*tradingview.PineIndicator, error) {
	pineClient := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond, pinefacade.WithProxy(cfg.ProxyURL))
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

	// When Source is provided (private scripts with raw Pine source),
	// build the indicator directly without Pine Facade — the Pine Facade
	// returns incomplete metaInfo for private scripts, causing 0 periods.
	var indicator *tradingview.PineIndicator
	var err error
	if req.Source != "" {
		indicatorOpts := map[string]any{
			"pineId":      req.PineID,
			"script":      req.Source,
			"metaInfo":    map[string]any{"inputs": []any{}},
			"pineVersion": "1.0",
		}
		indicator = tradingview.NewPineIndicator(indicatorOpts)
		for k, v := range req.Inputs {
			skip := false
			for _, r := range req.ReservedKeys {
				if k == r {
					skip = true
					break
				}
			}
			if skip {
				continue
			}
			if sErr := indicator.SetOption(k, v); sErr != nil {
				fmt.Fprintf(os.Stderr, "⚠ Input '%s': %v\n", k, sErr)
			}
		}
		fmt.Fprintf(os.Stderr, "Indicator built from source: %d inputs defined\n", len(indicator.Inputs))
	} else {
		indicator, err = LoadIndicator(cfg, req.PineID, req.Inputs, req.ReservedKeys)
		if err != nil {
			return nil, err
		}
		fmt.Fprintf(os.Stderr, "Indicator loaded: %d inputs defined\n", len(indicator.Inputs))
	}

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
			tradingview.WithProxy(cfg.ProxyURL),
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

	// No pre-cleanup: the chart session was just created fresh on a new WS
	// connection, so it has zero studies. The old pre-cleanup created a
	// throwaway chart session just to delete it, which left stale state on
	// the TradingView server and made study-limit errors worse, not better.

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
			// Wait for the server to process the study removal so the slot is
			// released before the deferred chart.Delete() and client.Close()
			// run. Without this, the next run may hit a study-limit error
			// because the server hasn't released the slot yet.
			time.Sleep(200 * time.Millisecond)
			fmt.Fprintf(os.Stderr, "✓ Study data received (%d periods, %d graphic types)\n", len(periods), len(graphicData))
			break
		}

		study.Remove()
		// Wait for the server to process the study removal before cleaning
		// up the chart session.
		time.Sleep(200 * time.Millisecond)

		if isStudyLimitError(studyErr) && attempt < maxAttempts {
			// Check if the root cause is expired/unauthenticated cookies.
			// When the auth token is "unauthorized_user_token", TradingView
			// allows 0 studies — every create_study fails with a "maximum
			// number of studies" error regardless of the actual tier.
			if authInfo := client.AuthStatus(); authInfo != nil && !authInfo.Authenticated {
				fmt.Fprintf(os.Stderr, "❌ Study limit hit, but the root cause is EXPIRED COOKIES.\n")
				fmt.Fprintf(os.Stderr, "  The auth token fetch failed: %v\n", authInfo.Error)
				fmt.Fprintf(os.Stderr, "  TradingView limits unauthorized sessions to 0 studies.\n")
				fmt.Fprintf(os.Stderr, "  Retrying will NOT help. Fix:\n")
				fmt.Fprintf(os.Stderr, "    1. Open https://www.tradingview.com/chart/ in your browser\n")
				fmt.Fprintf(os.Stderr, "    2. DevTools → Application → Cookies → https://www.tradingview.com\n")
				fmt.Fprintf(os.Stderr, "    3. Copy sessionid, sessionid_sign, device_t into .env\n")
				fmt.Fprintf(os.Stderr, "    4. Run: ./tvcli check-auth  (to verify before retrying)\n")
				chart.RemoveAllStudies()
				chart.Delete()
				return nil, fmt.Errorf("auth: cookies expired — re-extract SESSION/SIGNATURE/DEVICE_T (run 'tvcli check-auth' to diagnose)")
			}

			// Exponential backoff: 5s, 10s, 20s, 40s, 60s (capped).
			retryDelay := time.Duration(attempt*5) * time.Second
			if retryDelay > 60*time.Second {
				retryDelay = 60 * time.Second
			}
			fmt.Fprintf(os.Stderr, "⚠ Study limit hit (attempt %d/%d). Reconnecting in %v...\n", attempt, maxAttempts, retryDelay)
			fmt.Fprintf(os.Stderr, "  (This is an account-level limit. Close TradingView charts in\n")
			fmt.Fprintf(os.Stderr, "   your browser or wait for stale sessions to expire.)\n")
			if authInfo := client.AuthStatus(); authInfo != nil {
				fmt.Fprintf(os.Stderr, "  Auth: authenticated=%v plan=%s pro=%v\n", authInfo.Authenticated, authInfo.Plan, authInfo.Pro)
			}
			chart.RemoveAllStudies()
			chart.Delete()

			// Between retries, run a cleanup cycle: create and delete chart
			// sessions to flush stale study slots from the TradingView server.
			// This is more effective than just waiting because it forces the
			// server to allocate and release chart session resources.
			for cleanupIdx := 0; cleanupIdx < attempt; cleanupIdx++ {
				if client != nil {
					client.Close()
				}
				if err := connectFresh(); err != nil {
					return nil, fmt.Errorf("cleanup reconnect: %w", err)
				}
				cleanupChart := tradingview.NewChartSession(client)
				cleanupChart.SetMarket(req.Symbol, map[string]any{
					"timeframe": pinefacade.NormalizeTimeframe(req.Timeframe),
					"range":     1,
				})
				_ = cleanupChart.WaitForSymbol(5 * time.Second)
				cleanupChart.RemoveAllStudies()
				cleanupChart.Delete()
			}

			time.Sleep(retryDelay)
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
			// Include auth status in the error for better diagnostics.
			if authInfo := client.AuthStatus(); authInfo != nil && !authInfo.Authenticated {
				return nil, fmt.Errorf("study error: %w (root cause: cookies expired — run 'tvcli check-auth')", studyErr)
			}
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
