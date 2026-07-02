package migration

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/hubbleops/hubbleops/internal/preflight"
)

const (
	migrationRedteamMinCases = 100
	migrationFPBudget        = 5
)

func TestRedteamCorpusMeasurement(t *testing.T) {
	destructive := loadMigrationFileCases(t, map[string]int{
		"destructive/001_delete_tautology.sql":     90,
		"destructive/002_update_tautology.sql":     70,
		"destructive/003_mysql_exec_comment.sql":   90,
		"destructive/004_drop_table.sql":           90,
		"destructive/005_add_not_null.sql":         70,
		"destructive/006_rails_unanalyzable.rb":    70,
		"destructive/007_squawk_locking_index.sql": 70,
	})
	destructive = append(destructive, loadMigrationMatrixCases(t, "destructive")...)
	safe := loadMigrationFileCases(t, map[string]int{
		"safe/001_add_nullable_column.sql":     0,
		"safe/002_concurrent_index.sql":        0,
		"safe/003_bounded_delete.sql":          0,
		"safe/004_create_table_then_index.sql": 0,
	})
	safe = append(safe, loadMigrationMatrixCases(t, "safe")...)
	if total := len(destructive) + len(safe); total < migrationRedteamMinCases {
		t.Fatalf("migration corpus too small: cases=%d want >=%d", total, migrationRedteamMinCases)
	}

	fn := 0
	for _, tc := range destructive {
		findings := scanRedteamMigrationCase(tc)
		if preflight.HighestRisk(findings) < tc.MinRisk {
			fn++
			t.Errorf("%s false negative: highest_risk=%d want >=%d findings=%+v", tc.Name, preflight.HighestRisk(findings), tc.MinRisk, findings)
		}
	}

	fp := 0
	for _, tc := range safe {
		findings := scanRedteamMigrationCase(tc)
		if len(findings) > 0 {
			fp++
			t.Errorf("%s false positive: %+v", tc.Name, findings)
		}
	}
	t.Logf("migration redteam cases=%d destructive=%d FN=%d FN_rate=%.2f%% safe=%d FP=%d FP_rate=%.2f%% FP_budget=%d",
		len(destructive)+len(safe), len(destructive), fn, rate(fn, len(destructive)), len(safe), fp, rate(fp, len(safe)), migrationFPBudget)
	if fn > 0 {
		t.Fatalf("migration redteam false negatives=%d", fn)
	}
	if fp > migrationFPBudget {
		t.Fatalf("migration redteam false positives=%d exceed budget=%d", fp, migrationFPBudget)
	}
}

type migrationRedteamCase struct {
	Name    string
	Path    string
	Content string
	MinRisk int
}

type migrationMatrix struct {
	Destructive []migrationMatrixGroup `json:"destructive"`
	Safe        []migrationMatrixGroup `json:"safe"`
}

type migrationMatrixGroup struct {
	Name    string   `json:"name"`
	MinRisk int      `json:"min_risk"`
	PathExt string   `json:"path_ext"`
	Cases   []string `json:"cases"`
}

func scanRedteamMigrationCase(tc migrationRedteamCase) []preflight.Finding {
	return ScanContent(tc.Path, tc.Content)
}

func loadMigrationFileCases(t *testing.T, rels map[string]int) []migrationRedteamCase {
	t.Helper()
	var names []string
	for rel := range rels {
		names = append(names, rel)
	}
	sort.Strings(names)
	var out []migrationRedteamCase
	for _, rel := range names {
		out = append(out, migrationRedteamCase{
			Name:    rel,
			Path:    filepath.Join("testdata", "redteam", rel),
			Content: readMigrationFixture(t, rel),
			MinRisk: rels[rel],
		})
	}
	return out
}

func readMigrationFixture(t *testing.T, rel string) string {
	t.Helper()
	path := filepath.Join("testdata", "redteam", rel)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", rel, err)
	}
	return string(data)
}

func loadMigrationMatrixCases(t *testing.T, label string) []migrationRedteamCase {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", "redteam", "matrix.json"))
	if err != nil {
		t.Fatalf("read migration matrix: %v", err)
	}
	var matrix migrationMatrix
	if err := json.Unmarshal(data, &matrix); err != nil {
		t.Fatalf("parse migration matrix: %v", err)
	}
	groups := matrix.Destructive
	if label == "safe" {
		groups = matrix.Safe
	}
	var out []migrationRedteamCase
	for _, group := range groups {
		ext := group.PathExt
		if ext == "" {
			ext = ".sql"
		}
		for i, content := range group.Cases {
			out = append(out, migrationRedteamCase{
				Name:    fmt.Sprintf("%s/%s/%03d", label, group.Name, i+1),
				Path:    filepath.Join("testdata", "redteam", label, fmt.Sprintf("%s_%03d%s", group.Name, i+1, ext)),
				Content: content,
				MinRisk: group.MinRisk,
			})
		}
	}
	return out
}

func rate(n, total int) float64 {
	if total == 0 {
		return 0
	}
	return float64(n) * 100 / float64(total)
}
