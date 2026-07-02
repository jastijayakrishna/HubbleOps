# hubbleops-sqlparse (optional parse-validity oracle)

This is a **separate Go module** on purpose. It wraps real SQL parsers
(`pganalyze/pg_query_go` for Postgres, `vitess` for MySQL) whose cgo + newer-Go-toolchain
requirements would otherwise pollute the root `github.com/hubbleops/hubbleops` module graph.

Because it is its own module, the repo root stays clean: `go mod tidy` at the root never
pulls vitess/pg_query, and the product build is pinned to the repo's Go version. The main
`hubbleops` binary calls this one **as a subprocess** — nothing imports it.

## What it does

Reads SQL on stdin, parses it for `-dialect postgres|mysql`, and prints
`{"analyzable":true|false}`. Migration preflight uses it only as a *validity oracle*: if the
SQL fails to parse, the gate emits an `unanalyzable` finding (fail-closed); classification is
still done by the main scanner.

## Build (Linux + cgo, Go >= 1.26.4)

```bash
cd internal/sqlparse
go mod tidy            # generates go.sum in an environment with network access
CGO_ENABLED=1 go build -o hubbleops-sqlparse .
```

## Wire it into preflight

Point the main binary at it (or put `hubbleops-sqlparse` on `PATH`):

```bash
export HUBBLEOPS_SQLPARSE_BIN=/path/to/hubbleops-sqlparse
hubbleops preflight migration ./migrations -env production
```

If `HUBBLEOPS_SQLPARSE_BIN` is unset and no `hubbleops-sqlparse` is on `PATH`, preflight
transparently falls back to the built-in heuristic scanner.
