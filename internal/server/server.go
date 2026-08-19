// Package server provides an HTTP server wrapping tvcli's core functions
// for AI agent integration. It exposes endpoints for compiling Pine scripts,
// fetching OHLCV data, running indicators, and cleaning up chart sessions.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/skill"
	_ "github.com/mrme000m/tvcli/pkg/skill/parsers" // register all skills via init()
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/runner"
	"github.com/mrme000m/tvcli/pkg/schema"
	"github.com/mrme000m/tvcli/pkg/tradingview"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

// Server wraps the tvcli core functions behind an HTTP API.
// It serializes TradingView WS requests through a queue to prevent
// concurrent study-limit collisions and auth races on the free tier.
type Server struct {
	cfg      *config.Config
	pfClient *pinefacade.Client
	mux      *http.ServeMux

	// ── Request queue ───────────────────────────────────────────────
	// TradingView free tier allows ~2 concurrent indicators per chart
	// session, and the WS connection has a strict study limit. When
	// multiple HTTP clients (agentic trader, manual scans, enrichment
	// scripts) hit /run simultaneously, each spawns a fresh WS connection
	// and chart session, causing 503 study-limit and 401 auth errors.
	//
	// The runMu mutex serializes all /run requests so only one study
	// executes at a time. Callers wait in a buffered channel; when the
	// queue is full they get a 429 Too Many Requests immediately.
	runMu      sync.Mutex
	runQueue   chan struct{} // buffered semaphore for queue depth
	queueDepth int           // max queued requests (set in New)
	queueStats queueStats
}

type queueStats struct {
	mu             sync.Mutex
	totalRequests  int64
	totalQueued    int64
	totalExecuted  int64
	totalErrors    int64
	currentWaiting int
	currentActive  bool
	lastRequestAt  time.Time
	lastExecutedAt time.Time
	avgWaitMs      float64
}

// New creates a Server with the given config.
func New(cfg *config.Config) *Server {
	queueDepth := 8 // max queued requests before 429
	s := &Server{
		cfg:        cfg,
		pfClient:   pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond, pinefacade.WithProxy(cfg.ProxyURL)),
		mux:        http.NewServeMux(),
		runQueue:   make(chan struct{}, queueDepth),
		queueDepth: queueDepth,
	}
	s.registerRoutes()
	return s
}

// acquireQueueSlot blocks until a queue slot is available or returns
// false if the queue is full. The caller must call releaseSlot when done.
func (s *Server) acquireQueueSlot() bool {
	select {
	case s.runQueue <- struct{}{}:
		s.queueStats.mu.Lock()
		s.queueStats.totalRequests++
		s.queueStats.lastRequestAt = time.Now()
		s.queueStats.mu.Unlock()
		return true
	default:
		return false
	}
}

// waitForSlot blocks (up to timeout) for a queue slot.
func (s *Server) waitForSlot(timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	requestTime := time.Now()
	select {
	case s.runQueue <- struct{}{}:
		s.queueStats.mu.Lock()
		s.queueStats.totalRequests++
		s.queueStats.lastRequestAt = requestTime
		s.queueStats.mu.Unlock()
		return true
	case <-time.After(time.Until(deadline)):
		return false
	}
}

// releaseSlot frees a queue slot and marks the request as done.
// A 1.5s cooldown is applied after each request to let TradingView
// release server-side study slots before the next queued request starts.
func (s *Server) releaseSlot(success bool) {
	<-s.runQueue
	s.queueStats.mu.Lock()
	if !success {
		s.queueStats.totalErrors++
	}
	s.queueStats.totalExecuted++
	s.queueStats.totalQueued++
	s.queueStats.currentActive = false
	s.queueStats.lastExecutedAt = time.Now()
	s.queueStats.mu.Unlock()
	// Cooldown: let TV release the study slot before the next request.
	time.Sleep(1500 * time.Millisecond)
}

// markActive sets the active flag when a request starts executing.
func (s *Server) markActive() {
	s.queueStats.mu.Lock()
	s.queueStats.currentActive = true
	s.queueStats.mu.Unlock()
}

// Handler returns the HTTP handler.
func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) registerRoutes() {
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/compile", s.handleCompile)
	s.mux.HandleFunc("/fetch", s.handleFetch)
	s.mux.HandleFunc("/clean", s.handleClean)
	s.mux.HandleFunc("/run", s.handleRun)
	s.mux.HandleFunc("/run-skill", s.handleRunSkill)
	s.mux.HandleFunc("/skills", s.handleSkills)
	s.mux.HandleFunc("/check-auth", s.handleCheckAuth)
	s.mux.HandleFunc("/queue-stats", s.handleQueueStats)
}

// --- Types ------------------------------------------------------------------

type compileRequest struct {
	Source string `json:"source"`
}

type compileResponse struct {
	Success       bool   `json:"success"`
	SourceHash    string `json:"sourceHash"`
	ErrorMessage  string `json:"error,omitempty"`
	CompileResult any    `json:"compileResult,omitempty"`
}

type fetchRequest struct {
	Symbol    string `json:"symbol"`
	Timeframe string `json:"timeframe"`
	Bars      int    `json:"bars"`
}

type cleanRequest struct {
	Symbol     string `json:"symbol"`
	Iterations int    `json:"iterations"`
	DelayMs    int    `json:"delayMs"`
}

type runRequest struct {
	Source     string            `json:"source"`
	Symbol     string            `json:"symbol"`
	Timeframe  string            `json:"timeframe"`
	Bars       int               `json:"bars"`
	Inputs     map[string]string `json:"inputs"`
	ForceClean bool              `json:"forceCleanup"`
}

// --- Handlers ---------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	// Include auth status for agent health checks.
	authed := false
	plan := ""
	if s.cfg.HasAuth() {
		info := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken, auth.WithProxy(s.cfg.ProxyURL))
		authed = info.Authenticated
		plan = info.Plan
	}
	limits := config.GetTierLimits()
	s.queueStats.mu.Lock()
	queued := len(s.runQueue)
	s.queueStats.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"status":          "ok",
		"tier":            os.Getenv("TV_TIER"),
		"authenticated":   authed,
		"plan":            plan,
		"user":            s.cfg.UserName,
		"endpoint":        s.cfg.PineFacadeURL,
		"maxIndicators":   limits.MaxIndicators,
		"maxBars":         limits.MaxBars,
		"calcTimeoutSecs": limits.CalcTimeoutSecs,
		"queueDepth":      queued,
		"queueMax":        s.queueDepth,
		"queueActive":     s.queueStats.currentActive,
	})
}

func (s *Server) handleCompile(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req compileRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Source == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "source is required"})
		return
	}

	resp, err := s.pfClient.Compile(req.Source, s.cfg.CookieHeaderOrEmpty())
	if err != nil {
		writeJSON(w, http.StatusOK, compileResponse{
			Success:      false,
			SourceHash:   pinefacade.SHA256(req.Source),
			ErrorMessage: err.Error(),
		})
		return
	}

	success := true
	if m, ok := resp.(map[string]any); ok {
		if sv, ok := m["success"]; ok && sv == false {
			success = false
		}
	}
	writeJSON(w, http.StatusOK, compileResponse{
		Success:       success,
		SourceHash:    pinefacade.SHA256(req.Source),
		CompileResult: resp,
	})
}

func (s *Server) handleFetch(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req fetchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Symbol == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "symbol is required"})
		return
	}
	if req.Timeframe == "" {
		req.Timeframe = "5m"
	}
	if req.Bars == 0 {
		req.Bars = 500
	}

	symbol, err := pinefacade.ValidateSymbol(req.Symbol)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": fmt.Sprintf("invalid symbol: %v", err)})
		return
	}

	// Apply tier bar capping.
	limits := config.GetTierLimits()
	if limits.MaxBars > 0 && req.Bars > limits.MaxBars {
		req.Bars = limits.MaxBars
	}

	// ── Queue: serialize WS requests (shared with /run) ──────────────
	if !s.waitForSlot(120 * time.Second) {
		writeJSON(w, http.StatusTooManyRequests, map[string]any{
			"error":      "queue full — too many concurrent requests",
			"retryAfter": 5,
		})
		return
	}
	defer s.releaseSlot(true)
	s.runMu.Lock()
	defer s.runMu.Unlock()
	s.markActive()

	client := tradingview.NewClient(
		tradingview.WithToken(s.cfg.SessionID),
		tradingview.WithSignature(s.cfg.Signature),
		tradingview.WithDeviceToken(s.cfg.DeviceToken),
		tradingview.WithProxy(s.cfg.ProxyURL),
		tradingview.WithDebug(s.cfg.Debug),
	)
	if err := client.Connect(); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "ws connect: " + err.Error()})
		return
	}
	defer client.Close()
	if !client.WaitForConnected(10 * time.Second) {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "ws timeout"})
		return
	}

	chart := tradingview.NewChartSession(client)

	// Wait for the first data update before reading periods, matching
	// the service.FetchOHLCVBarsWithClient pattern. Without this, the
	// server's /fetch endpoint races the data arrival and returns 0 bars.
	done := make(chan struct{})
	once := sync.Once{}
	chart.OnUpdate(func() {
		once.Do(func() { close(done) })
	})

	chart.SetMarket(symbol, map[string]any{
		"timeframe": pinefacade.NormalizeTimeframe(req.Timeframe),
		"range":     req.Bars,
	})
	if err := chart.WaitForSymbol(15 * time.Second); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "symbol load: " + err.Error()})
		return
	}

	select {
	case <-done:
	case <-time.After(30 * time.Second):
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "timeout waiting for OHLCV data"})
		return
	}
	// Brief settle to allow follow-up data to arrive.
	time.Sleep(500 * time.Millisecond)

	periods := chart.Periods()
	chart.RemoveAllStudies()
	chart.Delete()

	writeJSON(w, http.StatusOK, map[string]any{
		"symbol":    symbol,
		"timeframe": req.Timeframe,
		"bars":      len(periods),
		"periods":   periods,
	})
}

func (s *Server) handleClean(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req cleanRequest
	_ = json.NewDecoder(r.Body).Decode(&req)
	if req.Iterations == 0 {
		req.Iterations = 5
	}
	if req.DelayMs == 0 {
		req.DelayMs = 300
	}
	if req.Symbol == "" {
		req.Symbol = "OANDA:XAUUSD"
	}

	// Cleanup through the same queue + mutex as runs: a cleanup that raced a
	// live study caused auth collisions and made study-limit errors worse.
	if !s.waitForSlot(120 * time.Second) {
		writeJSON(w, http.StatusTooManyRequests, map[string]any{
			"error": "queue full — cleanup deferred", "retryAfter": 5,
		})
		return
	}
	defer s.releaseSlot(true)
	s.runMu.Lock()
	defer s.runMu.Unlock()
	s.markActive()

	client := tradingview.NewClient(
		tradingview.WithToken(s.cfg.SessionID),
		tradingview.WithSignature(s.cfg.Signature),
		tradingview.WithDeviceToken(s.cfg.DeviceToken),
		tradingview.WithProxy(s.cfg.ProxyURL),
		tradingview.WithDebug(s.cfg.Debug),
	)
	if err := client.Connect(); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "ws connect: " + err.Error()})
		return
	}
	defer client.Close()
	if !client.WaitForConnected(10 * time.Second) {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "ws timeout"})
		return
	}

	sessionsCleaned := 0
	for i := 0; i < req.Iterations; i++ {
		chart := tradingview.NewChartSession(client)
		chart.SetMarket(req.Symbol, map[string]any{"timeframe": "1", "range": 1})
		_ = chart.WaitForSymbol(5 * time.Second)
		existing := chart.GetStudies()
		if len(existing) > 0 {
			chart.RemoveAllStudies()
		}
		chart.Delete()
		sessionsCleaned++
		time.Sleep(time.Duration(req.DelayMs) * time.Millisecond)
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"success":  true,
		"sessions": sessionsCleaned,
		"message":  fmt.Sprintf("Cleaned %d chart sessions", sessionsCleaned),
	})
}

func (s *Server) handleRun(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req runRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Source == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "source is required"})
		return
	}

	// ── Queue: serialize TradingView WS requests ──────────────────────
	// Only one study runs at a time. Callers wait up to 120s for a slot.
	// If the queue is full, return 429 immediately.
	if !s.waitForSlot(120 * time.Second) {
		s.queueStats.mu.Lock()
		queued := len(s.runQueue)
		s.queueStats.mu.Unlock()
		writeJSON(w, http.StatusTooManyRequests, map[string]any{
			"error":      "queue full — too many concurrent requests",
			"queueDepth": queued,
			"maxQueue":   s.queueDepth,
			"retryAfter": 5,
		})
		return
	}
	defer s.releaseSlot(true)

	// Acquire the run mutex — ensures only one WS connection at a time.
	s.runMu.Lock()
	defer s.runMu.Unlock()
	s.markActive()

	// 1. Compile to validate syntax.
	_, err := s.pfClient.Compile(req.Source, s.cfg.CookieHeaderOrEmpty())
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "compile: " + err.Error()})
		return
	}

	// 2. Save as temp script via Pine Facade to get a real Pine ID.
	//    The WS create_study expects compiled IL in the text field, which
	//    pinefacade.Get() returns for a saved Pine ID. Sending raw source
	//    directly does not work.
	tempName := "agent_server_" + pinefacade.SHA256(req.Source)[:12]
	var pineID string
	var metaInfo map[string]any
	var savedScript bool

	saveResp, saveErr := s.pfClient.SaveNew(req.Source, tempName, s.cfg.CookieHeaderOrEmpty())
	if saveErr == nil {
		if pid := extractPineID(saveResp); pid != "" {
			pineID = pid
			savedScript = true
			if fetched, ferr := s.pfClient.Get(pineID, "last", s.cfg.CookieHeaderOrEmpty()); ferr == nil && fetched.MetaInfo != nil {
				metaInfo = fetched.MetaInfo
			}
		}
	}
	if pineID == "" {
		pineID = "USER;eval" + pinefacade.SHA256(req.Source)[:12]
	}

	// 3. Cleanup temp script when done.
	if savedScript {
		defer func() {
			s.pfClient.Delete(pineID, s.cfg.CookieHeaderOrEmpty())
		}()
	}

	// 4. Resolve symbol / timeframe / bars.
	symbol := req.Symbol
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalized, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": fmt.Sprintf("invalid symbol: %v", err)})
		return
	}
	symbol = normalized

	tf := req.Timeframe
	if tf == "" {
		tf = "5m"
	}

	limits := config.GetTierLimits()
	bars := req.Bars
	if bars == 0 {
		bars = 500
	}
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		bars = limits.MaxBars
	}

	// Pre-check auth before running (fail fast on expired cookies).
	if authInfo := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken, auth.WithProxy(s.cfg.ProxyURL)); !authInfo.Authenticated {
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"error":         "auth: cookies expired — re-extract SESSION/SIGNATURE/DEVICE_T",
			"authenticated": false,
			"canRunStudies": false,
		})
		return
	}

	// 5. Run via service.RunScript (fetches compiled IL from Pine Facade).
	res, err := service.RunScript(context.Background(), s.cfg, service.RunRequest{
		PineID:       pineID,
		Symbol:       symbol,
		Timeframe:    tf,
		Bars:         bars,
		Inputs:       req.Inputs,
		ReservedKeys: []string{"source", "symbol", "timeframe", "bars", "inputs", "forceCleanup"},
		SettleMs:     1500,
		ForceCleanup: req.ForceClean,
		CalcTimeout:  time.Duration(limits.CalcTimeoutSecs) * time.Second,
		Debug:        s.cfg.Debug,
	})
	if err != nil {
		s.queueStats.mu.Lock()
		s.queueStats.totalErrors++
		s.queueStats.mu.Unlock()
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"error":   err.Error(),
			"pineId":  pineID,
			"periods": nil,
		})
		return
	}

	// 6. Build schema and parse output.
	var sch *schema.PineSchema
	if metaInfo != nil {
		sch = schema.FromMetaInfo(pineID, metaInfo)
	}

	result := runner.ParseOutput(res.Periods, res.Graphic, res.StrategyReport, tf, pineID, sch)
	// Wrap in a map to add extra fields alongside the structured result.
	writeJSON(w, http.StatusOK, map[string]any{
		"status":    "ok",
		"pineId":    pineID,
		"symbol":    symbol,
		"timeframe": tf,
		"result":    result,
	})
}

// lastPriceMissing returns true when the parser did not produce a price.
func lastPriceMissing(v any) bool {
	if v == nil {
		return true
	}
	switch p := v.(type) {
	case float64:
		return p == 0
	case int:
		return p == 0
	case string:
		return p == "" || p == "0" || p == "0.0"
	}
	return false
}

// isStudyLimitError reports whether an error is a TradingView study-limit
// rejection (account-level indicator/chart-session cap on the free tier).
func isStudyLimitError(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "maximum number of studies") ||
		strings.Contains(msg, "too many") ||
		strings.Contains(msg, "study limit")
}

// roundPrice rounds a price to a reasonable number of decimals.
func roundPrice(f float64) float64 {
	if f == 0 {
		return 0
	}
	// Round to 5 decimal places.
	scale := 1e5
	return math.Round(f*scale) / scale
}

// runSkillRequest is the input to /run-skill — accepts a skill name instead of raw source.
type runSkillRequest struct {
	Skill     string            `json:"skill"`
	Symbol    string            `json:"symbol"`
	Timeframe string            `json:"timeframe"`
	Bars      int               `json:"bars"`
	Inputs    map[string]string `json:"inputs"`
}

// handleRunSkill resolves a skill name to its PineID via the skill registry,
// runs it through the same queue as /run, parses the output with the skill's
// parser, and returns the agent-formatted JSON.
//
// POST /run-skill { "skill": "smc", "symbol": "OANDA:XAUUSD", "timeframe": "1H", "bars": 180 }
// → 200 { ...agent-formatted skill output... }
// → 404 "skill not found: <name>"
// → 429 { "error": "queue full", "retryAfter": 5 }
// → 503 "study limit exceeded"
func (s *Server) handleRunSkill(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req runSkillRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Skill == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "skill is required"})
		return
	}

	// Resolve skill name → *Skill via the global registry.
	sk := skill.Get(req.Skill)
	if sk == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{
			"error": fmt.Sprintf("skill not found: %s (available: use /skills endpoint)", req.Skill),
		})
		return
	}

	// Symbol normalization + defaults.
	symbol := req.Symbol
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalized, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid symbol: " + err.Error()})
		return
	}
	symbol = normalized

	tf := req.Timeframe
	if tf == "" {
		tf = "5m"
	}

	bars := req.Bars
	if bars == 0 {
		bars = 180
	}
	limits := config.GetTierLimits()
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		bars = limits.MaxBars
	}

	// Ownership precheck: a private (USER;) skill script must still exist in
	// the current user's saved list, otherwise it will 401 ("no source")
	// mid-run. Skipped for skills that carry raw Source (no facade fetch).
	if pinefacade.AccessFromPineID(sk.PineID) == "private" && sk.Source == "" {
		if err := service.PreCheckScriptOwnership(s.cfg, sk.PineID); err != nil {
			writeJSON(w, http.StatusNotFound, map[string]any{
				"error":  fmt.Sprintf("skill %s: %s", req.Skill, err.Error()),
				"code":   "script_not_owned",
				"skill":  req.Skill,
				"symbol": symbol,
			})
			return
		}
	}

	// ── Queue: serialize TradingView WS requests (same as /run) ──────────
	if !s.waitForSlot(120 * time.Second) {
		s.queueStats.mu.Lock()
		queued := len(s.runQueue)
		s.queueStats.mu.Unlock()
		writeJSON(w, http.StatusTooManyRequests, map[string]any{
			"error":      "queue full — too many concurrent requests",
			"queueDepth": queued,
			"maxQueue":   s.queueDepth,
			"retryAfter": 5,
		})
		return
	}
	defer s.releaseSlot(true)

	s.runMu.Lock()
	defer s.runMu.Unlock()
	s.markActive()

	start := time.Now()

	// Build inputs from skill defaults + request overrides.
	inputs := make(map[string]string)
	for _, inp := range sk.Inputs {
		if inp.Default != nil {
			inputs[inp.TVInputID] = fmt.Sprintf("%v", inp.Default)
		}
	}
	for k, v := range req.Inputs {
		inputs[k] = v
	}

	// Run the skill via the service layer.
	runReq := service.RunRequest{
		PineID:       sk.PineID,
		Symbol:       symbol,
		Timeframe:    tf,
		Bars:         bars,
		Inputs:       inputs,
		ReservedKeys: nil,
		SettleMs:     1500,
		CalcTimeout:  time.Duration(limits.CalcTimeoutSecs) * time.Second,
		Source:       sk.Source,
	}

	res, err := service.RunScript(context.Background(), s.cfg, runReq)
	if err != nil {
		// Classify failures so the agent can react precisely instead of
		// treating every 503 as a study-limit hit (which forced scans onto
		// the cTrader fallback after any transient timeout).
		msg := err.Error()
		code := "generic"
		switch {
		case isStudyLimitError(err):
			code = "study_limit"
		case strings.Contains(msg, "timeout"):
			code = "timeout"
		case strings.Contains(msg, "auth") || strings.Contains(msg, "cookies"):
			code = "auth"
		case strings.Contains(msg, "symbol"):
			code = "symbol"
		}
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"error":  fmt.Sprintf("skill %s: %s", req.Skill, err.Error()),
			"code":   code,
			"skill":  req.Skill,
			"symbol": symbol,
		})
		return
	}
	duration := time.Since(start)

	// Parse the raw data with the skill's parser.
	var result skill.SkillResult
	if sk.ParseWithSchema != nil && res.Indicator != nil && res.Indicator.Schema != nil {
		result = sk.ParseWithSchema(res.Periods, res.Graphic, res.Indicator.Schema, tf, symbol, nil)
	} else {
		result = sk.ParseOutput(res.Periods, res.Graphic, tf, symbol, nil)
	}
	if result.Status == "" {
		result.Status = "ok"
	}
	if result.Workflow == "" {
		result.Workflow = sk.Name
	}

	// Back-fill missing lastPrice from OHLCV.
	if result.Status == "ok" && lastPriceMissing(result.Market.LastPrice) {
		if ohlcvBars, ferr := service.FetchOHLCVBars(s.cfg, symbol, tf, 2); ferr == nil && len(ohlcvBars) > 0 {
			result.Market.LastPrice = roundPrice(ohlcvBars[len(ohlcvBars)-1].Close)
		}
	}

	// Return agent-formatted JSON (same as CLI --json --agent).
	agentOut := sk.ToAgent(result, symbol, tf, duration.Milliseconds())
	writeJSON(w, http.StatusOK, agentOut)
}

// extractPineID tries to find a pineId in a SaveNew response.
// Matches the cmd.ExtractPineID logic: checks pineId, id, scriptIdPart at
// top level, inside "response", and inside "result.metaInfo.scriptIdPart".
func extractPineID(resp any) string {
	m, ok := resp.(map[string]any)
	if !ok {
		return ""
	}
	// Top-level keys with semicolons.
	for _, key := range []string{"pineId", "id", "scriptIdPart"} {
		if s, ok := m[key].(string); ok && strings.Contains(s, ";") {
			return strings.ReplaceAll(s, "%3B", ";")
		}
	}
	// Nested "response" object.
	if inner, ok := m["response"].(map[string]any); ok {
		for _, key := range []string{"pineId", "id", "scriptIdPart"} {
			if s, ok := inner[key].(string); ok && strings.Contains(s, ";") {
				return strings.ReplaceAll(s, "%3B", ";")
			}
		}
	}
	// Nested "result.metaInfo.scriptIdPart".
	if result, ok := m["result"].(map[string]any); ok {
		if mi, ok := result["metaInfo"].(map[string]any); ok {
			if s, ok := mi["scriptIdPart"].(string); ok && s != "" {
				if strings.Contains(s, ";") {
					return strings.ReplaceAll(s, "%3B", ";")
				}
				return "USER;" + strings.ReplaceAll(s, "%3B", ";")
			}
		}
	}
	return ""
}

// --- Helpers ----------------------------------------------------------------

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// Serve starts the HTTP server on the given address.
func (s *Server) Serve(addr string) error {
	fmt.Fprintf(os.Stderr, "tvcli server listening on %s\n", addr)
	return http.ListenAndServe(addr, s.mux)
}

// ParseAddr extracts the address from the --addr flag, defaulting to :8765.
func ParseAddr(flags map[string]string) string {
	addr := flags["addr"]
	if addr == "" {
		addr = ":8765"
	}
	if !strings.HasPrefix(addr, ":") && !strings.Contains(addr, ":") {
		addr = ":" + addr
	}
	return addr
}

// handleSkills lists the registered skill names (for /run-skill discovery).
// GET /skills → { "skills": ["smc", ...], "count": N }
func (s *Server) handleSkills(w http.ResponseWriter, r *http.Request) {
	names := make([]string, 0)
	for _, sk := range skill.All() {
		if sk != nil && sk.Name != "" {
			names = append(names, sk.Name)
		}
	}
	sort.Strings(names)
	writeJSON(w, http.StatusOK, map[string]any{
		"skills": names,
		"count":  len(names),
	})
}

// handleCheckAuth verifies TradingView auth cookies and subscription tier.
// GET /check-auth → { "configured", "authenticated", "pro", "plan", "canRunStudies", "error" }
func (s *Server) handleCheckAuth(w http.ResponseWriter, r *http.Request) {
	if !s.cfg.HasAuth() {
		writeJSON(w, http.StatusOK, map[string]any{
			"configured":    false,
			"authenticated": false,
			"canRunStudies": false,
			"error":         "no SESSION cookie configured",
		})
		return
	}

	info := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken, auth.WithProxy(s.cfg.ProxyURL))

	result := map[string]any{
		"configured":    true,
		"authenticated": info.Authenticated,
		"pro":           info.Pro,
		"plan":          info.Plan,
		"canRunStudies": info.Authenticated,
	}
	if info.Error != nil {
		result["error"] = info.Error.Error()
	}
	if info.Username != "" {
		result["username"] = info.Username
	}

	status := http.StatusOK
	if !info.Authenticated {
		status = http.StatusUnauthorized
	}
	writeJSON(w, status, result)
}

// handleQueueStats returns the current request queue state.
// GET /queue-stats → { "queued", "maxQueue", "active", "stats" }
func (s *Server) handleQueueStats(w http.ResponseWriter, r *http.Request) {
	s.queueStats.mu.Lock()
	stats := map[string]any{
		"queued":         len(s.runQueue),
		"maxQueue":       s.queueDepth,
		"active":         s.queueStats.currentActive,
		"waiting":        s.queueStats.currentWaiting,
		"totalRequests":  s.queueStats.totalRequests,
		"totalQueued":    s.queueStats.totalQueued,
		"totalExecuted":  s.queueStats.totalExecuted,
		"totalErrors":    s.queueStats.totalErrors,
		"avgWaitMs":      s.queueStats.avgWaitMs,
		"lastRequestAt":  s.queueStats.lastRequestAt,
		"lastExecutedAt": s.queueStats.lastExecutedAt,
	}
	s.queueStats.mu.Unlock()
	writeJSON(w, http.StatusOK, stats)
}
