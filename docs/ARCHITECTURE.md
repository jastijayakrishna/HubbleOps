# HubbleOps — ARCHITECTURE.md

**Status: FROZEN v1.** Section numbers are referenced by the prompt pack; do not renumber. Changes to anything marked FROZEN go through `dev/proposals.md`, never in place.

---

## §1. Mission and product promise

HubbleOps is a Truth Engine plus an independent Verification Authority for third-party API migrations. Provider #1 is Google Ads (v22 → v25). The customer buys **two proofs**, not a patch:

1. **Completeness** — "You found every observable usage, and explicitly listed what you could not know."
2. **Blast radius** — "Your change broke nothing else, and here is every module that could have been affected and what tested it."

Customer loop:

```
Connect repo → Exposure Map → provider change → Impact Report → Migration PR → Proof Pack → merge → Backslide Guard → memory makes the next migration cheaper
```

Catastrophic failure is **FALSE VERIFIED**, not UNKNOWN:

```
Cost(FALSE_VERIFIED) ≫ Cost(UNKNOWN) > Cost(HUMAN_REQUIRED) > Cost(correct repair)
```

---

## §2. First principles and laws

**Axiom 1 — A proof is a function of its inputs.** `Verdict = V(tree_hash, dep_resolution_hash, contract_hash, tool_versions, verifier_image_hash)`. Anything outside the hash is outside the proof.

**Axiom 2 — Rice's theorem bounds static analysis.** Runtime-selected versions and dynamically built requests are undecidable statically. UNKNOWN is a required output; dynamic and provider-side evidence are structural, not optional.

**Completeness math.** For a usage `u`, `P(miss u) = Π_i P(miss u | observer i)` only if observers fail independently; therefore observers must span orthogonal channels (text, structure, dependencies, execution, provider telemetry). The completeness claim is: *every provider-observed (service, method, version) tuple maps to ≥1 explained candidate, and every candidate has a status.*

**Blast-radius math.** `Δ` = changed symbols; `R = reach(G, Δ)` over the import/call graph; `C: test → files` from coverage; `UNKNOWN_BLAST = R \ ⋃C(passing frozen tests)`.

**Laws (FROZEN):**
- L1 `UNEXPLAINED_CANDIDATES = 0` at every persisted state.
- L2 `UNKNOWN ≠ UNEXPLAINED`. A genuinely undecidable UNKNOWN with a precise closing instruction is correct. Gates target UNEXPLAINED = 0, never UNKNOWN = 0.
- L3 UNKNOWN conservation: `UNKNOWN_after ⊆ UNKNOWN_before ∪ evidence_linked ∪ decision_linked`.
- L4 Proof is bound to ProofScope. New SHA → old proof dead → new proof required.
- L5 Dependency direction: `app/` selects a ProviderPack and passes its parts into generic layers as parameters. Generic layers never import `packs/` and contain no provider names.
- L6 `verify/` never imports `repair/`, `packs/`, or `sandbox/runner`; it uses `sandbox/verifier_image` only. The system that changes code never judges the change.
- L7 Memory may reduce work, never proof.
- L8 Verdicts: `VERIFIED_FOR_SCOPE | HUMAN_REQUIRED | UNKNOWN | FAILED`. Never `SAFE`.
- L9 Fail closed: parse failure → `FILE_UNSCANNED`; missing tool → `TOOLING_MISSING`; resolution failure → `*_UNKNOWN`; timeout → `UNKNOWN(reason)`.
- L10 AI-derived evidence is `DERIVED_AI_EVIDENCE`; it can never alone change a status or close an UNKNOWN.
- L11 Candidate tests are evidence; frozen base-SHA tests are proof.

---

## §3. System architecture and the Provider Pack contract

```
                app/  (pack registry + CLI)  ── selects ──►  packs/{google_ads,_mock}
                  │ injects SurfaceSpec, RuleSet, ContractOracle, ChangeCompiler,
                  │ TelemetryAdapter, CaptureHooks, RepairTransforms, RepairTools, Falsifiers
                  ▼
 ┌──────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   trust   ╔═══════════════════╗  ┌──────────────┐
 │ closure/ │─►│ observe/ +    │─►│ obligations/  │─►│ repair/       │ boundary  ║ verify/           ║─►│ proof/       │
 │ (§6.1)   │  │ graph/ (§6)   │  │ (§7.2)        │  │ + sandbox/    │ ════════► ║ (§9, own image)   ║  │ (§16–§18)    │
 └──────────┘  └───────────────┘  └───────────────┘  │ (§8)          │           ╚═══════════════════╝  └──────────────┘
                      ▲                              └───────────────┘                    │
                      └──────────────── store/ memory (§14) ◄─────────────────────────────┘
```

Five generic components, one pack interface. Packs are data + small hook classes; provider quirks never enter generic code.

### §3.1 ProviderPack contract (FROZEN)

```python
class ProviderPack(Protocol):
    name: str
    surface: SurfaceSpec                      # identifiers, hosts, package names, version carriers,
                                              # request languages, sink arg positions, config/env keys
    def rules(self, language: str) -> RuleSet          # ast-grep YAML rule files: sinks, version carriers, request-string sinks
    contract: ContractOracle                  # catalog(version), diff(v_from, v_to), validate(request, version)
    changes: ChangeCompiler                   # sources → Change Pack (JSONL, hashed, provenance, cross-checked)
    telemetry: TelemetryAdapter               # provider usage export → [(service, method, version)]
    def capture_hooks(self, language: str) -> CaptureHooks   # interceptor/patch code loaded by observe/dynamic loaders
    def repair_transforms(self) -> list[Transform]           # deterministic, precondition-checked
    def repair_tools(self) -> list[ToolSpec]                 # e.g., provider's own dev-assistant plugin for the repair image
    def falsifiers(self) -> list[Falsifier]                  # adversarial checks keyed by failure class
```

| Slot | Google Ads | `_mock` (architecture test) |
|---|---|---|
| Identifiers / hosts | `google-ads`, `googleads.googleapis.com` | `mockprov`, `api.mockprov.test` |
| Version carriers | `get_service(version=)`, `get_type(version=)`, URL `/vNN/`, namespace `google.ads.googleads.vNN`, env/config keys | `client(version=)`, URL `/vN/` |
| Request language | GAQL | key=value strings |
| Contract source | googleapis protos + GoogleAdsFieldService catalog + guides + release notes | hand-written JSON |
| Validation oracle | `validate_only` on Search / mutate; catalog field checks | in-memory validator with one known-bad request |
| Telemetry | Cloud Console methods/versions export | CSV |
| Capture hooks | gRPC interceptor + HTTP patch (py/php/node) | function patch |
| Repair transforms | version literal, SDK pin, namespace rename, REST path | version literal |
| Repair tools | Google Ads API Developer Assistant plugin | none |

Adding a provider = filling the table and writing rules. Generic code is untouched; `packs/_mock` proves it in CI (§13).

### §3.2 Observer contract (FROZEN)

```python
class Observer(Protocol):
    name: str                                   # "text" | "deps" | "structure" | "dynamic" | "telemetry" | "sentinel"
    def scan(self, closure: SourceClosure, ctx: ObserverContext) -> list[Evidence]
# ObserverContext carries only pack-provided parts (surface, rules, hooks) and run metadata. No pack import.
```

---

## §4. Exposure Map (customer-facing output format, FROZEN)

```
HubbleOps — GOOGLE ADS EXPOSURE MAP

Repository   acme/ad-platform        Commit  84ea29d
Detected     Google Ads API v22      Target  v25
ProofScope   ps_3f9a…                Pack    google_ads@<changes_hash>

────────────────────────────────────────────────────────
DISCOVERY
  Candidates found        67
  Affected                21
  Not affected (evidence) 39
  UNKNOWN                  7
  Unexplained              0
  Production services accounted for   7 / 7

────────────────────────────────────────────────────────
AFFECTED
  src/google/client.py:83   explicit service version v22
    client.get_service("GoogleAdsService", version="v22")
    evidence: source literal · observer: structure · confidence: PROVEN
  workers/upload.py:142     REST path /v22/
    evidence: endpoint literal · observer: text · confidence: PROVEN
  reports/campaign.py:71    query selects field removed in v25
    evidence: request skeleton · observer: structure+dynamic · confidence: PROVEN
  …

────────────────────────────────────────────────────────
UNKNOWN
  legacy/config.py:22       version loaded from ADS_API_VERSION at runtime
    close with: production telemetry export, sentinel event, or `hops decide`
  …

────────────────────────────────────────────────────────
NOT AFFECTED (with evidence)   39   [expand]
```

Rules: every UNKNOWN prints a *close with* instruction; counts always include `Unexplained`; the map is deterministic for a given ProofScope.

---

## §5. Data model (FROZEN schemas in `core/schemas/*.json`)

All IDs are SHA-256 of canonical JSON. All records carry `run_id` and `proof_scope_hash`.

**Evidence** `{id, claim_type, observer, repo_sha, path, line_start, line_end, source_hash, value, provider_subject, dependency_context_hash, derivation ∈ {OBSERVED, DERIVED_DETERMINISTIC, DERIVED_AI_EVIDENCE}, confidence ∈ {RAW, PROVEN, DOCUMENTED, INFERRED}}`

**Candidate** `{id, provider, evidence_ids[], status ∈ {AFFECTED, NOT_AFFECTED_WITH_EVIDENCE, UNKNOWN, HUMAN_REQUIRED, EXCLUDED_WITH_EVIDENCE}, reason, close_with?}` — status is required at schema level.

**Obligation** `{id, provider_change_id, evidence_ids[], current_state, required_state, repair_class ∈ {DETERMINISTIC, PROVIDER_TOOL, AGENT, HUMAN, PRESERVE_UNKNOWN}, verification_method, status}`

**ProofScope** `{repo_sha, tree_hash, dependency_resolution_hash, build_command, build_config_hash, provider_contract_hash, rules_hash, scanner_version, verifier_version, verifier_image_hash}` — the hash of this record is the proof key.

**Receipt** `{proof_scope, candidates_summary, obligations, changed_files, migration_audit, oracle_results[], blast_radius, request_shape_differential, response_consumer_check, frozen_baseline_tests, candidate_tests, falsifiers, unknowns[], unknown_conservation, verdict, reasons[]}`

**Claim-specific evidence resolution (FROZEN principle):** no global precedence. Per `claim_type`:
- `sdk_installed`: lock > manifest
- `call_version`: per_call_override > client_init > sdk_default > UNKNOWN
- `production_version`: telemetry > sentinel > dynamic > static
- `request_text`: dynamic > structure(skeleton) > text

---

## §6. Truth Engine

### §6.1 Source Closure
Enumerate the universe before scanning. Every path is classified `INSIDE | GENERATED | VENDORED | SUBMODULE | EXTERNAL_BOUNDARY | UNSCANNED`. Unreadable/unclassifiable → `FILE_UNSCANNED` candidate. Absence is never silence.

### §6.2 Observer A — Text (ripgrep)
`rg --json` over the closure using `SurfaceSpec` identifiers: package names, hosts, version literals, service/method names, request-language anchors (e.g., `FROM <resource>`), config/env keys, generated namespaces. Recall-first; false positives are cheap. Never removed as the semantic layer improves.

### §6.3 Observer B — Dependencies
Generic lockfile/manifest parsers (Python, JS, PHP, Java, C#, Go, Ruby) resolve installed packages; matched against `surface.package_names`. Resolved SDK version bounds which API versions *can* execute. Resolution failure → `DEPENDENCY_STATE_UNKNOWN`.

### §6.4 Observer C — Structure (ast-grep + graph) and the Wrapper Engine
- Rule families from the pack: sinks, version carriers, request-string sinks. Rules are tested with must-match / must-not-match snippets.
- `graph/imports.py`: import/symbol graph (definitions, calls, imports), language-agnostic.
- **Wrapper walk (backward, k = 5):** sink → enclosing function W₁ → carried parameters (those flowing into version/request/config args) → callers by permissive name+arity match → resolve arguments: literal → resolved; variable → next hop; config/env read → `UNKNOWN_CONFIG(key)`. Subclasses, interface implementations, decorator wrappers, and factory registrations inherit wrapper status.
- **Query skeletons (forward):** concatenation/f-string/format/template chains → skeleton with literal fragments and holes; holes → `UNKNOWN_QUERY_HOLE`. The observer emits skeletons; field validation happens in the Obligation Engine via the injected `ContractOracle`.
- **Boundaries:** internal HTTP/queue/RPC hops carrying a request or version → `EXTERNAL_BOUNDARY` candidate naming the payload.
- **AI residue filter:** only for candidates unresolved after the walk; ≤ 2 files of context; one question; output `DERIVED_AI_EVIDENCE`.
- **Promotion:** wrappers confirmed by dynamic/sentinel stack traces are written to `.hubbleops/surface.yml` as repo-local rules; next scan resolves them in one hop (§19).

### §6.5 Observer D — Dynamic capture (test time)
The customer's own test suite runs in the sandbox (§8.1) with pack-supplied capture hooks loaded by generic per-language loaders (Python `sitecustomize`, PHP `auto_prepend_file`, Node `--require`). Events (versioned schema): `{version, service, method, request_text, request_type, stack[], ts}`. Each stack is an observed wrapper chain; chains absent statically → `OBSERVED_NOT_STATIC` candidates. No events with call sites present → `UNKNOWN_DYNAMIC`.

### §6.6 Observer E — Provider telemetry and sentinel
- **Telemetry:** the pack's adapter parses the provider's usage export (Google: Cloud Console methods/versions) into `(service, method, version)`. Reconciliation: each tuple ↦ ≥1 explained candidate; unmatched → `TELEMETRY_UNEXPLAINED`.
- **Sentinel (production sensor, §15):** customer-installed package emitting the same event schema from production; `observer="sentinel"`.

### §6.7 Candidate Ledger and resolver
All observers feed one ledger. A candidate is never deleted; it is explained. The resolver applies §5 claim tables. Deterministic categorizers run before any AI. Output: Exposure Map (§4).

---

## §7. Provider intelligence

### §7.1 Change Pack
Per provider, per version pair: `data/changes_<from>_<to>.jsonl`. Sources for Google Ads: (a) googleapis proto diff; (b) GoogleAdsFieldService catalogs for both versions (selectable, filterable, sortable, data_type, selectable_with); (c) upgrade guides + release notes as structured claims; (d) client-library compatibility. Every fact: `source_url, retrieved_at, sha256, confidence`. Cross-check: (a)∧(b) → `PROVEN`; only (c) → `DOCUMENTED`; disagreement → `UNKNOWN_PROVIDER_CONTRACT`. No LLM adjudication. Offline-reproducible from cached sources; hash enters ProofScope.

### §7.2 Obligation Engine
`build(change_pack, ledger, oracle) → Obligation[]`. Every AFFECTED candidate → ≥ 1 obligation with `file:line, current_state, required_state, repair_class, verification_method`. Request-skeleton literals are validated against the target catalog via the injected oracle here, not in observers. UNKNOWNs → `PRESERVE_UNKNOWN` obligations. Agents receive obligations, never "upgrade the repo".

---

## §8. Sandbox and Repair Worker

### §8.1 Sandbox (`sandbox/`)
`runner, image, limits, network, mounts, capture`. git worktree + rootless docker/podman; no host env leakage; network default-deny with allowlist; wall-clock timeout; CPU/mem limits; full command log. Serves dynamic capture and repair. **`sandbox/verifier_image.py` is a separate image/config for verification; verifier isolation is never a mode of the repair runner.**

### §8.2 Repair Worker (`repair/`, untrusted)
Order: pack `repair_transforms()` (deterministic, precondition-checked) → pack `repair_tools()` (provider's own assistant) → coding agent (Claude Code) → human. The agent gets obligations + bounded evidence + Change Pack excerpts; tool allowlist excludes push, non-allowlisted network, and `verify/`. Output: **candidate SHA** and artifacts; the agent's change manifest is a HINT, never truth. Loop: repair → verify → failure evidence → one retry → stop. Exit code never encodes a verdict.

---

## §9. Verification Authority (`verify/`, own image)

Inputs: base SHA, candidate SHA (read-only), Change Pack, ProofScope, injected pack. Never reads agent logs, confidence, or manifests as truth.

- **A. Migration audit** — independent rescan with all observers; old versions/namespaces/endpoints/removed fields extinct or explained; obligations reconciled one by one.
- **B. Contract oracle** — every captured request → `ContractOracle.validate(request, target)` (Google: `validate_only` on Search and mutate shapes; field checks vs target catalog). Result lines carry request hash + timestamp. `ORACLE_UNAVAILABLE` caps the verdict at UNKNOWN.
- **C. Blast radius** — `Δ` from `git diff` → definitions; `R = reach(G, Δ)`; **diff containment** (each hunk ↦ obligation id or `COLLATERAL(reason)`, else FAIL); **frozen baseline tests** (copied from base SHA) run against candidate with coverage → `frozen_baseline_tests_pass`; candidate tests run separately (evidence only); `UNKNOWN_BLAST = R \ ⋃C(passing frozen tests)`.
- **D. Behavior** — `request_shape_differential_pass` (base vs candidate capture; every changed shape ↦ obligation) and `response_consumer_check_pass` (no read of a removed/renamed field's old name).
- **E. Falsifiers** — pack falsifiers matching detected failure classes (Google: per-call override residue, SDK+REST coexistence, dynamic version config, generated namespace, partial failure, retry-after-partial-mutate, stream chunk boundaries, INT64).
- **F. UNKNOWN conservation** — L3.

### §9.1 Verdict rule (FROZEN)

```
VERIFIED_FOR_SCOPE  iff  audit_pass
                     ∧ oracle_all_accepted
                     ∧ zero_unexplained_hunks
                     ∧ UNKNOWN_BLAST = ∅
                     ∧ request_shape_differential_pass
                     ∧ response_consumer_check_pass
                     ∧ frozen_baseline_tests_pass
                     ∧ falsifiers_pass
                     ∧ unknown_conservation_pass
HUMAN_REQUIRED      iff  all of the above except UNKNOWN_BLAST ≠ ∅   (module list attached)
UNKNOWN             iff  ORACLE_UNAVAILABLE or any input unresolvable
FAILED              iff  any check FAIL                                (reasons attached)
```
`verify/verdict.py` is pure and total. Property tests: every input maps to exactly one verdict; flipping any single pass-flag from a VERIFIED input never yields VERIFIED.

---

## §10. Repository structure and dependency direction

```
hubbleops/
  app/          registry.py cli.py            # ONLY importer of packs/
  core/         evidence.py candidate.py obligation.py proof_scope.py receipt.py schemas/
  closure/      source_closure.py
  observe/      text.py deps.py structure.py ai_triage.py dynamic/{loaders,schema.json,runner.py} telemetry.py ledger.py resolver.py
  graph/        imports.py radius.py coverage.py
  obligations/  engine.py
  sandbox/      runner.py image.py limits.py network.py mounts.py capture.py verifier_image.py
  repair/       orchestrator.py deterministic.py agent.py
  verify/       audit.py oracle.py radius.py behavior.py falsify.py conserve.py verdict.py
  proof/        receipt.py pr_body.py guard.py
  store/        sqlite.py artifacts.py facts.py bindings.py decisions.py reverse_index.py
  packs/        _protocol.py _mock/ google_ads/{surface.yaml,rules/,contract.py,changes.py,telemetry.py,capture/,repairs/,falsifiers.py,data/,fixtures/}
packages/hubbleops-sentinel/                   # never imports hubbleops.*
tests/         unit/ property/ fixtures/ adversarial/ heldout/ integration/
docs/          ARCHITECTURE.md BUILD_ORDER.md FAILURE_ATLAS.md
dev/           plan.md context.md tasks.md proposals.md
```

Enforced by tests: `test_imports.py` (generic layers never import `packs/` or `repair/`; `verify/` never imports `sandbox/runner`, `store/facts`, `store/bindings`), `test_no_provider_leak.py` (no provider names in generic layers), `test_isolation.py` (verifier image cannot read repair paths), sentinel import test.

---

## §11. Stack (FROZEN unless proposed)

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12+ | fastest iteration; provider tooling is Python; hot loops are native binaries |
| Text scan | ripgrep | fast, language-independent |
| Structure | ast-grep (tree-sitter) + YAML rules | polyglot, declarative rules shipped by packs, library + CLI, no custom parser |
| Precise semantics (optional) | SCIP indexes behind a flag | compiler-accurate when a build exists |
| Graph / radius | NetworkX + coverage.py / c8 / jacoco / phpunit coverage | deterministic test-impact mapping |
| State | SQLite WAL + content-addressed artifacts | replayable, single-host |
| Isolation | git worktree + rootless docker/podman | hard boundary, no orchestration |
| Repair | pack tools + Claude Code | outsource provider expertise and edit generation |
| Quality | uv, ruff, pyright strict, pytest, hypothesis | invariants as property tests |
| Records | JSON Schema, SHA-256 IDs | human-readable, diffable |

Not in V0: Kubernetes, Kafka, Postgres, graph DB, vector DB, custom parser, custom model, agent swarm, dashboard.

---

## §12. Engineering practices

Deterministic (sort, seed, stamp), idempotent, replayable (`hops replay <run_id>`), content-addressed, fail-closed (L9), atomic artifacts (tmp → fsync → rename), timeouts and resource limits on every external process, structured logs keyed by `run_id, component, candidate_id, obligation_id, duration, outcome`, versioned interfaces recorded in ProofScope, no `except: pass`, no hidden global state, no "best effort" that changes safety semantics.

---

## §13. Testing strategy

- **Unit** — small deterministic logic.
- **Property (Hypothesis)** — L1, L3, L4 (receipt never reused across SHAs), determinism, incremental == clean (scan and verify), verdict totality and single-flip.
- **Metamorphic** — rename/move wrapper, split query, add hop → affected set unchanged.
- **Fixtures** — `tests/fixtures/` are the spec; every real UNKNOWN pattern becomes one (anonymized). 20-case fast gate; full corpus pre-release.
- **Adversarial** — deliberately wrong migrations; 100% rejected; nightly red-team; any escape is P0 and a permanent fixture.
- **Verifier mutation** — corrupt verified candidates nightly; verifier must catch all.
- **Pack conformance** — `packs/_mock` passes the full pipeline with zero generic-layer diff.
- **Held-out** — ugly repos never used during development.

---

## §14. Memory

Four stores, one rule (L7):

- **Fact cache** — `(blob_hash, rules_hash, analyzer_version) → Evidence[]`. Incremental scan = changed blobs + import fanout(k).
- **Binding store** — wrapper chains with `depends_on = {blob hashes}`; invalidated iff any hash changes.
- **Decision registry** — human resolutions keyed to the blob hash of the source location; never re-asked while unchanged.
- **Reverse impact index** — `provider_subject → bindings → files (→ repos)`; a new Change Pack triggers only affected files.

Cost targets: `repo update ≈ |changed blobs| + |fanout|`; `provider update ≈ |changed subjects| × |affected bindings|`. Incremental verification recomputes only the causal radius but **always** binds the Receipt to the new ProofScope. Memory never feeds `verify/` directly.

---

## §15. Sticky mode (production sensor and compounding assets)

- **Sentinel** (`packages/hubbleops-sentinel`) — standalone, provider adapters inside it (Google: gRPC interceptor or logging-config install), emits the §6.5 event schema from production. Never imports `hubbleops.*`; output is observational evidence, never a verdict.
- **Live API inventory** — what actually calls the provider, by version/method/wrapper chain, always current.
- **Growing surface memory** — `.hubbleops/` rules, bindings, decisions make migration *n+1* cheaper only with the engine that reads them.
- **Cumulative backslide guard** — every retired construct ever migrated stays guarded (§18).
- **Sunset calendar + Impact Report** — provider release → scoped impact via the reverse index.
All memory exports as plain YAML/JSON. Stickiness by value, not lock-in.

---

## §16. Proof Pack / Receipt format (FROZEN)

```
HubbleOps PROOF PACK — Google Ads migration v22 → v25

Base       84ea29d          Candidate   a903122
ProofScope ps_3f9a…         Verifier image  sha256:…
Contract   google_ads@<changes_hash>

────────────────────────────────────────────
DISCOVERY
  candidates 67 · affected 21 · not affected (evidence) 39 · UNKNOWN 7 · unexplained 0
  production services accounted for 7 / 7

OBLIGATIONS
  21 total · 21 resolved · 0 open · 0 unexplained

MIGRATION AUDIT                              PASS
  old SDK constraint NONE · explicit v22 usage NONE · v22 REST endpoints NONE · removed subjects NONE

CONTRACT ORACLE (Google Ads v25, validate_only)
  31 / 31 search requests ACCEPTED · 8 / 8 mutate shapes ACCEPTED   [request hashes + timestamps attached]

BLAST RADIUS
  changed symbols 6 · reachable modules 19 · covered by frozen tests 19 · UNKNOWN_BLAST none
  diff containment: 14 hunks → 14 obligations · 0 collateral · 0 unexplained
  request-shape differential PASS · response-consumer check PASS

TESTS
  frozen baseline 431 PASS / 2 SKIP (proof) · candidate 438 PASS / 2 SKIP (evidence)

FALSIFIERS  8 / 8 PASS

UNKNOWN
  7 preserved, each with closing instruction     [list]
  conservation PASS

VERDICT   VERIFIED_FOR_SCOPE
```
`receipt.json` carries the same content machine-readably. A `HUMAN_REQUIRED` receipt lists `UNKNOWN_BLAST` modules by name.

---

## §17. PR, GitHub App, merge revalidation

- PR opened only for `VERIFIED_FOR_SCOPE` or `HUMAN_REQUIRED` (labeled, list attached). Body = receipt.md. `receipt.json` attached as a check artifact.
- Status check keyed to the **exact** candidate SHA. Never green for `HUMAN_REQUIRED`.
- **Any new commit to the PR branch invalidates the previous Receipt and ProofScope and triggers a full `hops verify <base_sha> <new_candidate_sha>`.** After §14's incremental verification is proven equivalent to clean verification under causal invalidation, the incremental path may be used, but the Receipt is always bound to the new ProofScope.
- `merge_group` supported; the exact merge-group SHA is verified.
- Permissions: `contents:read`, `pull_requests:write`, `checks:write` only.

---

## §18. Backslide Guard

`proof/guard.py` generates `.github/workflows/hubbleops-guard.yml` running `hops guard`: ripgrep-only, < 2 s, over `.hubbleops/retired.yml` (patterns supplied by the pack when the migration is verified). Reintroduction of a retired version, namespace, field, or endpoint fails CI. No AI, no deep scan. Every migration permanently increases protection.

---

## §19. Repo-resident memory (`.hubbleops/`) and wrapper promotion

Committed with the PR, reviewable by the customer:
- `surface.yml` — promoted wrapper rules (ast-grep form) with provenance (run id, evidence ids), revocable.
- `bindings.json` — wrapper chains with `depends_on` hashes.
- `decisions.yml` — `hops decide <unknown_id> --value … --by …`, keyed to blob hash.
- `retired.yml` — cumulative retired surface for the guard.

Promotion rule: a wrapper becomes a repo-local rule when confirmed by dynamic or sentinel stack evidence (or a recorded decision). Next scan resolves it in one hop.

---

## §20. Scalability path

- 1–10 repos: CLI + local runs.
- 10–100: GitHub App; runs on a queue; SQLite per run; Postgres only for the run index.
- 100+: reverse index drives provider-change fan-out; SCIP caches per ProofScope. Generic code unchanged.

---

## §21. Frozen vs open, and build order

**FROZEN:** schemas (§5), ProviderPack and Observer contracts (§3), verdict rule (§9.1), laws (§2), dependency direction (§10), Exposure Map and Receipt formats (§4, §16).

**OPEN:** language adapters, rule sets, capture hooks, falsifiers, agent prompts, pack data ingestion, storage backend, UI.

**Build order** (details in `docs/BUILD_ORDER.md`): Phase 1 closure + text/deps + ledger + Exposure Map → Phase 2 Google Ads pack + Change Pack (offline gate) → *real-repo loop begins and never stops* → Phase 3 wrapper engine → Phase 4 sandbox + dynamic capture + sentinel + telemetry → Phase 5 verifier (before repair; adversarial gate) → Phase 6 obligations + repair worker → Phase 7 Proof Pack + PR + guard + `.hubbleops` → Phase 8 incremental memory → Phase 9 mock-pack conformance → Phase 10 pilot hardening.

**Definition of V0 success:** a real repository goes `Exposure Map → obligations → candidate → independent audit → oracle → blast radius → falsifiers → Proof Pack → PR` and the customer says: *"Yes. I would pay so I never have to deal with this again."*
