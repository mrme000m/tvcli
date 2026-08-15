package pinefacade

import (
	"io"
	"net/http"
	"strings"
	"testing"
)

// stubTransport serves a fixed JSON body for any request, letting us exercise
// GetScriptAccess without hitting the live TradingView endpoints.
func stubTransport(body string) http.RoundTripper {
	return roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: 200,
			Body:       io.NopCloser(strings.NewReader(body)),
			Header:     make(http.Header),
		}, nil
	})
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestPineIDPrefixAndAccess(t *testing.T) {
	cases := []struct {
		id      string
		prefix  string
		idPart  string
		public  bool
		access  string
	}{
		{"PUB;abc123", "PUB", "abc123", true, "public"},
		{"PRIVATE;xyz", "PRIVATE", "xyz", false, "invite-only"},
		{"STD;abc", "STD", "abc", false, "invite-only"},
		{"USER;mine", "USER", "mine", false, "private"},
		{"abc123", "", "abc123", true, "public"}, // no namespace -> assume public
	}
	for _, c := range cases {
		if got := PineIDPrefix(c.id); got != c.prefix {
			t.Errorf("PineIDPrefix(%q) = %q, want %q", c.id, got, c.prefix)
		}
		if got := ScriptIDPart(c.id); got != c.idPart {
			t.Errorf("ScriptIDPart(%q) = %q, want %q", c.id, got, c.idPart)
		}
		if got := IsPublicPineID(c.id); got != c.public {
			t.Errorf("IsPublicPineID(%q) = %v, want %v", c.id, got, c.public)
		}
		if got := AccessFromPineID(c.id); got != c.access {
			t.Errorf("AccessFromPineID(%q) = %q, want %q", c.id, got, c.access)
		}
	}
}

func TestGetScriptAccessFromSearch(t *testing.T) {
	body := `{"results":[{"scriptIdPart":"abc123","title":"My MTF","type":"study","access":1}]}`
	c := NewClient("http://unused", "tester", 0)
	// Route every request (including the hardcoded tradingview host) to the
	// stubbed response, regardless of the requested URL.
	c.httpClient = &http.Client{Transport: stubTransport(body)}

	sa, err := c.GetScriptAccess("PUB;abc123", "")
	if err != nil {
		t.Fatalf("GetScriptAccess error: %v", err)
	}
	if sa.Access != "public" {
		t.Errorf("access = %q, want public", sa.Access)
	}
	if sa.Type != "study" {
		t.Errorf("type = %q, want study", sa.Type)
	}
	if sa.Source != "search" {
		t.Errorf("source = %q, want search", sa.Source)
	}
}

func TestGetScriptAccessInviteOnlyNotFound(t *testing.T) {
	c := NewClient("http://unused", "tester", 0)
	c.httpClient = &http.Client{Transport: stubTransport(`{"results":[]}`)}

	// PRIVATE namespace + absent from public search => invite-only.
	sa, err := c.GetScriptAccess("PRIVATE;secret", "")
	if err != nil {
		t.Fatalf("GetScriptAccess error: %v", err)
	}
	if sa.Access != "invite-only" {
		t.Errorf("access = %q, want invite-only", sa.Access)
	}
	if sa.Source != "search" {
		t.Errorf("source = %q, want search", sa.Source)
	}
}
