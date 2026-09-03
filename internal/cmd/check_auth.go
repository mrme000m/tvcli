package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/tradingview"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

type checkAuthCmd struct{ app *App }

func (c *checkAuthCmd) Name() string      { return "check-auth" }
func (c *checkAuthCmd) Aliases() []string { return []string{"auth-check", "diagnose"} }
func (c *checkAuthCmd) Synopsis() string  { return "Verify TradingView auth cookies and subscription tier" }

// Run verifies the TradingView session cookies, reports the authentication
// status and detected subscription tier, and optionally tests a WS connection
// to confirm the study limit. This command is the fastest way to diagnose
// why indicator/skill commands fail with "study limit" errors.
func (c *checkAuthCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	jsonOut := flags.Has("json")

	// --all validates every registry account against the cookies-only JSON
	// profile APIs (batch). Username is returned by the API, not the stored
	// UserName field.
	if flags.Has("all") && cfg.Accounts != nil {
		return c.runBatch(env, cfg, jsonOut)
	}

	// Step 1: Check if cookies are configured at all.
	if !cfg.HasAuth() {
		return reportAuthResult(jsonOut, &authResult{
			Configured: false,
			Message:     "No SESSION cookie configured — set it in .env",
		})
	}

	// Step 2: Fetch auth info from the TradingView page.
	fmt.Fprintf(os.Stderr, "Checking TradingView authentication...\n")

	info := auth.FetchAuthInfo(cfg.SessionID, cfg.Signature, "", cfg.DeviceToken, auth.WithProxy(cfg.ProxyURL))

	result := &authResult{
		Configured:   true,
		Authenticated: info.Authenticated,
		Pro:          info.Pro,
		Plan:         info.Plan,
		Username:     info.Username,
		StatusCode:   info.StatusCode,
	}

	if info.Error != nil {
		result.Error = info.Error.Error()
	}

	// If authenticated, also test a WS connection to verify the study limit.
	if info.Authenticated && info.Token != "" {
		fmt.Fprintf(os.Stderr, "  ✓ Authenticated\n")
		if info.Plan != "" {
			fmt.Fprintf(os.Stderr, "  Plan: %s\n", info.Plan)
		}
		if info.Pro {
			fmt.Fprintf(os.Stderr, "  Pro: yes\n")
		}

		// Check the configured tier vs the detected tier.
		tier := config.GetTierLimits()
		fmt.Fprintf(os.Stderr, "  Configured tier: %s (%d indicators/chart, %d bars)\n",
			cfg.TierName(), tier.MaxIndicators, tier.MaxBars)

		// Try a quick WS connection to verify the study limit.
		fmt.Fprintf(os.Stderr, "\n  Testing WS connection...\n")
		client := tradingview.NewClient(
			tradingview.WithToken(cfg.SessionID),
			tradingview.WithSignature(cfg.Signature),
			tradingview.WithDeviceToken(cfg.DeviceToken),
			tradingview.WithProxy(cfg.ProxyURL),
			tradingview.WithDebug(cfg.Debug),
		)
		if err := client.Connect(); err != nil {
			result.WSError = fmt.Sprintf("ws connect: %v", err)
			result.CanRunStudies = false
		} else if !client.WaitForConnected(10 * time.Second) {
			result.WSError = "ws timeout"
			result.CanRunStudies = false
		} else {
			wsAuth := client.AuthStatus()
			if wsAuth != nil {
				result.WSAuthenticated = wsAuth.Authenticated
			}
			result.CanRunStudies = true
			fmt.Fprintf(os.Stderr, "  ✓ WS connection established\n")
			if wsAuth != nil && wsAuth.Authenticated {
				fmt.Fprintf(os.Stderr, "  ✓ Auth token obtained via WS\n")
			} else {
				fmt.Fprintf(os.Stderr, "  ⚠ WS auth token may be unauthorized\n")
				result.CanRunStudies = false
			}
		}
		client.Close()
	} else {
		fmt.Fprintf(os.Stderr, "  ✗ NOT authenticated — cookies are expired or invalid\n")
		if info.Error != nil {
			fmt.Fprintf(os.Stderr, "  Error: %v\n", info.Error)
		}
		fmt.Fprintf(os.Stderr, "\n  Fix:\n")
		fmt.Fprintf(os.Stderr, "    1. Open https://www.tradingview.com/chart/ in your browser\n")
		fmt.Fprintf(os.Stderr, "    2. DevTools → Application → Cookies → https://www.tradingview.com\n")
		fmt.Fprintf(os.Stderr, "    3. Copy these values into .env:\n")
		fmt.Fprintf(os.Stderr, "       SESSION=<sessionid cookie>\n")
		fmt.Fprintf(os.Stderr, "       SIGNATURE=<sessionid_sign cookie>\n")
		fmt.Fprintf(os.Stderr, "       DEVICE_T=<device_t cookie>\n")
		fmt.Fprintf(os.Stderr, "    4. Re-run: ./tvcli check-auth\n")
		result.CanRunStudies = false
	}

	// Include background server state.
	result.ServerRunning = ServerRunning()
	if result.ServerRunning {
		result.ServerHealth = ServerHealth()
	}

	return reportAuthResult(jsonOut, result)
}

type authResult struct {
	Configured      bool            `json:"configured"`
	Authenticated   bool            `json:"authenticated"`
	Pro             bool            `json:"pro"`
	Plan            string          `json:"plan,omitempty"`
	Username        string          `json:"username,omitempty"`
	StatusCode      int             `json:"statusCode,omitempty"`
	Error           string          `json:"error,omitempty"`
	WSAuthenticated bool            `json:"wsAuthenticated,omitempty"`
	CanRunStudies   bool            `json:"canRunStudies"`
	WSError         string          `json:"wsError,omitempty"`
	Message         string          `json:"message,omitempty"`
	ServerRunning   bool            `json:"serverRunning"`
	ServerHealth    map[string]any  `json:"serverHealth,omitempty"`
}

func reportAuthResult(jsonOut bool, r *authResult) error {
	if jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(r)
	}

	fmt.Fprintf(os.Stderr, "\n─── Auth Diagnosis ───\n")
	if !r.Configured {
		fmt.Fprintf(os.Stderr, "Status: NOT CONFIGURED\n")
		fmt.Fprintf(os.Stderr, "Message: %s\n", r.Message)
		return fmt.Errorf("no auth configured")
	}
	if !r.Authenticated {
		fmt.Fprintf(os.Stderr, "Status: NOT AUTHENTICATED (cookies expired)\n")
		if r.Error != "" {
			fmt.Fprintf(os.Stderr, "Error: %s\n", r.Error)
		}
		fmt.Fprintf(os.Stderr, "Can run studies: NO\n")
		return fmt.Errorf("auth: cookies expired")
	}
	fmt.Fprintf(os.Stderr, "Status: AUTHENTICATED\n")
	if r.Plan != "" {
		fmt.Fprintf(os.Stderr, "Plan: %s\n", r.Plan)
	}
	if r.Pro {
		fmt.Fprintf(os.Stderr, "Pro: yes\n")
	}
	fmt.Fprintf(os.Stderr, "Can run studies: %s\n", boolStr(r.CanRunStudies))
	if r.WSError != "" {
		fmt.Fprintf(os.Stderr, "WS Error: %s\n", r.WSError)
	}
	return nil
}

// batchAuthRow is one account's validation result for --all output.
type batchAuthRow struct {
	Name          string `json:"name"`
	Configured    bool   `json:"configured"`
	Authenticated bool   `json:"authenticated"`
	Pro           bool   `json:"pro"`
	Plan          string `json:"plan,omitempty"`
	Username      string `json:"username,omitempty"`
	StatusCode    int    `json:"statusCode,omitempty"`
	Error         string `json:"error,omitempty"`
}

// runBatch validates every registry account and reports the result as either
// a JSON array (--json) or a human table (stderr). Returns an error only on
// output failure; per-account auth failures are reported per-row, not fatal.
func (c *checkAuthCmd) runBatch(env *cli.Env, cfg *config.Config, jsonOut bool) error {
	reg := cfg.Accounts
	names := reg.Names()
	rows := make([]batchAuthRow, 0, len(names))
	for _, name := range names {
		acc := reg.Accounts[name]
		row := batchAuthRow{Name: name, Configured: acc.HasAuth()}
		if !acc.HasAuth() {
			row.Error = "no session cookies"
			rows = append(rows, row)
			continue
		}
		info := auth.FetchAccountStateWithCookies(acc.CookieHeader(), auth.WithProxy(acc.ProxyURL))
		row.Authenticated = info.Authenticated
		row.Pro = info.Pro
		row.Plan = info.Plan
		row.Username = info.Username
		row.StatusCode = info.StatusCode
		if info.Error != nil {
			row.Error = info.Error.Error()
		}
		rows = append(rows, row)
	}

	if jsonOut {
		enc := json.NewEncoder(env.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(map[string]any{"count": len(rows), "results": rows})
	}

	fmt.Fprintf(env.Stderr, "─── Account Pool Validation (%d) ───\n", len(rows))
	for _, r := range rows {
		status := "INVALID"
		if r.Authenticated {
			status = "VALID"
		} else if !r.Configured {
			status = "NO-AUTH"
		}
		line := fmt.Sprintf("  %-20s %-8s plan=%-12s pro=%s user=%-16s",
			r.Name, status, orDash(r.Plan), boolStr(r.Pro), orDash(r.Username))
		if r.Error != "" {
			line += fmt.Sprintf(" err=%s", r.Error)
		}
		fmt.Fprintln(env.Stderr, line)
	}
	return nil
}

func boolStr(b bool) string {
	if b {
		return "YES"
	}
	return "NO"
}
