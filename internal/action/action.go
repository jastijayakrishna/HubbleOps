package action

import "strings"

const (
	DecisionAllow           = "allow"
	DecisionRequireApproval = "require_approval"
	DecisionBlock           = "block"

	RiskLow      = "low"
	RiskMedium   = "medium"
	RiskHigh     = "high"
	RiskCritical = "critical"

	PolicyVersion = "engineering-gate/v1"
)

type Request struct {
	Project         string   `json:"project,omitempty"`
	SessionID       string   `json:"session_id,omitempty"`
	Actor           string   `json:"actor"`
	HumanDelegator  string   `json:"human_delegator,omitempty"`
	Action          string   `json:"action"`
	Target          string   `json:"target,omitempty"`
	Intent          string   `json:"intent,omitempty"`
	Environment     string   `json:"environment,omitempty"`
	Evidence        []string `json:"evidence,omitempty"`
	IdempotencyKey  string   `json:"idempotency_key,omitempty"`
	ServiceRisk     string   `json:"service_risk,omitempty"`
	PolicyVersion   string   `json:"policy_version,omitempty"`
	CaptureMode     string   `json:"capture_mode,omitempty"`
	BlastRadius     string   `json:"blast_radius,omitempty"`
	RequiredContext []string `json:"required_context,omitempty"`
}

type Decision struct {
	Decision           string   `json:"decision"`
	Reason             string   `json:"reason"`
	RiskScore          int      `json:"risk_score"`
	RiskClass          string   `json:"risk_class"`
	RequiredApprovers  []string `json:"required_approvers,omitempty"`
	AllowedNextActions []string `json:"allowed_next_actions,omitempty"`
	Approvals          []string `json:"approvals,omitempty"`
	ReceiptID          string   `json:"receipt_id,omitempty"`
	DecisionID         string   `json:"decision_id,omitempty"`
	PolicyVersion      string   `json:"policy_version"`
	Evidence           []string `json:"evidence,omitempty"`
	EvidenceHashes     []string `json:"evidence_hashes,omitempty"`
	PolicyRuleID       string   `json:"policy_rule_id,omitempty"`
	PolicyChangedAfter bool     `json:"policy_changed_after_review,omitempty"`
	RequiresReceipt    bool     `json:"requires_receipt"`
	ReceiptAttempted   bool     `json:"receipt_attempted"`
	ReceiptError       string   `json:"receipt_error,omitempty"`
	BlastRadius        string   `json:"blast_radius,omitempty"`
	TargetFingerprint  string   `json:"target_fingerprint,omitempty"`
	IntentHash         string   `json:"intent_hash,omitempty"`
	IdempotencyKeyHash string   `json:"idempotency_key_hash,omitempty"`
}

func NormalizeDecision(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case DecisionBlock:
		return DecisionBlock
	case "approval", "require", "requires_approval", DecisionRequireApproval:
		return DecisionRequireApproval
	default:
		return DecisionAllow
	}
}

func RiskClass(score int) string {
	switch {
	case score >= 90:
		return RiskCritical
	case score >= 70:
		return RiskHigh
	case score >= 40:
		return RiskMedium
	default:
		return RiskLow
	}
}
