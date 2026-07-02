package terraform

import (
	"strings"
	"testing"
)

// FuzzScan asserts the plan parser never panics on arbitrary input and never emits an
// out-of-range risk score. Malformed JSON returning an error is acceptable.
func FuzzScan(f *testing.F) {
	seeds := []string{
		"",
		"{",
		"not json",
		`{"resource_changes":null}`,
		`{"resource_changes":[]}`,
		`{"resource_changes":[{"address":"null_resource.x","type":"null_resource","change":{"actions":["delete"]}}]}`,
		`{"resource_changes":[{"type":"aws_db_instance","change":{"actions":["update"],"before":{"deletion_protection":true,"allocated_storage":1000},"after":{"deletion_protection":false,"allocated_storage":10}}}]}`,
		`{"resource_changes":[{"change":{"actions":["delete","create"]}}]}`,
		`{"resource_changes":[{"change":{"actions":[123]}}]}`,
	}
	for _, s := range seeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, plan string) {
		findings, err := Scan(strings.NewReader(plan), Options{ProtectedResources: []string{"aws_s3_bucket.x"}})
		if err != nil {
			return
		}
		for _, finding := range findings {
			if finding.RiskScore < 0 || finding.RiskScore > 100 {
				t.Fatalf("risk score out of range: %d", finding.RiskScore)
			}
			if finding.Source != "terraform" || finding.Kind == "" {
				t.Fatalf("malformed finding: %+v", finding)
			}
		}
	})
}
