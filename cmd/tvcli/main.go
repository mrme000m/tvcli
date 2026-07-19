package main

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/runner"
	"github.com/ch99q/tvcli/pkg/tradingview"
	"gopkg.in/yaml.v3"
)

// runMu serializes all `tv run` invocations — only one study can be active
// per TradingView subscription, so concurrent runs would hit the study limit.
var runMu sync.Mutex

// Subscription tier limits (from tradingview.com/pricing/)
type TierLimits struct {
	MaxCharts       int // charts per tab
	MaxIndicators   int // indicators per chart
	MaxConnections  int // simultaneous WebSocket connections
	MaxBars         int // historical bars (minute)
	CalcTimeoutSecs int // calculation time limit
}

var tiers = map[string]TierLimits{
	"free":      {1, 2, 2, 180, 20},
	"essential": {2, 5, 10, 365, 40},
	"plus":      {4, 10, 20, 0, 40},   // 0 = unlimited
	"premium":   {8, 25, 50, 0, 40},
	"ultimate":  {16, 50, 200, 0, 100},
}

func getTierLimits() TierLimits {
	tier := os.Getenv("TV_TIER")
	if tier == "" {
		tier = "free"
	}
	if l, ok := tiers[tier]; ok {
		return l
	}
	return tiers["free"]
}

func main() {
	cfg := config.Load()
	args := os.Args[1:]

	if len(args) == 0 {
		printHelp()
		return
	}

	cmd := args[0]
	flags := parseFlags(args[1:])

	if cfg.Debug {
		fmt.Fprintf(os.Stderr, "[debug] auth: %s\n", cfg.AuthSummary())
	}

	// Write operations require auth
	writeCmds := map[string]bool{
		"create": true, "new": true, "push": true, "delete": true, "rm": true,
	}
	if writeCmds[cmd] && !cfg.HasAuth() {
		fatal("Write operation '%s' requires SESSION/SIGNATURE cookies.\n"+
			"Extract them from your browser and set in .env:\n"+
			"  SESSION=<sessionid cookie>\n"+
			"  SIGNATURE=<sessionid_sign cookie>\n"+
			"  TV_USER=<your TradingView username>", cmd)
	}

	switch cmd {
	case "list", "ls":
		cmdList(cfg, flags)
	case "create", "new":
		cmdCreate(cfg, flags)
	case "pull":
		cmdPull(cfg, flags)
	case "push":
		cmdPush(cfg, flags)
	case "delete", "rm":
		cmdDelete(cfg, flags)
	case "search", "find":
		cmdSearch(cfg, flags)
	case "publist", "pl":
		cmdPubList(cfg, flags)
	case "top":
		cmdTop(cfg, flags)
	case "compile", "check":
		cmdCompile(cfg, flags)
	case "run":
		cmdRun(cfg, flags)
	case "fetch", "ohlcv":
		cmdFetch(cfg, flags)
	case "sync":
		cmdSync(cfg, flags)
	case "help", "--help", "-h":
		printHelp()
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", cmd)
		printHelp()
		os.Exit(1)
	}
}

func cmdList(cfg *config.Config, flags flagSet) {
	store, err := loadMetaStore(cfg)
	if err != nil {
		fatal("Failed to load metadata: %v", err)
	}

	if flags.has("public") || flags.has("p") {
		cmdPubList(cfg, flags)
		return
	}

	if flags.has("remote") || flags.has("r") {
		client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
		data, err := client.ListSaved(cfg.CookieHeader())
		if err != nil {
			fatal("Failed to list remote scripts: %v", err)
		}
		b, _ := json.MarshalIndent(data, "", "  ")
		fmt.Println(string(b))
		return
	}

	scripts := store.listScripts()
	if len(scripts) == 0 {
		fmt.Println("No scripts tracked. Use \"create\" to add one.")
		return
	}

	fmt.Println("\nTracked Scripts:")
	fmt.Println("================")
	for _, s := range scripts {
		status := "!"
		if s.RemoteHash == s.LocalHash {
			status = "✓"
		}
		fmt.Printf("  %s #%-3s | %s\n", status, s.ID, s.Name)
		fmt.Printf("         pineId: %s\n", s.PineID)
		if s.LocalPath != "" {
			fmt.Printf("         local:  %s\n", s.LocalPath)
		}
		if s.RemoteVersion != "" {
			fmt.Printf("         version: %s\n", s.RemoteVersion)
		}
		fmt.Println()
	}
}

func cmdCreate(cfg *config.Config, flags flagSet) {
	if len(flags.positional) == 0 {
		fatal("Usage: create <file.pine> [--name \"Script Name\"]")
	}
	filePath := flags.positional[0]

	absPath, _ := filepath.Abs(filePath)
	source, err := os.ReadFile(absPath)
	if err != nil {
		fatal("File not found: %s", filePath)
	}
	sourceStr := string(source)

	store, err := loadMetaStore(cfg)
	if err != nil {
		fatal("Failed to load metadata: %v", err)
	}

	// Check if already tracked
	if existing := store.findByLocalPath(absPath); existing != nil {
		fmt.Printf("Script already tracked as #%s. Use \"push\" to update.\n", existing.ID)
		return
	}

	// Compile first
	fmt.Println("Compiling...")
	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	compileRes, err := client.Compile(sourceStr, cfg.CookieHeader())
	if err != nil {
		fatal("Compile error: %v", err)
	}
	if cr, ok := compileRes.(map[string]any); ok {
		if success, ok := cr["success"].(bool); ok && !success {
			fmt.Println("Compilation failed:")
			if result, ok := cr["result"].(map[string]any); ok {
				if errors, ok := result["errors"].([]any); ok {
					for i, e := range errors {
						if i >= 5 {
							break
						}
						fmt.Printf("  %v\n", e)
					}
				}
			}
			fatal("Fix compilation errors before creating.")
		}
	}
	fmt.Println("✓ Compiled")

	// Create on remote
	name := flags.get("name")
	if name == "" {
		base := filepath.Base(absPath)
		name = strings.TrimSuffix(base, filepath.Ext(base))
	}
	fmt.Printf("Creating remote script: %s\n", name)

	createRes, err := client.SaveNew(sourceStr, name, cfg.CookieHeader())
	if err != nil {
		fatal("Create error: %v", err)
	}

	// Extract pineId from response
	pineID := extractPineID(createRes)
	if pineID == "" {
		fmt.Printf("Response: %v\n", createRes)
		fatal("Could not extract pineId from create response")
	}
	pineID = strings.ReplaceAll(pineID, "USER;USER;", "USER;")

	fmt.Printf("✓ Created: %s\n", pineID)

	// Track locally
	id := store.nextID()
	store.setScript(id, metaEntry{
		Name:          name,
		PineID:        pineID,
		LocalPath:     relPath(cfg, absPath),
		LocalHash:     pinefacade.SHA256(sourceStr),
		RemoteHash:    pinefacade.SHA256(sourceStr),
		RemoteVersion: "1.0",
	})

	// Generate inputs YAML
	inputsData := pinefacade.GenerateInputsYAML(sourceStr, name, pineID)
	inputsPath := filepath.Join(cfg.DataDir, "inputs", name+"_inputs.yaml")
	os.MkdirAll(filepath.Dir(inputsPath), 0755)
	b, _ := yaml.Marshal(inputsData)
	os.WriteFile(inputsPath, b, 0644)
	fmt.Printf("✓ Generated: %s\n", inputsPath)

	fmt.Printf("\n✓ Created script #%s\n", id)
}

func cmdPull(cfg *config.Config, flags flagSet) {
	store, err := loadMetaStore(cfg)
	if err != nil {
		fatal("Failed to load metadata: %v", err)
	}

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	var pineID, localPath, scriptName string

	if len(flags.positional) > 0 {
		target := flags.positional[0]
		if isNumeric(target) {
			entry := store.getScript(target)
			if entry == nil || entry.PineID == "" {
				fatal("No pineId for #%s", target)
			}
			pineID = entry.PineID
			localPath = entry.LocalPath
			scriptName = entry.Name
		} else if pinefacade.LooksLikePineID(target) {
			pineID = pinefacade.NormalizePineID(target)
			if existing := store.findByPineID(pineID); existing != nil {
				localPath = existing.LocalPath
				scriptName = existing.Name
			}
		} else {
			fatal("Unknown target: %s. Use numeric ID or pineId.", target)
		}
	}

	fmt.Printf("Pulling %s...\n", pineID)
	result, err := client.Get(pineID, "last", cfg.CookieHeader())
	if err != nil {
		fatal("Failed to fetch %s: %v", pineID, err)
	}

	if result.Source == "" {
		fatal("Pulled empty source")
	}

	if scriptName == "" && result.Meta != nil {
		scriptName = result.Meta.ScriptName
	}
	if scriptName == "" {
		scriptName = "script"
	}

	if localPath == "" {
		id := store.nextID()
		fileName := id + "--" + slugify(scriptName) + ".pine"
		store.setScript(id, metaEntry{
			Name:          scriptName,
			PineID:        pineID,
			LocalPath:     fileName,
			LocalHash:     pinefacade.SHA256(result.Source),
			RemoteHash:    pinefacade.SHA256(result.Source),
			RemoteVersion: result.Meta.Version,
		})
		localPath = fileName
		fmt.Printf("✓ Tracked as #%s\n", id)
	}

	absPath := filepath.Join(cfg.DataDir, localPath)
	os.MkdirAll(filepath.Dir(absPath), 0755)
	os.WriteFile(absPath, []byte(result.Source), 0644)
	fmt.Printf("✓ Saved: %s\n", localPath)
}

func cmdPush(cfg *config.Config, flags flagSet) {
	if len(flags.positional) == 0 {
		fatal("Usage: push <id|file> [--force]")
	}

	store, err := loadMetaStore(cfg)
	if err != nil {
		fatal("Failed to load metadata: %v", err)
	}

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	target := flags.positional[0]
	force := flags.has("force")

	var id, pineID, localPath string
	var entry *metaEntry

	if isNumeric(target) {
		id = target
		entry = store.getScript(id)
		if entry == nil {
			fatal("No script #%s", id)
		}
		pineID = entry.PineID
		localPath = entry.LocalPath
	} else {
		localPath, _ = filepath.Abs(target)
		entry = store.findByLocalPath(localPath)
		if entry != nil {
			id = entry.ID
			pineID = entry.PineID
		}
	}

	if pineID == "" {
		source, _ := os.ReadFile(localPath)
		pineID = pinefacade.ExtractPineIDFromSource(string(source))
	}
	if pineID == "" {
		fatal("No pineId found. Use \"create\" first.")
	}

	source, _ := os.ReadFile(localPath)
	sourceStr := string(source)
	localHash := pinefacade.SHA256(sourceStr)

	if !force && entry != nil && entry.RemoteHash == localHash {
		fmt.Println("No changes to push. Use --force to push anyway.")
		return
	}

	fmt.Println("Compiling...")
	compileRes, err := client.Compile(sourceStr, cfg.CookieHeader())
	if err != nil {
		fatal("Compile error: %v", err)
	}
	if cr, ok := compileRes.(map[string]any); ok {
		if success, ok := cr["success"].(bool); ok && !success {
			fatal("Compilation failed")
		}
	}
	fmt.Println("✓ Compiled")

	fmt.Println("Pushing...")
	pushRes, err := client.SaveNext(pineID, sourceStr, cfg.CookieHeader())
	if err != nil {
		fatal("Push error: %v", err)
	}

	pushedPine := extractPineID(pushRes)
	if pushedPine == "" {
		pushedPine = pineID
	}
	pushedPine = strings.ReplaceAll(pushedPine, "USER;USER;", "USER;")

	version := extractVersion(pushRes)
	fmt.Printf("✓ Pushed: %s (version: %s)\n", pushedPine, version)

	if id != "" {
		store.setScript(id, metaEntry{
			PineID:        pushedPine,
			LocalHash:     localHash,
			RemoteHash:    localHash,
			RemoteVersion: version,
		})
	}
}

func cmdDelete(cfg *config.Config, flags flagSet) {
	if len(flags.positional) == 0 {
		fatal("Usage: delete <id>")
	}

	store, err := loadMetaStore(cfg)
	if err != nil {
		fatal("Failed to load metadata: %v", err)
	}

	id := flags.positional[0]
	entry := store.getScript(id)
	if entry == nil {
		fatal("No script #%s", id)
	}

	if !flags.has("yes") && !flags.has("y") {
		fmt.Printf("This will delete remote script: %s\n", entry.PineID)
		fmt.Println("Run with --yes to confirm.")
		return
	}

	if entry.PineID != "" {
		client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
		_, err := client.Delete(entry.PineID, cfg.CookieHeader())
		if err != nil {
			fmt.Printf("Warning: Could not delete from remote: %v\n", err)
		} else {
			fmt.Println("✓ Deleted from remote")
		}
	}

	store.deleteScript(id)
	fmt.Printf("✓ Removed #%s from tracking\n", id)
}

func cmdSearch(cfg *config.Config, flags flagSet) {
	if len(flags.positional) == 0 {
		fatal("Usage: search <query> [--limit N] [--json]")
	}

	query := flags.positional[0]
	limit := flags.getInt("limit", 20)
	asJSON := flags.has("json")

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	data, err := client.SearchPublicScripts(query, "")
	if err != nil {
		fatal("Search failed: %v", err)
	}

	items := normalizeSearchResults(data, limit)

	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{
			"query":   query,
			"limit":   limit,
			"count":   len(items),
			"results": items,
		}, "", "  ")
		fmt.Println(string(b))
	} else {
		fmt.Printf("\nSearch '%s': %d results\n\n", query, len(items))
		printSearchTable(items)
	}
}

func cmdPubList(cfg *config.Config, flags flagSet) {
	offset := flags.getInt("offset", 0)
	limit := flags.getInt("limit", 20)
	asJSON := flags.has("json")

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	data, err := client.ListPublicScripts(offset)
	if err != nil {
		fatal("Failed to list public scripts: %v", err)
	}

	items := normalizeSearchResults(data, limit)

	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{
			"offset":  offset,
			"limit":   limit,
			"count":   len(items),
			"results": items,
		}, "", "  ")
		fmt.Println(string(b))
	} else {
		next := extractNext(data)
		fmt.Printf("\nPublic scripts: %d (offset=%d, next=%v)\n\n", len(items), offset, next)
		printSearchTable(items)
	}
}

func cmdTop(cfg *config.Config, flags flagSet) {
	limit := flags.getInt("limit", 100)
	output := flags.get("output")
	if output == "" {
		output = "top_scripts.json"
	}

	fmt.Printf("Fetching top %d scripts...\n", limit)

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)

	var allItems []map[string]any
	offset := 0
	batchSize := 20

	for len(allItems) < limit {
		data, err := client.ListPublicScripts(offset)
		if err != nil {
			fatal("Failed to fetch scripts at offset %d: %v", offset, err)
		}

		items := normalizeSearchResults(data, batchSize)
		if len(items) == 0 {
			break
		}

		allItems = append(allItems, items...)
		offset += batchSize
		fmt.Fprintf(os.Stderr, "  Fetched %d scripts...\n", len(allItems))

		if len(items) < batchSize {
			break
		}
	}

	if len(allItems) > limit {
		allItems = allItems[:limit]
	}

	payload := map[string]any{
		"total":   len(allItems),
		"scripts": allItems,
	}
	b, _ := json.MarshalIndent(payload, "", "  ")
	os.WriteFile(output, b, 0644)
	fmt.Printf("\n✓ Saved %d scripts to %s\n", len(allItems), output)
}

func normalizeSearchResults(data any, limit int) []map[string]any {
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
			"title":        firstNonEmptyStr(item, "title", "scriptName"),
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

func firstNonEmptyStr(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := m[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

func extractNext(data any) any {
	if m, ok := data.(map[string]any); ok {
		return m["next"]
	}
	return nil
}

func printSearchTable(items []map[string]any) {
	for i, it := range items {
		author := ""
		if a, ok := it["author"].(map[string]any); ok {
			author, _ = a["username"].(string)
		}
		title, _ := it["title"].(string)
		id, _ := it["scriptIdPart"].(string)
		typ, _ := it["type"].(string)
		access, _ := it["access"].(string)

		fmt.Printf("%3d. %s\n", i+1, title)
		fmt.Printf("     id: %s\n", id)
		fmt.Printf("     author: %s | type: %s | access: %s\n", author, typ, access)
	}
}

func cmdCompile(cfg *config.Config, flags flagSet) {
	if len(flags.positional) == 0 {
		fatal("Usage: compile <file.pine>")
	}

	filePath := flags.positional[0]
	source, err := os.ReadFile(filePath)
	if err != nil {
		fatal("File not found: %s", filePath)
	}

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	res, err := client.Compile(string(source), cfg.CookieHeader())
	if err != nil {
		fatal("Compile error: %v", err)
	}

	b, _ := json.MarshalIndent(res, "", "  ")
	fmt.Println(string(b))
}

func cmdRun(cfg *config.Config, flags flagSet) {
	if len(flags.positional) == 0 {
		fatal("Usage: run <pineId> [--symbol X] [--tf 5m] [--bars 500] [--json] [--raw] [--out F] [--raw-out F] [--signals] [--force-cleanup]")
	}

	// --persistent / --loop uses the persistent runner (reuses WS connection)
	if flags.has("persistent") || flags.has("loop") {
		cmdRunPersistent(cfg, flags)
		return
	}

	// Serialize runs — only one study per chart on TradingView
	runMu.Lock()
	defer runMu.Unlock()

	// Load tier limits
	limits := getTierLimits()
	tier := os.Getenv("TV_TIER")
	if tier == "" {
		tier = "free"
	}
	forceCleanup := flags.has("force-cleanup") || flags.has("cleanup")

	fmt.Fprintf(os.Stderr, "Tier: %s (max %d charts, %d indicators/chart, %ds calc)\n",
		tier, limits.MaxCharts, limits.MaxIndicators, limits.CalcTimeoutSecs)
	if forceCleanup {
		fmt.Fprintf(os.Stderr, "⚠ Force cleanup mode: will aggressively try to free studies\n")
	}

	pineID := flags.positional[0]
	if !pinefacade.LooksLikePineID(pineID) {
		store, _ := loadMetaStore(cfg)
		entry := store.getScript(pineID)
		if entry != nil {
			pineID = entry.PineID
		}
	}

	symbol := flags.get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}

	// Validate and normalize symbol format
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		fatal("Invalid symbol: %v\n\nTradingView requires the exchange prefix format:\n"+
			"  Forex:    OANDA:XAUUSD, OANDA:EURUSD\n"+
			"  Crypto:   BINANCE:BTCUSDT, COINBASE:BTCUSD\n"+
			"  Stocks:   NASDAQ:AAPL, NYSE:TSLA\n"+
			"\nUse --symbol EXCHANGE:SYMBOL", err)
	}
	symbol = normalizedSymbol

	tf := flags.get("tf")
	if tf == "" {
		tf = flags.get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.getInt("bars", 500)

	// Cap bars to tier limit
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		fmt.Fprintf(os.Stderr, "⚠ Capping bars from %d to %d (tier limit)\n", bars, limits.MaxBars)
		bars = limits.MaxBars
	}

	fmt.Printf("Running %s\n", pineID)
	fmt.Printf("  Symbol: %s @ %s, range=%d\n", symbol, tf, bars)

	// Warn about web UI interference
	if !forceCleanup {
		fmt.Fprintf(os.Stderr, "\n⚠ If this fails with 'study limit' error, your TradingView web UI likely has\n")
		fmt.Fprintf(os.Stderr, "  indicators loaded that count against your %d indicator limit.\n", limits.MaxIndicators)
		fmt.Fprintf(os.Stderr, "  Close charts in your browser or use --force-cleanup to retry.\n\n")
	}

	// Load indicator metadata via /translate/ to get full metaInfo (inputs, plots, script)
	pineClient := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	indResult, err := pineClient.Get(pineID, "last", cfg.CookieHeaderOrEmpty())
	if err != nil {
		fatal("Failed to load indicator: %v", err)
	}
	if indResult.Source == "" {
		fatal("Indicator returned empty source for %s", pineID)
	}

	if cfg.Debug {
		fmt.Fprintf(os.Stderr, "[debug] source length: %d, metaInfo present: %v\n",
			len(indResult.Source), indResult.MetaInfo != nil)
		if indResult.MetaInfo != nil {
			if inputs, ok := indResult.MetaInfo["inputs"].([]any); ok {
				fmt.Fprintf(os.Stderr, "[debug] metaInfo.inputs count: %d\n", len(inputs))
			}
		}
	}

	// Build indicator with FULL metaInfo from Pine Facade (matching JS getIndicator())
	indicatorOpts := map[string]any{
		"pineId": pineID,
		"script": indResult.Source,
	}
	// Pass full metaInfo so NewPineIndicator can extract inputs/definitions
	if indResult.MetaInfo != nil {
		indicatorOpts["metaInfo"] = indResult.MetaInfo
		// Extract pine version from metaInfo (JS: meta.pine?.version)
		if pine, ok := indResult.MetaInfo["pine"].(map[string]any); ok {
			if v, ok := pine["version"].(string); ok {
				indicatorOpts["pineVersion"] = v
			}
		}
	} else {
		indicatorOpts["metaInfo"] = map[string]any{"inputs": []any{}}
	}

	indicator := tradingview.NewPineIndicator(indicatorOpts)

	fmt.Fprintf(os.Stderr, "Indicator loaded: %d inputs defined\n", len(indicator.Inputs))

	// Apply custom inputs from flags
	for k, v := range flags.flags {
		if k == "symbol" || k == "tf" || k == "timeframe" || k == "bars" || k == "json" || k == "out" || k == "force-cleanup" || k == "cleanup" || k == "raw" || k == "raw-out" || k == "signals" || k == "settle" || k == "schema" {
			continue
		}
		if err := indicator.SetOption(k, v); err != nil {
			fmt.Fprintf(os.Stderr, "⚠ Input '%s': %v\n", k, err)
		}
	}

	// --schema: dump the parsed metaInfo schema and exit
	if flags.has("schema") {
		if indicator.Schema != nil {
			fmt.Println(indicator.Schema.Summary())
			if flags.has("json") {
				b, _ := json.MarshalIndent(indicator.Schema, "", "  ")
				fmt.Println(string(b))
			}
		} else {
			fmt.Fprintf(os.Stderr, "No schema available for %s (metaInfo had no plots/styles)\n", pineID)
		}
		return
	}

	// Connect fresh — disconnect first to release any stale sessions
	var client *tradingview.Client
	connectFresh := func() error {
		if client != nil {
			client.Close()
		}
		client = tradingview.NewClient(
			tradingview.WithToken(cfg.SessionID),
			tradingview.WithSignature(cfg.Signature),
			tradingview.WithDebug(cfg.Debug),
		)
		if err := client.Connect(); err != nil {
			return fmt.Errorf("ws connect: %w", err)
		}
		if !client.WaitForConnected(10 * time.Second) {
			return fmt.Errorf("ws timeout")
		}
		return nil
	}

	if err := connectFresh(); err != nil {
		fatal("WebSocket connect failed: %v", err)
	}
	defer func() {
		if client != nil {
			client.Close()
		}
	}()

	// Helper to create a fresh chart session with symbol loaded
	createChart := func() (*tradingview.ChartSession, error) {
		ch := tradingview.NewChartSession(client)
		ch.OnError(func(err error) {
			fmt.Fprintf(os.Stderr, "Chart error: %v\n", err)
		})
		ch.SetMarket(symbol, map[string]any{
			"timeframe": pinefacade.NormalizeTimeframe(tf),
			"range":     bars,
		})
		if err := ch.WaitForSymbol(15 * time.Second); err != nil {
			return nil, fmt.Errorf("symbol load: %w", err)
		}
		if cfg.Debug {
			info := ch.GetSymbolInfo()
			fmt.Fprintf(os.Stderr, "[debug] chart session %s loaded, symbol info: %v\n", ch.GetSessionID(), info)
		}
		return ch, nil
	}

	chart, err := createChart()
	if err != nil {
		fatal("%v", err)
	}

	// Always destroy chart and clean up on exit
	defer func() {
		chart.RemoveAllStudies()
		chart.Delete()
	}()

	// Create study with retry (matches JS run-generic.cjs pattern)
	var periods []map[string]any
	var graphicData map[string]map[string]any
	var stratReport map[string]any

	calcTimeout := time.Duration(limits.CalcTimeoutSecs) * time.Second
	if calcTimeout == 0 {
		calcTimeout = 120 * time.Second
	}

	isStudyLimitError := func(err error) bool {
		if err == nil {
			return false
		}
		msg := err.Error()
		return strings.Contains(msg, "maximum number of studies") ||
			strings.Contains(msg, "too many") ||
			strings.Contains(msg, "study limit")
	}

	// Max attempts: 3 normal, 5 with force-cleanup
	maxAttempts := 3
	if forceCleanup {
		maxAttempts = 5
	}

	// Pre-cleanup: always disconnect and reconnect fresh to clear stale session state.
	// This ensures we start with a clean chart session every run.
	fmt.Fprintf(os.Stderr, "🧹 Pre-cleanup: fresh session...\n")
	chart.RemoveAllStudies()
	chart.Delete()
	time.Sleep(500 * time.Millisecond)
	if err := connectFresh(); err != nil {
		fatal("Pre-cleanup reconnect failed: %v", err)
	}
	chart, err = createChart()
	if err != nil {
		fatal("Pre-cleanup chart recreate failed: %v", err)
	}

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		// ALWAYS clean up existing studies before creating — only way to ensure we have room
		if existing := chart.GetStudies(); len(existing) > 0 {
			fmt.Fprintf(os.Stderr, "🧹 Cleaning %d existing study/studies on this session...\n", len(existing))
			chart.RemoveAllStudies()
			time.Sleep(500 * time.Millisecond)
		}

		if existing := chart.GetStudies(); len(existing) > 0 {
			fmt.Fprintf(os.Stderr, "📊 Session has %d existing study/studies: %v\n", len(existing), existing)
		} else {
			fmt.Fprintf(os.Stderr, "📊 Session has no existing studies\n")
		}

		study := chart.Study(indicator)

		done := make(chan struct{})
		var studyErr error
		once := sync.Once{}

		// TradingView often sends graphics/backfill in follow-up messages after the
		// first data update. Collect updates for a short settle window so we don't
		// quit while the payload is still streaming.
		settleMs := flags.getInt("settle", 1500)
		if settleMs <= 0 {
			settleMs = 1500
		}

		study.OnUpdate(func() {
			once.Do(func() {
				p := study.Periods()
				if len(p) > 0 {
					periods = p
					graphicData = study.Graphic()
					stratReport = study.StrategyReport()
					// Start settle timer on first successful update.
					go func() {
						timer := time.NewTimer(time.Duration(settleMs) * time.Millisecond)
						defer timer.Stop()
						select {
						case <-done:
							// already finished by error/timeout
						case <-timer.C:
							close(done)
						}
					}()
				}
			})
		})
		study.OnError(func(err error) {
			once.Do(func() {
				studyErr = err
				close(done)
			})
		})

		select {
		case <-done:
		case <-time.After(calcTimeout):
			studyErr = fmt.Errorf("timeout after %s waiting for study data", calcTimeout)
		}

		// Final snapshot after settle / timeout.
		periods = study.Periods()
		graphicData = study.Graphic()
		stratReport = study.StrategyReport()

		if studyErr == nil && len(periods) > 0 {
			study.Remove()
			fmt.Fprintf(os.Stderr, "✓ Study data received (%d periods, %d graphic types)\n", len(periods), len(graphicData))
			break
		}

		study.Remove()

		if isStudyLimitError(studyErr) && attempt < maxAttempts {
			fmt.Fprintf(os.Stderr, "⚠ Study limit hit (attempt %d/%d). Account-wide indicator limit exceeded.\n", attempt, maxAttempts)
			fmt.Fprintf(os.Stderr, "  Your TradingView web UI likely has indicators loaded (e.g., XAUUSD + ICT-V).\n")
			fmt.Fprintf(os.Stderr, "  These count against your account's %d indicator limit.\n", limits.MaxIndicators)
			fmt.Fprintf(os.Stderr, "\n  To fix this:\n")
			fmt.Fprintf(os.Stderr, "    1. Close charts with indicators in your TradingView browser tab\n")
			fmt.Fprintf(os.Stderr, "    2. Or use --force-cleanup to retry more aggressively\n")
			fmt.Fprintf(os.Stderr, "  Reconnecting in %ds...\n", attempt*3)

			// Full cleanup: destroy chart, disconnect, reconnect fresh, recreate chart
			chart.RemoveAllStudies()
			chart.Delete()
			time.Sleep(time.Duration(attempt*3) * time.Second)

			// Reconnect to release any stale session state
			if err := connectFresh(); err != nil {
				fatal("Reconnect failed: %v", err)
			}

			chart, err = createChart()
			if err != nil {
				fatal("Chart recreate failed: %v", err)
			}
			continue
		}

		if studyErr != nil {
			fatal("Study error: %v", studyErr)
		}
		break
	}

	if len(periods) == 0 {
		fatal("No data received from study")
	}

	// --raw: dump unprocessed capture (periods + graphic + strategyReport + meta)
	// for debugging. Destination priority: --raw-out <file>, else <out>.raw.json,
	// else stdout. The processed result is still emitted unless --json is absent
	// AND --raw wrote to stdout (avoids mixing raw JSON with human-readable text).
	if rawOut := flags.get("raw-out"); flags.has("raw") || rawOut != "" {
		rawPayload := map[string]any{
			"pineId":         pineID,
			"symbol":         symbol,
			"timeframe":      tf,
			"bars":           bars,
			"periodCount":    len(periods),
			"periods":        periods,
			"graphic":        graphicData,
			"strategyReport": stratReport,
		}
		rawJSON, _ := json.MarshalIndent(rawPayload, "", "  ")
		dest := ""
		switch {
		case rawOut != "" && rawOut != "true":
			dest = rawOut
		case flags.get("out") != "":
			dest = flags.get("out") + ".raw.json"
		}
		if dest != "" {
			os.WriteFile(dest, rawJSON, 0644)
			fmt.Fprintf(os.Stderr, "✓ Raw dump: %s\n", dest)
		} else {
			fmt.Println(string(rawJSON))
			// Raw JSON went to stdout; skip the processed result unless --json
			// was explicitly requested (in which case it follows on stdout too).
			if !flags.has("json") {
				return
			}
		}
	}

	if flags.has("signals") {
		signals := runner.ExtractSignals(periods, graphicData, stratReport, tf, pineID, symbol, indicator.Schema)
		if flags.has("json") {
			b, _ := json.MarshalIndent(signals, "", "  ")
			fmt.Println(string(b))
		} else {
			fmt.Println(signals.Compact())
		}
		if outFile := flags.get("out"); outFile != "" {
			b, _ := json.MarshalIndent(signals, "", "  ")
			os.WriteFile(outFile, b, 0644)
			fmt.Printf("✓ Saved: %s\n", outFile)
		}
		return
	}

	// --multi-run: generate and display input sweep configurations
	if flags.has("multi-run") || flags.has("sweep") {
		configs := runner.GenerateRunConfigs(indicator.Schema, nil)
		fmt.Fprintf(os.Stderr, "\n📊 Multi-Run: %d configurations generated\n\n", len(configs))
		for i, cfg := range configs {
			fmt.Fprintf(os.Stderr, "  %2d. %s\n", i+1, cfg.Label)
			if len(cfg.Inputs) > 0 {
				for k, v := range cfg.Inputs {
					fmt.Fprintf(os.Stderr, "      %s = %v\n", k, v)
				}
			}
		}
		if flags.has("json") {
			b, _ := json.MarshalIndent(configs, "", "  ")
			fmt.Println(string(b))
		}
		if outFile := flags.get("out"); outFile != "" {
			b, _ := json.MarshalIndent(configs, "", "  ")
			os.WriteFile(outFile, b, 0644)
			fmt.Printf("✓ Saved: %s\n", outFile)
		}
		return
	}

	result := runner.ParseOutput(periods, graphicData, stratReport, tf, pineID, indicator.Schema)
	output := runner.FormatResults(result, flags.has("json"))
	fmt.Println(output)

	if outFile := flags.get("out"); outFile != "" {
		os.WriteFile(outFile, []byte(output), 0644)
		fmt.Printf("✓ Saved: %s\n", outFile)
	}
}

// cmdRunPersistent runs an indicator using a persistent WS connection.
// With --loop <interval>, it re-runs the same indicator periodically.
func cmdRunPersistent(cfg *config.Config, flags flagSet) {
	pineID := flags.positional[0]
	if !pinefacade.LooksLikePineID(pineID) {
		store, _ := loadMetaStore(cfg)
		entry := store.getScript(pineID)
		if entry != nil {
			pineID = entry.PineID
		}
	}

	symbol := flags.get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		fatal("Invalid symbol: %v", err)
	}
	symbol = normalizedSymbol

	tf := flags.get("tf")
	if tf == "" {
		tf = flags.get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.getInt("bars", 500)

	limits := getTierLimits()
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		bars = limits.MaxBars
	}

	// Load indicator metadata
	pineClient := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
	indResult, err := pineClient.Get(pineID, "last", cfg.CookieHeaderOrEmpty())
	if err != nil {
		fatal("Failed to load indicator: %v", err)
	}
	if indResult.Source == "" {
		fatal("Indicator returned empty source for %s", pineID)
	}

	indicatorOpts := map[string]any{
		"pineId": pineID,
		"script": indResult.Source,
	}
	if indResult.MetaInfo != nil {
		indicatorOpts["metaInfo"] = indResult.MetaInfo
		if pine, ok := indResult.MetaInfo["pine"].(map[string]any); ok {
			if v, ok := pine["version"].(string); ok {
				indicatorOpts["pineVersion"] = v
			}
		}
	} else {
		indicatorOpts["metaInfo"] = map[string]any{"inputs": []any{}}
	}

	indicator := tradingview.NewPineIndicator(indicatorOpts)

	// Apply custom inputs
	for k, v := range flags.flags {
		reserved := map[string]bool{
			"symbol": true, "tf": true, "timeframe": true, "bars": true,
			"json": true, "out": true, "raw": true, "raw-out": true,
			"signals": true, "settle": true, "persistent": true, "loop": true,
			"force-cleanup": true, "cleanup": true,
		}
		if reserved[k] {
			continue
		}
		if err := indicator.SetOption(k, v); err != nil {
			fmt.Fprintf(os.Stderr, "⚠ Input '%s': %v\n", k, err)
		}
	}

	settleMs := flags.getInt("settle", 1500)
	if settleMs <= 0 {
		settleMs = 1500
	}
	calcTimeout := time.Duration(limits.CalcTimeoutSecs) * time.Second
	if calcTimeout == 0 {
		calcTimeout = 60 * time.Second
	}

	// Build persistent runner
	pr := runner.NewPersistentRunner(
		[]tradingview.ClientOption{
			tradingview.WithToken(cfg.SessionID),
			tradingview.WithSignature(cfg.Signature),
			tradingview.WithDebug(cfg.Debug),
		},
		cfg.Debug,
	)
	defer pr.Close()

	fmt.Fprintf(os.Stderr, "Persistent mode: WS connection will stay open\n")
	fmt.Fprintf(os.Stderr, "Running %s on %s @ %s (%d bars)\n", pineID, symbol, tf, bars)

	// Check for --loop interval
	loopInterval := 0
	if flags.has("loop") {
		loopStr := flags.get("loop")
		if loopStr == "" || loopStr == "true" {
			loopInterval = 300 // default 5 minutes
		} else {
			d, err := time.ParseDuration(loopStr)
			if err != nil {
				// Try parsing as seconds
				n := 0
				fmt.Sscanf(loopStr, "%d", &n)
				if n > 0 {
					loopInterval = n
				} else {
					fatal("Invalid loop interval: %s (use e.g. 30s, 5m, 1h)", loopStr)
				}
			} else {
				loopInterval = int(d.Seconds())
			}
		}
		fmt.Fprintf(os.Stderr, "Loop mode: re-running every %ds\n", loopInterval)
	}

	runCount := 0
	for {
		runCount++
		if loopInterval > 0 {
			fmt.Fprintf(os.Stderr, "\n--- Run #%d ---\n", runCount)
		}

		result, err := pr.Run(runner.RunOnceOptions{
			PineID:      pineID,
			Symbol:      symbol,
			Timeframe:   tf,
			Bars:        bars,
			Indicator:   indicator,
			SettleMs:    settleMs,
			CalcTimeout: calcTimeout,
			Debug:       cfg.Debug,
		})

		if err != nil {
			fmt.Fprintf(os.Stderr, "Run error: %v\n", err)
			if loopInterval == 0 {
				fatal("Run failed: %v", err)
			}
			// In loop mode, log error and continue
			fmt.Fprintf(os.Stderr, "Retrying in %ds...\n", loopInterval)
			time.Sleep(time.Duration(loopInterval) * time.Second)
			continue
		}

		if flags.has("signals") {
			if result.Extracted != nil {
				if flags.has("json") {
					b, _ := json.MarshalIndent(result.Extracted, "", "  ")
					fmt.Println(string(b))
				} else {
					fmt.Println(result.Extracted.Compact())
				}
			} else {
				fmt.Fprintf(os.Stderr, "No signals extracted\n")
			}
		} else if flags.has("json") {
			b, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(b))
		} else {
			output := runner.FormatResults(result, false)
			fmt.Println(output)
		}

		if outFile := flags.get("out"); outFile != "" {
			b, _ := json.MarshalIndent(result, "", "  ")
			os.WriteFile(outFile, b, 0644)
			fmt.Fprintf(os.Stderr, "✓ Saved: %s\n", outFile)
		}

		// Single run (no --loop)
		if loopInterval == 0 {
			break
		}

		fmt.Fprintf(os.Stderr, "Next run in %ds...\n", loopInterval)
		time.Sleep(time.Duration(loopInterval) * time.Second)
	}
}

func cmdFetch(cfg *config.Config, flags flagSet) {
	symbol := flags.get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		fatal("Invalid symbol: %v\n\nUse --symbol EXCHANGE:SYMBOL (e.g. OANDA:XAUUSD, BINANCE:BTCUSDT)", err)
	}
	symbol = normalizedSymbol

	tf := flags.get("tf")
	if tf == "" {
		tf = flags.get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.getInt("bars", 180) // free tier default

	limits := getTierLimits()
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		fmt.Fprintf(os.Stderr, "Capping bars from %d to %d (tier limit)\n", bars, limits.MaxBars)
		bars = limits.MaxBars
	}

	fmt.Fprintf(os.Stderr, "Fetching OHLCV: %s @ %s, %d bars\n", symbol, tf, bars)

	// Connect WS
	client := tradingview.NewClient(
		tradingview.WithToken(cfg.SessionID),
		tradingview.WithSignature(cfg.Signature),
		tradingview.WithDebug(cfg.Debug),
	)
	if err := client.Connect(); err != nil {
		fatal("WS connect: %v", err)
	}
	if !client.WaitForConnected(10 * time.Second) {
		fatal("WS timeout")
	}
	defer client.Close()

	// Create chart session — no study needed, just the s1 price series
	ch := tradingview.NewChartSession(client)
	ch.OnError(func(err error) {
		fmt.Fprintf(os.Stderr, "Chart error: %v\n", err)
	})

	// Wire OnUpdate BEFORE SetMarket so we catch the initial data batch
	done := make(chan struct{})
	once := sync.Once{}
	ch.OnUpdate(func() {
		once.Do(func() { close(done) })
	})

	ch.SetMarket(symbol, map[string]any{
		"timeframe": pinefacade.NormalizeTimeframe(tf),
		"range":     bars,
	})

	// Wait for symbol to resolve
	if err := ch.WaitForSymbol(15 * time.Second); err != nil {
		fatal("Symbol load: %v", err)
	}

	// Wait for OHLCV data to arrive
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		fatal("Timeout waiting for OHLCV data")
	}
	// Settle to catch follow-up bars
	time.Sleep(800 * time.Millisecond)

	periods := ch.Periods()
	if len(periods) == 0 {
		fatal("No OHLCV data received")
	}

	fmt.Fprintf(os.Stderr, "Received %d bars\n", len(periods))

	// Sort by time ascending for output
	sort.Slice(periods, func(i, j int) bool {
		t1, _ := periods[i]["time"].(float64)
		t2, _ := periods[j]["time"].(float64)
		return t1 < t2
	})

	// Build output filenames
	symbolClean := strings.ReplaceAll(symbol, ":", "_")
	baseName := fmt.Sprintf("%s_%s_%dbars", symbolClean, tf, bars)

	outDir := flags.get("dir")
	if outDir == "" {
		outDir = "."
	}
	os.MkdirAll(outDir, 0755)

	jsonPath := filepath.Join(outDir, baseName+".json")
	csvPath := filepath.Join(outDir, baseName+".csv")

	// --- JSON output ---
	jsonData := map[string]any{
		"symbol":    symbol,
		"timeframe": tf,
		"bars":      bars,
		"count":     len(periods),
		"fetchedAt": time.Now().UTC().Format(time.RFC3339),
		"data":      periods,
	}

	if outJSON := flags.get("json-out"); outJSON != "" {
		jsonPath = outJSON
	}
	jsonBytes, _ := json.MarshalIndent(jsonData, "", "  ")
	if err := os.WriteFile(jsonPath, jsonBytes, 0644); err != nil {
		fatal("Write JSON: %v", err)
	}
	fmt.Fprintf(os.Stderr, "  JSON: %s (%d bytes)\n", jsonPath, len(jsonBytes))

	// --- CSV output ---
	if outCSV := flags.get("csv-out"); outCSV != "" {
		csvPath = outCSV
	}
	csvFile, err := os.Create(csvPath)
	if err != nil {
		fatal("Create CSV: %v", err)
	}
	defer csvFile.Close()

	// CSV header
	fmt.Fprintln(csvFile, "time,open,high,low,close,volume")
	for _, bar := range periods {
		ts, _ := bar["time"].(float64)
		o, _ := bar["open"].(float64)
		h, _ := bar["high"].(float64)
		l, _ := bar["low"].(float64)
		c, _ := bar["close"].(float64)
		v, _ := bar["volume"].(float64)
		// Convert Unix timestamp to human-readable
		utcTime := time.Unix(int64(ts), 0).UTC().Format("2006-01-02T15:04:05Z")
		fmt.Fprintf(csvFile, "%s,%.8f,%.8f,%.8f,%.8f,%.2f\n", utcTime, o, h, l, c, v)
	}
	csvFile.Close()
	csvInfo, _ := os.Stat(csvPath)
	fmt.Fprintf(os.Stderr, "  CSV:  %s (%d bytes)\n", csvPath, csvInfo.Size())

	// Stdout: brief summary
	fmt.Printf("Fetched %d bars for %s @ %s\n", len(periods), symbol, tf)
}

// ohlcvBar is a single OHLCV bar for serialization.
type ohlcvBar struct {
	Time   float64 `json:"t"`
	Open   float64 `json:"o"`
	High   float64 `json:"h"`
	Low    float64 `json:"l"`
	Close  float64 `json:"c"`
	Volume float64 `json:"v"`
}

// ohlcvFile is the on-disk compressed format.
type ohlcvFile struct {
	Symbol    string     `json:"symbol"`
	Timeframe string     `json:"tf"`
	Count     int        `json:"count"`
	UpdatedAt string     `json:"updatedAt"`
	Data      []ohlcvBar `json:"data"`
}

func loadOHLCV(path string) (*ohlcvFile, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	gz, err := gzip.NewReader(f)
	if err != nil {
		return nil, err
	}
	defer gz.Close()

	var file ohlcvFile
	if err := json.NewDecoder(gz).Decode(&file); err != nil {
		return nil, err
	}
	return &file, nil
}

func saveOHLCV(path string, file *ohlcvFile) error {
	os.MkdirAll(filepath.Dir(path), 0755)

	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	gz := gzip.NewWriter(f)
	defer gz.Close()

	gz.Comment = "tvcli ohlcv"
	enc := json.NewEncoder(gz)
	enc.SetIndent("", "")
	return enc.Encode(file)
}

func lastTimestamp(data []ohlcvBar) float64 {
	if len(data) == 0 {
		return 0
	}
	return data[len(data)-1].Time
}

// mergeOHLCV merges new bars into existing, deduplicating by timestamp.
// Both slices must be sorted ascending by time. Returns merged sorted slice.
func mergeOHLCV(existing, fresh []ohlcvBar) []ohlcvBar {
	if len(existing) == 0 {
		return fresh
	}
	if len(fresh) == 0 {
		return existing
	}

	// Build index of existing timestamps
	seen := make(map[float64]bool, len(existing))
	for _, b := range existing {
		seen[b.Time] = true
	}

	merged := make([]ohlcvBar, 0, len(existing)+len(fresh))
	merged = append(merged, existing...)
	for _, b := range fresh {
		if !seen[b.Time] {
			merged = append(merged, b)
		}
	}

	sort.Slice(merged, func(i, j int) bool {
		return merged[i].Time < merged[j].Time
	})
	return merged
}

// fetchOHLCVBars connects via WS, fetches raw OHLCV bars, and returns them sorted ascending.
func fetchOHLCVBars(cfg *config.Config, symbol, tf string, bars int) ([]ohlcvBar, error) {
	client := tradingview.NewClient(
		tradingview.WithToken(cfg.SessionID),
		tradingview.WithSignature(cfg.Signature),
		tradingview.WithDebug(cfg.Debug),
	)
	if err := client.Connect(); err != nil {
		return nil, fmt.Errorf("ws connect: %w", err)
	}
	if !client.WaitForConnected(10 * time.Second) {
		client.Close()
		return nil, fmt.Errorf("ws timeout")
	}
	defer client.Close()

	ch := tradingview.NewChartSession(client)
	ch.OnError(func(err error) {
		fmt.Fprintf(os.Stderr, "Chart error: %v\n", err)
	})

	done := make(chan struct{})
	once := sync.Once{}
	ch.OnUpdate(func() {
		once.Do(func() { close(done) })
	})

	ch.SetMarket(symbol, map[string]any{
		"timeframe": pinefacade.NormalizeTimeframe(tf),
		"range":     bars,
	})

	if err := ch.WaitForSymbol(15 * time.Second); err != nil {
		return nil, fmt.Errorf("symbol load: %w", err)
	}

	select {
	case <-done:
	case <-time.After(30 * time.Second):
		return nil, fmt.Errorf("timeout waiting for OHLCV data")
	}
	time.Sleep(800 * time.Millisecond)

	periods := ch.Periods()
	if len(periods) == 0 {
		return nil, fmt.Errorf("no OHLCV data received")
	}

	// Convert to compact bar format
	bars_out := make([]ohlcvBar, 0, len(periods))
	for _, p := range periods {
		bars_out = append(bars_out, ohlcvBar{
			Time:   p["time"].(float64),
			Open:   p["open"].(float64),
			High:   p["high"].(float64),
			Low:    p["low"].(float64),
			Close:  p["close"].(float64),
			Volume: p["volume"].(float64),
		})
	}

	sort.Slice(bars_out, func(i, j int) bool {
		return bars_out[i].Time < bars_out[j].Time
	})

	return bars_out, nil
}

func cmdSync(cfg *config.Config, flags flagSet) {
	symbol := flags.get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		fatal("Invalid symbol: %v\n\nUse --symbol EXCHANGE:SYMBOL (e.g. OANDA:XAUUSD, BINANCE:BTCUSDT)", err)
	}
	symbol = normalizedSymbol

	tf := flags.get("tf")
	if tf == "" {
		tf = flags.get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.getInt("bars", 5000)

	limits := getTierLimits()
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		fmt.Fprintf(os.Stderr, "Capping bars from %d to %d (tier limit)\n", bars, limits.MaxBars)
		bars = limits.MaxBars
	}

	// Determine output path
	symbolClean := strings.ReplaceAll(symbol, ":", "_")
	baseName := fmt.Sprintf("%s_%s", symbolClean, tf)

	outDir := flags.get("dir")
	if outDir == "" {
		outDir = "."
	}
	os.MkdirAll(outDir, 0755)

	filePath := flags.get("out")
	if filePath == "" {
		filePath = filepath.Join(outDir, baseName+".json.gz")
	}

	// Load existing file for gap detection
	var existing *ohlcvFile
	force := flags.has("force")
	if !force {
		if f, err := loadOHLCV(filePath); err == nil {
			existing = f
			fmt.Fprintf(os.Stderr, "Loaded existing: %s (%d bars, updated %s)\n",
				filePath, f.Count, f.UpdatedAt)
		}
	}

	// Determine how many bars to fetch
	fetchBars := bars
	if existing != nil && len(existing.Data) > 0 {
		// Gap-fill: we need enough bars to cover from last timestamp to now.
		// TradingView returns the most N bars, so if the gap is small relative
		// to the request, we'll get it. Request the full bar count to maximize coverage.
		latest := lastTimestamp(existing.Data)
		age := time.Now().Unix() - int64(latest)
		// Estimate bars needed based on timeframe
	 tfSecs := timeframeSeconds(tf)
		if tfSecs > 0 {
			gapBars := int(age/int64(tfSecs)) + 10 // +10 buffer
			if gapBars < bars {
				fetchBars = bars // request full amount to be safe
			}
		}
		fmt.Fprintf(os.Stderr, "Gap-fill: last bar at %s, fetching %d bars\n",
			time.Unix(int64(latest), 0).UTC().Format("2006-01-02T15:04:05Z"), fetchBars)
	}

	// Fetch
	fmt.Fprintf(os.Stderr, "Fetching OHLCV: %s @ %s, %d bars\n", symbol, tf, fetchBars)
	start := time.Now()

	fresh, err := fetchOHLCVBars(cfg, symbol, tf, fetchBars)
	if err != nil {
		fatal("Fetch: %v", err)
	}

	elapsed := time.Since(start)
	fmt.Fprintf(os.Stderr, "Received %d bars in %s\n", len(fresh), elapsed.Round(time.Millisecond))

	// Merge with existing
	var merged []ohlcvBar
	if existing != nil {
		merged = mergeOHLCV(existing.Data, fresh)
		added := len(merged) - len(existing.Data)
		fmt.Fprintf(os.Stderr, "Merged: %d existing + %d new = %d total (+%d)\n",
			len(existing.Data), len(fresh), len(merged), added)
	} else {
		merged = fresh
	}

	// Save compressed
	file := &ohlcvFile{
		Symbol:    symbol,
		Timeframe: tf,
		Count:     len(merged),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
		Data:      merged,
	}

	if err := saveOHLCV(filePath, file); err != nil {
		fatal("Save: %v", err)
	}

	finfo, _ := os.Stat(filePath)
	sizeKB := finfo.Size() / 1024

	// Time span
	span := ""
	if len(merged) > 1 {
		first := time.Unix(int64(merged[0].Time), 0).UTC()
		last := time.Unix(int64(merged[len(merged)-1].Time), 0).UTC()
		d := last.Sub(first)
		span = fmt.Sprintf(", spans %s (%s to %s)", d.Round(time.Second), first.Format("2006-01-02"), last.Format("2006-01-02"))
	}

	fmt.Fprintf(os.Stderr, "\nSaved: %s (%dKB gzipped, %d bars%s)\n", filePath, sizeKB, len(merged), span)

	// Loop mode
	loopSecs := 0
	if flags.has("loop") {
		loopStr := flags.get("loop")
		if loopStr == "" || loopStr == "true" {
			loopSecs = 300
		} else {
			d, err := time.ParseDuration(loopStr)
			if err != nil {
				n := 0
				fmt.Sscanf(loopStr, "%d", &n)
				if n > 0 {
					loopSecs = n
				} else {
					fatal("Invalid loop interval: %s", loopStr)
				}
			} else {
				loopSecs = int(d.Seconds())
			}
		}
		fmt.Fprintf(os.Stderr, "Loop mode: syncing every %ds\n", loopSecs)
	}

	if loopSecs == 0 {
		return
	}

	// Loop: re-fetch and merge periodically
	for {
		time.Sleep(time.Duration(loopSecs) * time.Second)

		// Reload file in case it was edited externally
		if f, err := loadOHLCV(filePath); err == nil {
			existing = f
		}

		fresh, err := fetchOHLCVBars(cfg, symbol, tf, bars)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Loop fetch error: %v\n", err)
			continue
		}

		if existing != nil {
			merged = mergeOHLCV(existing.Data, fresh)
		} else {
			merged = fresh
		}

		file.Data = merged
		file.Count = len(merged)
		file.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

		if err := saveOHLCV(filePath, file); err != nil {
			fmt.Fprintf(os.Stderr, "Loop save error: %v\n", err)
			continue
		}

		finfo, _ := os.Stat(filePath)
		fmt.Fprintf(os.Stderr, "[%s] Synced: %d bars, %dKB\n",
			time.Now().Format("15:04:05"), len(merged), finfo.Size()/1024)
	}
}

// timeframeSeconds returns the approximate seconds per bar for a timeframe string.
func timeframeSeconds(tf string) int64 {
	t := strings.ToUpper(tf)
	switch t {
	case "1":
		return 60
	case "3":
		return 180
	case "5":
		return 300
	case "15":
		return 900
	case "30":
		return 1800
	case "45":
		return 2700
	case "60", "1H":
		return 3600
	case "120", "2H":
		return 7200
	case "180", "3H":
		return 10800
	case "240", "4H":
		return 14400
	case "D", "1D":
		return 86400
	case "W", "1W":
		return 604800
	case "M", "1M":
		return 2592000
	}
	// Try parsing as minutes
	n := 0
	fmt.Sscanf(tf, "%d", &n)
	if n > 0 {
		return int64(n) * 60
	}
	return 300 // default 5m
}

func printHelp() {
	fmt.Print(`
TradingView Pine Script Manager (Go)

Usage: tv-cli <command> [options]

Commands:
  list                          List all tracked scripts
    -r, --remote                 List remote saved scripts
    -p, --public                 List public TradingView scripts
  publist                       List public TradingView scripts
    --offset N                   Pagination offset (default: 0)
    --limit N                   Max results (default: 20)
    --json                      JSON output
  top                           Fetch top public scripts to JSON
    --limit N                   Number of scripts (default: 100)
    --output <file>             Output file (default: top_scripts.json)
  create <file.pine>            Create new remote script
    --name "Name"               Script name
  pull <id|pineId>              Pull remote script to local
  push <id|file>                Push local changes
    --force                     Push even if unchanged
  delete <id>                   Delete script
    --yes                       Confirm deletion
  compile <file.pine>           Compile script
  fetch                         Fetch raw OHLCV data (no indicator needed)
    --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)
    --tf 5m                     Timeframe (default: 5m)
    --bars 180                  Number of bars (free tier: 180)
    --dir <dir>                 Output directory (default: .)
    --json-out <file>           Custom JSON output path
    --csv-out <file>            Custom CSV output path
  sync                          Fetch + compress OHLCV to .json.gz (gap-fills existing)
    --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)
    --tf 5m                     Timeframe (default: 5m)
    --bars 5000                 Max bars to request
    --dir <dir>                 Output directory (default: .)
    --out <file>                Output file path (default: SYMBOL_tf.json.gz)
    --force                     Ignore existing file, re-fetch everything
    --loop <interval>           Keep syncing (e.g. 5m, 1h). Gap-fills each cycle.
  run <pineId>                  Run script with chart session
    --symbol EXCHANGE:SYMBOL     Market symbol (e.g., OANDA:XAUUSD, BINANCE:BTCUSDT)
    --tf 5m                     Timeframe
    --bars 500                  Number of bars
    --json                      JSON output
    --raw                       Dump raw unprocessed capture (periods + graphic + strategyReport)
    --raw-out <file>            Write raw dump to file (implies --raw)
    --out <file>                Save output to file
    --signals                   Emit script-agnostic extracted signals (JSON with --json, compact text default)
    --schema                    Show parsed metaInfo schema (plots, styles, palettes) without running
    --multi-run, --sweep        Generate input sweep configurations (shows what would be varied)
    --settle <ms>               Wait after first data update for follow-up graphics/backfill (default 1500)
    --force-cleanup             Aggressively retry when study limit hit (web UI indicators blocking)
    --persistent                Keep WS connection open across runs (no reconnect between runs)
    --loop <interval>           Re-run periodically (e.g. 30s, 5m, 1h). Implies --persistent.

  Symbol formats:
    Forex:    OANDA:XAUUSD, OANDA:EURUSD, FXCM:GBPUSD
    Crypto:   BINANCE:BTCUSDT, COINBASE:BTCUSD, BYBIT:ETHUSDT
    Stocks:   NASDAQ:AAPL, NYSE:TSLA, AMEX:SPY
    Auto:     XAUUSD → OANDA:XAUUSD, BTCUSDT → BINANCE:BTCUSDT
  search <query>                Search public scripts
    --limit N                   Max results (default: 20)
    --json                      JSON output

Authentication:
  Extract SESSION and SIGNATURE cookies from your browser:
    1. Log in to tradingview.com
    2. Open DevTools → Application → Cookies
    3. Copy sessionid and sessionid_sign values
    4. Set in .env file (loaded automatically):

  SESSION=<sessionid cookie value>
  SIGNATURE=<sessionid_sign cookie value>
  TV_USER=<your TradingView username>

  Write operations (create/push/delete) require all three.
  Read operations (list/pull/search/compile) work with SESSION+SIGNATURE.
  run works with any auth (anonymous fallback available).

Subscription Tier (set TV_TIER to match your plan):
  TV_TIER=free       1 chart, 2 indicators, 2 connections, 180d bars, 20s calc
  TV_TIER=essential  2 charts, 5 indicators, 10 connections, 365d bars, 40s calc
  TV_TIER=plus       4 charts, 10 indicators, 20 connections, unlimited bars, 40s calc
  TV_TIER=premium    8 charts, 25 indicators, 50 connections, unlimited bars, 40s calc
  TV_TIER=ultimate   16 charts, 50 indicators, 200 connections, unlimited bars, 100s calc

  Default: free. The run command auto-cleans studies and caps bars to your tier.
`)
}

// --- helpers ---

type flagSet struct {
	positional []string
	flags      map[string]string
}

func parseFlags(args []string) flagSet {
	fs := flagSet{flags: make(map[string]string)}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if strings.HasPrefix(a, "--") {
			key := strings.TrimPrefix(a, "--")
			if idx := strings.Index(key, "="); idx >= 0 {
				fs.flags[key[:idx]] = key[idx+1:]
			} else if i+1 < len(args) && !strings.HasPrefix(args[i+1], "-") {
				fs.flags[key] = args[i+1]
				i++
			} else {
				fs.flags[key] = "true"
			}
		} else if strings.HasPrefix(a, "-") && len(a) == 2 {
			key := string(a[1])
			if i+1 < len(args) && !strings.HasPrefix(args[i+1], "-") {
				fs.flags[key] = args[i+1]
				i++
			} else {
				fs.flags[key] = "true"
			}
		} else {
			fs.positional = append(fs.positional, a)
		}
	}
	return fs
}

func (fs flagSet) has(key string) bool {
	_, ok := fs.flags[key]
	return ok
}

func (fs flagSet) get(key string) string {
	return fs.flags[key]
}

func (fs flagSet) getInt(key string, def int) int {
	v := fs.flags[key]
	if v == "" {
		return def
	}
	n := 0
	fmt.Sscanf(v, "%d", &n)
	if n == 0 {
		return def
	}
	return n
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "Error: "+format+"\n", args...)
	os.Exit(1)
}

func isNumeric(s string) bool {
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return len(s) > 0
}

func slugify(input string) string {
	s := strings.TrimSpace(input)
	s = strings.ToLower(s)
	s = strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' {
			return r
		}
		return '-'
	}, s)
	for strings.Contains(s, "--") {
		s = strings.ReplaceAll(s, "--", "-")
	}
	s = strings.Trim(s, "-")
	if s == "" {
		return "script"
	}
	return s
}

func relPath(cfg *config.Config, absPath string) string {
	rel, err := filepath.Rel(".", absPath)
	if err != nil {
		return absPath
	}
	return rel
}

func extractPineID(data any) string {
	switch v := data.(type) {
	case map[string]any:
		for _, key := range []string{"pineId", "id", "scriptIdPart"} {
			if s, ok := v[key].(string); ok && strings.Contains(s, ";") {
				return s
			}
		}
		if result, ok := v["result"].(map[string]any); ok {
			if mi, ok := result["metaInfo"].(map[string]any); ok {
				if s, ok := mi["scriptIdPart"].(string); ok {
					return s
				}
			}
		}
	}
	return ""
}

func extractVersion(data any) string {
	if m, ok := data.(map[string]any); ok {
		if v, ok := m["version"].(string); ok {
			return v
		}
	}
	return ""
}

// --- metadata store ---

type metaEntry struct {
	ID            string `json:"id"`
	Name          string `json:"name"`
	PineID        string `json:"pineId"`
	LocalPath     string `json:"localPath"`
	LocalHash     string `json:"localHash"`
	RemoteHash    string `json:"remoteHash"`
	RemoteVersion string `json:"remoteVersion"`
	UpdatedAt     string `json:"updatedAt"`
}

type metaStore struct {
	dataDir  string
	metaFile string
	scripts  map[string]*metaEntry
}

func loadMetaStore(cfg *config.Config) (*metaStore, error) {
	absMeta, _ := filepath.Abs(cfg.MetaFile)
	ms := &metaStore{
		dataDir:  cfg.DataDir,
		metaFile: absMeta,
		scripts:  make(map[string]*metaEntry),
	}

	os.MkdirAll(cfg.DataDir, 0755)
	os.MkdirAll(filepath.Join(cfg.DataDir, "inputs"), 0755)

	data, err := os.ReadFile(absMeta)
	if err != nil {
		return ms, nil
	}

	var raw struct {
		Version int                    `json:"version"`
		Scripts map[string]*metaEntry  `json:"scripts"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return ms, nil
	}
	ms.scripts = raw.Scripts
	return ms, nil
}

func (ms *metaStore) save() {
	data := map[string]any{
		"version": 1,
		"scripts": ms.scripts,
	}
	b, _ := json.MarshalIndent(data, "", "  ")
	os.WriteFile(ms.metaFile, b, 0644)
}

func (ms *metaStore) getScript(id string) *metaEntry {
	return ms.scripts[id]
}

func (ms *metaStore) setScript(id string, entry metaEntry) {
	if existing, ok := ms.scripts[id]; ok {
		if entry.Name == "" {
			entry.Name = existing.Name
		}
		if entry.PineID == "" {
			entry.PineID = existing.PineID
		}
		if entry.LocalPath == "" {
			entry.LocalPath = existing.LocalPath
		}
		if entry.LocalHash == "" {
			entry.LocalHash = existing.LocalHash
		}
		if entry.RemoteHash == "" {
			entry.RemoteHash = existing.RemoteHash
		}
		if entry.RemoteVersion == "" {
			entry.RemoteVersion = existing.RemoteVersion
		}
	}
	entry.ID = id
	entry.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	ms.scripts[id] = &entry
	ms.save()
}

func (ms *metaStore) deleteScript(id string) {
	delete(ms.scripts, id)
	ms.save()
}

func (ms *metaStore) listScripts() []*metaEntry {
	var result []*metaEntry
	for _, s := range ms.scripts {
		result = append(result, s)
	}
	return result
}

func (ms *metaStore) nextID() string {
	max := 0
	for id := range ms.scripts {
		n := 0
		fmt.Sscanf(id, "%d", &n)
		if n > max {
			max = n
		}
	}
	return fmt.Sprintf("%d", max+1)
}

func (ms *metaStore) findByPineID(pineID string) *metaEntry {
	norm := pinefacade.NormalizePineID(pineID)
	for _, s := range ms.scripts {
		if pinefacade.NormalizePineID(s.PineID) == norm {
			return s
		}
	}
	return nil
}

func (ms *metaStore) findByLocalPath(filePath string) *metaEntry {
	abs, _ := filepath.Abs(filePath)
	for _, s := range ms.scripts {
		sAbs, _ := filepath.Abs(s.LocalPath)
		if sAbs == abs {
			return s
		}
	}
	return nil
}
