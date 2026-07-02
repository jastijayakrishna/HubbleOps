package deploy

import (
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
)

func TestScanTierZeroProductionDeployRequiresReviewRisk(t *testing.T) {
	findings := Scan(Options{Service: "billing-api", Environment: "prod", ServiceRisk: "tier_0"})
	if len(findings) != 1 {
		t.Fatalf("findings=%d want 1", len(findings))
	}
	got := findings[0]
	if got.Source != preflight.SourceDeploy || got.Kind != preflight.KindDeployRelease || got.Action != ActionRelease {
		t.Fatalf("unexpected finding identity: %+v", got)
	}
	if got.RiskScore < 70 || got.RiskClass != action.RiskHigh {
		t.Fatalf("risk score/class=%d/%s want high review risk", got.RiskScore, got.RiskClass)
	}
	evidence := strings.Join(got.Evidence, " ")
	if !strings.Contains(evidence, "service_risk=tier_0") || !strings.Contains(evidence, "service_fingerprint=sha256:") {
		t.Fatalf("evidence missing privacy-safe deploy markers: %v", got.Evidence)
	}
	if strings.Contains(evidence, "billing-api") {
		t.Fatalf("evidence stored raw service name: %v", got.Evidence)
	}
}

func TestScanDefaultsUnknownRiskToTierTwo(t *testing.T) {
	findings := Scan(Options{Service: "docs-api", Environment: "staging"})
	if len(findings) != 1 {
		t.Fatalf("findings=%d want 1", len(findings))
	}
	if findings[0].RiskScore >= 70 {
		t.Fatalf("default service tier should not require review by score alone: %+v", findings[0])
	}
	if !strings.Contains(strings.Join(findings[0].Evidence, " "), "service_risk=tier_2") {
		t.Fatalf("evidence=%v want tier_2 default", findings[0].Evidence)
	}
}
