package pipeline

import (
	"fmt"
	"strings"
)

// TableGrid is a 2D table drawn by a Pine script via the dwgtables /
// dwgtablecells graphic draw types.
//
// TradingView emits the table geometry once (dwgtables) and each cell
// independently (dwgtablecells). ReconstructTables reassembles them into a
// row-major grid keyed by [row][col] so parsers can read headers and labeled
// rows without depending on cell-id ordering. This is the canonical way to
// read "dashboard" style indicators (multi-timeframe trend tables, volume
// profile summaries, etc.) that emit no period/plot data.
type TableGrid struct {
	ID    string
	Cols  int
	Rows  int
	Cells [][]string // Cells[r][c]; "" for empty/unset
}

// Header returns the first row (typically the column titles).
func (g TableGrid) Header() []string {
	if len(g.Cells) > 0 {
		return g.Cells[0]
	}
	return nil
}

// RowByLabel returns the index of the row whose first cell equals label
// (case-insensitive, trimmed), or -1 when not found.
func (g TableGrid) RowByLabel(label string) int {
	want := strings.ToLower(strings.TrimSpace(label))
	for r, row := range g.Cells {
		if len(row) > 0 && strings.ToLower(strings.TrimSpace(row[0])) == want {
			return r
		}
	}
	return -1
}

// cellText normalizes a raw cell object's text field ("t").
func cellText(v any) string {
	m, ok := v.(map[string]any)
	if !ok {
		return ""
	}
	if s, ok := m["t"].(string); ok {
		return s
	}
	return ""
}

// graphicInt is a small helper used by the table reconstructor to coerce
// either a float64, int, or numeric-string cell field to an int.
func graphicInt(v any, key string) int {
	m, ok := v.(map[string]any)
	if !ok {
		return 0
	}
	switch n := m[key].(type) {
	case float64:
		return int(n)
	case int:
		return n
	case string:
		var i int
		if _, err := fmt.Sscanf(n, "%d", &i); err == nil {
			return i
		}
	}
	return 0
}

// ReconstructTables rebuilds every dwgtable present in graphic. Tables with
// no populated cells are skipped. It returns nil when graphic has no table
// draw types at all.
func ReconstructTables(graphic map[string]map[string]any) []TableGrid {
	if graphic == nil {
		return nil
	}
	rawTables, ok := graphic["dwgtables"]
	if !ok || len(rawTables) == 0 {
		return nil
	}
	rawCells, _ := graphic["dwgtablecells"]

	// Index cells by (tid, row, col) for O(1) lookup while filling the grid.
	// The tid (table id) on each cell ensures correctness when multiple
	// tables are present — without it, cells from different tables could
	// collide at the same (row, col) position.
	cellByTidRC := map[[3]int]string{}
	for _, cv := range rawCells {
		m, ok := cv.(map[string]any)
		if !ok {
			continue
		}
		r, c := graphicInt(m, "row"), graphicInt(m, "col")
		// Prefer the cell's explicit tid; fall back to 0 for legacy cells.
		tid := graphicInt(m, "tid")
		if t := cellText(m); t != "" {
			cellByTidRC[[3]int{tid, r, c}] = t
		}
	}

	var grids []TableGrid
	for id, tv := range rawTables {
		tm, ok := tv.(map[string]any)
		if !ok {
			continue
		}
		cols, rows := graphicInt(tm, "cols"), graphicInt(tm, "rows")
		if cols <= 0 || rows <= 0 {
			continue
		}
		grid := make([][]string, rows)
		for r := 0; r < rows; r++ {
			grid[r] = make([]string, cols)
		}
		populated := 0
		for r := 0; r < rows; r++ {
			for c := 0; c < cols; c++ {
				// Try with the table's own id first, then fallback to tid=0.
				if t, ok := cellByTidRC[[3]int{parseTableID(id), r, c}]; ok {
					grid[r][c] = t
					populated++
				} else if t, ok := cellByTidRC[[3]int{0, r, c}]; ok {
					grid[r][c] = t
					populated++
				}
			}
		}
		if populated == 0 {
			continue
		}
		grids = append(grids, TableGrid{ID: id, Cols: cols, Rows: rows, Cells: grid})
	}
	return grids
}

// parseTableID coerces a table ID (which may be float64 from JSON decode) to string.
func parseTableID(v any) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	case string:
		var i int
		if _, err := fmt.Sscanf(n, "%d", &i); err == nil {
			return i
		}
	}
	return 0
}

// GraphicType reports which graphic draw types are present, so callers can
// pick the right resolution strategy (table vs labels vs lines/boxes).
func GraphicType(graphic map[string]map[string]any) []string {
	if graphic == nil {
		return nil
	}
	var types []string
	for k, v := range graphic {
		if len(v) > 0 {
			types = append(types, k)
		}
	}
	return types
}
