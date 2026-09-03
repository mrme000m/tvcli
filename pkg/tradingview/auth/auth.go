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
	"time"
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
	// Always set a timeout: the page fetch runs on the request path and a
	// stalled connection must not hang the goroutine (and its account slot)
	// forever — especially under multi-account failover where each candidate
	// account does its own fetch.
	client := &http.Client{Timeout: 30 * time.Second}
	if o.proxyURL == "" {
		return client
	}
	if u, err := url.Parse(o.proxyURL); err == nil && u.Scheme != "" {
		client.Transport = &http.Transport{Proxy: http.ProxyURL(u)}
	}
	return client
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
	ID          int64  `json:"id"`        // numeric layout id (DB primary key)
	ImageURL    string `json:"image_url"` // chart slug — the value in the chart URL (/chart/<image_url>/)
	Name        string `json:"name"`
	ShortName   string `json:"short_name"`
	Symbol      string `json:"symbol"`
	ShortSymbol string `json:"short_symbol"`
	Resolution  string `json:"resolution"`
	Interval    string `json:"interval"`
	URL         string `json:"url"` // duplicate of image_url in the API response
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

// FetchAccountState validates session cookies against TradingView's JSON
// profile APIs (no HTML scraping, no auth_token extraction). It is the
// reliable batch-validation path for the multi-account pool: cookies alone
// determine (a) authenticated, (b) plan/tier, and (c) username — the last is
// returned by the API response, not read from any stored field.
//
// Endpoints:
//   - GET /api/v1/user/profile/subscriptions/ → {account_type, is_pro, profile_pro_plan}
//     200 = authenticated; 403 = {"detail":"Login required.","code":"login_required"}.
//   - GET /api/v1/user/profile/me/            → {username, uri}
//     200 = authenticated; 403 = {"code":"not_authenticated"}.
//
// Token stays empty on this path (the WS auth_token lives only on the chart
// HTML page, which FetchAuthInfo scrapes); callers that need a WS token must
// keep using FetchAuthInfo/FetchToken.
func FetchAccountState(session, signature, deviceT string, opts ...Option) AuthInfo {
	return fetchAccountState(GenCookies(session, signature, deviceT), opts...)
}

// FetchAccountStateWithCookies is FetchAccountState but takes a raw Cookie
// header value, honoring the full-Cookies override and ExtraCookies precedence
// (see account.Account.CookieHeader and config.CookieHeaderOrEmpty).
func FetchAccountStateWithCookies(cookie string, opts ...Option) AuthInfo {
	return fetchAccountState(cookie, opts...)
}

func fetchAccountState(cookie string, opts ...Option) AuthInfo {
	if cookie == "" {
		return AuthInfo{Error: fmt.Errorf("no session cookies")}
	}

	status, body, err := doJSON("/api/v1/user/profile/subscriptions/", cookie, opts...)
	if err != nil {
		return AuthInfo{Error: err}
	}
	info := AuthInfo{StatusCode: status}
	if status != http.StatusOK {
		info.Error = authErrorFromBody(status, body)
		return info
	}

	var sub struct {
		AccountType    string `json:"account_type"`
		IsPro          bool   `json:"is_pro"`
		ProfileProPlan string `json:"profile_pro_plan"`
	}
	if err := json.Unmarshal(body, &sub); err != nil {
		// 200 is still a valid authentication signal; only the plan failed to parse.
		info.Authenticated = true
		info.Error = fmt.Errorf("parse subscriptions: %w", err)
		return info
	}
	info.Authenticated = true
	info.Plan = sub.AccountType
	if info.Plan == "" {
		info.Plan = sub.ProfileProPlan
	}
	info.Pro = sub.IsPro

	// Username is best-effort: a failure here must not flip Authenticated.
	if mstatus, mbody, merr := doJSON("/api/v1/user/profile/me/", cookie, opts...); merr == nil && mstatus == http.StatusOK {
		var me struct {
			Username string `json:"username"`
		}
		if json.Unmarshal(mbody, &me) == nil {
			info.Username = me.Username
		}
	}
	return info
}

// doJSON performs a cookies-only GET against a TradingView JSON endpoint with
// the same XHR headers the chart page sends, mirroring layout.go.
func doJSON(path, cookie string, opts ...Option) (int, []byte, error) {
	req, err := http.NewRequest("GET", "https://www.tradingview.com"+path, nil)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Cookie", cookie)
	req.Header.Set("X-Requested-With", "XMLHttpRequest")
	req.Header.Set("Accept", "application/json, text/javascript, */*; q=0.01")
	req.Header.Set("Origin", "https://www.tradingview.com")
	req.Header.Set("Referer", "https://www.tradingview.com/chart/")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

	resp, err := httpClient(opts...).Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("fetch %s: %w", path, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, body, nil
}

// authErrorFromBody turns a non-200 auth response into a concise error using
// the JSON code/detail fields when present (login_required / not_authenticated).
func authErrorFromBody(status int, body []byte) error {
	var e struct {
		Detail string `json:"detail"`
		Code   string `json:"code"`
	}
	_ = json.Unmarshal(body, &e)
	switch {
	case e.Code != "":
		return fmt.Errorf("cookies expired: %s", e.Code)
	case e.Detail != "":
		return fmt.Errorf("cookies expired: %s", e.Detail)
	default:
		return fmt.Errorf("auth fetch status %d", status)
	}
}
