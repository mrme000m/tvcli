package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

type layoutsCmd struct{ app *App }

func (c *layoutsCmd) Name() string      { return "layouts" }
func (c *layoutsCmd) Aliases() []string { return []string{"charts"} }
func (c *layoutsCmd) Synopsis() string  { return "List the authenticated user's saved chart layouts" }

// Run lists saved TradingView chart layouts via GET /my-charts/?limit=N
// (the same endpoint the web app's "Manage layouts" dialog uses).
func (c *layoutsCmd) Run(env *cli.Env) error {
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
	fmt.Fprintf(env.Stdout, "%-24s %-22s %-8s %-12s %s\n", "NAME", "SYMBOL", "TF", "ID", "MODIFIED")
	for _, ch := range charts {
		tf := ch.Interval
		if tf == "" {
			tf = ch.Resolution
		}
		fmt.Fprintf(env.Stdout, "%-24s %-22s %-8s %-12d %s\n",
			truncateStr(ch.Name, 24), truncateStr(ch.Symbol, 22), tf, ch.ID, ch.Modified)
	}
	if len(charts) > 0 {
		fmt.Fprintf(env.Stdout, "\nOpen: https://www.tradingview.com/chart/%s/\n", charts[0].URL)
	}
	return nil
}

func truncateStr(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}