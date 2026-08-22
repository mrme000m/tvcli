// Package auth handles TradingView HTTP authentication concerns: building
// the session cookie header and scraping the auth_token from a logged-in
// page. Extracted from pkg/tradingview/client.go so the WS transport no
// longer carries HTTP scraping code.
package auth

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

// Option configures an auth fetch. Options are variadic so existing callers
// keep working unchanged.
type Option func(*fetchOpts)

type fetchOpts struct {
	proxyURL string
}

// WithProxy routes the page fetch through the given proxy
// (e.g. "socks5://127.0.0.1:1080" or "http://proxy:8080"). Empty disables.
func WithProxy(proxyURL string) Option {
	return func(o *fetchOpts) { o.proxyURL = proxyURL }
}

// httpClient builds the client used for the page fetch, honoring the proxy
// option. net/http's Transport natively supports socks5://, socks5h://,
// http://, and https:// proxy URLs via the Proxy function.
func httpClient(opts ...Option) *http.Client {
	o := &fetchOpts{}
	for _, fn := range opts {
		fn(o)
	}
	if o.proxyURL == "" {
		return http.DefaultClient
	}
	if u, err := url.Parse(o.proxyURL); err == nil && u.Scheme != "" {
		return &http.Client{Transport: &http.Transport{Proxy: http.ProxyURL(u)}}
	}
	return http.DefaultClient
}

// GenCookies builds the Cookie header value for TradingView API requests
// from a session id, (optional) signature, and (optional) device_t token.
// The device_t cookie is required by TradingView for proper authentication;
// without it the auth_token fetch fails and the WS client falls back to an
// unauthorized token, which triggers strict study limits on free accounts.
// Returns "" if session is empty.
func GenCookies(session, signature, deviceT string) string {
	if session == "" {
		return ""
	}
	cookie := "sessionid=" + session
	if signature != "" {
		cookie += ";sessionid_sign=" + signature
	}
	if deviceT != "" {
		cookie += ";device_t=" + deviceT
	}
	return cookie
}

var authTokenRe = regexp.MustCompile(`"auth_token":"([^"]+)"`)

// AuthInfo holds the authentication and subscription state scraped from a
// TradingView page. It lets callers distinguish "cookies expired" from
// "study limit reached" — the two most common failure modes for agent
// workflows.
type AuthInfo struct {
	Token         string // The auth_token (empty if not authenticated)
	Authenticated bool   // True if cookies are valid
	Pro           bool   // True if the account is a Pro subscriber
	Plan          string // Detected plan name (e.g. "essential", "pro", "")
	Username      string // Detected username (if available)
	StatusCode    int    // HTTP status code of the page fetch
	Error         error  // Non-nil if the fetch failed entirely
}

// FetchToken scrapes the auth_token from a TradingView page using the session
// cookies. location defaults to https://www.tradingview.com/ if empty.
// The deviceT parameter is the device_t cookie value, required for proper
// authentication on free accounts.
// Returns an error if the page has no auth_token (e.g. cookies expired).
func FetchToken(session, signature, location, deviceT string, opts ...Option) (string, error) {
	info := FetchAuthInfo(session, signature, location, deviceT, opts...)
	return info.Token, info.Error
}

// MyChart is one saved chart layout from /my-charts/.
type MyChart struct {
	ID          int64  `json:"id"`
	Name        string `json:"name"`
	ShortName   string `json:"short_name"`
	Symbol      string `json:"symbol"`
	ShortSymbol string `json:"short_symbol"`
	Resolution  string `json:"resolution"`
	Interval    string `json:"interval"`
	URL         string `json:"url"`
	Created     string `json:"created"`
	Modified    string `json:"modified"`
	Favorite    bool   `json:"favorite"`
}

// FetchMyCharts lists the authenticated user's saved chart layouts from
// https://www.tradingview.com/my-charts/. The web app loads its "Manage
// layouts" dialog with this endpoint (GET /my-charts/?limit=N, XHR); it
// needs the same session cookies as every other authenticated endpoint.
func FetchMyCharts(session, signature, deviceT string, limit int, opts ...Option) ([]MyChart, error) {
	if limit <= 0 {
		limit = 20
	}
	u := fmt.Sprintf("https://www.tradingview.com/my-charts/?limit=%d", limit)

	req, _ := http.NewRequest("GET", u, nil)
	if cookie := GenCookies(session, signature, deviceT); cookie != "" {
		req.Header.Set("Cookie", cookie)
	}
	req.Header.Set("X-Requested-With", "XMLHttpRequest")
	req.Header.Set("Accept", "application/json, text/javascript, */*; q=0.01")
	req.Header.Set("Origin", "https://www.tradingview.com")
	req.Header.Set("Referer", "https://www.tradingview.com/chart/")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

	resp, err := httpClient(opts...).Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetch my-charts: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("my-charts returned status %d", resp.StatusCode)
	}

	var charts []MyChart
	if err := json.Unmarshal(body, &charts); err != nil {
		return nil, fmt.Errorf("parse my-charts: %w", err)
	}
	return charts, nil
}

// FetchAuthInfo scrapes the TradingView chart page to determine the full
// authentication and subscription state. It returns an AuthInfo struct with
// the auth_token (if authenticated), subscription plan, and any error.
//
// The HTML page embeds class flags like "is-not-authenticated" or "is-pro"
// and sometimes a JSON block with the user's plan. This function parses those
// to give callers a complete picture without running a study.
func FetchAuthInfo(session, signature, location, deviceT string, opts ...Option) AuthInfo {
	if location == "" {
		location = "https://www.tradingview.com/chart/"
	}

	cookie := GenCookies(session, signature, deviceT)
	if cookie == "" {
		return AuthInfo{Error: fmt.Errorf("no session cookies")}
	}

	req, _ := http.NewRequest("GET", location, nil)
	req.Header.Set("Cookie", cookie)
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")

	resp, err := httpClient(opts...).Do(req)
	if err != nil {
		return AuthInfo{Error: fmt.Errorf("fetch page: %w", err)}
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	page := string(body)

	info := AuthInfo{StatusCode: resp.StatusCode}

	// Detect authentication state from HTML class flags.
	// The <html> tag includes classes like "is-not-authenticated" or
	// "is-authenticated" and "is-pro" / "is-not-pro".
	if strings.Contains(page, "is-not-authenticated") {
		info.Authenticated = false
	} else if strings.Contains(page, "is-authenticated") {
		info.Authenticated = true
	}
	info.Pro = strings.Contains(page, "is-pro") && !strings.Contains(page, "is-not-pro")

	// Extract auth_token via regex (only present when authenticated).
	if strings.Contains(page, "auth_token") {
		matches := authTokenRe.FindStringSubmatch(page)
		if len(matches) > 1 {
			info.Token = matches[1]
		}
	}

	// Try to extract the plan from embedded JSON.
	// TradingView pages sometimes include "plan":"<name>" or
	// "pro_plan":"<name>" in initData or user data blocks.
	planRe := regexp.MustCompile(`"plan"\s*:\s*"([^"]+)"`)
	if m := planRe.FindStringSubmatch(page); len(m) > 1 {
		info.Plan = m[1]
	} else {
		proPlanRe := regexp.MustCompile(`"pro_plan"\s*:\s*"([^"]+)"`)
		if m := proPlanRe.FindStringSubmatch(page); len(m) > 1 {
			info.Plan = m[1]
		}
	}

	// Extract username if present.
	userRe := regexp.MustCompile(`"username"\s*:\s*"([^"]+)"`)
	if m := userRe.FindStringSubmatch(page); len(m) > 1 {
		info.Username = m[1]
	}

	// Determine error: if not authenticated, the cookies are expired.
	if !info.Authenticated {
		if info.Token == "" {
			info.Error = fmt.Errorf("cookies expired or invalid — not authenticated (status=%d)", resp.StatusCode)
		} else {
			info.Error = fmt.Errorf("auth_token present but page reports not authenticated (status=%d)", resp.StatusCode)
		}
	}

	return info
}
