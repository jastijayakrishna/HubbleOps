# Quickstart

Run a local engineering-action preflight and verify the receipt.

## Terraform

Use the included fixture:

```bash
go run ./cmd/hubbleops preflight terraform \
  internal/preflight/terraform/testdata/datatalks_destroy_plan.json \
  -policy configs/policy.yaml.example \
  -project demo \
  -session demo-pr-1 \
  -actor agent:local-cli \
  -human-delegator local \
  -env production \
  -intent "demo protected destroy" \
  -receipt-secret "local-demo-secret"
```

Expected shape:

```text
HubbleOps preflight decision
decision=block risk_score=99 risk_class=critical findings=1 receipt_id=dec_...
reason=production protected-resource destroy is blocked
```

The command exits `1` for a blocked change after writing the receipt. The full
contract: `0` allow, `1` block, `2` usage/flag error, `3` require_approval,
`4` internal error (for example a receipt write failure — no decision was
produced, so do not treat the action as cleared to run).

## Migration

```bash
go run ./cmd/hubbleops preflight migration ./migrations \
  -policy configs/policy.yaml.example \
  -project demo \
  -session demo-pr-2 \
  -actor agent:local-cli \
  -env production \
  -receipt-secret "local-demo-secret"
```

## Deploy

```bash
go run ./cmd/hubbleops preflight deploy \
  -service billing-api \
  -artifact "demo-sha-1" \
  -idempotency-key "deploy:demo-sha-1" \
  -policy configs/policy.yaml.example \
  -project demo \
  -session demo-deploy-1 \
  -actor agent:local-cli \
  -human-delegator local \
  -env production \
  -receipt-secret "local-demo-secret"
```

Expected shape:

```text
HubbleOps preflight decision
decision=require_approval risk_score=85 risk_class=high findings=1 receipt_id=dec_...
reason=tier 0 production deploy requires review
required_approvers=sre,billing-owner
```

Reusing the same `-idempotency-key` for the same service/env is blocked and
written as a second receipt. The default deploy ledger is `data/action-ledger.json`.

## Gate API

Start the server:

```bash
go run ./cmd/gate \
  -policy configs/policy.yaml.example \
  -wal-dir data/wal \
  -receipt-secret "local-demo-secret"
```

Send an action request:

```bash
curl -s http://localhost:8080/v1/preflight \
  -H "Content-Type: application/json" \
  -d '{
    "project":"demo",
    "session_id":"demo-api-1",
    "actor":"agent:local-cli",
    "human_delegator":"local",
    "action":"github.pull_request",
    "target":"demo/repo#1",
    "environment":"main",
    "intent":"OPS-1 safe docs change"
  }'
```

JSON responses preserve decision context but return caller-controlled targets,
file paths, evidence, approvers, and reviewer identifiers as safe labels or
fingerprints. Raw SQL text, plan contents, PR body text, secrets, emails, and
payment-looking values should not appear in responses or WAL receipts.

For GitHub App mode, point the App webhook at `/github/webhook`, set
`GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`, and `GITHUB_APP_PRIVATE_KEY_FILE`, then
require the `HubbleOps Action Firewall` check in branch protection.

## Approvals

When a response includes `approval_request`, review it through the gate API:

```bash
curl -s http://localhost:8080/v1/approvals/APPROVAL_ID/review \
  -H "Content-Type: application/json" \
  -d '{"reviewer":"sre","source":"api","decision":"approved"}'
```

Rerun the same preflight request. The gate consults the approval store and writes
a new signed receipt with approval evidence. Fetch the latest receipt metadata:

```bash
curl -s http://localhost:8080/v1/receipts/DECISION_ID
```

## Phase 4 Demo

```bash
go run ./cmd/hubbleops demo phase4 \
  -wal-dir data/phase4-demo/wal \
  -approval-store data/phase4-demo/approvals.json \
  -receipt-secret "local-demo-secret"
```

## Verify

```bash
go run ./cmd/hubbleops verify-receipts \
  -receipt-secret "local-demo-secret" \
  -require-signatures \
  data/wal/*.jsonl
```

## Evidence Pack

```bash
go run ./cmd/hubbleops evidence-pack \
  -receipt-secret "local-demo-secret" \
  data/wal/*.jsonl
```

## Useful Commands

```bash
make test
make preflight-terraform
make preflight-deploy
make phase4-demo
make gate
```
