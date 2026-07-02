package loop

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/hubbleops/hubbleops/internal/privacy"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

const (
	// ActionPolicyVersion /3 introduced the two-phase (pending-lease -> committed)
	// idempotency protocol: a side effect is claimed under a short lease before it runs
	// and only promoted to the full duplicate window once it provably committed. This is
	// duplicate *suppression* with crash-safe reconciliation, not a distributed
	// transaction — see docs.
	// /4 hardens that protocol: releases must prove lease ownership with the claim
	// nonce, the duplicate window for fail-closed risks is floored server-side, and the
	// Redis commit is a single atomic script.
	ActionPolicyVersion = "idempotency-ledger/1"
	DetectorVersion     = "idempotency-ledger/1"

	ActionRiskRead      = "read"
	ActionRiskWrite     = "write"
	ActionRiskDangerous = "dangerous"

	SignalDuplicateSideEffect         = "duplicate_side_effect"
	SignalIdempotencyKeyReuseMismatch = "idempotency_key_reuse_mismatch"
	SignalMissingIdempotency          = "missing_idempotency_key"
	SignalActionFirewallUnavailable   = "action_firewall_unavailable"
	SignalActionInFlight              = "action_in_flight"

	// ActionOutcome* describe what the two-phase ledger did with a claim. They let the
	// HTTP layer tell apart a benign concurrent retry (in-flight), a committed duplicate
	// that must be replayed rather than re-executed, and a contradictory reuse.
	ActionOutcomeClaimed         = "claimed"
	ActionOutcomeInFlight        = "in_flight"
	ActionOutcomeCommittedReplay = "committed_replay"
	ActionOutcomeMismatch        = "mismatch"
)

const (
	defaultDuplicateWindow = 24 * time.Hour
	// defaultActionLease is how long a *pending* claim is held before the side effect
	// is confirmed. It must outlast a normal tool execution but stay short so that a
	// crash between claim and execution frees the key quickly: the effect provably
	// never committed, so a retry must be allowed rather than blocked for the full
	// duplicate window. Tunable per-store via WithLease.
	defaultActionLease = 2 * time.Minute
)

var (
	ErrLeaseNotHeld     = errors.New("action lease not held")
	ErrAlreadyCommitted = errors.New("action already committed")
)

type Action string

const (
	ActionNone  Action = "allow"
	ActionWarn  Action = "warn"
	ActionBlock Action = "block"
)

type Decision struct {
	SignalsFired     []string `json:"signals_fired,omitempty"`
	Confidence       float64  `json:"confidence,omitempty"`
	ActionCeiling    Action   `json:"action_ceiling,omitempty"`
	DetectorVersion  string   `json:"detector_version,omitempty"`
	Reason           string   `json:"reason,omitempty"`
	HadSession       bool     `json:"had_session,omitempty"`
	PolicyVersion    string   `json:"policy_version,omitempty"`
	DecisionEvidence []string `json:"decision_evidence,omitempty"`
}

type ActionStore struct {
	ledger actionLedger
	lease  time.Duration
}

func NewActionStore(rdb *redis.Client) *ActionStore {
	return &ActionStore{ledger: redisActionLedger{rdb: rdb}}
}

func NewPostgresActionStore(pool *pgxpool.Pool) *ActionStore {
	return &ActionStore{ledger: postgresActionLedger{pool: pool}}
}

func NewMemoryActionStore() *ActionStore {
	return &ActionStore{ledger: newMemoryActionLedger()}
}

// WithLease overrides the pending-claim lease duration (default 2m). A zero or
// negative value keeps the default.
func (as *ActionStore) WithLease(lease time.Duration) *ActionStore {
	if as != nil && lease > 0 {
		as.lease = lease
	}
	return as
}

func (as *ActionStore) leaseDuration() time.Duration {
	if as == nil || as.lease <= 0 {
		return defaultActionLease
	}
	return as.lease
}

type ActionObservation struct {
	Project    string
	SessionID  string
	StepID     string
	ToolName   string
	ActionRisk string
	// RawActionRisk is the client-declared risk label before any server-side floor was
	// applied. Fail-closed semantics (window floor, hold-on-ambiguous) key on both: the
	// raw label catches an honest "money_movement", the floored one catches a client lie.
	RawActionRisk          string
	IdempotencyKey         string
	AgentID                string
	UserID                 string
	ResourceID             string
	AmountCents            int64
	MaxAmountCents         int64
	BackupID               string
	Recipient              string
	AllowedDomain          string
	CapabilityToken        string
	DuplicateWindowSeconds int
	UnixMillis             int64
}

type ActionDecision struct {
	Decision Decision
	Reason   string
	Evidence []string
	// Outcome is one of ActionOutcome* for claim-stage decisions; empty for policy
	// blocks (amount/recipient/capability/missing-key) and reads. The HTTP layer uses
	// it to pick the right status: 200+replay for a committed duplicate, 409 for an
	// in-flight retry, 422 for a contradictory reuse.
	Outcome string
	// Replay carries the recorded outcome of the original committed action and is set
	// only when Outcome == ActionOutcomeCommittedReplay.
	Replay *ActionReplay
	// ClaimNonce proves ownership of the pending lease acquired by this decision. It is
	// set only when Outcome == ActionOutcomeClaimed; the result path must echo it on
	// Release so a late failure callback cannot free a lease it does not own.
	ClaimNonce string
}

// ActionReplay is the recorded result of the first, already-committed attempt for an
// idempotency key. It is what the firewall returns instead of executing a duplicate
// side effect a second time.
type ActionReplay struct {
	DecisionID        string `json:"decision_id,omitempty"`
	ResultClass       string `json:"result_class,omitempty"`
	ResultFingerprint string `json:"result_fingerprint,omitempty"`
	// Result is read only from legacy records that already contain a raw replay body.
	// New commits store result fingerprints and shape metadata instead.
	Result          json.RawMessage `json:"result,omitempty"`
	FirstSeenMillis int64           `json:"first_seen_millis,omitempty"`
	CommittedMillis int64           `json:"committed_millis,omitempty"`
}

// ActionResult is the post-execution outcome the result path hands back to the ledger
// so a pending claim is promoted to committed (on success) or released (on failure).
type ActionResult struct {
	Project                string
	IdempotencyKey         string
	ClaimNonce             string
	ToolName               string
	ActionRisk             string
	RawActionRisk          string
	ResourceID             string
	AmountCents            int64
	Recipient              string
	DecisionID             string
	ResultClass            string
	ResultFingerprint      string
	Result                 json.RawMessage
	DuplicateWindowSeconds int
	UnixMillis             int64
}

func (as *ActionStore) Decide(ctx context.Context, obs ActionObservation) (ActionDecision, error) {
	if as == nil || as.ledger == nil {
		return ActionDecision{}, fmt.Errorf("action ledger is not configured")
	}
	risk := NormalizeActionRisk(obs.ActionRisk)
	if risk == ActionRiskRead {
		return ActionDecision{Decision: allowActionDecision("no idempotency policy fired", obs.SessionID)}, nil
	}
	if obs.IdempotencyKey == "" {
		action := ActionWarn
		confidence := 0.60
		reason := "side-effect action is missing an idempotency key"
		if risk == ActionRiskDangerous {
			action = ActionBlock
			confidence = 0.85
			reason = "dangerous action is missing an idempotency key"
		}
		evidence := []string{"action_risk=" + risk, "idempotency_key=missing"}
		return ActionDecision{
			Decision: Decision{
				SignalsFired:     []string{SignalMissingIdempotency},
				Confidence:       confidence,
				ActionCeiling:    action,
				DetectorVersion:  DetectorVersion,
				Reason:           reason,
				HadSession:       obs.SessionID != "",
				PolicyVersion:    ActionPolicyVersion,
				DecisionEvidence: evidence,
			},
			Reason:   reason,
			Evidence: evidence,
		}, nil
	}

	window := duplicateWindow(obs.DuplicateWindowSeconds, firstNonEmptyString(obs.RawActionRisk, obs.ActionRisk), risk)
	lease := as.leaseDuration()
	key := actionKey(obs.Project, obs.IdempotencyKey)
	nowMillis := obs.UnixMillis
	if nowMillis == 0 {
		nowMillis = time.Now().UnixMilli()
	}
	currentFP := actionRequestFingerprint(obs, risk)
	nonce := newClaimNonce()
	record := map[string]any{
		"project":              actionLedgerIdentifier(obs.Project),
		"session_id":           actionLedgerIdentifier(obs.SessionID),
		"tool_name":            actionLedgerIdentifier(obs.ToolName),
		"action_risk":          risk,
		"idempotency_key_hash": actionValueFingerprint(obs.IdempotencyKey),
		"agent_id":             actionLedgerIdentifier(obs.AgentID),
		"user_id":              actionLedgerIdentifier(obs.UserID),
		"resource_fingerprint": actionValueFingerprint(obs.ResourceID),
		"amount_cents":         obs.AmountCents,
		"step_id":              actionLedgerIdentifier(obs.StepID),
		"first_seen_ms":        nowMillis,
		"request_fingerprint":  currentFP,
		"claim_nonce":          nonce,
	}
	data, _ := json.Marshal(record)
	// Phase 1: claim a short PENDING lease, not the full duplicate window. The window
	// is only committed once the side effect provably succeeds (ActionStore.Commit),
	// so a crash between this claim and execution frees the key when the lease expires
	// instead of silently blocking every retry for 24h.
	status, previous, err := as.ledger.Claim(ctx, key, data, lease, nonce)
	if err != nil {
		return ActionDecision{}, fmt.Errorf("claim action idempotency: %w", err)
	}
	if status == claimStatusClaimed {
		evidence := []string{"idempotency_key=first_seen", "claim=pending", "lease=" + lease.String(), "duplicate_window=" + window.String()}
		if obs.ResourceID != "" {
			evidence = append(evidence, "resource_fingerprint="+actionValueFingerprint(obs.ResourceID))
		}
		return ActionDecision{
			Decision: Decision{
				ActionCeiling:    ActionNone,
				DetectorVersion:  DetectorVersion,
				Reason:           "first action with this idempotency key",
				HadSession:       obs.SessionID != "",
				PolicyVersion:    ActionPolicyVersion,
				DecisionEvidence: evidence,
			},
			Reason:     "first action with this idempotency key",
			Evidence:   evidence,
			Outcome:    ActionOutcomeClaimed,
			ClaimNonce: nonce,
		}, nil
	}

	// An existing record is present. A reuse with a *different* payload is a client bug
	// or tampering regardless of whether the prior attempt is in-flight or committed, so
	// check that first and surface it as a contradiction (422), never a benign replay.
	var prev map[string]any
	if previous != "" {
		_ = json.Unmarshal([]byte(previous), &prev)
		if prevFP, _ := prev["request_fingerprint"].(string); prevFP != "" && prevFP != currentFP {
			d := blockActionDecision(
				SignalIdempotencyKeyReuseMismatch,
				"idempotency key reused with a different action payload",
				[]string{
					"idempotency_key=reused_with_different_payload",
					"stored_fingerprint=" + prevFP,
					"incoming_fingerprint=" + currentFP,
				},
				1.0,
				obs.SessionID,
			)
			d.Outcome = ActionOutcomeMismatch
			return d, nil
		}
	}

	if status == claimStatusInFlight {
		// The first attempt with this key is still within its pending lease — the side
		// effect may be running right now. Re-executing would risk a duplicate, so tell
		// the caller it is in flight and to retry; it is not a permanent block.
		evidence := []string{"idempotency_key=in_flight", "action_risk=" + risk, "lease=" + lease.String()}
		reason := "action with this idempotency key is already in flight; retry shortly"
		return ActionDecision{
			Decision: Decision{
				SignalsFired:     []string{SignalActionInFlight},
				Confidence:       1.0,
				ActionCeiling:    ActionBlock,
				DetectorVersion:  DetectorVersion,
				Reason:           reason,
				HadSession:       obs.SessionID != "",
				PolicyVersion:    ActionPolicyVersion,
				DecisionEvidence: evidence,
			},
			Reason:   reason,
			Evidence: evidence,
			Outcome:  ActionOutcomeInFlight,
		}, nil
	}

	// status == claimStatusCommitted: the first attempt already committed. Replay its
	// recorded outcome instead of running the side effect again.
	evidence := []string{
		"idempotency_key=committed",
		"action_risk=" + risk,
		"duplicate_window=" + window.String(),
	}
	if previous != "" {
		evidence = append(evidence, "previous_action="+summarizeActionRecord(previous))
	}
	reason := "duplicate side-effect: replaying recorded outcome of the original committed action"
	return ActionDecision{
		Decision: Decision{
			SignalsFired:     []string{SignalDuplicateSideEffect},
			Confidence:       1.0,
			ActionCeiling:    ActionBlock,
			DetectorVersion:  DetectorVersion,
			Reason:           reason,
			HadSession:       obs.SessionID != "",
			PolicyVersion:    ActionPolicyVersion,
			DecisionEvidence: evidence,
		},
		Reason:   reason,
		Evidence: evidence,
		Outcome:  ActionOutcomeCommittedReplay,
		Replay:   replayFromRecord(prev),
	}, nil
}

// Commit promotes a pending claim to committed for the full duplicate window and records
// the result so a later duplicate can be replayed. It is called from the result path only
// for a successful side effect; a failed one calls Release instead so a retry is allowed.
func (as *ActionStore) Commit(ctx context.Context, res ActionResult) error {
	if as == nil || as.ledger == nil {
		return fmt.Errorf("action ledger is not configured")
	}
	if res.IdempotencyKey == "" {
		return nil
	}
	risk := NormalizeActionRisk(res.ActionRisk)
	window := duplicateWindow(res.DuplicateWindowSeconds, firstNonEmptyString(res.RawActionRisk, res.ActionRisk), risk)
	key := actionKey(res.Project, res.IdempotencyKey)
	nowMillis := res.UnixMillis
	if nowMillis == 0 {
		nowMillis = time.Now().UnixMilli()
	}
	resultFingerprint := actionLedgerIdentifier(res.ResultFingerprint)
	if resultFingerprint == "" && len(res.Result) > 0 {
		resultFingerprint = actionValueFingerprint(string(res.Result))
	}
	record := map[string]any{
		"project":              actionLedgerIdentifier(res.Project),
		"tool_name":            actionLedgerIdentifier(res.ToolName),
		"action_risk":          risk,
		"idempotency_key_hash": actionValueFingerprint(res.IdempotencyKey),
		"resource_fingerprint": actionValueFingerprint(res.ResourceID),
		"amount_cents":         res.AmountCents,
		"committed_ms":         nowMillis,
		"request_fingerprint":  actionResultFingerprint(res, risk),
		"decision_id":          actionLedgerIdentifier(res.DecisionID),
		"result_class":         actionLedgerIdentifier(res.ResultClass),
		"result_fingerprint":   resultFingerprint,
	}
	if len(res.Result) > 0 {
		record["result_payload_fingerprint"] = actionValueFingerprint(string(res.Result))
		record["result_shape"] = jsonPayloadShape(res.Result)
	}
	data, _ := json.Marshal(record)
	return as.ledger.Commit(ctx, key, data, window, res.ClaimNonce)
}

// Release drops a pending claim so a known-failed action is immediately retryable rather
// than waiting out the lease. The nonce must be the ClaimNonce returned when the lease
// was acquired: a release that cannot prove ownership is a no-op, so a late failure
// callback from an earlier attempt cannot free a newer attempt's live lease.
func (as *ActionStore) Release(ctx context.Context, project, idempotencyKey, nonce string) error {
	if as == nil || as.ledger == nil {
		return fmt.Errorf("action ledger is not configured")
	}
	if idempotencyKey == "" {
		return nil
	}
	return as.ledger.Release(ctx, actionKey(project, idempotencyKey), nonce)
}

// Heartbeat refreshes a pending idempotency claim so a legitimate long-running side
// effect does not lose its short lease and allow a retry to execute concurrently.
func (as *ActionStore) Heartbeat(ctx context.Context, project, idempotencyKey, nonce string) error {
	if as == nil || as.ledger == nil {
		return fmt.Errorf("action ledger is not configured")
	}
	if idempotencyKey == "" {
		return nil
	}
	return as.ledger.Extend(ctx, actionKey(project, idempotencyKey), as.leaseDuration(), nonce)
}

// HeartbeatEvery keeps a pending claim alive until stopped, the context is cancelled, or
// the lease is no longer owned. Losing the lease means the caller should stop executing if
// it can: another attempt may have legitimately reclaimed the idempotency key.
func (as *ActionStore) HeartbeatEvery(ctx context.Context, project, key, nonce string) (stop func()) {
	heartbeatCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		defer close(done)
		lease := as.leaseDuration()
		interval := lease / 3
		if interval <= 0 {
			interval = lease
		}
		if interval <= 0 {
			interval = time.Second
		}
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-heartbeatCtx.Done():
				return
			case <-ticker.C:
				err := as.Heartbeat(heartbeatCtx, project, key, nonce)
				if errors.Is(err, ErrLeaseNotHeld) || errors.Is(err, ErrAlreadyCommitted) {
					return
				}
			}
		}
	}()
	return func() {
		cancel()
		<-done
	}
}

// SweepExpired removes expired pending leases and committed duplicate-window records
// from ledgers that need explicit cleanup. Backends with native TTL expiry can no-op.
func (as *ActionStore) SweepExpired(ctx context.Context) (int64, error) {
	if as == nil || as.ledger == nil {
		return 0, fmt.Errorf("action ledger is not configured")
	}
	sweeper, ok := as.ledger.(actionLedgerSweeper)
	if !ok {
		return 0, nil
	}
	return sweeper.SweepExpired(ctx)
}

// Invalidate removes a committed or pending key without ownership proof. It is retained
// for legacy deploy-result callbacks; new production callers should use
// InvalidateOwned so a caller that only knows the idempotency key cannot erase another
// action's committed duplicate-suppression record.
func (as *ActionStore) Invalidate(ctx context.Context, project, idempotencyKey string) error {
	if as == nil || as.ledger == nil {
		return fmt.Errorf("action ledger is not configured")
	}
	if idempotencyKey == "" {
		return nil
	}
	return as.ledger.Invalidate(ctx, actionKey(project, idempotencyKey))
}

// InvalidateOwned removes a committed key only when its stored decision_id matches the
// caller-provided decisionID. Pending records and committed records from a different
// decision are left intact, so failure callbacks cannot clear someone else's dedup entry.
func (as *ActionStore) InvalidateOwned(ctx context.Context, project, idempotencyKey, decisionID string) error {
	if as == nil || as.ledger == nil {
		return fmt.Errorf("action ledger is not configured")
	}
	if idempotencyKey == "" || strings.TrimSpace(decisionID) == "" {
		return nil
	}
	return as.ledger.InvalidateOwned(ctx, actionKey(project, idempotencyKey), actionLedgerIdentifier(decisionID))
}

// newClaimNonce returns an unguessable per-claim ownership token. It is not a secret in
// the cryptographic sense — it only has to be unique enough that a stale callback can
// never accidentally (or deliberately) match a lease it did not acquire.
func newClaimNonce() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("t%x", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}

func replayFromRecord(rec map[string]any) *ActionReplay {
	if rec == nil {
		return nil
	}
	replay := &ActionReplay{}
	if v, ok := rec["decision_id"].(string); ok {
		replay.DecisionID = v
	}
	if v, ok := rec["result_class"].(string); ok {
		replay.ResultClass = v
	}
	if v, ok := rec["result_fingerprint"].(string); ok {
		replay.ResultFingerprint = v
	}
	if v, ok := rec["result"]; ok {
		if raw, err := json.Marshal(v); err == nil && string(raw) != "null" {
			replay.Result = raw
		}
	}
	replay.FirstSeenMillis = recordMillis(rec, "first_seen_ms")
	replay.CommittedMillis = recordMillis(rec, "committed_ms")
	return replay
}

func decisionIDFromRecord(raw string) string {
	var rec map[string]any
	if err := json.Unmarshal([]byte(raw), &rec); err != nil {
		return ""
	}
	decisionID, _ := rec["decision_id"].(string)
	return decisionID
}

func carryForwardRequestFingerprint(committed []byte, pending string) ([]byte, error) {
	if pending == "" {
		return committed, nil
	}
	var pendingRec map[string]any
	if err := json.Unmarshal([]byte(pending), &pendingRec); err != nil {
		return committed, nil
	}
	claimFP, _ := pendingRec["request_fingerprint"].(string)
	if claimFP == "" {
		return committed, nil
	}
	var committedRec map[string]any
	if err := json.Unmarshal(committed, &committedRec); err != nil {
		return nil, err
	}
	if got, _ := committedRec["request_fingerprint"].(string); got == claimFP {
		return committed, nil
	}
	committedRec["request_fingerprint"] = claimFP
	committedRec["fingerprint_source"] = "claim_carryforward"
	return json.Marshal(committedRec)
}

func recordMillis(rec map[string]any, key string) int64 {
	switch v := rec[key].(type) {
	case float64:
		return int64(v)
	case int64:
		return v
	case json.Number:
		n, _ := v.Int64()
		return n
	}
	return 0
}

func blockActionDecision(signal, reason string, evidence []string, confidence float64, sessionID string) ActionDecision {
	return ActionDecision{
		Decision: Decision{
			SignalsFired:     []string{signal},
			Confidence:       confidence,
			ActionCeiling:    ActionBlock,
			DetectorVersion:  DetectorVersion,
			Reason:           reason,
			HadSession:       sessionID != "",
			PolicyVersion:    ActionPolicyVersion,
			DecisionEvidence: evidence,
		},
		Reason:   reason,
		Evidence: evidence,
	}
}

func emailDomain(recipient string) string {
	recipient = strings.TrimSpace(strings.ToLower(recipient))
	at := strings.LastIndex(recipient, "@")
	if at < 0 || at == len(recipient)-1 {
		return ""
	}
	return recipient[at+1:]
}

func firstNonEmptyString(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func NormalizeActionRisk(risk string) string {
	risk = strings.ToLower(strings.TrimSpace(risk))
	switch risk {
	case "", "readonly", "read_only", "low":
		return ActionRiskRead
	case "side_effect", "customer_visible", "money_movement", "write", "medium", "high":
		return ActionRiskWrite
	case "danger", "dangerous", "critical", "destructive":
		return ActionRiskDangerous
	default:
		return risk
	}
}

// actionRiskRank orders risk classes so a floor can only ever raise, never lower,
// the effective risk. Unknown/custom classes rank above read so they are not
// silently treated as harmless.
func actionRiskRank(risk string) int {
	switch NormalizeActionRisk(risk) {
	case ActionRiskRead:
		return 0
	case ActionRiskWrite:
		return 1
	case ActionRiskDangerous:
		return 2
	default:
		return 1
	}
}

// FloorRisk applies a server-side minimum risk class to a client-supplied label.
// The client can raise the risk but never downgrade below the floor the operator
// classified for this tool, so a refund sent as risk:"read" still engages the
// idempotency/dedup firewall. This is a per-tool floor, not a rules engine.
func FloorRisk(client, floor string) string {
	client = NormalizeActionRisk(client)
	if strings.TrimSpace(floor) == "" {
		return client
	}
	if actionRiskRank(floor) > actionRiskRank(client) {
		return NormalizeActionRisk(floor)
	}
	return client
}

// FailClosedRisk reports whether an action's declared risk is high-stakes enough
// that HubbleOps should fail CLOSED (block) when it cannot complete a check or
// durably record the decision — rather than letting the side effect through.
// It keys on the raw label so "money_movement" is caught even though it
// normalizes to the write tier for loop-detection purposes.
func FailClosedRisk(risk string) bool {
	switch strings.ToLower(strings.TrimSpace(risk)) {
	case "money_movement", "danger", "dangerous", "critical", "destructive":
		return true
	}
	return NormalizeActionRisk(risk) == ActionRiskDangerous
}

func allowActionDecision(reason, sessionID string) Decision {
	return Decision{
		ActionCeiling:   ActionNone,
		DetectorVersion: DetectorVersion,
		Reason:          reason,
		HadSession:      sessionID != "",
		PolicyVersion:   ActionPolicyVersion,
	}
}

// Action disposition tells the result path how to reconcile a pending claim:
// commit it (the side effect succeeded), release it (it failed, so a retry is allowed),
// or hold it (ambiguous/in-progress — let the lease decide).
const (
	ActionDispositionCommit  = "commit"
	ActionDispositionRelease = "release"
	ActionDispositionHold    = "hold"
)

// ResultClassDisposition maps a post-execution result class to a ledger disposition. Only
// a clearly successful action commits the full duplicate window; a clearly failed one is
// released so it can be retried; anything ambiguous is held so the short lease — not a
// 24h block — governs retryability.
func ResultClassDisposition(resultClass string) string {
	switch strings.ToLower(strings.TrimSpace(resultClass)) {
	case "success", "empty", "ok":
		return ActionDispositionCommit
	case "timeout", "not_found", "permission_error", "schema_error", "unknown_error", "error", "failed", "rate_limited":
		return ActionDispositionRelease
	default:
		return ActionDispositionHold
	}
}

// ResultDisposition is the risk-aware disposition. An ambiguous failure
// (timeout, generic 5xx/unknown error) does NOT prove the side effect never
// committed — the request may have executed before the connection died. For
// fail-closed actions (dangerous / money movement) the pending lease is HELD so
// a blind retry is suppressed until the lease expires or the outcome is
// verified, instead of being released into a potential double execution.
// Provably-not-executed classes (rate limited, not found, permission, schema)
// stay immediately retryable for every risk tier.
func ResultDisposition(resultClass, rawRisk, flooredRisk string) string {
	disposition := ResultClassDisposition(resultClass)
	if disposition != ActionDispositionRelease {
		return disposition
	}
	switch strings.ToLower(strings.TrimSpace(resultClass)) {
	case "timeout", "unknown_error", "error", "failed":
		if FailClosedRisk(rawRisk) || NormalizeActionRisk(flooredRisk) == ActionRiskDangerous {
			return ActionDispositionHold
		}
	}
	return disposition
}

// duplicateWindow resolves the committed-record TTL. The window is client-tunable for
// low-stakes writes, but for fail-closed risks (money movement, dangerous — by raw label
// or by server floor) it is a server-side guarantee: a client value below the default
// is floored so a buggy or adversarial caller cannot collapse its own dedup window.
func duplicateWindow(seconds int, rawRisk, risk string) time.Duration {
	window := defaultDuplicateWindow
	if seconds > 0 {
		window = time.Duration(seconds) * time.Second
	}
	if (FailClosedRisk(rawRisk) || FailClosedRisk(risk)) && window < defaultDuplicateWindow {
		return defaultDuplicateWindow
	}
	return window
}

func actionKey(project, idempotencyKey string) string {
	sum := sha256.Sum256([]byte(project + "\x00" + idempotencyKey))
	return fmt.Sprintf("action:idempotency:%x", sum[:])
}

func actionValueFingerprint(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(value))
	return fmt.Sprintf("sha256:%x", sum[:])
}

func actionLedgerIdentifier(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if len(value) > 160 || privacy.ContainsSensitiveText(value) || !hasOnlyActionLedgerIdentifierChars(value) || hasUnsafeActionLedgerAtSign(value) {
		return actionValueFingerprint(value)
	}
	return value
}

func hasOnlyActionLedgerIdentifierChars(value string) bool {
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

func hasUnsafeActionLedgerAtSign(value string) bool {
	return strings.IndexByte(value, '@') > 0
}

func jsonPayloadShape(raw json.RawMessage) string {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return "bytes"
	}
	switch value.(type) {
	case map[string]any:
		return "object"
	case []any:
		return "array"
	case string:
		return "string"
	case bool:
		return "bool"
	case nil:
		return "null"
	default:
		return "number"
	}
}

// actionRequestFingerprint binds an idempotency key to the payload it was first
// used with. Reusing the same key with a different tool, risk, resource, amount,
// or recipient domain is a client bug or tampering, not a safe replay, so the
// duplicate path compares this fingerprint instead of trusting the key alone.
func actionRequestFingerprint(obs ActionObservation, risk string) string {
	return actionFingerprintParts(obs.ToolName, risk, obs.ResourceID, obs.AmountCents, obs.Recipient)
}

// actionResultFingerprint recomputes the request fingerprint from the post-execution
// result observation so a committed record carries the same binding the claim did, and a
// later duplicate is still checked for payload mismatch.
func actionResultFingerprint(res ActionResult, risk string) string {
	return actionFingerprintParts(res.ToolName, risk, res.ResourceID, res.AmountCents, res.Recipient)
}

func actionFingerprintParts(tool, risk, resourceID string, amountCents int64, recipient string) string {
	parts := []string{
		"tool=" + tool,
		"risk=" + risk,
		"resource=" + actionValueFingerprint(resourceID),
		fmt.Sprintf("amount=%d", amountCents),
		"recipient=" + emailDomain(recipient),
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return fmt.Sprintf("sha256:%x", sum[:])
}

func summarizeActionRecord(raw string) string {
	var rec map[string]any
	if err := json.Unmarshal([]byte(raw), &rec); err != nil {
		if len(raw) > 120 {
			return raw[:120]
		}
		return raw
	}
	parts := []string{}
	for _, key := range []string{"tool_name", "step_id", "session_id", "first_seen_ms"} {
		if value, ok := rec[key]; ok && value != "" {
			parts = append(parts, fmt.Sprintf("%s=%v", key, value))
		}
	}
	return strings.Join(parts, ",")
}

// claimStatus is the result of attempting to claim a pending lease.
type claimStatus int

const (
	// claimStatusClaimed: a fresh pending lease was acquired — no live record existed,
	// or a prior pending lease had expired (so the prior side effect provably never
	// committed and a retry is allowed).
	claimStatusClaimed claimStatus = iota
	// claimStatusInFlight: a live pending lease already exists; the first attempt may
	// still be running.
	claimStatusInFlight
	// claimStatusCommitted: a committed record already exists within the duplicate
	// window; the action is a true duplicate and must be replayed, not re-executed.
	claimStatusCommitted
)

const (
	ledgerStatePending   = "pending"
	ledgerStateCommitted = "committed"
)

type actionLedger interface {
	// Claim attempts to acquire a fresh PENDING lease for key, owned by nonce. When a
	// live pending lease or a committed record already exists it returns the
	// corresponding status and the stored record JSON in previous.
	Claim(ctx context.Context, key string, pending []byte, lease time.Duration, nonce string) (claimStatus, string, error)
	// Commit promotes key to COMMITTED with the full window and stores the result record.
	// It must prove ownership of a live pending lease with nonce. If the pending lease has
	// already expired and disappeared, commit is allowed so a slow-but-successful original
	// side effect can still become the replay source.
	Commit(ctx context.Context, key string, committed []byte, window time.Duration, nonce string) error
	// Release drops a pending lease so a known-failed action is immediately retryable. It
	// must not remove a committed record, and it must not remove a pending lease whose
	// stored owner nonce does not match the caller's (a lease claimed without a nonce —
	// a legacy record — remains releasable by anyone).
	Release(ctx context.Context, key, nonce string) error
	// Extend refreshes a live pending lease owned by nonce. Heartbeats close the
	// slow-execution gap where a legitimate action runs longer than the short pending
	// lease, loses its claim, and a retry executes the same side effect concurrently.
	// Legacy pending records with an empty stored nonce are extendable by anyone, matching
	// Release. Committed records return ErrAlreadyCommitted; missing, expired, or
	// nonce-mismatched pending records return ErrLeaseNotHeld.
	Extend(ctx context.Context, key string, lease time.Duration, nonce string) error
	// Invalidate removes a key regardless of state (pending or committed). It is the
	// legacy unowned escape hatch; prefer InvalidateOwned for production callbacks.
	Invalidate(ctx context.Context, key string) error
	// InvalidateOwned removes only a committed record whose stored decision_id matches
	// the caller's decision ID. Pending rows and mismatched committed rows survive.
	InvalidateOwned(ctx context.Context, key, decisionID string) error
}

type actionLedgerSweeper interface {
	SweepExpired(ctx context.Context) (int64, error)
}

type redisActionLedger struct {
	rdb *redis.Client
}

// claimScript acquires a pending lease iff no record exists, otherwise reports whether the
// existing record is pending (in-flight) or committed. State is tracked as a hash field so
// the lease TTL on the key handles expiry without any JSON parsing in Lua.
var claimScript = redis.NewScript(`
local state = redis.call('HGET', KEYS[1], 'state')
if not state then
  redis.call('HSET', KEYS[1], 'state', 'pending', 'record', ARGV[1], 'nonce', ARGV[3])
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return {'claimed', ''}
end
local rec = redis.call('HGET', KEYS[1], 'record')
if state == 'committed' then return {'committed', rec} end
return {'pending', rec}
`)

// releaseScript drops the key only while it is still pending AND the caller proves
// ownership with the nonce minted at claim time, so a late failure callback can neither
// delete another attempt's committed record nor free a newer attempt's live lease.
// A pending record with no stored nonce (written before ownership existed) stays
// releasable by anyone.
var releaseScript = redis.NewScript(`
if redis.call('HGET', KEYS[1], 'state') == 'pending' then
  local owner = redis.call('HGET', KEYS[1], 'nonce')
  if (not owner) or owner == '' or owner == ARGV[1] then
    return redis.call('DEL', KEYS[1])
  end
end
return 0
`)

// invalidateOwnedScript deletes only committed records that carry the caller's
// decision_id in their stored record JSON. Malformed legacy records and pending leases
// are treated as not owned and left untouched.
var invalidateOwnedScript = redis.NewScript(`
if redis.call('HGET', KEYS[1], 'state') ~= 'committed' then return 0 end
local rec = redis.call('HGET', KEYS[1], 'record')
if not rec then return 0 end
local ok, obj = pcall(cjson.decode, rec)
if not ok then return 0 end
if obj['decision_id'] == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
`)

// extendScript refreshes the TTL only while the key is still pending and the caller owns
// the claim nonce. An empty stored nonce is a legacy pending record and remains
// extendable. Committed records are reported distinctly from missing/mismatched leases so
// callers can stop heartbeating for the right reason.
var extendScript = redis.NewScript(`
local state = redis.call('HGET', KEYS[1], 'state')
if not state then return 'missing' end
if state == 'committed' then return 'committed' end
if state == 'pending' then
  local owner = redis.call('HGET', KEYS[1], 'nonce')
  if (not owner) or owner == '' or owner == ARGV[2] then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
    return 'extended'
  end
end
return 'notheld'
`)

func (l redisActionLedger) Claim(ctx context.Context, key string, pending []byte, lease time.Duration, nonce string) (claimStatus, string, error) {
	if l.rdb == nil {
		return claimStatusClaimed, "", fmt.Errorf("redis client is nil")
	}
	res, err := claimScript.Run(ctx, l.rdb, []string{key}, pending, lease.Milliseconds(), nonce).Result()
	if err != nil {
		return claimStatusClaimed, "", err
	}
	return parseClaimResult(res)
}

// commitScript promotes the key to committed and sets the full-window TTL in one atomic
// step. Doing this as two client calls (HSET then PEXPIRE) left a crash window in which
// the committed record kept the short pending-lease TTL, silently collapsing the 24h
// duplicate window to ~2 minutes for that key. The claim's ownership nonce is dropped:
// a committed record is never releasable, so retaining it would only mislead.
var commitScript = redis.NewScript(`
local state = redis.call('HGET', KEYS[1], 'state')
if state == 'committed' then return 1 end
local record = ARGV[1]
if state == 'pending' then
  local owner = redis.call('HGET', KEYS[1], 'nonce')
  if owner and owner ~= '' and owner ~= ARGV[3] then
    return redis.error_reply('claim nonce mismatch')
  end
  local pending = redis.call('HGET', KEYS[1], 'record')
  if pending then
    local pending_ok, pending_obj = pcall(cjson.decode, pending)
    local committed_ok, committed_obj = pcall(cjson.decode, ARGV[1])
    if pending_ok and committed_ok then
      local claim_fp = pending_obj['request_fingerprint']
      if claim_fp and claim_fp ~= '' and committed_obj['request_fingerprint'] ~= claim_fp then
        committed_obj['request_fingerprint'] = claim_fp
        committed_obj['fingerprint_source'] = 'claim_carryforward'
        record = cjson.encode(committed_obj)
      end
    end
  end
end
redis.call('HSET', KEYS[1], 'state', 'committed', 'record', record)
redis.call('HDEL', KEYS[1], 'nonce')
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
`)

func (l redisActionLedger) Commit(ctx context.Context, key string, committed []byte, window time.Duration, nonce string) error {
	if l.rdb == nil {
		return fmt.Errorf("redis client is nil")
	}
	return commitScript.Run(ctx, l.rdb, []string{key}, committed, window.Milliseconds(), nonce).Err()
}

func (l redisActionLedger) Release(ctx context.Context, key, nonce string) error {
	if l.rdb == nil {
		return fmt.Errorf("redis client is nil")
	}
	return releaseScript.Run(ctx, l.rdb, []string{key}, nonce).Err()
}

func (l redisActionLedger) Extend(ctx context.Context, key string, lease time.Duration, nonce string) error {
	if l.rdb == nil {
		return fmt.Errorf("redis client is nil")
	}
	res, err := extendScript.Run(ctx, l.rdb, []string{key}, lease.Milliseconds(), nonce).Text()
	if err != nil {
		return err
	}
	switch res {
	case "extended":
		return nil
	case "committed":
		return ErrAlreadyCommitted
	default:
		return ErrLeaseNotHeld
	}
}

func (l redisActionLedger) Invalidate(ctx context.Context, key string) error {
	if l.rdb == nil {
		return fmt.Errorf("redis client is nil")
	}
	return l.rdb.Del(ctx, key).Err()
}

func (l redisActionLedger) InvalidateOwned(ctx context.Context, key, decisionID string) error {
	if l.rdb == nil {
		return fmt.Errorf("redis client is nil")
	}
	return invalidateOwnedScript.Run(ctx, l.rdb, []string{key}, decisionID).Err()
}

func (l redisActionLedger) SweepExpired(ctx context.Context) (int64, error) {
	// Redis owns expiry with per-key TTLs, so there is no table/file to sweep.
	return 0, nil
}

func parseClaimResult(res any) (claimStatus, string, error) {
	arr, ok := res.([]any)
	if !ok || len(arr) < 1 {
		return claimStatusClaimed, "", fmt.Errorf("unexpected claim script result: %v", res)
	}
	label, _ := arr[0].(string)
	previous := ""
	if len(arr) > 1 {
		previous, _ = arr[1].(string)
	}
	switch label {
	case "claimed":
		return claimStatusClaimed, "", nil
	case "committed":
		return claimStatusCommitted, previous, nil
	default:
		return claimStatusInFlight, previous, nil
	}
}

type postgresActionLedger struct {
	pool *pgxpool.Pool
}

const actionLedgerSweepBatch = 5000

func (l postgresActionLedger) Claim(ctx context.Context, key string, pending []byte, lease time.Duration, nonce string) (claimStatus, string, error) {
	if l.pool == nil {
		return claimStatusClaimed, "", fmt.Errorf("postgres pool is nil")
	}
	expiresAt := time.Now().Add(lease)
	tx, err := l.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return claimStatusClaimed, "", err
	}
	defer tx.Rollback(ctx)

	// Expired rows (lapsed pending leases and lapsed committed windows alike) are cleared
	// so the key is re-claimable.
	if _, err := tx.Exec(ctx, "DELETE FROM action_ledger WHERE action_key = $1 AND expires_at <= now()", key); err != nil {
		return claimStatusClaimed, "", err
	}
	tag, err := tx.Exec(ctx, `
		INSERT INTO action_ledger (action_key, first_record, state, expires_at)
		VALUES ($1, $2::jsonb, 'pending', $3)
		ON CONFLICT (action_key) DO NOTHING
	`, key, string(pending), expiresAt)
	if err != nil {
		return claimStatusClaimed, "", err
	}
	if tag.RowsAffected() == 1 {
		if err := tx.Commit(ctx); err != nil {
			return claimStatusClaimed, "", err
		}
		return claimStatusClaimed, "", nil
	}

	var previous, state string
	if err := tx.QueryRow(ctx, "SELECT first_record::text, state FROM action_ledger WHERE action_key = $1", key).Scan(&previous, &state); err != nil {
		return claimStatusClaimed, "", err
	}
	if err := tx.Commit(ctx); err != nil {
		return claimStatusClaimed, "", err
	}
	if state == ledgerStateCommitted {
		return claimStatusCommitted, previous, nil
	}
	return claimStatusInFlight, previous, nil
}

func (l postgresActionLedger) Commit(ctx context.Context, key string, committed []byte, window time.Duration, nonce string) error {
	if l.pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	expiresAt := time.Now().Add(window)
	tx, err := l.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "DELETE FROM action_ledger WHERE action_key = $1 AND expires_at <= now()", key); err != nil {
		return err
	}
	var previous, state string
	err = tx.QueryRow(ctx, "SELECT first_record::text, state FROM action_ledger WHERE action_key = $1 FOR UPDATE", key).Scan(&previous, &state)
	if err == pgx.ErrNoRows {
		if _, err := tx.Exec(ctx, `
			INSERT INTO action_ledger (action_key, first_record, state, expires_at)
			VALUES ($1, $2::jsonb, 'committed', $3)
		`, key, string(committed), expiresAt); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	if err != nil {
		return err
	}
	if state == ledgerStateCommitted {
		return tx.Commit(ctx)
	}
	var rec map[string]any
	_ = json.Unmarshal([]byte(previous), &rec)
	if owner, _ := rec["claim_nonce"].(string); owner != "" && owner != nonce {
		return fmt.Errorf("claim nonce mismatch")
	}
	committed, err = carryForwardRequestFingerprint(committed, previous)
	if err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE action_ledger
		SET first_record = $2::jsonb, state = 'committed', expires_at = $3
		WHERE action_key = $1
	`, key, string(committed), expiresAt); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// Release deletes the pending row only when the caller's nonce matches the one stored in
// the pending record at claim time (kept inside first_record so no schema change is
// needed). Rows written before ownership existed carry no nonce and stay releasable.
func (l postgresActionLedger) Release(ctx context.Context, key, nonce string) error {
	if l.pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	_, err := l.pool.Exec(ctx, `
		DELETE FROM action_ledger
		WHERE action_key = $1 AND state = 'pending'
		  AND (COALESCE(first_record->>'claim_nonce', '') = '' OR first_record->>'claim_nonce' = $2)
	`, key, nonce)
	return err
}

func (l postgresActionLedger) Extend(ctx context.Context, key string, lease time.Duration, nonce string) error {
	if l.pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	expiresAt := time.Now().Add(lease)
	tag, err := l.pool.Exec(ctx, `
		UPDATE action_ledger
		SET expires_at = $2
		WHERE action_key = $1 AND state = 'pending' AND expires_at > now()
		  AND (COALESCE(first_record->>'claim_nonce', '') = '' OR first_record->>'claim_nonce' = $3)
	`, key, expiresAt, nonce)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 1 {
		return nil
	}
	var state string
	err = l.pool.QueryRow(ctx, `
		SELECT state FROM action_ledger
		WHERE action_key = $1 AND expires_at > now()
	`, key).Scan(&state)
	if err == pgx.ErrNoRows {
		return ErrLeaseNotHeld
	}
	if err != nil {
		return err
	}
	if state == ledgerStateCommitted {
		return ErrAlreadyCommitted
	}
	return ErrLeaseNotHeld
}

func (l postgresActionLedger) Invalidate(ctx context.Context, key string) error {
	if l.pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	_, err := l.pool.Exec(ctx, "DELETE FROM action_ledger WHERE action_key = $1", key)
	return err
}

func (l postgresActionLedger) InvalidateOwned(ctx context.Context, key, decisionID string) error {
	if l.pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	_, err := l.pool.Exec(ctx, `
		DELETE FROM action_ledger
		WHERE action_key = $1 AND state = 'committed' AND first_record->>'decision_id' = $2
	`, key, decisionID)
	return err
}

func (l postgresActionLedger) SweepExpired(ctx context.Context) (int64, error) {
	if l.pool == nil {
		return 0, fmt.Errorf("postgres pool is nil")
	}
	var total int64
	for {
		tag, err := l.pool.Exec(ctx, `
			WITH doomed AS (
				SELECT ctid
				FROM action_ledger
				WHERE expires_at <= now()
				LIMIT $1
			)
			DELETE FROM action_ledger AS a
			USING doomed
			WHERE a.ctid = doomed.ctid
		`, actionLedgerSweepBatch)
		if err != nil {
			return total, err
		}
		removed := tag.RowsAffected()
		total += removed
		if removed < actionLedgerSweepBatch {
			return total, nil
		}
	}
}

type memoryActionLedger struct {
	mu    sync.Mutex
	items map[string]memoryActionItem
}

type memoryActionItem struct {
	value     string
	state     string
	nonce     string
	expiresAt time.Time
}

func newMemoryActionLedger() *memoryActionLedger {
	return &memoryActionLedger{items: make(map[string]memoryActionItem)}
}

func (l *memoryActionLedger) Claim(ctx context.Context, key string, pending []byte, lease time.Duration, nonce string) (claimStatus, string, error) {
	select {
	case <-ctx.Done():
		return claimStatusClaimed, "", ctx.Err()
	default:
	}
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	if item, ok := l.items[key]; ok {
		if item.expiresAt.IsZero() || item.expiresAt.After(now) {
			if item.state == ledgerStateCommitted {
				return claimStatusCommitted, item.value, nil
			}
			return claimStatusInFlight, item.value, nil
		}
		delete(l.items, key)
	}
	expiresAt := time.Time{}
	if lease > 0 {
		expiresAt = now.Add(lease)
	}
	l.items[key] = memoryActionItem{value: string(pending), state: ledgerStatePending, nonce: nonce, expiresAt: expiresAt}
	return claimStatusClaimed, "", nil
}

func (l *memoryActionLedger) Commit(ctx context.Context, key string, committed []byte, window time.Duration, nonce string) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	now := time.Now()
	var pending string
	l.mu.Lock()
	defer l.mu.Unlock()
	if item, ok := l.items[key]; ok {
		if !item.expiresAt.IsZero() && !item.expiresAt.After(now) {
			delete(l.items, key)
		} else if item.state == ledgerStateCommitted {
			return nil
		} else if item.nonce != "" && item.nonce != nonce {
			return fmt.Errorf("claim nonce mismatch")
		} else if item.state == ledgerStatePending {
			pending = item.value
		}
	}
	var err error
	committed, err = carryForwardRequestFingerprint(committed, pending)
	if err != nil {
		return err
	}
	expiresAt := time.Time{}
	if window > 0 {
		expiresAt = now.Add(window)
	}
	l.items[key] = memoryActionItem{value: string(committed), state: ledgerStateCommitted, expiresAt: expiresAt}
	return nil
}

func (l *memoryActionLedger) Release(ctx context.Context, key, nonce string) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if item, ok := l.items[key]; ok && item.state == ledgerStatePending && (item.nonce == "" || item.nonce == nonce) {
		delete(l.items, key)
	}
	return nil
}

func (l *memoryActionLedger) Extend(ctx context.Context, key string, lease time.Duration, nonce string) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	item, ok := l.items[key]
	if !ok {
		return ErrLeaseNotHeld
	}
	if !item.expiresAt.IsZero() && !item.expiresAt.After(now) {
		delete(l.items, key)
		return ErrLeaseNotHeld
	}
	if item.state == ledgerStateCommitted {
		return ErrAlreadyCommitted
	}
	if item.state != ledgerStatePending || (item.nonce != "" && item.nonce != nonce) {
		return ErrLeaseNotHeld
	}
	item.expiresAt = now.Add(lease)
	l.items[key] = item
	return nil
}

func (l *memoryActionLedger) Invalidate(ctx context.Context, key string) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.items, key)
	return nil
}

func (l *memoryActionLedger) InvalidateOwned(ctx context.Context, key, decisionID string) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	item, ok := l.items[key]
	if !ok || item.state != ledgerStateCommitted || decisionIDFromRecord(item.value) != decisionID {
		return nil
	}
	delete(l.items, key)
	return nil
}

func (l *memoryActionLedger) SweepExpired(ctx context.Context) (int64, error) {
	select {
	case <-ctx.Done():
		return 0, ctx.Err()
	default:
	}
	now := time.Now()
	var removed int64
	l.mu.Lock()
	defer l.mu.Unlock()
	for key, item := range l.items {
		if !item.expiresAt.IsZero() && !item.expiresAt.After(now) {
			delete(l.items, key)
			removed++
		}
	}
	return removed, nil
}
