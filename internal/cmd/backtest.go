// backtest.go — run a STRATEGY-type Pine script headlessly over the
// TradingView WebSocket with custom input values and extract the full strategy
// backtest result (performance metrics + executed trades). This is the
// strategy counterpart to `tv run` (which also handles indicators): it loads
// the script, applies inputs, runs it via create_study, and pulls the report
// that the server streams back in du frames — no live chart needed.
package cmd

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/pipeline"
)

// backtestReservedKeys are CLI flags (not strategy inputs) that collectInputs
// must not forward to the script runtime.
var backtestReservedKeys = append(append([]string{}, ReservedRunKeys...),
	"trades", "json", "out",
)

type backtestCmd struct{ app *App }

func (c *backtestCmd) Name() string     { return "backtest" }
func (c *backtestCmd) Aliases() []string { return []string{"bt", "strategy"} }
func (c *backtestCmd) Synopsis() string {
	return "Run a strategy with custom inputs and extract its backtest results"
}

func (c *backtestCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}
	if len(flags.Positional) == 0 {
		return fmt.Errorf(`usage: backtest "<pineId>" [--symbol X] [--tf 5m] [--bars 500] [--inputs '{"…": …}']`)
	}

	if err := PreCheckAuth(cfg); err != nil {
		return err
	}

	pineID, err := resolvePineID(flags.Positional[0], cfg)
	if err != nil {
		return err
	}

	symbol := flags.Get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		return fmt.Errorf("invalid symbol: %v\n\nUse --symbol EXCHANGE:SYMBOL (e.g. OANDA:XAUUSD, BINANCE:BTCUSDT)", err)
	}
	symbol = normalizedSymbol

	tf := flags.Get("tf")
	if tf == "" {
		tf = flags.Get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}
	bars := flags.GetInt("bars", 500)
	inputs := collectInputs(flags, 1, backtestReservedKeys)

	fmt.Fprintf(env.Stderr, "Backtesting %s on %s @ %s (%d bars)\n", pineID, symbol, tf, bars)
	if len(inputs) > 0 {
		fmt.Fprintf(env.Stderr, "  inputs: %v\n", inputs)
	}

	indicator, err := service.LoadIndicator(cfg, pineID, inputs, backtestReservedKeys)
	if err != nil {
		return fmt.Errorf("load strategy: %w", err)
	}
	if indicator.Schema != nil && !indicator.Schema.IsStrategy {
		fmt.Fprintf(env.Stderr, "⚠ %s is flagged isStrategy=false — running anyway; the backtest will be empty if it emits no trades.\n", pineID)
	}

	limits := config.GetTierLimits()
	calcTimeout := time.Duration(limits.CalcTimeoutSecs) * time.Second
	if calcTimeout == 0 {
		calcTimeout = 120 * time.Second
	}

	start := time.Now()
	res, err := service.RunScript(context.Background(), cfg, service.RunRequest{
		PineID:       pineID,
		Symbol:       symbol,
		Timeframe:    tf,
		Bars:         bars,
		Inputs:       inputs,
		ReservedKeys: backtestReservedKeys,
		SettleMs:     flags.GetInt("settle", 1500),
		CalcTimeout:  calcTimeout,
		Debug:        cfg.Debug,
	})
	if err != nil {
		return fmt.Errorf("backtest: %w", err)
	}
	durationMs := time.Since(start).Milliseconds()

	// Extract the strategy report (metrics + trades) from the du payload.
	sig := pipeline.Extract(pineID, symbol, tf, res.Periods, res.Graphic, res.StrategyReport, true)
	report := sig.Report
	if report == nil {
		rk := make([]string, 0, len(res.StrategyReport))
		for k := range res.StrategyReport {
			rk = append(rk, k)
		}
		return fmt.Errorf("no strategy report extracted — strategyReport keys: %v (periods=%d graphic=%d). Inspect raw with: tv run %s --raw", rk, len(res.Periods), len(res.Graphic), pineID)
	}
	if report.TotalTrades == 0 {
		fmt.Fprintf(env.Stderr, "⚠ Backtest produced 0 trades (no periods on this timeframe/tier, or the script is not a strategy).\n")
	}

	out := map[string]any{
		"pineId":     pineID,
		"symbol":     symbol,
		"timeframe":  tf,
		"bars":       bars,
		"durationMs": durationMs,
		"inputs":     inputs,
		"backtest":   report,
	}

	if flags.Has("json") || flags.Get("out") != "" {
		emitJSON(env, out, flags.Get("out"))
		return nil
	}
	fmt.Fprintln(env.Stdout, formatBacktest(pineID, symbol, tf, report))
	return nil
}

// formatBacktest renders the extracted StrategySummary as a readable report.
func formatBacktest(pineID, symbol, tf string, r *pipeline.StrategySummary) string {
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("Strategy backtest — %s\n", pineID))
	sb.WriteString(fmt.Sprintf("  Symbol/Timeframe:  %s @ %s\n", symbol, tf))
	if r.Currency != "" {
		sb.WriteString(fmt.Sprintf("  Currency:          %s\n", r.Currency))
	}
	sb.WriteString(fmt.Sprintf("  Net Profit:        %+.2f (%+.3f%%)\n", r.NetProfit, r.NetProfitPercent*100))
	sb.WriteString(fmt.Sprintf("  Gross Profit/Loss: %.2f / %.2f\n", r.GrossProfit, r.GrossLoss))
	sb.WriteString(fmt.Sprintf("  Profit Factor:     %.3f\n", r.ProfitFactor))
	sb.WriteString(fmt.Sprintf("  Total Trades:      %d (win %d / loss %d, %.1f%% profitable)\n",
		r.TotalTrades, r.WinningTrades, r.LosingTrades, r.WinRate*100))
	sb.WriteString(fmt.Sprintf("  Max Drawdown:      %.2f (%+.3f%%)\n", r.MaxDrawdown, r.MaxDDPercent*100))
	sb.WriteString(fmt.Sprintf("  Avg Trade:         %+.2f | Largest Win %.2f | Largest Loss %.2f\n",
		r.AvgTrade, r.LargestWin, r.LargestLoss))
	if r.SharpeRatio != 0 || r.SortinoRatio != 0 {
		sb.WriteString(fmt.Sprintf("  Sharpe/Sortino:    %.2f / %.2f\n", r.SharpeRatio, r.SortinoRatio))
	}
	if r.BuyHoldReturn != 0 {
		sb.WriteString(fmt.Sprintf("  Buy & Hold:        %.2f\n", r.BuyHoldReturn))
	}
	if r.CommissionPaid != 0 {
		sb.WriteString(fmt.Sprintf("  Commission Paid:   %.2f\n", r.CommissionPaid))
	}
	if len(r.Trades) > 0 {
		n := len(r.Trades)
		show := n
		if show > 8 {
			show = 8
		}
		sb.WriteString(fmt.Sprintf("  Trades (%d total; showing %d):\n", n, show))
		for _, t := range r.Trades[:show] {
			side := "SELL"
			if t.Side == "buy" || t.Side == "le" {
				side = "BUY "
			}
			sb.WriteString(fmt.Sprintf("    %s @ %.2f  pnl %+.2f\n", side, t.Entry, t.Profit))
		}
	}
	return sb.String()
}

func (c *backtestCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "backtest — Run a STRATEGY with custom inputs and extract its backtest results")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv backtest \"<pineId>\" [options]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Runs the strategy headlessly over the TradingView WebSocket (create_study),")
	fmt.Fprintln(w, "then extracts the performance report the server streams back in du frames:")
	fmt.Fprintln(w, "Net PnL, Profit Factor, Max Drawdown, win rate, and the executed trade list.")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --symbol EXCHANGE:SYMBOL  Market symbol (default: OANDA:XAUUSD)")
	fmt.Fprintln(w, "  --tf 5m                  Timeframe (default: 5m)")
	fmt.Fprintln(w, "  --bars 500               Number of bars (calc window)")
	fmt.Fprintln(w, "  --inputs '<json>'        Custom input overrides, e.g. '{\"length\": 20}'")
	fmt.Fprintln(w, "  --settle MS              Wait for the du recompute (default: 1500)")
	fmt.Fprintln(w, "  --json                   JSON output (metrics + full trade list)")
	fmt.Fprintln(w, "  --out FILE               Write JSON to a file")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv backtest \"STD;Bollinger%1Bands%1Strategy\" --symbol OANDA:XAUUSD --tf 2h --bars 500")
	fmt.Fprintln(w, "  tv backtest \"STD;RSI%1Strategy\" --inputs '{\"in_0\": 14}' --json")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Inputs may be passed as --inputs JSON, --input k=v, or positional k=v after")
	fmt.Fprintln(w, "the pine id. Input ids follow 'tvcli inputs <pineId>' (canonical in_N).")
}