package github

import (
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/preflight"
)

func TestCodeownersLastMatchWins(t *testing.T) {
	owners := ParseCODEOWNERS(`
* @org/all
/infra/ @org/platform
infra/payments/** @org/payments @sre
*.md @docs
`)
	if got := owners.OwnersFor("infra/payments/main.tf"); strings.Join(got, ",") != "@org/payments,@sre" {
		t.Fatalf("owners=%v want payments owners", got)
	}
	if got := owners.OwnersFor("README.md"); strings.Join(got, ",") != "@docs" {
		t.Fatalf("owners=%v want docs owner", got)
	}
}

func TestHasLinkedTicket(t *testing.T) {
	if !HasLinkedTicket("OPS-842 protect billing deploy", "", "") {
		t.Fatalf("Jira-style ticket was not detected")
	}
	if !HasLinkedTicket("", "Fixes #42", "") {
		t.Fatalf("GitHub closing keyword was not detected")
	}
	if HasLinkedTicket("refactor checkout", "small cleanup", "feature/no-ticket") {
		t.Fatalf("plain text should not count as linked ticket")
	}
}

func TestScanChangedFilesAndMissingTicket(t *testing.T) {
	findings := Scan(PullRequest{
		Owner:   "acme",
		Repo:    "checkout",
		Number:  842,
		Title:   "change billing schema",
		Body:    "no ticket here",
		HeadRef: "feature/billing-schema",
		ChangedFiles: []ChangedFile{
			{Filename: "migrations/20260630_add_invoice.sql", Status: "modified"},
			{Filename: "README.md", Status: "modified"},
		},
		CodeOwners: `
/migrations/ @db-owner
*.md @docs
`,
	})
	if len(findings) != 3 {
		t.Fatalf("findings=%d want 3", len(findings))
	}
	if findings[0].Source != preflight.SourceGitHub || findings[0].Kind != preflight.KindGitHubChangedFile {
		t.Fatalf("first finding identity=%+v", findings[0])
	}
	if findings[2].Kind != preflight.KindGitHubMissingTicket || findings[2].Action != ActionMissingTicket {
		t.Fatalf("missing ticket finding=%+v", findings[2])
	}
	evidence := strings.Join(findings[0].Evidence, " ")
	if !strings.Contains(evidence, "github_file_fingerprint=sha256:") || !strings.Contains(evidence, "codeowner_fingerprint=sha256:") {
		t.Fatalf("evidence missing fingerprints: %v", findings[0].Evidence)
	}
	if strings.Contains(evidence, "20260630_add_invoice") || strings.Contains(evidence, "@db-owner") {
		t.Fatalf("evidence leaked raw path or owner: %v", findings[0].Evidence)
	}
}

func TestScanDoesNotFlagTicketWhenPresent(t *testing.T) {
	findings := Scan(PullRequest{
		Title: "OPS-842 change docs",
		ChangedFiles: []ChangedFile{
			{Filename: "README.md"},
		},
	})
	for _, finding := range findings {
		if finding.Kind == preflight.KindGitHubMissingTicket {
			t.Fatalf("unexpected missing ticket finding: %+v", finding)
		}
	}
}
