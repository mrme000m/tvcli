// Package cli is a tiny command framework: FlagSet, Command interface, and a
// Root that dispatches by name. Intentionally smaller than cobra — just enough
// for tvcli's 12 subcommands plus runtime-registered skill commands.
package cli

import (
	"fmt"
	"io"
	"os"
	"strings"
)

// Flags is a parsed --key=value / --key value / positional args bag.
type Flags struct {
	Positional []string
	flags      map[string]string
	multi      map[string][]string // every occurrence, in order (repeatable flags)
}

// ParseFlags parses args into a Flags. Flags beginning with -- set key/value
// (either --k=v or --k v); single-char -x is treated the same way. Anything
// else is positional. A repeated flag keeps last-wins semantics for Get but
// every occurrence is recorded and readable via GetAll, so repeatable flags
// like --input are never silently dropped.
func ParseFlags(args []string) Flags {
	fs := Flags{flags: make(map[string]string), multi: make(map[string][]string)}
	set := func(key, val string) {
		fs.flags[key] = val
		fs.multi[key] = append(fs.multi[key], val)
	}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if strings.HasPrefix(a, "--") {
			key := strings.TrimPrefix(a, "--")
			if idx := strings.Index(key, "="); idx >= 0 {
				set(key[:idx], key[idx+1:])
			} else if i+1 < len(args) && !strings.HasPrefix(args[i+1], "-") {
				set(key, args[i+1])
				i++
			} else {
				set(key, "true")
			}
		} else if strings.HasPrefix(a, "-") && len(a) == 2 {
			key := string(a[1])
			if i+1 < len(args) && !strings.HasPrefix(args[i+1], "-") {
				set(key, args[i+1])
				i++
			} else {
				set(key, "true")
			}
		} else {
			fs.Positional = append(fs.Positional, a)
		}
	}
	return fs
}

func (fs Flags) Has(key string) bool {
	_, ok := fs.flags[key]
	return ok
}

func (fs Flags) Get(key string) string {
	return fs.flags[key]
}

// GetAll returns every occurrence of a repeated flag in argument order
// (nil when absent). Get returns only the last occurrence; use GetAll for
// flags that are documented as repeatable (e.g. --input k=v --input k2=v2).
func (fs Flags) GetAll(key string) []string {
	vs := fs.multi[key]
	out := make([]string, len(vs))
	copy(out, vs)
	return out
}

// AllMulti returns a copy of the full occurrence map (key → values in
// argument order). Use it when consuming repeatable dotted flags such as
// --input.k=v passed more than once.
func (fs Flags) AllMulti() map[string][]string {
	out := make(map[string][]string, len(fs.multi))
	for k, vs := range fs.multi {
		cp := make([]string, len(vs))
		copy(cp, vs)
		out[k] = cp
	}
	return out
}

func (fs Flags) GetInt(key string, def int) int {
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

// All returns a copy of the raw flag map. Use this when passing inputs
// through to a service that consumes arbitrary key/value pairs.
func (fs Flags) All() map[string]string {
	out := make(map[string]string, len(fs.flags))
	for k, v := range fs.flags {
		out[k] = v
	}
	return out
}

// Command is one subcommand. Implementations live in internal/cmd and
// internal/skillcmd.
type Command interface {
	Name() string
	Aliases() []string
	Synopsis() string
	Run(env *Env) error
}

// Env is the per-invocation context passed to a Command.
type Env struct {
	Args   []string
	Flags  Flags
	Stdout io.Writer
	Stderr io.Writer
}

// Root is a command registry + dispatcher.
type Root struct {
	cmds     []Command
	byName   map[string]Command
	helpFunc func(*Root, io.Writer)
}

// NewRoot returns an empty root. Use Add to register commands.
func NewRoot() *Root {
	return &Root{byName: make(map[string]Command)}
}

// Add registers a command under its Name() and Aliases(). Last-wins.
func (r *Root) Add(c Command) {
	r.cmds = append(r.cmds, c)
	r.byName[c.Name()] = c
	for _, a := range c.Aliases() {
		r.byName[a] = c
	}
}

// Commands returns the registered commands in insertion order.
func (r *Root) Commands() []Command { return r.cmds }

// Lookup finds a command by name or alias. Returns nil if not found.
func (r *Root) Lookup(name string) Command { return r.byName[name] }

// SetHelp installs a help renderer used by Execute when no args / --help.
func (r *Root) SetHelp(fn func(*Root, io.Writer)) { r.helpFunc = fn }

// Execute dispatches args[0] to the matching command. If args is empty or
// the command name is "help"/"--help"/"-h", it prints help via SetHelp (or
// a default that lists commands) and returns nil.
func (r *Root) Execute(args []string, stdout, stderr io.Writer) error {
	if len(args) == 0 {
		r.printHelp(stdout)
		return nil
	}
	name := args[0]
	if name == "help" || name == "--help" || name == "-h" {
		r.printHelp(stdout)
		return nil
	}
	cmd := r.byName[name]
	if cmd == nil {
		fmt.Fprintf(stderr, "Unknown command: %s\n", name)
		r.printHelp(stderr)
		return fmt.Errorf("unknown command: %s", name)
	}
	env := &Env{
		Args:   args[1:],
		Flags:  ParseFlags(args[1:]),
		Stdout: stdout,
		Stderr: stderr,
	}
	return cmd.Run(env)
}

func (r *Root) printHelp(w io.Writer) {
	if r.helpFunc != nil {
		r.helpFunc(r, w)
		return
	}
	fmt.Fprintln(w, "Commands:")
	for _, c := range r.cmds {
		fmt.Fprintf(w, "  %s\t%s\n", c.Name(), c.Synopsis())
	}
}

// Fatal prints an error to stderr and exits with code 1. Convenience for
// command implementations that don't want to propagate errors all the way up.
func Fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "Error: "+format+"\n", args...)
	os.Exit(1)
}
