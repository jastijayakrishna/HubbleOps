package policy

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"

	"gopkg.in/yaml.v3"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
)

// preconditionKeywords are entries in a rule's `require:` list that name a CONDITION the
// action must satisfy (evidence must be present), not a human approver. They are enforced
// as preconditions instead of being flattened into required_approvers.
var preconditionKeywords = map[string]bool{
	"linked_ticket": true,
	"rollback_plan": true,
	"oncall_ack":    true,
	"backup":        true,
	"change_ticket": true,
}

type Policy struct {
	Version            string                   `yaml:"version"`
	ProtectedResources []string                 `yaml:"protected_resources"`
	Services           map[string]ServiceConfig `yaml:"services"`
	Rules              []Rule                   `yaml:"rules"`
	Warnings           []string                 `yaml:"-"`
}

type ServiceConfig struct {
	Risk              string   `yaml:"risk"`
	Owners            []string `yaml:"owners"`
	RequiredApprovers []string `yaml:"required_approvers"`
}

type Rule struct {
	ID                 string     `yaml:"id"`
	If                 Conditions `yaml:"if"`
	Decision           string     `yaml:"decision"`
	Reason             string     `yaml:"reason"`
	RiskScore          int        `yaml:"risk_score"`
	RequiredApprovers  []string   `yaml:"required_approvers"`
	Require            []string   `yaml:"require"`
	AllowedNextActions []string   `yaml:"allowed_next_actions"`
}

type Conditions struct {
	Action            string   `yaml:"action"`
	Environment       string   `yaml:"environment"`
	Env               string   `yaml:"env"`
	TouchesAny        []string `yaml:"touches_any"`
	MigrationContains []string `yaml:"migration_contains"`
	ServiceRisk       string   `yaml:"service_risk"`
}

func Load(path string) (*Policy, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("read policy: %w", err)
	}
	defer f.Close()
	var p Policy
	dec := yaml.NewDecoder(f)
	dec.KnownFields(true)
	if err := dec.Decode(&p); err != nil {
		return nil, fmt.Errorf("parse policy %s: %w", path, err)
	}
	if strings.TrimSpace(p.Version) == "" {
		p.Version = action.PolicyVersion
	}
	problems, warnings := validate(&p)
	p.Warnings = warnings
	if len(problems) > 0 {
		return nil, ValidationError{Path: path, Problems: problems}
	}
	return &p, nil
}

type ValidationError struct {
	Path     string
	Problems []string
}

func (e ValidationError) Error() string {
	prefix := "validate policy"
	if strings.TrimSpace(e.Path) != "" {
		prefix += " " + e.Path
	}
	return prefix + ": " + strings.Join(e.Problems, "; ")
}

func validate(p *Policy) ([]string, []string) {
	if p == nil {
		return nil, nil
	}
	var problems []string
	var warnings []string
	ids := map[string]int{}
	for i, rule := range p.Rules {
		rulePath := fmt.Sprintf("rules[%d]", i)
		id := strings.TrimSpace(rule.ID)
		if id == "" {
			problems = append(problems, rulePath+".id is required")
		} else if first, ok := ids[id]; ok {
			problems = append(problems, fmt.Sprintf("%s.id %q is duplicate of rules[%d]", rulePath, id, first))
		} else {
			ids[id] = i
		}

		decision, ok := normalizePolicyDecision(rule.Decision)
		if !ok {
			problems = append(problems, fmt.Sprintf("%s.decision %q is invalid; use allow, require_approval, or block", rulePath, strings.TrimSpace(rule.Decision)))
		}
		if rule.RiskScore < 0 || rule.RiskScore > 100 {
			problems = append(problems, fmt.Sprintf("%s.risk_score must be between 0 and 100", rulePath))
		}
		if !hasAnyCondition(rule.If) && id != "default" && decision != action.DecisionAllow {
			problems = append(problems, rulePath+".if must contain at least one condition, unless id is default or decision is allow")
		}
		for j, entry := range rule.Require {
			key := strings.TrimSpace(entry)
			if key == "" || preconditionKeywords[strings.ToLower(key)] || isRoleLike(key) {
				continue
			}
			warnings = append(warnings, fmt.Sprintf("%s.require[%d] %q is not a known precondition and does not look role-like", rulePath, j, key))
		}
	}
	warnings = append(warnings, shadowWarnings(p.Rules)...)
	return problems, warnings
}

func normalizePolicyDecision(value string) (string, bool) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case action.DecisionAllow:
		return action.DecisionAllow, true
	case action.DecisionBlock:
		return action.DecisionBlock, true
	case "approval", "require", "requires_approval", action.DecisionRequireApproval:
		return action.DecisionRequireApproval, true
	default:
		return "", false
	}
}

func hasAnyCondition(cond Conditions) bool {
	return strings.TrimSpace(cond.Action) != "" ||
		strings.TrimSpace(cond.Environment) != "" ||
		strings.TrimSpace(cond.Env) != "" ||
		len(cond.TouchesAny) > 0 ||
		len(cond.MigrationContains) > 0 ||
		strings.TrimSpace(cond.ServiceRisk) != ""
}

func isRoleLike(value string) bool {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return false
	}
	if strings.ContainsAny(value, "-_/:@") {
		return true
	}
	switch value {
	case "owner", "owners", "sre", "admin", "approver", "reviewer", "codeowner", "codeowners", "security", "dba", "ops", "oncall":
		return true
	}
	for _, token := range []string{"owner", "approver", "reviewer", "admin", "sre", "security", "ops"} {
		if strings.HasSuffix(value, token) {
			return true
		}
	}
	return false
}

func shadowWarnings(rules []Rule) []string {
	var warnings []string
	for i := 0; i < len(rules); i++ {
		firstDecision, ok := normalizePolicyDecision(rules[i].Decision)
		if !ok || firstDecision != action.DecisionAllow {
			continue
		}
		for j := i + 1; j < len(rules); j++ {
			laterDecision, ok := normalizePolicyDecision(rules[j].Decision)
			if !ok || laterDecision != action.DecisionBlock {
				continue
			}
			if reflect.DeepEqual(rules[i].If, rules[j].If) {
				warnings = append(warnings, fmt.Sprintf("rules[%d] %q allow shadows later block rule rules[%d] %q for an identical if block", i, rules[i].ID, j, rules[j].ID))
			}
		}
	}
	return warnings
}

// IMPORTANT: policy rules are first-match-wins. Reordering rules changes enforcement.
func Evaluate(req action.Request, findings []preflight.Finding, p *Policy) action.Decision {
	version := action.PolicyVersion
	if p != nil && strings.TrimSpace(p.Version) != "" {
		version = p.Version
	}
	base := defaultDecision(req, findings, version)
	if p == nil {
		return base
	}
	base.RequiredApprovers = defaultApprovers(base, req, p)
	for _, rule := range p.Rules {
		if !rule.matches(req, findings) {
			continue
		}
		score := rule.RiskScore
		if score <= 0 {
			score = base.RiskScore
		}
		if score <= 0 {
			score = preflight.HighestRisk(findings)
		}
		decision := action.NormalizeDecision(rule.Decision)
		reason := strings.TrimSpace(rule.Reason)
		if reason == "" {
			reason = "matched policy rule " + rule.ID
		}
		approverRoles, preconditions := splitRequire(rule.Require)
		required := append([]string{}, rule.RequiredApprovers...)
		required = append(required, approverRoles...)

		evidence := preflight.EvidenceCodes(findings)
		allowedNext := append([]string{}, rule.AllowedNextActions...)

		// An unsatisfied precondition escalates an allow to require_approval and names what
		// is missing, instead of the entry silently disappearing into the approver list.
		if missing := unsatisfiedPreconditions(req, preconditions); len(missing) > 0 {
			if decision == action.DecisionAllow {
				decision = action.DecisionRequireApproval
			}
			reason = "missing required precondition(s): " + strings.Join(missing, ", ")
			for _, m := range missing {
				evidence = append(evidence, "missing_precondition="+m)
				allowedNext = append(allowedNext, "provide_"+m)
			}
		}

		if len(required) == 0 && decision != action.DecisionAllow {
			required = serviceApprovers(req.Target, p)
		}
		return action.Decision{
			Decision:           decision,
			Reason:             reason,
			RiskScore:          score,
			RiskClass:          action.RiskClass(score),
			RequiredApprovers:  uniqueStrings(required),
			AllowedNextActions: uniqueStrings(allowedNext),
			PolicyVersion:      version,
			Evidence:           evidence,
			PolicyRuleID:       rule.ID,
			RequiresReceipt:    decision == action.DecisionBlock,
		}
	}
	return base
}

func (p *Policy) Service(name string) (ServiceConfig, bool) {
	if p == nil || len(p.Services) == 0 {
		return ServiceConfig{}, false
	}
	name = normalizeServiceName(name)
	for candidate, cfg := range p.Services {
		if normalizeServiceName(candidate) == name {
			return cfg, true
		}
	}
	return ServiceConfig{}, false
}

func (p *Policy) ServiceRisk(name string) string {
	cfg, ok := p.Service(name)
	if !ok {
		return ""
	}
	return strings.TrimSpace(cfg.Risk)
}

func defaultDecision(req action.Request, findings []preflight.Finding, version string) action.Decision {
	score := preflight.HighestRisk(findings)
	decision := action.DecisionAllow
	reason := "no risky engineering action detected"
	var approvers []string
	switch {
	case score >= 90:
		decision = action.DecisionBlock
		reason = "destructive engineering action detected"
	case score >= 70:
		decision = action.DecisionRequireApproval
		reason = "risky engineering action requires review"
		approvers = []string{"owner"}
	default:
		if strings.TrimSpace(req.Action) != "" {
			reason = "preflight checks passed"
		}
	}
	return action.Decision{
		Decision:           decision,
		Reason:             reason,
		RiskScore:          score,
		RiskClass:          action.RiskClass(score),
		RequiredApprovers:  approvers,
		AllowedNextActions: allowedNextActions(decision),
		PolicyVersion:      version,
		Evidence:           preflight.EvidenceCodes(findings),
		RequiresReceipt:    decision == action.DecisionBlock,
	}
}

func (r Rule) matches(req action.Request, findings []preflight.Finding) bool {
	cond := r.If
	if cond.Action != "" && !actionMatches(cond.Action, req, findings) {
		return false
	}
	env := firstNonEmpty(cond.Environment, cond.Env)
	if env != "" && normalizeEnv(req.Environment) != normalizeEnv(env) {
		return false
	}
	if cond.ServiceRisk != "" && !strings.EqualFold(strings.TrimSpace(req.ServiceRisk), strings.TrimSpace(cond.ServiceRisk)) {
		return false
	}
	if len(cond.TouchesAny) > 0 && !touchesAny(cond.TouchesAny, req, findings) {
		return false
	}
	if len(cond.MigrationContains) > 0 && !migrationContains(cond.MigrationContains, findings) {
		return false
	}
	return true
}

func actionMatches(want string, req action.Request, findings []preflight.Finding) bool {
	want = strings.ToLower(strings.TrimSpace(want))
	if strings.ToLower(strings.TrimSpace(req.Action)) == want {
		return true
	}
	for _, finding := range findings {
		if strings.ToLower(strings.TrimSpace(finding.Action)) == want {
			return true
		}
	}
	return false
}

func touchesAny(patterns []string, req action.Request, findings []preflight.Finding) bool {
	targets := preflight.Targets(findings)
	if req.Target != "" {
		targets = append(targets, req.Target)
	}
	for _, pattern := range patterns {
		pattern = strings.ToLower(strings.TrimSpace(pattern))
		if pattern == "" {
			continue
		}
		for _, target := range targets {
			if pathGlobMatch(pattern, target) {
				return true
			}
		}
	}
	return false
}

func migrationContains(tags []string, findings []preflight.Finding) bool {
	want := map[string]struct{}{}
	for _, tag := range tags {
		tag = strings.ToUpper(strings.TrimSpace(tag))
		if tag != "" {
			want[tag] = struct{}{}
		}
	}
	for _, finding := range findings {
		for _, tag := range finding.ChangeTags {
			_, raw, ok := strings.Cut(tag, ":")
			if !ok {
				raw = tag
			}
			if _, ok := want[strings.ToUpper(strings.TrimSpace(raw))]; ok {
				return true
			}
		}
	}
	return false
}

func allowedNextActions(decision string) []string {
	switch decision {
	case action.DecisionBlock:
		return []string{"open_review"}
	case action.DecisionRequireApproval:
		return []string{"request_approval"}
	default:
		return []string{"continue"}
	}
}

func defaultApprovers(decision action.Decision, req action.Request, p *Policy) []string {
	if decision.Decision != action.DecisionRequireApproval {
		return decision.RequiredApprovers
	}
	if approvers := serviceApprovers(req.Target, p); len(approvers) > 0 && (len(decision.RequiredApprovers) == 0 || isGenericOwner(decision.RequiredApprovers)) {
		return approvers
	}
	return decision.RequiredApprovers
}

func isGenericOwner(values []string) bool {
	return len(values) == 1 && strings.EqualFold(strings.TrimSpace(values[0]), "owner")
}

func serviceApprovers(service string, p *Policy) []string {
	cfg, ok := p.Service(service)
	if !ok {
		return nil
	}
	values := append([]string{}, cfg.RequiredApprovers...)
	values = append(values, cfg.Owners...)
	return uniqueStrings(values)
}

func normalizeServiceName(name string) string {
	return strings.ToLower(strings.TrimSpace(name))
}

func uniqueStrings(values []string) []string {
	seen := map[string]struct{}{}
	var out []string
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

func normalizeEnv(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "prod":
		return "production"
	case "dev":
		return "development"
	default:
		return strings.ToLower(strings.TrimSpace(value))
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

// splitRequire separates a rule's `require:` entries into human approver roles and
// preconditions (known condition keywords). Unknown entries are treated as approver roles,
// preserving backwards compatibility.
func splitRequire(require []string) (roles, preconditions []string) {
	for _, entry := range require {
		key := strings.ToLower(strings.TrimSpace(entry))
		if key == "" {
			continue
		}
		if preconditionKeywords[key] {
			preconditions = append(preconditions, key)
		} else {
			roles = append(roles, strings.TrimSpace(entry))
		}
	}
	return roles, preconditions
}

func unsatisfiedPreconditions(req action.Request, preconditions []string) []string {
	var missing []string
	for _, name := range preconditions {
		if !satisfiesPrecondition(req, name) {
			missing = append(missing, name)
		}
	}
	return missing
}

// satisfiesPrecondition reports whether the request carries proof that a precondition is
// met — either an explicit required_context entry or an evidence marker like
// "<name>=present". linked_ticket also accepts the github detector's "github_linked_ticket=present".
func satisfiesPrecondition(req action.Request, name string) bool {
	for _, ctx := range req.RequiredContext {
		if strings.EqualFold(strings.TrimSpace(ctx), name) {
			return true
		}
	}
	markers := map[string]bool{
		name + "=present":   true,
		name + "=satisfied": true,
		name + "=ack":       true,
		name + "=true":      true,
	}
	if name == "linked_ticket" {
		markers["github_linked_ticket=present"] = true
	}
	for _, ev := range req.Evidence {
		if markers[strings.ToLower(strings.TrimSpace(ev))] {
			return true
		}
	}
	return false
}

// pathGlobMatch matches a touches_any pattern against a target path or resource address.
// It supports `**` as a full path segment (bare `**` matches everything), filepath globs
// (`*`, `?`, `[...]`), and bare literals that match exactly or as a path-segment prefix -
// so "billing" matches "billing/x" but never "rebilling".
func pathGlobMatch(pattern, target string) bool {
	pattern = normalizeGlobPath(pattern)
	target = normalizeGlobPath(target)
	if pattern == "" {
		return false
	}
	if pattern == "**" {
		return true
	}
	if target == "" {
		return false
	}
	if pattern == target {
		return true
	}
	if strings.Contains(pattern, "**") {
		if matched, handled := doubleStarMatch(pattern, target); handled {
			return matched
		}
	}
	if strings.ContainsAny(pattern, "*?[") {
		if ok, _ := filepath.Match(pattern, target); ok {
			return true
		}
		ok, _ := filepath.Match(pattern, filepath.Base(target))
		return ok
	}
	return strings.HasPrefix(target, pattern+"/")
}

func normalizeGlobPath(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = filepath.ToSlash(value)
	return strings.Trim(value, "/")
}

func doubleStarMatch(pattern, target string) (bool, bool) {
	parts := strings.Split(pattern, "/")
	star := -1
	for i, part := range parts {
		if part == "**" {
			if star >= 0 {
				return false, false
			}
			star = i
			continue
		}
		if strings.Contains(part, "**") {
			return false, false
		}
	}
	if star < 0 {
		return false, false
	}

	prefix := strings.Join(parts[:star], "/")
	suffix := strings.Join(parts[star+1:], "/")
	switch {
	case prefix == "" && suffix == "":
		return true, true
	case prefix != "" && suffix == "":
		return hasPathPrefix(target, prefix), true
	case prefix == "" && suffix != "":
		return hasPathSuffix(target, suffix), true
	default:
		return hasPathPrefix(target, prefix) &&
			hasPathSuffix(target, suffix) &&
			len(target) >= len(prefix)+1+len(suffix), true
	}
}

func hasPathPrefix(target, prefix string) bool {
	return target == prefix || strings.HasPrefix(target, prefix+"/")
}

func hasPathSuffix(target, suffix string) bool {
	return target == suffix || strings.HasSuffix(target, "/"+suffix)
}
