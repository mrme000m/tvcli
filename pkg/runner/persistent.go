package runner

import (
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/ch99q/tvcli/pkg/tradingview"
)

// PersistentRunner holds a long-lived WebSocket connection to TradingView
// and reuses it across multiple indicator runs, avoiding the overhead of
// reconnecting and re-authenticating each time.
type PersistentRunner struct {
	opts   []tradingview.ClientOption
	client *tradingview.Client
	mu     sync.Mutex

	// Callbacks
	onConnected    func()
	onDisconnected func()
	onError        func(error)
	debug          bool
}

// NewPersistentRunner creates a runner that will maintain a single WS connection.
func NewPersistentRunner(opts []tradingview.ClientOption, debug bool) *PersistentRunner {
	pr := &PersistentRunner{
		opts:  opts,
		debug: debug,
	}
	return pr
}

func (pr *PersistentRunner) OnConnected(fn func())    { pr.onConnected = fn }
func (pr *PersistentRunner) OnDisconnected(fn func()) { pr.onDisconnected = fn }
func (pr *PersistentRunner) OnError(fn func(error))   { pr.onError = fn }

// connect establishes the WS connection (or reconnects after a drop).
func (pr *PersistentRunner) connect() error {
	pr.mu.Lock()
	defer pr.mu.Unlock()

	if pr.client != nil {
		pr.client.Close()
		pr.client = nil
	}

	pr.client = tradingview.NewClient(pr.opts...)

	// Wire reconnect callbacks
	pr.client.OnDisconnected(func() {
		if pr.debug {
			log.Printf("[persistent] ws disconnected")
		}
		if pr.onDisconnected != nil {
			pr.onDisconnected()
		}
	})

	if err := pr.client.Connect(); err != nil {
		return fmt.Errorf("ws connect: %w", err)
	}
	if !pr.client.WaitForConnected(10 * time.Second) {
		return fmt.Errorf("ws timeout waiting for connected")
	}

	if pr.onConnected != nil {
		pr.onConnected()
	}
	return nil
}

// EnsureConnected makes sure the WS connection is alive, reconnecting if needed.
func (pr *PersistentRunner) EnsureConnected() error {
	pr.mu.Lock()
	c := pr.client
	pr.mu.Unlock()

	if c != nil && c.IsConnected() {
		return nil
	}

	if pr.debug {
		log.Printf("[persistent] connection lost, reconnecting...")
	}
	return pr.connect()
}

// Client returns the underlying WS client.
func (pr *PersistentRunner) Client() *tradingview.Client {
	pr.mu.Lock()
	defer pr.mu.Unlock()
	return pr.client
}

// Close shuts down the persistent connection. Call when done with all runs.
func (pr *PersistentRunner) Close() {
	pr.mu.Lock()
	defer pr.mu.Unlock()
	if pr.client != nil {
		pr.client.Close()
		pr.client = nil
	}
}

// Run executes an indicator run using the persistent connection.
// It creates a fresh chart session, loads the symbol, attaches the study,
// waits for data, then tears down the chart session — but keeps the WS alive.
func (pr *PersistentRunner) Run(opts RunOnceOptions) (*RunResult, error) {
	if err := pr.EnsureConnected(); err != nil {
		return nil, err
	}

	client := pr.Client()

	// Create chart session
	ch := tradingview.NewChartSession(client)
	if opts.Debug {
		log.Printf("[persistent] created chart session %s", ch.GetSessionID())
	}

	// Load symbol
	ch.SetMarket(opts.Symbol, map[string]any{
		"timeframe": opts.Timeframe,
		"range":     opts.Bars,
	})
	if err := ch.WaitForSymbol(15 * time.Second); err != nil {
		ch.Delete()
		return nil, fmt.Errorf("symbol load: %w", err)
	}

	// Attach study
	study := ch.Study(opts.Indicator)

	done := make(chan struct{})
	var studyErr error
	once := sync.Once{}

	var periods []map[string]any
	var graphicData map[string]map[string]any
	var stratReport map[string]any

	settleMs := opts.SettleMs
	if settleMs <= 0 {
		settleMs = 1500
	}

	study.OnUpdate(func() {
		once.Do(func() {
			p := study.Periods()
			if len(p) > 0 {
				periods = p
				graphicData = study.Graphic()
				stratReport = study.StrategyReport()
				go func() {
					timer := time.NewTimer(time.Duration(settleMs) * time.Millisecond)
					defer timer.Stop()
					select {
					case <-done:
					case <-timer.C:
						close(done)
					}
				}()
			}
		})
	})
	study.OnError(func(err error) {
		once.Do(func() {
			studyErr = err
			close(done)
		})
	})

	calcTimeout := opts.CalcTimeout
	if calcTimeout == 0 {
		calcTimeout = 60 * time.Second
	}

	select {
	case <-done:
	case <-time.After(calcTimeout):
		studyErr = fmt.Errorf("timeout after %s waiting for study data", calcTimeout)
	}

	// Final snapshot
	periods = study.Periods()
	graphicData = study.Graphic()
	stratReport = study.StrategyReport()

	// Clean up chart session (but keep WS alive)
	study.Remove()
	ch.Delete()

	if studyErr != nil {
		return nil, studyErr
	}
	if len(periods) == 0 {
		return nil, fmt.Errorf("no data received from study")
	}

	result := ParseOutput(periods, graphicData, stratReport, opts.Timeframe, opts.PineID, opts.Indicator.Schema)
	return result, nil
}

// RunOnceOptions configures a single run on the persistent connection.
type RunOnceOptions struct {
	PineID     string
	Symbol     string
	Timeframe  string
	Bars       int
	Indicator  *tradingview.PineIndicator
	SettleMs   int
	CalcTimeout time.Duration
	Debug      bool
}
