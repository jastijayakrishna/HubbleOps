package migration

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hubbleops/hubbleops/internal/preflight"
)

func kindsOf(findings []preflight.Finding) map[string]int {
	out := map[string]int{}
	for _, f := range findings {
		out[f.Kind]++
	}
	return out
}

func TestScanContentFlagsDestructiveMigration(t *testing.T) {
	sql := `
-- DROP TABLE ignored_in_comment;
ALTER TABLE customers ALTER COLUMN email TYPE text;
DROP TABLE IF EXISTS invoices;
TRUNCATE audit_events;
`
	findings := ScanContent("migrations/001.sql", sql)
	if len(findings) != 3 {
		t.Fatalf("findings=%d want 3: %+v", len(findings), findings)
	}
	if !preflight.ContainsKind(findings, preflight.KindMigrationDrop, preflight.KindMigrationAlter, preflight.KindMigrationTruncate) {
		t.Fatalf("missing expected kinds: %+v", findings)
	}
	for _, finding := range findings {
		encoded := strings.Join(finding.Evidence, " ")
		if strings.Contains(encoded, "customers") || strings.Contains(encoded, "invoices") || strings.Contains(encoded, "audit_events") {
			t.Fatalf("evidence leaked raw identifier: %v", finding.Evidence)
		}
		if !strings.Contains(encoded, "file_fingerprint=sha256:") {
			t.Fatalf("evidence missing file fingerprint: %v", finding.Evidence)
		}
	}
}

// The headline false positive: a harmless statement that merely mentions a dangerous
// keyword inside a string literal must NOT be flagged.
func TestScanContentNoFalsePositiveOnStringLiteral(t *testing.T) {
	cases := []string{
		`INSERT INTO audit(msg) VALUES ('reminder: do not DROP TABLE customers in prod');`,
		`UPDATE notes SET body = 'TRUNCATE is dangerous' WHERE id = 1;`,
		`INSERT INTO t(x) VALUES ($tag$ DELETE FROM orders $tag$);`,
		`SELECT 'DROP TABLE x', "DROP TABLE" FROM t WHERE 1=1;`,
	}
	for _, sql := range cases {
		if findings := ScanContent("m.sql", sql); len(findings) != 0 {
			t.Fatalf("false positive on %q: %+v", sql, findings)
		}
	}
}

func TestScanContentCatchesUnboundedDML(t *testing.T) {
	del := ScanContent("m.sql", `DELETE FROM orders;`)
	if kindsOf(del)[preflight.KindMigrationDeleteNoWhere] != 1 || del[0].RiskScore < 90 {
		t.Fatalf("unbounded DELETE not flagged: %+v", del)
	}
	upd := ScanContent("m.sql", `UPDATE users SET is_admin = true;`)
	if kindsOf(upd)[preflight.KindMigrationUpdateNoWhere] != 1 {
		t.Fatalf("unbounded UPDATE not flagged: %+v", upd)
	}
}

func TestScanContentCatchesTautologicalWhereDML(t *testing.T) {
	del := ScanContent("m.sql", `DELETE FROM users WHERE 1=1;`)
	if kindsOf(del)[preflight.KindMigrationDeleteNoWhere] != 1 || del[0].RiskScore < 90 {
		t.Fatalf("tautological DELETE not flagged as block-level risk: %+v", del)
	}
	if !strings.Contains(strings.Join(del[0].Evidence, " "), "migration_contains=DELETE_TAUTOLOGICAL_WHERE") {
		t.Fatalf("tautological DELETE evidence missing detector tag: %v", del[0].Evidence)
	}

	upd := ScanContent("m.sql", `UPDATE accounts SET x=0 WHERE 1=1;`)
	if kindsOf(upd)[preflight.KindMigrationUpdateNoWhere] != 1 || upd[0].RiskScore < 70 {
		t.Fatalf("tautological UPDATE not flagged for approval/block: %+v", upd)
	}
}

func TestScanContentCatchesMySQLExecutableCommentDDL(t *testing.T) {
	findings := ScanContent("m.sql", `/*! DROP TABLE users */;`)
	if kindsOf(findings)[preflight.KindMigrationDrop] != 1 || findings[0].RiskScore < 90 {
		t.Fatalf("MySQL executable comment DROP not flagged: %+v", findings)
	}
}

func TestScanContentBoundedDMLIsSafe(t *testing.T) {
	sql := `
DELETE FROM orders WHERE created_at < now() - interval '30 days';
UPDATE users SET seen = true WHERE id = 42;
UPDATE accounts SET touched = true WHERE 1=1 AND id = 42;
`
	if findings := ScanContent("m.sql", sql); len(findings) != 0 {
		t.Fatalf("bounded DML flagged: %+v", findings)
	}
}

func TestScanPathsIncludesORMMigrationFilesAsUnanalyzable(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "202607010001_drop_users.rb")
	if err := os.WriteFile(path, []byte(`class DropUsers < ActiveRecord::Migration[7.1]
  def change
    drop_table :users
  end
end
`), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	findings, err := ScanPaths([]string{dir})
	if err != nil {
		t.Fatalf("scan paths: %v", err)
	}
	if kindsOf(findings)[preflight.KindMigrationUnanalyzable] != 1 || findings[0].RiskScore < 70 {
		t.Fatalf("ORM migration was silently allowed: %+v", findings)
	}
	if strings.Contains(strings.Join(findings[0].Evidence, " "), "drop_table") {
		t.Fatalf("unanalyzable evidence leaked raw ORM content: %v", findings[0].Evidence)
	}
}

// A WHERE that lives only inside a subquery does NOT bound the outer statement: the
// UPDATE still rewrites every row, so it must be flagged.
func TestScanContentSubqueryWhereDoesNotBoundOuterStatement(t *testing.T) {
	sql := `UPDATE t SET x = (SELECT v FROM u WHERE u.id = t.id);`
	findings := ScanContent("m.sql", sql)
	if kindsOf(findings)[preflight.KindMigrationUpdateNoWhere] != 1 {
		t.Fatalf("subquery-only WHERE was treated as bounded: %+v", findings)
	}
}

func TestScanContentFlagsNonConcurrentIndex(t *testing.T) {
	locking := ScanContent("m.sql", `CREATE INDEX idx_big ON big_table (col);`)
	if kindsOf(locking)[preflight.KindMigrationIndexLock] != 1 {
		t.Fatalf("non-concurrent CREATE INDEX not flagged: %+v", locking)
	}
	safe := ScanContent("m.sql", `CREATE INDEX CONCURRENTLY idx_big ON big_table (col);`)
	if len(safe) != 0 {
		t.Fatalf("CONCURRENTLY index should be safe: %+v", safe)
	}
}

func TestScanContentDoesNotFlagIndexOnTableCreatedInSameMigration(t *testing.T) {
	sql := `
CREATE TABLE "BookingSeat" ("id" text primary key, "referenceUid" text);
CREATE UNIQUE INDEX "BookingSeat_referenceUid_key" ON "BookingSeat"("referenceUid");
`
	if findings := ScanContent("m.sql", sql); len(findings) != 0 {
		t.Fatalf("index on new quoted table should be safe: %+v", findings)
	}
}

func TestScanContentDoesNotFlagTemporaryTableCleanup(t *testing.T) {
	sql := `
CREATE TEMP TABLE tmp_backfill (id bigint);
TRUNCATE tmp_backfill;
DROP TABLE tmp_backfill;
`
	if findings := ScanContent("m.sql", sql); len(findings) != 0 {
		t.Fatalf("temporary table cleanup should be safe: %+v", findings)
	}
}

func TestScanContentFlagsCTEPrefixedUnboundedDML(t *testing.T) {
	del := ScanContent("m.sql", `WITH doomed AS (SELECT id FROM users) DELETE FROM users USING doomed;`)
	if kindsOf(del)[preflight.KindMigrationDeleteNoWhere] != 1 {
		t.Fatalf("CTE-prefixed unbounded DELETE not flagged: %+v", del)
	}
	upd := ScanContent("m.sql", `WITH target AS (SELECT id FROM accounts) UPDATE accounts SET suspended = true FROM target;`)
	if kindsOf(upd)[preflight.KindMigrationUpdateNoWhere] != 1 {
		t.Fatalf("CTE-prefixed unbounded UPDATE not flagged: %+v", upd)
	}
}

func TestScanContentFlagsCTEPrefixedBulkDMLForReview(t *testing.T) {
	del := ScanContent("m.sql", `WITH doomed AS (SELECT id FROM users WHERE disabled) DELETE FROM users USING doomed WHERE users.id = doomed.id;`)
	if kindsOf(del)[preflight.KindMigrationBulkDML] != 1 || del[0].RiskScore < 70 {
		t.Fatalf("CTE-prefixed bulk DELETE not flagged for review: %+v", del)
	}
	upd := ScanContent("m.sql", `WITH target AS (SELECT id FROM accounts) UPDATE accounts SET role = 'admin' FROM target WHERE accounts.id = target.id;`)
	if kindsOf(upd)[preflight.KindMigrationBulkDML] != 1 || upd[0].RiskScore < 70 {
		t.Fatalf("CTE-prefixed bulk UPDATE not flagged for review: %+v", upd)
	}
}

func TestScanContentFlagsInsertSelectBulkCopy(t *testing.T) {
	findings := ScanContent("m.sql", `INSERT INTO audit_log SELECT * FROM events;`)
	if kindsOf(findings)[preflight.KindMigrationBulkInsert] != 1 || findings[0].RiskScore < 70 {
		t.Fatalf("INSERT SELECT bulk copy not flagged: %+v", findings)
	}
}

func TestScanContentFlagsMergeRewrites(t *testing.T) {
	sql := `
MERGE INTO users
USING incoming_users ON users.id = incoming_users.id
WHEN MATCHED THEN DELETE;
`
	findings := ScanContent("m.sql", sql)
	if kindsOf(findings)[preflight.KindMigrationDeleteNoWhere] != 1 {
		t.Fatalf("MERGE DELETE not flagged: %+v", findings)
	}
}

func TestScanContentFlagsAdditionalDestructiveStatements(t *testing.T) {
	sql := `
DROP DATABASE customer_prod;
DROP INDEX CONCURRENTLY IF EXISTS users_email_idx;
REINDEX TABLE users;
`
	findings := ScanContent("m.sql", sql)
	if len(findings) != 3 {
		t.Fatalf("expected 3 destructive statement findings: %+v", findings)
	}
	if kindsOf(findings)[preflight.KindMigrationDrop] != 1 || kindsOf(findings)[preflight.KindMigrationAlter] != 2 {
		t.Fatalf("unexpected kinds: %+v", findings)
	}
}

func TestScanContentFlagsRiskyAlterTableAdditions(t *testing.T) {
	sql := `
ALTER TABLE orders ADD CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users(id);
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE(email);
ALTER TABLE accounts ADD PRIMARY KEY (id);
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
ALTER TABLE users ADD COLUMN public_id uuid NOT NULL DEFAULT gen_random_uuid();
`
	findings := ScanContent("m.sql", sql)
	if len(findings) != 5 {
		t.Fatalf("expected 5 risky ALTER findings: %+v", findings)
	}
	if kindsOf(findings)[preflight.KindMigrationAlter] != 5 {
		t.Fatalf("unexpected alter findings: %+v", findings)
	}
}

func TestScanContentAllowsNotValidConstraint(t *testing.T) {
	sql := `ALTER TABLE orders ADD CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;`
	if findings := ScanContent("m.sql", sql); len(findings) != 0 {
		t.Fatalf("NOT VALID constraint should be safe: %+v", findings)
	}
}

func TestScanContentFlagsConcurrentIndexInsideTransaction(t *testing.T) {
	sql := `
BEGIN;
CREATE INDEX CONCURRENTLY users_email_idx ON users(email);
COMMIT;
`
	findings := ScanContent("m.sql", sql)
	if kindsOf(findings)[preflight.KindMigrationIndexLock] != 1 {
		t.Fatalf("CREATE INDEX CONCURRENTLY inside transaction not flagged: %+v", findings)
	}
}

func TestScanContentFlagsAddNotNullWithoutDefault(t *testing.T) {
	bad := ScanContent("m.sql", `ALTER TABLE users ADD COLUMN flag boolean NOT NULL;`)
	if kindsOf(bad)[preflight.KindMigrationAddNotNull] != 1 {
		t.Fatalf("ADD COLUMN NOT NULL without default not flagged: %+v", bad)
	}
	ok := ScanContent("m.sql", `ALTER TABLE users ADD COLUMN flag boolean NOT NULL DEFAULT false;`)
	if len(ok) != 0 {
		t.Fatalf("ADD COLUMN NOT NULL DEFAULT should be safe: %+v", ok)
	}
	nullable := ScanContent("m.sql", `ALTER TABLE users ADD COLUMN nickname text;`)
	if len(nullable) != 0 {
		t.Fatalf("nullable ADD COLUMN should be safe: %+v", nullable)
	}
}

func TestScanContentFlagsDropColumnAndConstraint(t *testing.T) {
	findings := ScanContent("m.sql", `ALTER TABLE accounts DROP COLUMN ssn, DROP CONSTRAINT fk_owner;`)
	if len(findings) != 2 {
		t.Fatalf("expected 2 findings for two destructive actions: %+v", findings)
	}
}

// Real-world safe migration: additive, concurrent, bounded -> zero findings.
func TestScanContentSafeMigrationHasNoFindings(t *testing.T) {
	sql := `
CREATE TABLE widgets (id bigserial primary key, name text);
CREATE INDEX CONCURRENTLY idx_widgets_name ON widgets (name);
ALTER TABLE widgets ADD COLUMN price integer;
INSERT INTO widgets (name) VALUES ('hello; DROP TABLE x');
`
	if findings := ScanContent("migrations/002.sql", sql); len(findings) != 0 {
		t.Fatalf("safe migration flagged: %+v", findings)
	}
}
