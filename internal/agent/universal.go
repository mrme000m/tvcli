// Package agent provides a universal script analyzer that automatically
// converts any Pine Script indicator into structured analysis output.
package agent

import (
	"context"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/service"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/pipeline"
	"github.com/ch99q/tvcli/pkg/schema"
)

// UniversalAnalyzerConfig configures the universal analyzer.
type UniversalAnalyzerConfig struct {
	Symbol         string
	Timeframe      string
	Bars           int
	Inputs         map[string]string
	Schema         *schema.PineSchema // Pre-fetched schema (optional)
	ForceSchema    bool               // Force schema fetch even if cached
	Debug          bool
	SettleMs       int
	Timeout        time.Duration
	ValidateInputs bool // Validate inputs against schema before running
	ListInputsOnly bool // Only fetch and list inputs, don't run analysis
}

// UniversalResult contains the full analysis from any Pine script.
type UniversalResult struct {
	ScriptInfo    ScriptInfo
	MarketData    MarketData
	Signals       *pipeline.Signals
	GraphicData   GraphicAnalysis
	Summary       AnalysisSummary
	AgentEnvelope *SkillAgentResult // Agent-ready v2 envelope
	Raw           *RawData          // Optional raw data
}

type ScriptInfo struct {
	PineID       string
	Name         string
	Version      string
	IsStrategy   bool
	IsOverlay    bool
	PlotCount    int
	InputCount   int
	HasSchema    bool
	GraphicTypes []string // e.g., ["dwgboxes", "dwglines", "dwglabels"]
}

type MarketData struct {
	Symbol      string
	Timeframe   string
	LastPrice   float64
	PriceSource string // "plot", "graphic", "ohlcv"
	BarCount    int
	TimeRange   string
}

type GraphicAnalysis struct {
	Boxes      []BoxGraphic
	Lines      []LineGraphic
	Labels     []LabelGraphic
	Tables     []TableGraphic
	Histograms []HistogramGraphic
	Summary    GraphicSummary
}

type BoxGraphic struct {
	ID          string
	X1, X2      float64 // Time indices
	Y1, Y2      float64 // Price levels
	High        float64
	Low         float64
	Mid         float64
	Text        string
	BorderColor int
	FillColor   int
	Style       string
	Extend      string
	// Inferred type
	InferredType string // "volume_profile", "order_block", "fvg", "liquidity", "session", "other"
	Confidence   float64
}

type LineGraphic struct {
	ID           string
	X1, X2       float64
	Y1, Y2       float64
	Color        int
	Width        float64
	Style        string
	Extend       string
	IsHorizontal bool
	AvgPrice     float64
	Slope        float64
	InferredType string // "support", "resistance", "trendline", "ema", "vwap", "other"
	Confidence   float64
}

type LabelGraphic struct {
	ID           string
	X            float64
	Y            float64
	Text         string
	Color        int
	Size         string
	Align        string
	VAlign       string
	InferredType string // "buy", "sell", "bos", "choch", "liquidity", "level", "text"
	Confidence   float64
}

type TableGraphic struct {
	ID           string
	Cols         int
	Rows         int
	Cells        [][]string
	Headers      []string
	InferredType string // "dashboard", "stats", "signals", "profile", "other"
}

type HistogramGraphic struct {
	ID           string
	PriceLow     float64
	PriceHigh    float64
	FirstBar     float64
	LastBar      float64
	Rates        []float64
	InferredType string // "volume_profile", "tpo", "other"
}

type GraphicSummary struct {
	TotalBoxes      int
	TotalLines      int
	TotalLabels     int
	TotalTables     int
	TotalHistograms int
	PriceRange      [2]float64 // [min, max]
	TimeRange       [2]float64 // [min bar index, max bar index]
	InferredTypes   map[string]int
	// VolumePeaks are the point-of-control (POC) + value-area levels recovered
	// from volume-profile style box stacks (many uniformly-spaced boxes sharing
	// a left edge, where each box's X-extent is proportional to its volume).
	VolumePeaks []VolumePeak `json:"volumePeaks,omitempty"`
	// Zones are price regions bounded by graphic elements (e.g. a linefill
	// between two horizontal lines), often an order-block boxed region.
	Zones []Zone `json:"zones,omitempty"`
}

// VolumePeak is one recovered volume-profile stack summary.
type VolumePeak struct {
	PocPrice   float64 `json:"poc"`
	Vah        float64 `json:"vah"`
	Val        float64 `json:"val"`
	StackCount int     `json:"stackCount"`
	LeftBar    float64 `json:"leftBar,omitempty"`
	Confidence float64 `json:"confidence"`
}

// Zone is a bounded price region recovered from graphics (linefill/box pairs).
type Zone struct {
	Top        float64 `json:"top"`
	Bottom     float64 `json:"bottom"`
	Mid        float64 `json:"mid"`
	LeftBar    float64 `json:"leftBar,omitempty"`
	RightBar   float64 `json:"rightBar,omitempty"`
	Confidence float64 `json:"confidence"`
}

type AnalysisSummary struct {
	Bias            string
	Confidence      float64
	KeyLevels       []KeyLevel
	Signals         []SignalEvent
	Patterns        []Pattern
	RiskMetrics     RiskMetrics
	Recommendations []string
	Warnings        []string
}

type KeyLevel struct {
	Price       float64
	Kind        string // "support", "resistance", "poc", "vah", "val", "order_block", "fvg", "liquidity"
	Strength    float64
	Source      string // "plot", "box", "line", "label", "table", "histogram"
	Description string
	Age         int  // bars since formation
	IsActive    bool // price near level
}

type SignalEvent struct {
	Time       int64
	Kind       string // "buy", "sell", "alert", "bos", "choch", "liquidity_sweep"
	Price      float64
	Strength   float64
	Source     string
	Text       string
	Confidence float64
}

type Pattern struct {
	Name        string
	Type        string // "structure", "volume", "momentum", "session"
	Bias        string
	Confidence  float64
	Description string
	KeyPrices   []float64
}

type RiskMetrics struct {
	ATR                  float64
	VolatilityRank       float64
	DistanceToSupport    float64
	DistanceToResistance float64
	RiskRewardLong       float64
	RiskRewardShort      float64
}

type SkillAgentResult struct {
	Status        string
	ExitCode      int
	Timestamp     string
	Execution     ExecutionMeta
	AgentContext  AgentContext
	Market        MarketDataAgent
	Structure     map[string]any
	Opportunities []OpportunityAgent
	Narrative     NarrativeAgent
	Conformance   ConformanceAgent
	SchemaVersion string
}

type ExecutionMeta struct {
	DurationMs int64
	Attempts   int
}

type AgentContext struct {
	Workflow     string
	ModelVersion string
	Symbol       string
	Timeframe    string
}

type MarketDataAgent struct {
	LastPrice any
	Bias      string
}

type OpportunityAgent struct {
	Rank              int
	Setup             string
	Direction         string
	Confidence        string
	ConfluenceScore   float64
	DistanceFromPrice any
	IsStale           bool
	Rationale         string
	Entry             float64
	StopLoss          float64
	TP1               float64
	TP2               float64
	TP3               float64
	RiskReward        float64
}

type NarrativeAgent struct {
	MarketStructure string
	PrimaryOpp      string
	Warnings        []string
	Watchlist       []string
}

type ConformanceAgent struct {
	HasValidData bool
	AgenticScore float64
}

type RawData struct {
	Periods        []map[string]any
	Graphic        map[string]map[string]any
	StrategyReport map[string]any
	Schema         *schema.PineSchema
}

// UniversalAnalyzer analyzes any Pine script automatically.
type UniversalAnalyzer struct {
	cfg    *config.Config
	config UniversalAnalyzerConfig
}

// NewUniversalAnalyzer creates a new universal analyzer.
func NewUniversalAnalyzer(cfg *config.Config, config UniversalAnalyzerConfig) *UniversalAnalyzer {
	if config.Symbol == "" {
		config.Symbol = "OANDA:XAUUSD"
	}
	if config.Timeframe == "" {
		config.Timeframe = "5m"
	}
	if config.Bars == 0 {
		config.Bars = 500
	}
	if config.Timeout == 0 {
		config.Timeout = 120 * time.Second
	}
	if config.SettleMs == 0 {
		config.SettleMs = 1500
	}
	return &UniversalAnalyzer{cfg: cfg, config: config}
}

// Analyze runs the script and produces a complete universal analysis.
// ListInputs fetches the schema and returns available input definitions.
func (a *UniversalAnalyzer) ListInputs(ctx context.Context, pineID string) ([]schema.InputDef, error) {
	pfClient := pinefacade.NewClient(a.cfg.PineFacadeURL, a.cfg.UserName, a.config.Timeout)
	indResult, err := pfClient.Get(pineID, "last", a.cfg.CookieHeaderOrEmpty())
	if err != nil {
		return nil, fmt.Errorf("fetch script: %w", err)
	}
	if indResult.MetaInfo == nil {
		return nil, fmt.Errorf("no metaInfo available for script")
	}
	sch := schema.FromMetaInfo(pineID, indResult.MetaInfo)
	if sch == nil {
		return nil, fmt.Errorf("failed to build schema")
	}
	return sch.Inputs, nil
}

// ValidateAndConvertInputs validates user inputs against schema and converts types.
func (a *UniversalAnalyzer) ValidateAndConvertInputs(inputs map[string]string, sch *schema.PineSchema) (map[string]string, []string, error) {
	if sch == nil {
		return inputs, nil, nil // No schema, pass through as-is
	}

	// Build lookup: input ID (with/without in_ prefix) and name -> InputDef
	byID := make(map[string]*schema.InputDef)
	for i := range sch.Inputs {
		inp := &sch.Inputs[i]
		byID[inp.ID] = inp
		if strings.HasPrefix(inp.ID, "in_") {
			byID[inp.ID[3:]] = inp // Also index without "in_" prefix
		}
		if inp.Name != "" && inp.Name != inp.ID {
			byID[inp.Name] = inp
		}
	}

	validated := make(map[string]string)
	var warnings []string

	for key, val := range inputs {
		inpDef, ok := byID[key]
		if !ok {
			warnings = append(warnings, fmt.Sprintf("input '%s' not found in script (available: %s)", key, a.listInputIDs(sch)))
			continue
		}

		// Type conversion and validation
		converted, err := a.convertInputValue(val, inpDef)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("input '%s': %v", key, err))
			continue
		}

		// Use the canonical TV input ID (with in_ prefix)
		canonicalKey := inpDef.ID
		validated[canonicalKey] = converted
	}

	return validated, warnings, nil
}

func (a *UniversalAnalyzer) listInputIDs(sch *schema.PineSchema) string {
	ids := make([]string, len(sch.Inputs))
	for i, inp := range sch.Inputs {
		ids[i] = inp.ID
	}
	return strings.Join(ids, ", ")
}

func (a *UniversalAnalyzer) convertInputValue(val string, inp *schema.InputDef) (string, error) {
	switch inp.Type {
	case "integer", "int":
		if _, err := strconv.Atoi(val); err != nil {
			return "", fmt.Errorf("expected integer, got %q", val)
		}
		// Validate min/max if specified
		if inp.Min != nil {
			if minVal, ok := toFloat(inp.Min); ok {
				if f, _ := strconv.ParseFloat(val, 64); f < minVal {
					return "", fmt.Errorf("value %s below minimum %v", val, inp.Min)
				}
			}
		}
		if inp.Max != nil {
			if maxVal, ok := toFloat(inp.Max); ok {
				if f, _ := strconv.ParseFloat(val, 64); f > maxVal {
					return "", fmt.Errorf("value %s above maximum %v", val, inp.Max)
				}
			}
		}
		return val, nil
	case "float":
		if _, err := strconv.ParseFloat(val, 64); err != nil {
			return "", fmt.Errorf("expected float, got %q", val)
		}
		if inp.Min != nil {
			if minVal, ok := toFloat(inp.Min); ok {
				if f, _ := strconv.ParseFloat(val, 64); f < minVal {
					return "", fmt.Errorf("value %s below minimum %v", val, inp.Min)
				}
			}
		}
		if inp.Max != nil {
			if maxVal, ok := toFloat(inp.Max); ok {
				if f, _ := strconv.ParseFloat(val, 64); f > maxVal {
					return "", fmt.Errorf("value %s above maximum %v", val, inp.Max)
				}
			}
		}
		return val, nil
	case "bool":
		lower := strings.ToLower(val)
		if lower == "true" || lower == "1" || lower == "yes" || lower == "on" {
			return "true", nil
		}
		if lower == "false" || lower == "0" || lower == "no" || lower == "off" {
			return "false", nil
		}
		return "", fmt.Errorf("expected boolean, got %q", val)
	case "string":
		// Validate against options if specified
		if len(inp.Options) > 0 {
			found := false
			for _, opt := range inp.Options {
				if opt == val {
					found = true
					break
				}
			}
			if !found {
				return "", fmt.Errorf("value %q not in allowed options: %v", val, inp.Options)
			}
		}
		return val, nil
	case "color":
		// Color inputs accept hex or named colors - pass through
		return val, nil
	default:
		// Unknown type, pass through
		return val, nil
	}
}

// Analyze runs the script and produces a complete universal analysis.
func (a *UniversalAnalyzer) Analyze(ctx context.Context, pineID string) (*UniversalResult, error) {
	start := time.Now()

	// 1. Fetch schema if not provided or forced
	var sch *schema.PineSchema
	var scriptName string
	var isStrategy, isOverlay bool

	if a.config.Schema != nil && !a.config.ForceSchema {
		sch = a.config.Schema
	} else {
		// Fetch from Pine Facade
		pfClient := pinefacade.NewClient(a.cfg.PineFacadeURL, a.cfg.UserName, a.config.Timeout)
		indResult, err := pfClient.Get(pineID, "last", a.cfg.CookieHeaderOrEmpty())
		if err != nil {
			return nil, fmt.Errorf("fetch script: %w", err)
		}
		if indResult.Meta != nil {
			scriptName = indResult.Meta.ScriptName
		}
		if indResult.MetaInfo != nil {
			sch = schema.FromMetaInfo(pineID, indResult.MetaInfo)
			if pine, ok := indResult.MetaInfo["pine"].(map[string]any); ok {
				if v, ok := pine["isStrategy"].(bool); ok {
					isStrategy = v
				}
				if v, ok := pine["isOverlay"].(bool); ok {
					isOverlay = v
				}
			}
		}
	}

	// 1b. Validate inputs against schema if requested
	inputs := a.config.Inputs
	if a.config.ValidateInputs && sch != nil {
		var err error
		inputs, _, err = a.ValidateAndConvertInputs(inputs, sch)
		if err != nil {
			return nil, fmt.Errorf("input validation: %w", err)
		}
	}

	// 1c. If list-inputs-only mode, return early with input info
	if a.config.ListInputsOnly {
		if sch == nil {
			return nil, fmt.Errorf("no schema available")
		}
		// Return a result with just script info and inputs
		return &UniversalResult{
			ScriptInfo: ScriptInfo{
				PineID:       pineID,
				Name:         scriptName,
				Version:      sch.Version,
				IsStrategy:   isStrategy,
				IsOverlay:    isOverlay,
				PlotCount:    len(sch.Plots),
				InputCount:   len(sch.Inputs),
				HasSchema:    true,
				GraphicTypes: []string{},
			},
			Raw: &RawData{Schema: sch},
		}, nil
	}

	// 2. Run script via WebSocket (LoadIndicator is called internally)
	res, err := service.RunScript(ctx, a.cfg, service.RunRequest{
		PineID:       pineID,
		Symbol:       a.config.Symbol,
		Timeframe:    a.config.Timeframe,
		Bars:         a.config.Bars,
		Inputs:       inputs,
		ReservedKeys: nil,
		SettleMs:     a.config.SettleMs,
		ForceCleanup: false,
		CalcTimeout:  a.config.Timeout,
		Debug:        a.config.Debug,
	})
	if err != nil {
		return nil, fmt.Errorf("run script: %w", err)
	}

	duration := time.Since(start)

	// 3. Extract signals using pipeline (schema-aware)
	var signals *pipeline.Signals
	signals = pipeline.Extract(pineID, a.config.Symbol, a.config.Timeframe, res.Periods, res.Graphic, res.StrategyReport, isStrategy)

	// 5. Analyze graphics deeply
	graphicAnalysis := a.analyzeGraphics(res.Graphic, signals)

	// 6. Build script info
	plotCount := 0
	inputCount := 0
	version := ""
	if sch != nil {
		plotCount = len(sch.Plots)
		inputCount = len(sch.Inputs)
		version = sch.Version
	}
	scriptInfo := ScriptInfo{
		PineID:       pineID,
		Name:         scriptName,
		Version:      version,
		IsStrategy:   isStrategy,
		IsOverlay:    isOverlay,
		PlotCount:    plotCount,
		InputCount:   inputCount,
		HasSchema:    sch != nil,
		GraphicTypes: pipeline.GraphicType(res.Graphic),
	}

	// 7. Build market data
	marketData := a.buildMarketData(res.Periods, graphicAnalysis, signals)

	// 8. Build analysis summary
	summary := a.buildSummary(signals, graphicAnalysis, marketData)

	// 9. Create agent-ready envelope
	agentEnv := a.buildAgentEnvelope(signals, graphicAnalysis, summary, marketData, scriptInfo, duration)

	return &UniversalResult{
		ScriptInfo:    scriptInfo,
		MarketData:    marketData,
		Signals:       signals,
		GraphicData:   graphicAnalysis,
		Summary:       summary,
		AgentEnvelope: agentEnv,
		Raw: &RawData{
			Periods:        res.Periods,
			Graphic:        res.Graphic,
			StrategyReport: res.StrategyReport,
			Schema:         sch,
		},
	}, nil
}

// analyzeGraphics performs deep semantic analysis of graphic data using the
// two-layer generic design:
//   Layer 1: flat parsing of boxes/lines/labels/tables/histograms (below)
//   Layer 2: topology-based grouping and semantic inference
//            (postProcessGraphics → postProcessGraphicsGeneric)
func (a *UniversalAnalyzer) analyzeGraphics(graphic map[string]map[string]any, signals *pipeline.Signals) GraphicAnalysis {
	analysis := GraphicAnalysis{
		Summary: GraphicSummary{
			InferredTypes: make(map[string]int),
			PriceRange:    [2]float64{math.MaxFloat64, -math.MaxFloat64},
			TimeRange:     [2]float64{math.MaxFloat64, -math.MaxFloat64},
		},
	}

	// Analyze boxes
	if boxes, ok := graphic["dwgboxes"]; ok {
		for id, item := range boxes {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			box := a.parseBox(id, m)
			box.InferredType, box.Confidence = a.inferBoxType(box, signals)
			analysis.Boxes = append(analysis.Boxes, box)
			analysis.Summary.TotalBoxes++
			analysis.Summary.InferredTypes[box.InferredType]++
			analysis.updatePriceRange(box.High, box.Low)
			analysis.updateTimeRange(box.X1, box.X2)
		}
	}

	// Analyze lines
	if lines, ok := graphic["dwglines"]; ok {
		for id, item := range lines {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			line := a.parseLine(id, m)
			line.InferredType, line.Confidence = a.inferLineType(line, signals)
			analysis.Lines = append(analysis.Lines, line)
			analysis.Summary.TotalLines++
			analysis.Summary.InferredTypes[line.InferredType]++
			analysis.updatePriceRange(line.Y1, line.Y2)
			analysis.updateTimeRange(line.X1, line.X2)
		}
	}

	// Analyze labels
	if labels, ok := graphic["dwglabels"]; ok {
		for id, item := range labels {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			label := a.parseLabel(id, m)
			label.InferredType, label.Confidence = a.inferLabelType(label, signals)
			analysis.Labels = append(analysis.Labels, label)
			analysis.Summary.TotalLabels++
			analysis.Summary.InferredTypes[label.InferredType]++
			if y, ok := toFloat(m["y"]); ok {
				analysis.updatePriceRange(y, y)
			}
			if x, ok := toFloat(m["x"]); ok {
				analysis.updateTimeRange(x, x)
			}
		}
	}

	// Analyze tables
	if _, ok := graphic["dwgtables"]; ok {
		grids := pipeline.ReconstructTables(graphic)
		for _, grid := range grids {
			table := TableGraphic{
				ID:      grid.ID,
				Cols:    grid.Cols,
				Rows:    grid.Rows,
				Cells:   grid.Cells,
				Headers: grid.Header(),
			}
			table.InferredType = a.inferTableType(table)
			analysis.Tables = append(analysis.Tables, table)
			analysis.Summary.TotalTables++
			analysis.Summary.InferredTypes[table.InferredType]++
		}
	}

	// Analyze histograms (volume profile)
	if hists, ok := graphic["hhists"]; ok {
		for id, item := range hists {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			hist := a.parseHistogram(id, m)
			analysis.Histograms = append(analysis.Histograms, hist)
			analysis.Summary.TotalHistograms++
			analysis.Summary.InferredTypes["volume_profile"]++
			analysis.updatePriceRange(hist.PriceHigh, hist.PriceLow)
		}
	}

	// Deep-graphics recovery: volume-profile POC/VAH/VAL and linefill zones.
	a.postProcessGraphics(&analysis, graphic)

	// Ensure price range is valid (not the initial sentinel values)
	if analysis.Summary.PriceRange[0] == math.MaxFloat64 {
		analysis.Summary.PriceRange = [2]float64{0, 0}
	}
	if analysis.Summary.TimeRange[0] == math.MaxFloat64 {
		analysis.Summary.TimeRange = [2]float64{0, 0}
	}

	return analysis
}

func (a *UniversalAnalyzer) parseBox(id string, m map[string]any) BoxGraphic {
	x1, _ := toFloat(m["x1"])
	x2, _ := toFloat(m["x2"])
	y1, _ := toFloat(m["y1"])
	y2, _ := toFloat(m["y2"])
	text, _ := m["t"].(string)
	bc, _ := toFloat(m["bc"])
	c, _ := toFloat(m["c"])
	st, _ := m["st"].(string)
	ex, _ := m["ex"].(string)

	high := math.Max(y1, y2)
	low := math.Min(y1, y2)

	return BoxGraphic{
		ID:          id,
		X1:          x1,
		X2:          x2,
		Y1:          y1,
		Y2:          y2,
		High:        high,
		Low:         low,
		Mid:         (high + low) / 2,
		Text:        text,
		BorderColor: int(bc),
		FillColor:   int(c),
		Style:       st,
		Extend:      ex,
	}
}

func (a *UniversalAnalyzer) parseLine(id string, m map[string]any) LineGraphic {
	x1, _ := toFloat(m["x1"])
	x2, _ := toFloat(m["x2"])
	y1, _ := toFloat(m["y1"])
	y2, _ := toFloat(m["y2"])
	ci, _ := toFloat(m["ci"])
	w, _ := toFloat(m["w"])
	st, _ := m["st"].(string)
	ex, _ := m["ex"].(string)

	avgPrice := (y1 + y2) / 2
	slope := math.Abs(y2-y1) / math.Max(math.Abs(y1), 1)
	isHorizontal := slope < 0.001

	return LineGraphic{
		ID:           id,
		X1:           x1,
		X2:           x2,
		Y1:           y1,
		Y2:           y2,
		Color:        int(ci),
		Width:        w,
		Style:        st,
		Extend:       ex,
		IsHorizontal: isHorizontal,
		AvgPrice:     avgPrice,
		Slope:        slope,
	}
}

func (a *UniversalAnalyzer) parseLabel(id string, m map[string]any) LabelGraphic {
	x, _ := toFloat(m["x"])
	y, _ := toFloat(m["y"])
	text, _ := m["t"].(string)
	tc, _ := toFloat(m["tc"])
	ts, _ := m["ts"].(string)
	tha, _ := m["tha"].(string)
	tva, _ := m["tva"].(string)

	return LabelGraphic{
		ID:     id,
		X:      x,
		Y:      y,
		Text:   text,
		Color:  int(tc),
		Size:   ts,
		Align:  tha,
		VAlign: tva,
	}
}

func (a *UniversalAnalyzer) parseHistogram(id string, m map[string]any) HistogramGraphic {
	priceLow, _ := toFloat(m["priceLow"])
	priceHigh, _ := toFloat(m["priceHigh"])
	firstBar, _ := toFloat(m["firstBarTime"])
	lastBar, _ := toFloat(m["lastBarTime"])

	var rates []float64
	if r, ok := m["rate"].([]any); ok {
		for _, v := range r {
			if f, ok := toFloat(v); ok {
				rates = append(rates, f)
			}
		}
	}

	return HistogramGraphic{
		ID:           id,
		PriceLow:     priceLow,
		PriceHigh:    priceHigh,
		FirstBar:     firstBar,
		LastBar:      lastBar,
		Rates:        rates,
		InferredType: "volume_profile",
	}
}

func (ga *GraphicAnalysis) updatePriceRange(y1, y2 float64) {
	if ga.Summary.PriceRange[0] > y1 {
		ga.Summary.PriceRange[0] = y1
	}
	if ga.Summary.PriceRange[0] > y2 {
		ga.Summary.PriceRange[0] = y2
	}
	if ga.Summary.PriceRange[1] < y1 {
		ga.Summary.PriceRange[1] = y1
	}
	if ga.Summary.PriceRange[1] < y2 {
		ga.Summary.PriceRange[1] = y2
	}
}

func (ga *GraphicAnalysis) updateTimeRange(x1, x2 float64) {
	if ga.Summary.TimeRange[0] > x1 {
		ga.Summary.TimeRange[0] = x1
	}
	if ga.Summary.TimeRange[0] > x2 {
		ga.Summary.TimeRange[0] = x2
	}
	if ga.Summary.TimeRange[1] < x1 {
		ga.Summary.TimeRange[1] = x1
	}
	if ga.Summary.TimeRange[1] < x2 {
		ga.Summary.TimeRange[1] = x2
	}
}

// inferBoxType uses heuristics to classify box graphics.
func (a *UniversalAnalyzer) inferBoxType(box BoxGraphic, signals *pipeline.Signals) (string, float64) {
	text := strings.ToUpper(box.Text)
	width := math.Abs(box.X2 - box.X1)
	height := math.Abs(box.Y2 - box.Y1)

	// FVG: boxes with text like "FVG", "FAIR VALUE", "IMBALANCE"
	if strings.Contains(text, "FVG") || strings.Contains(text, "FAIR VALUE") ||
		strings.Contains(text, "IMBALANCE") || strings.Contains(text, "GAP") {
		return "fvg", 0.9
	}

	// FVG heuristic: narrow time width (2-5 bars), no text, gap-like height
	// FVG boxes typically span 2-5 bars and represent a gap between candles
	if box.Text == "" && width >= 1 && width <= 6 && height > 0 {
		// Check if it's a gap (height represents a price gap)
		// Typical FVG: small time width, visible price gap
		if height > 5 && height < 200 {
			return "fvg", 0.7
		}
	}

	// Order Block: wider boxes, often with text like "OB", "Order Block"
	if strings.Contains(text, "OB") || strings.Contains(text, "ORDER BLOCK") ||
		strings.Contains(text, "BULLISH") || strings.Contains(text, "BEARISH") {
		if width > 5 {
			return "order_block", 0.9
		}
	}

	// Liquidity: boxes with text like "LIQUIDITY", "BUYSIDE", "SELLSIDE", "BSL", "SSL"
	if strings.Contains(text, "LIQUIDITY") || strings.Contains(text, "BUYSIDE") ||
		strings.Contains(text, "SELLSIDE") || strings.Contains(text, "BSL") ||
		strings.Contains(text, "SSL") {
		return "liquidity", 0.9
	}

	// Volume Profile: many boxes, similar heights, no text, stacked vertically
	// Usually many boxes (handled by caller counting), height < 50, width < 10
	if box.Text == "" && height > 0 && height < 50 && width < 10 {
		return "volume_profile", 0.8
	}

	// Session: boxes spanning full session hours
	if width > 50 && height > 100 {
		return "session", 0.7
	}

	// Generic price zone
	return "price_zone", 0.5
}

// inferLineType classifies line graphics using flat heuristics (Layer 1).
// These classifications are overridden by the topology-based grouping in
// graphics_generic.go (Layer 2) during postProcessGraphics.
func (a *UniversalAnalyzer) inferLineType(line LineGraphic, signals *pipeline.Signals) (string, float64) {
	// Vertical marker (x1 == x2) — market open/close or session boundary lines.
	if math.Abs(line.X1-line.X2) < 1e-6 {
		return "session", 0.8
	}
	if line.IsHorizontal {
		// Check if it's near a known level from signals
		for _, lvl := range signals.Levels {
			if math.Abs(lvl.Value-line.AvgPrice)/math.Max(math.Abs(lvl.Value), 1) < 0.001 {
				return lvl.Kind, 0.9
			}
		}
		// Generic horizontal line
		if line.Width > 1 {
			return "support_resistance", 0.7
		}
		return "horizontal_level", 0.6
	}

	// Trendline or moving average
	if line.Slope > 0 {
		return "trendline_up", 0.7
	}
	return "trendline_down", 0.7
}

// inferLabelType classifies text labels using flat heuristics (Layer 1).
// These classifications are overridden by the topology-based grouping in
// graphics_generic.go (Layer 2) during postProcessGraphics.
func (a *UniversalAnalyzer) inferLabelType(label LabelGraphic, signals *pipeline.Signals) (string, float64) {
	text := strings.ToUpper(label.Text)

	// Direct signals
	if strings.Contains(text, "BUY") || strings.Contains(text, "LONG") || strings.Contains(text, "BULL") {
		return "buy", 0.9
	}
	if strings.Contains(text, "SELL") || strings.Contains(text, "SHORT") || strings.Contains(text, "BEAR") {
		return "sell", 0.9
	}

	// SMC Structure
	if text == "BOS" || strings.Contains(text, "BREAK OF STRUCTURE") {
		return "bos", 0.9
	}
	if text == "CHOCH" || strings.Contains(text, "CHANGE OF CHARACTER") {
		return "choch", 0.9
	}

	// Liquidity
	if strings.Contains(text, "LIQUIDITY") || strings.Contains(text, "BSL") || strings.Contains(text, "SSL") ||
		strings.Contains(text, "BUYSIDE") || strings.Contains(text, "SELLSIDE") {
		return "liquidity", 0.9
	}

	// Levels
	if strings.Contains(text, "SUPPORT") {
		return "support", 0.8
	}
	if strings.Contains(text, "RESISTANCE") || strings.Contains(text, "RES") {
		return "resistance", 0.8
	}
	if strings.Contains(text, "POC") || strings.Contains(text, "POINT OF CONTROL") {
		return "poc", 0.9
	}
	if strings.Contains(text, "VAH") || strings.Contains(text, "VALUE AREA HIGH") {
		return "vah", 0.9
	}
	if strings.Contains(text, "VAL") || strings.Contains(text, "VALUE AREA LOW") {
		return "val", 0.9
	}

	// Order Blocks / FVG
	if strings.Contains(text, "OB") || strings.Contains(text, "ORDER BLOCK") {
		return "order_block", 0.8
	}
	if strings.Contains(text, "FVG") || strings.Contains(text, "FAIR VALUE") {
		return "fvg", 0.8
	}

	return "text", 0.3
}

// inferTableType classifies table graphics.
func (a *UniversalAnalyzer) inferTableType(table TableGraphic) string {
	if len(table.Headers) == 0 {
		return "unknown"
	}

	headers := strings.Join(table.Headers, " ")
	upper := strings.ToUpper(headers)

	if strings.Contains(upper, "TIMEFRAME") || strings.Contains(upper, "TREND") || strings.Contains(upper, "BIAS") {
		return "dashboard"
	}
	if strings.Contains(upper, "VOLUME") || strings.Contains(upper, "PROFILE") || strings.Contains(upper, "POC") {
		return "profile"
	}
	if strings.Contains(upper, "SIGNAL") || strings.Contains(upper, "BUY") || strings.Contains(upper, "SELL") {
		return "signals"
	}
	if strings.Contains(upper, "STAT") || strings.Contains(upper, "WIN") || strings.Contains(upper, "PROFIT") {
		return "stats"
	}

	return "dashboard"
}

// buildMarketData creates market data from periods and graphics.
func (a *UniversalAnalyzer) buildMarketData(periods []map[string]any, graphic GraphicAnalysis, signals *pipeline.Signals) MarketData {
	price := 0.0
	source := "none"

	// Try to get price from signals.Last
	for _, f := range []string{"close", "Close", "price", "Price"} {
		if v, ok := signals.Last[f]; ok {
			if p, ok := toFloat(v); ok && p > 0 {
				price = p
				source = "plot"
				break
			}
		}
	}

	// Fallback to graphic data
	if price == 0 {
		if graphic.Summary.PriceRange[1] > 0 {
			price = (graphic.Summary.PriceRange[0] + graphic.Summary.PriceRange[1]) / 2
			source = "graphic"
		}
	}

	// Fallback to last period OHLC
	if price == 0 && len(periods) > 0 {
		last := periods[0]
		for _, f := range []string{"close", "Close"} {
			if v, ok := toFloat(last[f]); ok && v > 0 {
				price = v
				source = "ohlcv"
				break
			}
		}
	}

	timeRange := ""
	if len(periods) >= 2 {
		first, _ := toFloat(periods[len(periods)-1]["$time"])
		last, _ := toFloat(periods[0]["$time"])
		timeRange = fmt.Sprintf("%d to %d", int64(first), int64(last))
	}

	return MarketData{
		Symbol:      a.config.Symbol,
		Timeframe:   a.config.Timeframe,
		LastPrice:   price,
		PriceSource: source,
		BarCount:    len(periods),
		TimeRange:   timeRange,
	}
}

// buildSummary creates a high-level analysis summary.
func (a *UniversalAnalyzer) buildSummary(signals *pipeline.Signals, graphic GraphicAnalysis, market MarketData) AnalysisSummary {
	summary := AnalysisSummary{
		Bias:            signals.Bias,
		Confidence:      signals.Confidence,
		KeyLevels:       []KeyLevel{},
		Signals:         []SignalEvent{},
		Patterns:        []Pattern{},
		RiskMetrics:     RiskMetrics{},
		Recommendations: []string{},
		Warnings:        append([]string{}, signals.Warnings...),
	}

	// Extract key levels from signals.Levels
	for _, lvl := range signals.Levels {
		age := 0
		isActive := false
		if market.LastPrice > 0 {
			dist := math.Abs(lvl.Value-market.LastPrice) / market.LastPrice * 100
			isActive = dist < 2.0 // within 2%
		}
		summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
			Price:       lvl.Value,
			Kind:        lvl.Kind,
			Strength:    0.7,
			Source:      lvl.Field,
			Description: fmt.Sprintf("%s at %.2f", lvl.Kind, lvl.Value),
			Age:         age,
			IsActive:    isActive,
		})
	}

	// Extract key levels from graphic boxes
	for _, box := range graphic.Boxes {
		if box.InferredType != "volume_profile" && box.InferredType != "price_zone" {
			summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
				Price:       box.High,
				Kind:        box.InferredType + "_high",
				Strength:    box.Confidence,
				Source:      "box",
				Description: fmt.Sprintf("%s high: %.2f", box.InferredType, box.High),
				IsActive:    market.LastPrice > 0 && math.Abs(box.High-market.LastPrice)/market.LastPrice < 0.02,
			})
			summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
				Price:       box.Low,
				Kind:        box.InferredType + "_low",
				Strength:    box.Confidence,
				Source:      "box",
				Description: fmt.Sprintf("%s low: %.2f", box.InferredType, box.Low),
				IsActive:    market.LastPrice > 0 && math.Abs(box.Low-market.LastPrice)/market.LastPrice < 0.02,
			})
		}
	}

	// Extract key levels from horizontal lines
	for _, line := range graphic.Lines {
		if line.IsHorizontal {
			summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
				Price:       line.AvgPrice,
				Kind:        line.InferredType,
				Strength:    line.Confidence,
				Source:      "line",
				Description: fmt.Sprintf("%s at %.2f", line.InferredType, line.AvgPrice),
				IsActive:    market.LastPrice > 0 && math.Abs(line.AvgPrice-market.LastPrice)/market.LastPrice < 0.02,
			})
		}
	}

	// Recovered volume-profile peaks: POC (point of control), VAH, VAL.
	for _, pk := range graphic.Summary.VolumePeaks {
		isActive := market.LastPrice > 0 && math.Abs(pk.PocPrice-market.LastPrice)/market.LastPrice < 0.02
		summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
			Price: pk.PocPrice, Kind: "poc", Strength: pk.Confidence,
			Source: "histogram", Description: fmt.Sprintf("POC (volume profile, %d bins) at %.2f", pk.StackCount, pk.PocPrice),
			IsActive: isActive,
		})
		summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
			Price: pk.Vah, Kind: "vah", Strength: pk.Confidence * 0.8,
			Source: "histogram", Description: fmt.Sprintf("Value area high at %.2f", pk.Vah), IsActive: false,
		})
		summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
			Price: pk.Val, Kind: "val", Strength: pk.Confidence * 0.8,
			Source: "histogram", Description: fmt.Sprintf("Value area low at %.2f", pk.Val), IsActive: false,
		})
	}
	// Recovered linefill zones (order-block box regions).
	for _, z := range graphic.Summary.Zones {
		summary.KeyLevels = append(summary.KeyLevels, KeyLevel{
			Price: z.Mid, Kind: "order_block", Strength: z.Confidence,
			Source: "linefill", Description: fmt.Sprintf("Order-block zone %.2f..%.2f", z.Bottom, z.Top),
			IsActive: market.LastPrice > 0 && math.Abs(z.Mid-market.LastPrice)/market.LastPrice < 0.02,
		})
	}

	// Extract signals from graphic labels
	for _, label := range graphic.Labels {
		if label.InferredType != "text" && label.InferredType != "unknown" {
			summary.Signals = append(summary.Signals, SignalEvent{
				Time:       int64(label.X),
				Kind:       label.InferredType,
				Price:      label.Y,
				Strength:   label.Confidence,
				Source:     "label",
				Text:       label.Text,
				Confidence: label.Confidence,
			})
		}
	}

	// Extract signals from pipeline events (filter out colorer/style values)
	for _, ev := range signals.Events {
		// Skip colorer/style values which are typically small integers (1, 2)
		// and fields that contain "colorer" or "style"
		fieldLower := strings.ToLower(ev.Field)
		if strings.Contains(fieldLower, "colorer") || strings.Contains(fieldLower, "style") {
			continue
		}
		// Skip values that are clearly color constants (1, 2, 0, -1)
		if ev.Value == 1 || ev.Value == 2 || ev.Value == 0 || ev.Value == -1 {
			continue
		}
		summary.Signals = append(summary.Signals, SignalEvent{
			Time:       ev.Time,
			Kind:       ev.Kind,
			Price:      ev.Value,
			Strength:   0.7,
			Source:     "plot",
			Text:       ev.Text,
			Confidence: 0.7,
		})
	}

	// Sort signals by time (newest first)
	sort.Slice(summary.Signals, func(i, j int) bool {
		return summary.Signals[i].Time > summary.Signals[j].Time
	})

	// Build patterns from graphic types
	typeCounts := make(map[string]int)
	for _, box := range graphic.Boxes {
		typeCounts[box.InferredType]++
	}
	for typ, count := range typeCounts {
		if count > 0 {
			summary.Patterns = append(summary.Patterns, Pattern{
				Name:        typ,
				Type:        "structure",
				Bias:        signals.Bias,
				Confidence:  0.7,
				Description: fmt.Sprintf("Found %d %s zones", count, typ),
			})
		}
	}

	// Risk metrics from ATR if available
	if atr, ok := signals.Last["atr"]; ok {
		if v, ok := toFloat(atr); ok {
			summary.RiskMetrics.ATR = v
		}
	}
	if market.LastPrice > 0 {
		// Find nearest support/resistance
		var nearestSup, nearestRes float64
		for _, lvl := range summary.KeyLevels {
			if lvl.Kind == "support" || strings.Contains(lvl.Kind, "support") {
				if lvl.Price < market.LastPrice && (nearestSup == 0 || lvl.Price > nearestSup) {
					nearestSup = lvl.Price
				}
			}
			if lvl.Kind == "resistance" || strings.Contains(lvl.Kind, "resistance") {
				if lvl.Price > market.LastPrice && (nearestRes == 0 || lvl.Price < nearestRes) {
					nearestRes = lvl.Price
				}
			}
		}
		if nearestSup > 0 {
			summary.RiskMetrics.DistanceToSupport = (market.LastPrice - nearestSup) / market.LastPrice * 100
		}
		if nearestRes > 0 {
			summary.RiskMetrics.DistanceToResistance = (nearestRes - market.LastPrice) / market.LastPrice * 100
		}
		if nearestSup > 0 && nearestRes > 0 {
			summary.RiskMetrics.RiskRewardLong = (nearestRes - market.LastPrice) / (market.LastPrice - nearestSup)
			summary.RiskMetrics.RiskRewardShort = (market.LastPrice - nearestSup) / (nearestRes - market.LastPrice)
		}
	}

	// Recommendations
	if len(summary.Signals) > 0 {
		lastSignal := summary.Signals[0]
		if lastSignal.Kind == "buy" || lastSignal.Kind == "bullish" {
			summary.Recommendations = append(summary.Recommendations, fmt.Sprintf("Consider long near %.2f (signal: %s)", lastSignal.Price, lastSignal.Text))
		} else if lastSignal.Kind == "sell" || lastSignal.Kind == "bearish" {
			summary.Recommendations = append(summary.Recommendations, fmt.Sprintf("Consider short near %.2f (signal: %s)", lastSignal.Price, lastSignal.Text))
		}
	}

	return summary
}

// buildAgentEnvelope creates the agent-ready v2 envelope.
func (a *UniversalAnalyzer) buildAgentEnvelope(signals *pipeline.Signals, graphic GraphicAnalysis, summary AnalysisSummary, market MarketData, scriptInfo ScriptInfo, duration time.Duration) *SkillAgentResult {
	// Build structure map
	structure := map[string]any{
		"script": map[string]any{
			"pineId":       scriptInfo.PineID,
			"name":         scriptInfo.Name,
			"isStrategy":   scriptInfo.IsStrategy,
			"isOverlay":    scriptInfo.IsOverlay,
			"plotCount":    scriptInfo.PlotCount,
			"inputCount":   scriptInfo.InputCount,
			"hasSchema":    scriptInfo.HasSchema,
			"graphicTypes": scriptInfo.GraphicTypes,
		},
		"signals": map[string]any{
			"bias":       signals.Bias,
			"confidence": signals.Confidence,
			"events":     len(signals.Events),
			"levels":     len(signals.Levels),
			"warnings":   signals.Warnings,
		},
		"graphic": map[string]any{
			"boxes":         len(graphic.Boxes),
			"lines":         len(graphic.Lines),
			"labels":        len(graphic.Labels),
			"tables":        len(graphic.Tables),
			"histograms":    len(graphic.Histograms),
			"inferredTypes": graphic.Summary.InferredTypes,
		},
		"market": map[string]any{
			"symbol":      market.Symbol,
			"timeframe":   market.Timeframe,
			"lastPrice":   market.LastPrice,
			"priceSource": market.PriceSource,
			"barCount":    market.BarCount,
		},
	}

	// Build opportunities from summary
	opps := []OpportunityAgent{}
	for i, sig := range summary.Signals {
		if i >= 5 {
			break
		}
		dir := "long"
		if sig.Kind == "sell" || sig.Kind == "bearish" || sig.Kind == "short" {
			dir = "short"
		}
		conf := "MED"
		if sig.Confidence > 0.8 {
			conf = "HIGH"
		} else if sig.Confidence < 0.5 {
			conf = "LOW"
		}
		opps = append(opps, OpportunityAgent{
			Rank:              i + 1,
			Setup:             sig.Kind,
			Direction:         dir,
			Confidence:        conf,
			ConfluenceScore:   sig.Confidence,
			DistanceFromPrice: 0,
			Rationale:         fmt.Sprintf("%s signal at %.2f: %s", sig.Kind, sig.Price, sig.Text),
			Entry:             sig.Price,
			StopLoss:          0,
			TP1:               0,
			TP2:               0,
			TP3:               0,
			RiskReward:        0,
		})
	}

	// Add level-based opportunities
	for i, lvl := range summary.KeyLevels {
		if i >= 5 {
			break
		}
		if lvl.IsActive {
			dir := "long"
			if strings.Contains(lvl.Kind, "resistance") || strings.Contains(lvl.Kind, "vah") {
				dir = "short"
			}
			opps = append(opps, OpportunityAgent{
				Rank:              len(opps) + 1,
				Setup:             "key_level_" + lvl.Kind,
				Direction:         dir,
				Confidence:        "MED",
				ConfluenceScore:   lvl.Strength,
				DistanceFromPrice: 0,
				Rationale:         lvl.Description,
				Entry:             lvl.Price,
				StopLoss:          0,
				TP1:               0,
				TP2:               0,
				TP3:               0,
				RiskReward:        0,
			})
		}
	}

	// Risk/Reward for top opportunity
	if len(opps) > 0 && market.LastPrice > 0 {
		for i := range opps {
			if opps[i].Entry > 0 {
				// Use risk metrics
				if dir := opps[i].Direction; dir == "long" {
					if summary.RiskMetrics.DistanceToSupport > 0 && summary.RiskMetrics.DistanceToResistance > 0 {
						opps[i].StopLoss = market.LastPrice * (1 - summary.RiskMetrics.DistanceToSupport/100)
						opps[i].TP1 = market.LastPrice * (1 + summary.RiskMetrics.DistanceToResistance/100)
						opps[i].RiskReward = summary.RiskMetrics.RiskRewardLong
					}
				} else {
					if summary.RiskMetrics.DistanceToSupport > 0 && summary.RiskMetrics.DistanceToResistance > 0 {
						opps[i].StopLoss = market.LastPrice * (1 + summary.RiskMetrics.DistanceToResistance/100)
						opps[i].TP1 = market.LastPrice * (1 - summary.RiskMetrics.DistanceToSupport/100)
						opps[i].RiskReward = summary.RiskMetrics.RiskRewardShort
					}
				}
			}
		}
	}

	narrative := NarrativeAgent{
		MarketStructure: fmt.Sprintf("Bias: %s | Confidence: %.0f%% | %d graphic elements (%s)",
			signals.Bias, signals.Confidence*100,
			len(graphic.Boxes)+len(graphic.Lines)+len(graphic.Labels)+len(graphic.Tables),
			mapToString(graphic.Summary.InferredTypes)),
		PrimaryOpp: primaryOppFromAgent(opps),
		Warnings:   summary.Warnings,
	}

	return &SkillAgentResult{
		Status:    "ok",
		ExitCode:  0,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Execution: ExecutionMeta{
			DurationMs: duration.Milliseconds(),
			Attempts:   1,
		},
		AgentContext: AgentContext{
			Workflow:     "universal_analyzer",
			ModelVersion: "agent-ready-v2",
			Symbol:       a.config.Symbol,
			Timeframe:    a.config.Timeframe,
		},
		Market: MarketDataAgent{
			LastPrice: market.LastPrice,
			Bias:      signals.Bias,
		},
		Structure:     structure,
		Opportunities: opps,
		Narrative:     narrative,
		Conformance: ConformanceAgent{
			HasValidData: len(signals.Events) > 0 || len(signals.Levels) > 0 || len(graphic.Boxes) > 0 || len(graphic.Lines) > 0 || len(graphic.Labels) > 0,
			AgenticScore: signals.Confidence,
		},
		SchemaVersion: "agent-ready-v2.0.0",
	}
}

func primaryOppFromAgent(opps []OpportunityAgent) string {
	if len(opps) == 0 {
		return "No clear opportunities"
	}
	return fmt.Sprintf("%s %s [%s]", opps[0].Direction, opps[0].Setup, opps[0].Confidence)
}

func mapToString(m map[string]int) string {
	parts := []string{}
	for k, v := range m {
		parts = append(parts, fmt.Sprintf("%s:%d", k, v))
	}
	sort.Strings(parts)
	return strings.Join(parts, ", ")
}

// --- Helpers ---

func ifSchema(sch *schema.PineSchema) int {
	if sch != nil {
		return len(sch.Plots)
	}
	return 0
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	}
	return 0, false
}
