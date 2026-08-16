// Package cmd: eval command — run arbitrary Pine Script source without
// a pre-published pineId. Compiles via translate_light, optionally saves
// the script via SaveNew to get metaInfo, then runs it on the WS study path.
package cmd

import (
	"context"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"time"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/service"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/runner"
	"github.com/ch99q/tvcli/pkg/schema"
	"github.com/ch99q/tvcli/pkg/tradingview"
)

// evalReservedKeys are flags consumed by eval itself — not passed as indicator inputs.
var evalReservedKeys = append(append([]string{}, ReservedRunKeys...),
	"compile-only", "script", "agent",
)

type evalCmd struct{ app *App }

func (c *evalCmd) Name() string      { return "eval" }
func (c *evalCmd) Aliases() []string { return nil }
func (c *evalCmd) Synopsis() string {
	return "Run arbitrary Pine Script source (no pre-published pineId needed)"
}

// sanitizeForJSON recursively walks a value and replaces NaN/Inf floats with 0.
// Go's encoding/json cannot marshal NaN or Inf, so we must sanitize before
// calling emitJSON on agent-ready results.
func sanitizeForJSON(v any) any {
	if v == nil {
		return nil
	}
	switch x := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, val := range x {
			out[k] = sanitizeForJSON(val)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = sanitizeForJSON(val)
		}
		return out
	case []map[string]any:
		out := make([]map[string]any, len(x))
		for i, val := range x {
			out[i] = sanitizeForJSON(val).(map[string]any)
		}
		return out
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return 0.0
		}
		return x
	case float32:
		if math.IsNaN(float64(x)) || math.IsInf(float64(x), 0) {
			return 0.0
		}
		return x
	default:
		return sanitizeWithReflection(v)
	}
}

// sanitizeWithReflection uses reflection to walk structs and replace NaN/Inf floats.
func sanitizeWithReflection(v any) any {
	val := reflect.ValueOf(v)
	// Dereference pointer if needed
	if val.Kind() == reflect.Ptr {
		if val.IsNil() {
			return nil
		}
		val = val.Elem()
	}
	switch val.Kind() {
	case reflect.Struct:
		t := val.Type()
		out := make(map[string]any)
		for i := 0; i < val.NumField(); i++ {
			fieldVal := val.Field(i)
			fieldName := t.Field(i).Name
			if !fieldVal.CanInterface() {
				continue
			}
			out[fieldName] = sanitizeForJSON(fieldVal.Interface())
		}
		return out
	case reflect.Slice, reflect.Array:
		out := make([]any, val.Len())
		for i := 0; i < val.Len(); i++ {
			out[i] = sanitizeForJSON(val.Index(i).Interface())
		}
		return out
	case reflect.Map:
		out := make(map[string]any)
		iter := val.MapRange()
		for iter.Next() {
			k := iter.Key().String()
			out[k] = sanitizeForJSON(iter.Value().Interface())
		}
		return out
	case reflect.Float64:
		f := val.Float()
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return 0.0
		}
		return f
	case reflect.Float32:
		f := val.Float()
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return 0.0
		}
		return float32(f)
	default:
		return v
	}
}

func (c *evalCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	// --- Resolve script source ------------------------------------------------
	source := flags.Get("script")
	if source == "" {
		if len(flags.Positional) == 0 {
			return fmt.Errorf(`usage: eval <file.pine> [options]  OR  eval --script '//@version=5 ...' [options]`)
		}
		path := flags.Positional[0]
		abs, err := filepath.Abs(path)
		if err != nil {
			return fmt.Errorf("resolve path: %w", err)
		}
		data, err := os.ReadFile(abs)
		if err != nil {
			return fmt.Errorf("read file %s: %w", path, err)
		}
		source = string(data)
	}
	if source == "" {
		return fmt.Errorf("script source is empty")
	}

	// --- Compile to validate syntax ------------------------------------------
	pfClient := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	compileResp, err := pfClient.Compile(source, cfg.CookieHeaderOrEmpty())
	if err != nil {
		return fmt.Errorf("compile: %w", err)
	}
	fmt.Fprintf(env.Stderr, "✓ Compiled\n")

	// Quick success check.
	if m, ok := compileResp.(map[string]any); ok {
		if s, ok := m["success"]; ok && s == false {
			return fmt.Errorf("compile failed: %v", m)
		}
	}

	// --- --compile-only: output compile result and exit ----------------------
	if flags.Has("compile-only") {
		result := map[string]any{
			"success":    true,
			"source":     source,
			"sourceHash": pinefacade.SHA256(source),
		}
		if m, ok := compileResp.(map[string]any); ok {
			result["compileResult"] = m
		}
		emitJSON(env, result, flags.Get("out"))
		return nil
	}

	// --- Resolve symbol / timeframe / bars -----------------------------------
	symbol := flags.Get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalized, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		return fmt.Errorf("invalid symbol %q: %w\nUse --symbol EXCHANGE:SYMBOL (e.g. OANDA:XAUUSD, BINANCE:BTCUSDT)", symbol, err)
	}
	symbol = normalized

	tf := flags.Get("tf")
	if tf == "" {
		tf = flags.Get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}

	limits := config.GetTierLimits()
	bars := flags.GetInt("bars", 500)
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		fmt.Fprintf(env.Stderr, "⚠ Capping bars from %d to %d (tier limit)\n", bars, limits.MaxBars)
		bars = limits.MaxBars
	}

	fmt.Fprintf(env.Stdout, "Evaluating script on %s @ %s, bars=%d\n", symbol, tf, bars)

	// --- Create temp script via SaveNew, get pineId, fetch metaInfo -----------
	tempName := "agent_eval_" + pinefacade.SHA256(source)[:12]
	var pineID string
	var metaInfo map[string]any
	var savedScript bool

	saveResp, saveErr := pfClient.SaveNew(source, tempName, cfg.CookieHeaderOrEmpty())
	if saveErr == nil {
		if pid := extractPineIDFromSave(saveResp); pid != "" {
			pineID = pid
			savedScript = true
			fmt.Fprintf(env.Stderr, "✓ Temp script saved: %s\n", pineID)
			if fetched, ferr := pfClient.Get(pineID, "last", cfg.CookieHeaderOrEmpty()); ferr == nil && fetched.MetaInfo != nil {
				metaInfo = fetched.MetaInfo
			}
		}
	}
	if pineID == "" {
		if saveErr != nil {
			fmt.Fprintf(env.Stderr, "⚠ SaveNew failed (%v), running with synthetic pineId (no metaInfo/schema)\n", saveErr)
		} else {
			fmt.Fprintf(env.Stderr, "⚠ Could not extract pineId from SaveNew response, running with synthetic pineId\n")
		}
		pineID = "USER;eval" + pinefacade.SHA256(source)[:12]
	}

	// Cleanup temp script when done.
	if savedScript {
		defer func() {
			if _, derr := pfClient.Delete(pineID, cfg.CookieHeaderOrEmpty()); derr != nil {
				fmt.Fprintf(env.Stderr, "⚠ Failed to delete temp script %s: %v\n", pineID, derr)
			} else {
				fmt.Fprintf(env.Stderr, "✓ Temp script deleted: %s\n", pineID)
			}
		}()
	}

	// --- Build indicator and run via WS ---------------------------------------
	indicatorOpts := map[string]any{
		"pineId": pineID,
		"script": source,
	}
	if metaInfo != nil {
		indicatorOpts["metaInfo"] = metaInfo
		if pine, ok := metaInfo["pine"].(map[string]any); ok {
			if v, ok := pine["version"].(string); ok {
				indicatorOpts["pineVersion"] = v
			}
		}
	} else {
		indicatorOpts["metaInfo"] = map[string]any{"inputs": []any{}}
	}
	indicator := tradingview.NewPineIndicator(indicatorOpts)

	// Collect Pine input overrides from every supported spelling and apply them
	// to the locally-built indicator (for diagnostics) and to the run request.
	// collectInputs merges --input k=v, --input.k=v, positional "k=v" args
	// after the script path, and raw --in_N=v flags into one map.
	inputOverrides := collectInputs(env.Flags, 1, evalReservedKeys)
	for k, v := range inputOverrides {
		if err := indicator.SetOption(k, v); err != nil {
			fmt.Fprintf(env.Stderr, "⚠ Input '%s': %v\n", k, err)
		}
	}
	fmt.Fprintf(env.Stderr, "Indicator loaded: %d inputs defined\n", len(indicator.Inputs))

	// Build schema from metaInfo if available.
	var sch *schema.PineSchema
	if metaInfo != nil {
		sch = schema.FromMetaInfo(pineID, metaInfo)
	}

	// Run via WS.
	res, err := service.RunScript(context.Background(), cfg, service.RunRequest{
		PineID:       pineID,
		Symbol:       symbol,
		Timeframe:    tf,
		Bars:         bars,
		Inputs:       inputOverrides,
		ReservedKeys: evalReservedKeys,
		SettleMs:     flags.GetInt("settle", 1500),
		ForceCleanup: flags.Has("force-cleanup") || flags.Has("cleanup"),
		CalcTimeout:  time.Duration(limits.CalcTimeoutSecs) * time.Second,
		Debug:        cfg.Debug,
	})
	if err != nil {
		return fmt.Errorf("run: %w", err)
	}

	periods := res.Periods
	graphicData := res.Graphic
	stratReport := res.StrategyReport

	// --- Output ---------------------------------------------------------------
	if flags.Has("raw") || flags.Has("raw-out") {
		emitRawEval(env, pineID, symbol, tf, bars, periods, graphicData, stratReport)
		return nil
	}

	if flags.Has("signals") || flags.Has("agent") {
		signals := runner.ExtractSignals(periods, graphicData, stratReport, tf, pineID, symbol, sch)
		if flags.Has("agent") {
			workflow := "eval"
			if sch != nil && sch.Name != "" {
				workflow = sch.Name
			}
			start := time.Now()
			result := signalsToAgent(signals, workflow, symbol, tf, time.Since(start).Milliseconds())
			// Sanitize NaN/Inf floats before JSON marshal.
			sanitized := sanitizeForJSON(result)
			emitJSON(env, sanitized, flags.Get("out"))
		} else if flags.Has("json") {
			emitJSON(env, sanitizeForJSON(signals), flags.Get("out"))
		} else {
			emitText(env, signals.Compact(), flags.Get("out"))
		}
		return nil
	}

	result := runner.ParseOutput(periods, graphicData, stratReport, tf, pineID, sch)
	output := runner.FormatResults(result, flags.Has("json"))
	fmt.Fprintln(env.Stdout, output)

	if outFile := flags.Get("out"); outFile != "" {
		os.WriteFile(outFile, []byte(output), 0644)
		fmt.Fprintf(env.Stdout, "✓ Saved: %s\n", outFile)
	}
	return nil
}

// extractPineIDFromSave tries to find a pineId in the SaveNew response.
func extractPineIDFromSave(resp any) string {
	if pid := ExtractPineID(resp); pid != "" {
		return pid
	}
	if m, ok := resp.(map[string]any); ok {
		if inner, ok := m["response"].(map[string]any); ok {
			if pid := ExtractPineID(inner); pid != "" {
				return pid
			}
		}
	}
	return ""
}

// emitRawEval dumps the raw periods/graphic/strategyReport as JSON.
func emitRawEval(env *cli.Env, pineID, symbol, tf string, bars int, periods []map[string]any, graphic map[string]map[string]any, stratReport map[string]any) {
	payload := map[string]any{
		"pineId":         pineID,
		"symbol":         symbol,
		"timeframe":      tf,
		"bars":           bars,
		"periodCount":    len(periods),
		"periods":        periods,
		"graphic":        graphic,
		"strategyReport": stratReport,
	}
	emitJSON(env, payload, "")
}
