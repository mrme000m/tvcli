package tradingview

import (
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/ch99q/tvcli/pkg/tradingview/auth"
	"github.com/gorilla/websocket"
)

type Client interface {
	Connect() error
	Close()
	IsConnected() bool
	WaitForConnected(timeout time.Duration) bool
	OnConnected(fn func())
	OnDisconnected(fn func())
	OnError(fn func(error))
	Send(msgType string, params []any)
	Debug() bool

	RegisterSession(id, typ string, onData func(map[string]any))
	UnregisterSession(id string)

	// AuthStatus returns the authentication info from the last Connect() call.
	// Returns nil if Connect() hasn't been called or no session cookies were
	// configured (anonymous connections).
	AuthStatus() *auth.AuthInfo
}

type sessionEntry struct {
	typ    string
	onData func(map[string]any)
}

type WSClient struct {
	conn           *websocket.Conn
	server         string
	token          string
	signature      string
	deviceToken    string
	location       string
	loggedIn       bool
	connected      bool
	mu             sync.Mutex
	sessions       map[string]*sessionEntry
	sendQueue      [][]byte
	debug          bool
	onConnected    []func()
	onDisconnected []func()
	onError        []func(error)
	authInfo       *auth.AuthInfo // auth status from last FetchAuthInfo call
}

func NewClient(opts ...ClientOption) Client {
	c := &WSClient{
		server:   "data",
		location: "https://www.tradingview.com/chart/",
		sessions: make(map[string]*sessionEntry),
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

type ClientOption func(*WSClient)

func WithServer(s string) ClientOption    { return func(c *WSClient) { c.server = s } }
func WithToken(t string) ClientOption     { return func(c *WSClient) { c.token = t } }
func WithSignature(s string) ClientOption { return func(c *WSClient) { c.signature = s } }
func WithDeviceToken(d string) ClientOption { return func(c *WSClient) { c.deviceToken = d } }
func WithLocation(l string) ClientOption  { return func(c *WSClient) { c.location = l } }
func WithDebug(d bool) ClientOption       { return func(c *WSClient) { c.debug = d } }

func (c *WSClient) OnConnected(fn func())    { c.onConnected = append(c.onConnected, fn) }
func (c *WSClient) OnDisconnected(fn func()) { c.onDisconnected = append(c.onDisconnected, fn) }
func (c *WSClient) OnError(fn func(error))   { c.onError = append(c.onError, fn) }

// Debug reports whether the client is in debug logging mode.
func (c *WSClient) Debug() bool { return c.debug }

// AuthStatus returns the authentication info from the last Connect() call.
// Returns nil if Connect() hasn't been called or no session cookies were
// configured (anonymous connections).
func (c *WSClient) AuthStatus() *auth.AuthInfo { return c.authInfo }

func (c *WSClient) RegisterSession(id, typ string, onData func(map[string]any)) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.sessions[id] = &sessionEntry{typ: typ, onData: onData}
}

func (c *WSClient) UnregisterSession(id string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.sessions, id)
}

func (c *WSClient) Connect() error {
	uri := fmt.Sprintf("wss://%s.tradingview.com/socket.io/websocket?from=chart&type=chart", c.server)

	headers := http.Header{}
	// Origin must be the base TradingView URL, not the chart page.
	headers.Set("Origin", "https://www.tradingview.com/")
	headers.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
	headers.Set("Accept-Encoding", "gzip, deflate, br")
	headers.Set("Accept-Language", "en,en-US;q=0.9")
	headers.Set("Cache-Control", "no-cache")
	headers.Set("Pragma", "no-cache")

	if c.token != "" {
		cookie := auth.GenCookies(c.token, c.signature, c.deviceToken)
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

	// Fetch auth token from TradingView page when cookies are present.
	// Always report auth failures to stderr (not just in debug mode) so
	// agents and users can immediately see when cookies are expired — the
	// most common cause of silent "study limit" errors.
	authToken := "unauthorized_user_token"
	if c.token != "" {
		info := auth.FetchAuthInfo(c.token, c.signature, c.location, c.deviceToken)
		c.authInfo = &info
		if info.Error == nil && info.Token != "" {
			authToken = info.Token
			if c.debug {
				log.Printf("[DEBUG] fetched auth_token: %s...", truncateStr(info.Token, 20))
			}
		} else {
			// Auth failed — always warn, not just in debug mode.
			if info.Error != nil {
				fmt.Fprintf(os.Stderr, "⚠ Authentication failed: %v\n", info.Error)
				fmt.Fprintf(os.Stderr, "  The WS connection will use an unauthorized token.\n")
				fmt.Fprintf(os.Stderr, "  TradingView limits unauthorized sessions to 0 studies —\n")
				fmt.Fprintf(os.Stderr, "  every indicator/skill command will fail with 'study limit'.\n")
				fmt.Fprintf(os.Stderr, "  Fix: re-extract SESSION/SIGNATURE/DEVICE_T cookies from your\n")
				fmt.Fprintf(os.Stderr, "  browser (DevTools → Application → Cookies → tradingview.com).\n")
			}
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

func (c *WSClient) readLoop() {
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

func (c *WSClient) Send(msgType string, params []any) {
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

func (c *WSClient) sendRaw(data string) {
	if c.conn == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.conn.WriteMessage(websocket.TextMessage, []byte(data))
}

func (c *WSClient) Close() {
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

	// Allow delete messages to flush before closing socket. The 500ms delay
	// gives TradingView's server time to process session deletions and
	// release study slots before a new connection arrives. This is critical
	// for free/essential tier accounts where the study limit is strict and
	// stale sessions from a closed connection can block new ones.
	time.Sleep(500 * time.Millisecond)

	c.sessions = make(map[string]*sessionEntry)

	if c.conn != nil {
		c.conn.Close()
	}
	c.connected = false
	c.loggedIn = false
}

// IsConnected returns true if the WebSocket connection is alive and authenticated.
func (c *WSClient) IsConnected() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.connected && c.loggedIn
}

func (c *WSClient) WaitForConnected(timeout time.Duration) bool {
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
