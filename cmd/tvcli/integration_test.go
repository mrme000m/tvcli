//go:build integration

package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/ch99q/tvcli/internal/config"
)

var (
	projectRoot string
	tvcliBin    string
	hasAuth     bool
)

func TestMain(m *testing.M) {
	projectRoot = findProjectRoot()
	if projectRoot == "" {
		fmt.Fprintln(os.Stderr, "could not find project root (go.mod)")
		os.Exit(1)
	}

	bin := filepath.Join(os.TempDir(), fmt.Sprintf("tvcli_integration_%d", time.Now().UnixNano()))
	build := exec.Command("go", "build", "-o", bin, "./cmd/tvcli")
	build.Dir = projectRoot
	build.Stderr = os.Stderr
	if err := build.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "failed to build tvcli for integration tests: %v\n", err)
		os.Exit(1)
	}
	tvcliBin = bin
	defer os.Remove(bin)

	cfg := config.Load()
	hasAuth = cfg.HasAuth()

	os.Exit(m.Run())
}

func findProjectRoot() string {
	_, file, _, _ := runtime.Caller(0)
	dir := filepath.Dir(file)
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func runCmd(t *testing.T, args ...string) (stdout, stderr string, exitCode int) {
	t.Helper()
	cmd := exec.Command(tvcliBin, args...)
	cmd.Dir = projectRoot
	var outb, errb bytes.Buffer
	cmd.Stdout = &outb
	cmd.Stderr = &errb
	err := cmd.Run()
	stdout = outb.String()
	stderr = errb.String()
	if exitErr, ok := err.(*exec.ExitError); ok {
		exitCode = exitErr.ExitCode()
	} else if err != nil {
		exitCode = -1
	}
	t.Logf("---- tvcli %s ----\nexit: %d\nstdout:\n%s\nstderr:\n%s\n---- end ----",
		strings.Join(args, " "), exitCode, stdout, stderr)
	return
}

func mustExit0(t *testing.T, args ...string) (string, string) {
	t.Helper()
	out, errOut, code := runCmd(t, args...)
	if code != 0 {
		t.Fatalf("expected exit 0, got %d for `tvcli %s`", code, strings.Join(args, " "))
	}
	return out, errOut
}

func writeTempPine(t *testing.T, src string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.pine")
	if err := os.WriteFile(path, []byte(src), 0644); err != nil {
		t.Fatalf("write pine file: %v", err)
	}
	return path
}

func TestIntegration_Help(t *testing.T) {
	out, _ := mustExit0(t, "help")
	if !strings.Contains(out, "Commands:") {
		t.Fatal("help output missing 'Commands:'")
	}
}

func TestIntegration_List(t *testing.T) {
	out, _ := mustExit0(t, "list")
	if !strings.Contains(out, "Tracked Scripts") && !strings.Contains(out, "No scripts tracked") {
		t.Fatalf("unexpected `list` output: %s", out)
	}
}

func TestIntegration_ListAlias(t *testing.T) {
	out, _ := mustExit0(t, "ls")
	if !strings.Contains(out, "Tracked Scripts") && !strings.Contains(out, "No scripts tracked") {
		t.Fatalf("unexpected `ls` output: %s", out)
	}
}

func TestIntegration_PubList(t *testing.T) {
	if !hasAuth {
		t.Skip("publist requires SESSION cookie")
	}
	out, _ := mustExit0(t, "publist", "--limit", "1")
	if !strings.Contains(out, "Public scripts:") {
		t.Fatalf("unexpected `publist` output: %s", out)
	}
}

func TestIntegration_Search(t *testing.T) {
	if !hasAuth {
		t.Skip("search requires SESSION cookie")
	}
	out, _ := mustExit0(t, "search", "RSI", "--limit", "1", "--json")
	if !strings.Contains(out, "results") {
		t.Fatalf("unexpected `search` output: %s", out)
	}
}

func TestIntegration_Top(t *testing.T) {
	if !hasAuth {
		t.Skip("top requires SESSION cookie")
	}
	dir := t.TempDir()
	out, _ := mustExit0(t, "top", "--limit", "1", "--output", filepath.Join(dir, "top.json"))
	if !strings.Contains(out, "Saved") {
		t.Fatalf("unexpected `top` output: %s", out)
	}
}

func TestIntegration_Compile(t *testing.T) {
	if !hasAuth {
		t.Skip("compile requires SESSION cookie")
	}
	pine := writeTempPine(t, "//@version=5\nindicator(\"Integration Test\")\nplot(close)\n")
	out, _ := mustExit0(t, "compile", pine)
	if !strings.Contains(out, "{") {
		t.Fatalf("unexpected `compile` output: %s", out)
	}
}

func TestIntegration_Fetch(t *testing.T) {
	dir := t.TempDir()
	out, errOut := mustExit0(t, "fetch", "--symbol", "BINANCE:BTCUSDT", "--tf", "1d", "--bars", "3", "--dir", dir)
	if !strings.Contains(out, "Fetched") && !strings.Contains(errOut, "Received") {
		t.Fatalf("unexpected `fetch` output: stdout=%s stderr=%s", out, errOut)
	}
}

func TestIntegration_Sync(t *testing.T) {
	dir := t.TempDir()
	out, errOut := mustExit0(t, "sync",
		"--symbol", "BINANCE:BTCUSDT", "--tf", "1d", "--bars", "3",
		"--dir", dir,
		"--out", filepath.Join(dir, "BTCUSDT_1d.json.gz"),
	)
	combined := out + errOut
	if !strings.Contains(combined, "Saved:") {
		t.Fatalf("unexpected `sync` output: %s", combined)
	}
}

func TestIntegration_Run(t *testing.T) {
	if !hasAuth {
		t.Skip("run requires SESSION cookie")
	}
	out, errOut := mustExit0(t, "run", "PUB;131",
		"--symbol", "OANDA:XAUUSD", "--tf", "1D", "--bars", "2",
		"--settle", "500", "--json",
	)
	if !strings.Contains(out, "{") && !strings.Contains(errOut, "Study data received") {
		t.Fatalf("unexpected `run` output: stdout=%s stderr=%s", out, errOut)
	}
}

func TestIntegration_PullPushRun(t *testing.T) {
	if !hasAuth {
		t.Skip("pull/push/run requires SESSION cookie")
	}

	dir := t.TempDir()
	t.Setenv("TV_DATA_DIR", dir)
	t.Setenv("TV_META_FILE", filepath.Join(dir, ".tv-meta.json"))

	// 1. Pull a known public indicator.
	const pubPineID = "PUB;131"
	pullOut, _ := mustExit0(t, "pull", pubPineID)
	matches := regexp.MustCompile(`Tracked as #(\d+)`).FindStringSubmatch(pullOut)
	if len(matches) < 2 {
		t.Fatalf("could not extract local ID from pull output: %s", pullOut)
	}
	localID := matches[1]

	// 2. Push it back to the account (creates a private copy).
	pushOut, _ := mustExit0(t, "push", localID)
	pushMatches := regexp.MustCompile(`Pushed:\s+(USER;\S+)`).FindStringSubmatch(pushOut)
	if len(pushMatches) < 2 {
		t.Fatalf("could not extract pushed pineId from push output: %s", pushOut)
	}
	pushedPineID := pushMatches[1]

	// 3. Run the pushed copy on BTCUSD with custom inputs.
	runOut, runErr := mustExit0(t, "run", pushedPineID,
		"--symbol", "BINANCE:BTCUSDT",
		"--tf", "1d",
		"--bars", "5",
		"--settle", "500",
		"--in_0", "21",
		"--in_1", "80",
		"--in_2", "20",
		"--json",
	)
	combined := runOut + runErr
	if !strings.Contains(combined, `"extracted"`) && !strings.Contains(combined, "Study data received") {
		t.Fatalf("run did not produce expected output: %s", combined)
	}

	// 4. Clean up the pushed copy.
	mustExit0(t, "delete", localID, "--yes")
}
