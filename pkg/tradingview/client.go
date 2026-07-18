package tradingview

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"regexp"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type Client struct {
	conn       *websocket.Conn
	server     string
	token      string
	signature  string
	location   string
	loggedIn   bool
	connected  bool
	mu         sync.Mutex
	sessions   map[string]*sessionEntry
	sendQueue  [][]byte
	debug      bool
	onConnected    []func()
	onDisconnected []func()
	onError        []func(error)
}

type sessionEntry struct {
	typ    string
	onData func(map[string]any)
}

func NewClient(opts ...ClientOption) *Client {
	c := &Client{
		server:   "data",
		location: "https://www.tradingview.com/",
		sessions: make(map[string]*sessionEntry),
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

type ClientOption func(*Client)

func WithServer(s string) ClientOption   { return func(c *Client) { c.server = s } }
func WithToken(t string) ClientOption    { return func(c *Client) { c.token = t } }
func WithSignature(s string) ClientOption { return func(c *Client) { c.signature = s } }
func WithLocation(l string) ClientOption { return func(c *Client) { c.location = l } }
func WithDebug(d bool) ClientOption      { return func(c *Client) { c.debug = d } }

func (c *Client) OnConnected(fn func())    { c.onConnected = append(c.onConnected, fn) }
func (c *Client) OnDisconnected(fn func()) { c.onDisconnected = append(c.onDisconnected, fn) }
func (c *Client) OnError(fn func(error))   { c.onError = append(c.onError, fn) }

func (c *Client) Connect() error {
	uri := fmt.Sprintf("wss://%s.tradingview.com/socket.io/websocket?from=chart&type=chart", c.server)

	headers := http.Header{}
	headers.Set("Origin", c.location)
	headers.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
	headers.Set("Accept-Encoding", "gzip, deflate, br")
	headers.Set("Accept-Language", "en,en-US;q=0.9")
	headers.Set("Cache-Control", "no-cache")
	headers.Set("Pragma", "no-cache")

	if c.token != "" {
		cookie := genAuthCookies(c.token, c.signature)
		if cookie != "" {
			headers.Set("Cookie", cookie)
		}
	}

	dialer := websocket.Dialer{
		HandshakeTimeout:  10 * time.Second,
		EnableCompression: true,
	}

	conn, _, err := dialer.Dial(uri, headers)
	if err != nil {
		return fmt.Errorf("ws connect: %w", err)
	}

	c.conn = conn
	c.connected = true

	// Fetch auth token from TradingView page when cookies are present
	authToken := "unauthorized_user_token"
	if c.token != "" {
		if token, err := fetchAuthToken(c.token, c.signature, c.location); err == nil && token != "" {
			authToken = token
			if c.debug {
				log.Printf("[DEBUG] fetched auth_token: %s...", token[:min(20, len(token))])
			}
		} else if c.debug {
			log.Printf("[DEBUG] auth_token fetch failed: %v, using unauthorized", err)
		}
	}

	// Send auth token
	c.sendRaw(Protocol{}.FormatWSPacket(map[string]any{
		"m": "set_auth_token",
		"p": []any{authToken},
	}))

	c.loggedIn = true
	for _, fn := range c.onConnected {
		fn()
	}

	go c.readLoop()
	return nil
}

func (c *Client) readLoop() {
	defer func() {
		c.connected = false
		c.loggedIn = false
		for _, fn := range c.onDisconnected {
			fn()
		}
	}()

	for {
		_, message, err := c.conn.ReadMessage()
		if err != nil {
			if c.debug {
				log.Printf("[DEBUG] read error: %v", err)
			}
			return
		}

		packets := Protocol{}.ParseWSPacket(string(message))
		for _, pkt := range packets {
			switch v := pkt.(type) {
			case float64:
				// Ping — respond
				if c.debug {
					log.Printf("[WS RECV] ping %d", int(v))
				}
				c.sendRaw(Protocol{}.FormatWSPacket(fmt.Sprintf("~h~%d", int(v))))
			case map[string]any:
				m, _ := v["m"].(string)
				if c.debug {
					log.Printf("[WS RECV] %s", m)
				}
				// Check for session_id handshake
				if _, ok := v["session_id"]; ok {
					continue
				}

				p, _ := v["p"].([]any)

				if m == "protocol_error" {
					for _, fn := range c.onError {
						fn(fmt.Errorf("protocol error: %v", p))
					}
					continue
				}

				if len(p) > 0 {
					sessionID, _ := p[0].(string)
					if session, ok := c.sessions[sessionID]; ok {
						session.onData(map[string]any{
							"type": m,
							"data": p,
						})
					}
				}
			}
		}
	}
}

func (c *Client) Send(msgType string, params []any) {
	pkt := Protocol{}.FormatWSPacket(map[string]any{
		"m": msgType,
		"p": params,
	})
	if c.debug {
		b, _ := json.Marshal(map[string]any{"m": msgType, "p": params})
		log.Printf("[WS SEND] %s payload=%s", msgType, string(b))
	}
	c.sendRaw(pkt)
}

func truncateStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func (c *Client) sendRaw(data string) {
	if c.conn == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.conn.WriteMessage(websocket.TextMessage, []byte(data))
}

func (c *Client) Close() {
	// Send delete messages for all active sessions before closing,
	// so TradingView's server can release indicator slots.
	// Matches JS tv-optimized end() behavior.
	for sessionID, entry := range c.sessions {
		switch entry.typ {
		case "chart":
			c.Send("chart_delete_session", []any{sessionID})
		case "replay":
			c.Send("replay_delete_session", []any{sessionID})
		case "quote":
			c.Send("quote_delete_session", []any{sessionID})
		}
	}

	// Allow delete messages to flush before closing socket
	time.Sleep(50 * time.Millisecond)

	c.sessions = make(map[string]*sessionEntry)

	if c.conn != nil {
		c.conn.Close()
	}
	c.connected = false
	c.loggedIn = false
}

// IsConnected returns true if the WebSocket connection is alive and authenticated.
func (c *Client) IsConnected() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.connected && c.loggedIn
}

func (c *Client) WaitForConnected(timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if c.connected && c.loggedIn {
			return true
		}
		time.Sleep(50 * time.Millisecond)
	}
	return false
}

func genSessionID(prefix string) string {
	const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
	b := make([]byte, 12)
	for i := range b {
		b[i] = chars[rand.Intn(len(chars))]
	}
	return prefix + "_" + string(b)
}

func genAuthCookies(session, signature string) string {
	if session == "" {
		return ""
	}
	cookie := "sessionid=" + session
	if signature != "" {
		cookie += ";sessionid_sign=" + signature
	}
	return cookie
}

// fetchAuthToken scrapes auth_token from TradingView's page using session cookies.
// This is equivalent to the JS getUser() function.
func fetchAuthToken(session, signature, location string) (string, error) {
	if location == "" {
		location = "https://www.tradingview.com/"
	}

	cookie := genAuthCookies(session, signature)
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

	if !contains(page, "auth_token") {
		return "", fmt.Errorf("no auth_token in page (status=%d)", resp.StatusCode)
	}

	// Extract auth_token from JSON embedded in page
	re := regexp.MustCompile(`"auth_token":"([^"]+)"`)
	matches := re.FindStringSubmatch(page)
	if len(matches) > 1 {
		return matches[1], nil
	}

	return "", fmt.Errorf("auth_token regex match failed")
}

func contains(s, substr string) bool {
	return len(s) > 0 && len(substr) > 0 && (len(s) >= len(substr)) && findSubstring(s, substr)
}

func findSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
