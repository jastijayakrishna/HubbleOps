package github

import (
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
	"github.com/hubbleops/hubbleops/internal/privacy"
)

const (
	ActionPullRequest   = "github.pull_request"
	ActionMissingTicket = "github.missing_ticket"
)

type PullRequest struct {
	Owner        string
	Repo         string
	Number       int
	Title        string
	Body         string
	HeadRef      string
	BaseRef      string
	Author       string
	ChangedFiles []ChangedFile
	CodeOwners   string
}

type ChangedFile struct {
	Filename string
	Status   string
}

type CodeOwners struct {
	rules []ownerRule
}

type ownerRule struct {
	Pattern string
	Owners  []string
}

func Scan(pr PullRequest) []preflight.Finding {
	target := prTarget(pr)
	owners := ParseCODEOWNERS(pr.CodeOwners)
	var findings []preflight.Finding
	for _, file := range pr.ChangedFiles {
		path := normalizePath(file.Filename)
		if path == "" {
			continue
		}
		fileOwners := owners.OwnersFor(path)
		risk := changedFileRisk(path, fileOwners)
		evidence := []string{
			"github_file_fingerprint=" + privacy.FingerprintString(path),
			"github_file_status=" + normalizeStatus(file.Status),
		}
		if len(fileOwners) > 0 {
			evidence = append(evidence, "codeowners=present")
			for _, owner := range fileOwners {
				evidence = append(evidence, "codeowner_fingerprint="+privacy.FingerprintString(owner))
			}
		} else {
			evidence = append(evidence, "codeowners=missing")
		}
		findings = append(findings, preflight.Finding{
			Source:    preflight.SourceGitHub,
			Kind:      preflight.KindGitHubChangedFile,
			Action:    ActionPullRequest,
			Target:    path,
			RiskScore: risk,
			RiskClass: action.RiskClass(risk),
			Evidence:  evidence,
			ChangeTags: []string{
				"github:changed_file",
				"github_status:" + normalizeStatus(file.Status),
			},
		})
	}
	if len(pr.ChangedFiles) > 0 && !HasLinkedTicket(pr.Title, pr.Body, pr.HeadRef) {
		findings = append(findings, preflight.Finding{
			Source:    preflight.SourceGitHub,
			Kind:      preflight.KindGitHubMissingTicket,
			Action:    ActionMissingTicket,
			Target:    target,
			RiskScore: 45,
			RiskClass: action.RiskClass(45),
			Evidence: []string{
				"github_linked_ticket=missing",
				"github_pr_fingerprint=" + privacy.FingerprintString(target),
			},
			ChangeTags: []string{"github:missing_ticket"},
		})
	}
	return findings
}

func ParseCODEOWNERS(text string) CodeOwners {
	var out CodeOwners
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(stripComment(line))
		if line == "" || strings.HasPrefix(line, "[") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		var owners []string
		for _, owner := range fields[1:] {
			owner = strings.TrimSpace(owner)
			if owner != "" {
				owners = append(owners, owner)
			}
		}
		if len(owners) > 0 {
			out.rules = append(out.rules, ownerRule{Pattern: normalizePattern(fields[0]), Owners: owners})
		}
	}
	return out
}

func (c CodeOwners) OwnersFor(path string) []string {
	path = normalizePath(path)
	var owners []string
	for _, rule := range c.rules {
		if codeownerMatch(rule.Pattern, path) {
			owners = append([]string{}, rule.Owners...)
		}
	}
	return owners
}

func HasLinkedTicket(values ...string) bool {
	text := strings.Join(values, " ")
	return jiraTicketPattern.MatchString(text) || githubIssuePattern.MatchString(text)
}

var (
	jiraTicketPattern   = regexp.MustCompile(`(?i)\b[A-Z][A-Z0-9]+-\d+\b`)
	githubIssuePattern  = regexp.MustCompile(`(?i)\b(?:fix(?:es)?|close(?:s|d)?|resolve(?:s|d)?)\s+#\d+\b`)
	highRiskPathPattern = regexp.MustCompile(`(?i)(^|/)(infra|terraform|migrations?|database|db|schema|deploy|k8s|kubernetes)(/|$)`)
)

func changedFileRisk(path string, owners []string) int {
	if highRiskPathPattern.MatchString(path) {
		if len(owners) == 0 {
			return 65
		}
		return 55
	}
	if len(owners) == 0 {
		return 25
	}
	return 15
}

func prTarget(pr PullRequest) string {
	repo := strings.Trim(strings.TrimSpace(pr.Owner)+"/"+strings.TrimSpace(pr.Repo), "/")
	if repo == "" {
		repo = "github"
	}
	if pr.Number > 0 {
		return repo + "#" + strconv.Itoa(pr.Number)
	}
	return repo
}

func stripComment(line string) string {
	escaped := false
	for i, r := range line {
		if r == '\\' {
			escaped = !escaped
			continue
		}
		if r == '#' && !escaped {
			return line[:i]
		}
		escaped = false
	}
	return line
}

func normalizePath(path string) string {
	path = filepath.ToSlash(strings.TrimSpace(path))
	path = strings.TrimPrefix(path, "./")
	return strings.TrimPrefix(path, "/")
}

func normalizePattern(pattern string) string {
	pattern = filepath.ToSlash(strings.TrimSpace(pattern))
	pattern = strings.TrimPrefix(pattern, "./")
	return pattern
}

func normalizeStatus(status string) string {
	status = strings.ToLower(strings.TrimSpace(status))
	if status == "" {
		return "modified"
	}
	return status
}

func codeownerMatch(pattern, path string) bool {
	pattern = normalizePattern(pattern)
	path = normalizePath(path)
	if pattern == "" {
		return false
	}
	anchored := strings.HasPrefix(pattern, "/")
	pattern = strings.TrimPrefix(pattern, "/")
	if strings.HasSuffix(pattern, "/") {
		return strings.HasPrefix(path, strings.TrimSuffix(pattern, "/")+"/")
	}
	if strings.Contains(pattern, "**") {
		prefix, suffix, _ := strings.Cut(pattern, "**")
		prefix = strings.Trim(prefix, "/")
		suffix = strings.Trim(suffix, "/")
		if prefix != "" && !strings.HasPrefix(path, prefix) {
			return false
		}
		if suffix != "" && !strings.HasSuffix(path, suffix) {
			return false
		}
		return true
	}
	if strings.ContainsAny(pattern, "*?[") {
		if ok, _ := filepath.Match(pattern, path); ok {
			return true
		}
		base := filepath.Base(path)
		ok, _ := filepath.Match(pattern, base)
		return ok
	}
	if anchored {
		return path == pattern || strings.HasPrefix(path, pattern+"/")
	}
	return path == pattern || strings.HasSuffix(path, "/"+pattern) || strings.HasPrefix(path, pattern+"/")
}
