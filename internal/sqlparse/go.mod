// Separate module: the optional `hubbleops-sqlparse` parse-validity oracle.
//
// It is deliberately NOT part of the root github.com/hubbleops/hubbleops module.
// Keeping it here isolates its heavy cgo parser dependencies (pganalyze/pg_query_go
// and vitess, which pulls a newer Go toolchain requirement) so the root module's
// dependency graph stays clean and `go mod tidy` at the repo root never drags them
// in. The main binary invokes this one as a subprocess; nothing imports it.
//
// go.sum is generated where this module is actually built (Linux + cgo, Go >= 1.26.4)
// via `go mod tidy` in this directory — see README.md.
module github.com/hubbleops/hubbleops/internal/sqlparse

go 1.26.4

require (
	github.com/pganalyze/pg_query_go/v6 v6.2.2
	vitess.io/vitess v0.24.2
)
