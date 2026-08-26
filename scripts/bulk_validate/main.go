// Command bulk_validate reads a CSV of TradingView account cookies, validates
// each one against TradingView's auth endpoint, and merges the valid accounts
// into the account "pool" (the accounts.json sidecar used by the tvcli
// server's multi-account failover).
//
// CSV columns expected (case-insensitive header):
//
//	profile, username, sessionid, sessionid_sign, device_t
//
// Usage:
//
//	go run ./scripts/bulk_validate \
//	  -csv tv_free_accounts.csv \
//	  -out accounts.json \
//	  -concurrency 8 \
//	  -role adhoc -tier free \
//	  [-proxy socks5://127.0.0.1:1080] \
//	  [-timeout 20s] [-dry-run] [-overwrite] [-set-default]
//
// Validated accounts are keyed by a sanitized username (falling back to the
// profile label); collisions get a numeric suffix. Existing accounts in
// -out are preserved unless -overwrite is given, in which case the pool is
// rebuilt from only the valid CSV rows.
package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mrme000m/tvcli/pkg/account"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

var (
	csvPath     = flag.String("csv", "tv_free_accounts.csv", "path to the accounts CSV file")
	outPath     = flag.String("out", "accounts.json", "path to the accounts.json pool to write")
	proxy       = flag.String("proxy", "", "optional egress proxy for the validation requests")
	concurrency = flag.Int("concurrency", 8, "number of accounts to validate in parallel")
	role        = flag.String("role", "adhoc", "role to assign valid accounts (core|script|signal|adhoc)")
	tier        = flag.String("tier", "free", "tier to assign valid accounts when none is detected")
	timeout     = flag.Duration("timeout", 20*time.Second, "per-account validation timeout")
	dryRun      = flag.Bool("dry-run", false, "validate but do not write the pool")
	overwrite   = flag.Bool("overwrite", false, "rebuild the pool from valid rows only (drop existing entries)")
	setDefault  = flag.Bool("set-default", false, "set the first valid account as the pool default")
)

// knownTiers mirrors account.tiers so we can trust a detected plan string.
var knownTiers = map[string]bool{
	"free": true, "essential": true, "plus": true,
	"premium": true, "ultimate": true,
}

// csvRow is one parsed CSV record.
type csvRow struct {
	Profile   string
	Username  string
	SessionID string
	Signature string
	DeviceT   string
}

// result is the outcome of validating one CSV row.
type result struct {
	row    csvRow
	name   string
	valid  bool
	reason string
	plan   string
	user   string
}

// nonAlnum matches anything that isn't a safe account-name character.
var nonAlnum = regexp.MustCompile(`[^a-zA-Z0-9]+`)

// sanitizeName turns an arbitrary label into a safe account key.
func sanitizeName(s string) string {
	s = strings.TrimSpace(s)
	s = nonAlnum.ReplaceAllString(s, "-")
	s = strings.Trim(s, "-")
	if len(s) > 48 {
		s = s[:48]
	}
	return strings.ToLower(s)
}

// parseCSV reads the accounts CSV and maps each record to a csvRow using a
// case-insensitive header lookup.
func parseCSV(path string) ([]csvRow, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open csv: %w", err)
	}
	defer f.Close()

	r := csv.NewReader(f)
	r.FieldsPerRecord = -1 // tolerate ragged rows
	records, err := r.ReadAll()
	if err != nil {
		return nil, fmt.Errorf("read csv: %w", err)
	}
	if len(records) == 0 {
		return nil, fmt.Errorf("csv is empty")
	}

	header := records[0]
	idx := map[string]int{}
	for i, h := range header {
		idx[strings.ToLower(strings.TrimSpace(h))] = i
	}
	get := func(rec []string, key string) string {
		if i, ok := idx[key]; ok && i < len(rec) {
			return strings.TrimSpace(rec[i])
		}
		return ""
	}

	var rows []csvRow
	for _, rec := range records[1:] {
		rows = append(rows, csvRow{
			Profile:   get(rec, "profile"),
			Username:  get(rec, "username"),
			SessionID: get(rec, "sessionid"),
			Signature: get(rec, "sessionid_sign"),
			DeviceT:   get(rec, "device_t"),
		})
	}
	return rows, nil
}

// validate runs the auth check for one row.
func validate(row csvRow) result {
	res := result{row: row}

	if row.SessionID == "" {
		res.reason = "missing sessionid"
		return res
	}

	info := auth.FetchAuthInfo(
		row.SessionID, row.Signature, "", row.DeviceT,
		auth.WithProxy(*proxy),
	)
	if !info.Authenticated {
		if info.Error != nil {
			res.reason = info.Error.Error()
		} else {
			res.reason = "not authenticated"
		}
		return res
	}

	res.valid = true
	res.plan = info.Plan
	res.user = info.Username
	return res
}

func main() {
	flag.Parse()

	rows, err := parseCSV(*csvPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "csv: read %d rows from %s\n", len(rows), *csvPath)

	// Bounded concurrency over the validation step.
	sem := make(chan struct{}, *concurrency)
	var wg sync.WaitGroup
	results := make([]result, len(rows))
	var validCount, invalidCount int64

	for i, row := range rows {
		wg.Add(1)
		go func(i int, row csvRow) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			r := validate(row)
			if r.valid {
				atomic.AddInt64(&validCount, 1)
			} else {
				atomic.AddInt64(&invalidCount, 1)
			}
			results[i] = r
		}(i, row)
	}
	wg.Wait()

	// Build unique account names from valid results.
	used := map[string]bool{}
	var valid []result
	for i := range results {
		r := results[i]
		if !r.valid {
			continue
		}
		base := sanitizeName(r.row.Username)
		if base == "" {
			base = sanitizeName(r.row.Profile)
		}
		if base == "" {
			base = "acct"
		}
		name := base
		for n := 2; used[name]; n++ {
			name = fmt.Sprintf("%s-%d", base, n)
		}
		used[name] = true
		r.name = name
		results[i] = r
		valid = append(valid, r)
	}

	// Report.
	fmt.Fprintf(os.Stderr, "\n=== validation summary ===\n")
	fmt.Fprintf(os.Stderr, "total:   %d\n", len(rows))
	fmt.Fprintf(os.Stderr, "valid:   %d\n", validCount)
	fmt.Fprintf(os.Stderr, "invalid: %d\n", invalidCount)
	if *dryRun {
		for _, r := range results {
			if r.valid {
				fmt.Fprintf(os.Stderr, "  OK   %-20s plan=%-10s user=%s\n", r.name, r.plan, r.user)
			} else {
				src := r.row.Username
				if src == "" {
					src = r.row.Profile
				}
				fmt.Fprintf(os.Stderr, "  FAIL %-20s %s\n", src, r.reason)
			}
		}
		fmt.Fprintf(os.Stderr, "\n[dry-run] no pool written\n")
		return
	}

	// Load existing pool (unless overwriting).
	reg := account.NewRegistry()
	if !*overwrite {
		if existing, err := account.LoadFromJSON(*outPath); err == nil && existing != nil {
			reg = existing
		}
	}

	for _, r := range valid {
		t := *tier
		if r.plan != "" && knownTiers[r.plan] {
			t = r.plan
		}
		reg.Accounts[r.name] = account.Account{
			Name:        r.name,
			Role:        *role,
			SessionID:   r.row.SessionID,
			Signature:   r.row.Signature,
			DeviceToken: r.row.DeviceT,
			UserName:    r.user,
			Tier:        t,
			ProxyURL:    *proxy,
		}
	}

	if *setDefault && len(valid) > 0 && (reg.Default == "" || *overwrite) {
		reg.Default = valid[0].name
	}

	if err := reg.SaveToJSON(*outPath); err != nil {
		fmt.Fprintf(os.Stderr, "error writing pool: %v\n", err)
		os.Exit(1)
	}

	fmt.Fprintf(os.Stderr, "\nwrote %d valid accounts to %s (default=%q)\n",
		len(valid), *outPath, reg.Default)
}
