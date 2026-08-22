// study.go — list studies on the live chart session, read their input
// values, and SET input values programmatically, with optional before/after
// screenshots to verify the visual reflection.
//
// Network debugging (verified live on chart dvv4N29P, RSI Strategy E7gFVY):
//
//   - Listing studies / reading inputs is an in-page model read
//     (dataSources() / getStudyById(id).getInputValues()) — NO network
//     traffic; the inputs map is the same one create_study carried on the
//     wire: {"in_0":{"v":14,"f":true,"t":"integer"}, ...}.
//   - CHANGING an input sends ONE WS message (no XHR):
//     {"m":"modify_study","p":[chartSessionId, studyId, studyInstanceId,
//     {full inputs map with new values}]} — the study instance id increments
//     per change (st1, st4, ...). Server replies study_loading → du
//     (a large recomputed payload; for a strategy this is the full
//     performance report + trades).
//   - The change reflects visually: the pane redraws (plots/lines/markers).
//     Verified: RSI length in_0 14→7 → modify_study → 24KB du → 0.30% of
//     chart pixels changed in a before/after screenshot diff.
//
// Programmatic path used here (the exposed surface, verified):
//
//	chart.getStudyById(id).getInputValues()  // read (id/value list)
//	study.setInputValues(cur)                // apply (sends modify_study)
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

type studyCmd struct{ app *App }

func (c *studyCmd) Name() string     { return "study" }
func (c *studyCmd) Aliases() []string { return []string{"studies"} }
func (c *studyCmd) Synopsis() string {
	return "List studies on the live chart, read/set their input values programmatically (via bdg)"
}

func (c *studyCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	sub := ""
	if len(flags.Positional) > 0 {
		sub = flags.Positional[0]
	}
	switch sub {
	case "list":
		return c.listStudies(env)
	case "inputs":
		if len(flags.Positional) < 2 {
			return fmt.Errorf("usage: study inputs <entityId> [--json]")
		}
		return c.readInputs(env, flags.Positional[1])
	case "report":
		if len(flags.Positional) < 2 {
			return fmt.Errorf("usage: study report <entityId> [--signals N] [--json]")
		}
		return c.readReport(env, flags.Positional[1])
	case "set":
		if len(flags.Positional) < 2 {
			return fmt.Errorf("usage: study set <entityId> --inputs '<json>' [--before a.png] [--after b.png]")
		}
		return c.setInputs(env, flags.Positional[1])
	default:
		return fmt.Errorf("usage: study <list|inputs|report|set> ... (run 'tv study --help')")
	}
}

// --- list ---

func (c *studyCmd) listStudies(env *cli.Env) error {
	bdgPath := c.resolveBDGPath()
	args := []string{"tv", "studies"}
	if env.Flags.Has("json") {
		args = append(args, "-j")
	}
	out, err := c.runBDG(bdgPath, args)
	if err != nil {
		return fmt.Errorf("bdg tv studies: %w", err)
	}
	fmt.Fprint(env.Stdout, string(out))
	return nil
}

// --- inputs ---

type studyInput struct {
	ID    string `json:"id"`
	Value any    `json:"value"`
}

func (c *studyCmd) readInputs(env *cli.Env, entityID string) error {
	bdgPath := c.resolveBDGPath()
	out, err := c.runBDG(bdgPath, []string{"tv", "study", "inputs", entityID, "-j"})
	if err != nil {
		return fmt.Errorf("bdg tv study inputs: %w", err)
	}
	// bdg wraps in {version, success, data:{...}}
	var envWrap struct {
		Data struct {
			ID     string       `json:"id"`
			Count  int          `json:"count"`
			Inputs []studyInput `json:"inputs"`
			Error  string       `json:"error"`
		} `json:"data"`
	}
	if err := json.Unmarshal(out, &envWrap); err != nil {
		fmt.Fprint(env.Stdout, string(out))
		return nil
	}
	if envWrap.Data.Error != "" {
		return fmt.Errorf("read inputs: %s", envWrap.Data.Error)
	}
	if env.Flags.Has("json") {
		b, _ := json.MarshalIndent(envWrap.Data.Inputs, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}
	fmt.Fprintf(env.Stdout, "%d input(s) on study %s:\n", envWrap.Data.Count, entityID)
	for _, in := range envWrap.Data.Inputs {
		val, _ := json.Marshal(in.Value)
		fmt.Fprintf(env.Stdout, "  %s = %s\n", in.ID, string(val))
	}
	return nil
}

// --- set ---

func (c *studyCmd) setInputs(env *cli.Env, entityID string) error {
	flags := env.Flags
	inputsJSON := flags.Get("inputs")
	if inputsJSON == "" {
		return fmt.Errorf("--inputs '<json>' is required, e.g. --inputs '{\"in_0\": 7}'")
	}
	beforeShot := flags.Get("before")
	afterShot := flags.Get("after")
	verbose := flags.Has("verbose")

	// Validate the JSON and normalize to a {id: value} list.
	var raw map[string]any
	if err := json.Unmarshal([]byte(inputsJSON), &raw); err != nil {
		return fmt.Errorf("invalid --inputs JSON: %v", err)
	}
	type kv struct {
		ID    string `json:"id"`
		Value any    `json:"value"`
	}
	overrides := make([]kv, 0, len(raw))
	for k, v := range raw {
		overrides = append(overrides, kv{ID: k, Value: v})
	}
	ovJSON, _ := json.Marshal(overrides)

	// Optional before screenshot.
	if beforeShot != "" {
		fmt.Fprintf(env.Stderr, "📸 Before screenshot: %s\n", beforeShot)
		if _, err := c.runBDG(c.resolveBDGPath(), []string{"dom", "screenshot", beforeShot}); err != nil {
			fmt.Fprintf(env.Stderr, "⚠ before screenshot failed: %v\n", err)
		}
	}

	// In-page: read current inputs, apply overrides (match by id OR title),
	// call setInputValues (sends modify_study → study_loading → du recompute).
	ovEsc, _ := json.Marshal(string(ovJSON))
	script := fmt.Sprintf(`(function(){
  try {
    var chart = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV && window.TradingViewApi._activeChartWidgetWV.value();
    var study = chart && chart.getStudyById ? chart.getStudyById(%q) : null;
    if (!study) return JSON.stringify({err: "study not found"});
    if (typeof study.getInputValues !== "function" || typeof study.setInputValues !== "function") {
      return JSON.stringify({err: "getInputValues/setInputValues unavailable"});
    }
    var overrides = JSON.parse(%s);
    var cur = study.getInputValues() || [];
    var matched = [], unmatched = [];
    var norm = function(s){ return String(s).toLowerCase(); };
    overrides.forEach(function(ov){
      var found = null;
      for (var i = 0; i < cur.length; i++) {
        var cid = cur[i].id != null ? norm(cur[i].id) : "";
        var ctitle = cur[i].title != null ? norm(cur[i].title) : (cur[i].name != null ? norm(cur[i].name) : "");
        if (cid === norm(ov.id) || ctitle === norm(ov.id)) { found = cur[i]; break; }
      }
      if (found) { found.value = ov.value; matched.push({id: found.id, value: ov.value}); }
      else { unmatched.push(ov.id); }
    });
    if (matched.length) { study.setInputValues(cur); }
    return JSON.stringify({matched: matched, unmatched: unmatched});
  } catch (e) { return JSON.stringify({err: String(e)}); }
})()`, entityID, ovEsc)

	if verbose {
		fmt.Fprintf(env.Stderr, "⚡ Sending modify_study (via setInputValues) for %s with %s...\n", entityID, inputsJSON)
	}
	res, err := c.runBDG(c.resolveBDGPath(), []string{"dom", "eval", script})
	if err != nil {
		return fmt.Errorf("setInputValues: %w", err)
	}
	// bdg returns the evaluated value JSON-encoded: a STRING containing the
	// object, i.e. "\"{\\\"matched\\\":...}\"" — unwrap twice.
	rawStr := strings.TrimSpace(string(res))
	var inner string
	if json.Unmarshal([]byte(rawStr), &inner) == nil {
		rawStr = strings.TrimSpace(inner)
	}
	var out struct {
		Matched   []map[string]any `json:"matched"`
		Unmatched []string         `json:"unmatched"`
		Err       string           `json:"err"`
	}
	if err := json.Unmarshal([]byte(rawStr), &out); err != nil {
		fmt.Fprintln(env.Stderr, string(res))
		return nil
	}
	if out.Err != "" {
		return fmt.Errorf("setInputValues: %s", out.Err)
	}
	matched := make([]string, 0, len(out.Matched))
	for _, m := range out.Matched {
		id, _ := m["id"].(string)
		val, _ := m["value"].(any)
		vj, _ := json.Marshal(val)
		matched = append(matched, fmt.Sprintf("%s=%s", id, string(vj)))
	}
	fmt.Fprintf(env.Stderr, "✓ Inputs applied (modify_study sent): %s\n", strings.Join(matched, ", "))
	if len(out.Unmatched) > 0 {
		fmt.Fprintf(env.Stderr, "⚠ Unmatched (use the canonical id or title): %s\n", strings.Join(out.Unmatched, ", "))
	}

	// Wait for the server to recompute (study_loading + du) and the pane to
	// redraw before the after screenshot.
	time.Sleep(3000 * time.Millisecond)

	// Verify the input took.
	verify, err := c.runBDG(c.resolveBDGPath(), []string{"tv", "study", "inputs", entityID, "-j"})
	if err == nil {
		var vw struct {
			Data struct {
				Inputs []studyInput `json:"inputs"`
			} `json:"data"`
		}
		if json.Unmarshal(verify, &vw) == nil {
			byID := map[string]any{}
			for _, in := range vw.Data.Inputs {
				byID[in.ID] = in.Value
			}
			var conf []string
			for _, ov := range overrides {
				if got, ok := byID[ov.ID]; ok {
					conf = append(conf, fmt.Sprintf("%s=%v", ov.ID, got))
				}
			}
			fmt.Fprintf(env.Stderr, "✓ Confirmed on study: %s\n", strings.Join(conf, ", "))
		}
	}

	// Optional after screenshot.
	if afterShot != "" {
		fmt.Fprintf(env.Stderr, "📸 After screenshot: %s\n", afterShot)
		if _, err := c.runBDG(c.resolveBDGPath(), []string{"dom", "screenshot", afterShot}); err != nil {
			fmt.Fprintf(env.Stderr, "⚠ after screenshot failed: %v\n", err)
		}
		fmt.Fprintf(env.Stderr, "✓ Diff with: magick compare %s %s -metric AE /tmp/diff.png\n", beforeShotOrPlaceholder(beforeShot), afterShot)
	}
	return nil
}

func beforeShotOrPlaceholder(p string) string {
	if p == "" {
		return "<before.png>"
	}
	return p
}

// --- report ---

// reportSummary mirrors the strategy report's performance.all + extras.
type reportSummary struct {
	NetProfit          float64 `json:"netProfit"`
	NetProfitPercent   float64 `json:"netProfitPercent"`
	GrossProfit        float64 `json:"grossProfit"`
	GrossLoss          float64 `json:"grossLoss"`
	ProfitFactor       float64 `json:"profitFactor"`
	TotalTrades        int     `json:"totalTrades"`
	WinningTrades      int     `json:"winningTrades"`
	LosingTrades       int     `json:"losingTrades"`
	PercentProfitable  float64 `json:"percentProfitable"`
	MaxDrawdown        float64 `json:"maxDrawdown"`
	MaxDrawdownPercent float64 `json:"maxDrawdownPercent"`
	SharpeRatio        float64 `json:"sharpeRatio"`
	AvgTrade           float64 `json:"avgTrade"`
	LargestWin         float64 `json:"largestWin"`
	LargestLoss        float64 `json:"largestLoss"`
	SignalCount        int     `json:"signalCount"`
	FirstTrade         any     `json:"firstTrade,omitempty"`
	RecentSignals      []any   `json:"recentSignals,omitempty"`
}

func (c *studyCmd) readReport(env *cli.Env, entityID string) error {
	flags := env.Flags
	nSignals := flags.GetInt("signals", 6)

	// In-page read of the strategy dataSource's _reportData: the same
	// performance report the Strategy Tester panel renders (and the same
	// payload the server recomputes via du after a modify_study). No network.
	script := fmt.Sprintf(`(function(){
  var out = {};
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    var target = null;
    m.dataSources().forEach(function(d){
      var id = null; try { id = d.id ? d.id() : d._id; } catch (e) {}
      if (id === %q) target = d;
    });
    if (!target) return JSON.stringify({err: "study not found"});
    var rd = target._reportData;
    if (!rd) return JSON.stringify({err: "no report data (not a strategy?)"});
    var v = rd._value !== undefined ? rd._value : rd;
    if (!v) return JSON.stringify({err: "empty report"});
    var perf = v.performance || v;
    var all = perf.all || perf;
    var summary = {
      netProfit: all.netProfit,
      netProfitPercent: all.netProfitPercent,
      grossProfit: all.grossProfit,
      grossLoss: all.grossLoss,
      profitFactor: all.profitFactor,
      totalTrades: all.totalTrades,
      winningTrades: all.numberOfWiningTrades,
      losingTrades: all.numberOfLosingTrades,
      percentProfitable: all.percentProfitable,
      maxDrawdown: perf.maxStrategyDrawDown,
      maxDrawdownPercent: perf.maxStrategyDrawDownPercent,
      sharpeRatio: perf.sharpeRatio,
      avgTrade: all.avgTrade,
      largestWin: all.largestWinTrade,
      largestLoss: all.largestLosTrade,
      signalCount: (v.trades || []).length
    };
    var trades = v.trades || [];
    if (trades.length) {
      var t0 = trades[0].e || {};
      summary.firstTrade = {side: t0.tp, price: t0.p};
    }
    var recent = [];
    for (var i = Math.max(0, trades.length - %d); i < trades.length; i++) {
      var t = trades[i];
      var e = t && t.e;
      if (!e) continue;
      recent.push({side: e.tp, price: e.p, time: e.tm || e.b || null, pnl: t.tp ? t.tp.v : null});
    }
    summary.recentSignals = recent;
    out = summary;
  } catch (e) { return JSON.stringify({err: String(e)}); }
  return JSON.stringify(out);
})()`, entityID, nSignals)

	res, err := c.runBDG(c.resolveBDGPath(), []string{"dom", "eval", script})
	if err != nil {
		return fmt.Errorf("read report: %w", err)
	}
	rawStr := strings.TrimSpace(string(res))
	var inner string
	if json.Unmarshal([]byte(rawStr), &inner) == nil {
		rawStr = strings.TrimSpace(inner)
	}
	var sum reportSummary
	if err := json.Unmarshal([]byte(rawStr), &sum); err != nil {
		fmt.Fprintln(env.Stderr, string(res))
		return nil
	}
	if sum.TotalTrades == 0 && sum.SignalCount == 0 {
		return fmt.Errorf("report empty for %s (is it a strategy?)", entityID)
	}

	if flags.Has("json") {
		b, _ := json.MarshalIndent(sum, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	fmt.Fprintf(env.Stdout, "Strategy backtest report — %s\n", entityID)
	fmt.Fprintf(env.Stdout, "  Net Profit:        %+.2f (%+.3f%%)\n", sum.NetProfit, sum.NetProfitPercent*100)
	fmt.Fprintf(env.Stdout, "  Gross Profit:      %.2f | Gross Loss: %.2f\n", sum.GrossProfit, sum.GrossLoss)
	fmt.Fprintf(env.Stdout, "  Profit Factor:     %.3f\n", sum.ProfitFactor)
	fmt.Fprintf(env.Stdout, "  Total Trades:      %d (win %d / loss %d, %.1f%% profitable)\n",
		sum.TotalTrades, sum.WinningTrades, sum.LosingTrades, sum.PercentProfitable*100)
	fmt.Fprintf(env.Stdout, "  Max Drawdown:      %.2f (%+.3f%%)\n", sum.MaxDrawdown, sum.MaxDrawdownPercent*100)
	fmt.Fprintf(env.Stdout, "  Sharpe Ratio:      %.2f\n", sum.SharpeRatio)
	fmt.Fprintf(env.Stdout, "  Avg Trade:         %+.2f | Largest Win: %.2f | Largest Loss: %.2f\n",
		sum.AvgTrade, sum.LargestWin, sum.LargestLoss)
	if len(sum.RecentSignals) > 0 {
		fmt.Fprintf(env.Stdout, "  Recent signals (last %d of %d):\n", len(sum.RecentSignals), sum.SignalCount)
		for _, s := range sum.RecentSignals {
			m, _ := s.(map[string]any)
			if m == nil {
				continue
			}
			side, _ := m["side"].(string)
			side = strings.ToUpper(side)
			price, _ := m["price"].(float64)
			pnl, _ := m["pnl"].(float64)
			tag := "BUY " // "le" = long entry
			if side == "SE" {
				tag = "SELL"
			}
			fmt.Fprintf(env.Stdout, "    %s @ %.2f  (pnl %+.2f)\n", tag, price, pnl)
		}
	}
	return nil
}

// runBDG executes bdg with the given args and returns its stdout.
func (c *studyCmd) runBDG(bdgPath string, args []string) ([]byte, error) {
	bdgParts := strings.Fields(bdgPath)
	cmd := exec.Command(bdgParts[0], append(bdgParts[1:], args...)...)
	cmd.Stderr = os.Stderr
	return cmd.Output()
}

// resolveBDGPath returns the bdg executable; when bdg isn't on PATH, the
// local dist entrypoint is used via `node <entry>`.
func (c *studyCmd) resolveBDGPath() string {
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

func (c *studyCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "study — List studies on the live chart, read/set their input values")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  tv study list                          List studies on the chart session")
	fmt.Fprintln(w, "  tv study inputs <entityId>            Read a study's current input values")
	fmt.Fprintln(w, "  tv study report <entityId>            Read a STRATEGY's backtest report + buy/sell signals")
	fmt.Fprintln(w, "  tv study set <entityId> --inputs '<json>'  Set inputs programmatically")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "For strategies (e.g. RSI Strategy E7gFVY) the output lives in the Strategy")
	fmt.Fprintln(w, "Tester bottom panel: performance summary (Net PnL, Profit Factor, Max")
	fmt.Fprintln(w, "Drawdown, ...) + the buy/sell trade list. 'tv study report' reads the same")
	fmt.Fprintln(w, "data the panel renders, from the study's _reportData (the payload the server")
	fmt.Fprintln(w, "recomputes via du after each modify_study) — combine with 'tv study set' for")
	fmt.Fprintln(w, "parameter sweeps: change an input, re-read the report, compare performance.")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "The set command sends modify_study on the WS (the same message the UI's")
	fmt.Fprintln(w, "settings dialog sends) and the server recomputes the study (du). The pane")
	fmt.Fprintln(w, "redraws — capture --before/--after screenshots to verify the visual change.")
	fmt.Fprintln(w, "Requires bdg attached to a chart tab:")
	fmt.Fprintln(w, "  node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  study list      --json           JSON output")
	fmt.Fprintln(w, "  study inputs    --json           JSON output")
	fmt.Fprintln(w, "  study report    --signals N      Recent buy/sell signals to show (default 6)")
	fmt.Fprintln(w, "                  --json           JSON output")
	fmt.Fprintln(w, "  study set       --inputs '<json>'   e.g. '{\"in_0\": 7}' (id or title matched)")
	fmt.Fprintln(w, "                  --before a.png    Screenshot before the change")
	fmt.Fprintln(w, "                  --after b.png     Screenshot after the change")
	fmt.Fprintln(w, "                  --verbose         Show what is being called")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv study list")
	fmt.Fprintln(w, "  tv study inputs E7gFVY")
	fmt.Fprintln(w, "  tv study report E7gFVY --signals 8")
	fmt.Fprintln(w, "  tv study set E7gFVY --inputs '{\"in_0\": 7}' --before a.png --after b.png")
	fmt.Fprintln(w, "  # parameter sweep:")
	fmt.Fprintln(w, "  for n in 5 7 9 14; do tv study set E7gFVY --inputs \"{\\\"in_0\\\": $n}\" >/dev/null && echo -n \"in_0=$n \" && tv study report E7gFVY --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f\"net={d[\\\"netProfit\\\"]:.0f} pf={d[\\\"profitFactor\\\"]:.2f} trades={d[\\\"totalTrades\\\"]}\")'; done")
}