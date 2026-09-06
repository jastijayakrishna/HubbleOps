# Plan — Phase 3 — Wrapper Engine

## Scope and maturity

**Classification:** Build. Phase 2 passed its fresh gate, completed the first real-repo loop, and is
merged locally into `main` at `2b97be2` with tag `v0.2`. Phase 3 starts from that merge on
`phase-03-wrapper-engine`.

**Outcome:** HubbleOps finds provider usage hidden behind first-party wrappers, imports, factories,
inheritance, decorators, asynchronous calls, configuration, and constructed request text. The
structural channel remains a conservative source of evidence: every unresolved data-flow path names
the uncertainty and how to close it, while field and provider-contract validation stay deferred to
Phase 6.

**Appetite:** one phase branch. Stop after the Phase 3 gate and required real-repo loop. Do not build
dynamic capture, verification, obligations, repair, wrapper promotion, Java/C# rules, SCIP, or a
networked/LLM integration. No push, deployment, or release is authorized.

## Evidence and constraints

- `docs/ARCHITECTURE.md` §6.4 fixes ast-grep plus a language-neutral graph/walker, backward depth 5,
  query skeletons, explicit boundaries, and structural coverage reporting.
- P-006 fixes initial rule coverage to Python, PHP, JavaScript, and TypeScript. Other source
  languages retain the language-independent observers and receive `STRUCTURE_UNSUPPORTED` from the
  structural observer rather than silence.
- `docs/FAILURE_ATLAS.md` carries nine Phase 2 real-repo patterns. The six fixture families already
  present under `tests/fixtures/phase3` are required inputs, including the generated-marker,
  language-scoping, and lockfile cross-evidence prerequisites as well as the three structural cases.
- The frozen Observer interface remains `scan(closure, ctx)`. `ObserverContext` may carry normalized,
  injected rule bundles and run metadata; generic layers may not import or name a provider or pack
  path.
- The frozen Evidence schema and Observer contract cannot be changed in place. F-6 requires a
  proposal before AI triage can be exposed operationally.
- `ast-grep 0.45.0` was installed for this task at the npm global prefix and is verified by the
  elevated repository command environment. Runtime lookup accepts the executable path injected by
  the application or discovers it from `PATH`; its version and all active rule bytes enter the
  proof scope.

## Success measures

Engineering completion requires all Phase 3 Definition-of-Done checks and the repository gate to
pass. Product efficacy is not yet proven: after the gate, the real-repo loop must scan 2–3 public or
prospect repositories and classify every UNKNOWN. A useful Phase 3 reduces wrapper-related UNKNOWNs
without reducing total candidate recall, introducing silent language gaps, or closing contract
uncertainty.

Guardrails:

- candidate/evidence conservation and proof-scope tests do not regress;
- the pre-Phase-3 suite remains green;
- structural output is byte-identical for identical closure, rules, tool version, and source bytes;
- unsupported and unscanned structural coverage is explicit;
- AI-derived evidence cannot independently change candidate status.

The post-gate decision is: proceed to Phase 4 only on literal `GATE: PASS` plus a completed
real-repo loop; otherwise repair Phase 3 or preserve a typed UNKNOWN.

## Smallest complete design

### Rule bundles and proof binding

Each active pack supplies three tested rule families for each supported language: sink calls,
version carriers, and request-string sinks. Google Ads supplies
`rules/{python,php,javascript,typescript}.yml`; `_mock` supplies `rules/python.yml`. Every rule has a
stable id and a corresponding test under `rules/tests/` with must-match and must-not-match snippets.
The cases run through `ast-grep test` and a pack-local `sgconfig.yml`.

The application layer obtains rule bundles from the selected pack and normalizes them into
`ObserverContext`. It computes `rules_hash` from sorted logical language/rule identifiers and file
bytes, never absolute paths or filesystem order. The hash enters `ProofScope.rules_hash`. The
existing `hubbleops + full observation-pipeline fingerprint + ripgrep` scanner identity is preserved
and the installed ast-grep version is appended to it. Changing a scanner module, rule, ripgrep, or
ast-grep version therefore invalidates the proof key.

### Generic graph

`graph/imports.py` consumes ast-grep JSON captures for definitions, calls, imports, assignments,
classes, inheritance, decorators, and factory/registration references and normalizes them into
provider-neutral nodes and edges. Thin language-specific ast-grep extraction rules capture each
parameter and argument as an AST node, import sources, assignment right-hand sides, base classes,
decorators, and registration values. The generic Python code never tokenizes or parses raw source
syntax. The graph model contains source path, byte/line range, symbol, arity, captured
parameters/arguments, and edge kind. Serialization sorts every node, edge, path, and captured value,
and contains no absolute repository path.

Ast-grep, rather than a custom source parser, establishes every syntax node and capture boundary.
Normalization consumes only discrete captures and resolves already-captured local import references;
it must not infer provider meaning. Call-to-definition edges use permissive name-plus-arity matching
and retain every plausible target. Import edges refine local symbol resolution but never prune a
plausible global target.

### Structural observer and wrapper walk

`observe/structure.py` implements the frozen two-argument observer contract. It validates ast-grep
availability/version, builds the generic graph, executes the injected rules over exact `INSIDE`
files, and emits deterministic Evidence records.

For each sink hit, the containing definition is W1. A backward work queue tracks the carried
argument positions through definitions and callers for at most five call edges. A literal resolves
at that location; an assignment/imported constant continues through the graph; a config or env read
emits `UNKNOWN_CONFIG(key)`; an unresolved symbol, ambiguity, parse gap, or depth limit emits a named
UNKNOWN with a closing instruction. Same-name/same-arity overrides, subclass implementations,
decorated definitions, registry/factory values, and async definitions inherit wrapper status as a
conservative superset.

Every emitted wrapper-chain value records ordered hops, carried parameters, source ranges, and the
terminal resolution. Multiple plausible targets remain paths grouped under the originating call-site
candidate. Conflicting terminal resolutions make that one candidate UNKNOWN; they never become
separate independently AFFECTED candidates. Precision heuristics must not discard any path.

### Versions, request skeletons, and boundaries

Version-carrier hits resolve direct literals and constants/imports that reach a sink. Effective
versions are emitted as `call_version` with deterministic provenance. A carrier in a language not
named by its SurfaceSpec declaration remains a recall-layer reference and cannot become an AFFECTED
call version.

Strings reaching request sinks are reduced to ordered literal fragments and named holes using
ast-grep captures. Concatenation, f-string/interpolation, format, and template chains emit frozen
`claim_type="request_text"` evidence whose value carries the skeleton. Any hole emits
`UNKNOWN_QUERY_HOLE`; a hole-free request remains structurally resolved but gets
`CONTRACT_VALIDATION_DEFERRED`, because only Phase 6 may validate fields against the injected
ContractOracle.

Internal HTTP, queue, and RPC calls that carry a query or version across a process/service boundary
emit an `external_boundary` UNKNOWN naming the payload and a closing instruction. Boundary candidate
identity includes path, source range, and captured payload identity so distinct payloads in one file
cannot collapse. They are not silently treated as provider sinks.

### Coverage and failure behavior

Every `INSIDE` file is mapped to a normalized language id; unknown extensions and extensionless files
map to `unknown`. Every `INSIDE` file without an active rule bundle emits
`structure_unsupported` evidence and an UNKNOWN candidate. Structural coverage per language is
stored in the run's closure summary and printed in the Exposure Map, including supported,
unsupported, and unscanned counts.

Missing or incompatible ast-grep raises `TOOLING_MISSING` and produces no partial successful run.
Malformed ast-grep output and tool timeout also reject partial results. `hops scan --force` is the
explicit fail-closed continuation for missing, incompatible, malformed, or timed-out structure work:
every `INSIDE` file receives `file_unscanned` evidence naming the structural tool failure. A syntax
parse failure limited to one file emits `file_unscanned` for that file while other files remain
accounted for. Timeout evidence remains UNKNOWN and never becomes FAILED.

### AI residue triage and F-6

`observe/ai_triage.py` accepts only candidates still unresolved after deterministic structure work,
at most two source files, and exactly one question per invocation. A default-off, non-CLI application
gate is an explicit `enabled=False` input; disabled mode cannot call the injected client. Enabled-mode
unit tests use a fake client only. The module returns only `DERIVED_AI_EVIDENCE` and has no candidate
mutation or store authority. The CLI exposes no enabling flag in Phase 3.

Before the module lands, add P-009 to `dev/proposals.md`: evidence-producer attestation at the store
boundary so an untrusted caller cannot relabel AI output as observed evidence. The proposal compares
schema-bound attestation, trusted emitter capabilities, and keeping AI disabled. It remains pending
owner decision; Phase 3 does not amend a frozen schema or Observer contract. AI triage stays
operationally unreachable until an accepted design mechanically enforces provenance.

## Definition of done

1. Google Ads has Python/PHP/JavaScript/TypeScript sink, version-carrier, and request-string rule
   families. `_mock` has a minimal equivalent. Every rule id has positive and negative ast-grep test
   cases, and all pack rule tests pass under ast-grep 0.45.0.
2. Active rule bytes are included in `rules_hash`; ast-grep identity is composed with the existing
   HubbleOps/full-pipeline/ripgrep scanner identity. Mutating one scanner module or rule changes the
   proof key; filesystem order does not.
3. `graph/imports.py` creates a deterministic provider-neutral import/symbol graph from ast-grep
   output. Identical inputs serialize byte-identically. Local Python and TypeScript import cases
   connect to their definitions without excluding ambiguous name/arity matches.
4. The backward walk follows carried values through up to five call hops and covers gateway class,
   DI container, abstract adapter plus two subclasses, factory registry, decorator, async wrapper,
   configuration, and an added intermediate hop. A sixth required hop ends in a named depth UNKNOWN.
5. Literal version resolution, imported constant resolution, `UNKNOWN_CONFIG(key)`, unresolved
   variables, ambiguous targets, and language-scoped carriers have positive and negative tests.
6. F-string, concatenation, format, and JavaScript/TypeScript template requests emit `request_text`
   with ordered literal fragments and holes. Holes preserve `UNKNOWN_QUERY_HOLE`; hole-free
   structural requests preserve `CONTRACT_VALIDATION_DEFERRED`. No observer validates provider
   fields.
7. Queue, internal HTTP, and RPC payload transfer emits `external_boundary` UNKNOWN evidence naming
   the carried payload. Two boundary payloads in one file have distinct source-range/payload keys.
8. Missing/incompatible ast-grep fails with `TOOLING_MISSING`. Timeout or malformed output rejects
   partial results. Forced scan emits `file_unscanned` for every `INSIDE` file. Per-file parse
   failures emit `file_unscanned`; unsupported and unknown/extensionless languages emit
   `structure_unsupported`; Exposure Map prints structural coverage per language.
9. Metamorphic tests show that renaming a wrapper, moving it to another file, splitting a query
   across variables, and adding an intermediate hop leave canonical candidate/evidence semantics
   unchanged apart from expected source identities.
10. The fixture corpus contains at least ten named wrapper patterns plus all six Phase 2 real-repo
    families. Fixture tests assert required positive, negative, UNKNOWN, and prerequisite behavior.
    FA-004/005 retain correct generated-marker classification; FA-008 closes only the manifest
    dependency uncertainty from matching lock evidence; FA-009 preserves target compatibility as
    UNKNOWN without a content-hashed mapping or human decision.
11. `ai_triage.py` has an explicit default-off non-CLI application gate, has no CLI enablement, reads
    no more than two files, asks once, emits only `DERIVED_AI_EVIDENCE`, and cannot change a candidate
    status. Fake-client tests cover both gate states. P-009 records the unresolved attestation
    boundary before the file lands.
12. Generic layers contain no provider name, hostname, package name, or pack path; no generic layer
    imports `packs/`; no custom parser or build step is introduced.
13. The full pytest suite, Ruff, strict Pyright, diff check, import tests, provider-leak tests, rule
    tests, fixture tests, deterministic tests, and required manual demonstrations pass.
14. After implementation, run a fresh gate, then the required real-repo loop, then an unconditional
    final fresh gate on the post-loop tree. `dev/context.md`, `dev/tasks.md`, `docs/BUILD_ORDER.md`,
    and the Phase 3 prompt record completion only after that final audit returns literal
    `GATE: PASS`.

## Verification

Required executed evidence:

- `ast-grep test -c hubbleops/packs/google_ads/sgconfig.yml --skip-snapshot-tests`
- `ast-grep test -c hubbleops/packs/_mock/sgconfig.yml --skip-snapshot-tests`
- focused graph, structure, fixture, failure-mode, coverage, AI-boundary, and metamorphic pytest runs;
- a printed DI wrapper chain with every hop and carried parameter;
- a forced scan with ast-grep unavailable showing one `FILE_UNSCANNED` result per `INSIDE` file;
- a normal scan containing an unsupported language and `STRUCTURE_UNSUPPORTED` rather than silence;
- two identical scans/graph exports compared byte-for-byte and metamorphic canonical semantics;
- `uv run pytest -q`;
- `uv run ruff check .`;
- `uv run ruff format --check .`;
- `uv run pyright`;
- `uv run pytest -q tests/unit/test_imports.py tests/unit/test_no_provider_leak.py`;
- `git diff --check`;
- gate-audit adversarial attempts against Laws L1, L3, L4, L5, and L10.

Record exact commands, outputs, and exit codes. A check not run is not a pass.

## Authority and invariants

Autonomous authority covers inspection, rule/test/fixture creation, generic implementation,
application wiring, narrow schema-compatible evidence/resolver additions, deterministic refactoring,
the ast-grep installation completed with explicit tool approval in this task, and local commits
implied by the user's explicit instruction to merge Phase 2 and execute Phase 3. Push remains
explicitly prohibited.

Human approval is required for accepting P-009; changing any frozen schema or contract; exposing AI
triage; adding SCIP, a framework/service/database, Java/C# rules, credentials, material network cost,
deployment, release, push, or a Phase 4 capability. None is authorized here.

Non-negotiable throughout:

- no candidate or structural gap disappears;
- UNKNOWN closes only with new evidence or a recorded human decision;
- same inputs produce byte-identical outputs;
- generic layers remain provider-neutral and receive pack parts by injection;
- unsupported, missing-tool, timeout, parse, ambiguity, config, query-hole, and depth failures are
  explicit and fail closed;
- AI evidence alone cannot change status;
- no field validation, build, dynamic execution, network scan, or repair occurs;
- unrelated work is preserved and nothing is pushed.

## Risks and alternatives

- **Highest risk — false completeness from graph ambiguity.** Prefer a conservative superset and
  explicit ambiguity over pruning. Metamorphic, multi-target, and depth fixtures falsify misses.
- **Ast-grep output drift.** Pin verified compatibility behavior, fingerprint the actual version,
  reject incompatible output, and test malformed JSON/subprocess failures.
- **Rule precision creates recall loss.** Rules detect broad structural families; deterministic
  graph and resolver layers explain matches. Negative rule tests constrain obvious noise without
  permitting silent exclusion.
- **Path/import resolution differs across ecosystems.** Local import edges refine the graph, while
  permissive name-plus-arity edges remain as a fallback. No build or package-manager execution is
  required.
- **AI provenance remains forgeable at the record API.** Keep AI operationally unreachable and raise
  P-009 instead of weakening or silently changing the frozen trust boundary.
- **Alternative: custom parsers/tree-sitter bindings.** Rejected by the frozen stack and no-custom-
  parser invariant.
- **Alternative: SCIP now.** Rejected as out of appetite and approval-bounded.
- **Alternative: text-only heuristics.** Rejected because they cannot provide syntax-bounded wrapper
  chains and would duplicate, rather than add an independent observation channel.

## Release and learning

Phase 3 is a local, unreleased branch. Rollback is removal of Phase 3 commits while `main` remains at
the Phase 2 merge. There is no migration or production data change. After a literal fresh-session
`GATE: PASS`, run the real-repo loop against 2–3 repositories. Every UNKNOWN is classified as closed
with evidence, closed by recorded human decision, preserved with an instruction, or a new anonymized
pattern. New patterns extend fixtures/rules/falsifiers before Phase 4; no repository-specific rule is
allowed. Rerun the full fresh gate on the post-loop tree unconditionally, even when the loop changes
no tracked byte. Phase 3 is complete only when that final audit returns literal `GATE: PASS`.

## Architecture record

No new ADR is required if implementation stays within frozen §6.4 and accepted P-006. P-009 is the
required pending architecture proposal because evidence attestation changes a frozen trust boundary.

## Open questions

None. The frozen architecture, accepted P-006, Phase 3 prompt, nine Failure Atlas rows, and existing
fixtures resolve the implementation choices needed for this phase. P-009 is intentionally not an
open implementation question because AI remains disabled until the owner decides it.
