// tf.go — change the live chart's timeframe, programmatically via the chart
// model's symbol-source setInterval() API (bdg CDP), with a UI-automation
// fallback (toolbar dropdown clicks).
//
// Network debugging (verified live on chart dvv4N29P with a WS probe + HTTP
// capture during 2h→15m→2h switches):
//
//   - WS (the ONLY meaningful traffic): the client sends ONE message
//     {"m":"modify_series","p":[chartSessionId,"sds_1",seriesInstanceId,
//     "sds_sym_1",resolution,""]} — seriesInstanceId increments per change
//     (s1 = initial create, s2, s3, ...); resolution is numeric minutes
//     ("15", "120") or "1D"/"1W"/"1M". The server replies series_loading →
//     series_completed → timescale_update with the new bars.
//   - XHR/fetch: NONE for the switch itself (only a telemetry POST, and the
//     layout autosave rides the WS/state layer, not per-change HTTP).
//
// Programmatic path (the method the UI itself calls on interval select):
//
//	chartWidget._modelWV._value.m_model.mainSeries()
//	  ._symbolSourceWV._value.setInterval('<resolution>')
//
// which calls setSymbolParams({interval}) → modify_series on the wire, and
// updates the toolbar/state too. Verified round-trip 2h→15m→2h.
//
// Lower-level alternative (data layer ONLY, does NOT update the UI state):
// chartApi.modifySeries(sessionId, "sds_1", nextSeriesId, "sds_sym_1",
// resolution, undefined, null, noop) — sends the same wire message but the
// toolbar stays on the old interval.
package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
)

type tfCmd struct{ app *App }

func (c *tfCmd) Name() string     { return "tf" }
func (c *tfCmd) Aliases() []string { return []string{"timeframe", "set-tf"} }
func (c *tfCmd) Synopsis() string {
	return "Change the live chart's timeframe programmatically (model setInterval via bdg): 1m..45m, 1h..12h, 1D, 1W, 1M"
}

// tfOption maps a CLI timeframe token to the popup menu item text.
// Matches TradingView's interval menu labels.
var tfOption = map[string]string{
	"1": "1 minute", "1m": "1 minute",
	"2": "2 minutes", "2m": "2 minutes",
	"3": "3 minutes", "3m": "3 minutes",
	"5": "5 minutes", "5m": "5 minutes",
	"10": "10 minutes", "10m": "10 minutes",
	"15": "15 minutes", "15m": "15 minutes",
	"30": "30 minutes", "30m": "30 minutes",
	"45": "45 minutes", "45m": "45 minutes",
	"60": "1 hour", "1h": "1 hour",
	"120": "2 hours", "2h": "2 hours",
	"180": "3 hours", "3h": "3 hours",
	"240": "4 hours", "4h": "4 hours",
	"360": "6 hours", "6h": "6 hours",
	"720": "12 hours", "12h": "12 hours",
	"1d": "1 day", "D": "1 day",
	"1w": "1 week", "W": "1 week",
	"1M": "1 month", "M": "1 month",
}

func (c *tfCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	// Desired timeframe: first positional or --tf/--tf value.
	want := flags.Get("tf")
	if want == "" {
		want = flags.Get("timeframe")
	}
	if want == "" && len(flags.Positional) > 0 {
		want = flags.Positional[0]
	}
	if want == "" {
		return fmt.Errorf("usage: tf <timeframe>  (e.g. tf 15m, tf 1h, tf 4h, tf 1D)")
	}

	option, ok := tfOption[strings.ToLower(want)]
	if !ok {
		return fmt.Errorf("unsupported timeframe '%s' (supported: 1m..45m, 1h..12h, 1D, 1W, 1M)", want)
	}

	// Resolution in minutes for the wire (modify_series sends the numeric
	// resolution string: "15", "120", "1D", "1W", "1M").
	resolution := resolutionFor(want)

	bdgPath := c.resolveBDGPath()
	verbose := flags.Has("verbose")

	// 1. Programmatic path (preferred): the chart model's symbol source
	// exposes setInterval(res) — the same method the UI calls when an
	// interval is selected. It updates BOTH the data layer (sends
	// modify_series on the WS, verified: series_loading → series_completed
	// → timescale_update) AND the toolbar/UI state. No DOM clicking needed.
	// Path (verified live on chart dvv4N29P):
	//   mainSeries()._symbolSourceWV._value.setInterval('15')
	setScript := fmt.Sprintf(`(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    var mm = m && m.m_model;
    var ms = mm && mm.mainSeries();
    var symSrc = ms && ms._symbolSourceWV && ms._symbolSourceWV._value;
    if (!symSrc || typeof symSrc.setInterval !== "function") return "no setInterval API";
    symSrc.setInterval(%q);
    return "ok";
  } catch (e) { return "err:" + String(e); }
})()`, resolution)
	if verbose {
		fmt.Fprintf(env.Stderr, "⚡ Calling setInterval(%s) on the chart model...\n", resolution)
	}
	res, err := c.runBDG(bdgPath, []string{"dom", "eval", setScript})
	if err != nil {
		return fmt.Errorf("setInterval: %w", err)
	}
	resStr := strings.Trim(strings.TrimSpace(string(res)), "\"")
	if resStr == "ok" {
		time.Sleep(1500 * time.Millisecond)
		// 2. Verify via the model interval + toolbar button.
		verifyScript := `(function(){var cwc=window._exposed_chartWidgetCollection;var cw=cwc&&cwc.activeChartWidget&&cwc.activeChartWidget._value;var m=cw&&cw._modelWV&&cw._modelWV._value;var mm=m&&m.m_model;var ms=mm&&mm.mainSeries();var sym=ms&&ms._symbolSourceWV&&ms._symbolSourceWV._value;var iv=sym&&typeof sym.interval==="function"?sym.interval():null;var b=document.querySelector(".menuBtn-HNxWMF1j");return {interval:iv,toolbar:b?(b.textContent||"").trim():null};})()`
		got, err := c.runBDG(bdgPath, []string{"dom", "eval", verifyScript})
		if err == nil {
			var v struct {
				Interval string `json:"interval"`
				Toolbar  string `json:"toolbar"`
			}
			if json.Unmarshal(got, &v) == nil {
				if v.Toolbar != "" {
					fmt.Fprintf(env.Stderr, "✓ Chart timeframe is now: %s (model interval %s)\n", v.Toolbar, v.Interval)
				} else {
					fmt.Fprintf(env.Stderr, "✓ Chart model interval is now: %s\n", v.Interval)
				}
				return nil
			}
		}
		fmt.Fprintf(env.Stderr, "✓ Timeframe change sent (resolution %s).\n", resolution)
		return nil
	}
	if strings.Contains(resStr, "err:") {
		fmt.Fprintf(env.Stderr, "⚠ Programmatic path failed (%s); falling back to UI automation.\n", resStr)
	} else {
		fmt.Fprintf(env.Stderr, "⚠ Programmatic API unavailable; falling back to UI automation.\n")
	}
	return c.changeViaUI(bdgPath, option, verbose, env)
}

// changeViaUI is the fallback: click the timeframe dropdown and select the
// interval option (real CDP input events through bdg dom eval).
func (c *tfCmd) changeViaUI(bdgPath, option string, verbose bool, env *cli.Env) error {
	openScript := "(function(){var b=document.querySelector('.menuBtn-HNxWMF1j');if(!b)return 'no dropdown button';var r=b.getBoundingClientRect();var o={bubbles:true,cancelable:true,view:window,clientX:r.x+r.width/2,clientY:r.y+r.height/2};b.dispatchEvent(new MouseEvent('mousedown',o));b.dispatchEvent(new MouseEvent('mouseup',o));b.dispatchEvent(new MouseEvent('click',o));return 'opened';})()"
	if verbose {
		fmt.Fprintf(env.Stderr, "📂 Opening timeframe dropdown...\n")
	}
	if _, err := c.runBDG(bdgPath, []string{"dom", "eval", openScript}); err != nil {
		return fmt.Errorf("open timeframe dropdown: %w", err)
	}
	time.Sleep(700 * time.Millisecond)

	clickScript := fmt.Sprintf(
		"(function(){var all=document.querySelectorAll('div,button,span,a');for(var i=0;i<all.length;i++){var e=all[i];if(e.children.length>0)continue;var t=(e.textContent||'').trim();if(t==='%s'){var r=e.getBoundingClientRect();var o={bubbles:true,cancelable:true,view:window,clientX:r.x+r.width/2,clientY:r.y+r.height/2};e.dispatchEvent(new MouseEvent('mousedown',o));e.dispatchEvent(new MouseEvent('mouseup',o));e.dispatchEvent(new MouseEvent('click',o));return 'clicked';}}return 'not found';})()",
		option)
	if verbose {
		fmt.Fprintf(env.Stderr, "🎯 Selecting %s...\n", option)
	}
	res, err := c.runBDG(bdgPath, []string{"dom", "eval", clickScript})
	if err != nil {
		return fmt.Errorf("select timeframe: %w", err)
	}
	if strings.Contains(string(res), "not found") {
		return fmt.Errorf("interval '%s' not found in the dropdown popup", option)
	}
	time.Sleep(1500 * time.Millisecond)

	verifyScript := "(function(){var b=document.querySelector('.menuBtn-HNxWMF1j');return b?(b.textContent||'').trim():'none';})()"
	got, err := c.runBDG(bdgPath, []string{"dom", "eval", verifyScript})
	if err != nil {
		return fmt.Errorf("verify timeframe: %w", err)
	}
	gotStr := strings.Trim(string(got), "\"")
	fmt.Fprintf(env.Stderr, "✓ Chart timeframe is now: %s\n", gotStr)
	return nil
}

// resolutionFor maps a CLI timeframe token to the wire resolution string used
// by modify_series (numeric minutes for intraday; D/W/M for daily+).
func resolutionFor(want string) string {
	lower := strings.ToLower(want)
	switch lower {
	case "1d", "d":
		return "1D"
	case "1w", "w":
		return "1W"
	}
	// Month: uppercase-M forms only ("1M"/"M"), so "1m" stays 1 minute.
	if want == "1M" || want == "M" || lower == "mon" || lower == "month" {
		return "1M"
	}
	// Hours: "1h", "2h", ... → minutes.
	if strings.HasSuffix(lower, "h") {
		hours := strings.TrimSuffix(lower, "h")
		var h int
		if n, _ := fmt.Sscanf(hours, "%d", &h); n == 1 && h > 0 {
			return fmt.Sprintf("%d", h*60)
		}
	}
	// Minutes: strip a trailing "m" ("15m" → "15").
	if strings.HasSuffix(lower, "m") {
		lower = strings.TrimSuffix(lower, "m")
	}
	return lower
}

// runBDG executes bdg with the given args and returns its stdout.
func (c *tfCmd) runBDG(bdgPath string, args []string) ([]byte, error) {
	bdgParts := strings.Fields(bdgPath)
	cmd := exec.Command(bdgParts[0], append(bdgParts[1:], args...)...)
	cmd.Stderr = os.Stderr
	return cmd.Output()
}

// resolveBDGPath returns the bdg executable; when bdg isn't on PATH, the
// local dist entrypoint is used via `node <entry>`.
func (c *tfCmd) resolveBDGPath() string {
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

func (c *tfCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "tf — Change the live chart's timeframe")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv tf <timeframe> [--verbose]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Calls the chart model's symbol-source setInterval() via bdg CDP — the SAME")
	fmt.Fprintln(w, "method the UI uses when you select an interval. It sends modify_series")
	fmt.Fprintln(w, "on the WS (pure WebSocket; no XHR) and updates the toolbar + chart state.")
	fmt.Fprintln(w, "Falls back to toolbar dropdown UI automation if the API is unavailable.")
	fmt.Fprintln(w, "Requires bdg attached to a chart tab:")
	fmt.Fprintln(w, "  node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Timeframes: 1m 2m 3m 5m 10m 15m 30m 45m 1h 2h 3h 4h 6h 12h 1D 1W 1M")
	fmt.Fprintln(w, "  (also accepts plain numbers: 15, 60, 240, ...)")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv tf 15m")
	fmt.Fprintln(w, "  tv tf 1h --verbose")
	fmt.Fprintln(w, "  tv tf 4h")
}