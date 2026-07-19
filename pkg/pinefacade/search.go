package pinefacade

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
)

// SearchPublicScripts searches TradingView's public script library.
// Hits the pubscripts-suggest-json endpoint (the autocomplete the editor uses).
func (c *Client) SearchPublicScripts(query, cookie string) (any, error) {
	u := fmt.Sprintf("https://www.tradingview.com/pubscripts-suggest-json/?search=%s", url.QueryEscape(query))
	return c.getPublic(u)
}

// ListPublicScripts fetches one page of the public script library.
func (c *Client) ListPublicScripts(offset int) (any, error) {
	u := fmt.Sprintf("https://www.tradingview.com/pubscripts-library/?offset=%d", offset)
	return c.getPublic(u)
}

// getPublic is the shared GET for the two public-listing endpoints above.
// These endpoints don't take the session cookie — they're public — but the
// headers mirror the browser's request to avoid being filtered.
func (c *Client) getPublic(u string) (any, error) {
	req, _ := http.NewRequest("GET", u, nil)
	req.Header.Set("Accept", "application/json, text/javascript, */*; q=0.01")
	req.Header.Set("X-Requested-With", "XMLHttpRequest")
	req.Header.Set("X-Language", "en")
	req.Header.Set("Origin", "https://www.tradingview.com")
	req.Header.Set("Referer", "https://www.tradingview.com/")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	var result any
	json.Unmarshal(body, &result)
	return result, nil
}
