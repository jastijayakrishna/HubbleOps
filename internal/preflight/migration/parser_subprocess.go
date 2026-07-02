package migration

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/hubbleops/hubbleops/internal/preflight"
)

// parserBackedFindings uses the optional, separately-built `hubbleops-sqlparse` binary as a
// real-parser validity oracle. That binary lives in its own module (internal/sqlparse) so its
// heavy cgo dependencies (pganalyze/pg_query_go, vitess) never enter this module's graph —
// `go mod tidy` here stays clean and the product build stays pinned to this repo's Go version.
//
// Semantics (identical to the previous in-process, build-tagged implementation):
//   - oracle not configured/available     -> (nil, false): defer to the heuristic scanner
//   - oracle ran and the SQL did NOT parse -> unanalyzable finding (fail-closed)
//   - oracle ran and the SQL parsed        -> heuristic classification via scanSQLStatements
func parserBackedFindings(path, sql string) ([]preflight.Finding, bool) {
	if !isSQL(path) {
		return nil, false
	}
	if strings.TrimSpace(sql) == "" {
		return nil, false
	}
	bin := sqlparseBinary()
	if bin == "" {
		return nil, false
	}
	analyzable, ran := runSQLParseOracle(bin, string(detectDialect(path, sql)), expandMySQLExecutableComments(sql))
	if !ran {
		// Could not run the oracle (missing/broken binary, timeout). Do not wedge the gate on
		// it — defer to the heuristic scanner, which remains hardened and fail-closed.
		return nil, false
	}
	if !analyzable {
		return []preflight.Finding{unanalyzableFinding(path)}, true
	}
	return scanSQLStatements(path, sql), true
}

// sqlparseBinary resolves the optional oracle: an explicit HUBBLEOPS_SQLPARSE_BIN path wins;
// otherwise a `hubbleops-sqlparse` on PATH is used when present. Returns "" when neither is
// available, which routes callers to the heuristic scanner.
func sqlparseBinary() string {
	if p := strings.TrimSpace(os.Getenv("HUBBLEOPS_SQLPARSE_BIN")); p != "" {
		if info, err := os.Stat(p); err == nil && !info.IsDir() {
			return p
		}
		return ""
	}
	if p, err := exec.LookPath("hubbleops-sqlparse"); err == nil {
		return p
	}
	return ""
}

func runSQLParseOracle(bin, dialect, sql string) (analyzable bool, ran bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, "-dialect", dialect)
	cmd.Stdin = strings.NewReader(sql)
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		return false, false
	}

	var res struct {
		Analyzable bool `json:"analyzable"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(out.Bytes()), &res); err != nil {
		return false, false
	}
	return res.Analyzable, true
}
