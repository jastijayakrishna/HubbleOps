# Proposals

The **only** place a change to the frozen surface may be requested. Nothing frozen is ever
redesigned in place.

Frozen surface (from [CLAUDE.md](../CLAUDE.md)):

- **Schemas** — `Evidence`, `Candidate`, `Obligation`, `ProofScope`, `Receipt` (`core/schemas/*.json`)
- **Interfaces** — `Observer`, `ProviderPack` (and sub-protocols), `Memory`
  (`docs/ARCHITECTURE.md` §3, §5)
- **The verdict function** — `verify/verdict.py`

Also record here: any new dependency, framework, database, or service outside the `CLAUDE.md` stack
list, and any generic-layer change demanded by [Phase 9](../prompts/phases/09-mock-pack-conformance.md).

Nothing here is approved until a human writes a decision on it.

---

## Template

### P-NNN — <title>

| | |
|---|---|
| **Raised** | YYYY-MM-DD, Phase N |
| **Touches** | which frozen item / which stack rule |
| **Status** | OPEN / ACCEPTED / REJECTED |

**What forced this.** The concrete case that cannot be handled within the current frozen surface.
A hypothetical is not a forcing case.

**Proposed change.** Exact, minimal.

**Blast radius.** What else must change; which gates must be re-run; whether existing Receipts
survive (they usually do not — a contract change moves ProofScope).

**Alternatives rejected, and why.**

**Decision.** Who, when, what.

---

## P-001 — PyYAML as a project dependency

| | |
|---|---|
| **Raised** | 2026-09-02, Phase 1 |
| **Touches** | the `CLAUDE.md` stack list (dependencies beyond stdlib/rich/hypothesis/pytest/jsonschema) |
| **Status** | ACCEPTED |

**What forced this.** Phase 1's DEFINITION OF DONE names `packs/google_ads/surface.yaml` and
`packs/_mock/surface.yaml` by filename. YAML is not optional elsewhere either: Phase 3 ships
ast-grep rule files as YAML, and Phase 7 commits `.hubbleops/surface.yml`. The standard library has
no YAML reader.

**Proposed change.** Add `pyyaml>=6.0.2` to `[project.dependencies]`. Load with `yaml.safe_load`
only; never `yaml.load`.

**Blast radius.** `app/registry.py` (reads a pack surface) and `observe/deps.py` (parses
`pnpm-lock.yaml`) import it. `pyyaml` is pure data parsing with no runtime services, so it does not
move ProofScope beyond the `scanner_version` string it already records. No existing Receipts.

**Alternatives rejected, and why.** A hand-written YAML-subset reader in `core/` avoids the
dependency but is a custom parser we would own and have to grow three times (surface specs, ast-grep
rules, repo-resident memory) — the exact thing §11 says is not in V0.

**Decision.** Accepted by the repository owner on 2026-09-02, before implementation began.

---

## P-002 — `referencing` declared explicitly

| | |
|---|---|
| **Raised** | 2026-09-02, Phase 1 |
| **Touches** | the `CLAUDE.md` stack list |
| **Status** | ACCEPTED |

**What forced this.** `receipt.json` refers to `proof_scope.json` with a cross-file `$ref`, so
`core/schema.py` must hand `Draft202012Validator` a schema registry. That registry type comes from
`referencing`, which jsonschema already installs and uses as its own resolution mechanism.

**Proposed change.** Add `referencing>=0.35.0` to `[project.dependencies]`. This adds no package to
the environment — it makes an existing, already-installed import honest rather than relying on a
transitive dependency of jsonschema.

**Blast radius.** `core/schema.py` only.

**Alternatives rejected, and why.** Inlining `proof_scope.json` into `receipt.json` would duplicate
a frozen schema and let the two drift. Validating the nested ProofScope in a special case inside
`validate()` would put a per-schema exception into generic code.

**Decision.** Accepted with P-001 on 2026-09-02.

---

## P-003 — `SurfaceSpec` defined in `core/surface.py`, re-exported by `packs/_protocol.py`

| | |
|---|---|
| **Raised** | 2026-09-02, Phase 1 gate |
| **Touches** | `ProviderPack` and its sub-protocols (`docs/ARCHITECTURE.md` §3.1) |
| **Status** | ACCEPTED |

**What forced this.** Phase 1's DoD item 2 says `packs/_protocol.py` *defines* `SurfaceSpec`.
`observe/text.py` and `observe/deps.py` name that type in their signatures, and law L5 forbids a
generic layer from importing `packs/`. Defining the dataclass in `packs/_protocol.py` and importing
it from `observe/` would break L5 on the first observer; there is no third position that satisfies
both sentences.

**Proposed change.** The dataclass lives in `hubbleops/core/surface.py`. `packs/_protocol.py`
re-exports it unchanged and remains the pack-facing name, alongside the `ProviderPack` Protocol.
Field set, serialization and `surface_hash()` are exactly as DoD 2 lists them; only the definition
site moves.

**Blast radius.** Import sites only. No field, no hash input and no wire format changes, so
`surface_hash()` and every ProofScope built from it are unaffected. `packs/_protocol.py` keeps
exporting the name, so a pack author sees no difference. No existing Receipts.

**Alternatives rejected, and why.** Duplicating the dataclass in both places lets the two drift and
gives two different `surface_hash()` implementations. Passing the surface into `observe/` as an
untyped mapping removes the L5 problem by removing the type, which is worse: the observers would
lose static checking of the one input that decides recall.

**Decision.** Accepted by the repository owner on 2026-09-02.

Law L5 outranks a checklist sentence. The dependency direction is not an internal tidiness
preference — it is what lets a customer believe the engine that scanned their repository was not
shaped around one provider's answers. A generic layer that cannot import a pack cannot be tuned to
a pack, and that is the property the proof rests on. DoD 2's wording describes where a pack author
*finds* the type, and that is still true: `packs/_protocol.py` re-exports `SurfaceSpec`,
`VersionCarrier`, `RequestLanguage` and `SinkArgument` in its `__all__`, so
`from hubbleops.packs._protocol import SurfaceSpec` works and a pack author sees no difference.
Only the definition site moved, and no field, hash input or wire format changed with it.

---

## P-004 — two status lines added to the frozen DISCOVERY block

| | |
|---|---|
| **Raised** | 2026-09-02, Phase 1 gate remediation |
| **Touches** | Exposure Map (`docs/ARCHITECTURE.md` §4, FROZEN) |
| **Status** | ACCEPTED |

**What forced this.** §4's DISCOVERY block lists `Candidates found`, `Affected`,
`Not affected (evidence)`, `UNKNOWN` and `Unexplained`. The frozen Candidate schema in §5 carries
five statuses, and two of them — `EXCLUDED_WITH_EVIDENCE` and `HUMAN_REQUIRED` — have no line in
that block. A repository whose candidates land in either status renders a DISCOVERY block whose
listed counts do not sum to `Candidates found`, and a customer reading the map cannot see where the
missing candidates went. Law L1 says a candidate never disappears; a status the map cannot print is
a candidate that disappears from the customer-facing artefact.

**Proposed change.** Two lines in the DISCOVERY block, between `Not affected (evidence)` and
`UNKNOWN`:

```
  Excluded (evidence)     <n>
  Human required          <n>
```

Nothing else in §4 changes. The remaining glyph and layout deviations found at the gate — an ASCII
`-` for the em dash in the title, `-` x 72 for the `─` x 56 rule, `-` for the `·` separator in the
provenance line — were defects, not proposals, and are now corrected to the frozen format.

**Blast radius.** `app/exposure.py` only, plus the CLI reconfiguring stdout to UTF-8 so the frozen
glyphs survive a Windows console. No record, hash or wire format changes, so no ProofScope moves and
no Receipt is affected.

**Alternatives rejected, and why.** Printing §4 verbatim and omitting the two counts hides
candidates from the one artefact the customer actually reads. Folding `EXCLUDED_WITH_EVIDENCE` into
`Not affected (evidence)` merges two statuses the frozen schema deliberately separates: one is
proven irrelevant, the other is proven outside first-party repair.

**Decision.** Accepted by the repository owner on 2026-09-02.

The Exposure Map is the one artefact a customer reads, and the product's whole claim is that no
candidate ever disappears. A DISCOVERY block whose counts do not sum to `Candidates found` breaks
that claim in the most visible place there is, and a customer who cannot see where the missing
candidates went has no reason to trust the ones they can see. Two lines is a smaller price than a
map that silently under-reports. §4 is otherwise rendered exactly as frozen.

---

## P-005 — Version lattice: per-candidate versions, computed Change Pack diffs, `latest` default target

| | |
|---|---|
| **Raised** | 2026-09-03, pre-Phase-2 design decision |
| **Touches** | §2 (completeness-math prose, no law text changed), §3.1 `ProviderPack` contract (`ContractOracle.diff` semantics, add `versions()`), §4 Exposure Map (`Detected` line) |
| **Status** | ACCEPTED |

**What forced this.** v22→v25 was the first commercial target, not the design. Real repos run
several versions at once — per-call overrides, two services pinned to different SDK lines, a REST
integration nobody has touched since v19. A single repo-wide "current version" cannot represent
that, and hand-building one Change Pack per commercial pair does not scale against a provider that
ships monthly: Google Ads adds a new API version roughly every quarter and every version stays
queryable for a supported window, so the set of versions a real fleet of repos actually runs against
is a lattice, not a pair.

**Proposed change.**
1. **Per-candidate version, not per-repo version.** Every candidate already carries its effective
   version through the existing claim table (`per_call > client_init > sdk_default > UNKNOWN`,
   §5). Obligations are generated per `(candidate, its_effective_version, target)`. A repo with v20,
   v22 and v24 call sites gets three obligation sets against one target; the SDK bump itself is an
   obligation like any other.
2. **Change Pack = per-version catalogs + computed diffs, not one hand-built pair.** The pack stores
   a catalog per supported version (protos from the googleapis git history +
   `GoogleAdsFieldService` catalog per version). `diff(v_from, v_to)` is *computed* by set
   difference over subjects and cached by hash rather than hand-authored. Renames and replacements
   across a gap are resolved by composing consecutive mappings (v22→v23→v24→v25); anything that
   does not compose cleanly is `UNKNOWN_PROVIDER_CONTRACT`, never guessed. Ingestion of a new
   version is a scheduled job, not a project, because the lattice grows monthly.
3. **Default target is `latest` supported by the resolved SDK line**, overridable per run. Sunset
   versions (v19–v21) are ordinary lattice nodes, not a special case — those repos are already
   broken today.

**Blast radius.** No Receipt exists yet — Phase 1 ships only `scan`/`exposure`, and
`ContractOracle`/`ChangeCompiler` are still unimplemented stub Protocols in `packs/_protocol.py`, so
no working code regresses. `proof_scope.json`'s `provider_contract_hash` already hashes "the Change
Pack" generically; a lattice-shaped Change Pack still reduces to one hash, so the frozen
`ProofScope` schema needs no field change. `obligation.json` will need an
effective-version-carrying field before Phase 6 writes a real obligation — noted here for that
phase's planning, not changed now, since the schema is FROZEN and an unbuilt phase is not a forcing
case for touching it yet. Downstream: Phase 2's DoD is rewritten to build the lattice instead of one
pair; Phase 6's DoD is rewritten for per-effective-version obligations and a `--target` flag; Phase
10's report gains per-version site counts.

**Alternatives rejected, and why.** Keep one hand-picked pair per phase and re-run a full Phase 2
gate for every future commercial target — does not scale at a monthly release cadence, and forces
an artificial single "current version" onto repos that are provably running several at once, which
is the exact kind of false confidence Axiom 1 exists to prevent.

**Decision.** Accepted by the repository owner (Jaya Krishna J) on 2026-09-03.

---

## P-006 — Language-agnostic channels: wire signature, sentinel proxy mode, `STRUCTURE_UNSUPPORTED`

| | |
|---|---|
| **Raised** | 2026-09-03, pre-Phase-3/4 design decision |
| **Touches** | §2 (orthogonal-channel list, no law text changed), §3.1 `ProviderPack` contract (add `wire_signature`) |
| **Status** | ACCEPTED |

**What forced this.** No universal semantic analyzer exists. GitHub built stack graphs on
Tree-sitter specifically so name-binding rules for any language could be written in a declarative
DSL with no build dependency — and still shipped rules for only four languages, and the project is
no longer maintained. Sourcegraph, Semgrep and Kythe converge on the same shape: one shared engine,
thin per-language rule files, and a fallback channel for everything the rules do not cover.
"Fully language-agnostic" and "reliable" are only compatible if the reliability comes from channels
that read no source code at all. One such channel already sits in every request the customer's code
makes: the gRPC method path (`/google.ads.googleads.v24.services.GoogleAdsService/SearchStream`),
the REST path (`/vNN/...`), and the `x-goog-api-client` header all name the version — and, unlike
source text, are identical in shape no matter which client language produced them.

**Proposed change.**
1. `ProviderPack` gains `wire_signature`: regexes that parse a request's path and headers into
   `(service, method, version)`, requiring zero per-language work.
2. The sentinel (§15) and the dynamic-capture event schema (§6.5) gain **proxy mode** — an egress
   proxy or the client library's own request logging, parsed via `wire_signature` — alongside the
   existing SDK-hook mode. Proxy mode is the default recommendation because it needs no per-language
   code; SDK hooks stay available where a proxy cannot sit in the path.
3. The structure observer (§6.4) reports `STRUCTURE_UNSUPPORTED` evidence for every `INSIDE` file in
   a language with no rule set, rather than the file silently contributing nothing. The Exposure Map
   shows per-language structural coverage. A missing per-language rule set becomes an explicit,
   visible gap instead of a silent one.

Order of per-language rule work stays bounded and customer-driven: Google Ads' actual client-library
ecosystem is Python, Java, .NET, PHP, Ruby, Perl, plus Node via REST — so Python/PHP/JS/TS first,
Java/C# second, nothing else until a customer needs it.

**Blast radius.** The Observer contract (§3.2) is unchanged: proxy mode is realized as
`observer="sentinel"` evidence (or the existing dynamic-capture schema), not a seventh observer
name, so the frozen `name` comment in §3.2 stays as written. The dynamic-capture event schema
(versioned, defined in Phase 4) will need an optional wire-sourced provenance field — noted here for
that phase's planning, not changed now. `STRUCTURE_UNSUPPORTED` is a new claim type the resolver
handles like any other unresolved claim (§6.7); it does not touch the frozen Candidate schema.
Downstream: Phase 2's DoD gains `wire_signature` tests against real logged request paths; Phase 3's
DoD gains the `STRUCTURE_UNSUPPORTED` requirement and restricts initial rules to Python/PHP/JS/TS;
Phase 4's DoD makes proxy mode the primary sentinel path and SDK hooks secondary; Phase 10's report
gains per-language structural coverage.

**Alternatives rejected, and why.** Lead with per-language structural coverage and treat the wire
channel as a later enhancement — every project surveyed above still ships a non-structural fallback
for languages its rules do not cover, and leading with structure risks a silent gap precisely where
a customer's actual client library is not yet supported, which is the one failure mode (`FALSE
VERIFIED`) the product exists to prevent.

**Decision.** Accepted by the repository owner (Jaya Krishna J) on 2026-09-03.

**Amendment.** P-008, accepted on 2026-09-04, corrects the sentence above that treated
`x-goog-api-client` as endpoint-version authority. The request target carries the Google Ads API
version; that header is retained only as client metadata, and ambiguous parsing yields typed UNKNOWN.

---

## P-007 — `provider_contract_hash` carries the surface as well as the change lattice

| | |
|---|---|
| **Raised** | 2026-09-03, Phase 1 |
| **Touches** | `ProofScope` schema (`core/schemas/proof_scope.json`), `docs/ARCHITECTURE.md` §5, §7.1 |
| **Status** | ACCEPTED |

**What forced this.** The third Phase 1 gate audit blocked on two different recall surfaces sharing
one proof key: `app/cli.py` left `provider_contract_hash` null, so scanning one tree with
`google_ads` and with `_mock` produced the same ProofScope hash and the same `run_id`. The ledger
export was overwritten in place while the database kept the union of both, and the Exposure Map
attributed every candidate to a surface that could only have produced some of them. The SurfaceSpec
is the single input that decides recall, so leaving it outside the proof key contradicts Axiom 1 and
L4. Phase 1 has no Change Pack, so no other frozen field carries a provider input.

**Proposed change.** `provider_contract_hash` is the hash of the provider contract in force, which
is the SurfaceSpec in Phase 1 and, from Phase 2, the SurfaceSpec composed with the full version
lattice — not the lattice alone. `docs/ARCHITECTURE.md:235` changes from "the hash of the full
lattice enters ProofScope as `provider_contract_hash`" to "the hash of the surface and the full
lattice enters ProofScope as `provider_contract_hash`". No schema field is added, renamed, or
removed; the field's type and required-ness are unchanged.

**Blast radius.** Phase 2 must compose rather than replace when it introduces the lattice; if it
replaces, F-2 reopens the moment a surface is edited. Every Phase 1 ProofScope hash and `run_id`
moves relative to the pre-fix code, which is correct — those runs were produced by an unbound
surface. No Receipts exist yet, so none are invalidated. `hops exposure` must keep rendering the
stored value rather than re-deriving it from disk (the read-side fix committed with this proposal).

**Alternatives rejected, and why.** Add a separate `surface_hash` field to ProofScope — a real
frozen-schema change with a wider blast radius, and it splits one question ("which provider contract
produced this?") across two fields that must then never disagree. Leave the surface out of the proof
key and disambiguate by `provider` name alone — the name does not move when the surface is edited,
which is precisely the F-2 failure. Put the surface in `rules_hash` — that field has its own frozen
meaning for the Phase 3 RuleSet and would collide there.

**Decision.** Accepted by the repository owner (Jaya Krishna J) on 2026-09-03. Phase 2 composes the
lattice into `provider_contract_hash` alongside the surface; it never replaces it.

---

## P-008 — Wire endpoint version comes from the request target, not client-library metadata

| | |
|---|---|
| **Raised** | 2026-09-04, Phase 2 plan review |
| **Touches** | §3.1 `ProviderPack.wire_signature` comment/table and P-006 wire-source wording |
| **Status** | ACCEPTED |

**What forced this.** P-006 says the gRPC path, REST path, and `x-goog-api-client` header all name
the Google Ads API endpoint version. The first two do. The third does not reliably do so: Google's
[request logging documentation](https://developers.google.com/google-ads/api/docs/productionize/logging)
shows the endpoint version in `google.ads.googleads.vNN.services...`, while
`x-goog-api-client` is a standard client-information header carrying runtime and library-version
tokens. Treating a client library major as an endpoint major can manufacture a false call-version
observation. The fresh Phase 2 plan review correctly rejected silently changing this frozen text.

**Proposed change.** Keep the frozen `wire_signature` capability and its language-independent
path-plus-headers input, but clarify its output as a typed `MATCH(service, method, version)` or
`UNKNOWN(reason)` result. For Google Ads, endpoint version is derived from the versioned gRPC/REST
request target. `x-goog-api-client` remains captured provenance and may corroborate client identity,
but never supplies endpoint version by itself. Missing components, header-only input, and conflicting
version signals produce explicit UNKNOWN evidence rather than `None` or silence. Remove the factual
claim that this header itself names the endpoint version; add no replacement undocumented header.

**Blast radius.** No frozen schema, Candidate status, verdict, or observer name changes. The
ProviderPack slot remains `wire_signature`; only its previously underspecified failure result and
the Google Ads carrier description are corrected. Phase 4 proxy capture receives an explicit
unknown parse issue it can conserve instead of losing the request. `_mock` is unchanged.

**Alternatives rejected, and why.** Parse the major from `gapic/<semver>` or another client token —
client release and endpoint version are different facts. Ignore ambiguous inputs — violates L9 and
creates a silent miss. Invent `x-goog-api-version` as a replacement — no retained Google Ads source
in this phase establishes it as a request carrier. Remove headers from the wire input entirely —
unnecessary; they remain valuable provenance and may detect contradictions.

**Decision.** Accepted by the repository owner (Jaya Krishna J) on 2026-09-04 through the explicit
instruction to execute Phase 2 completely after this proposal was presented as the sole blocker.

---

## P-009 — Evidence producer attestation

| | |
|---|---|
| **Raised** | 2026-09-05, Phase 3 before AI residue triage |
| **Touches** | frozen Evidence schema and Observer trust boundary |
| **Status** | OPEN |

**What forced this.** The store verifies that an Evidence id hashes its content and prevents an
UNKNOWN from closing on records labeled only `DERIVED_AI_EVIDENCE`. It does not verify who produced
the record or whether the caller truthfully selected `derivation`. A caller can copy AI output into
an otherwise valid Evidence record labeled `OBSERVED`; the store then treats it as independent
observation and can accept a status transition that L10 forbids. Phase 3 introduces the first AI
triage module, so this is now a concrete boundary rather than a hypothetical future concern.

**Proposed change.** Make evidence provenance store-verifiable rather than caller-asserted. Add a
versioned producer attestation to Evidence, covering the canonical record, run id, observer,
derivation, and producer identity. Trusted deterministic observers run in an authority that can mint
the corresponding attestation. The AI triage boundary cannot mint an observed or deterministic
attestation; its ingestion path stamps `DERIVED_AI_EVIDENCE` before signing. The store rejects a
missing, invalid, mismatched, or insufficient attestation before evaluating candidate transitions.
Define the key/capability lifecycle and process boundary in the accepting decision; Python naming or
an import-private singleton is not a security boundary.

**Blast radius.** The Evidence schema and canonical id change, so every observer, store write path,
fixture, export, replay path, scanner fingerprint, and evidence-id test must be updated together.
ProofScope moves and no earlier Receipt survives the new schema/tool identity. Gate audits must add
forged-label, replay-across-run, observer-swap, derivation-downgrade, and stolen-output experiments.
The change must land before AI triage receives any operational application or CLI route. Phase 3 can
ship the disconnected, default-off triage function without accepting this proposal because no AI
record reaches the ledger or store.

**Alternatives rejected, and why.** Trust the `derivation` string because the store hashes it — a
hash proves integrity after construction, not truthful origin. Hide an in-process token in a Python
module — code in the same interpreter can import or recover it. Inspect content heuristically for AI
style — neither deterministic nor enforceable. Delete AI triage — safe but needlessly removes a
future advisory tool; keeping it unreachable preserves the option without widening authority.

**Decision.** Pending repository-owner decision. Until accepted and mechanically enforced, AI
triage remains default-off, has no CLI or scan-pipeline path, and cannot write to the ledger or store.

---

## P-010 — Local environment and run-output directories are accounted, not enumerated

| | |
|---|---|
| **Raised** | 2026-09-07, Phase 4 |
| **Touches** | `ProofScope.tree_hash` semantics (frozen schema unchanged; its input changes) |
| **Status** | ACCEPTED 2026-09-08 |

**What forced this.** `source_closure.build` descended into every directory except `.git`, fully read
and SHA-256'd every file it found, and only then classified the file as `VENDORED` and excluded it
from scanning. Measured on this repository: 14,329 files probed, of which roughly 1,553 are source.
`_probe` accounted for 41.7 s of a 49.4 s walk, and the walk was 80% of a 51.9 s scan. Two categories
drove it. First, `.venv` and the tool caches — 6,609 files that git does not track, that `.gitignore`
already excludes, and that every developer regenerates. Second, `.hubbleops/artifacts`, `toolchain`
and `uv-cache` — HubbleOps's own run output, 5,590 entries, which means run *n+1* scans and hashes the
artifacts of runs 1..*n*. On a customer repository that cost compounds with every scan.

The proof consequence is worse than the latency. Because `tree_hash` is
`content_id([[entry.path, entry.blob_sha] …])` over all entries, rebuilding a virtualenv or running a
second scan changed `tree_hash`, therefore changed the ProofScope, therefore killed a Receipt that
nothing about the repository's source had invalidated. A proof was bound to bytes that are not the
repository.

**What changes.** Two directory sets are pruned at the walk, not at classification:
`ENVIRONMENT_DIRECTORIES` (`.venv`, `venv`, `virtualenv`, `__pycache__`, `.pytest_cache`,
`.ruff_cache`, `.mypy_cache`, `.hypothesis`, `.tox`, `.nox`) and the run-output subdirectories under
`.hubbleops/`. `.venv`, `venv` and `virtualenv` move out of `VENDOR_DIRECTORIES` into the new set.
Each pruned directory still yields exactly one `ClosureEntry`, classified `UNSCANNED` with a precise
reason, so it flows through `_closure_records` as `file_unscanned` evidence and the closure remains
fully accounted: UNEXPLAINED stays 0. The entry's `blob_sha` is derived from its path, so a rebuilt
environment no longer moves the ProofScope.

`node_modules`, `vendor`, `third_party`, `site-packages`, `bower_components`, `Pods` and `.yarn`
deliberately stay in `VENDOR_DIRECTORIES` and are still probed per file. They can be committed and
can carry real provider usage; FA-015 already records the decision not to auto-dismiss third-party
material. A bare `site-packages` outside a virtualenv is still hashed.

**Blast radius.** `tree_hash` changes for any repository containing one of these directories, so
ProofScope, run ids and Receipts change with it. This is the designed invalidation path and it is
automatic: `scanner_version` carries `scanner_fingerprint(OBSERVATION_SOURCES)`, which hashes every
`.py` in the package, so editing `source_closure.py` already moves the scope. No stored proof is
silently reinterpreted; each is superseded. The Phase 2/3/4 real-repo loop numbers were taken on
repositories with no dependencies installed and no `.hubbleops/` present, so the recorded candidate,
evidence and UNKNOWN counts are unaffected and stay comparable.

**Alternatives rejected, and why.** Keep probing but skip hashing for excluded files — still opens
and reads every file, and still changes `tree_hash`, so it pays the cost without the benefit. Prune
`node_modules` and `vendor` too — larger win, but a committed vendored client library is exactly
where provider usage hides; refused on FA-015 grounds. Add a stat-based `(size, mtime)` probe cache —
faster still, but mtime is not content, and a cache that can lie about a file's identity is a
proof-affecting shortcut; refused under L7, memory may reduce work, never proof. Derive the closure
from `git ls-files` — free exclusion of everything untracked, but untracked-yet-present files would
vanish from the closure entirely, which is precisely the silent absence the closure exists to prevent.

**Decision.** ACCEPTED by the repository owner on 2026-09-08, by the instruction to merge Phase 4,
which lands this change on `main`. The code change is covered by
`test_environment_directories_are_accounted_but_never_enumerated`,
`test_run_output_under_the_state_directory_is_accounted_but_not_enumerated`, and
`test_excluded_directory_identity_does_not_depend_on_its_contents`.

---

## P-011 — The Falsifier sub-protocol gets the shape Phase 5 deferred to it

| | |
|---|---|
| **Raised** | 2026-09-08, Phase 5 |
| **Touches** | frozen `ProviderPack` sub-protocol `Falsifier` (`packs/_protocol.py`) |
| **Status** | ACCEPTED 2026-09-08 |

**What forced this.** `Falsifier` is `name: str` and a docstring reading "Provider adversarial check
implemented in the verification phase". Phase 5 is that phase. `verify/falsify.py` must select the
falsifiers whose failure class the rescan actually detected and then run them, and neither selection
nor execution is expressible against a bare name. A falsifier that cannot be run is not a falsifier;
it is a label, and `falsifiers_pass` computed over labels is the exact shape of trap (5) — a check
that exists but decides nothing.

**Proposed change.** `Falsifier` gains two members:

```python
failure_class: str


def check(self, subject: FalsifierInput) -> FalsifierOutcome: ...
```

`FalsifierInput` and `FalsifierOutcome` are defined in `hubbleops/core/verification.py`, not in
`packs/_protocol.py`, so that `verify/` can name them without importing `packs/`. `_protocol.py`
imports them from `core/`, exactly as it already imports `SurfaceSpec` from `core/surface.py`.
`FalsifierOutcome.result` is `PASS | FAIL | UNKNOWN`: a falsifier that cannot decide says so and
drives the verdict to UNKNOWN, and is never allowed to read as PASS.

**Why this is a completion rather than a redesign.** The protocol reserved the member and deferred
its shape by name. Nothing that exists is being reinterpreted: no pack ships a falsifier today, so
there is no implementation to migrate and no stored record whose meaning changes. `ProviderPack`
itself is untouched — `falsifiers() -> list[Falsifier]` is unchanged.

**Blast radius.** `packs/_protocol.py`, both packs' `falsifiers()` implementations, and
`core/verification.py`. `Transform` and `ToolSpec` carry the identical deferral for Phase 6 and are
deliberately **not** changed here; Phase 6 raises its own proposal with the repair loop's evidence in
hand. No schema, no stored record, no ProofScope input changes, so no Receipt is invalidated.

**Alternatives rejected, and why.** Keep `Falsifier` a name and hold the executable check in a
parallel registry inside `verify/` — the registry would be keyed by provider name, which is precisely
the provider knowledge the generic layer may not hold. Have `app/` run the falsifiers and pass
booleans into `verify/` — moves an authority check outside the authority, so a caller could decide
`falsifiers_pass` and the verifier would believe it. Skip falsifiers in Phase 5 — the verdict rule is
frozen and names `falsifiers_pass`; a conjunct wired to a constant is a weakened verdict rule, which
is a declared approval boundary.

**Decision.** ACCEPTED by the repository owner on 2026-09-08, as the shape the frozen protocol
deferred to this phase. Enforced by `tests/unit/test_phase5_verdict.py::
test_every_flag_reaches_the_verdict`, which fails if `falsifiers_pass` stops changing the verdict.

---

## P-012 — Bind every verification input and oracle context into ProofScope

| | |
|---|---|
| **Raised** | 2026-09-09, Phase 5 gate audit |
| **Touches** | `hubbleops/core/schemas/proof_scope.json` and the frozen `ContractOracle` sub-protocol |
| **Status** | ACCEPTED |

**What forced this.** `app/verification.py` derives the verification ProofScope from the candidate
scan only. The selected base SHA, source and target versions, normalized obligations, human
decisions, base and candidate captures, and live oracle authority context do not enter the proof
key. Changing any one of those inputs can change the verdict while leaving `proof_scope_hash`
unchanged. In particular, a stale or substituted capture can change the dynamic differential,
migration audit, falsifiers and oracle results under the same scope, and choosing a different base
can change the diff, frozen tests and blast radius. This contradicts Axiom 1: anything outside the
hash is outside the proof.

**Proposed change.** Add a versioned verification-input manifest whose canonical content id binds
the resolved base and candidate SHAs, selected version pair, normalized obligations and decisions,
base and candidate capture bytes after schema validation, and the identity of the frozen baseline
suite. Add a non-secret `context_hash` to `ContractOracle` that binds the validation implementation,
transport mode and authority context needed to make validation results reproducible without hashing
credentials. Bind both hashes into ProofScope. Either add explicit
`verification_inputs_hash` and `oracle_context_hash` fields to the frozen schema, or formally
approve a documented composition into the existing `build_config_hash`; do not overload that field
without recording the semantic decision.

The verifier must reject an obligation, decision, capture or oracle result whose declared scope does
not match the recomputed verification-input manifest. Capture import needs a manifest binding the
event artifact to its originating repository SHA and execution identity; a bare JSONL path cannot
be promoted to source-bound ledger evidence. Receipt reuse must compare the complete recomputed
scope before presenting stored results.

**Blast radius.** ProofScope hashes, verification run ids and all Phase 5 Receipts change. The
ProofScope schema and fixtures change if explicit fields are selected. `ContractOracle`, both packs,
the Phase 4 capture manifest, verification CLI input loading, receipt reuse, and adversarial tests
must move together. Existing Receipts remain historical artifacts but cannot be reused under the new
scope semantics.

**Alternatives rejected, and why.** Bind only file paths — contents can change in place. Bind only
capture bytes — base selection, obligations, decisions and oracle context still move verdicts. Hash
credentials — leaks stable secret-derived identifiers and still fails to describe the provider
account or transport implementation cleanly. Silently place these values in `build_config_hash` —
mechanically safer, but it redefines a frozen proof field without an approved contract. Treat the
Receipt body hash as the proof key — reverses the architecture: the result would define its own
input identity.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-09, taking the explicit
form: `verification_inputs_hash` and `oracle_context_hash` become named fields on the frozen
ProofScope schema rather than being composed into `build_config_hash`. A proof key whose fields name
what they cover can be audited by a reader; one field that silently means two things cannot. Both
are null for a scan-only scope, which the schema already defines as "not yet bound", never "bound to
nothing".

---

## P-013 — Group and rank UNKNOWNs in the frozen Exposure Map

| | |
|---|---|
| **Raised** | 2026-09-09, pre-pilot readiness review |
| **Touches** | frozen §4 Exposure Map output format |
| **Status** | ACCEPTED |

**What forced this.** The Phase 3 real-repo loop produced, on `mcp-google-ads`, 422 candidates,
9 AFFECTED and 413 preserved UNKNOWN; on `google-ads-api`, 2,471 candidates, 3 AFFECTED and 402
preserved UNKNOWN. Every one of those UNKNOWNs is correct under L2 and carries a precise closing
instruction, and `UNEXPLAINED` is 0 in both runs. The engine is right; the rendering is not. An
UNKNOWN section that lists 413 undifferentiated entries beside 9 AFFECTED ones reads as "this tool
cannot decide anything" rather than "this tool refuses to guess". The Law that makes HubbleOps
trustworthy currently presents as the thing that makes it useless.

**Proposed change.** Within the frozen §4 UNKNOWN section, group entries by closing-instruction
class and rank the groups by proximity to a request sink, printing the highest-signal group expanded
and the remainder collapsed with a count and an `[expand]` marker — the idiom §4 already uses for
`NOT AFFECTED (with evidence) 39 [expand]`. No candidate's status changes. No UNKNOWN is closed,
merged or suppressed. `UNEXPLAINED` remains 0, and every entry retains its individual closing
instruction in the machine-readable export whether or not the terminal rendering collapsed it. The
grouping key is derived from the closing instruction the candidate already carries, so it is
provider-neutral by construction and deterministic for a given ProofScope.

**Blast radius.** `app/exposure.py` rendering and its golden-output tests; §4 of
`docs/ARCHITECTURE.md`; every fixture asserting exact map bytes. The ledger, the schemas, the
Candidate status vocabulary and the ProofScope are untouched. Determinism tests must still pass, and
a new test asserts the grouped counts sum to the ungrouped UNKNOWN count, so the rendering can never
lose a candidate.

**Alternatives rejected, and why.** Close low-signal UNKNOWNs automatically — forbidden by L3, and
precisely the failure the Laws exist to prevent. Suppress documentation and test-data candidates —
an incomplete exclusion list then hides real usage, the same reasoning that rejected auto-dismissing
non-provider hosts in FA-015. Leave the rendering alone and explain the number in conversation — a
frozen customer-facing format that needs a verbal apology is a defective format. Add a separate
summary command — two renderings of one truth is FA-017's defect shape with a delay. Group by
observer — accurate and cheaper, but it tells the reader where a candidate came from rather than
what to do about it, which is the question the section exists to answer.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-09, grouped by closing
instruction. The measurement of an ordinary application repository (plan Q6) still stands, because
it decides how much of the collapsed tail is real.

---

## P-014 — `Transform` gains its shape

| | |
|---|---|
| **Raised** | 2026-09-09, Tier 1 planning |
| **Touches** | frozen §3.1 `ProviderPack` contract (`Transform` sub-protocol) |
| **Status** | ACCEPTED |

**What forced this.** `packs/_protocol.py` declares `Transform` as a name with no shape, and
`dev/context.md` records the Phase 5 decision that "`Transform` and `ToolSpec` carry the identical
deferral and are deliberately untouched; Phase 6 raises its own proposal with the repair loop's
evidence in hand." This is that proposal. `repair/deterministic.py` is a generic runner, so it can
only reach a transform through this protocol; without a shape, the Google Ads transforms would have
to be imported by `repair/` directly, which is an L5 violation.

**Proposed change.** `Transform` gains `name: str`, `failure_class: str`, and the three methods the
Phase 6 prompt already names: `precondition(TransformInput) -> bool`, `apply(TransformInput) ->
TransformOutput`, `postcondition(TransformOutput) -> bool`. `TransformInput` and `TransformOutput`
live in `core/repair.py`, following `core/verification.py` under P-011, so `repair/` never imports
`packs/` and `app/` remains the only importer of packs. A transform whose precondition fails does
not apply and does not fail the run; the obligation stays open for a human. A transform whose
postcondition fails after applying reverts and names its failure class. `ToolSpec` stays deferred:
the `PROVIDER_TOOL` and `AGENT` repair classes are not implemented in this tier and route to
`HUMAN`, which the frozen `obligation.json` enum already permits.

**Blast radius.** `packs/_protocol.py`, both packs' `repair_transforms()`, the new `core/repair.py`,
`repair/deterministic.py`, and the protocol conformance tests. No verdict conjunct changes. The
repair worker remains untrusted and its change manifest remains a HINT.

**Alternatives rejected, and why.** Leave `Transform` a bare name and let `repair/` import the pack's
transforms directly — a direct L5 violation and exactly what `test_no_provider_leak` exists to catch.
Give `Transform` only `apply()` — a transform with no precondition applies where it does not belong,
and one with no postcondition cannot tell "repaired" from "corrupted". Defer until the repair agent
exists — the agent is cut from this tier, so the deferral would never end.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-09, as the shape the
frozen protocol deferred to Phase 6, mirroring P-011's resolution for `Falsifier`.

---

## P-015 — Candidate accounting gains the states a corpus ledger actually reaches

| | |
|---|---|
| **Raised** | 2026-09-09, repository intelligence engine |
| **Touches** | `hubbleops/core/schemas/candidate.json` (frozen `status` enum) |
| **Status** | ACCEPTED |

**What forced this.** `status` enumerates `AFFECTED`, `NOT_AFFECTED_WITH_EVIDENCE`, `UNKNOWN`,
`HUMAN_REQUIRED`, `EXCLUDED_WITH_EVIDENCE`. Role classification landed a real corpus ledger, and the
measured run over this repository's own tree shows those five cannot express what the ledger now
knows. A 10 MB record stream dense with surface names is not "excluded because the closure called it
VENDORED"; it is provider reference data, and the reason string is currently carrying a distinction
the enum should carry. The same pressure appears for a file the scanner could not read, for a
language with no rule bundle, and for a repaired site whose fix a human has accepted.

**Proposed change.** Add `PROVIDER_REFERENCE_DATA`, `UNSUPPORTED`, `UNSCANNED`, and
`HUMAN_ACCEPTED_RISK` to the `status` enum. `FIXED` and `VERIFIED` are deliberately **not** added:
a repaired or verified state belongs to a Receipt bound to a ProofScope, not to a candidate in a
scan ledger, and putting them here would let a scan claim a verification it never performed.

**Blast radius.** `core/candidate.py` `STATUSES`, `observe/resolver.py` handlers, `observe/ledger.py`
`counts()`, `app/exposure.py` rendering, and the Exposure Map's grouping. The verdict function does
not read candidate status, so no conjunct changes. `UNEXPLAINED_CANDIDATES = 0` is unaffected: these
states are reached instead of `UNKNOWN`, never instead of accounting.

**Alternatives rejected, and why.** Keep five states and encode the rest in `reason` — that is the
status quo, and it is why a grep for "why was this excluded" reads free text rather than an enum;
a machine cannot group on prose. Add every state the architecture note lists — `FIXED` and
`VERIFIED` would let a scan-time record assert a verification-time fact, which is the exact
confusion the verdict rule exists to prevent.

**Decision.** ACCEPTED by the repository owner on 2026-09-10 under the instruction to finish the
remaining build completely. The four states preserve accounting distinctions the corpus ledger has
already measured while keeping `FIXED` and `VERIFIED` outside scan-time candidate semantics.

---

## P-016 — One scan, many providers

| | |
|---|---|
| **Raised** | 2026-09-09, repository intelligence engine |
| **Touches** | `hubbleops/core/schemas/proof_scope.json` (frozen), `ProviderPack` selection in `app/` |
| **Status** | PROPOSED |

**What forced this.** `hops scan` takes `--pack` as a required argument, so discovering two providers
in one repository means two full scans: two source closures, two ripgrep passes, two ast-grep graph
builds. The corpus and the semantic index are provider-neutral by construction — that is what
`test_no_provider_leak` enforces — so rebuilding them per provider is pure waste, and it grows
linearly with the number of providers a customer uses.

**Proposed change.** `--pack` accepts more than one pack. The source closure, dependency resolution
and import graph are built once; each pack contributes its own surface patterns and rules to a single
ripgrep pass and a single combined ast-grep pass; each pack gets its **own** ledger and its **own**
ProofScope. `ProofScope` gains no composite provider field: a proof stays bound to exactly one
provider contract, and one run simply emits N scopes that share a `tree_hash` and
`dependency_resolution_hash`. The run record gains the set of scope hashes it produced.

**Blast radius.** `app/cli.py` argument handling and `scan_repository`, `observe/text.py` pattern
composition, `graph/imports.py` rule-set composition, `store/sqlite.py` run-to-scope cardinality, and
`proof_scope.json` only insofar as a run now references many scopes rather than one.

**Alternatives rejected, and why.** A composite ProofScope covering several providers — a new SHA in
one provider's contract would kill an unrelated provider's proof, and a proof key whose fields name
two things cannot be audited, which is the same argument P-012 settled. Run the packs sequentially
and cache the closure between runs — that is Phase 8's incremental machinery arriving early, through
a cache with no invalidation proof.

**Decision.** PENDING. Depends on nothing else, but it is worth landing after the corpus work
settles, because it composes rule sets that the parse-once change is still reshaping.

---

## P-017 — The Exposure Map states what discovery proved and what it did not

| | |
|---|---|
| **Raised** | 2026-09-09, repository intelligence engine |
| **Touches** | frozen §4 Exposure Map format |
| **Status** | ACCEPTED |

**What forced this.** The Exposure Map reports what was found. It does not report whether the search
that found it was complete, and those are different claims. A customer reading `Affected 7` cannot
tell it apart from `Affected 7, and 40 files failed to parse`. Role classification and the
resolution budget both made this concrete: a bulk data file is now deliberately excluded from code
candidacy, and a resolution can now exhaust a budget — both are correct outcomes, and both are
invisible in the current format. An absence of findings must never read as an absence of exposure.

**Proposed change.** A `DISCOVERY COMPLETENESS` section, additive; no existing line changes meaning
or position. It reports corpus accounting (entries enumerated, entries accounted, role counts),
analysis reach (supported source, indexed, parser failures), the resolution frontier (budget
exhaustions, dynamic boundaries, provider-relevant UNKNOWNs), and closes with an explicit verdict:

`DISCOVERY_COMPLETE` when every entry is accounted, every supported source file parsed, no
resolution exhausted its budget, and no provider-relevant UNKNOWN remains. `DISCOVERY_INCOMPLETE`
otherwise, listing each unmet reason by name. The verdict is about the *search*, never about the
*repository's safety*: `DISCOVERY_COMPLETE` with 400 preserved UNKNOWNs is a legitimate and
common result, because a preserved UNKNOWN with a closing instruction is a correct outcome under L2.

**Blast radius.** `app/exposure.py` render and its tests. No schema, no verdict conjunct, no
candidate status. `hops verify` does not read the Exposure Map, so no proof changes.

**Alternatives rejected, and why.** Put the verdict in the Receipt instead — the Receipt exists only
after a verification, and discovery completeness is a property of a *scan*, which is the artifact a
prospect sees first and the only one they may ever see. Infer completeness from `Unexplained 0` —
that counts accounting integrity, not search reach; a file that failed to parse is accounted for
and still unsearched, which is exactly the confusion this section exists to remove.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-09, under the directive
to build the repository intelligence engine. The section is additive and the owner should confirm
the verdict wording before it reaches a customer.

---

## P-018 — The Receipt preserves every accepted candidate accounting state

| | |
|---|---|
| **Raised** | 2026-09-10, P-015 implementation review |
| **Touches** | `hubbleops/core/schemas/receipt.json` (frozen candidate summary) |
| **Status** | ACCEPTED |

**What forced this.** P-015 adds `PROVIDER_REFERENCE_DATA`, `UNSUPPORTED`, `UNSCANNED`, and
`HUMAN_ACCEPTED_RISK` to the Candidate contract. The frozen Receipt summary previously had no fields
for those states, so a verification could account for them in its ledger and silently omit them from
the machine-readable proof artifact. The same omission existed in the obligation carry-forward list.

**Proposed change.** Add one non-negative summary count per P-015 state, render the operationally
important counts in `receipt.md`, and carry every open state into obligations. Bump the SQLite schema
version because the candidate status `CHECK` constraint changes; stores written with the old
constraint are refused rather than silently trusted or rewritten.

**Blast radius.** Receipt schema fixtures and rendering, obligation construction, SQLite schema
compatibility, and tests asserting ProofScope field count.

**Alternatives rejected, and why.** Fold the new states into `UNKNOWN` — destroys the distinctions
P-015 exists to preserve. Leave them out of the Receipt — makes ledger totals and proof totals
disagree. Migrate old SQLite files automatically — rewriting provenance storage during startup is a
larger trust decision than refusing an incompatible local cache.

**Decision.** ACCEPTED by the repository owner on 2026-09-10 under the instruction to finish the
remaining build completely; no accepted candidate state may disappear at a phase boundary.

---

## P-019 — Provider-owned verification secrets in generated Actions

| | |
|---|---|
| **Raised** | 2026-09-11, Phase 7 full regression gate |
| **Touches** | optional provider-pack capability, generated verification workflow |
| **Status** | ACCEPTED |

**What forced this.** The first Phase 7 workflow embedded one provider's credential variable names
inside `proof/memory.py`. The full regression suite correctly rejected fourteen provider-name leaks
from a generic layer. Removing the environment entirely would generate an Action that can never
reach a live oracle; tolerating the names would violate L5 and make every future provider a generic
code change.

**Proposed change.** A pack may optionally supply an environment-to-secret-name mapping. `app/`
selects the pack named by the schema-validated Receipt and passes that inert mapping into `proof/`.
The workflow generator accepts only uppercase environment and secret identifiers, sorts them, and
renders no secret values. This stays an optional capability discovered at the application boundary,
not a new required method on the frozen `ProviderPack` Protocol.

**Blast radius.** `app/registry.py`, `app/cli.py`, the provider pack, workflow generation, and the
Phase 7 boundary tests. ProofScope and Receipt contents do not change. Generated workflow permissions
remain read-only, and credential values remain GitHub-managed secrets.

**Alternatives rejected, and why.** Hardcode the first provider in `proof/` — violates L5. Generate
no bindings — permanently makes the live oracle unavailable in CI. Put secret names in the frozen
surface schema — turns an operational concern into discovery semantics and requires an unrelated
schema amendment. Add a required method to `ProviderPack` — breaks its frozen contract for a
capability only verification Actions need.

**Decision.** ACCEPTED by the repository owner on 2026-09-11 under the instruction to finish the
remaining build completely. The provider boundary and the ability to run a live oracle in customer
CI are both non-negotiable.

---

## P-020 — The Receipt states which authority accepted a request

| | |
|---|---|
| **Raised** | 2026-09-11, owner product decision on the credential boundary |
| **Touches** | `core/schemas/receipt.json` (frozen Receipt); `ValidationResult` on the frozen `ContractOracle` sub-protocol; the Google Ads `ContractOracle` implementation; the customer-CI clause of P-019 |
| **Status** | ACCEPTED |

**What forced this.** The generated verification Action requires the customer to place five Google
Ads OAuth secrets — `GOOGLE_ADS_CLIENT_ID`, `CLIENT_SECRET`, `REFRESH_TOKEN`, `DEVELOPER_TOKEN`,
`CUSTOMER_ID` — into their own repository, because `GoogleAdsContract.validate` returns
`ORACLE_UNAVAILABLE` without a transport and `verdict.py` caps an unavailable oracle at UNKNOWN.
Those credentials carry read *and* write authority; Google issues no validate-only credential, so
the only thing constraining them is HubbleOps's own source setting `validate_only = True`. The
safety boundary is our code, not the credential's scope, which is precisely what an enterprise
security review is built to reject. The repository owner has ruled that a read-only engagement is
the product's default. P-019 accepted the opposite as non-negotiable; that clause is what this
proposal revisits.

The forcing measurement: of the nine conjuncts of the frozen verdict rule, exactly one —
`oracle_all_accepted` — needs a network. Discovery, blast radius, obligations, repair, the shape
differential, the response-consumer check, the frozen baseline suite, the falsifiers and UNKNOWN
conservation are all offline, and were proven offline on `woocommerce/google-listings-and-ads` at
`b43b322` with 2,061 closure entries and zero credentials. A five-secret ask currently gates one of
nine conjuncts and every green receipt.

**Proposed change.**

1. `_validate_fields` gains a positive result. When the target catalog's `field_inventory` carries
   `complete: true` and every field named in the query resolves, return `VALID` attributed to
   catalog authority instead of `None`, which today becomes `ORACLE_UNAVAILABLE`. An incomplete
   inventory or an unresolved field keeps today's `UNKNOWN_PROVIDER_CONTRACT`. Fail-closed is
   preserved: catalog authority is claimed only where the catalog itself asserts completeness.
2. Every entry in the Receipt's `oracle_results` carries an `authority` of `LIVE` or `CATALOG`.
3. The Receipt gains one required top-level field, `oracle_authority`: `LIVE` when every result was
   decided by the provider, `CATALOG` when any result was decided by the catalog alone,
   `ORACLE_UNAVAILABLE` when the oracle could not decide. A Receipt that does not state its
   authority cannot be produced.
4. A configured live transport still takes precedence. Catalog authority is the floor, never a
   replacement, and a customer may raise a receipt from `CATALOG` to `LIVE` by supplying their own
   test-account credentials.

**What catalog authority proves, and what it does not.** It proves that every field named in the
GAQL query exists in the target catalog and that the catalog's field inventory is complete and
internally reconciled against every resource schema. It does not prove selectability, filterability,
sortability, segmentation compatibility, resource/field pairing, or date-range legality: none of
those are among the catalog's fact kinds (`field`, `field_inventory`, `enum`, `family`, `message`,
`proto`, `proto_field`, `service`, `documented_change`, `client_compatibility`). A query composed
entirely of existing fields can still be rejected by the provider.

It also does not cover `Mutate` at all. Only `Search` and `SearchStream` reach `_validate_fields`;
`Mutate` goes straight to the transport. Under this proposal a `Mutate` request with no live
transport stays `ORACLE_UNAVAILABLE`, so a repository whose provider usage is write-path still caps
at UNKNOWN. Offline mutate-shape validation against the catalog's `message` and `proto_field` facts
is feasible and is named here as follow-up work rather than smuggled into this change.

**Blast radius.** `packs/_protocol.py`, where `ValidationResult` gains an optional `authority`
following the placement P-011 and P-014 established for `Falsifier` and `Transform` — a pack is the
only party that knows which authority decided a request, so the value has to cross the protocol;
`core/verification.py` for `OracleOutcome`, `OracleCheck` and `OracleReview`; `verify/oracle.py` to
carry authority and to fail closed when a `VALID` arrives with none, since an unstated strength is
not proof; `packs/google_ads/contract.py` and `packs/_mock`; `app/verification.py`, where
`oracle_mode` becomes the authority value; `proof/receipt.py` and the PR-body renderer; and
`hubbleops/core/schemas/receipt.json` for the one required field — which this proposal must name
explicitly for `.claude/hooks/guard.py` to permit the edit. ProofScope does not change: catalog
authority is already bound by `provider_contract_hash` (P-007) and live authority by
`oracle_context_hash` (P-012). Existing Receipts do not survive the added required field. Gates to
re-run: Phase 5 and Phase 7.

**Alternatives rejected, and why.** Keep P-019 as written — makes a five-secret production-credential
ask the price of a green receipt, which the owner has ruled out. Carry authority only inside
`oracle_results` items, whose item schema is an open object and so needs no frozen change — cheaper,
but then nothing forces a Receipt to state its authority, and an unstated strength on a proof
artifact is the FA-020 pattern exactly: a reader infers proof from silence. Add a new verdict value
for catalog-backed proof — changes the frozen verdict function and its vocabulary to express what is
an evidence-provenance concern. Drop the oracle conjunct entirely — a conjunct of the frozen verdict
rule that cannot fail is a conjunct that is not there.

**Decision.** ACCEPTED by the repository owner on 2026-09-11, answering Q17, Q18 and Q19 of
[dev/plan.md](plan.md). A catalog-backed proof may reach `VERIFIED_FOR_SCOPE` with its authority on
the face of the Receipt. `oracle_authority` is a required top-level Receipt field, so no Receipt can
stay silent about its own strength. The Phase 5 gate no longer requires a live provider run, which
supersedes the customer-CI clause of P-019 and the live-oracle requirement in the Phase 5 prompt.

One consequence is recorded rather than hidden: the live transport then ships exercised only against
`_mock`, never against the real provider. It stays on the opt-in path and neither the Receipt nor any
document may describe `LIVE` authority as provider-proven until a real run exists.

---

## P-021 — Catalog authority validates Google Ads mutate protobuf shape

| | |
|---|---|
| **Raised** | 2026-09-11, completion of the credential-free verification boundary |
| **Touches** | Google Ads `ContractOracle` implementation, contract tests, Phase 5 evidence wording |
| **Status** | ACCEPTED |

**What forced this.** P-020 made a complete target catalog sufficient authority for a supported
`Search` request but deliberately left `Mutate` as follow-up. That left the default read-only
engagement unable to verify a repository whose provider usage is write-path even when every supplied
request field is present in the exact target-version protobuf catalog. Requiring live credentials
for that one request family would recreate the credential boundary P-020 retired.

**Selected design.** `GoogleAdsService.Mutate` may return `VALID` under `CATALOG` authority when the request
contains at least one operation and every supplied field recursively resolves through the target
catalog's `message` and `proto_field` facts. The validator normalizes protobuf JSON lower-camel names
to proto snake case, accepts the existing normalized `operations` alias for
`mutate_operations`, resolves nested message and enum types, and enforces repeated-versus-singular
and message-versus-scalar container shape plus primitive scalar JSON types and integer bounds. A
missing or conflicting field/type, duplicate spelling,
empty operation, ambiguous operation selection, or validation-depth exhaustion remains
`UNKNOWN_PROVIDER_CONTRACT`; a container that directly contradicts a resolved protobuf field is
`INVALID`. A configured live transport still outranks a catalog acceptance.

**Guarantee boundary.** Catalog authority proves only that the supplied protobuf JSON shape is
compatible with the target catalog. It does not prove required-field rules, oneof semantics,
resource names, scalar content formats, update-mask contents, permissions, quotas, cross-field
constraints, or provider business rules, and every positive result says so. No network call,
credential, frozen schema, verdict rule,
or generic Proof-layer provider knowledge is added.

**Alternatives rejected.** Treat any mapping as valid — not evidence-backed. Reject absent catalog
fields as provider-invalid — the catalog has no explicit complete-message marker, so absence is
UNKNOWN. Model every provider semantic offline — unsupported by the available facts and falsely
strong. Keep Mutate live-only — contradicts the accepted credential-free product boundary.

**Decision.** ACCEPTED by the repository owner on 2026-09-11 under the instruction to finish the
remaining build completely. Phase 5 and Phase 7 gates must include the new positive, negative, and
fail-closed cases.

---

## P-022 — CATALOG scope is quoted from the accepting pack, never asserted by the Receipt

| | |
|---|---|
| **Raised** | 2026-09-11, post-P-021 audit of the rendered Receipt |
| **Touches** | `hubbleops/core/schemas/receipt.json` (`oracle_authority` description only); `proof/receipt.py` authority rendering; the generic-surface boundary tests |
| **Status** | ACCEPTED |

**What forced this.** P-020 taught the Receipt to name its authority and, in the same change, wrote
the scope of a CATALOG acceptance into the generic layer as a fixed sentence: it proved a named
field exists and proved "nothing about selectability, filterability, segmentation, resource pairing,
or any mutate shape". P-021 then made a validated mutate protobuf shape a CATALOG acceptance. From
that moment the Receipt and the frozen schema both told the reader the opposite of what the build
had just proven, and nothing in the suite could notice, because no test pinned the sentence. A
Truth Engine that misstates the strength of its own evidence has failed at the one job it sells.
The defect was not the wording; it was that a generic layer asserted a fact only a pack can own.

**Selected design.** `proof/receipt.py` stops asserting any scope. For CATALOG authority it renders
the distinct `reason` of every accepted CATALOG check, deduplicated and sorted for determinism, each
on a `scope:` line. The pack is already required to state that scope on every positive result
(P-021), so the Receipt quotes its source instead of paraphrasing it, and a pack that learns a new
check updates the Receipt by construction. A CATALOG authority whose accepting checks state no scope
renders an explicit line saying the acceptance proves nothing, rather than falling silent. The
`receipt.json` `oracle_authority` description is amended the same way: it now describes where the
scope comes from and no longer enumerates one provider's capabilities. No enum member, required
field, verdict input, or record shape changes.

**Blast radius.** Rendering and one frozen `description` string. The Receipt's structure, the
ProofScope, the verdict function, and every persisted record are untouched. A previously issued
Receipt remains valid and re-renders with the same authority value.

**Alternatives rejected, and why.** Correct the sentence to mention mutate — restores truth today
and rots again at the next pack capability, which is the actual defect. Delete the scope line —
leaves the reader unable to tell a catalog acceptance from a live one, which is precisely what P-020
existed to prevent. Move the sentence into the pack but keep a generic fallback — two sources of
truth, and the stale one wins whenever the pack is silent. Add `mutate` to `provider_names.txt` so
the existing leak test catches it — collides with the legitimate generic words `mutated` and
`oneOf`, so it would have to be weakened to pass and would stop proving anything.

**Decision.** ACCEPTED by the repository owner on 2026-09-11 under the instruction to fix every
finding of the P-021 tree audit. Two permanent regression tests land with it: a Receipt-level test
that the rendered text carries the pack's own stated scope, and a boundary test that no generic
surface states what a provider's catalog authority covers. Recorded as FA-033.

---

## P-023 — The structural layer adjudicates the recall layer's matches

| | |
|---|---|
| **Raised** | 2026-09-11, Phase 6 |
| **Touches** | Resolution semantics of `surface_reference` / `endpoint_reference`; `CLAIM_PRECEDENCE`. **No frozen schema changes.** |
| **Status** | ACCEPTED 2026-09-11 |

**What forced this.** The real-repo loop on an ordinary application produced 752 UNKNOWNs, of which
**497 (37.8% of all 1,315 candidates)** are recall-layer matches carrying no syntactic role. Read at
their sites, they are imports, docblocks, PHP type hints, stylesheet selectors, help-centre URLs and
UI action slugs. The genuine call sites are a small minority of the matches, and nothing downstream
can tell the two apart, because a text record carries path, line and subject and nothing else. The
provider's name is the application's own domain vocabulary, so recall scales with how much the
product *talks about* the provider rather than how much it *calls* it. This is not repo-specific:
it is the documented reason OpenRewrite attributes types onto its syntax tree, and the reason
Sourcegraph separates precise from search-based navigation.

**Proposed change.** The `structure` observer emits an adjudication record for every recall match
that falls inside a file it successfully parsed. The record uses the same `claim_type` as the match
it adjudicates, with `observer: "structure"`, `confidence: "PROVEN"`, and a `value` carrying the
containing node kind and — where the language has an import table — the fully qualified symbol the
token binds to. `CLAIM_PRECEDENCE` for those claim types gains `"structure"` ahead of `"text"`, so
the existing precedence machinery lets the adjudication win. The resolver's handlers branch on
observer, exactly as `_request_text` already does.

A file the structure observer did not parse produces no adjudication, and the text claim survives
unchanged as UNKNOWN. **Absence of adjudication is never a disposition.**

Nothing in the frozen schemas moves: `claim_type` is an open string pattern, `value` is documented
as "any JSON value, shaped by claim_type", `observer` already contains `structure`, and `confidence`
already contains `PROVEN`. This proposal exists for the *semantics*, not the shape — it changes when
a candidate may leave UNKNOWN, which is a safety question and therefore the owner's.

**Blast radius.** `observe/structure.py`, `observe/resolver.py`, per-language context rules in each
pack's rule bundle, and `_mock` for conformance. Every ProofScope moves, because `rules_hash` and
the pipeline fingerprint both change — so every existing Receipt is dead, which is correct and
expected. The Phase 5 verdict function, the Receipt layout and every persisted record shape are
untouched. Q21 and Q22 in PART SIX of `dev/plan.md` must be answered before the disposition table
can be written; this proposal is the mechanism, not the table.

**Alternatives rejected, and why.** *Suppress matches by file type or name* — the fastest route to
the same headline number and the exact failure the laws exist to prevent: the first customer who
builds a request inside a template gets a false clean. *Full type attribution, OpenRewrite-style* —
needs the compilation classpath, and HubbleOps engages a customer repository read-only with no
build; unaffordable, and unnecessary for the cases measured. *Drop the recall layer once structure
covers a language* — recall is what makes the corpus complete; structure is what makes it precise,
and removing either breaks a different half of the proof. *Let the text observer infer context
itself with regexes* — a second, weaker parser competing with the real one, and it would fail on
exactly the multiline and embedded cases that matter.

**Decision.** ACCEPTED by the repository owner on 2026-09-11, together with the answers to Q21 (a
token proved by parse to sit inside a comment resolves `NOT_AFFECTED_WITH_EVIDENCE`, with a separate
documentation-drift count so stale docs stay visible without being a safety claim) and Q22 (a string
literal resolves only when its enclosing definition is itself reached by the graph and no path to a
sink exists, so an incomplete graph reads as uncertainty rather than safety).

Implemented for comments and symbol bindings. Measured on the same commit as the frozen baseline:
752 UNKNOWNs became 624, with **109 resolved to NOT_AFFECTED_WITH_EVIDENCE (every one a `comment`
node, independently audited for false safes: zero) and 19 to AFFECTED** at the version their import
carries. Candidate total unchanged at 1,315, unexplained 0, no candidate absent, no previously
AFFECTED candidate downgraded, and the four statically undecidable sites still UNKNOWN.

Q22's string-literal reachability is **not** implemented yet, and neither is the documentation-drift
count Q21 asks for; both remain open work under this accepted proposal.

Three things a later session must not undo. A file the structural layer did not parse produces no
adjudication, so the recall UNKNOWN survives — absence of adjudication is never a disposition. A
line carrying the subject both in code and in a comment is not adjudicated at all, because the
per-occurrence verdicts disagree and the safe reading is the unresolved one. And a token that is
merely a *substring* of a bound alias is left UNKNOWN, because substring-of-alias reasoning is
unsound; `tests/fixtures/phase1/vendored_sdk` pins exactly that asymmetry.

---

## P-024 — One observation must not become several independent candidates

| | |
|---|---|
| **Raised** | 2026-09-11, Phase 6 |
| **Touches** | **FROZEN**: Candidate identity — `core/schemas/candidate.json`, the `id` description defining identity as `{provider, claim_type, claim_key}` |
| **Status** | ACCEPTED 2026-09-11, not yet implemented |

**What forced this.** `google-ads` appears in both the `identifiers` and the `package_names` lists
of the Google Ads SurfaceSpec. One text match therefore raises two candidates — a
`surface_reference` and a `package_reference` — at the identical `(path, line, subject)`, each with
its own closing instruction, neither aware of the other. Measured on the real-repo loop: **194
triples claimed twice, which is 83.6% of every `package_reference` candidate and 25.8% of all 752
UNKNOWNs.** A quarter of the reported uncertainty is one observation counted twice. This generalises
to any pack whose package name is also a lexical identifier, which is most of them.

**Proposed change.** Establish the invariant: *one physical observation must not become multiple
independent candidates merely because several provider dictionaries matched it.* The preferred shape
is one candidate per observation carrying multiple claims, resolved by evidence, rather than several
candidates each carrying a fragment of the truth. Because candidate identity is keyed on
`claim_type`, this cannot be done without changing what identity means, which is frozen.

**Blast radius.** Candidate identity, therefore every candidate id in every stored ledger, therefore
the frozen real-repo baseline, which would have to be re-frozen under the new identity with an
explicit migration mapping old id to new. `claim_key` in `core/candidate.py`, the resolver's
grouping in `observe/ledger.py`, `verify/conserve.py` (an UNKNOWN's identity is what conservation
tracks), and the Exposure Map's grouping. This is the largest-blast-radius item in the programme and
should land **after** P-023, so its effect is measured against an already-adjudicated corpus rather
than against noise.

**Alternatives rejected, and why.** *Deduplicate at render time* — the Exposure Map would look right
while the ledger and every downstream consumer still carried two truths; a display fix for a model
defect. *Make each pack's categories disjoint* — pushes the problem into pack data where it will
recur silently for every new pack, and `google-ads` genuinely is both an identifier and a package
name, so the data is not wrong. *Drop the lower-precedence claim* — loses the closing instruction
that is correct for manifest sites, where `package_reference` is the right claim and
`surface_reference` is not.

**Decision.** Shape ACCEPTED by the repository owner on 2026-09-11, answering Q23: **one candidate
per observation carrying multiple claims, resolved by evidence.** Not yet implemented — it must land
after P-023 so its effect is measured against an adjudicated corpus rather than against recall
noise, and it requires re-freezing `tests/fixtures/real_repo/glna_unknowns_before.json` under the
new identity with an explicit old-id to new-id mapping, because candidate identity is what the
baseline and `verify/conserve.py` both key on. The invariant is already recorded as a strict
`xfail` in `tests/property/test_adjudication_invariants.py`, so the marker fails the suite the day
the behaviour changes and cannot be forgotten.

---

## P-025 — A request claim and a response read are different claims

| | |
|---|---|
| **Raised** | 2026-09-11, Phase 6 |
| **Touches** | New `claim_type` (open surface); interacts with the frozen verdict's `response_consumer_check_pass` |
| **Status** | OPEN |

**What forced this.** 19 UNKNOWNs are `request_text` anchors on query-language field tokens.
Read at their sites, 9 are PHP genuinely assembling a query and **10 are JavaScript reading fields
off an API response**. All 19 carry the closing instruction "extract the request skeleton with the
structure observer, or capture the executed request dynamically" — an instruction that cannot be
followed at a response read, because there is no request there to extract. A claim whose own closing
instruction is inapplicable at its own site is mislabelled, not merely uncertain.

**Proposed change.** Separate the response read into its own claim type with its own closing
instruction, and route it to the response-consumer class the frozen verdict already checks. These
sites are not noise: a removed or renamed field breaks a consumer just as surely as it breaks a
producer, and FA-019 already established that consumer analysis is a P0-grade concern.

**Blast radius.** `observe/structure.py`, the resolver, and the pack rule bundles that emit the
anchor. `verify/behavior.py` gains a source of consumer sites it does not have today, which can only
strengthen the check. ProofScope moves. The verdict function itself is untouched.

**Alternatives rejected, and why.** *Leave them as `request_text`* — keeps an instruction the site
cannot satisfy, which trains reviewers to ignore closing instructions, the one thing that must stay
trustworthy. *Resolve them as NOT_AFFECTED* — they are real reads of provider-shaped data and a
field removal breaks them. *Handle them only in Phase 5 verification* — discovery would still report
them wrongly, and the Exposure Map is what a customer reads first.

**Decision.** Pending.

---

## P-026 — `SurfaceSpec` gains the shape that keeps recall independent of hand-maintained lists

| | |
|---|---|
| **Raised** | 2026-09-12, Phase 6 |
| **Touches** | `SurfaceSpec` and its `VersionCarrier` / `RequestLanguage` members (FROZEN, §3.1) |
| **Status** | ACCEPTED |

**What forced this.** A scan of `dubinc/dub` at `b8866f4` — a real Google Ads v22 integration —
missed observable contract surface silently, not as UNKNOWN:

- `SELECT conversion_action.id … FROM conversion_action` produced **no candidate at all**, in either
  the text channel or the structural channel. Both channels test the same hand-written resource
  enumeration (`request_languages[].anchors` and the identical regex copied into
  `packs/google_ads/rules/{typescript,javascript,php}.yml`), and `conversion_action` is in neither —
  although `data/catalog_v19.jsonl` carries `conversion_action.*` with PROVEN provenance. §2's
  completeness math requires `P(miss u) = Π_i P(miss u | observer i)`; two channels reading one
  enumeration are not independent, so the product collapses to a single point of failure.
- `type.googleapis.com/google.ads.googleads.v22.errors.GoogleAdsFailure` in a TypeScript file
  yielded `surface_reference` UNKNOWN "carries no version" on a line that literally contains `v22`.
  The carrier that reads it exists but is declared `languages: [python]`, and `VersionCarrier`
  offers no way to say "this identifier is wire-level and therefore language-independent" (§6.6,
  P-008).
- `datamanager.googleapis.com/v1`, a distinct contract adjacent to the Google Ads one, produced no
  record of any kind — silence where an explicit separation belongs.
- OAuth scopes, `developer-token` / `login-customer-id` headers, the
  `customers/{id}/conversionActions/` resource-name template and the `GoogleAdsFailure` error-shape
  read produced no candidate, because no claim type describes them.

**Proposed change.** Four additive members; no existing member changes meaning.

1. `VersionCarrier.scope ∈ {wire, sdk}`, default `sdk`. A `wire` carrier is language-independent by
   construction: the loader **rejects** a `wire` carrier that restricts `languages`. The v22-in-
   TypeScript miss becomes unrepresentable rather than merely fixed.
2. `RequestLanguage` gains `shape` — one grammar-level regex with a named `resource` capture — and
   `known_resources`, which the pack **derives from its own catalog** and never hand-writes. Generic
   layers match `shape` to find every request construct, then classify the captured resource:
   known → `request_text`; unknown → `request_text` carrying `UNRECOGNISED_RESOURCE` → UNKNOWN with
   a closing instruction. A resource introduced in a future provider version surfaces with no code
   change, and the enumeration stops being a recall gate.
3. `adjacent_contracts: [{name, hosts, reason}]` → `EXCLUDED_WITH_EVIDENCE` candidates, so a
   neighbouring API is explicitly separated from this contract with evidence.
4. `contract_surfaces: [{kind, shape}]` — declared *shapes* (header, auth scope, resource-name
   template, endpoint path, response field path). Generic layers use them for the residue sweep:
   inside a file where provider context is already established, a contract-shaped construct whose
   byte range no evidence covers becomes an UNKNOWN residue candidate. This is the general repair
   for the class; items 1–3 are the specific instances of it that the audit happened to expose.

**Blast radius.** `core/surface.py`, `observe/text.py`, `observe/structure.py`, `observe/resolver.py`,
`packs/_protocol.py` re-export, both packs' surface data, and the `google_ads` rule bundles.
`surface_hash` moves, therefore `provider_contract_hash` and every ProofScope move (P-007), and every
existing Receipt is dead — which is the correct outcome for a recall-surface change, per L4. The
Candidate status enum is untouched: `EXCLUDED_WITH_EVIDENCE` and `UNKNOWN` already exist. The verdict
function is untouched.

**Alternatives rejected, and why.** *Add `conversion_action` to the lists* — repairs one example and
leaves the class; the next provider release reintroduces it, and nothing would notice. *Generate the
rule regexes from the catalog but keep the anchors hand-written* — still two artefacts that can drift,
and drift is the defect. *Drop the enumeration and match any `FROM <token>`* — recall without
classification; every SQL query in the repository becomes a Google Ads candidate, which trades a
silent miss for destroyed precision. Deriving the known set from the catalog keeps precision for
known resources **and** surfaces unknown ones as UNKNOWN. *Treat the residue sweep as a linter
outside the ledger* — a finding outside the ledger is exactly the silence this proposal exists to
remove.

**Decision.** Accepted 2026-09-12. Implemented with per-class evals under
`tests/fixtures/coverage/` plus `tests/property/test_coverage_invariant.py`; each fix reverted
independently fails its own eval.

---

## P-027 — The response-consumer check must bind a leaf name to a response read

| | |
|---|---|
| **Raised** | 2026-09-12, Phase 6/7 chain closure |
| **Touches** | `response_consumer_check_pass`, a conjunct of the FROZEN verdict rule |
| **Status** | ACCEPTED |

**What forced this.** `verify/behavior.consumers` watches every removed **and renamed** subject
plus that subject's bare leaf. Measured on the Google Ads v22→v25 lattice:

```
removed 236 changed 1346
distinct leaves 997
generic leaves watched: ['customer_id', 'end_date', 'id', 'name', 'resource_name',
                         'start_date', 'status', 'type', 'value']
```

`id`, `name`, `value`, `type` and `status` are watched as bare identifiers anywhere in the tree,
and `_is_read_position` only excludes a name immediately followed by `:`, which rejects a dict key
and accepts a keyword argument. `service.search(customer_id=…)` — the first line of every Google
Ads integration — is therefore a consumer hit, and `response_consumer_check_pass` is false for
essentially any repository. The check does not distinguish a provider response field from any
identifier in the language, so it reports a failure rate near 100% and is ignored, which is worse
than a check that is merely narrow.

**Proposed change.** The fully-qualified subject watch is unchanged and remains the check's spine;
it fires correctly today (measured on `_mock`: `campaigns.legacy` at `reporting.py:12`). Two
changes to the bare-leaf watch only:

1. A bare leaf is watched only where provider evidence already reaches that file, so the leaf is
   read off something the ledger has tied to the provider rather than off any identifier that
   happens to share a name.
2. The read-position test excludes a keyword argument and a named parameter as well as a dict key.
   A name being *supplied* to a call is a request parameter; a name being *taken* off a value is a
   response read. The two are different positions and the check must tell them apart.

**Blast radius.** `verify/behavior.py` only. The verdict function, the frozen schemas, the
Candidate vocabulary and every other conjunct are untouched. Because this makes a conjunct accept
more than it does today, it is gated on a revert-check table: each corruption in
`tests/adversarial` — including the split-literal P0 and FA-019 / FA-020 — must still be rejected,
proved by running the corpus, and reverting either change independently must fail its own test.

**Alternatives rejected, and why.** *Fix only the read-position test* — still fires on `row["id"]`
in unrelated code, so FAILED stays the default verdict and nothing is gained. *Drop the bare-leaf
watch* — precise, but it loses the destructured read (`const {legacy} = row`) that FA-019
established as a P0 concern; the qualified watch alone cannot see it. *Suppress a hand-written list
of generic names* — an incomplete exclusion list then hides a real field whose name happens to be
ordinary, which is the same defect shape FA-015 and P-026 already rejected. *Leave it alone and
explain the noise* — a conjunct that is false everywhere carries no information, and a proof whose
checks are routinely overridden is not a proof.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-12, in the form
"evidence-gated leaf plus a corrected read-position test". The long-run shape is P-025's
response-read claim supplying the consumer sites directly; this proposal is what holds until P-025
is decided, and it must be revisited when it is.

---

## P-028 — The frozen baseline suite is runner-plural

| | |
|---|---|
| **Raised** | 2026-09-12, Phase 6/7 chain closure |
| **Touches** | `frozen_baseline_tests_pass` inputs; adds a test runner the verifier drives |
| **Status** | ACCEPTED |

**What forced this.** `verify/suites.py` discovers only top-level `tests|test|spec|__tests__`
directories and only `.py` test files, and runs `python -m pytest`; `verify/coverage.py` is a
pytest plugin over `sys.monitoring`. `dubinc/dub`'s suite is vitest under `apps/web/tests`. The
verifier therefore reports `NO_FROZEN_TESTS`, `frozen_baseline_tests_pass` is false, and the
verdict is FAILED regardless of the diff. A repository with a real, passing, relevant test suite is
judged identically to one with no tests at all, which is not a conservative outcome — it is a wrong
one, and it is wrong in the direction that makes the product unusable on most of the JavaScript
ecosystem these integrations actually live in.

**Proposed change.** A runner abstraction inside `verify/`. Test runners are ecosystem facts, not
provider facts, so they stay out of `packs/` and out of the `ProviderPack` protocol — `verify/`
gains a second runner the same way it has a first one, and `test_no_provider_leak` is unaffected.
Suite discovery walks below the repository root instead of only its top level, and reads the
repository's own declaration (`package.json` `scripts.test`, vitest or jest configuration) rather
than guessing. **The repository's own runner is used and nothing is installed into the customer
tree**; a suite whose runner is absent from the staged workspace stays `NO_FROZEN_TESTS` and the
receipt names the runner that was missing, so "the runner was not there" and "there were no tests"
remain different, separately named outcomes.

Coverage is read from the runner's own JSON report as **one aggregate `SuiteCase` per
runner-suite**, whose `files` is the union of covered files and whose `outcome` is `passed` only if
the whole suite passed. This is sound rather than a convenience: `frozen_baseline_tests_pass` is a
conjunct, so if any frozen test fails the verdict is FAILED and the blast radius no longer decides
anything, and if every test passes then the union over the run *is* the union over passing tests,
which is what `UNKNOWN_BLAST = R \ ⋃C(passing frozen tests)` asks for. `radius.blast`'s existing
treatment of uncoverable modules as `UNKNOWN_BLAST` is kept, so a module the runner reports nothing
about is never treated as covered.

**Blast radius.** `verify/suites.py`, `verify/coverage.py`, the receipt's frozen-tests line, and
the Phase 5 fixtures. The verdict function, the frozen schemas and every other conjunct are
untouched. A new external binary the verifier may invoke enters `verifier_version` the same way
`rg` and `ast-grep` already enter `scanner_version`, so a runner upgrade kills the proof, which is
correct.

**Alternatives rejected, and why.** *One runner invocation per test file* — exact per-test
coverage, matching the pytest plugin, but O(N) process starts and coverage passes; on Dub's
`apps/web` that is minutes to hours, and the aggregate is already sound for the one question the
verdict asks. *Run the suite for pass/fail only and leave every JavaScript module in
`UNKNOWN_BLAST`* — honest, but it caps every such repository at `HUMAN_REQUIRED` forever, which
converts a tooling gap into a permanent product limit. *Install a runner into the customer tree* —
the verifier would then be proving a tree it modified. *Put runners in the pack* — a test runner
has nothing to do with a provider, and packing it there would mean every future pack re-declares
the same runners.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-12, with the aggregate
`SuiteCase` as the coverage granularity and the "never install a runner" constraint binding.

---

## P-029 — An unresolved input is one this verification actually consumed

| | |
|---|---|
| **Raised** | 2026-09-12, Phase 6/7 chain closure |
| **Touches** | the `unresolved` input to the FROZEN verdict rule's `UNKNOWN` branch |
| **Status** | ACCEPTED |

**What forced this.** `verify/verdict.py` is pure and total and stays untouched: `UNKNOWN iff
ORACLE_UNAVAILABLE or any input unresolvable`. The defect is in what `verify/audit.py` puts into
`unresolved`, measured on `python_pinned_v22` — a three-file repository whose only Google Ads call is
one GAQL `SELECT` — after the binding, grammar, detection and consumer repairs land:

```
VERDICT   UNKNOWN
REASONS
  UNRESOLVED: a request at src/reporting.py:4 never reached the oracle, …
  UNRESOLVED: a request at src/reporting.py:5 never reached the oracle, …
  UNRESOLVED: a request at src/reporting.py:6 never reached the oracle, …
  UNRESOLVED: contract subject accessible_bidding_strategy.target_impression_share.location
  UNRESOLVED: contract subject account_budget.pending_proposal.proposal_type
  …
```

Two distinct unscoped sets:

1. **Lattice-wide contract subjects.** `audit.run` adds one `unresolved` entry per
   `changes.unresolved()` — every `UNKNOWN_PROVIDER_CONTRACT` subject in the whole v22→v25 lattice.
   Measured: 1,346 `CHANGED` facts, of which the undecidable ones number in the hundreds. This
   repository mentions none of them. A subject no evidence in this run names is not an input to this
   proof, and binding it to this verdict means **every** Google Ads verification is `UNKNOWN`
   forever, whatever the repository contains.
2. **Recall anchors reported as unreached requests.** `oracle.requests_from` adds
   `src/reporting.py:4/5/6` to `unreachable` because the text observer raised a `request_text`
   anchor on each GAQL line. Those are recall-layer matches on lines *inside* one query, not three
   requests. The one actual request — the resolved skeleton at `src/reporting.py:3` — does reach the
   oracle and is decided. Calling a line of a query "a request that never reached the oracle" is a
   mislabelling, and it too caps every repository at `UNKNOWN`.

**Proposed change.** Scope both sets to this run's own inputs. (1) A contract subject enters
`unresolved` only when this run's evidence names it, using the same `core/subjects` scanner the
audit's extinction check already uses, so the engine, the audit and this set share one definition of
"this repository mentions the subject". (2) A `request_text` record contributes to `unreachable` only
when it carries a request skeleton that could not be resolved — an anchor whose own resolved request
reached the oracle from the same site contributes nothing.

Neither change touches `verify/verdict.py`, the frozen schemas, or any conjunct. What changes is
which facts are presented to the frozen rule as inputs of *this* verification. A genuinely
undecidable subject the repository does use still lands in `unresolved` and still caps the verdict at
`UNKNOWN`, which is L2 working correctly.

**Blast radius.** `verify/audit.py` and `verify/oracle.py`; the Receipt's UNRESOLVED list; every
Phase 5 adversarial expectation that relies on an unresolved entry. The verdict function, the
schemas, the Candidate vocabulary and the obligation grammar are untouched.

**Alternatives rejected, and why.** *Leave it and explain the noise* — a verdict that is `UNKNOWN`
for every repository carries no information, and a proof nobody can ever reach is not conservative,
it is broken. *Drop `changes.unresolved()` from the audit entirely* — then a subject the repository
genuinely uses and the lattice genuinely cannot map would pass silently, which is the L2 violation
this entry exists to prevent. *Filter the display but keep the input* — the Receipt would read
clean while the verdict stayed `UNKNOWN` with no visible cause, which is worse than the noise.
*Treat an unreached anchor as a failed request* — it would turn a recall match into a `FAILED`
verdict, inventing a defect out of a lexical hit.

**Decision.** ACCEPTED by the repository owner (Jaya Krishna J) on 2026-09-12, scoping both sets to
this run's own inputs. `verify/verdict.py` stays byte-identical; only what is presented to it as an
input of this verification changes.

---

## P-030 — Per-language precise indexers behind the graph, with ast-grep as the net

**Status.** DECIDED 2026-09-13. Raised 2026-09-12 from the owner's direction to make discovery
world-class.

**Problem.** The structural layer resolves symbols with hand-written ast-grep rules and a
home-grown import table per language. On `woocommerce/google-listings-and-ads` that leaves 290
identifier sites UNKNOWN in files whose own imports already declare V23, and every new language
costs a new resolver. The industry answer (Google's Kythe, Moderne's type-attributed LST,
Sourcegraph's SCIP indexers, Semgrep's per-language reachability) is one shared repository model fed
by per-language precise indexers, with search as the net.

**Proposed change.** `graph/` accepts symbol and reference data in a SCIP-shaped, provider-neutral
form. A language adapter produces it: a SCIP indexer where one exists and is proof-bound by version
(`scip-typescript`, `scip-python`, `scip-php` first, the three languages seen on real repositories),
ast-grep where none exists. A file with no precise index keeps its recall claims as UNKNOWN,
visibly — never a silent fallback to search (the documented Sourcegraph failure mode). The
provider pack keeps mapping contract subjects onto the graph; it never learns a language.

**Boundaries touched.** New external binaries (approval boundary: "no new frameworks or services
without proposal"). Indexer identity must enter the ProofScope `rules_hash`/`scanner_version` like
ast-grep does. No frozen schema changes: evidence `value` already carries node kind and binding.

**Not in scope.** Running the indexers on the customer's behalf requires the customer's
dependencies installed in the sandbox; absence stays UNKNOWN with a closing instruction.

**Decision.** DECIDED 2026-09-13 by measurement (PART NINE of `dev/plan.md`), under the owner's
instruction to decide without asking: TypeScript/TSX/JavaScript and Python (Linux host) are GO
and implemented in `core/precise.py` + `graph/indexers.py`; PHP, Java and .NET are recall-only
with the enabling sentence on the map. Nothing is vendored; P-034 carries the shipping question.
F5's file-level version binding landed earlier as the cheap subset that needed no binary.

---

## P-031 — GoogleAdsFieldService as the primary field source of the Google Ads pack

**Status.** OPEN. Raised 2026-09-12.

**Problem.** The pack reconciles the public Query Builder field reference against the public
protos and marked a field undecidable unless both listed it. Google's own per-version field
catalog is `GoogleAdsFieldService` (`/vNN/googleAdsFields`), exposed as `get_resource_metadata`
by Google's official open-source MCP server. It is the authoritative machine-readable inventory,
but it needs Google Ads API access, so it cannot be read at scan time under P-020.

**Proposed change.** `hops pack refresh` gains a `field_service` source family fetched once per
version at pack-build time with HubbleOps's own API access — never a customer's — and snapshotted
with hashes like every other family. Reconciliation order becomes: field service (authoritative
for GAQL fields), Query Builder reference (corroborating), protos (messages, services, enums).
Disagreement between present sources stays `UNKNOWN_PROVIDER_CONTRACT`; absence of a source is
never a conflict (that rule lands now under F1, independent of this proposal).

**Boundaries touched.** Pack data manifest schema (`families`), the refresh command's inputs, and
the lattice hash. No generic layer changes.

**Decision.** Pending. Blocked on HubbleOps holding its own Google Ads API access.

---

## P-032 — Dependency provisioning for the frozen suite, without egress from verify

**Status.** PROPOSED. Raised 2026-09-12 from Tier 3a (PART EIGHT of `dev/plan.md`).

**Problem.** `verify/suites.py` runs the repository's own test runner in a staged copy of the
candidate tree and links the dependency directories it finds in the customer checkout
(`vendor/`, `node_modules/`). A cold clone has neither, so on both ground-truth repositories the
frozen suite is `NO_FROZEN_TESTS`/`TOOLING_MISSING`, `frozen_baseline_tests` is unresolved and
the verdict is UNKNOWN whatever the diff says. P-028 forbids installing anything into the
customer tree, and every Law forbids a network call inside verify. Measured this session:
`google-listings-and-ads` additionally bootstraps PHPUnit through the WordPress test library
(`WP_TESTS_DIR`, a MySQL database, a WooCommerce checkout), which no installer provides.

**Proposed change.** Provisioning is its own step, before verify, and verify consumes its
transcript as an input the way it consumes a capture manifest.

- `hops provision <repo> --ecosystem <php|node|python>` runs the ecosystem's own installer in a
  provisioning workspace that is a copy of the checkout, never the checkout: `composer install`,
  `pnpm install --frozen-lockfile`, `npm ci`, `uv sync --frozen`. The installed directories are
  what `runners.link_dependencies` later links into the frozen and candidate workspaces.
- Two sources, both hash-recorded, never open egress: an offline cache (`--cache <dir>`, e.g. a
  mirrored composer cache or a pnpm store, with `--offline` passed to the installer), or an
  allowlisted registry set (`--allow https://registry.npmjs.org`, `--allow https://repo.packagist.org`)
  routed through the existing egress proxy in `sandbox/proxy.py`, so every host the installer
  reaches is either allowlisted or denied and logged.
- The transcript (`provision.json`) records the lockfile hash, installer identity and version,
  the allowlist or cache path, every fetched package with its integrity hash, exit status and
  the proxy's denial log. `verification_inputs_hash` binds it; a transcript whose lockfile hash
  does not match the base tree is refused.
- Test infrastructure the installer cannot provide (a database, a WordPress test library, a
  browser) is out of scope: the runner reports `EXECUTION_FAILED` with the bootstrap's own
  message, the conjunct stays unresolved, and the Receipt names it. The generated verification
  Action is where such suites run, on the customer's own runner with their own services.

**Boundaries touched.** A new verb (`hops provision`); no schema change (the transcript is an
input file, its hash enters `verification_inputs_hash` like captures); no change to
`verify/verdict.py`; sandbox egress stays behind the proxy allowlist that Phase 4 already proved.
This proposal also records a standing deviation from §9's "own image": today `verify/suites.py`
runs the customer's own test runner on the host through `bounded_process` while the verifier
image is fingerprinted into the ProofScope and proved isolated; moving suite execution into that
image is the second half of this proposal and lands with provisioning, since a suite without its
dependencies inside the image cannot run there either.

**Alternatives rejected, and why.** Installing inside verify would put a network call in the
authority and let the installer's output change what the authority judges. Vendoring
`node_modules`/`vendor` into the Receipt scope would make every proof enormous and still not
cover suites that need services. Skipping the frozen suite when dependencies are absent would
turn an unresolved conjunct into a silent pass.

**Decision.** Pending the owner's yes. Until then verify runs the repository's own runner from
whatever the checkout provides and reports the absence.

---

## P-033 — Standing deviations from §6.4 and §11 the gate audit found unrecorded

**Status.** RECORDED 2026-09-13 (the Tier 3a re-audit asked for a written justification; none
of these changed in this tier).

- §6.4 names a wrapper walk of k = 5 hops. `cf9a655` replaced the cap with a resolution budget
  and a memoised walk that runs to a fixed point (`observe/structure.py`, `ResolutionBudget`,
  `RESOLUTION_BUDGET_EXHAUSTED`). Strictly more conservative than a cap: a chain deeper than five
  hops resolves instead of being reported UNKNOWN, and exhaustion is a named UNKNOWN rather than a
  silent truncation. The DoD of Tier 2 required it ("no hop limit").
- §11 lists NetworkX, coverage.py and SCIP. The import graph is an in-house structure in
  `graph/imports.py` (deterministic ordering and a `SourceRange` identity NetworkX does not give
  for free), coverage is a `sys.monitoring` plugin in `verify/coverage.py` (per-test file sets,
  no `.coverage` SQLite in the proof scope), and SCIP is P-030. Runtime dependencies stay
  `jsonschema`, `pyyaml` (P-001), `referencing` (P-002).
- §11 says SCIP indexes sit "behind a flag". Tier 3b (2026-09-13) runs a precise indexer when
  it is found on PATH and reports recall-only per language when it is not; there is no flag
  because absence is already the visible off state and a flag that hides a present indexer
  would be a silent loss of precision. `indexer_executables` on `scan_repository` is the
  programmatic override. `--force` continues past a present indexer that fails, with the
  ProofScope segment `+forced-recall-only` so the two runs never share a scope.
- §3.2 says `ObserverContext` carries only pack-provided parts and run metadata. Tier 3b adds
  `precise_indexes: tuple[SymbolIndex, ...]`, an analysis artifact computed by `app/` before the
  observers run, so that the structure observer and the coverage report read the same objects.
  It is data, provider-neutral, defaulted to empty, and the Observer signature is unchanged.
  **Superseded by P-035**: §3.2 is FROZEN, so this is a change to a frozen surface and not a
  deviation to record. It awaits a human decision there.
- §10's listing omits `core/precise.py` (the SCIP reader lives in core because
  `core/observer.py` may not import `graph/`) and `graph/indexers.py`.

**Decision.** These are documented deviations, not proposals to change anything. §6.4 and §11
should be amended to match when the architecture document is next edited under a proposal.

---

## P-034 — Shipping the precise indexers with the wheel

**Status.** PROPOSED. Raised 2026-09-13 from Tier 3b (PART NINE of `dev/plan.md`), which
decided P-030 by measurement: TypeScript/TSX/JavaScript and Python are GO, PHP, Java and .NET
are recall-only until their toolchains exist in the sandbox.

**Problem.** The scan now runs `scip-typescript` and `scip-python` when it finds them and binds
`<version>+sha256:<entry script>` into `scanner_version`; when it does not find them the map says
so per language and every claim stays as it was. Nothing is vendored, so a customer only gets
precision by installing the indexer themselves, and the identity binds the entry script rather
than the whole installed package tree (the bundled `typescript` compiler and `@types/node`
appear inside symbols and come from that tree).

**Measured cost of vendoring.** `node.exe` 82.8 MB; `@sourcegraph/scip-typescript@0.4.0` with
its dependencies 24 MB (typescript 3.2 MB of that); `@sourcegraph/scip-python@0.6.6` 27 MB; the
combined `node_modules` 90 MB. Against a wheel that today vendors ripgrep and ast-grep at a few
MB each, this is a different order of size, and node is a runtime, not a binary.

**Proposed change.** Ship the two npm packages as a pinned archive per platform in
`toolchain.json` exactly like `rg` and `ast-grep` (archive sha256, member list, binary sha256 of
the entry script), and require a node runtime on PATH with a pinned minimum version whose
identity (`node --version` plus its sha256) joins the `scanner_version` segment. The package
tree hash replaces the entry-script hash as the identity. The sandbox and verifier images carry
the same archives, and the Linux image is the only host for `scip-python` (it does not start on
Windows, measured). Not proposed: vendoring node itself.

**Boundaries touched.** New binaries in the wheel (approval boundary). `core/toolchain.py`
gains an archive-of-files entry type. No schema change; `scanner_version` is free text.

**Alternatives rejected.** Vendoring node (82.8 MB per platform, and a runtime the customer
already has if they have TypeScript). Running the indexers through a hosted service (network in
the scan path, forbidden). Pretending: a scan without the indexer says recall-only today and
keeps saying it.

**Decision.** Pending repository-owner approval of the wheel size and the node requirement.

---

## P-035 — `ObserverContext` admits a precise index

| | |
|---|---|
| **Raised** | 2026-09-13, Phase 6 (engine-v0) |
| **Touches** | `docs/ARCHITECTURE.md` §3.2, the FROZEN Observer contract |
| **Status** | OPEN |

**What forced this.** §3.2 freezes `ObserverContext` as carrying "only pack-provided parts
(surface, rules, hooks) and run metadata". Tier 3b added `precise_indexes: tuple[SymbolIndex, ...]`
to it (`core/observer.py`), which is neither: it is an analysis artifact `app/` computes before the
observers run, so the structure observer and the coverage report read the same symbol objects
rather than each re-deriving them. P-030 decided the indexer capability and said explicitly "no
frozen schema changes"; it did not ask for this field. P-033 then recorded the field as a standing
deviation, and a standing deviation is not how a frozen surface changes — CLAUDE.md permits exactly
one route, a proposal a human accepts. The Phase 6 gate audit named this as blocker (4) and it is
correct: the code and the frozen contract disagree, and no human has ruled on the difference.

**Proposed change.** Amend §3.2's comment to: "`ObserverContext` carries pack-provided parts
(surface, rules, hooks), run metadata, and provider-neutral analysis artifacts `app/` computed for
this run. No pack import." The field itself is already implemented and unchanged by this proposal:
`precise_indexes: tuple[SymbolIndex, ...] = ()`, provider-neutral, defaulted to empty, with the
`Observer.scan` signature untouched.

**Blast radius.** None in code — this records what already ships. `docs/ARCHITECTURE.md` §3.2 is
edited. Existing Receipts survive: `SymbolIndex` identity is already inside `scanner_version`, so a
run with indexes and a run without them do not share a ProofScope, and nothing about the binding
changes. P-033's fourth bullet is superseded by this proposal and should point at it.

**Alternatives rejected.** *Remove the field and pass indexes as a separate argument to `scan`* —
that changes `Observer.scan`, which is the more central half of the same frozen section, and every
observer would carry a parameter only one of them reads. *Recompute the index inside the structure
observer* — two readers of one artifact drift, and the coverage report would run the indexer a
second time inside the scan path. *Leave it as a P-033 deviation* — that is the state the gate
audit failed, and it leaves the frozen document describing a contract the code does not honour.

**Decision.** Pending repository-owner ruling. If rejected, the field comes out and `app/` passes
the index by another route the owner names; the capability itself is P-030's, already DECIDED, and
is not reopened here.

---
