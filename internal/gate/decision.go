package gate

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/policy"
	"github.com/hubbleops/hubbleops/internal/preflight"
	"github.com/hubbleops/hubbleops/internal/privacy"
)

const decisionIDVersion = "v2"

func Decide(req action.Request, findings []preflight.Finding, pol *policy.Policy) action.Decision {
	decision := policy.Evaluate(req, findings, pol)
	decision.DecisionID = decisionID(req, findings)
	decision.ReceiptID = decision.DecisionID
	decision.EvidenceHashes = evidenceHashes(req, findings)
	decision.TargetFingerprint = privacy.FingerprintString(req.Target)
	decision.IntentHash = privacy.FingerprintString(req.Intent)
	decision.IdempotencyKeyHash = privacy.FingerprintString(req.IdempotencyKey)
	if decision.BlastRadius == "" {
		decision.BlastRadius = blastRadius(req, findings, decision.RiskScore)
	}
	if len(decision.AllowedNextActions) == 0 {
		decision.AllowedNextActions = defaultAllowedNextActions(decision.Decision)
	}
	return decision
}

func decisionID(req action.Request, findings []preflight.Finding) string {
	parts := []string{
		decisionIDVersion,
		"project=" + req.Project,
		"session_id=" + req.SessionID,
		"actor=" + req.Actor,
		"human_delegator=" + req.HumanDelegator,
		"action=" + req.Action,
		"target=" + req.Target,
		"environment=" + req.Environment,
		"idempotency_key=" + req.IdempotencyKey,
	}
	findingHashes := make([]string, 0, len(findings))
	for _, finding := range findings {
		findingHashes = append(findingHashes, findingIDHash(finding))
	}
	sort.Strings(findingHashes)
	for _, hash := range findingHashes {
		parts = append(parts, "finding="+hash)
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return fmt.Sprintf("dec_%x", sum[:12])
}

func findingIDHash(finding preflight.Finding) string {
	parts := []string{
		"source=" + finding.Source,
		"kind=" + finding.Kind,
		"action=" + finding.Action,
		"target=" + finding.Target,
		"risk=" + fmt.Sprintf("%d", finding.RiskScore),
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return hex.EncodeToString(sum[:])
}

func evidenceHashes(req action.Request, findings []preflight.Finding) []string {
	seen := map[string]struct{}{}
	var out []string
	add := func(value string) {
		value = strings.TrimSpace(value)
		if value == "" {
			return
		}
		fp := privacy.FingerprintString(value)
		if _, ok := seen[fp]; ok {
			return
		}
		seen[fp] = struct{}{}
		out = append(out, fp)
	}
	for _, item := range req.Evidence {
		add(item)
	}
	for _, finding := range findings {
		add(finding.Source + "|" + finding.Kind + "|" + finding.Action + "|" + finding.Target)
		for _, item := range finding.Evidence {
			add(item)
		}
	}
	sort.Strings(out)
	return out
}

func blastRadius(req action.Request, findings []preflight.Finding, score int) string {
	if strings.TrimSpace(req.BlastRadius) != "" {
		return req.BlastRadius
	}
	targets := len(preflight.Targets(findings))
	switch {
	case score >= 90 || targets > 5:
		return "high"
	case score >= 70 || targets > 0:
		return "medium"
	default:
		return "low"
	}
}

func defaultAllowedNextActions(decision string) []string {
	switch decision {
	case action.DecisionBlock:
		return []string{"open_review"}
	case action.DecisionRequireApproval:
		return []string{"request_approval"}
	default:
		return []string{"continue"}
	}
}
