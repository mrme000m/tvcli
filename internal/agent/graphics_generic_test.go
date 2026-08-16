package agent

import (
	"encoding/json"
	"os"
	"testing"
)

func TestVPGroups(t *testing.T) {
	data, err := os.ReadFile("../../skill_work/p2t_vp/vp_default_raw.json.raw.json")
	if err != nil {
		t.Skip("no raw data file")
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	// graphic comes as map[string]any, need to cast each value
	graphicRaw, _ := raw["graphic"].(map[string]any)
	if graphicRaw == nil {
		t.Fatal("no graphic")
	}
	graphic := make(map[string]map[string]any)
	for k, v := range graphicRaw {
		if m, ok := v.(map[string]any); ok {
			graphic[k] = m
		}
	}

	a := &UniversalAnalyzer{}
	analysis := &GraphicAnalysis{
		Summary: GraphicSummary{
			InferredTypes: make(map[string]int),
			PriceRange:    [2]float64{1e308, -1e308},
			TimeRange:     [2]float64{1e308, -1e308},
		},
	}

	if boxes, ok := graphic["dwgboxes"]; ok {
		for id, item := range boxes {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			box := a.parseBox(id, m)
			analysis.Boxes = append(analysis.Boxes, box)
		}
	}
	if lines, ok := graphic["dwglines"]; ok {
		for id, item := range lines {
			m, _ := item.(map[string]any)
			if m == nil {
				continue
			}
			line := a.parseLine(id, m)
			analysis.Lines = append(analysis.Lines, line)
		}
	}

	t.Logf("Parsed %d boxes, %d lines", len(analysis.Boxes), len(analysis.Lines))

	groups := buildBoxTopology(analysis)
	t.Logf("Got %d groups from buildBoxTopology", len(groups))
	for i, g := range groups {
		t.Logf("  Group %d: type=%s, boxes=%d, conf=%.2f, props=%v", i, g.Type, len(g.Boxes), g.Confidence, g.Properties)
	}

	a.postProcessGraphicsGeneric(analysis, graphic)

	t.Logf("After post-processing: %d VolumePeaks, %d Zones", len(analysis.Summary.VolumePeaks), len(analysis.Summary.Zones))
	for i, vp := range analysis.Summary.VolumePeaks {
		t.Logf("  VP %d: poc=%.2f, vah=%.2f, val=%.2f, stack=%d", i, vp.PocPrice, vp.Vah, vp.Val, vp.StackCount)
	}

	typeCounts := make(map[string]int)
	for _, b := range analysis.Boxes {
		typeCounts[b.InferredType]++
	}
	t.Logf("Box type counts: %v", typeCounts)
}
