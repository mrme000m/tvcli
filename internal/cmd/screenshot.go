// screenshot.go — capture chart screenshots using bdg's CDP connection.
//
// The go tvcli drives studies headlessly over the WebSocket API and cannot
// capture screenshots directly. This command bridges to bdg (the local
// browser-debugger-cli) which maintains a CDP connection to a live TradingView
// chart page and can capture full-page, viewport, or element screenshots.
package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
)

type screenshotCmd struct{ app *App }

func (c *screenshotCmd) Name() string     { return "screenshot" }
func (c *screenshotCmd) Aliases() []string { return []string{"shot", "cap"} }
func (c *screenshotCmd) Synopsis() string  { return "Capture chart screenshot via bdg (requires live browser session)" }

func (c *screenshotCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	// Output path: --out flag, else first positional, else timestamp default.
	outputPath := flags.Get("out")
	if outputPath == "" && len(flags.Positional) > 0 {
		outputPath = flags.Positional[0]
	}
	if outputPath == "" {
		ts := time.Now().Format("20060102-150405")
		outputPath = fmt.Sprintf("tv-screenshot-%s.png", ts)
	}

	// Options
	fullPage := flags.Has("full") || flags.Has("full-page")
	selector := flags.Get("selector")
	format := flags.Get("format")
	if format == "" {
		format = "png"
	}
	quality := flags.GetInt("quality", 90)
	noResize := flags.Has("no-resize")
	scrollTo := flags.Get("scroll")

	// Build bdg command
	bdgPath := c.resolveBDGPath()
	args := []string{"dom", "screenshot", outputPath}

	if fullPage {
		args = append(args, "--full-page")
	}
	if selector != "" {
		args = append(args, "--selector", selector)
	}
	if scrollTo != "" {
		args = append(args, "--scroll", scrollTo)
	}
	if format != "png" {
		args = append(args, "--format", format)
	}
	if quality != 90 {
		args = append(args, "--quality", fmt.Sprintf("%d", quality))
	}
	if noResize {
		args = append(args, "--no-resize")
	}

	fmt.Fprintf(env.Stderr, "📸 Capturing screenshot via bdg...\n")
	fmt.Fprintf(env.Stderr, "   %s %s\n", bdgPath, strings.Join(args, " "))

	// resolveBDGPath may return "node <entry.js>" when bdg isn't on PATH;
	// split it into executable + args so exec.Command runs it correctly.
	bdgParts := strings.Fields(bdgPath)
	cmd := exec.Command(bdgParts[0], append(bdgParts[1:], args...)...)
	cmd.Stdout = env.Stdout
	cmd.Stderr = env.Stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("bdg screenshot failed: %w\n\nHint: Ensure bdg is attached to a TradingView chart tab:\n  1. node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/\n  2. Then run this command", err)
	}

	fmt.Fprintf(env.Stderr, "✓ Screenshot saved: %s\n", outputPath)
	return nil
}

// resolveBDGPath returns the bdg executable. When a .js entrypoint is found
// (no bdg on PATH), the returned value is the full command line to run,
// so exec.Command is given the split argv in that case.
func (c *screenshotCmd) resolveBDGPath() string {
	// Check common locations
	paths := []string{
		"/Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js",
		"/Volumes/Spare/npm/global/bin/bdg",
		"bdg", // on PATH
	}
	for _, p := range paths {
		if strings.HasSuffix(p, ".js") {
			if _, err := os.Stat(p); err == nil {
				// Use `node <entry>` as the executable so exec.Command works.
				return "node " + p
			}
			continue
		}
		if _, err := exec.LookPath(p); err == nil {
			return p
		}
	}
	return "bdg" // fallback to PATH
}

func (c *screenshotCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "screenshot — Capture TradingView chart screenshot")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv screenshot [output.png] [options]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "This command uses bdg (browser-debugger-cli) which must be attached")
	fmt.Fprintln(w, "to a live TradingView chart tab via CDP. Start bdg first:")
	fmt.Fprintln(w, "  node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --out FILE           Output file path (default: tv-screenshot-TIMESTAMP.png)")
	fmt.Fprintln(w, "  --full, --full-page  Capture full page (default: viewport)")
	fmt.Fprintln(w, "  --selector CSS       Capture specific element by CSS selector")
	fmt.Fprintln(w, "  --scroll SELECTOR    Scroll to element before capture")
	fmt.Fprintln(w, "  --format png|jpeg    Output format (default: png)")
	fmt.Fprintln(w, "  --quality N          JPEG quality 1-100 (default: 90)")
	fmt.Fprintln(w, "  --no-resize          Disable auto-resize for large pages")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv screenshot chart.png")
	fmt.Fprintln(w, "  tv screenshot --full --out full_chart.png")
	fmt.Fprintln(w, "  tv screenshot --selector '.chart-container' --out chart_only.png")
	fmt.Fprintln(w, "  tv screenshot --scroll '.chart-area' --out scrolled.png")
}