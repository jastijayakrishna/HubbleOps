package policy

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
)

func TestLoadRejectsUnknownPolicyKeys(t *testing.T) {
	path := writePolicyFile(t, `
version: engineering-gate/v1
rules:
  - id: typo
    if:
      action: deploy.release
      enviroment: prod
      touch_any:
        - prod/**
    decision: block
    risk_score: 90
`)
	_, err := Load(path)
	if err == nil {
		t.Fatalf("Load succeeded, want unknown-field error")
	}
	msg := err.Error()
	for _, want := range []string{path, "enviroment", "touch_any"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("error %q missing %q", msg, want)
		}
	}
}

func TestLoadCollectsSemanticValidationProblems(t *testing.T) {
	path := writePolicyFile(t, `
version: engineering-gate/v1
rules:
  - id: ""
    if:
      action: deploy.release
    decision: quarantine
    risk_score: 101
  - id: duplicate
    if:
      action: terraform.destroy
    decision: block
    risk_score: -1
  - id: duplicate
    if: {}
    decision: block
    risk_score: 90
`)
	_, err := Load(path)
	if err == nil {
		t.Fatalf("Load succeeded, want validation error")
	}
	msg := err.Error()
	for _, want := range []string{
		path,
		"rules[0].id is required",
		`rules[0].decision "quarantine" is invalid`,
		"rules[0].risk_score must be between 0 and 100",
		"rules[1].risk_score must be between 0 and 100",
		`rules[2].id "duplicate" is duplicate`,
		"rules[2].if must contain at least one condition",
	} {
		if !strings.Contains(msg, want) {
			t.Fatalf("validation error %q missing %q", msg, want)
		}
	}
}

func TestLoadAllowsDefaultOrAllowRuleWithoutConditions(t *testing.T) {
	for name, body := range map[string]string{
		"default": `
version: engineering-gate/v1
rules:
  - id: default
    decision: block
    risk_score: 90
`,
		"allow": `
version: engineering-gate/v1
rules:
  - id: allow-all-low-risk
    decision: allow
    risk_score: 10
`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := Load(writePolicyFile(t, body)); err != nil {
				t.Fatalf("Load: %v", err)
			}
		})
	}
}

func TestLoadWarnsOnSuspiciousRequireAndAllowShadow(t *testing.T) {
	path := writePolicyFile(t, `
version: engineering-gate/v1
rules:
  - id: allow-prod-deploy
    if:
      action: deploy.release
      env: prod
    decision: allow
    require:
      - ticket
    risk_score: 10
  - id: block-prod-deploy
    if:
      action: deploy.release
      env: prod
    decision: block
    risk_score: 99
`)
	p, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	warnings := strings.Join(p.Warnings, "\n")
	for _, want := range []string{
		`rules[0].require[0] "ticket" is not a known precondition`,
		`rules[0] "allow-prod-deploy" allow shadows later block rule rules[1] "block-prod-deploy"`,
	} {
		if !strings.Contains(warnings, want) {
			t.Fatalf("warnings %q missing %q", warnings, want)
		}
	}
}

func TestExamplePolicyLoadsCleanly(t *testing.T) {
	path := filepath.Join("..", "..", "configs", "policy.yaml.example")
	p, err := Load(path)
	if err != nil {
		t.Fatalf("Load example policy: %v", err)
	}
	if len(p.Warnings) != 0 {
		t.Fatalf("example warnings=%v, want none", p.Warnings)
	}
}

func TestEvaluateBlocksTerraformDestroyInProd(t *testing.T) {
	p := &Policy{
		Version: "policy-v1",
		Rules: []Rule{{
			ID:        "block-prod-destroy",
			If:        Conditions{Action: "terraform.destroy", Env: "prod", TouchesAny: []string{"aws_s3_bucket.audit_logs_prod"}},
			Decision:  action.DecisionBlock,
			Reason:    "production destroy is blocked",
			RiskScore: 99,
		}},
	}
	decision := Evaluate(action.Request{Action: "terraform.plan", Environment: "production"}, []preflight.Finding{{
		Action:    "terraform.destroy",
		Target:    "aws_s3_bucket.audit_logs_prod",
		RiskScore: 95,
		Evidence:  []string{"terraform_action=delete"},
	}}, p)
	if decision.Decision != action.DecisionBlock || decision.PolicyRuleID != "block-prod-destroy" {
		t.Fatalf("decision=%+v", decision)
	}
	if !decision.RequiresReceipt {
		t.Fatalf("block decisions must require a receipt")
	}
}

func TestEvaluateRequiresApprovalForMigrationContains(t *testing.T) {
	p := &Policy{
		Rules: []Rule{{
			ID:                "review-drop-table",
			If:                Conditions{MigrationContains: []string{"DROP_TABLE"}},
			Decision:          action.DecisionRequireApproval,
			RequiredApprovers: []string{"db-owner"},
			RiskScore:         85,
		}},
	}
	decision := Evaluate(action.Request{Action: "migration.apply"}, []preflight.Finding{{
		Action:     "migration.drop_table",
		ChangeTags: []string{"migration:DROP_TABLE"},
		RiskScore:  95,
	}}, p)
	if decision.Decision != action.DecisionRequireApproval {
		t.Fatalf("decision=%+v", decision)
	}
	if len(decision.RequiredApprovers) != 1 || decision.RequiredApprovers[0] != "db-owner" {
		t.Fatalf("approvers=%v", decision.RequiredApprovers)
	}
}

func TestEvaluateDefaultsToBlockForCriticalFinding(t *testing.T) {
	decision := Evaluate(action.Request{Action: "terraform.plan"}, []preflight.Finding{{RiskScore: 95}}, nil)
	if decision.Decision != action.DecisionBlock {
		t.Fatalf("decision=%+v", decision)
	}
}

func TestServiceRiskAndOwnersFromPolicy(t *testing.T) {
	p := &Policy{
		Services: map[string]ServiceConfig{
			"billing-api": {
				Risk:   "tier_0",
				Owners: []string{"billing-owner", "sre"},
			},
		},
		Rules: []Rule{{
			ID:        "review-tier0-prod-deploy",
			If:        Conditions{Action: "deploy.release", Env: "prod", ServiceRisk: "tier_0"},
			Decision:  action.DecisionRequireApproval,
			RiskScore: 85,
		}},
	}
	if got := p.ServiceRisk("BILLING-API"); got != "tier_0" {
		t.Fatalf("service risk=%q want tier_0", got)
	}
	decision := Evaluate(action.Request{
		Action:      "deploy.release",
		Target:      "billing-api",
		Environment: "production",
		ServiceRisk: "tier_0",
	}, []preflight.Finding{{Action: "deploy.release", Target: "billing-api", RiskScore: 85}}, p)
	if decision.Decision != action.DecisionRequireApproval || decision.PolicyRuleID != "review-tier0-prod-deploy" {
		t.Fatalf("decision=%+v", decision)
	}
	if len(decision.RequiredApprovers) != 2 || decision.RequiredApprovers[0] != "billing-owner" || decision.RequiredApprovers[1] != "sre" {
		t.Fatalf("approvers=%v want service owners", decision.RequiredApprovers)
	}
}

func hasEvidence(d action.Decision, want string) bool {
	for _, e := range d.Evidence {
		if e == want {
			return true
		}
	}
	return false
}

func hasApprover(d action.Decision, want string) bool {
	for _, a := range d.RequiredApprovers {
		if a == want {
			return true
		}
	}
	return false
}

func TestEvaluateGlobTouchesAnyAvoidsSubstringMatch(t *testing.T) {
	p := &Policy{Rules: []Rule{{
		ID: "billing", If: Conditions{TouchesAny: []string{"billing/**"}},
		Decision: action.DecisionBlock, RiskScore: 90, Reason: "billing change",
	}}}
	// "rebilling/..." must NOT match "billing/**".
	if d := Evaluate(action.Request{Target: "rebilling/api.go"}, nil, p); d.PolicyRuleID == "billing" {
		t.Fatalf("substring false-match: %+v", d)
	}
	// "billing/..." must match.
	if d := Evaluate(action.Request{Target: "billing/api.go"}, nil, p); d.PolicyRuleID != "billing" || d.Decision != action.DecisionBlock {
		t.Fatalf("glob match failed: %+v", d)
	}
}

func TestPathGlobMatchDoubleStarSegmentBoundaries(t *testing.T) {
	tests := []struct {
		name    string
		pattern string
		target  string
		want    bool
	}{
		{
			name:    "prefix double star does not match sibling directory",
			pattern: "billing/**",
			target:  "billing-legacy/x",
			want:    false,
		},
		{
			name:    "prefix double star matches prefix itself",
			pattern: "billing/**",
			target:  "billing",
			want:    true,
		},
		{
			name:    "prefix double star matches direct child",
			pattern: "billing/**",
			target:  "billing/x",
			want:    true,
		},
		{
			name:    "prefix double star matches nested child",
			pattern: "billing/**",
			target:  "billing/x/y",
			want:    true,
		},
		{
			name:    "suffix double star matches path suffix",
			pattern: "**/prod.tfvars",
			target:  "env/prod.tfvars",
			want:    true,
		},
		{
			name:    "suffix double star does not match partial segment",
			pattern: "**/prod.tfvars",
			target:  "notprod.tfvars",
			want:    false,
		},
		{
			name:    "middle double star matches nested path",
			pattern: "infra/**/secrets",
			target:  "infra/a/b/secrets",
			want:    true,
		},
		{
			name:    "middle double star does not match partial prefix segment",
			pattern: "infra/**/secrets",
			target:  "infra-x/secrets",
			want:    false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := pathGlobMatch(tt.pattern, tt.target); got != tt.want {
				t.Fatalf("pathGlobMatch(%q, %q)=%t want %t", tt.pattern, tt.target, got, tt.want)
			}
		})
	}
}

func TestEvaluateUnsatisfiedPreconditionEscalatesToApproval(t *testing.T) {
	p := &Policy{Rules: []Rule{{
		ID: "needs-ticket", If: Conditions{Action: "github.pull_request"},
		Decision: action.DecisionAllow, Require: []string{"linked_ticket"}, Reason: "ok",
	}}}
	// No linked ticket -> allow is escalated to require_approval with a clear missing-X reason.
	d := Evaluate(action.Request{Action: "github.pull_request"}, nil, p)
	if d.Decision != action.DecisionRequireApproval || !hasEvidence(d, "missing_precondition=linked_ticket") {
		t.Fatalf("missing precondition not enforced: %+v", d)
	}
	// Ticket present -> the rule's allow stands.
	d2 := Evaluate(action.Request{Action: "github.pull_request", Evidence: []string{"github_linked_ticket=present"}}, nil, p)
	if d2.Decision != action.DecisionAllow {
		t.Fatalf("satisfied precondition should allow: %+v", d2)
	}
}

func TestEvaluateRequireSeparatesApproverFromPrecondition(t *testing.T) {
	p := &Policy{Rules: []Rule{{
		ID: "drop", If: Conditions{MigrationContains: []string{"DROP_TABLE"}},
		Decision: action.DecisionRequireApproval, Require: []string{"db_owner", "rollback_plan"}, RiskScore: 90,
	}}}
	d := Evaluate(action.Request{Action: "migration.apply"}, []preflight.Finding{{
		ChangeTags: []string{"migration:DROP_TABLE"}, RiskScore: 95,
	}}, p)
	if !hasApprover(d, "db_owner") {
		t.Fatalf("db_owner should be a required approver: %+v", d.RequiredApprovers)
	}
	if hasApprover(d, "rollback_plan") {
		t.Fatalf("rollback_plan must NOT be flattened into approvers: %+v", d.RequiredApprovers)
	}
	if !hasEvidence(d, "missing_precondition=rollback_plan") {
		t.Fatalf("rollback_plan precondition not enforced: %+v", d.Evidence)
	}
}

func TestServiceOwnersReplaceDefaultOwnerForRiskyDeploy(t *testing.T) {
	p := &Policy{
		Services: map[string]ServiceConfig{
			"billing-api": {
				Risk:   "tier_0",
				Owners: []string{"billing-owner"},
			},
		},
	}
	decision := Evaluate(action.Request{
		Action:      "deploy.release",
		Target:      "billing-api",
		Environment: "production",
		ServiceRisk: "tier_0",
	}, []preflight.Finding{{Action: "deploy.release", Target: "billing-api", RiskScore: 85}}, p)
	if decision.Decision != action.DecisionRequireApproval {
		t.Fatalf("decision=%+v", decision)
	}
	if len(decision.RequiredApprovers) != 1 || decision.RequiredApprovers[0] != "billing-owner" {
		t.Fatalf("approvers=%v want service owner", decision.RequiredApprovers)
	}
}

func writePolicyFile(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "policy.yaml")
	if err := os.WriteFile(path, []byte(strings.TrimSpace(body)+"\n"), 0600); err != nil {
		t.Fatalf("write policy: %v", err)
	}
	return path
}
