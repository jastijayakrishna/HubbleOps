package loop

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFileActionStorePersistsCommittedDuplicate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "action-ledger.json")
	ctx := context.Background()
	obs := ActionObservation{
		Project:        "proj",
		SessionID:      "sess-1",
		ToolName:       "deploy.release",
		ActionRisk:     ActionRiskDangerous,
		IdempotencyKey: "deploy:sha-abc",
		ResourceID:     "production/billing-api",
		UnixMillis:     1_000,
	}

	firstStore := NewFileActionStore(path)
	first, err := firstStore.Decide(ctx, obs)
	if err != nil {
		t.Fatalf("first decide: %v", err)
	}
	if first.Outcome != ActionOutcomeClaimed {
		t.Fatalf("first outcome=%q want claimed", first.Outcome)
	}
	if err := firstStore.Commit(ctx, ActionResult{
		Project:        obs.Project,
		IdempotencyKey: obs.IdempotencyKey,
		ClaimNonce:     first.ClaimNonce,
		ToolName:       obs.ToolName,
		ActionRisk:     obs.ActionRisk,
		ResourceID:     obs.ResourceID,
		DecisionID:     "dec_deploy",
		ResultClass:    "requires_review",
		UnixMillis:     2_000,
	}); err != nil {
		t.Fatalf("commit: %v", err)
	}

	secondStore := NewFileActionStore(path)
	obs.SessionID = "sess-2"
	obs.UnixMillis = 3_000
	second, err := secondStore.Decide(ctx, obs)
	if err != nil {
		t.Fatalf("second decide: %v", err)
	}
	if second.Outcome != ActionOutcomeCommittedReplay || second.Decision.ActionCeiling != ActionBlock {
		t.Fatalf("duplicate decision=%+v outcome=%q", second.Decision, second.Outcome)
	}
	if second.Replay == nil || second.Replay.DecisionID != "dec_deploy" {
		t.Fatalf("replay=%+v want original decision id", second.Replay)
	}
}

func TestFileActionStoreDoesNotPersistRawDeployIdentifiers(t *testing.T) {
	path := filepath.Join(t.TempDir(), "action-ledger.json")
	rawEmail := "customer@example.com"
	rawKey := "deploy:prod:billing-api:sha-secret"
	rawResource := "production/billing-api"
	store := NewFileActionStore(path)
	first, err := store.Decide(context.Background(), ActionObservation{
		Project:        "project:" + rawEmail,
		SessionID:      "session:" + rawEmail,
		ToolName:       "deploy.release",
		ActionRisk:     ActionRiskDangerous,
		AgentID:        "agent:" + rawEmail,
		UserID:         rawEmail,
		IdempotencyKey: rawKey,
		ResourceID:     rawResource,
	})
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read ledger: %v", err)
	}
	raw := string(data)
	if strings.Contains(raw, rawKey) || strings.Contains(raw, rawResource) || strings.Contains(raw, "billing-api") {
		t.Fatalf("file ledger stored raw deploy identifiers: %s", raw)
	}
	if strings.Contains(raw, rawEmail) {
		t.Fatalf("file ledger stored raw identity fields: %s", raw)
	}
	if !strings.Contains(raw, "idempotency_key_hash") || !strings.Contains(raw, "resource_fingerprint") {
		t.Fatalf("file ledger missing privacy-safe fingerprints: %s", raw)
	}

	if err := store.Commit(context.Background(), ActionResult{
		Project:        "project:" + rawEmail,
		IdempotencyKey: rawKey,
		ClaimNonce:     first.ClaimNonce,
		ToolName:       "deploy.release",
		ActionRisk:     ActionRiskDangerous,
		ResourceID:     rawResource,
		DecisionID:     "dec_deploy",
		ResultClass:    "success",
		Result:         []byte(`{"email":"customer@example.com","ok":true}`),
	}); err != nil {
		t.Fatalf("commit: %v", err)
	}
	data, err = os.ReadFile(path)
	if err != nil {
		t.Fatalf("read committed ledger: %v", err)
	}
	raw = string(data)
	if strings.Contains(raw, rawEmail) || strings.Contains(raw, rawResource) || strings.Contains(raw, "billing-api") {
		t.Fatalf("committed file ledger stored raw values: %s", raw)
	}
	if strings.Contains(raw, `"result":`) || !strings.Contains(raw, "result_payload_fingerprint") || !strings.Contains(raw, "result_shape") {
		t.Fatalf("committed file ledger stored raw result or missed result metadata: %s", raw)
	}
}
