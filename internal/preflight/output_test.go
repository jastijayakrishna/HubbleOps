package preflight

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSanitizeFindingsForOutputRemovesRawCanaries(t *testing.T) {
	findings := []Finding{{
		Source: "github",
		Kind:   KindGitHubChangedFile,
		Action: "github.pull_request",
		Target: "infra/customer@example.com",
		File:   "migrations/raw_customer_name_AcmePrivate.sql",
		Evidence: []string{
			"github_linked_ticket=missing",
			"raw_note=sk_live_hubbleops_secret",
			"card=4242 4242 4242 4242",
		},
		ChangeTags: []string{"github:changed_file", "owner@example.com"},
		RiskScore:  80,
		RiskClass:  "high",
	}}
	got := SanitizeFindingsForOutput(findings)
	data, err := json.Marshal(got)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	raw := string(data)
	for _, forbidden := range []string{
		"customer@example.com",
		"raw_customer_name_AcmePrivate",
		"sk_live_hubbleops_secret",
		"4242 4242 4242 4242",
		"owner@example.com",
	} {
		if strings.Contains(raw, forbidden) {
			t.Fatalf("sanitized findings leaked %q: %s", forbidden, raw)
		}
	}
	if !strings.Contains(raw, "fingerprint:sha256:") ||
		!strings.Contains(raw, "github_linked_ticket=missing") ||
		!strings.Contains(raw, "evidence_fingerprint=sha256:") {
		t.Fatalf("sanitized findings lost expected safe context: %s", raw)
	}
}
