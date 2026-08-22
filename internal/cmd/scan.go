// scan.go — programmatic strategy-vs-indicator search over TradingView public
// scripts.
//
// Purpose: find MORE sample scripts for the two workflows, so sweeps and
// input-change tests are robust:
//
//   - strategies  → feed `tv study set/report` (live-chart parameter sweeps)
//                   or `tv run --signals` (headless backtest + buy/sell signals)
//   - indicators  → feed `tv study set` input-change tests or `tv analyze`
//
// Classification comes from TWO sources:
//
//  1. The search API's `extra.kind` field (TradingView's own classification:
//     "strategy" vs "study"=indicator). Fast, no extra fetches.
//  2. --verify: fetch each script's metaInfo via the pine-facade /translate/
//     and read the authoritative `pine.isStrategy` flag + input/plot counts.
//     Any mismatch between the two is flagged — that robustness is the point
//     (search classification can be stale/mislabeled; the metaInfo is truth).
//
// Verified live: searching "RSI" returns kind=strategy for the RSI Strategy
// and kind=study for indicator scripts; metaInfo pine.isStrategy matches.
package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/schema"
)

type scanCmd struct{ app *App }

func (c *scanCmd) Name() string     { return "scan" }
func (c *scanCmd) Aliases() []string { return []string{"discover", "kind"} }
func (c *scanCmd) Synopsis() string {
	return "Search TradingView public scripts classified as strategy or indicator (feed sweeps/input tests)"
}

// scanItem is one classified script.
type scanItem struct {
	PineID    string `json:"pineId"`
	Name      string `json:"name"`
	Kind      string `json:"kind"` // "strategy" | "indicator" (from search extra.kind)
	Access    int    `json:"access"`
	Likes     int    `json:"likes"`
	Query     string `json:"query"`
	// Verified (only with --verify):
	VerifiedKind string `json:"verifiedKind,omitempty"` // authoritative pine.isStrategy
	Inputs       int    `json:"inputs,omitempty"`
	Plots        int    `json:"plots,omitempty"`
	Mismatch     bool   `json:"mismatch,omitempty"`
	Note         string `json:"note,omitempty"`
}

func (c *scanCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	// Queries: positional args, each may be comma-separated.
	var queries []string
	for _, p := range flags.Positional {
		for _, q := range strings.Split(p, ",") {
			if q = strings.TrimSpace(q); q != "" {
				queries = append(queries, q)
			}
		}
	}
	if len(queries) == 0 {
		return fmt.Errorf("usage: scan <query> [more queries...] [--type strategy|indicator|any] [--limit N] [--verify] [--json]")
	}

	wantType := strings.ToLower(flags.Get("type"))
	if wantType == "" {
		wantType = "any"
	}
	limit := flags.GetInt("limit", 20)
	perQuery := flags.GetInt("per-query", 20)
	verify := flags.Has("verify")
	verifyMax := flags.GetInt("verify-max", 10)
	asJSON := flags.Has("json")

	cfg := c.app.Config
	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))

	var items []scanItem
	seen := map[string]bool{}

	for _, q := range queries {
		data, err := client.SearchPublicScripts(q, "")
		if err != nil {
			fmt.Fprintf(env.Stderr, "⚠ search %q failed: %v\n", q, err)
			continue
		}
		results := NormalizeSearchResults(data, perQuery)
		for _, r := range results {
			id, _ := r["scriptIdPart"].(string)
			if id == "" || seen[id] {
				continue
			}
			seen[id] = true
			name, _ := r["title"].(string)
			kind, _ := r["kind"].(string)
			// Normalize: search returns "strategy" | "study" (study = indicator).
			kind = normalizeKind(kind)
			access, _ := r["access"].(int)
			likes := intOrZero(r["agreeCount"])
			item := scanItem{
				PineID: id,
				Name:   name,
				Kind:   kind,
				Access: access,
				Likes:  likes,
				Query:  q,
			}
			items = append(items, item)
		}
	}

	// Filter by type.
	if wantType == "strategy" || wantType == "indicator" {
		filtered := items[:0]
		for _, it := range items {
			if it.Kind == wantType {
				filtered = append(filtered, it)
			}
		}
		items = filtered
	}
	// Cap the total at --limit.
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}

	// Verify against the authoritative metaInfo (pine.isStrategy + input counts).
	if verify {
		for i := range items {
			if i >= verifyMax {
				items[i].Note = "not verified (verify-max reached)"
				continue
			}
			items[i] = c.verifyItem(client, cfg, items[i])
		}
	}

	// Sort: strategies first when --type any, then by likes desc.
	sort.SliceStable(items, func(a, b int) bool {
		if wantType == "any" && items[a].Kind != items[b].Kind {
			return items[a].Kind == "strategy"
		}
		if items[a].Mismatch != items[b].Mismatch {
			return !items[a].Mismatch
		}
		return items[a].Likes > items[b].Likes
	})

	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{
			"queries": queries,
			"type":    wantType,
			"count":   len(items),
			"results": items,
		}, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	printScanTable(env.Stdout, items, wantType)
	return nil
}

// verifyItem fetches the script's metaInfo and confirms the kind.
func (c *scanCmd) verifyItem(client *pinefacade.Client, cfg *config.Config, it scanItem) scanItem {
	res, err := client.Get(it.PineID, "last", cfg.CookieHeaderOrEmpty())
	if err != nil || res.MetaInfo == nil {
		it.Note = "metaInfo fetch failed"
		return it
	}
	mi := res.MetaInfo
	isStrategy := false
	if pine, ok := mi["pine"].(map[string]any); ok {
		if v, ok := pine["isStrategy"].(bool); ok {
			isStrategy = v
		}
	}
	sch := schema.FromMetaInfo(it.PineID, mi)
	if sch != nil {
		it.Inputs = len(sch.Inputs)
		it.Plots = len(sch.Plots)
	}
	verified := "indicator"
	if isStrategy {
		verified = "strategy"
	}
	it.VerifiedKind = verified
	if verified != it.Kind {
		it.Mismatch = true
		it.Note = fmt.Sprintf("search said %s but metaInfo says %s", it.Kind, verified)
	} else if it.Kind == "" {
		it.Kind = verified
		it.Note = "kind filled from metaInfo"
	}
	return it
}

// normalizeKind maps the search API kind to the two canonical types.
func normalizeKind(k string) string {
	switch strings.ToLower(k) {
	case "strategy":
		return "strategy"
	case "study", "indicator":
		return "indicator"
	default:
		return "" // unknown — left for metaInfo verification
	}
}

func printScanTable(w io.Writer, items []scanItem, wantType string) {
	if len(items) == 0 {
		fmt.Fprintln(w, "No results. Try a different query or --type any.")
		return
	}
	fmt.Fprintf(w, "\nScan: %d script(s) (%s)\n\n", len(items), wantType)
	strategies, indicators := 0, 0
	for _, it := range items {
		switch it.Kind {
		case "strategy":
			strategies++
		case "indicator":
			indicators++
		}
	}
	fmt.Fprintf(w, "  strategies: %d | indicators: %d\n\n", strategies, indicators)

	for i, it := range items {
		kind := strings.ToUpper(it.Kind)
		flag := ""
		switch {
		case it.Mismatch:
			flag = "  ⚠ " + it.Note
		case it.Note != "":
			flag = "  (" + it.Note + ")"
		}
		verifyPart := ""
		if it.VerifiedKind != "" {
			verifyPart = fmt.Sprintf(" | verified=%s", it.VerifiedKind)
			if it.Inputs > 0 || it.Plots > 0 {
				verifyPart += fmt.Sprintf(" | inputs=%d plots=%d", it.Inputs, it.Plots)
			}
		}
		fmt.Fprintf(w, "%3d. [%s] %s\n", i+1, kind, it.Name)
		fmt.Fprintf(w, "     %s | likes=%d%s%s\n", it.PineID, it.Likes, verifyPart, flag)
	}
	fmt.Fprintln(w)
}

func (c *scanCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "scan — Search TradingView public scripts classified as strategy or indicator")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv scan <query> [more queries...] [options]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Purpose: find more sample scripts so strategy sweeps and indicator input")
	fmt.Fprintln(w, "changes are robust — a sweep over 5+ scripts beats a single sample.")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --type strategy|indicator|any   Filter by kind (default: any)")
	fmt.Fprintln(w, "  --limit N                       Max total results (default: 20)")
	fmt.Fprintln(w, "  --per-query N                   Max results per query (default: 20)")
	fmt.Fprintln(w, "  --verify                        Fetch each metaInfo and confirm the kind")
	fmt.Fprintln(w, "                                  (authoritative pine.isStrategy + input/plot counts)")
	fmt.Fprintln(w, "  --verify-max N                  Max scripts to verify (default: 10)")
	fmt.Fprintln(w, "  --json                          JSON output")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv scan RSI --type strategy --limit 10")
	fmt.Fprintln(w, "  tv scan \"RSI,MACD,EMA\" --type indicator --verify --limit 15")
	fmt.Fprintln(w, "  tv scan strategy --type strategy --verify --limit 25   # seed sweep corpus")
	fmt.Fprintln(w, "  tv scan \"supertrend,smc,ict\" --type indicator --json --out scan.json")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Feed the results into:")
	fmt.Fprintln(w, "  strategies  → tv study set <id> --inputs '...' + tv study report <id>   (parameter sweeps)")
	fmt.Fprintln(w, "  indicators  → tv study set <id> --inputs '...' / tv analyze <id>        (input changes)")
}