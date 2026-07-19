package pinefacade

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	baseURL    string
	userName   string
	httpClient *http.Client
}

func NewClient(baseURL, userName string, timeout time.Duration) *Client {
	return &Client{
		baseURL:  strings.TrimRight(baseURL, "/"),
		userName: userName,
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

func (c *Client) baseHeaders(cookie string) map[string]string {
	return map[string]string{
		"Cookie":           cookie,
		"Origin":           "https://www.tradingview.com",
		"Referer":          "https://www.tradingview.com/",
		"User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
		"X-Requested-With": "XMLHttpRequest",
	}
}

func (c *Client) Get(pineID, version, cookie string) (*ScriptResult, error) {
	resolved := version
	if resolved == "" || resolved == "-1" {
		if v, err := c.resolveLatestVersion(pineID, cookie); err == nil && v != "" {
			resolved = v
		}
	}
	if resolved == "" {
		resolved = "last"
	}

	// Always use /translate/ for full metaInfo (inputs, plots, styles).
	// The /get/ endpoint only returns raw source without metaInfo,
	// which causes server-side Pine compilation errors.
	return c.fetchTranslate(pineID, resolved, cookie)
}

func (c *Client) fetchTranslate(pineID, version, cookie string) (*ScriptResult, error) {
	encoded := url.PathEscape(strings.ReplaceAll(pineID, "%3B", ";"))
	u := fmt.Sprintf("%s/translate/%s/%s", c.baseURL, encoded, version)

	req, _ := http.NewRequest("GET", u, nil)
	for k, v := range c.baseHeaders(cookie) {
		req.Header.Set(k, v)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetch %s: %w", pineID, err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	return c.parseFetchResponse(body)
}

func (c *Client) tryGetVersion(pineID, version, cookie string) (*ScriptResult, error) {
	encoded := url.PathEscape(strings.ReplaceAll(pineID, "%3B", ";"))
	u := fmt.Sprintf("%s/get/%s/%s", c.baseURL, encoded, version)

	req, _ := http.NewRequest("GET", u, nil)
	for k, v := range c.baseHeaders(cookie) {
		req.Header.Set(k, v)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	result, err := c.parseFetchResponse(body)
	if err != nil || result == nil || result.Source == "" {
		return nil, fmt.Errorf("no source")
	}

	return result, nil
}

func (c *Client) resolveLatestVersion(pineID, cookie string) (string, error) {
	encoded := url.PathEscape(strings.ReplaceAll(pineID, "%3B", ";"))
	u := fmt.Sprintf("%s/versions/%s", c.baseURL, encoded)

	req, _ := http.NewRequest("GET", u, nil)
	for k, v := range c.baseHeaders(cookie) {
		req.Header.Set(k, v)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return "", fmt.Errorf("status %d", resp.StatusCode)
	}

	body, _ := io.ReadAll(resp.Body)
	var raw any
	if err := json.Unmarshal(body, &raw); err != nil {
		return "", err
	}

	entries := normalizeVersionEntries(raw)
	var candidates []string
	for _, e := range entries {
		if v := extractVersion(e); v != "" {
			candidates = append(candidates, v)
		}
	}

	best := ""
	for _, c := range candidates {
		if best == "" || compareVersions(c, best) > 0 {
			best = c
		}
	}
	return best, nil
}

func (c *Client) ListSaved(cookie string) (any, error) {
	u := fmt.Sprintf("%s/list?filter=saved", c.baseURL)
	req, _ := http.NewRequest("GET", u, nil)
	for k, v := range c.baseHeaders(cookie) {
		req.Header.Set(k, v)
	}

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

func (c *Client) Compile(source, cookie string) (any, error) {
	u := fmt.Sprintf("%s/translate_light?user_name=%s&v=3", c.baseURL, url.QueryEscape(c.userName))
	return c.postMultipart(u, source, cookie)
}

func (c *Client) SaveNew(source, name, cookie string) (any, error) {
	if c.userName == "" {
		return nil, fmt.Errorf("save_new requires a user name")
	}
	u := fmt.Sprintf("%s/save/new?name=%s&user_name=%s&allow_overwrite=true",
		c.baseURL, url.QueryEscape(name), url.QueryEscape(c.userName))
	return c.postMultipart(u, source, cookie)
}

func (c *Client) SaveNext(pineID, source, cookie string) (any, error) {
	if c.userName == "" {
		return nil, fmt.Errorf("save_next requires a user name")
	}
	pine := strings.ReplaceAll(pineID, "%3B", ";")
	encoded := url.PathEscape(pine)
	u := fmt.Sprintf("%s/save/next/%s?user_name=%s", c.baseURL, encoded, url.QueryEscape(c.userName))
	return c.postMultipart(u, source, cookie)
}

func (c *Client) Delete(pineID, cookie string) (any, error) {
	if c.userName == "" {
		return nil, fmt.Errorf("delete requires a user name")
	}
	pine := strings.ReplaceAll(pineID, "%3B", ";")
	encoded := url.PathEscape(pine)
	u := fmt.Sprintf("%s/delete/%s?user_name=%s", c.baseURL, encoded, url.QueryEscape(c.userName))

	req, _ := http.NewRequest("POST", u, nil)
	for k, v := range c.baseHeaders(cookie) {
		req.Header.Set(k, v)
	}

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


func (c *Client) postMultipart(u, source, cookie string) (any, error) {
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	part, _ := writer.CreateFormField("source")
	part.Write([]byte(source))
	writer.Close()

	req, _ := http.NewRequest("POST", u, &buf)
	for k, v := range c.baseHeaders(cookie) {
		req.Header.Set(k, v)
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	var result any
	if err := json.Unmarshal(body, &result); err != nil {
		return string(body), nil
	}
	return result, nil
}

func (c *Client) parseFetchResponse(body []byte) (*ScriptResult, error) {
	var raw any
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, err
	}

	switch v := raw.(type) {
	case string:
		return &ScriptResult{Source: v}, nil
	case map[string]any:
		source := extractSource(v)
		meta := &ScriptMeta{}
		var metaInfo map[string]any

		if n, ok := v["scriptName"].(string); ok {
			meta.ScriptName = n
		}
		if n, ok := v["scriptTitle"].(string); ok && meta.ScriptName == "" {
			meta.ScriptName = n
		}
		if ver, ok := v["version"].(string); ok {
			meta.Version = ver
		}
		if res, ok := v["result"].(map[string]any); ok {
			if n, ok := res["scriptName"].(string); ok && meta.ScriptName == "" {
				meta.ScriptName = n
			}
			if mi, ok := res["metaInfo"].(map[string]any); ok {
				if n, ok := mi["scriptIdPart"].(string); ok && meta.ScriptName == "" {
					meta.ScriptName = n
				}
				metaInfo = mi
			}
			// Also extract ilTemplate as script source if no direct source found
			if source == "" {
				if ilTemplate, ok := res["ilTemplate"].(string); ok && ilTemplate != "" {
					source = ilTemplate
				}
			}
		}
		return &ScriptResult{Source: source, Meta: meta, MetaInfo: metaInfo}, nil
	default:
		return nil, fmt.Errorf("unexpected response type")
	}
}

// extractSource tries multiple fields to find Pine source code.
// Priority: source > scriptSource > result.scriptSource > result.IL (base64 decode).
func extractSource(data map[string]any) string {
	// Direct source fields
	for _, key := range []string{"source", "scriptSource"} {
		if s, ok := data[key].(string); ok && s != "" {
			return s
		}
	}

	// Nested in result
	if res, ok := data["result"].(map[string]any); ok {
		if s, ok := res["scriptSource"].(string); ok && s != "" {
			return s
		}
		// Decode base64-encoded IL (TradingView intermediate language)
		if il, ok := res["IL"].(string); ok && il != "" {
			if decoded := decodeIL(il); decoded != "" {
				return decoded
			}
		}
	}

	return ""
}

// decodeIL decodes a base64-encoded TradingView ILScript string.
// Handles URL-safe base64 variants ( -, _ instead of +, / ).
func decodeIL(il string) string {
	// Convert URL-safe base64 to standard base64
	s := strings.ReplaceAll(il, "-", "+")
	s = strings.ReplaceAll(s, "_", "/")

	// Add padding if needed
	if mod := len(s) % 4; mod != 0 {
		s += strings.Repeat("=", 4-mod)
	}

	decoded, err := base64.StdEncoding.DecodeString(s)
	if err != nil {
		return ""
	}
	return string(decoded)
}

