package cmd

import (
	"fmt"
	"io"
)

// SearchTableItem helpers are shared by list/search/publist/top commands.
// They normalize the raw pinefacade search/list response into a flat slice.

// NormalizeSearchResults extracts the `results` slice from a pinefacade
// response and limits it to `limit` items with normalized fields.
func NormalizeSearchResults(data any, limit int) []map[string]any {
	var results []any

	switch v := data.(type) {
	case map[string]any:
		if r, ok := v["results"].([]any); ok {
			results = r
		}
	case []any:
		results = v
	}

	var items []map[string]any
	for i, raw := range results {
		if i >= limit {
			break
		}
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		normalized := map[string]any{
			"scriptIdPart": item["scriptIdPart"],
			"title":        FirstNonEmptyStr(item, "title", "scriptName"),
			"scriptName":   item["scriptName"],
			"type":         item["type"],
			"access":       item["access"],
			"version":      item["version"],
		}
		if author, ok := item["author"].(map[string]any); ok {
			normalized["author"] = map[string]any{
				"id":       author["id"],
				"username": author["username"],
			}
		}
		items = append(items, normalized)
	}
	return items
}

// FirstNonEmptyStr returns the first non-empty string value for any of keys
// from m, or "" if none match.
func FirstNonEmptyStr(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := m[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

// ExtractNext returns the pagination `next` cursor from a response, or nil.
func ExtractNext(data any) any {
	if m, ok := data.(map[string]any); ok {
		return m["next"]
	}
	return nil
}

// PrintSearchTable writes a human-readable listing of search results to w.
func PrintSearchTable(w io.Writer, items []map[string]any) {
	for i, it := range items {
		author := ""
		if a, ok := it["author"].(map[string]any); ok {
			author, _ = a["username"].(string)
		}
		title, _ := it["title"].(string)
		id, _ := it["scriptIdPart"].(string)
		typ, _ := it["type"].(string)
		access, _ := it["access"].(string)

		fmt.Fprintf(w, "%3d. %s\n", i+1, title)
		fmt.Fprintf(w, "     id: %s\n", id)
		fmt.Fprintf(w, "     author: %s | type: %s | access: %s\n", author, typ, access)
	}
}
