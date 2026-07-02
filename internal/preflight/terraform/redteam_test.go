package terraform

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/preflight"
)

const (
	terraformRedteamMinCases = 100
	terraformFPBudget        = 5
)

func TestRedteamCorpusMeasurement(t *testing.T) {
	destructive := loadTerraformFileCases(t, map[string]int{
		"destructive/001_stateful_replace.json":      90,
		"destructive/002_after_unknown_storage.json": 70,
		"destructive/003_stateful_destroy.json":      90,
		"destructive/004_public_ingress.json":        90,
	})
	destructive = append(destructive, loadTerraformMatrixCases(t, "destructive")...)
	safe := loadTerraformFileCases(t, map[string]int{
		"safe/001_benign_update.json":   0,
		"safe/002_harmless_delete.json": 0,
		"safe/003_private_ingress.json": 0,
	})
	safe = append(safe, loadTerraformMatrixCases(t, "safe")...)
	if total := len(destructive) + len(safe); total < terraformRedteamMinCases {
		t.Fatalf("terraform corpus too small: cases=%d want >=%d", total, terraformRedteamMinCases)
	}

	fn := 0
	for _, tc := range destructive {
		findings := scanRedteamTerraformCase(t, tc)
		if preflight.HighestRisk(findings) < tc.MinRisk {
			fn++
			t.Errorf("%s false negative: highest_risk=%d want >=%d findings=%+v", tc.Name, preflight.HighestRisk(findings), tc.MinRisk, findings)
		}
	}

	fp := 0
	for _, tc := range safe {
		findings := scanRedteamTerraformCase(t, tc)
		if len(findings) > 0 {
			fp++
			t.Errorf("%s false positive: %+v", tc.Name, findings)
		}
	}
	t.Logf("terraform redteam cases=%d destructive=%d FN=%d FN_rate=%.2f%% safe=%d FP=%d FP_rate=%.2f%% FP_budget=%d",
		len(destructive)+len(safe), len(destructive), fn, rate(fn, len(destructive)), len(safe), fp, rate(fp, len(safe)), terraformFPBudget)
	if fn > 0 {
		t.Fatalf("terraform redteam false negatives=%d", fn)
	}
	if fp > terraformFPBudget {
		t.Fatalf("terraform redteam false positives=%d exceed budget=%d", fp, terraformFPBudget)
	}
}

type terraformRedteamCase struct {
	Name    string
	MinRisk int
	Plan    string
}

type terraformMatrix struct {
	Destructive []terraformMatrixGroup `json:"destructive"`
	Safe        []terraformMatrixGroup `json:"safe"`
}

type terraformMatrixGroup struct {
	Name         string         `json:"name"`
	MinRisk      int            `json:"min_risk"`
	Actions      []string       `json:"actions"`
	Types        []string       `json:"types"`
	Before       map[string]any `json:"before"`
	After        map[string]any `json:"after"`
	AfterUnknown map[string]any `json:"after_unknown"`
}

func scanRedteamTerraformCase(t *testing.T, tc terraformRedteamCase) []preflight.Finding {
	t.Helper()
	findings, err := Scan(strings.NewReader(tc.Plan), Options{})
	if err != nil {
		t.Fatalf("scan %s: %v", tc.Name, err)
	}
	return findings
}

func loadTerraformFileCases(t *testing.T, rels map[string]int) []terraformRedteamCase {
	t.Helper()
	var names []string
	for rel := range rels {
		names = append(names, rel)
	}
	sort.Strings(names)
	var out []terraformRedteamCase
	for _, rel := range names {
		out = append(out, terraformRedteamCase{Name: rel, MinRisk: rels[rel], Plan: readTerraformFixture(t, rel)})
	}
	return out
}

func readTerraformFixture(t *testing.T, rel string) string {
	t.Helper()
	path := filepath.Join("testdata", "redteam", rel)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", rel, err)
	}
	return string(data)
}

func loadTerraformMatrixCases(t *testing.T, label string) []terraformRedteamCase {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", "redteam", "matrix.json"))
	if err != nil {
		t.Fatalf("read terraform matrix: %v", err)
	}
	var matrix terraformMatrix
	if err := json.Unmarshal(data, &matrix); err != nil {
		t.Fatalf("parse terraform matrix: %v", err)
	}
	groups := matrix.Destructive
	if label == "safe" {
		groups = matrix.Safe
	}
	var out []terraformRedteamCase
	for _, group := range groups {
		if len(group.Actions) == 0 {
			t.Fatalf("terraform matrix group %s has no actions", group.Name)
		}
		for i, typ := range group.Types {
			rc := resourceChange{
				Address: fmt.Sprintf("%s.%s_%02d", typ, group.Name, i+1),
				Type:    typ,
				Name:    fmt.Sprintf("%s_%02d", group.Name, i+1),
				Change: change{
					Actions:      append([]string{}, group.Actions...),
					Before:       cloneMap(group.Before),
					After:        cloneMap(group.After),
					AfterUnknown: cloneMap(group.AfterUnknown),
				},
			}
			body, err := json.Marshal(plan{ResourceChanges: []resourceChange{rc}})
			if err != nil {
				t.Fatalf("marshal %s/%s/%d: %v", label, group.Name, i, err)
			}
			out = append(out, terraformRedteamCase{
				Name:    fmt.Sprintf("%s/%s/%02d/%s", label, group.Name, i+1, typ),
				MinRisk: group.MinRisk,
				Plan:    string(body),
			})
		}
	}
	return out
}

func cloneMap(in map[string]any) map[string]any {
	if len(in) == 0 {
		return nil
	}
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func rate(n, total int) float64 {
	if total == 0 {
		return 0
	}
	return float64(n) * 100 / float64(total)
}
