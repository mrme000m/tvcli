package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/server"
)

const defaultServeAddr = ":8765"

// pidFilePath returns the path to the tvcli server PID file.
func pidFilePath() string {
	exe, err := os.Executable()
	if err == nil {
		return filepath.Join(filepath.Dir(exe), ".tvcli-server.pid")
	}
	return ".tvcli-server.pid"
}

// logFilePath returns the path for the background server log.
func logFilePath() string {
	exe, err := os.Executable()
	if err == nil {
		return filepath.Join(filepath.Dir(exe), ".tvcli-server.log")
	}
	return ".tvcli-server.log"
}

type serveCmd struct{ app *App }

func (c *serveCmd) Name() string      { return "serve" }
func (c *serveCmd) Aliases() []string { return []string{"server"} }
func (c *serveCmd) Synopsis() string  { return "Start/stop/status HTTP server for AI agent integration" }

func (c *serveCmd) Run(env *cli.Env) error {
	flags := env.Flags

	// Subcommands via flags
	if flags.Has("stop") {
		return c.stopServer(env)
	}
	if flags.Has("status") {
		return c.statusServer(env)
	}

	addr := flags.Get("addr")
	if addr == "" {
		addr = defaultServeAddr
	}
	if len(addr) > 0 && addr[0] != ':' && !strings.Contains(addr, ":") {
		addr = ":" + addr
	}

	// --daemon / -d: run in background
	if flags.Has("daemon") || flags.Has("d") {
		return c.startDaemon(env, addr)
	}

	// --background: internal flag used by the daemon fork (foreground mode,
	// suppress the endpoint banner since output goes to a log file).
	if flags.Has("background") {
		return c.runBackground(addr)
	}

	// Default: foreground (blocks the terminal)
	return c.runForeground(env, addr)
}

// runForeground starts the server in the current process (blocks).
func (c *serveCmd) runForeground(env *cli.Env, addr string) error {
	c.printEndpoints(env.Stderr, addr)
	srv := server.New(c.app.Config)
	return srv.Serve(addr)
}

// runBackground is the internal mode used by the daemon fork. It starts the
// server without printing the banner (output goes to a log file).
func (c *serveCmd) runBackground(addr string) error {
	srv := server.New(c.app.Config)
	return srv.Serve(addr)
}

// startDaemon forks the current process to run the server in the background.
func (c *serveCmd) startDaemon(env *cli.Env, addr string) error {
	// Check if already running.
	if pid := readPIDFile(); pid > 0 {
		if isProcessAlive(pid) {
			return fmt.Errorf("server already running (PID %d). Use --stop first, or --status to check", pid)
		}
		os.Remove(pidFilePath()) // stale PID file
	}

	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve executable: %w", err)
	}

	// Open log file for the background process.
	logFile, err := os.OpenFile(logFilePath(), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return fmt.Errorf("open log file: %w", err)
	}

	cmd := exec.Command(exe, "serve", "--addr", addr, "--background")
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true} // detach from terminal

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return fmt.Errorf("start background server: %w", err)
	}

	pid := cmd.Process.Pid
	writePIDFile(pid)
	logFile.Close()

	// Wait briefly to check if it started successfully.
	time.Sleep(500 * time.Millisecond)
	if !isProcessAlive(pid) {
		os.Remove(pidFilePath())
		return fmt.Errorf("server process exited immediately — check %s", logFilePath())
	}

	fmt.Fprintf(env.Stdout, "✓ Server started in background (PID %d) on %s\n", pid, addr)
	fmt.Fprintf(env.Stdout, "  Logs: %s\n", logFilePath())
	fmt.Fprintf(env.Stdout, "  Stop:   tvcli serve --stop\n")
	fmt.Fprintf(env.Stdout, "  Status: tvcli serve --status\n")
	return nil
}

// stopServer reads the PID file and kills the background server.
func (c *serveCmd) stopServer(env *cli.Env) error {
	pid := readPIDFile()
	if pid <= 0 {
		fmt.Fprintln(env.Stdout, "Server is not running (no PID file).")
		return nil
	}
	if !isProcessAlive(pid) {
		os.Remove(pidFilePath())
		fmt.Fprintln(env.Stdout, "Server was not running (stale PID file removed).")
		return nil
	}

	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		return fmt.Errorf("kill server PID %d: %w", pid, err)
	}

	// Wait up to 3 seconds for graceful shutdown.
	for i := 0; i < 30; i++ {
		time.Sleep(100 * time.Millisecond)
		if !isProcessAlive(pid) {
			break
		}
	}
	if isProcessAlive(pid) {
		syscall.Kill(pid, syscall.SIGKILL)
		time.Sleep(100 * time.Millisecond)
	}

	os.Remove(pidFilePath())
	fmt.Fprintf(env.Stdout, "✓ Server stopped (PID %d)\n", pid)
	return nil
}

// statusServer checks if the background server is running and queries /health.
func (c *serveCmd) statusServer(env *cli.Env) error {
	pid := readPIDFile()
	running := pid > 0 && isProcessAlive(pid)

	if env.Flags.Has("json") {
		result := map[string]any{"running": running}
		if running {
			result["pid"] = pid
			if health := queryServerHealth(env.Flags.Get("addr")); health != nil {
				result["health"] = health
			}
		}
		if pid > 0 && !running {
			result["stalePid"] = pid
		}
		b, _ := json.MarshalIndent(result, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	if !running {
		if pid > 0 {
			os.Remove(pidFilePath())
			fmt.Fprintln(env.Stdout, "✗ Server is not running (stale PID file removed).")
		} else {
			fmt.Fprintln(env.Stdout, "✗ Server is not running.")
		}
		fmt.Fprintln(env.Stdout, "  Start: tvcli serve --daemon")
		return nil
	}

	fmt.Fprintf(env.Stdout, "✓ Server running (PID %d)\n", pid)

	addr := env.Flags.Get("addr")
	if addr == "" {
		addr = defaultServeAddr
	}
	if health := queryServerHealth(addr); health != nil {
		fmt.Fprintf(env.Stdout, "  Tier: %v | Auth: %v | User: %v\n",
			health["tier"], health["authenticated"], health["user"])
		fmt.Fprintf(env.Stdout, "  Max bars: %v | Max indicators: %v | Calc timeout: %vs\n",
			health["maxBars"], health["maxIndicators"], health["calcTimeoutSecs"])
	} else {
		fmt.Fprintf(env.Stdout, "  ⚠ Health check failed (server may still be starting)\n")
	}

	fmt.Fprintf(env.Stdout, "  Logs: %s\n", logFilePath())
	fmt.Fprintf(env.Stdout, "  Stop: tvcli serve --stop\n")
	return nil
}

// queryServerHealth fetches /health from the server at the given address.
func queryServerHealth(addr string) map[string]any {
	if addr == "" {
		addr = defaultServeAddr
	}
	if !strings.HasPrefix(addr, "http") {
		addr = "http://localhost" + addr
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(addr + "/health")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	var h map[string]any
	json.NewDecoder(resp.Body).Decode(&h)
	return h
}

// printEndpoints prints the server endpoint summary to the writer.
func (c *serveCmd) printEndpoints(w io.Writer, addr string) {
	fmt.Fprintf(w, "tvcli server endpoints:\n")
	fmt.Fprintf(w, "  GET  /health       — status check (auth + tier + account pool + failover)\n")
	fmt.Fprintf(w, "  GET  /check-auth   — verify auth cookies & subscription tier (?account=NAME)\n")
	fmt.Fprintf(w, "  GET  /skills       — registered Pine indicator skills\n")
	fmt.Fprintf(w, "  GET  /accounts     — account registry (masked)\n")
	fmt.Fprintf(w, "  GET  /queue-stats  — per-account concurrency usage\n")
	fmt.Fprintf(w, "  POST /compile      — compile Pine script source\n")
	fmt.Fprintf(w, "  POST /fetch        — fetch OHLCV data (account failover)\n")
	fmt.Fprintf(w, "  POST /clean        — clean chart sessions\n")
	fmt.Fprintf(w, "  POST /run          — compile + run Pine script (account failover)\n")
	fmt.Fprintf(w, "  POST /run-skill    — run a registered skill by name (account failover)\n")
	fmt.Fprintf(w, "  POST /hunt         — batch skill run across many symbols (account pool)\n")
	fmt.Fprintf(w, "\n")
}

// --- PID file helpers -------------------------------------------------------

func readPIDFile() int {
	data, err := os.ReadFile(pidFilePath())
	if err != nil {
		return 0
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return 0
	}
	return pid
}

func writePIDFile(pid int) {
	os.WriteFile(pidFilePath(), []byte(strconv.Itoa(pid)), 0644)
}

func isProcessAlive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}

// ServerRunning returns true if the background server is running.
// Used by other commands to include server state in their output.
func ServerRunning() bool {
	pid := readPIDFile()
	return pid > 0 && isProcessAlive(pid)
}

// ServerHealth returns the /health response from the background server,
// or nil if it's not running.
func ServerHealth() map[string]any {
	if !ServerRunning() {
		return nil
	}
	return queryServerHealth(defaultServeAddr)
}
