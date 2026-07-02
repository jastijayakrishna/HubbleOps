package gate

import (
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
)

func TestDecideFingerprintsRawContext(t *testing.T) {
	req := action.Request{
		Project:        "proj",
		SessionID:      "sess",
		Actor:          "agent:claude-code",
		Action:         "migration.apply",
		Target:         "customers",
		Intent:         "delete customer@example.com data",
		IdempotencyKey: "migration:customer@example.com",
		Evidence:       []string{"ticket says customer@example.com"},
	}
	decision := Decide(req, []preflight.Finding{{
		Source:    preflight.SourceMigration,
		Kind:      preflight.KindMigrationDrop,
		Action:    "migration.drop_table",
		Target:    "customers",
		RiskScore: 95,
		Evidence:  []string{"migration_contains=DROP_TABLE"},
	}}, nil)

	encoded := strings.Join(append(decision.EvidenceHashes, decision.IntentHash, decision.IdempotencyKeyHash, decision.TargetFingerprint), " ")
	if strings.Contains(encoded, "customer@example.com") || strings.Contains(encoded, "customers") {
		t.Fatalf("decision hashes leaked raw context: %+v", decision)
	}
	if decision.DecisionID == "" || decision.ReceiptID != decision.DecisionID {
		t.Fatalf("receipt identity not set: %+v", decision)
	}
}

func TestDecisionIDDifferentiatesTransposedFields(t *testing.T) {
	first := action.Request{
		Project:     "proj",
		SessionID:   "sess",
		Actor:       "X",
		Action:      "deploy.release",
		Target:      "Y",
		Environment: "prod",
	}
	second := first
	second.Actor = "Y"
	second.Target = "X"

	firstID := decisionID(first, nil)
	secondID := decisionID(second, nil)
	if firstID == secondID {
		t.Fatalf("decision IDs collided after actor/target transposition: %s", firstID)
	}
}

func TestDecisionIDIgnoresFindingOrder(t *testing.T) {
	req := action.Request{
		Project:     "proj",
		SessionID:   "sess",
		Actor:       "agent:codex",
		Action:      "terraform.apply",
		Target:      "plan.json",
		Environment: "prod",
	}
	findings := []preflight.Finding{
		{Source: "terraform", Kind: "destroy", Action: "terraform.destroy", Target: "aws_db_instance.prod", RiskScore: 99},
		{Source: "terraform", Kind: "replace", Action: "terraform.replace", Target: "aws_s3_bucket.logs", RiskScore: 80},
	}
	shuffled := []preflight.Finding{findings[1], findings[0]}

	if got, want := decisionID(req, shuffled), decisionID(req, findings); got != want {
		t.Fatalf("decision ID changed when findings were shuffled: got %s want %s", got, want)
	}
}

func TestDecisionIDDeterministic(t *testing.T) {
	req := action.Request{
		Project:        "proj",
		SessionID:      "sess",
		Actor:          "agent:codex",
		HumanDelegator: "krish",
		Action:         "migration.apply",
		Target:         "migrations/004_drop.sql",
		Environment:    "prod",
		IdempotencyKey: "migration:004",
	}
	findings := []preflight.Finding{{
		Source:    "migration",
		Kind:      "drop_table",
		Action:    "migration.drop_table",
		Target:    "private_customers",
		RiskScore: 95,
	}}

	if got, want := decisionID(req, findings), decisionID(req, findings); got != want {
		t.Fatalf("decision ID is not deterministic: got %s want %s", got, want)
	}
}
