// Package server provides an HTTP server wrapping tvcli's core functions
// for AI agent integration. It exposes endpoints for compiling Pine scripts,
// fetching OHLCV data, running indicators, and cleaning up chart sessions.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/service"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/runner"
	"github.com/ch99q/tvcli/pkg/schema"
	"github.com/ch99q/tvcli/pkg/tradingview"
	"github.com/ch99q/tvcli/pkg/tradingview/auth"
)

// Server wraps the tvcli core functions behind an HTTP API.
type Server struct {
	cfg      *config.Config
	pfClient *pinefacade.Client
	mux      *http.ServeMux
}

// New creates a Server with the given config.
func New(cfg *config.Config) *Server {
	s := &Server{
		cfg:      cfg,
		pfClient: pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond),
		mux:      http.NewServeMux(),
	}
	s.registerRoutes()
	return s
}

// Handler returns the HTTP handler.
func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) registerRoutes() {
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/compile", s.handleCompile)
	s.mux.HandleFunc("/fetch", s.handleFetch)
	s.mux.HandleFunc("/clean", s.handleClean)
	s.mux.HandleFunc("/run", s.handleRun)
	s.mux.HandleFunc("/check-auth", s.handleCheckAuth)
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
		info := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken)
		authed = info.Authenticated
		plan = info.Plan
	}
	limits := config.GetTierLimits()
	writeJSON(w, http.StatusOK, map[string]any{
		"status":         "ok",
		"tier":           os.Getenv("TV_TIER"),
		"authenticated":  authed,
		"plan":           plan,
		"user":           s.cfg.UserName,
		"endpoint":       s.cfg.PineFacadeURL,
		"maxIndicators":  limits.MaxIndicators,
		"maxBars":        limits.MaxBars,
		"calcTimeoutSecs": limits.CalcTimeoutSecs,
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

	client := tradingview.NewClient(
		tradingview.WithToken(s.cfg.SessionID),
		tradingview.WithSignature(s.cfg.Signature),
		tradingview.WithDeviceToken(s.cfg.DeviceToken),
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
		req.Iterations = 3
	}
	if req.DelayMs == 0 {
		req.DelayMs = 500
	}
	if req.Symbol == "" {
		req.Symbol = "BINANCE:BTCUSDT"
	}

	client := tradingview.NewClient(
		tradingview.WithToken(s.cfg.SessionID),
		tradingview.WithSignature(s.cfg.Signature),
		tradingview.WithDeviceToken(s.cfg.DeviceToken),
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
	if authInfo := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken); !authInfo.Authenticated {
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"error":          "auth: cookies expired — re-extract SESSION/SIGNATURE/DEVICE_T",
			"authenticated":  false,
			"canRunStudies":  false,
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

	info := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken)

	result := map[string]any{
		"configured":    true,
		"authenticated": info.Authenticated,
		"pro":           info.Pro,
		"plan":          info.Plan,
		"canRunStudies":  info.Authenticated,
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
