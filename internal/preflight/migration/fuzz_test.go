package migration

import "testing"

// FuzzScanContent asserts the statement analyzer never panics on arbitrary SQL, is
// deterministic, and only ever emits well-formed findings. The seed corpus includes the
// adversarial shapes (keywords inside strings/comments/dollar-quotes, truncated input) that
// a naive scanner mishandles. Runs the seeds on every `go test`; full fuzzing via -fuzz.
func FuzzScanContent(f *testing.F) {
	seeds := []string{
		"",
		"DROP TABLE customers;",
		"INSERT INTO t(x) VALUES ('DROP TABLE customers');",
		"-- DROP TABLE x\n/* TRUNCATE y */ SELECT 1;",
		"INSERT INTO t VALUES ($tag$ DELETE FROM orders $tag$);",
		"ALTER TABLE a ADD COLUMN b int NOT NULL, DROP COLUMN c;",
		"DELETE FROM t WHERE id IN (SELECT id FROM u WHERE x=1);",
		"CREATE INDEX CONCURRENTLY i ON t (c);",
		"UPDATE t SET x = (SELECT 1 WHERE y=2)",
		"'unterminated string",
		"$$ unterminated dollar",
		"DROP   TABLE   IF   EXISTS   \"weird name\";",
		"SELECT '日本語', \"DROP TABLE\" FROM t;",
	}
	for _, s := range seeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, sql string) {
		findings := ScanContent("m.sql", sql)
		if len(ScanContent("m.sql", sql)) != len(findings) {
			t.Fatalf("non-deterministic result for %q", sql)
		}
		for _, finding := range findings {
			if finding.Kind == "" || finding.Source != "migration" {
				t.Fatalf("malformed finding: %+v", finding)
			}
			if finding.RiskScore < 0 || finding.RiskScore > 100 {
				t.Fatalf("risk score out of range: %d", finding.RiskScore)
			}
		}
	})
}
