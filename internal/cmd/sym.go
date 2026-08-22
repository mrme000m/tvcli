// sym.go — change the live chart's symbol programmatically via the chart
// widget-collection setSymbol() API (bdg CDP).
//
// Network debugging (verified live on chart dvv4N29P with a WS probe + HTTP
// capture during BTCUSDT→BINANCE:XAUUSD→PEPPERSTONE:XAUUSD switches):
//
//   - WS (the only meaningful traffic; no XHR for the change itself): the
//     client sends, in order:
//       1. quote_remove_symbols  [quoteSession, "<old symbol>"]       — drop old
//       2. quote_add_symbols     [quoteSession, "=<symbol-descriptor>"] — subscribe new
//       3. resolve_symbol        [chartSession, "<NEW symbol id>", "=<descriptor>"]
//       4. modify_series         [chartSession, "sds_1", "<new series instance>",
//                                 "<new symbol id>", resolution, ""]
//       5. quote_add_symbols     [quoteSession, "<plain ticker>"]
//     The symbol id INCREMENTS per change (sds_sym_1 initial, then sds_sym_3,
//     sds_sym_4, ...); the series instance id increments too (s1, s2, s3, ...).
//     The symbol descriptor is JSON: {"adjustment":"splits","currency-id":
//     "<CCY>","session":"regular","symbol":"<EXCHANGE:TICKER>"}. Server replies
//     symbol_resolved → series_loading → series_completed → timescale_update.
//
//   - Two programmatic levels (both verified):
//     • LOW (data only): chartWidget._modelWV._value.m_model.mainSeries()
//       ._symbolSourceWV._value.setSymbol(sym) — resolves the new symbol and
//       streams its data, but the toolbar/legend button stays on the OLD text.
//     • HIGH (full, what the UI symbol-search flow invokes):
//       window._exposed_chartWidgetCollection.setSymbol(sym) — applies through
//       the widget path: same wire messages AND updates the toolbar button,
//       legend, and document title. Use this one.
package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

type symCmd struct{ app *App }

func (c *symCmd) Name() string     { return "sym" }
func (c *symCmd) Aliases() []string { return []string{"symbol", "set-symbol"} }
func (c *symCmd) Synopsis() string {
	return "Change the live chart's symbol programmatically (widget setSymbol via bdg): e.g. sym BTCUSDT, sym OANDA:XAUUSD"
}

func (c *symCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	want := flags.Get("sym")
	if want == "" {
		want = flags.Get("symbol")
	}
	if want == "" && len(flags.Positional) > 0 {
		want = flags.Positional[0]
	}
	if want == "" {
		return fmt.Errorf("usage: sym <symbol>  (e.g. sym BTCUSDT, sym OANDA:XAUUSD, sym AAPL)")
	}

	// Normalize: plain tickers get a default exchange (BTCUSDT → BINANCE:BTCUSDT).
	normalized, err := pinefacade.ValidateSymbol(want)
	if err != nil {
		return fmt.Errorf("invalid symbol: %v", err)
	}

	bdgPath := c.resolveBDGPath()
	verbose := flags.Has("verbose")
	shot := flags.Get("out") // optional screenshot path

	// The HIGH-level API: window._exposed_chartWidgetCollection.setSymbol(sym).
	// Returns a promise; bdg dom eval awaits it (Runtime.evaluate with
	// awaitPromise), so we verify after.
	setScript := fmt.Sprintf(`(function(){
  try {
    var col = window._exposed_chartWidgetCollection;
    if (!col || typeof col.setSymbol !== "function") return "no setSymbol API";
    col.setSymbol(%q);
    return "ok";
  } catch (e) { return "err:" + String(e); }
})()`, normalized)
	if verbose {
		fmt.Fprintf(env.Stderr, "⚡ Calling setSymbol(%s) on the chart widget collection...\n", normalized)
	}
	res, err := c.runBDG(bdgPath, []string{"dom", "eval", setScript})
	if err != nil {
		return fmt.Errorf("setSymbol: %w", err)
	}
	resStr := strings.Trim(strings.TrimSpace(string(res)), "\"")
	if resStr != "ok" {
		if strings.Contains(resStr, "err:") {
			return fmt.Errorf("setSymbol failed in page: %s", resStr)
		}
		return fmt.Errorf("setSymbol API unavailable in page: %s", resStr)
	}

	// Wait for resolve + series load, then verify model + toolbar.
	time.Sleep(3000 * time.Millisecond)
	verifyScript := `(function(){var cwc=window._exposed_chartWidgetCollection;var cw=cwc&&cwc.activeChartWidget&&cwc.activeChartWidget._value;var m=cw&&cw._modelWV&&cw._modelWV._value;var mm=m&&m.m_model;var ms=mm&&mm.mainSeries();var sym=ms&&ms._symbolSourceWV&&ms._symbolSourceWV._value;var model=sym&&typeof sym.symbol==="function"?String(sym.symbol()):null;var b=document.querySelector(".button-eTD3FKHQ");return {model:model,toolbar:b?(b.textContent||"").trim():null};})()`
	got, err := c.runBDG(bdgPath, []string{"dom", "eval", verifyScript})
	if err == nil {
		var v struct {
			Model   string `json:"model"`
			Toolbar string `json:"toolbar"`
		}
		if json.Unmarshal(got, &v) == nil && v.Model != "" {
			// The toolbar shows the SHORT symbol (e.g. "XAUUSD" for
			// "OANDA:XAUUSD", "AAPL" for "BATS:AAPL"); accept either an exact
			// or a short-name (after the last ':') match as "synced".
			short := v.Model
			if i := strings.LastIndex(short, ":"); i >= 0 {
				short = short[i+1:]
			}
			toolbarNorm := strings.TrimSpace(v.Toolbar)
			synced := strings.EqualFold(toolbarNorm, short) || strings.EqualFold(toolbarNorm, v.Model)
			if synced {
				fmt.Fprintf(env.Stderr, "✓ Chart symbol is now: %s (toolbar synced)\n", v.Model)
			} else {
				fmt.Fprintf(env.Stderr, "✓ Chart model symbol is now: %s (toolbar shows %s)\n", v.Model, v.Toolbar)
			}
		}
	}

	// Optional screenshot of the new symbol.
	if shot != "" {
		shotArgs := []string{"dom", "screenshot", shot}
		if flags.Has("full") || flags.Has("full-page") {
			shotArgs = append(shotArgs, "--full-page")
		}
		fmt.Fprintf(env.Stderr, "📸 Capturing screenshot...\n")
		if _, err := c.runBDG(bdgPath, shotArgs); err != nil {
			return fmt.Errorf("screenshot: %w", err)
		}
		fmt.Fprintf(env.Stderr, "✓ Screenshot saved: %s\n", shot)
	}
	return nil
}

// runBDG executes bdg with the given args and returns its stdout.
func (c *symCmd) runBDG(bdgPath string, args []string) ([]byte, error) {
	bdgParts := strings.Fields(bdgPath)
	cmd := exec.Command(bdgParts[0], append(bdgParts[1:], args...)...)
	cmd.Stderr = os.Stderr
	return cmd.Output()
}

// resolveBDGPath returns the bdg executable; when bdg isn't on PATH, the
// local dist entrypoint is used via `node <entry>`.
func (c *symCmd) resolveBDGPath() string {
	paths := []string{
		"/Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js",
		"/Volumes/Spare/npm/global/bin/bdg",
		"bdg",
	}
	for _, p := range paths {
		if strings.HasSuffix(p, ".js") {
			if _, err := os.Stat(p); err == nil {
				return "node " + p
			}
			continue
		}
		if _, err := exec.LookPath(p); err == nil {
			return p
		}
	}
	return "bdg"
}

func (c *symCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "sym — Change the live chart's symbol programmatically")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv sym <symbol> [--out shot.png] [--verbose]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Calls the chart widget collection's setSymbol() via bdg CDP — the SAME")
	fmt.Fprintln(w, "method the UI's symbol-search flow invokes. It sends quote_remove_symbols")
	fmt.Fprintln(w, "-> quote_add_symbols -> resolve_symbol -> modify_series on the WS (no XHR)")
	fmt.Fprintln(w, "and updates the toolbar + legend + title. Requires bdg attached to a chart tab:")
	fmt.Fprintln(w, "  node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Symbol formats:")
	fmt.Fprintln(w, "  BTCUSDT            plain ticker (auto-exchange: BINANCE:BTCUSDT)")
	fmt.Fprintln(w, "  OANDA:XAUUSD       full EXCHANGE:SYMBOL")
	fmt.Fprintln(w, "  BINANCE:ETHUSDT, NASDAQ:AAPL, ...")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --out FILE         Also screenshot the new symbol to FILE")
	fmt.Fprintln(w, "  --full             Full-page screenshot (with --out)")
	fmt.Fprintln(w, "  --verbose          Show what is being called")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv sym BTCUSDT")
	fmt.Fprintln(w, "  tv sym OANDA:XAUUSD --out gold.png")
	fmt.Fprintln(w, "  tv sym NASDAQ:AAPL --verbose")
}