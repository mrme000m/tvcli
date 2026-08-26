// bdg_util.go — shared helpers for commands that drive the LIVE chart through
// bdg (the browser-debugger CLI). Keeps path resolution + exec in one place.
package cmd

import (
	"os"
	"os/exec"
	"strings"
)

// bdgPath returns the bdg invocation string: the local dist entrypoint run via
// node, a global bdg binary, or "bdg" resolved from PATH — whichever exists.
func bdgPath() string {
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

// runBDGCmd executes the given bdg invocation with args and returns stdout.
// Stderr is passed through to the process's own stderr.
func runBDGCmd(bdg string, args []string) ([]byte, error) {
	parts := strings.Fields(bdg)
	cmd := exec.Command(parts[0], append(parts[1:], args...)...)
	cmd.Stderr = os.Stderr
	return cmd.Output()
}