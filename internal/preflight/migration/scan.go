// Package migration analyzes migration files for destructive or locking changes.
//
// It is statement-aware rather than a single-regex sweep: it first scrubs comments,
// string literals and dollar-quoted bodies so that dangerous
// keywords appearing inside data (e.g. an INSERT mentioning "DROP TABLE") never trigger a
// finding, then classifies each top-level statement. Safe quoted identifiers are preserved
// for table context. This catches the classes a naive
// regex misses — unbounded DELETE/UPDATE, non-CONCURRENT CREATE INDEX, ADD COLUMN NOT NULL
// without a default — modeled on Squawk / strong_migrations.
package migration

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
	"github.com/hubbleops/hubbleops/internal/privacy"
)

var (
	createIndexPattern = regexp.MustCompile(`^\s*create\s+(unique\s+)?index\b`)
	createTablePattern = regexp.MustCompile(`^\s*create\s+(temporary\s+|temp\s+|unlogged\s+)?table\b`)
	cteDMLPattern      = regexp.MustCompile(`\bas\s*\(\s*(delete|update|merge)\b`)
)

type rawFinding struct {
	kind   string
	tag    string
	action string
	target string
	risk   int
}

type scanContext struct {
	createdTables map[string]struct{}
	tempTables    map[string]struct{}
	inTransaction bool
}

func newScanContext() *scanContext {
	return &scanContext{
		createdTables: map[string]struct{}{},
		tempTables:    map[string]struct{}{},
	}
}

func ScanPaths(paths []string) ([]preflight.Finding, error) {
	files, err := migrationFiles(paths)
	if err != nil {
		return nil, err
	}
	var findings []preflight.Finding
	for _, path := range files {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("read migration %s: %w", path, err)
		}
		findings = append(findings, ScanContent(path, string(data))...)
	}
	return findings, nil
}

func ScanContent(path, sql string) []preflight.Finding {
	if shouldFailClosed(path, sql) {
		return []preflight.Finding{unanalyzableFinding(path)}
	}
	if findings, ok := parserBackedFindings(path, sql); ok {
		return findings
	}
	return scanSQLStatements(path, sql)
}

func scanSQLStatements(path, sql string) []preflight.Finding {
	var findings []preflight.Finding
	ctx := newScanContext()
	for _, stmt := range splitStatements(scrub(expandMySQLExecutableComments(sql))) {
		lower := strings.ToLower(stmt)
		if isTransactionControl(lower, "begin", "start transaction") {
			ctx.inTransaction = true
			continue
		}
		if isTransactionControl(lower, "commit", "rollback", "end") {
			ctx.inTransaction = false
			continue
		}
		if table, temporary, ok := createTableName(lower); ok {
			rememberCreatedTable(ctx, table, temporary)
		}
		for _, rf := range classifyStatement(stmt, ctx) {
			findings = append(findings, toFinding(path, rf))
		}
	}
	return findings
}

func toFinding(path string, rf rawFinding) preflight.Finding {
	return preflight.Finding{
		Source:    preflight.SourceMigration,
		Kind:      rf.kind,
		Action:    rf.action,
		Target:    rf.target,
		File:      path,
		RiskScore: rf.risk,
		RiskClass: action.RiskClass(rf.risk),
		Evidence: []string{
			"source=migration",
			"migration_contains=" + rf.tag,
			"file_fingerprint=" + privacy.FingerprintString(filepath.ToSlash(path)),
		},
		ChangeTags: []string{"migration:" + rf.tag},
	}
}

func unanalyzableFinding(path string) preflight.Finding {
	return preflight.Finding{
		Source:    preflight.SourceMigration,
		Kind:      preflight.KindMigrationUnanalyzable,
		Action:    "migration.review_unanalyzable",
		File:      path,
		RiskScore: 75,
		RiskClass: action.RiskClass(75),
		Evidence: []string{
			"source=migration",
			"migration_contains=UNANALYZABLE_MIGRATION",
			"parse_status=unanalyzable",
			"file_fingerprint=" + privacy.FingerprintString(filepath.ToSlash(path)),
		},
		ChangeTags: []string{"migration:UNANALYZABLE_MIGRATION"},
	}
}

func shouldFailClosed(path, content string) bool {
	if strings.TrimSpace(content) == "" {
		return false
	}
	if !isSQL(path) {
		return isMigrationFile(path)
	}
	return looksLikeORMMigration(content)
}

type sqlDialect string

const (
	sqlDialectPostgres sqlDialect = "postgres"
	sqlDialectMySQL    sqlDialect = "mysql"
)

func detectDialect(path, sql string) sqlDialect {
	lowerPath := strings.ToLower(filepath.ToSlash(path))
	lowerSQL := strings.ToLower(sql)
	switch {
	case strings.Contains(lowerPath, "mysql"), strings.Contains(lowerPath, "mariadb"):
		return sqlDialectMySQL
	case strings.Contains(lowerSQL, "/*!"), strings.Contains(lowerSQL, "`"):
		return sqlDialectMySQL
	default:
		return sqlDialectPostgres
	}
}

func looksLikeORMMigration(content string) bool {
	lower := strings.ToLower(content)
	markers := []string{
		"activerecord::migration",
		"class migration",
		"create_table ",
		"drop_table ",
		"add_column ",
		"remove_column ",
		"execute \"",
		"execute '",
		"alembic",
		"op.create_table",
		"op.drop_table",
		"op.add_column",
		"op.execute",
		"prisma migrate",
		"knex.schema",
		"sequelize.query",
		"queryrunner.query",
	}
	for _, marker := range markers {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

func expandMySQLExecutableComments(sql string) string {
	var b strings.Builder
	for i := 0; i < len(sql); {
		if i+2 < len(sql) && sql[i] == '/' && sql[i+1] == '*' && sql[i+2] == '!' {
			end := strings.Index(sql[i+3:], "*/")
			if end < 0 {
				b.WriteByte(' ')
				i = len(sql)
				continue
			}
			inner := sql[i+3 : i+3+end]
			inner = strings.TrimLeft(inner, "0123456789 \t\r\n")
			b.WriteByte(' ')
			b.WriteString(inner)
			b.WriteByte(' ')
			i += 3 + end + 2
			continue
		}
		b.WriteByte(sql[i])
		i++
	}
	return b.String()
}

// scrub replaces comments, string literals, dollar-quoted bodies and double-quoted
// identifiers with neutral whitespace so keyword/structure analysis only sees SQL syntax,
// never data. ASCII delimiters are detected at byte level, which is safe for UTF-8 since
// continuation bytes never collide with the ASCII delimiters we scan for.
func scrub(sql string) string {
	var b strings.Builder
	n := len(sql)
	for i := 0; i < n; {
		c := sql[i]
		switch {
		case c == '-' && i+1 < n && sql[i+1] == '-': // line comment
			j := i + 2
			for j < n && sql[j] != '\n' {
				j++
			}
			b.WriteByte(' ')
			i = j
		case c == '/' && i+1 < n && sql[i+1] == '*': // block comment (nestable)
			depth := 1
			j := i + 2
			for j < n && depth > 0 {
				if j+1 < n && sql[j] == '/' && sql[j+1] == '*' {
					depth++
					j += 2
					continue
				}
				if j+1 < n && sql[j] == '*' && sql[j+1] == '/' {
					depth--
					j += 2
					continue
				}
				j++
			}
			b.WriteByte(' ')
			i = j
		case c == '\'': // single-quoted string ('' and backslash escapes)
			j := i + 1
			for j < n {
				if sql[j] == '\\' && j+1 < n {
					j += 2
					continue
				}
				if sql[j] == '\'' {
					if j+1 < n && sql[j+1] == '\'' {
						j += 2
						continue
					}
					j++
					break
				}
				j++
			}
			b.WriteByte(' ')
			i = j
		case c == '"': // double-quoted identifier
			j := i + 1
			for j < n {
				if sql[j] == '"' {
					if j+1 < n && sql[j+1] == '"' {
						j += 2
						continue
					}
					j++
					break
				}
				j++
			}
			b.WriteString(safeIdentifierForScan(sql[i:j]))
			i = j
		case c == '$': // dollar-quoted string $tag$...$tag$
			if end, ok := dollarQuote(sql, i); ok {
				b.WriteByte(' ')
				i = end
			} else {
				b.WriteByte(c)
				i++
			}
		default:
			b.WriteByte(c)
			i++
		}
	}
	return b.String()
}

func dollarQuote(sql string, i int) (end int, ok bool) {
	n := len(sql)
	j := i + 1
	for j < n && (sql[j] == '_' || isAlphaNum(sql[j])) {
		j++
	}
	if j >= n || sql[j] != '$' {
		return i, false
	}
	tag := sql[i : j+1]
	start := j + 1
	idx := strings.Index(sql[start:], tag)
	if idx < 0 {
		return i, false
	}
	return start + idx + len(tag), true
}

func splitStatements(scrubbed string) []string {
	var out []string
	for _, part := range strings.Split(scrubbed, ";") {
		part = collapseSpaces(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

func isTransactionControl(lower string, controls ...string) bool {
	for _, control := range controls {
		if lower == control || strings.HasPrefix(lower, control+" ") {
			return true
		}
	}
	return false
}

func classifyStatement(stmt string, ctx *scanContext) []rawFinding {
	lower := strings.ToLower(stmt)
	switch {
	case strings.HasPrefix(lower, "drop table"):
		target := identAfter(lower, "drop table")
		if isKnownTemporaryTable(ctx, target) {
			return nil
		}
		return []rawFinding{{preflight.KindMigrationDrop, "DROP_TABLE", "migration.drop_table", target, 95}}
	case strings.HasPrefix(lower, "drop database"):
		return []rawFinding{{preflight.KindMigrationDrop, "DROP_DATABASE", "migration.drop_database", identAfter(lower, "drop database"), 98}}
	case strings.HasPrefix(lower, "drop index"):
		return []rawFinding{{preflight.KindMigrationAlter, "DROP_INDEX", "migration.drop_index", identAfter(lower, "drop index"), 72}}
	case strings.HasPrefix(lower, "reindex"):
		return []rawFinding{{preflight.KindMigrationAlter, "REINDEX", "migration.reindex", identAfter(lower, "reindex"), 72}}
	case strings.HasPrefix(lower, "truncate"):
		target := identAfter(lower, "truncate")
		if isKnownTemporaryTable(ctx, target) {
			return nil
		}
		return []rawFinding{{preflight.KindMigrationTruncate, "TRUNCATE", "migration.truncate", target, 92}}
	case strings.HasPrefix(lower, "delete"):
		if !hasTopLevelKeyword(lower, "where") {
			return []rawFinding{{preflight.KindMigrationDeleteNoWhere, "DELETE_NO_WHERE", "migration.delete_unbounded", identAfter(lower, "from"), 90}}
		}
		if hasTautologicalWhere(lower) {
			return []rawFinding{{preflight.KindMigrationDeleteNoWhere, "DELETE_TAUTOLOGICAL_WHERE", "migration.delete_unbounded", identAfter(lower, "from"), 90}}
		}
	case strings.HasPrefix(lower, "update"):
		if !hasTopLevelKeyword(lower, "where") {
			return []rawFinding{{preflight.KindMigrationUpdateNoWhere, "UPDATE_NO_WHERE", "migration.update_unbounded", identAfter(lower, "update"), 85}}
		}
		if hasTautologicalWhere(lower) {
			return []rawFinding{{preflight.KindMigrationUpdateNoWhere, "UPDATE_TAUTOLOGICAL_WHERE", "migration.update_unbounded", identAfter(lower, "update"), 85}}
		}
	case strings.HasPrefix(lower, "with"):
		return classifyCTEDML(lower)
	case strings.HasPrefix(lower, "merge"):
		return classifyMerge(lower)
	case strings.HasPrefix(lower, "insert") && hasTopLevelKeyword(lower, "select"):
		return []rawFinding{{preflight.KindMigrationBulkInsert, "INSERT_SELECT", "migration.insert_select", identAfter(lower, "into"), 76}}
	case createIndexPattern.MatchString(lower):
		target := identAfter(lower, "on")
		if isKnownNewOrTemporaryTable(ctx, target) {
			return nil
		}
		if strings.Contains(lower, "concurrently") && ctx != nil && ctx.inTransaction {
			return []rawFinding{{preflight.KindMigrationIndexLock, "CREATE_INDEX_CONCURRENTLY_IN_TRANSACTION", "migration.create_index_concurrently_in_transaction", target, 82}}
		}
		if !strings.Contains(lower, "concurrently") {
			return []rawFinding{{preflight.KindMigrationIndexLock, "CREATE_INDEX_LOCK", "migration.create_index_lock", target, 70}}
		}
	case strings.HasPrefix(lower, "alter table"):
		return classifyAlterTable(lower)
	}
	return nil
}

func classifyAlterTable(lower string) []rawFinding {
	rest := strings.TrimSpace(strings.TrimPrefix(lower, "alter table"))
	rest = trimPrefixWord(rest, "if exists")
	rest = trimPrefixWord(rest, "only")
	table, remainder := firstToken(rest)
	table = strings.Trim(table, `"'`)
	var out []rawFinding
	for _, clause := range splitTopLevelCommas(remainder) {
		clause = strings.TrimSpace(clause)
		switch {
		case strings.HasPrefix(clause, "detach partition"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "DETACH_PARTITION", "migration.detach_partition", table, 80})
		case strings.HasPrefix(clause, "drop column"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "DROP_COLUMN", "migration.drop_column", table, 88})
		case strings.HasPrefix(clause, "drop constraint"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "DROP_CONSTRAINT", "migration.drop_constraint", table, 75})
		case strings.HasPrefix(clause, "rename column"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "RENAME_COLUMN", "migration.rename_column", table, 70})
		case strings.HasPrefix(clause, "alter column") && (strings.Contains(clause, " type ") || strings.Contains(clause, "set data type")):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "ALTER_COLUMN", "migration.alter_column", table, 80})
		case strings.HasPrefix(clause, "alter column") && strings.Contains(clause, "set not null"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "SET_NOT_NULL", "migration.set_not_null", table, 70})
		case strings.HasPrefix(clause, "alter column") && strings.Contains(clause, "drop not null"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "DROP_NOT_NULL", "migration.drop_not_null", table, 72})
		case isAddConstraint(clause) && containsConstraintKind(clause, "foreign key", "check") && !strings.Contains(clause, "not valid"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "ADD_CONSTRAINT_VALIDATED", "migration.add_constraint_validated", table, 78})
		case isAddConstraint(clause) && containsConstraintKind(clause, "unique", "primary key"):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "ADD_UNIQUE_OR_PRIMARY_KEY", "migration.add_unique_or_primary_key", table, 82})
		case isAddColumn(clause) && strings.Contains(clause, "not null") && !strings.Contains(clause, "default"):
			out = append(out, rawFinding{preflight.KindMigrationAddNotNull, "ADD_NOT_NULL", "migration.add_not_null", table, 80})
		case isAddColumn(clause) && hasVolatileDefault(clause):
			out = append(out, rawFinding{preflight.KindMigrationAlter, "VOLATILE_DEFAULT", "migration.volatile_default", table, 74})
		}
	}
	return out
}

func isAddColumn(clause string) bool {
	if !strings.HasPrefix(clause, "add") {
		return false
	}
	// "add column ..." or "add <name> ..." (COLUMN keyword optional); exclude "add constraint".
	return !strings.HasPrefix(clause, "add constraint")
}

func isAddConstraint(clause string) bool {
	return strings.HasPrefix(clause, "add constraint") ||
		strings.HasPrefix(clause, "add foreign key") ||
		strings.HasPrefix(clause, "add check") ||
		strings.HasPrefix(clause, "add unique") ||
		strings.HasPrefix(clause, "add primary key")
}

func containsConstraintKind(clause string, kinds ...string) bool {
	for _, kind := range kinds {
		if strings.Contains(clause, " "+kind+" ") ||
			strings.Contains(clause, " "+kind+"(") ||
			strings.HasSuffix(clause, " "+kind) {
			return true
		}
	}
	return false
}

func hasVolatileDefault(clause string) bool {
	if !strings.Contains(clause, " default ") {
		return false
	}
	volatile := []string{
		"clock_timestamp(",
		"gen_random_uuid(",
		"random(",
		"uuid_generate_v4(",
	}
	for _, marker := range volatile {
		if strings.Contains(clause, marker) {
			return true
		}
	}
	return false
}

func classifyCTEDML(lower string) []rawFinding {
	if tail := dataModifyingCTETail(lower); tail != "" {
		return classifyDMLTail(tail, true)
	}
	deleteAt := topLevelWordIndex(lower, "delete")
	updateAt := topLevelWordIndex(lower, "update")
	mergeAt := topLevelWordIndex(lower, "merge")
	insertAt := topLevelWordIndex(lower, "insert")
	idx := earliestPositive(deleteAt, updateAt, mergeAt, insertAt)
	if idx < 0 {
		return nil
	}
	tail := lower[idx:]
	return classifyDMLTail(tail, false)
}

func classifyDMLTail(tail string, insideCTE bool) []rawFinding {
	switch {
	case strings.HasPrefix(tail, "delete"):
		if !hasTopLevelKeyword(tail, "where") {
			return []rawFinding{{preflight.KindMigrationDeleteNoWhere, "DELETE_NO_WHERE", "migration.delete_unbounded", identAfter(tail, "from"), 90}}
		}
		if hasTautologicalWhere(tail) {
			return []rawFinding{{preflight.KindMigrationDeleteNoWhere, "DELETE_TAUTOLOGICAL_WHERE", "migration.delete_unbounded", identAfter(tail, "from"), 90}}
		}
		return []rawFinding{{preflight.KindMigrationBulkDML, "CTE_DELETE", "migration.cte_delete", identAfter(tail, "from"), 76}}
	case strings.HasPrefix(tail, "update"):
		if !hasTopLevelKeyword(tail, "where") {
			return []rawFinding{{preflight.KindMigrationUpdateNoWhere, "UPDATE_NO_WHERE", "migration.update_unbounded", identAfter(tail, "update"), 85}}
		}
		if hasTautologicalWhere(tail) {
			return []rawFinding{{preflight.KindMigrationUpdateNoWhere, "UPDATE_TAUTOLOGICAL_WHERE", "migration.update_unbounded", identAfter(tail, "update"), 85}}
		}
		return []rawFinding{{preflight.KindMigrationBulkDML, "CTE_UPDATE", "migration.cte_update", identAfter(tail, "update"), 76}}
	case strings.HasPrefix(tail, "merge"):
		return classifyMerge(tail)
	case strings.HasPrefix(tail, "insert") && hasTopLevelKeyword(tail, "select"):
		return []rawFinding{{preflight.KindMigrationBulkInsert, "INSERT_SELECT", "migration.insert_select", identAfter(tail, "into"), 76}}
	}
	if insideCTE {
		return []rawFinding{{preflight.KindMigrationBulkDML, "CTE_DML", "migration.cte_dml", "", 76}}
	}
	return nil
}

func dataModifyingCTETail(lower string) string {
	loc := cteDMLPattern.FindStringIndex(lower)
	if loc == nil {
		return ""
	}
	tail := lower[loc[0]:]
	for _, kw := range []string{"delete", "update", "merge"} {
		if idx := strings.Index(tail, kw); idx >= 0 {
			return tail[idx:]
		}
	}
	return ""
}

func classifyMerge(lower string) []rawFinding {
	target := identAfter(lower, "into")
	var out []rawFinding
	if strings.Contains(lower, " then delete") {
		out = append(out, rawFinding{preflight.KindMigrationDeleteNoWhere, "MERGE_DELETE", "migration.merge_delete", target, 88})
	}
	if strings.Contains(lower, " then update") {
		out = append(out, rawFinding{preflight.KindMigrationUpdateNoWhere, "MERGE_UPDATE", "migration.merge_update", target, 82})
	}
	return out
}

func earliestPositive(values ...int) int {
	best := -1
	for _, value := range values {
		if value <= 0 {
			continue
		}
		if best < 0 || value < best {
			best = value
		}
	}
	return best
}

// hasTopLevelKeyword reports whether kw appears as a whole word at parenthesis depth 0,
// so a WHERE that lives only inside a subquery does not count as bounding the statement.
func hasTopLevelKeyword(s, kw string) bool {
	depth := 0
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '(':
			depth++
		case ')':
			if depth > 0 {
				depth--
			}
		default:
			if depth == 0 && wordAt(s, i, kw) {
				return true
			}
		}
	}
	return false
}

func hasTautologicalWhere(s string) bool {
	where, ok := topLevelWhereClause(s)
	if !ok {
		return false
	}
	return isConstantTruePredicate(where)
}

func topLevelWhereClause(s string) (string, bool) {
	whereAt := topLevelWordIndex(s, "where")
	if whereAt < 0 {
		return "", false
	}
	clause := strings.TrimSpace(s[whereAt+len("where"):])
	if clause == "" {
		return "", false
	}
	cutAt := len(clause)
	for _, kw := range []string{"returning", "order", "limit", "group", "having", "for"} {
		if idx := topLevelWordIndex(clause, kw); idx >= 0 && idx < cutAt {
			cutAt = idx
		}
	}
	return strings.TrimSpace(clause[:cutAt]), true
}

func isConstantTruePredicate(expr string) bool {
	expr = stripOuterParens(collapseSpaces(strings.ToLower(strings.TrimSpace(expr))))
	if expr == "" {
		return false
	}
	switch expr {
	case "true", "1", "1=1", "1 = 1", "1 is not null":
		return true
	case "false", "0", "1=0", "1 = 0", "0=1", "0 = 1":
		return false
	}
	if allTopLevelParts(expr, "and", isConstantTruePredicate) {
		return true
	}
	if anyTopLevelPart(expr, "or", isConstantTruePredicate) {
		return true
	}
	return false
}

func stripOuterParens(s string) string {
	for {
		s = strings.TrimSpace(s)
		if len(s) < 2 || s[0] != '(' || s[len(s)-1] != ')' {
			return s
		}
		depth := 0
		wrapped := true
		for i := 0; i < len(s); i++ {
			switch s[i] {
			case '(':
				depth++
			case ')':
				depth--
				if depth == 0 && i != len(s)-1 {
					wrapped = false
				}
			}
			if depth < 0 {
				return s
			}
		}
		if !wrapped || depth != 0 {
			return s
		}
		s = s[1 : len(s)-1]
	}
}

func allTopLevelParts(expr, op string, pred func(string) bool) bool {
	parts := splitTopLevelWord(expr, op)
	if len(parts) <= 1 {
		return false
	}
	for _, part := range parts {
		if !pred(part) {
			return false
		}
	}
	return true
}

func anyTopLevelPart(expr, op string, pred func(string) bool) bool {
	parts := splitTopLevelWord(expr, op)
	if len(parts) <= 1 {
		return false
	}
	for _, part := range parts {
		if pred(part) {
			return true
		}
	}
	return false
}

func splitTopLevelWord(s, kw string) []string {
	var parts []string
	start, depth := 0, 0
	for i := 0; i <= len(s)-len(kw); i++ {
		switch s[i] {
		case '(':
			depth++
			continue
		case ')':
			if depth > 0 {
				depth--
			}
			continue
		}
		if depth == 0 && wordAt(s, i, kw) {
			parts = append(parts, strings.TrimSpace(s[start:i]))
			start = i + len(kw)
			i += len(kw) - 1
		}
	}
	parts = append(parts, strings.TrimSpace(s[start:]))
	return parts
}

func topLevelWordIndex(s, kw string) int {
	depth := 0
	for i := 0; i <= len(s)-len(kw); i++ {
		switch s[i] {
		case '(':
			depth++
			continue
		case ')':
			if depth > 0 {
				depth--
			}
			continue
		}
		if depth == 0 && wordAt(s, i, kw) {
			return i
		}
	}
	return -1
}

func wordAt(s string, i int, kw string) bool {
	if i+len(kw) > len(s) || !strings.EqualFold(s[i:i+len(kw)], kw) {
		return false
	}
	if i > 0 && isWordByte(s[i-1]) {
		return false
	}
	if i+len(kw) < len(s) && isWordByte(s[i+len(kw)]) {
		return false
	}
	return true
}

func identAfter(s, kw string) string {
	idx := wordIndex(s, kw)
	if idx < 0 {
		return ""
	}
	rest := strings.TrimSpace(s[idx+len(kw):])
	for {
		before := rest
		for _, skip := range []string{"if not exists", "if exists", "concurrently", "table", "only"} {
			rest = trimPrefixWord(rest, skip)
		}
		if rest == before {
			break
		}
	}
	return firstIdent(rest)
}

func createTableName(lower string) (string, bool, bool) {
	if !createTablePattern.MatchString(lower) {
		return "", false, false
	}
	rest := trimPrefixWord(lower, "create")
	temporary := false
	for {
		before := rest
		switch {
		case strings.HasPrefix(rest, "temporary "):
			temporary = true
			rest = trimPrefixWord(rest, "temporary")
		case strings.HasPrefix(rest, "temp "):
			temporary = true
			rest = trimPrefixWord(rest, "temp")
		case strings.HasPrefix(rest, "unlogged "):
			rest = trimPrefixWord(rest, "unlogged")
		}
		if rest == before {
			break
		}
	}
	rest = trimPrefixWord(rest, "table")
	rest = trimPrefixWord(rest, "if not exists")
	table := normalizeIdentifier(firstIdent(rest))
	return table, temporary, table != ""
}

func rememberCreatedTable(ctx *scanContext, table string, temporary bool) {
	if ctx == nil {
		return
	}
	for _, key := range identifierKeys(table) {
		ctx.createdTables[key] = struct{}{}
		if temporary {
			ctx.tempTables[key] = struct{}{}
		}
	}
}

func isKnownNewOrTemporaryTable(ctx *scanContext, table string) bool {
	if ctx == nil {
		return false
	}
	for _, key := range identifierKeys(table) {
		if _, ok := ctx.createdTables[key]; ok {
			return true
		}
		if _, ok := ctx.tempTables[key]; ok {
			return true
		}
	}
	return false
}

func isKnownTemporaryTable(ctx *scanContext, table string) bool {
	if ctx == nil {
		return false
	}
	for _, key := range identifierKeys(table) {
		if _, ok := ctx.tempTables[key]; ok {
			return true
		}
	}
	return false
}

func identifierKeys(identifier string) []string {
	normalized := normalizeIdentifier(identifier)
	if normalized == "" {
		return nil
	}
	keys := []string{normalized}
	if idx := strings.LastIndex(normalized, "."); idx >= 0 && idx+1 < len(normalized) {
		keys = append(keys, normalized[idx+1:])
	}
	return keys
}

func normalizeIdentifier(identifier string) string {
	identifier = strings.ToLower(strings.TrimSpace(identifier))
	identifier = strings.ReplaceAll(identifier, " . ", ".")
	identifier = strings.ReplaceAll(identifier, ". ", ".")
	identifier = strings.ReplaceAll(identifier, " .", ".")
	identifier = strings.Trim(identifier, `"'`)
	return identifier
}

func wordIndex(s, kw string) int {
	for from := 0; from <= len(s)-len(kw); {
		idx := strings.Index(s[from:], kw)
		if idx < 0 {
			return -1
		}
		pos := from + idx
		before := pos == 0 || !isWordByte(s[pos-1])
		afterPos := pos + len(kw)
		after := afterPos >= len(s) || !isWordByte(s[afterPos])
		if before && after {
			return pos
		}
		from = pos + 1
	}
	return -1
}

func firstIdent(s string) string {
	s = strings.TrimSpace(s)
	end := strings.IndexAny(s, " \t\n(),;")
	if end < 0 {
		end = len(s)
	}
	return strings.Trim(s[:end], `"'`)
}

func firstToken(s string) (string, string) {
	s = strings.TrimSpace(s)
	idx := strings.IndexAny(s, " \t\n")
	if idx < 0 {
		return s, ""
	}
	return s[:idx], strings.TrimSpace(s[idx+1:])
}

func trimPrefixWord(s, w string) string {
	s = strings.TrimSpace(s)
	if s == w {
		return ""
	}
	if strings.HasPrefix(s, w) && len(s) > len(w) && (s[len(w)] == ' ' || s[len(w)] == '\t' || s[len(w)] == '\n') {
		return strings.TrimSpace(s[len(w):])
	}
	return s
}

func splitTopLevelCommas(s string) []string {
	var out []string
	depth, start := 0, 0
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '(':
			depth++
		case ')':
			if depth > 0 {
				depth--
			}
		case ',':
			if depth == 0 {
				out = append(out, strings.TrimSpace(s[start:i]))
				start = i + 1
			}
		}
	}
	out = append(out, strings.TrimSpace(s[start:]))
	return out
}

func collapseSpaces(s string) string {
	return strings.Join(strings.Fields(s), " ")
}

func isAlphaNum(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9')
}

func isWordByte(b byte) bool {
	return isAlphaNum(b) || b == '_'
}

func safeIdentifierForScan(quoted string) string {
	if len(quoted) < 2 || quoted[0] != '"' || quoted[len(quoted)-1] != '"' {
		return "qident"
	}
	inner := strings.ReplaceAll(quoted[1:len(quoted)-1], `""`, `"`)
	if inner == "" {
		return "qident"
	}
	for _, r := range inner {
		switch {
		case r >= 'a' && r <= 'z':
		case r >= 'A' && r <= 'Z':
		case r >= '0' && r <= '9':
		case r == '_' || r == '$':
		default:
			return "qident"
		}
	}
	return inner
}

func migrationFiles(paths []string) ([]string, error) {
	if len(paths) == 0 {
		return nil, fmt.Errorf("at least one migration file or directory is required")
	}
	seen := map[string]struct{}{}
	var files []string
	for _, path := range paths {
		path = strings.TrimSpace(path)
		if path == "" {
			continue
		}
		info, err := os.Stat(path)
		if err != nil {
			return nil, fmt.Errorf("stat %s: %w", path, err)
		}
		if !info.IsDir() {
			if isMigrationFile(path) {
				addFile(&files, seen, path)
			}
			continue
		}
		err = filepath.WalkDir(path, func(child string, d os.DirEntry, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			if d.IsDir() || !isMigrationFile(child) {
				return nil
			}
			addFile(&files, seen, child)
			return nil
		})
		if err != nil {
			return nil, fmt.Errorf("walk %s: %w", path, err)
		}
	}
	sort.Strings(files)
	return files, nil
}

func addFile(files *[]string, seen map[string]struct{}, path string) {
	clean := filepath.Clean(path)
	if _, ok := seen[clean]; ok {
		return
	}
	seen[clean] = struct{}{}
	*files = append(*files, clean)
}

func isSQL(path string) bool {
	return strings.EqualFold(filepath.Ext(path), ".sql")
}

func isMigrationFile(path string) bool {
	ext := strings.ToLower(filepath.Ext(path))
	switch ext {
	case ".sql", ".rb", ".py", ".js", ".ts", ".prisma":
		return true
	default:
		return false
	}
}
