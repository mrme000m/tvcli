// layout.go — TradingView chart layout management (save/update) over HTTP.
//
// Endpoints (reverse-engineered live from the chart page, 2026-08-23):
//
//   - List:   GET  https://www.tradingview.com/my-charts/?limit=N   → JSON array
//   - Save:   POST https://www.tradingview.com/api/v1/charts/save/  → {id, image_url, last_modified}
//
// The save endpoint is multipart/form-data and always carries the FULL chart
// state as a gzipped JSON blob (`content`, filename "blob.gz"). `image_url`
// present = UPDATE an existing layout; absent = CREATE a new one. A layout is
// identified two ways: the numeric `id` and the URL slug `image_url` (the
// value in the chart URL, e.g. /chart/EAAWCIJf/).
package auth

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
)

// SaveChartParams is the field set for POST /api/v1/charts/save/. All symbol
// descriptor fields are required; Content is the raw (uncompressed) chart-state
// JSON that TradingView gzips before upload.
type SaveChartParams struct {
	Name           string // layout name
	Description    string // layout description
	Resolution     string // interval, e.g. "15", "60", "1D"
	Symbol         string // full symbol, e.g. "INDEX:BTCUSD", "BINANCE:BTCUSDT"
	SymbolType     string // "index", "crypto", "stock", "forex", ...
	Exchange       string // exchange, e.g. "INDEX", "BINANCE", "NASDAQ"
	ListedExchange string // listed exchange (often == Exchange)
	ShortName      string // short symbol, e.g. "BTCUSD"
	Legs           string // JSON array of legs, e.g. `[{"symbol":"INDEX:BTCUSD","pro_symbol":"INDEX:BTCUSD"}]`
	IsRealtime     bool
	ImageURL       string // present → update; empty → create
	Content        []byte // raw chart-state JSON (gzipped automatically)
}

// SaveChartResult is the response of the save endpoint.
type SaveChartResult struct {
	ID           int64  `json:"id"`
	ImageURL     string `json:"image_url"`
	LastModified int64  `json:"last_modified"`
}

// SaveChart creates or updates a chart layout via POST /api/v1/charts/save/.
func SaveChart(session, signature, deviceT string, p SaveChartParams, opts ...Option) (*SaveChartResult, error) {
	content, err := gzipBytes(p.Content)
	if err != nil {
		return nil, fmt.Errorf("gzip content: %w", err)
	}

	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	if p.ImageURL != "" {
		if err := w.WriteField("image_url", p.ImageURL); err != nil {
			return nil, err
		}
	}
	for _, f := range []struct{ k, v string }{
		{"resolution", p.Resolution},
		{"symbol_type", p.SymbolType},
		{"exchange", p.Exchange},
		{"listed_exchange", p.ListedExchange},
		{"symbol", p.Symbol},
		{"short_name", p.ShortName},
		{"legs", p.Legs},
		{"name", p.Name},
		{"description", p.Description},
	} {
		if err := w.WriteField(f.k, f.v); err != nil {
			return nil, err
		}
	}
	isRealtime := "0"
	if p.IsRealtime {
		isRealtime = "1"
	}
	if err := w.WriteField("is_realtime", isRealtime); err != nil {
		return nil, err
	}
	// The server requires the content part to carry Content-Type: application/gzip
	// (CreateFormFile would send application/octet-stream, which it rejects with
	// "Unknown content type").
	h := make(textproto.MIMEHeader)
	h.Set("Content-Disposition", `form-data; name="content"; filename="blob.gz"`)
	h.Set("Content-Type", "application/gzip")
	fw, err := w.CreatePart(h)
	if err != nil {
		return nil, err
	}
	if _, err := fw.Write(content); err != nil {
		return nil, err
	}
	if err := w.Close(); err != nil {
		return nil, err
	}

	req, err := http.NewRequest("POST", "https://www.tradingview.com/api/v1/charts/save/", &body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())
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
		return nil, fmt.Errorf("save chart: %w", err)
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(resp.Body)
	// 201 = created, 200 = updated.
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		return nil, fmt.Errorf("save chart returned status %d: %s", resp.StatusCode, string(bodyBytes))
	}
	var r SaveChartResult
	if err := json.Unmarshal(bodyBytes, &r); err != nil {
		return nil, fmt.Errorf("parse save chart response: %w", err)
	}
	return &r, nil
}

// DeleteChart deletes one or more chart layouts by their image_url slug via
// POST /api/v1/charts/delete/ with a JSON body {"uid": [...]}. The server
// replies {"status":"ok"} on success.
func DeleteChart(session, signature, deviceT string, uids []string, opts ...Option) error {
	if len(uids) == 0 {
		return fmt.Errorf("no chart slugs to delete")
	}
	body, err := json.Marshal(map[string]any{"uid": uids})
	if err != nil {
		return err
	}

	req, err := http.NewRequest("POST", "https://www.tradingview.com/api/v1/charts/delete/", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
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
		return fmt.Errorf("delete chart: %w", err)
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("delete chart returned status %d: %s", resp.StatusCode, string(bodyBytes))
	}
	return nil
}

// gzipBytes gzip-compresses raw bytes (the chart-state JSON).
func gzipBytes(b []byte) ([]byte, error) {
	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	if _, err := gw.Write(b); err != nil {
		return nil, err
	}
	if err := gw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// LayoutsURL returns the chart URL for a layout's image_url slug.
func LayoutsURL(imageURL string) string {
	return "https://www.tradingview.com/chart/" + url.PathEscape(imageURL) + "/"
}