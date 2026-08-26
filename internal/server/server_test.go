package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/account"
)

// newTestServer builds a Server over a two-account registry without copying any
// credentials into the base config (so /health and /accounts perform no
// TradingView network calls).
func newTestServer() *Server {
	reg := account.NewRegistry()
	reg.Default = "core"
	reg.Accounts["core"] = account.Account{Name: "core", Role: account.RoleCore, Tier: "free", UserName: "alice", SessionID: "s-core", ProxyURL: "socks5://127.0.0.1:1080"}
	reg.Accounts["adhoc-1"] = account.Account{Name: "adhoc-1", Role: account.RoleAdhoc, Tier: "free", UserName: "bob"}
	cfg := &config.Config{
		PineFacadeURL: "http://unused",
		Accounts:      reg,
		ActiveAccount: "core",
		Tier:          "free",
	}
	return New(cfg)
}

func getJSON(t *testing.T, h http.Handler, path string) map[string]any {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("GET %s: Content-Type = %q, want application/json", path, ct)
	}
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("GET %s: non-JSON body %q: %v", path, rec.Body.String(), err)
	}
	return out
}

func TestHealthReportsAccountPool(t *testing.T) {
	s := newTestServer()
	body := getJSON(t, s.Handler(), "/health")

	if got := body["accounts"]; got != float64(2) {
		t.Errorf("accounts = %v, want 2", got)
	}
	if got := body["activeAccount"]; got != "core" {
		t.Errorf("activeAccount = %v, want %q", got, "core")
	}
	// failoverMax defaults to 4 when TV_FAILOVER_MAX is unset.
	if got := body["failoverMax"]; got != float64(4) {
		t.Errorf("failoverMax = %v, want 4", got)
	}
}

func TestAccountsListsMaskedRegistry(t *testing.T) {
	s := newTestServer()
	body := getJSON(t, s.Handler(), "/accounts")

	if got := body["default"]; got != "core" {
		t.Errorf("default = %v, want %q", got, "core")
	}
	if got := body["active"]; got != "core" {
		t.Errorf("active = %v, want %q", got, "core")
	}
	if got := body["count"]; got != float64(2) {
		t.Errorf("count = %v, want 2", got)
	}
	rows, ok := body["accounts"].([]any)
	if !ok || len(rows) != 2 {
		t.Fatalf("accounts rows = %#v, want 2 entries", body["accounts"])
	}
	// Rows are sorted by name ("adhoc-1" < "core"); locate the core row.
	var core map[string]any
	for _, r := range rows {
		row := r.(map[string]any)
		if row["name"] == "core" {
			core = row
		}
	}
	if core == nil {
		t.Fatalf("account rows %#v missing 'core'", rows)
	}
	// Credentials must never be serialized.
	for _, forbidden := range []string{"sessionId", "session", "signature", "deviceToken", "cookies"} {
		if _, present := core[forbidden]; present {
			t.Errorf("account row leaked credential key %q", forbidden)
		}
	}
	// hasAuth/hasProxy flags are booleans.
	if got := core["hasAuth"]; got != true {
		t.Errorf("core hasAuth = %v, want true (sessionId present)", got)
	}
	if got := core["hasProxy"]; got != true {
		t.Errorf("core hasProxy = %v, want true (proxy present)", got)
	}
}

func TestCheckAuthUnknownAccountReturns404(t *testing.T) {
	s := newTestServer()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/check-auth?account=nope", nil)
	s.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("GET /check-auth?account=nope status = %d, want 404", rec.Code)
	}
}

func TestNormalizeHuntSymbolsDedupesAndRejects(t *testing.T) {
	valid, invalid := normalizeHuntSymbols([]string{
		"BINANCE:BTCUSDT",
		"binance:btcusdt",   // same symbol after normalization → dedupe
		" BINANCE:ETHUSDT ", // trimmed → kept once
		"",
		"bad symbol with spaces",
		"OANDA:XAUUSD",
	})
	if len(valid) != 3 {
		t.Fatalf("valid = %#v, want 3 entries (BTC, ETH, XAUUSD)", valid)
	}
	if valid[0] != "BINANCE:BTCUSDT" || valid[1] != "BINANCE:ETHUSDT" || valid[2] != "OANDA:XAUUSD" {
		t.Errorf("valid order/content = %#v, want [BINANCE:BTCUSDT BINANCE:ETHUSDT OANDA:XAUUSD]", valid)
	}
	if _, ok := invalid["bad symbol with spaces"]; !ok {
		t.Errorf("invalid = %#v, want 'bad symbol with spaces' rejected", invalid)
	}
	if len(invalid) != 1 {
		t.Errorf("invalid = %#v, want exactly 1 entry", invalid)
	}
}

func TestNormalizeHuntSymbolsEmpty(t *testing.T) {
	valid, invalid := normalizeHuntSymbols(nil)
	if len(valid) != 0 || len(invalid) != 0 {
		t.Fatalf("nil input: valid=%#v invalid=%#v, want both empty", valid, invalid)
	}
}
