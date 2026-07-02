package action

import (
	"strings"

	"github.com/hubbleops/hubbleops/internal/privacy"
)

// SanitizeForOutput keeps public decision fields useful while preventing raw
// caller-controlled evidence, approvers, or reviewer identifiers from being
// echoed in CLI/API responses.
func SanitizeForOutput(decision Decision) Decision {
	decision.Decision = safeOutputIdentifier(decision.Decision)
	decision.Reason = safeOutputText(decision.Reason)
	decision.RiskClass = safeOutputIdentifier(decision.RiskClass)
	decision.Evidence = safeOutputEvidence(decision.Evidence)
	decision.RequiredApprovers = safeOutputIdentifiers(decision.RequiredApprovers)
	decision.AllowedNextActions = safeOutputIdentifiers(decision.AllowedNextActions)
	decision.Approvals = safeOutputIdentifiers(decision.Approvals)
	decision.ReceiptError = safeOutputText(decision.ReceiptError)
	decision.PolicyVersion = safeOutputIdentifier(decision.PolicyVersion)
	decision.PolicyRuleID = safeOutputIdentifier(decision.PolicyRuleID)
	decision.BlastRadius = safeOutputIdentifier(decision.BlastRadius)
	return decision
}

var safeOutputEvidenceKeys = map[string]struct{}{
	"action_risk":                  {},
	"approval_id":                  {},
	"approval_source":              {},
	"approval_status":              {},
	"claim":                        {},
	"codeowner_fingerprint":        {},
	"codeowners":                   {},
	"deploy_action":                {},
	"deploy_artifact_hash":         {},
	"deploy_environment":           {},
	"deploy_idempotency":           {},
	"deletion_protection_disabled": {},
	"duplicate_window":             {},
	"file_fingerprint":             {},
	"force_destroy_enabled":        {},
	"github_event":                 {},
	"github_file_fingerprint":      {},
	"github_file_status":           {},
	"github_head_sha_fingerprint":  {},
	"github_linked_ticket":         {},
	"github_pr_fingerprint":        {},
	"github_pr_number":             {},
	"github_repo_fingerprint":      {},
	"iam_wildcard_policy":          {},
	"idempotency_key":              {},
	"ledger_error":                 {},
	"lease":                        {},
	"mass_destroy_count":           {},
	"migration_contains":           {},
	"migration_input_hash":         {},
	"parse_status":                 {},
	"protected_resource":           {},
	"public_ingress":               {},
	"risk_unknown":                 {},
	"resource_fingerprint":         {},
	"resource_type":                {},
	"reviewer_fingerprint":         {},
	"s3_public_access":             {},
	"s3_versioning_disabled":       {},
	"service_fingerprint":          {},
	"service_risk":                 {},
	"skip_final_snapshot_enabled":  {},
	"source":                       {},
	"storage_shrink":               {},
	"terraform_action":             {},
	"terraform_plan_hash":          {},
}

func safeOutputEvidence(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	out := make([]string, 0, len(values))
	seen := map[string]struct{}{}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		safe := safeOutputEvidenceItem(value)
		if _, ok := seen[safe]; ok {
			continue
		}
		seen[safe] = struct{}{}
		out = append(out, safe)
	}
	return out
}

func safeOutputEvidenceItem(value string) string {
	key, raw, ok := strings.Cut(value, "=")
	key = strings.ToLower(strings.TrimSpace(key))
	raw = strings.TrimSpace(raw)
	_, allowed := safeOutputEvidenceKeys[key]
	if ok && allowed && isSafeOutputValue(raw) {
		return key + "=" + raw
	}
	return "evidence_fingerprint=" + privacy.FingerprintString(value)
}

func safeOutputIdentifiers(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	out := make([]string, 0, len(values))
	seen := map[string]struct{}{}
	for _, value := range values {
		safe := safeOutputIdentifier(value)
		if safe == "" {
			continue
		}
		if _, ok := seen[safe]; ok {
			continue
		}
		seen[safe] = struct{}{}
		out = append(out, safe)
	}
	return out
}

func safeOutputIdentifier(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if !isSafeOutputValue(value) {
		return "fingerprint:" + privacy.FingerprintString(value)
	}
	return value
}

func safeOutputText(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if len(value) > 512 || privacy.ContainsSensitiveText(value) || containsControl(value) {
		return "fingerprint:" + privacy.FingerprintString(value)
	}
	return value
}

func isSafeOutputValue(value string) bool {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > 160 || privacy.ContainsSensitiveText(value) {
		return false
	}
	if privacy.IsFingerprint(value) || privacy.IsSafeLabel(value) {
		return true
	}
	return isSafeOutputIdentifier(value)
}

func isSafeOutputIdentifier(value string) bool {
	if strings.IndexByte(value, '@') > 0 {
		return false
	}
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z':
		case r >= 'A' && r <= 'Z':
		case r >= '0' && r <= '9':
		case r == '_' || r == '.' || r == ':' || r == '/' || r == '#' || r == '-' || r == '@':
		default:
			return false
		}
	}
	return true
}

func containsControl(value string) bool {
	for _, r := range value {
		if r == '\n' || r == '\r' || r == '\t' || r < 0x20 || r == 0x7f {
			return true
		}
	}
	return false
}
