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
| **Status** | PROPOSED |

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

**Decision.** PENDING. Blocked twice over: `.claude/hooks/guard.py` refuses every write under
`hubbleops/core/schemas/` past phase 1 and has no notion of an accepted proposal, so the owner's
`_accepted_proposal_names()` edit (already queued for P-012) must land before this or P-012 can be
implemented at all.

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
