package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/mrme000m/tvcli/internal/cli"
)

// execRaw handles the shared --raw / --raw-out / --out dumping used by both
// the skill and run commands. It returns true when the caller should stop
// (the raw payload was printed to stdout as the final output), so callers can
// keep their existing post-raw control flow in one place.
//
// Behaviour (identical for both commands):
//   - --raw-out FILE  -> write FILE, continue
//   - --out FILE      -> write FILE.raw.json, continue
//   - --raw (stdout)  -> print, stop unless --json is also set
func execRaw(env *cli.Env, payload map[string]any, flags cli.Flags) bool {
	rawOut := flags.Get("raw-out")
	if !flags.Has("raw") && rawOut == "" {
		return false
	}
	rawJSON, _ := json.MarshalIndent(payload, "", "  ")

	dest := ""
	switch {
	case rawOut != "" && rawOut != "true":
		dest = rawOut
	case flags.Get("out") != "":
		dest = flags.Get("out") + ".raw.json"
	}

	if dest != "" {
		os.WriteFile(dest, rawJSON, 0644)
		fmt.Fprintf(env.Stderr, "✓ Raw dump: %s\n", dest)
		return false
	}

	fmt.Fprintln(env.Stdout, string(rawJSON))
	return !flags.Has("json")
}

// emitJSON marshals v and writes it to --out (or stdout). A short confirmation
// line is printed to stderr when a file is written.
func emitJSON(env *cli.Env, v any, out string) {
	b, _ := json.MarshalIndent(v, "", "  ")
	if out != "" {
		os.WriteFile(out, b, 0644)
		fmt.Fprintf(env.Stderr, "✓ Saved: %s\n", out)
		return
	}
	fmt.Fprintln(env.Stdout, string(b))
}

// emitText writes text to --out (or stdout), with the same confirmation
// convention as emitJSON.
func emitText(env *cli.Env, text string, out string) {
	if out != "" {
		os.WriteFile(out, []byte(text), 0644)
		fmt.Fprintf(env.Stderr, "✓ Saved: %s\n", out)
		return
	}
	fmt.Fprintln(env.Stdout, text)
}
