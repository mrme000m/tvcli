package pinefacade

import (
	"strings"
)

// ScriptAccess classifies a Pine script's visibility / invitation requirement
// and (when a live lookup succeeds) its script type.
type ScriptAccess struct {
	// Access is one of: "public" (runs on any tier, free included),
	// "invite-only" (server-gated, needs an invitation), "private" (an owned
	// account script), or "unknown" (could not be determined).
	Access string
	// Type is the script type reported by the public library search, e.g.
	// "study", "strategy", "indicator". Empty when not looked up.
	Type string
	// Source records how Access was determined: "prefix" (from the Pine ID
	// namespace alone, always available) or "search" (confirmed live against
	// the public script library).
	Source string
}

// PineIDPrefix returns the namespace prefix of a Pine ID — the token before
// the first ';' — uppercased and trimmed. Examples: "PUB", "PRIVATE",
// "STD", "USER", or "" when the ID has no namespace.
func PineIDPrefix(pineID string) string {
	if i := strings.IndexByte(pineID, ';'); i >= 0 {
		return strings.ToUpper(strings.TrimSpace(pineID[:i]))
	}
	return ""
}

// ScriptIDPart returns the trailing identifier of a Pine ID (after the first
// ';'), or the whole string when there is no namespace.
func ScriptIDPart(pineID string) string {
	if i := strings.IndexByte(pineID, ';'); i >= 0 {
		return pineID[i+1:]
	}
	return pineID
}

// IsPublicPineID reports whether the Pine ID belongs to the public library.
// Public scripts (PUB;) run on any TradingView tier, including free; only
// non-public namespaces need an invitation or an owned account. This is the
// cheap, always-available signal used to gate execution.
func IsPublicPineID(pineID string) bool {
	return AccessFromPineID(pineID) == "public"
}

// AccessFromPineID classifies a script from its Pine ID namespace alone. This
// is the offline signal and never negates an unrecognized namespace (it
// returns "public") so a working public script is never wrongly blocked.
func AccessFromPineID(pineID string) string {
	switch PineIDPrefix(pineID) {
	case "PUB":
		return "public"
	case "PRIVATE", "STD", "PRO":
		return "invite-only"
	case "USER":
		return "private"
	default:
		return "public"
	}
}

// GetScriptAccess confirms a script's access/type. It starts from the offline
// Pine ID prefix classification and, when a live search is possible, queries
// the public script library (the same endpoint powering `tv search`) to read
// the script's type and access flag. This is the "make better use of the
// search functions" path: it both identifies the script type and dynamically
// detects scripts that are not publicly runnable.
//
// A non-public script that does not surface in the public library is treated
// as invite-only. PUB scripts that simply don't appear in a search are left
// as public (search indexing is best-effort).
func (c *Client) GetScriptAccess(pineID, cookie string) (ScriptAccess, error) {
	sa := ScriptAccess{Access: AccessFromPineID(pineID), Source: "prefix"}
	idPart := ScriptIDPart(pineID)

	data, err := c.SearchPublicScripts(idPart, cookie)
	if err != nil {
		return sa, err
	}

	items := normalizeSearchItems(data)
	for _, it := range items {
		sid, _ := it["scriptIdPart"].(string)
		title, _ := it["title"].(string)
		if !strings.Contains(sid, idPart) && !strings.Contains(idPart, sid) &&
			!strings.Contains(strings.ToLower(title), strings.ToLower(idPart)) {
			continue
		}
		sa.Source = "search"
		if t, ok := it["type"].(string); ok && t != "" {
			sa.Type = t
		}
		if a, ok := it["access"].(string); ok && a != "" {
			sa.Access = normalizeAccessString(a)
		} else if af, ok := it["access"].(float64); ok {
			sa.Access = normalizeAccessFloat(af)
		}
		return sa, nil
	}

	// Not found in the public library.
	if PineIDPrefix(pineID) != "PUB" {
		sa.Access = "invite-only"
		sa.Source = "search"
	}
	return sa, nil
}

func normalizeAccessString(s string) string {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "public", "open":
		return "public"
	case "protected", "closed", "private":
		return "private"
	default:
		return strings.ToLower(strings.TrimSpace(s))
	}
}

func normalizeAccessFloat(f float64) string {
	switch int(f) {
	case 1:
		return "public" // source visible
	case 2:
		return "private" // protected / closed source
	default:
		return "public"
	}
}

// normalizeSearchItems flattens a pinefacade search/list response into a slice
// of item maps, tolerating both the {results:[...]} envelope and a bare list.
func normalizeSearchItems(data any) []map[string]any {
	var raw []any
	switch v := data.(type) {
	case map[string]any:
		if r, ok := v["results"].([]any); ok {
			raw = r
		}
	case []any:
		raw = v
	}
	items := make([]map[string]any, 0, len(raw))
	for _, x := range raw {
		if m, ok := x.(map[string]any); ok {
			items = append(items, m)
		}
	}
	return items
}
