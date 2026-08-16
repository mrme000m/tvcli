// inputs_util.go — unified parsing of Pine script input overrides from CLI flags.
//
// tvcli accepts Pine input overrides in several spellings. The flag parser
// (internal/cli) stores every "--k[=v]" as a map entry and every bare token
// as a Positional arg, so the historically-documented forms
//
//	tv eval script.pine length=20            (positional key=value)
//	tv run "PUB;.." --input in_1=4           ("--input k=v")
//	tv <skill> --input length_volatility=20  ("--input k=v")
//
// did not survive flag parsing: positional "k=v" landed in Positional and
// "--input k=v" collapsed into a single "input" key whose value was the whole
// "k=v" string. collectInputs() reassembles all of them into one map so
// inputs actually reach the script runtime, whatever spelling the caller used.
package cmd

import (
	"strings"

	"github.com/ch99q/tvcli/internal/cli"
)

// collectInputs builds the map of Pine input overrides after CLI flag parsing.
//
//	spinePos is the count of leading positional args that are NOT input
//	overrides (e.g. the script path for `eval`, the pineId for `run`, a skill
//	name for a skill command). Only positional args at index >= spinePos are
//	inspected for "key=value" forms, so a pineId or file path is never treated
//	as an input.
//
// reserved lists flag keys that are CLI flags, not script inputs (symbol, tf,
// bars, json, ...); they are dropped even when a caller passes them via
// "--input" (unlikely, but safe).
//
// Sources, all merged into the returned map (later wins):
//  1. "--input k=v"  and  "--input k1=v1,k2=v2"   (single flag, comma or = split)
//  2. "--input.k=v"  dotted form (used by `tv analyze` / `tv agent`)
//  3. positional "k=v" args after spinePos
//  4. any other non-reserved "--k=v" flag  (e.g. "--in_1=4", "--length=20")
func collectInputs(flags cli.Flags, spinePos int, reserved []string) map[string]string {
	skip := make(map[string]bool, len(reserved))
	for _, k := range reserved {
		skip[k] = true
	}

	flagsOut := map[string]string{}
	one := func(k, v string) {
		if k == "" {
			return
		}
		if skip[k] {
			return
		}
		flagsOut[k] = v
	}

	// 1. --input key=value (and comma-separated lists)
	if v := flags.Get("input"); v != "" {
		for _, kv := range strings.Split(v, ",") {
			if i := strings.Index(kv, "="); i >= 0 {
				one(strings.TrimSpace(kv[:i]), strings.TrimSpace(kv[i+1:]))
			}
		}
	}

	// 2. --input.k=v dotted form
	for k, v := range flags.All() {
		if strings.HasPrefix(k, "input.") {
			one(strings.TrimPrefix(k, "input."), v)
		}
	}

	// 3. positional "k=v" after the spine (reader-provided overrides)
	for _, pos := range flags.Positional[spinePos:] {
		if i := strings.Index(pos, "="); i >= 0 {
			k := strings.TrimSpace(pos[:i])
			v := strings.TrimSpace(pos[i+1:])
			if k != "" {
				one(k, v)
			}
		}
	}

	// 4. plain non-reserved "--k=v" flags, so callers can target raw TV input
	//    IDs directly (--in_1=4) while stray CLI flags stay out.
	for k, v := range flags.All() {
		if k == "input" {
			continue
		}
		if strings.HasPrefix(k, "input.") {
			continue
		}
		one(k, v)
	}

	return flagsOut
}
