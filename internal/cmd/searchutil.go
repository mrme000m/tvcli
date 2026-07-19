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

		kind := ""
		var stats map[string]any
		if extra, ok := item["extra"].(map[string]any); ok {
			if k, ok := extra["kind"].(string); ok {
				kind = k
			}
			if s, ok := extra["stats"].(map[string]any); ok {
				stats = s
			}
		}

		// access: 1 = source visible, 2 = protected/closed source.
		access := 0
		if v, ok := item["access"].(float64); ok {
			access = int(v)
		}
		source := ""
		if s, ok := item["scriptSource"].(string); ok {
			source = s
		}

		statsCount := 0
		if stats != nil {
			for _, v := range stats {
				if n, ok := v.(float64); ok {
					statsCount += int(n)
				}
			}
		}

		normalized := map[string]any{
			"scriptIdPart": item["scriptIdPart"],
			"title":        FirstNonEmptyStr(item, "title", "scriptName"),
			"scriptName":   item["scriptName"],
			"shortTitle":   item["shortTitle"],
			"kind":         kind,
			"type":         item["type"],
			"access":       access,
			"sourceVisible": access == 1 && source != "",
			"agreeCount":   intOrZero(item["agreeCount"]),
			"isRecommended": item["isRecommended"],
			"weight":       intOrZero(item["weight"]),
			"imageUrl":     item["imageUrl"],
			"version":      item["version"],
			"stats":        stats,
			"qualitySignals": map[string]any{
				"likes":            intOrZero(item["agreeCount"]),
				"weight":           intOrZero(item["weight"]),
				"recommended":      item["isRecommended"],
				"sourceAvailable":  access == 1 && source != "",
				"version":          item["version"],
				"outputCount":      statsCount,
			},
		}
		if source != "" {
			normalized["scriptSource"] = source
		}
		if author, ok := item["author"].(map[string]any); ok {
			normalized["author"] = map[string]any{
				"id":        author["id"],
				"username":  author["username"],
				"is_broker": author["is_broker"],
			}
		}
		items = append(items, normalized)
	}
	return items
}

func intOrZero(v any) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	case int64:
		return int(n)
	}
	return 0
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
		kind, _ := it["kind"].(string)
		access := "protected"
		if a, ok := it["access"].(int); ok && a == 1 {
			access = "public"
		}
		likes := 0
		if n, ok := it["agreeCount"].(int); ok {
			likes = n
		}
		weight := 0
		if n, ok := it["weight"].(int); ok {
			weight = n
		}
		recommended := ""
		if r, ok := it["isRecommended"].(bool); ok && r {
			recommended = " ★ recommended"
		}

		fmt.Fprintf(w, "%3d. %s%s\n", i+1, title, recommended)
		fmt.Fprintf(w, "     id: %s\n", id)
		fmt.Fprintf(w, "     author: %s | kind: %s | access: %s\n", author, kind, access)
		fmt.Fprintf(w, "     quality: likes=%d | weight=%d\n", likes, weight)
	}
}
