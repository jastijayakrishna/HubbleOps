package terraform

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/preflight"
)

func kindsOfTerraform(findings []preflight.Finding) map[string]int {
	out := map[string]int{}
	for _, finding := range findings {
		out[finding.Kind]++
	}
	return out
}

func TestScanFlagsProtectedTerraformDestroy(t *testing.T) {
	f, err := os.Open("testdata/datatalks_destroy_plan.json")
	if err != nil {
		t.Fatalf("open fixture: %v", err)
	}
	defer f.Close()

	findings, err := Scan(f, Options{ProtectedResources: []string{"aws_s3_bucket.audit_logs_prod"}})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 {
		t.Fatalf("findings=%d want 1: %+v", len(findings), findings)
	}
	got := findings[0]
	if got.Kind != preflight.KindTerraformDelete || got.Action != "terraform.destroy" {
		t.Fatalf("finding kind/action=%s/%s", got.Kind, got.Action)
	}
	if got.RiskScore != 95 || got.RiskClass != "critical" {
		t.Fatalf("risk=%d/%s", got.RiskScore, got.RiskClass)
	}
	if strings.Contains(strings.Join(got.Evidence, " "), "before") {
		t.Fatalf("evidence leaked plan state: %v", got.Evidence)
	}
}

func TestScanFlagsTerraformReplace(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_db_instance.main","type":"aws_db_instance","change":{"actions":["delete","create"]}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformReplace || findings[0].Action != "terraform.replace" {
		t.Fatalf("findings=%+v", findings)
	}
	if findings[0].RiskScore < 90 {
		t.Fatalf("stateful replace must score like destroy: %+v", findings)
	}
}

func TestScanMalformedTerraformJSONReturnsCleanError(t *testing.T) {
	_, err := Scan(strings.NewReader(`{"resource_changes":[`), Options{})
	if err == nil {
		t.Fatalf("expected malformed terraform JSON error")
	}
	if !strings.Contains(err.Error(), "parse terraform plan json") {
		t.Fatalf("error=%v", err)
	}
}

// Deleting a harmless, stateless resource must NOT be treated as a destructive change
// (the false positive that gets a gate disabled).
func TestScanHarmlessResourceDeleteIsNotFlagged(t *testing.T) {
	for _, typ := range []string{"null_resource", "random_id", "terraform_data", "local_file", "tls_private_key"} {
		plan := `{"resource_changes":[{"address":"` + typ + `.x","type":"` + typ + `","change":{"actions":["delete"]}}]}`
		findings, err := Scan(strings.NewReader(plan), Options{})
		if err != nil {
			t.Fatalf("scan %s: %v", typ, err)
		}
		if len(findings) != 0 {
			t.Fatalf("harmless %s delete flagged: %+v", typ, findings)
		}
	}
}

func TestScanStatefulDeleteIsHighRisk(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_db_instance.prod","type":"aws_db_instance","change":{"actions":["delete"]}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformDelete || findings[0].RiskScore < 90 {
		t.Fatalf("stateful delete not high risk: %+v", findings)
	}
}

// The audit's flagship false negative: an in-place update that disables deletion
// protection on a prod database. Real plans carry before/after; this must now block.
func TestScanCatchesDeletionProtectionDisable(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_db_instance.prod","type":"aws_db_instance","change":{"actions":["update"],"before":{"deletion_protection":true},"after":{"deletion_protection":false}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformUpdate || findings[0].RiskScore < 90 {
		t.Fatalf("deletion_protection disable not caught: %+v", findings)
	}
	if strings.Contains(strings.Join(findings[0].Evidence, " "), "before") {
		t.Fatalf("evidence leaked plan state: %v", findings[0].Evidence)
	}
}

func TestScanCatchesStorageShrink(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_db_instance.prod","type":"aws_db_instance","change":{"actions":["update"],"before":{"allocated_storage":1000},"after":{"allocated_storage":100}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformUpdate || findings[0].RiskScore < 70 {
		t.Fatalf("storage shrink not caught: %+v", findings)
	}
}

func TestScanCatchesGuardedAfterUnknown(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_db_instance.prod","type":"aws_db_instance","change":{"actions":["update"],"before":{"allocated_storage":1000,"deletion_protection":true},"after":{"tags":{"team":"db"}},"after_unknown":{"allocated_storage":true}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformUpdate || findings[0].RiskScore < 70 {
		t.Fatalf("guarded after_unknown not escalated: %+v", findings)
	}
	if !strings.Contains(strings.Join(findings[0].Evidence, " "), "risk_unknown=true") {
		t.Fatalf("missing risk_unknown evidence: %v", findings[0].Evidence)
	}
}

func TestScanCatchesSecurityGroupPublicIngress(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_security_group.ssh","type":"aws_security_group","change":{"actions":["create"],"after":{"ingress":[{"protocol":"tcp","from_port":22,"to_port":22,"cidr_blocks":["0.0.0.0/0"]}]}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformUpdate || findings[0].RiskScore < 90 {
		t.Fatalf("public SSH ingress not caught: %+v", findings)
	}
	if !strings.Contains(strings.Join(findings[0].Evidence, " "), "public_ingress=true") {
		t.Fatalf("evidence missing public ingress marker: %v", findings[0].Evidence)
	}
}

func TestScanPrivateSecurityGroupIngressIsSafe(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_security_group.internal","type":"aws_security_group","change":{"actions":["create"],"after":{"ingress":[{"protocol":"tcp","from_port":5432,"to_port":5432,"cidr_blocks":["10.0.0.0/8"]}]}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 0 {
		t.Fatalf("private ingress should be safe: %+v", findings)
	}
}

func TestScanCatchesIAMWildcardPolicy(t *testing.T) {
	policy := `{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"*","Resource":"*"}]}`
	plan := `{"resource_changes":[{"address":"aws_iam_policy.admin","type":"aws_iam_policy","change":{"actions":["create"],"after":{"policy":` + strconv.Quote(policy) + `}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].RiskScore < 90 {
		t.Fatalf("wildcard IAM policy not caught: %+v", findings)
	}
	evidence := strings.Join(findings[0].Evidence, " ")
	if !strings.Contains(evidence, "iam_wildcard_policy=true") {
		t.Fatalf("evidence missing IAM marker: %v", findings[0].Evidence)
	}
	if strings.Contains(evidence, "Action") || strings.Contains(evidence, "Resource") {
		t.Fatalf("evidence leaked raw policy: %v", findings[0].Evidence)
	}
}

func TestScanCatchesS3PublicAccessAndVersioningRollback(t *testing.T) {
	plan := `{"resource_changes":[` +
		`{"address":"aws_s3_bucket_public_access_block.logs","type":"aws_s3_bucket_public_access_block","change":{"actions":["update"],"before":{"block_public_acls":true,"block_public_policy":true,"ignore_public_acls":true,"restrict_public_buckets":true},"after":{"block_public_acls":false,"block_public_policy":true,"ignore_public_acls":true,"restrict_public_buckets":true}}},` +
		`{"address":"aws_s3_bucket_versioning.logs","type":"aws_s3_bucket_versioning","change":{"actions":["update"],"before":{"versioning_configuration":[{"status":"Enabled"}]},"after":{"versioning_configuration":[{"status":"Suspended"}]}}}` +
		`]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 2 {
		t.Fatalf("expected S3 public access and versioning findings: %+v", findings)
	}
	evidence := strings.Join(append(findings[0].Evidence, findings[1].Evidence...), " ")
	if !strings.Contains(evidence, "s3_public_access=true") || !strings.Contains(evidence, "s3_versioning_disabled=true") {
		t.Fatalf("missing S3 evidence markers: %+v", findings)
	}
}

func TestScanKMSKeyDeleteIsHighRisk(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_kms_key.prod","type":"aws_kms_key","change":{"actions":["delete"]}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 1 || findings[0].Kind != preflight.KindTerraformDelete || findings[0].RiskScore < 90 {
		t.Fatalf("KMS key delete not high risk: %+v", findings)
	}
}

func TestScanCatchesMassDestroy(t *testing.T) {
	var b strings.Builder
	b.WriteString(`{"resource_changes":[`)
	for i := 0; i < 10; i++ {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, `{"address":"aws_instance.node_%d","type":"aws_instance","change":{"actions":["delete"]}}`, i)
	}
	b.WriteString(`]}`)
	findings, err := Scan(strings.NewReader(b.String()), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if kindsOfTerraform(findings)[preflight.KindTerraformDelete] != 11 {
		t.Fatalf("expected per-resource plus mass destroy findings: %+v", findings)
	}
	last := findings[len(findings)-1]
	if last.Target != "terraform_plan" || !strings.Contains(strings.Join(last.Evidence, " "), "mass_destroy_count=10") {
		t.Fatalf("missing mass destroy finding: %+v", findings)
	}
}

func TestScanBenignUpdateIsSafe(t *testing.T) {
	plan := `{"resource_changes":[{"address":"aws_db_instance.prod","type":"aws_db_instance","change":{"actions":["update"],"before":{"tags":{"team":"a"},"allocated_storage":100},"after":{"tags":{"team":"b"},"allocated_storage":200}}}]}`
	findings, err := Scan(strings.NewReader(plan), Options{})
	if err != nil {
		t.Fatalf("scan: %v", err)
	}
	if len(findings) != 0 {
		t.Fatalf("benign update (tag change + storage grow) flagged: %+v", findings)
	}
}
