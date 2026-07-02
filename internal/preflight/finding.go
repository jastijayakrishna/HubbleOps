package preflight

import "strings"

const (
	SourceTerraform = "terraform"
	SourceMigration = "migration"
	SourceDeploy    = "deploy"
	SourceGitHub    = "github"

	KindTerraformDelete        = "terraform_delete"
	KindTerraformReplace       = "terraform_replace"
	KindTerraformUpdate        = "terraform_update"
	KindTerraformPlanMissing   = "terraform_plan_missing"
	KindMigrationDrop          = "migration_drop"
	KindMigrationAlter         = "migration_alter"
	KindMigrationTruncate      = "migration_truncate"
	KindMigrationDeleteNoWhere = "migration_delete_no_where"
	KindMigrationUpdateNoWhere = "migration_update_no_where"
	KindMigrationIndexLock     = "migration_index_lock"
	KindMigrationAddNotNull    = "migration_add_not_null"
	KindMigrationBulkDML       = "migration_bulk_dml"
	KindMigrationBulkInsert    = "migration_bulk_insert"
	KindMigrationUnanalyzable  = "unanalyzable_migration"
	KindDeployRelease          = "deploy_release"
	KindDeployDuplicate        = "deploy_duplicate"
	KindDeployLedgerError      = "deploy_ledger_error"
	KindGitHubChangedFile      = "github_changed_file"
	KindGitHubCodeowners       = "github_codeowners"
	KindGitHubMissingTicket    = "github_missing_ticket"
)

type Finding struct {
	Source     string   `json:"source"`
	Kind       string   `json:"kind"`
	Action     string   `json:"action"`
	Target     string   `json:"target,omitempty"`
	File       string   `json:"file,omitempty"`
	RiskScore  int      `json:"risk_score"`
	RiskClass  string   `json:"risk_class"`
	Evidence   []string `json:"evidence"`
	ChangeTags []string `json:"change_tags,omitempty"`
}

func HighestRisk(findings []Finding) int {
	max := 0
	for _, finding := range findings {
		if finding.RiskScore > max {
			max = finding.RiskScore
		}
	}
	return max
}

func EvidenceCodes(findings []Finding) []string {
	seen := map[string]struct{}{}
	var out []string
	for _, finding := range findings {
		for _, item := range finding.Evidence {
			item = strings.TrimSpace(item)
			if item == "" {
				continue
			}
			if _, ok := seen[item]; ok {
				continue
			}
			seen[item] = struct{}{}
			out = append(out, item)
		}
	}
	return out
}

func Targets(findings []Finding) []string {
	seen := map[string]struct{}{}
	var out []string
	for _, finding := range findings {
		target := strings.TrimSpace(finding.Target)
		if target == "" {
			continue
		}
		if _, ok := seen[target]; ok {
			continue
		}
		seen[target] = struct{}{}
		out = append(out, target)
	}
	return out
}

func ContainsKind(findings []Finding, kinds ...string) bool {
	want := map[string]struct{}{}
	for _, kind := range kinds {
		want[kind] = struct{}{}
	}
	for _, finding := range findings {
		if _, ok := want[finding.Kind]; ok {
			return true
		}
	}
	return false
}
