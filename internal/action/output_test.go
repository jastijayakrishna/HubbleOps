package action

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSanitizeForOutputRemovesRawDecisionCanaries(t *testing.T) {
	decision := Decision{
		Decision:          DecisionRequireApproval,
		Reason:            "review customer@example.com before continuing",
		RequiredApprovers: []string{"owner@example.com", "sre"},
		Approvals:         []string{"reviewer@example.com"},
		Evidence: []string{
			"github_linked_ticket=missing",
			"raw_note=customer@example.com sk_live_hubbleops_secret",
			"payment=4242 4242 4242 4242",
		},
		ReceiptError: "password=correct-horse-battery-staple",
	}
	got := SanitizeForOutput(decision)
	data, err := json.Marshal(got)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	raw := string(data)
	for _, forbidden := range []string{
		"customer@example.com",
		"sk_live_hubbleops_secret",
		"4242 4242 4242 4242",
		"password=correct-horse-battery-staple",
		"owner@example.com",
		"reviewer@example.com",
	} {
		if strings.Contains(raw, forbidden) {
			t.Fatalf("sanitized decision leaked %q: %s", forbidden, raw)
		}
	}
	if !strings.Contains(raw, "github_linked_ticket=missing") ||
		!strings.Contains(raw, "evidence_fingerprint=sha256:") ||
		!strings.Contains(raw, "fingerprint:sha256:") {
		t.Fatalf("sanitized decision lost expected safe context: %s", raw)
	}
}
