// Package auth handles TradingView HTTP authentication concerns: building
// the session cookie header and scraping the auth_token from a logged-in
// page. Extracted from pkg/tradingview/client.go so the WS transport no
// longer carries HTTP scraping code.
package auth

import (
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
)

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

// FetchToken scrapes the auth_token from a TradingView page using the session
// cookies. location defaults to https://www.tradingview.com/ if empty.
// The deviceT parameter is the device_t cookie value, required for proper
// authentication on free accounts.
// Returns an error if the page has no auth_token (e.g. cookies expired).
func FetchToken(session, signature, location, deviceT string) (string, error) {
	if location == "" {
		location = "https://www.tradingview.com/chart/"
	}

	cookie := GenCookies(session, signature, deviceT)
	if cookie == "" {
		return "", fmt.Errorf("no session cookies")
	}

	req, _ := http.NewRequest("GET", location, nil)
	req.Header.Set("Cookie", cookie)
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("fetch page: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	page := string(body)

	if !strings.Contains(page, "auth_token") {
		return "", fmt.Errorf("no auth_token in page (status=%d)", resp.StatusCode)
	}

	matches := authTokenRe.FindStringSubmatch(page)
	if len(matches) > 1 {
		return matches[1], nil
	}

	return "", fmt.Errorf("auth_token regex match failed")
}
