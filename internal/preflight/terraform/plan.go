// Package terraform analyzes `terraform show -json` plan output for destructive changes.
//
// It is resource-aware, not action-flag-only: deleting a stateless helper resource
// (null_resource, random_*, terraform_data) is not treated like destroying a stateful data
// store, and in-place updates are inspected for the changes that actually cause incidents —
// disabling deletion_protection, shrinking storage, enabling force_destroy/skip_final_snapshot.
package terraform

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
)

type Options struct {
	ProtectedResources []string
}

type plan struct {
	ResourceChanges []resourceChange `json:"resource_changes"`
}

type resourceChange struct {
	Address string `json:"address"`
	Type    string `json:"type"`
	Name    string `json:"name"`
	Change  change `json:"change"`
}

type change struct {
	Actions      []string       `json:"actions"`
	Before       map[string]any `json:"before"`
	After        map[string]any `json:"after"`
	AfterUnknown map[string]any `json:"after_unknown"`
}

func Scan(r io.Reader, opts Options) ([]preflight.Finding, error) {
	var p plan
	if err := json.NewDecoder(r).Decode(&p); err != nil {
		return nil, fmt.Errorf("parse terraform plan json: %w", err)
	}

	protected := make([]string, 0, len(opts.ProtectedResources))
	for _, item := range opts.ProtectedResources {
		if item = strings.TrimSpace(strings.ToLower(item)); item != "" {
			protected = append(protected, item)
		}
	}

	var findings []preflight.Finding
	for _, rc := range p.ResourceChanges {
		findings = append(findings, classifyResourceChange(rc, protected)...)
	}
	if count := massDestroyCount(p.ResourceChanges, protected); count >= 10 {
		findings = append(findings, newPlanFinding(preflight.KindTerraformDelete, "terraform.destroy", "delete", 95, []string{
			fmt.Sprintf("mass_destroy_count=%d", count),
		}))
	}
	return findings, nil
}

func classifyResourceChange(rc resourceChange, protected []string) []preflight.Finding {
	hasDelete, hasCreate, hasUpdate := false, false, false
	for _, a := range rc.Change.Actions {
		switch strings.ToLower(strings.TrimSpace(a)) {
		case "delete":
			hasDelete = true
		case "create":
			hasCreate = true
		case "update":
			hasUpdate = true
		}
	}
	isProtected := matchesProtected(rc.Address, rc.Type, protected)
	danger := resourceDanger(rc.Type)
	tfAction := changeActionLabel(hasDelete, hasCreate, hasUpdate)
	var findings []preflight.Finding

	switch {
	case hasDelete && hasCreate: // replace (destroy + recreate)
		if danger != dangerHarmless || isProtected {
			risk := pickRisk(isProtected, danger, 90, 70)
			findings = append(findings, newFinding(preflight.KindTerraformReplace, "terraform.replace", "replace", rc, risk, isProtected, nil))
		}
	case hasDelete: // destroy
		if danger != dangerHarmless || isProtected {
			risk := pickRisk(isProtected, danger, 90, 70)
			findings = append(findings, newFinding(preflight.KindTerraformDelete, "terraform.destroy", "delete", rc, risk, isProtected, nil))
		}
	}
	if hasUpdate {
		findings = append(findings, inspectUpdate(rc, isProtected)...)
	}
	if hasCreate || hasUpdate || (hasDelete && hasCreate) {
		findings = append(findings, inspectRiskyAttributes(rc, isProtected, tfAction)...)
	}
	return findings
}

// inspectUpdate emits a finding only for in-place changes that are actually destructive.
func inspectUpdate(rc resourceChange, isProtected bool) []preflight.Finding {
	before, after := rc.Change.Before, rc.Change.After
	var findings []preflight.Finding
	add := func(tag string, risk int) {
		if isProtected && risk < 95 {
			risk = 95
		}
		findings = append(findings, newFinding(preflight.KindTerraformUpdate, "terraform.update", "update", rc, risk, isProtected, []string{tag}))
	}

	for _, key := range guardedUnknownAttributes(rc.Type) {
		if unknownValue(rc.Change.AfterUnknown, key) {
			add("risk_unknown=true", 85)
			break
		}
	}
	for _, key := range []string{"deletion_protection", "deletion_protection_enabled", "enable_deletion_protection"} {
		if wasTrue(before, key) && isFalse(after, key) {
			add("deletion_protection_disabled=true", 90)
		}
	}
	if wasFalse(before, "force_destroy") && isTrue(after, "force_destroy") {
		add("force_destroy_enabled=true", 80)
	}
	if wasFalse(before, "skip_final_snapshot") && isTrue(after, "skip_final_snapshot") {
		add("skip_final_snapshot_enabled=true", 82)
	}
	for _, key := range []string{"allocated_storage", "size"} {
		if b, ok := numVal(before, key); ok {
			if a, ok := numVal(after, key); ok && a < b {
				add("storage_shrink=true", 85)
			}
		}
	}
	return findings
}

func guardedUnknownAttributes(resourceType string) []string {
	common := []string{
		"allocated_storage",
		"size",
		"deletion_protection",
		"deletion_protection_enabled",
		"enable_deletion_protection",
		"force_destroy",
		"skip_final_snapshot",
	}
	switch strings.ToLower(strings.TrimSpace(resourceType)) {
	case "aws_security_group", "aws_security_group_rule", "aws_vpc_security_group_ingress_rule":
		return append(common, "ingress", "cidr_blocks", "ipv6_cidr_blocks", "cidr_ipv4", "cidr_ipv6", "from_port", "to_port", "protocol", "ip_protocol")
	case "aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy":
		return append(common, "policy")
	case "aws_s3_bucket_public_access_block":
		return append(common, "block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")
	case "aws_s3_bucket_acl":
		return append(common, "acl")
	case "aws_s3_bucket_policy":
		return append(common, "policy")
	case "aws_s3_bucket_versioning":
		return append(common, "versioning_configuration", "versioning", "status", "enabled")
	default:
		return common
	}
}

func unknownValue(m map[string]any, key string) bool {
	if len(m) == 0 {
		return false
	}
	for k, value := range m {
		if strings.EqualFold(strings.TrimSpace(k), key) && unknownTruthy(value) {
			return true
		}
		if nested, ok := value.(map[string]any); ok && unknownValue(nested, key) {
			return true
		}
		for _, item := range valuesFromList(value) {
			if nested, ok := item.(map[string]any); ok && unknownValue(nested, key) {
				return true
			}
		}
	}
	return false
}

func unknownTruthy(value any) bool {
	switch v := value.(type) {
	case bool:
		return v
	case map[string]any:
		for _, item := range v {
			if unknownTruthy(item) {
				return true
			}
		}
	case []any:
		for _, item := range v {
			if unknownTruthy(item) {
				return true
			}
		}
	case []map[string]any:
		for _, item := range v {
			if unknownTruthy(item) {
				return true
			}
		}
	}
	return false
}

func valuesFromList(value any) []any {
	switch v := value.(type) {
	case []any:
		return v
	case []map[string]any:
		out := make([]any, 0, len(v))
		for _, item := range v {
			out = append(out, item)
		}
		return out
	default:
		return nil
	}
}

func inspectRiskyAttributes(rc resourceChange, isProtected bool, tfAction string) []preflight.Finding {
	after := rc.Change.After
	if len(after) == 0 {
		return nil
	}
	typ := strings.ToLower(strings.TrimSpace(rc.Type))
	var findings []preflight.Finding
	add := func(tag string, risk int) {
		if isProtected && risk < 95 {
			risk = 95
		}
		findings = append(findings, newFinding(preflight.KindTerraformUpdate, "terraform.update", tfAction, rc, risk, isProtected, []string{tag}))
	}

	switch typ {
	case "aws_security_group":
		if risk, ok := securityGroupPublicIngressRisk(after); ok {
			add("public_ingress=true", risk)
		}
	case "aws_security_group_rule", "aws_vpc_security_group_ingress_rule":
		if risk, ok := securityRulePublicIngressRisk(typ, after); ok {
			add("public_ingress=true", risk)
		}
	case "aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy":
		if hasWildcardIAMPolicy(after["policy"]) {
			add("iam_wildcard_policy=true", 90)
		}
	case "aws_s3_bucket_public_access_block":
		if disablesS3PublicAccessBlock(after) {
			add("s3_public_access=true", 88)
		}
	case "aws_s3_bucket_acl":
		if hasPublicS3ACL(after) {
			add("s3_public_access=true", 86)
		}
	case "aws_s3_bucket_policy":
		if policyAllowsPublicPrincipal(after["policy"]) {
			add("s3_public_access=true", 88)
		}
	case "aws_s3_bucket_versioning":
		if s3VersioningDisabled(rc.Change.Before, after) {
			add("s3_versioning_disabled=true", 82)
		}
	}
	return findings
}

func changeActionLabel(hasDelete, hasCreate, hasUpdate bool) string {
	switch {
	case hasDelete && hasCreate:
		return "replace"
	case hasDelete:
		return "delete"
	case hasCreate:
		return "create"
	case hasUpdate:
		return "update"
	default:
		return "no-op"
	}
}

func newFinding(kind, actionName, tfAction string, rc resourceChange, risk int, isProtected bool, extra []string) preflight.Finding {
	evidence := []string{
		"source=terraform",
		"terraform_action=" + tfAction,
	}
	if rc.Type != "" {
		evidence = append(evidence, "resource_type="+safeLabel(rc.Type))
	}
	if isProtected {
		evidence = append(evidence, "protected_resource=true")
	}
	tags := []string{"terraform:" + tfAction, "resource:" + safeLabel(rc.Type)}
	for _, item := range extra {
		evidence = append(evidence, item)
		if key, _, ok := strings.Cut(item, "="); ok {
			tags = append(tags, "terraform:"+key)
		}
	}
	return preflight.Finding{
		Source:     preflight.SourceTerraform,
		Kind:       kind,
		Action:     actionName,
		Target:     strings.TrimSpace(rc.Address),
		RiskScore:  risk,
		RiskClass:  action.RiskClass(risk),
		Evidence:   evidence,
		ChangeTags: tags,
	}
}

func newPlanFinding(kind, actionName, tfAction string, risk int, extra []string) preflight.Finding {
	evidence := []string{
		"source=terraform",
		"terraform_action=" + tfAction,
	}
	evidence = append(evidence, extra...)
	tags := []string{"terraform:" + tfAction}
	for _, item := range extra {
		if key, _, ok := strings.Cut(item, "="); ok {
			tags = append(tags, "terraform:"+key)
		}
	}
	return preflight.Finding{
		Source:     preflight.SourceTerraform,
		Kind:       kind,
		Action:     actionName,
		Target:     "terraform_plan",
		RiskScore:  risk,
		RiskClass:  action.RiskClass(risk),
		Evidence:   evidence,
		ChangeTags: tags,
	}
}

// pickRisk returns the protected score (95) when protected, otherwise the stateful or
// standard score for the resource's danger class.
func pickRisk(isProtected bool, danger string, stateful, standard int) int {
	if isProtected {
		return 95
	}
	if danger == dangerStateful {
		return stateful
	}
	return standard
}

const (
	dangerHarmless = "harmless"
	dangerStateful = "stateful"
	dangerStandard = "standard"
)

var (
	harmlessExact    = map[string]bool{"null_resource": true, "terraform_data": true}
	harmlessPrefixes = []string{"random_", "tls_", "time_", "local_", "template_", "external_", "archive_"}
	statefulPrefixes = []string{
		"aws_db_", "aws_rds_", "aws_s3_bucket", "aws_dynamodb_table", "aws_ebs_volume",
		"aws_efs_", "aws_elasticache_", "aws_redshift_", "aws_docdb_", "aws_neptune_",
		"aws_kinesis_", "aws_cloudwatch_log_group", "aws_secretsmanager_secret",
		"aws_route53_zone", "aws_glacier_vault", "aws_kms_key", "aws_qldb_", "aws_timestream",
		"google_sql_", "google_storage_bucket", "google_bigquery_", "google_spanner_",
		"azurerm_postgresql", "azurerm_mysql", "azurerm_mariadb", "azurerm_sql",
		"azurerm_storage_account", "azurerm_cosmosdb",
	}
)

func resourceDanger(typ string) string {
	t := strings.ToLower(strings.TrimSpace(typ))
	if harmlessExact[t] {
		return dangerHarmless
	}
	for _, p := range harmlessPrefixes {
		if strings.HasPrefix(t, p) {
			return dangerHarmless
		}
	}
	for _, p := range statefulPrefixes {
		if strings.HasPrefix(t, p) {
			return dangerStateful
		}
	}
	return dangerStandard
}

func matchesProtected(address, resourceType string, protected []string) bool {
	address = strings.ToLower(strings.TrimSpace(address))
	resourceType = strings.ToLower(strings.TrimSpace(resourceType))
	for _, item := range protected {
		if item == address || item == resourceType || strings.Contains(address, item) {
			return true
		}
	}
	return false
}

func massDestroyCount(changes []resourceChange, protected []string) int {
	count := 0
	for _, rc := range changes {
		hasDelete := false
		for _, action := range rc.Change.Actions {
			if strings.EqualFold(strings.TrimSpace(action), "delete") {
				hasDelete = true
				break
			}
		}
		if !hasDelete {
			continue
		}
		if resourceDanger(rc.Type) == dangerHarmless && !matchesProtected(rc.Address, rc.Type, protected) {
			continue
		}
		count++
	}
	return count
}

func securityGroupPublicIngressRisk(after map[string]any) (int, bool) {
	risk := 0
	for _, rule := range mapsFromValue(after["ingress"]) {
		if r, ok := publicIngressRuleRisk(rule); ok && r > risk {
			risk = r
		}
	}
	return risk, risk > 0
}

func securityRulePublicIngressRisk(resourceType string, after map[string]any) (int, bool) {
	if resourceType == "aws_security_group_rule" {
		if typ, ok := stringVal(after, "type"); ok && !strings.EqualFold(typ, "ingress") {
			return 0, false
		}
	}
	return publicIngressRuleRisk(after)
}

func publicIngressRuleRisk(rule map[string]any) (int, bool) {
	if !hasPublicCIDR(rule) {
		return 0, false
	}
	protocol := firstStringVal(rule, "protocol", "ip_protocol")
	from, hasFrom := numVal(rule, "from_port")
	to, hasTo := numVal(rule, "to_port")
	switch {
	case protocol == "-1" || strings.EqualFold(protocol, "all"):
		return 92, true
	case hasFrom && hasTo && from <= 0 && to >= 65535:
		return 92, true
	case portRangeIncludes(from, to, hasFrom && hasTo, 22), portRangeIncludes(from, to, hasFrom && hasTo, 3389):
		return 90, true
	default:
		return 72, true
	}
}

func hasPublicCIDR(rule map[string]any) bool {
	for _, key := range []string{"cidr_blocks", "ipv6_cidr_blocks", "cidr_ipv4", "cidr_ipv6"} {
		for _, value := range stringValues(rule[key]) {
			switch strings.TrimSpace(value) {
			case "0.0.0.0/0", "::/0":
				return true
			}
		}
	}
	return false
}

func portRangeIncludes(from, to float64, ok bool, port float64) bool {
	return ok && from <= port && to >= port
}

func hasWildcardIAMPolicy(policy any) bool {
	doc, ok := policyDocument(policy)
	if !ok {
		return false
	}
	for _, statement := range policyStatements(doc) {
		if effect, ok := stringValue(mapValueCI(statement, "Effect")); !ok || !strings.EqualFold(effect, "Allow") {
			continue
		}
		if containsWildcard(mapValueCI(statement, "Action")) || containsWildcard(mapValueCI(statement, "Resource")) {
			return true
		}
	}
	return false
}

func policyAllowsPublicPrincipal(policy any) bool {
	doc, ok := policyDocument(policy)
	if !ok {
		return false
	}
	for _, statement := range policyStatements(doc) {
		if effect, ok := stringValue(mapValueCI(statement, "Effect")); !ok || !strings.EqualFold(effect, "Allow") {
			continue
		}
		if containsWildcard(mapValueCI(statement, "Principal")) {
			return true
		}
	}
	return false
}

func policyDocument(policy any) (map[string]any, bool) {
	switch v := policy.(type) {
	case map[string]any:
		return v, true
	case string:
		var parsed any
		if err := json.Unmarshal([]byte(v), &parsed); err != nil {
			return nil, false
		}
		doc, ok := parsed.(map[string]any)
		return doc, ok
	default:
		return nil, false
	}
}

func policyStatements(doc map[string]any) []map[string]any {
	value := mapValueCI(doc, "Statement")
	var out []map[string]any
	switch statements := value.(type) {
	case []any:
		for _, item := range statements {
			if statement, ok := item.(map[string]any); ok {
				out = append(out, statement)
			}
		}
	case map[string]any:
		out = append(out, statements)
	}
	return out
}

func containsWildcard(value any) bool {
	switch v := value.(type) {
	case string:
		return strings.TrimSpace(v) == "*"
	case []any:
		for _, item := range v {
			if containsWildcard(item) {
				return true
			}
		}
	case map[string]any:
		for _, item := range v {
			if containsWildcard(item) {
				return true
			}
		}
	}
	return false
}

func disablesS3PublicAccessBlock(after map[string]any) bool {
	for _, key := range []string{"block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"} {
		if isFalse(after, key) {
			return true
		}
	}
	return false
}

func hasPublicS3ACL(after map[string]any) bool {
	acl, ok := stringVal(after, "acl")
	if !ok {
		return false
	}
	switch strings.ToLower(strings.TrimSpace(acl)) {
	case "public-read", "public-read-write", "authenticated-read":
		return true
	default:
		return false
	}
}

func s3VersioningDisabled(before, after map[string]any) bool {
	beforeStatus, beforeOK := s3VersioningStatus(before)
	afterStatus, afterOK := s3VersioningStatus(after)
	return beforeOK && afterOK &&
		strings.EqualFold(beforeStatus, "enabled") &&
		!strings.EqualFold(afterStatus, "enabled")
}

func s3VersioningStatus(values map[string]any) (string, bool) {
	if values == nil {
		return "", false
	}
	if status, ok := stringVal(values, "status"); ok {
		return status, true
	}
	for _, key := range []string{"versioning_configuration", "versioning"} {
		for _, item := range mapsFromValue(values[key]) {
			if status, ok := stringVal(item, "status"); ok {
				return status, true
			}
			if enabled, ok := boolVal(item, "enabled"); ok {
				if enabled {
					return "enabled", true
				}
				return "disabled", true
			}
		}
	}
	return "", false
}

func wasTrue(m map[string]any, key string) bool  { v, ok := boolVal(m, key); return ok && v }
func wasFalse(m map[string]any, key string) bool { v, ok := boolVal(m, key); return ok && !v }
func isTrue(m map[string]any, key string) bool   { v, ok := boolVal(m, key); return ok && v }
func isFalse(m map[string]any, key string) bool  { v, ok := boolVal(m, key); return ok && !v }

func stringVal(m map[string]any, key string) (string, bool) {
	if m == nil {
		return "", false
	}
	return stringValue(m[key])
}

func firstStringVal(m map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := stringVal(m, key); ok {
			return strings.ToLower(strings.TrimSpace(value))
		}
	}
	return ""
}

func stringValue(value any) (string, bool) {
	v, ok := value.(string)
	if !ok {
		return "", false
	}
	return v, strings.TrimSpace(v) != ""
}

func stringValues(value any) []string {
	switch v := value.(type) {
	case string:
		if strings.TrimSpace(v) == "" {
			return nil
		}
		return []string{v}
	case []any:
		out := make([]string, 0, len(v))
		for _, item := range v {
			if s, ok := stringValue(item); ok {
				out = append(out, s)
			}
		}
		return out
	case []string:
		return v
	default:
		return nil
	}
}

func mapsFromValue(value any) []map[string]any {
	switch v := value.(type) {
	case map[string]any:
		return []map[string]any{v}
	case []any:
		out := make([]map[string]any, 0, len(v))
		for _, item := range v {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
		return out
	case []map[string]any:
		return v
	default:
		return nil
	}
}

func mapValueCI(m map[string]any, key string) any {
	for k, value := range m {
		if strings.EqualFold(k, key) {
			return value
		}
	}
	return nil
}

func boolVal(m map[string]any, key string) (bool, bool) {
	if m == nil {
		return false, false
	}
	v, ok := m[key].(bool)
	return v, ok
}

func numVal(m map[string]any, key string) (float64, bool) {
	if m == nil {
		return 0, false
	}
	switch n := m[key].(type) {
	case float64:
		return n, true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}

func safeLabel(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	var b strings.Builder
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '_' || r == '-' || r == '.' {
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		return "unknown"
	}
	return b.String()
}
