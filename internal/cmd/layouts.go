package cmd

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

type layoutsCmd struct{ app *App }

func (c *layoutsCmd) Name() string      { return "layouts" }
func (c *layoutsCmd) Aliases() []string { return []string{"charts", "layout"} }
func (c *layoutsCmd) Synopsis() string  { return "List, create, rename, and delete saved chart layouts" }

// Run dispatches layout subcommands: list (default), show, create, rename, delete.
func (c *layoutsCmd) Run(env *cli.Env) error {
	sub := ""
	if len(env.Flags.Positional) > 0 {
		sub = env.Flags.Positional[0]
	}
	switch sub {
	case "", "list", "ls":
		return c.list(env)
	case "show":
		return c.show(env)
	case "create", "new":
		return c.create(env)
	case "rename":
		return c.rename(env)
	case "delete", "rm":
		return c.delete(env)
	default:
		c.printHelp(env)
		return nil
	}
}

// list lists saved layouts via GET /my-charts/ (the same endpoint the web
// app's "Manage layouts" dialog uses).
func (c *layoutsCmd) list(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if !cfg.HasAuth() {
		return fmt.Errorf("layouts requires SESSION — set SESSION/SIGNATURE/DEVICE_T in .env")
	}

	limit := flags.GetInt("limit", 20)
	charts, err := auth.FetchMyCharts(cfg.SessionID, cfg.Signature, cfg.DeviceToken, limit, auth.WithProxy(cfg.ProxyURL))
	if err != nil {
		return err
	}

	if flags.Has("json") {
		b, _ := json.MarshalIndent(map[string]any{"count": len(charts), "layouts": charts}, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	fmt.Fprintf(env.Stdout, "\nSaved layouts: %d\n\n", len(charts))
	fmt.Fprintf(env.Stdout, "%-22s %-18s %-6s %-10s %-12s %s\n", "NAME", "SYMBOL", "TF", "LAYOUT ID", "CHART", "MODIFIED")
	for _, ch := range charts {
		tf := ch.Interval
		if tf == "" {
			tf = ch.Resolution
		}
		slug := ch.ImageURL
		if slug == "" {
			slug = ch.URL
		}
		fmt.Fprintf(env.Stdout, "%-22s %-18s %-6s %-10d %-12s %s\n",
			truncateStr(ch.Name, 22), truncateStr(ch.Symbol, 18), tf, ch.ID, slug, ch.Modified)
	}
	if len(charts) > 0 {
		first := charts[0].ImageURL
		if first == "" {
			first = charts[0].URL
		}
		fmt.Fprintf(env.Stdout, "\nOpen: https://www.tradingview.com/chart/%s/\n", first)
	}
	return nil
}

// show prints one layout matched by name, chart slug, or numeric id.
func (c *layoutsCmd) show(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags
	if len(flags.Positional) < 2 {
		return fmt.Errorf("usage: layouts show <name|chartSlug|id>")
	}
	query := strings.ToLower(flags.Positional[1])

	charts, err := auth.FetchMyCharts(cfg.SessionID, cfg.Signature, cfg.DeviceToken, 200, auth.WithProxy(cfg.ProxyURL))
	if err != nil {
		return err
	}
	for _, ch := range charts {
		slug := ch.ImageURL
		if slug == "" {
			slug = ch.URL
		}
		if strings.ToLower(ch.Name) == query || strings.ToLower(slug) == query || fmt.Sprint(ch.ID) == flags.Positional[1] {
			if flags.Has("json") {
				b, _ := json.MarshalIndent(ch, "", "  ")
				fmt.Fprintln(env.Stdout, string(b))
				return nil
			}
			fmt.Fprintf(env.Stdout, "Layout: %s (id %d)\n  chart: %s\n  symbol: %s @ %s\n  modified: %s\n  url: %s\n",
				ch.Name, ch.ID, slug, ch.Symbol, firstNonEmptyStr(ch.Interval, ch.Resolution), ch.Modified, auth.LayoutsURL(slug))
			return nil
		}
	}
	return fmt.Errorf("layout %q not found", flags.Positional[1])
}

// create saves a NEW empty layout headlessly via POST /api/v1/charts/save/
// with a minimal chart-state content ("{}") — no browser needed.
func (c *layoutsCmd) create(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags
	if !cfg.HasAuth() {
		return fmt.Errorf("layouts create requires SESSION")
	}

	name := flags.Get("name")
	if name == "" && len(flags.Positional) > 1 {
		name = flags.Positional[1]
	}
	if name == "" {
		return fmt.Errorf("usage: layouts create <name> [--symbol EXCHANGE:SYM] [--tf 15] [--symbol-type TYPE]")
	}

	symbol := flags.Get("symbol")
	if symbol == "" {
		symbol = "BINANCE:BTCUSDT"
	}
	tf := flags.Get("tf")
	if tf == "" {
		tf = flags.Get("timeframe")
	}
	if tf == "" {
		tf = "15"
	}
	desc := layoutSymbolDescriptor(symbol, flags.Get("symbol-type"))

	res, err := auth.SaveChart(cfg.SessionID, cfg.Signature, cfg.DeviceToken, auth.SaveChartParams{
		Name:           name,
		Description:    "",
		Resolution:     tf,
		Symbol:         desc.Symbol,
		SymbolType:     desc.SymbolType,
		Exchange:       desc.Exchange,
		ListedExchange: desc.ListedExchange,
		ShortName:      desc.ShortName,
		Legs:           desc.Legs,
		IsRealtime:     true,
		Content:        []byte("{}"), // minimal empty chart state
	}, auth.WithProxy(cfg.ProxyURL))
	if err != nil {
		return err
	}

	fmt.Fprintf(env.Stdout, "✓ Created layout %q (id %d, chart %s)\n  %s\n",
		name, res.ID, res.ImageURL, auth.LayoutsURL(res.ImageURL))
	return nil
}

// rename renames the layout of the CURRENTLY OPEN chart in the live browser.
// It drives the in-page saveChartService._renameController, which re-saves the
// full chart state under the new name (content is auto-sourced from the
// browser, so nothing is lost). Requires bdg attached to a chart tab.
func (c *layoutsCmd) rename(env *cli.Env) error {
	flags := env.Flags
	if len(flags.Positional) < 2 {
		return fmt.Errorf("usage: layouts rename <newName>")
	}
	newName := flags.Positional[1]

	nameJSON, _ := json.Marshal(newName)
	script := fmt.Sprintf(`(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var rc = cw && cw._saveChartService && cw._saveChartService._renameController;
    if (!rc || typeof rc._actionHandler !== "function") return JSON.stringify({err:"rename controller unavailable"});
    rc._actionHandler({ newValue: %s, dialogClose: function(){} });
    return JSON.stringify({ok:true, renamedTo:%s});
  } catch (e) { return JSON.stringify({err:String(e)}); }
})()`, nameJSON, nameJSON)

	out, err := runBDGCmd(bdgPath(), []string{"dom", "eval", script})
	if err != nil {
		return fmt.Errorf("rename: %w", err)
	}
	res := strings.TrimSpace(string(out))
	var inner string
	if json.Unmarshal([]byte(res), &inner) == nil {
		res = strings.TrimSpace(inner)
	}
	var r struct {
		OK        bool   `json:"ok"`
		RenamedTo string `json:"renamedTo"`
		Err       string `json:"err"`
	}
	if err := json.Unmarshal([]byte(res), &r); err != nil || (r.Err != "") {
		return fmt.Errorf("rename failed: %s", res)
	}
	fmt.Fprintf(env.Stdout, "✓ Renamed current layout to %q\n", r.RenamedTo)
	return nil
}

// delete deletes layouts headlessly via POST /api/v1/charts/delete/ with
// {"uid": [slug...]}. Accepts chart slugs or numeric ids (resolved to slugs).
func (c *layoutsCmd) delete(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags
	if !cfg.HasAuth() {
		return fmt.Errorf("layouts delete requires SESSION")
	}
	if len(flags.Positional) < 2 {
		return fmt.Errorf("usage: layouts delete <chartSlug|id> [more...]")
	}

	// Resolve ids → slugs (image_url) by listing first.
	charts, err := auth.FetchMyCharts(cfg.SessionID, cfg.Signature, cfg.DeviceToken, 500, auth.WithProxy(cfg.ProxyURL))
	if err != nil {
		return err
	}
	byID := map[string]string{}
	byName := map[string]string{}
	for _, ch := range charts {
		slug := ch.ImageURL
		if slug == "" {
			slug = ch.URL
		}
		byID[fmt.Sprint(ch.ID)] = slug
		byName[strings.ToLower(ch.Name)] = slug
	}

	seen := map[string]bool{}
	uids := make([]string, 0, len(flags.Positional)-1)
	for _, arg := range flags.Positional[1:] {
		slug := arg
		if s, ok := byID[arg]; ok {
			slug = s
		} else if s, ok := byName[strings.ToLower(arg)]; ok {
			slug = s
		}
		// assume a bare unmatched value is already a chart slug
		if seen[slug] {
			continue
		}
		seen[slug] = true
		uids = append(uids, slug)
	}

	if err := auth.DeleteChart(cfg.SessionID, cfg.Signature, cfg.DeviceToken, uids, auth.WithProxy(cfg.ProxyURL)); err != nil {
		return err
	}
	fmt.Fprintf(env.Stdout, "✓ Deleted %d layout(s): %s\n", len(uids), strings.Join(uids, ", "))
	return nil
}

// layoutSymbolDescriptor builds the symbol fields for the save endpoint from
// an EXCHANGE:SYMBOL string.
func layoutSymbolDescriptor(symbol, symbolType string) symbolDesc {
	exchange, short := symbol, symbol
	if i := strings.Index(symbol, ":"); i >= 0 {
		exchange, short = symbol[:i], symbol[i+1:]
	}
	if symbolType == "" {
		symbolType = inferSymbolType(exchange, short)
	}
	legs, _ := json.Marshal([]map[string]string{{"symbol": symbol, "pro_symbol": symbol}})
	return symbolDesc{
		Symbol:         symbol,
		SymbolType:     symbolType,
		Exchange:       exchange,
		ListedExchange: strings.ToUpper(exchange),
		ShortName:      short,
		Legs:           string(legs),
	}
}

// symbolDesc is the symbol descriptor portion of a layout save.
type symbolDesc struct {
	Symbol, SymbolType, Exchange, ListedExchange, ShortName, Legs string
}

// inferSymbolType guesses the TradingView symbol_type from the exchange and
// symbol, with a best-effort mapping. Callers can override via --symbol-type.
func inferSymbolType(exchange, sym string) string {
	upEx, upSym := strings.ToUpper(exchange), strings.ToUpper(sym)
	// Commodity metals first (gold/silver/platinum/palladium).
	if strings.HasPrefix(upSym, "XAU") || strings.HasPrefix(upSym, "XAG") ||
		strings.HasPrefix(upSym, "XPT") || strings.HasPrefix(upSym, "XPD") ||
		strings.HasPrefix(upSym, "GC") || strings.HasPrefix(upSym, "SI") {
		return "commodity"
	}
	if strings.Contains(upSym, "BTC") || strings.Contains(upSym, "ETH") ||
		strings.Contains(upSym, "USDT") || strings.Contains(upSym, "USDC") {
		return "crypto"
	}
	switch upEx {
	case "BINANCE", "BYBIT", "COINBASE", "KUCOIN", "OKX", "FTX", "CRYPTO", "KRAKEN", "BITFINEX", "BINANCEUS":
		return "crypto"
	case "OANDA", "FX_IDC", "FX", "FXCM", "PEPPERSTONE", "FOREX", "SAXO":
		return "forex"
	case "NASDAQ", "NYSE", "AMEX", "BATS", "ARCA", "TSX", "LSE":
		return "stock"
	case "INDEX", "TVC", "SP", "DJ", "FTSE":
		return "index"
	case "COMEX", "NYMEX", "CBOT", "CME":
		return "futures"
	default:
		return "stock"
	}
}

func firstNonEmptyStr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func truncateStr(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}

func (c *layoutsCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "layouts — List, create, rename, and delete saved TradingView chart layouts")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  tv layouts [list] [--limit N] [--json]        List saved layouts")
	fmt.Fprintln(w, "  tv layouts show <name|chartSlug|id>           Show one layout")
	fmt.Fprintln(w, "  tv layouts create <name> [--symbol E:S] [--tf 15] [--symbol-type TYPE]")
	fmt.Fprintln(w, "  tv layouts rename <newName>                   Rename the current chart's layout (live browser)")
	fmt.Fprintln(w, "  tv layouts delete <chartSlug|id> [...]        Delete layouts")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "API (all HTTP, headless except rename):")
	fmt.Fprintln(w, "  list   GET  /my-charts/?limit=N")
	fmt.Fprintln(w, "  create POST /api/v1/charts/save/   (multipart; content=gzip chart state)")
	fmt.Fprintln(w, "  update POST /api/v1/charts/save/   (same, with image_url set)")
	fmt.Fprintln(w, "  delete POST /api/v1/charts/delete/ (JSON {\"uid\":[slug...]})")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "A layout has a numeric id and a chart slug (image_url). 'rename' drives the")
	fmt.Fprintln(w, "live browser's saveChartService so the full chart state is preserved; the")
	fmt.Fprintln(w, "other subcommands are pure HTTP (no browser).")
}