package tradingview

import (
	"testing"

	"github.com/gorilla/websocket"
)

func TestApplyProxySocks5(t *testing.T) {
	d := &websocket.Dialer{}
	applyProxy(d, "socks5://127.0.0.1:1080")
	if d.NetDial == nil {
		t.Fatal("socks5 proxy should set NetDial")
	}
	if d.Proxy != nil {
		t.Fatal("socks5 proxy should not set Dialer.Proxy")
	}
}

func TestApplyProxyHTTP(t *testing.T) {
	d := &websocket.Dialer{}
	applyProxy(d, "http://proxy.local:8080")
	if d.Proxy == nil {
		t.Fatal("http proxy should set Dialer.Proxy")
	}
	if d.NetDial != nil {
		t.Fatal("http proxy should not set NetDial")
	}
}

func TestApplyProxyEmptyAndInvalid(t *testing.T) {
	d := &websocket.Dialer{}
	applyProxy(d, "")
	if d.NetDial != nil || d.Proxy != nil {
		t.Fatal("empty proxy must leave dialer untouched")
	}
	applyProxy(d, "not a url ::")
	if d.NetDial != nil || d.Proxy != nil {
		t.Fatal("invalid proxy must leave dialer untouched")
	}
}