package preflight

import (
	"strings"

	"github.com/hubbleops/hubbleops/internal/privacy"
)

// SanitizeFindingsForOutput removes raw targets, file paths, and caller-supplied
// evidence from API/CLI responses while preserving stable fingerprints for review.
func SanitizeFindingsForOutput(findings []Finding) []Finding {
	if len(findings) == 0 {
		return nil
	}
	out := make([]Finding, 0, len(findings))
	for _, finding := range findings {
		safe := finding
		safe.Source = safeOutputIdentifier(finding.Source)
		safe.Kind = safeOutputIdentifier(finding.Kind)
		safe.Action = safeOutputIdentifier(finding.Action)
		safe.Target = safeOutputTarget(finding.Target)
		safe.File = safeOutputTarget(finding.File)
		safe.RiskClass = safeOutputIdentifier(finding.RiskClass)
		safe.Evidence = safeOutputEvidence(finding.Evidence)
		safe.ChangeTags = safeOutputIdentifiers(finding.ChangeTags)
		out = append(out, safe)
	}
	return out
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

func safeOutputTarget(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	return "fingerprint:" + privacy.FingerprintString(value)
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
