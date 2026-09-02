# Phase 1 — Source Closure + text/dependency observers + Ledger + Exposure Map

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §1–§6, `dev/context.md` |
| **Ships** | `hops scan`, `hops exposure`, frozen schemas, SQLite store, import/leak tests |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS` |
| **Then** | Phase 2 |

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md). Plan mode → `dev/plan.md` with
OPEN QUESTIONS → answer them → fresh-session [plan review](../cross-cutting/plan-review.md) →
implement → fresh-session gate audit.

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 1. Read CLAUDE.md, docs/ARCHITECTURE.md §1–§6, dev/context.md.

OUTCOME: `hops scan <repo> --pack google_ads` produces a deterministic Candidate Ledger and Exposure Map for any repository, with UNEXPLAINED_CANDIDATES = 0, using the Source Closure, a generic text observer, and a generic dependency observer that receive the pack's SurfaceSpec as a parameter.

DEFINITION OF DONE:
1. core/schemas/{evidence,candidate,obligation,proof_scope,receipt}.json exist and are validated on every write; IDs are SHA-256 of canonical content.
2. packs/_protocol.py defines SurfaceSpec (identifiers, hosts, package names, version carriers, request languages, sink argument positions, config/env keys) as a typed, serializable dataclass, plus the ProviderPack Protocol stub (fields filled in later phases). packs/google_ads/surface.yaml is the first instance; packs/_mock/surface.yaml is the second. app/registry.py loads a pack by name.
3. closure/source_closure.py classifies every path as INSIDE | GENERATED | VENDORED | SUBMODULE | EXTERNAL_BOUNDARY | UNSCANNED; anything unreadable/unclassifiable → FILE_UNSCANNED candidate.
4. observe/text.py: `scan(closure, surface: SurfaceSpec) -> list[Evidence]` wrapping `rg --json`. It contains no provider name, hostname, package name, or pack path. Evidence: observer="text", confidence=RAW, exact path/line/hash.
5. observe/deps.py: `scan(closure, surface) -> list[Evidence]` parsing requirements*.txt, pyproject.toml, poetry.lock, uv.lock, package.json + lockfiles, composer.json/lock, pom.xml, build.gradle, *.csproj, go.mod, Gemfile.lock generically; matches resolved packages against surface.package_names. Resolution failure → DEPENDENCY_STATE_UNKNOWN, never "absent".
6. observe/ledger.py: every Evidence attaches to a Candidate; statuses limited to AFFECTED, NOT_AFFECTED_WITH_EVIDENCE, UNKNOWN, HUMAN_REQUIRED, EXCLUDED_WITH_EVIDENCE; a Candidate without status cannot be persisted (schema-level).
7. observe/resolver.py: claim-specific precedence tables keyed by claim_type (sdk_installed: lock > manifest; call_version: per_call > client_init > sdk_default > UNKNOWN; production_version: telemetry > sentinel > dynamic > static). No global precedence list anywhere.
8. store/sqlite.py: WAL, foreign_keys ON, tables runs/evidence/candidates/obligations/checks/artifacts; every row carries run_id and proof_scope_hash; artifacts written tmp → fsync → rename.
9. app/cli.py: `hops scan`, `hops exposure` in the ARCHITECTURE.md §4 format, with counts and each UNKNOWN's "close with" instruction. app/ is the ONLY place that imports packs/.
10. tests/unit/test_imports.py (core/observe/graph/obligations/verify/proof/store/sandbox never import packs/ or repair/) and tests/unit/test_no_provider_leak.py (grep those dirs for entries in tests/unit/provider_names.txt: google, googleads, google_ads, GoogleAds, gaql, ads_api) both pass.
11. tests/property: (a) ledger never persists unexplained candidates, (b) scan is byte-identical across two runs, (c) `rg` missing from PATH → TOOLING_MISSING error, never an empty ledger, (d) the same closure scanned with google_ads and _mock surfaces yields different ledgers and both are fully explained.
12. tests/fixtures/phase1/: ≥6 repos (python pinned v22, php REST /v22/ path, js dynamic ADS_API_VERSION, vendored sdk, unparsable file, monorepo workspace) with expected_candidates.json; all pass.

INVARIANTS: CLAUDE.md laws. No AI calls. No structural parsing. No ast-grep.
OPEN MIDDLE: module internals, output styling, SQLite details beyond the six tables.
APPROVAL BOUNDARIES: any dependency beyond stdlib/rich/hypothesis/pytest/jsonschema; any schema field rename.
EVIDENCE REQUIRED: `uv run pytest -q`; `hops scan` on each fixture; `sha256sum` of two consecutive ledger exports; output of test_no_provider_leak.
NON-GOALS: wrappers, Change Pack, verification, repair, GitHub.
TRAPS: (1) "not found" treated as "does not exist"; (2) hard-coding a hostname or package name in observe/ "just for now"; (3) dedup that loses provenance; (4) dict-order-dependent output; (5) a global evidence priority constant.
PROCESS: plan mode → dev/plan.md with OPEN QUESTIONS → stop.
```
