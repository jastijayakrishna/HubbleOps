// Command hubbleops-sqlparse is an optional, separately-built SQL parse-validity oracle for
// HubbleOps migration preflight.
//
// It lives in its own Go module so its heavy cgo parser dependencies (pganalyze/pg_query_go,
// vitess) never enter the main hubbleops module graph. The main binary shells out to this one;
// no Go code imports it. When this binary is absent, migration preflight falls back to its
// built-in heuristic scanner (still hardened, still fail-closed on unanalyzable input).
//
// Protocol: read SQL from stdin, parse it with a real engine for -dialect, and print
// {"analyzable":true|false} to stdout. Exit 0 whenever the parser ran (regardless of the
// verdict); non-zero only on an operational error (bad flags, unreadable stdin).
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	pgquery "github.com/pganalyze/pg_query_go/v6"
	"vitess.io/vitess/go/vt/sqlparser"
)

func main() {
	dialect := flag.String("dialect", "postgres", "sql dialect: postgres or mysql")
	flag.Parse()

	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "read stdin:", err)
		os.Exit(2)
	}
	sql := string(raw)

	analyzable := true
	if strings.TrimSpace(sql) != "" {
		switch strings.ToLower(strings.TrimSpace(*dialect)) {
		case "mysql", "mariadb":
			analyzable = parseMySQL(sql) == nil
		default:
			analyzable = parsePostgres(sql) == nil
		}
	}

	if err := json.NewEncoder(os.Stdout).Encode(struct {
		Analyzable bool `json:"analyzable"`
	}{Analyzable: analyzable}); err != nil {
		fmt.Fprintln(os.Stderr, "encode result:", err)
		os.Exit(2)
	}
}

func parsePostgres(sql string) error {
	_, err := pgquery.ParseToJSON(sql)
	return err
}

func parseMySQL(sql string) error {
	parser := sqlparser.NewTestParser()
	_, err := parser.ParseMultipleIgnoreEmpty(sql)
	return err
}
