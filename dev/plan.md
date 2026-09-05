# Plan — Phase 2 — Google Ads Provider Pack and offline Change Pack lattice

Written before implementation on `phase-02-google-ads-pack-and-change-pack`. Every question is
answered below. A fresh reviewer must approve this plan before implementation starts.

## Scope and maturity

**Classification:** Build. Phase 1 has a fresh `GATE: PASS`; Phase 2 is permitted by frozen
Architecture §3.1 and §7.1 and accepted proposals P-005 through P-008.

**Outcome:** HubbleOps can load complete `_mock` and `google_ads` ProviderPacks, reproduce a
provenance-bearing Google Ads contract lattice for major versions v19 through the current major,
compute adjacent and multi-hop changes without hand-authored diffs, parse wire and Cloud Console
records without language-specific code, and prove validation requests cannot mutate provider state.

**Current boundary:** On the 2026-09-04 source snapshot, `v25` is the current major endpoint and
the v25 catalog includes the latest published backward-compatible v25 minor refresh. Minor releases
refresh their major node because Google publishes endpoints and protos by major version; they do not
become separate breaking-change nodes. The historical lattice still includes sunset v19-v21 because
the phase explicitly requires v19 through current and real repositories still contain those calls.

**Appetite and stop:** one phase branch, no production deployment, credentials, live oracle,
obligations, verification verdicts, repair, capture hooks, or language rules. Stop on an
unreconcilable frozen-contract conflict, a required production dependency, an unhashable source,
or any path that could send a non-validation provider request.

## Evidence and rationale

Official Google sources establish the node and ingestion decisions:

- Versioning documents major endpoints as the compatibility boundary and minor versions as
  backward-compatible updates to an existing endpoint.
- The release and sunset pages identify v25 as the current major and provide release/sunset dates.
- The upgrade page publishes version-to-version proto difference tables.
- Google's Query Builder schemas expose current GoogleAdsFieldService metadata without credentials.
  The removed v19 catalog comes from version-checked Internet Archive captures of every resource in
  the official v19 overview; live FieldService calls are deliberately outside this offline gate.
- The client-library page publishes the language/version compatibility table.
- Google request-logging examples show the version in the gRPC/REST path. `x-goog-api-client`
  identifies client runtime/library versions, not reliably the Google Ads API endpoint version.

Accepted P-008 corrects P-006 safely: a wire observation may include `x-goog-api-client`, but the API
version must come from the versioned request target. Header-only input and any conflicting signal
return typed UNKNOWN with a reason; they are never guessed into a tuple or dropped.

## Deliverables and boundaries

### Provider contract

`hubbleops/packs/_protocol.py` will contain typed, runtime-checkable protocols and immutable value
records only for Version, typed wire parse results, catalog/diff results, and validation results.
Future-slot protocols remain minimal. Protocol docstrings are the only new code docstrings/comments
permitted by `CLAUDE.md`.

Both packs expose one object satisfying the frozen ProviderPack slots:

- `_mock` has the frozen two fixed nodes, a tiny hand-written source
  catalog, deterministic diffs, an in-memory safe validator, REST wire parsing, CSV telemetry, and
  explicit empty implementations for future-phase slots.
- `google_ads` loads its existing surface plus the shipped source lattice, Google wire parser,
  Cloud Console telemetry parser, safe ContractOracle, and explicit empty future-phase slots.

`app/registry.py` is the only production importer of pack modules. Generic `core/`, `closure/`,
`observe/`, `store/`, and later layers continue to receive pack parts as parameters and contain no
provider name.

### Hashed source and catalog pipeline

`hubbleops/packs/google_ads/data/sources/` is the only offline compiler input. A canonical manifest
binds every input file to SHA-256, retrieval time, upstream URL, version, and source kind. Inputs are
normalized snapshots produced from:

1. googleapis proto contents for v18-v25, where v18 exists only as the v19 comparison baseline;
2. public per-version Google Ads field-reference metadata corresponding to
   GoogleAdsFieldService: selectable, filterable, sortable, data_type, and selectable_with;
3. structured claims from the official upgrade guide and release notes;
4. client-library compatibility and release/sunset metadata.

The implemented acquisition boundary uses Google's Query Builder `fields_index.json` plus every
listed resource schema for v20-v25. It uses the official v19 overview as the complete 169-resource
inventory and resolves version-checked Internet Archive captures for every resource. Truncated
captures are rejected and alternate captures are tried. Original Google URLs and exact archive URLs
are retained. A redirect or archive response containing a different major is a hard failure, never
relabelled data. This evidence-driven refinement replaced the earlier tentative v20-v21 archive-page
approach because the Query Builder publishes complete versioned schemas derived from FieldService.

The refresh path retains fetched raw bytes, hashes them before parsing, and records the normalized
snapshot hash beside the raw hash. The manifest records expected proto path/symbol counts and every
field-reference resource/page/field count. Missing, duplicate, or unparsed inputs are fatal; each
parser must account for its complete input inventory, so a reproducibly partial parse cannot pass.
The offline build path performs no network calls, verifies every manifest hash before use, and emits sorted canonical
`data/catalog_v19.jsonl` through `data/catalog_v25.jsonl`. Adding a new major is data-driven: add its
manifest entries and source snapshots, then invoke the same compiler. No source-specific branch may
name v25 as a special case.

Every catalog fact contains `subject`, `kind`, normalized attributes, `source_url`, `retrieved_at`,
`sha256`, and confidence `PROVEN` or `DOCUMENTED`, plus corroborating provenance where applicable.
Applicability is mechanical: service, message, and enum facts are proto-only; a subject exposed by
the field inventory requires both proto and field-source reconciliation. Proto-only structural facts
are PROVEN only for those non-field kinds. A field/resource fact
is PROVEN only when proto and field metadata agree. Guide/release-note, client-compatibility, and
release/sunset facts are provider-documentation facts and therefore DOCUMENTED unless a second
applicable source is explicitly reconciled.
A missing counterpart, unequal normalized type, or unequal presence for an applicable subject is a
material contradiction. It is retained as a typed pack conflict and yields
`UNKNOWN_PROVIDER_CONTRACT`; the compiler never uses an LLM or chooses a convenient source.

The build report records input hashes, per-version catalog hashes/counts, and the full lattice hash.
Two offline builds from the same source directory must be byte-identical and have the same report.

### Catalog, diff, and composition semantics

`catalog(version)` validates the requested lattice node and reads exactly its catalog. Adjacent
`diff(a,b)` is computed from normalized subject sets and attributes, annotated by structured docs,
and cached under `sha256({ordered_from_catalog_hash, ordered_to_catalog_hash})`. Version labels are
not cache authority. No diff data file is an authority.

Non-adjacent `diff(a,c)` composes every ordered adjacent hop. A subject that survives every hop maps
to the same result as `compose(diff(a,b), diff(b,c))`. Zero or one exact mapping may compose;
multiple replacements, contradictory mappings, missing intermediate nodes, or a source conflict
yield `UNKNOWN_PROVIDER_CONTRACT` with provenance. Removal without replacement remains removal and
is not treated as ambiguous. Reverse diffs are rejected rather than inferred.

Known v25 removals `CustomerLifecycleGoal` and `CampaignLifecycleGoal` and known current-v25 field
addition `Campaign.aca_migration_date_time` must be computed from the v24/v25 catalogs with PROVEN
confidence. A hand-checked v19→v25 fixture covers at least six consecutive hops and must match the
composed result.

### Validation safety

The Google oracle first validates Search/GAQL field references against the selected local catalog.
A field absent from a complete, conflict-free catalog is INVALID; incomplete or conflicting catalog
state is `UNKNOWN_PROVIDER_CONTRACT`. It then accepts an injected `Transport`; the shipped default
is unavailable and performs no I/O. Search uses `SearchGoogleAdsRequest` with `validate_only=true`.
SearchStream queries are validated through the semantically equivalent Search validation endpoint,
because `SearchGoogleAdsStreamRequest` has no validate-only field. Mutate validation always
overwrites/sets `validate_only=true`, allows only known validation-capable operations, and has no API
for a non-validation mutation. A fake transport records the exact call and operation.
Missing credentials/transport, unsupported operation, provider error, or ambiguous catalog state
returns `ORACLE_UNAVAILABLE` or `UNKNOWN_PROVIDER_CONTRACT`, never pass. Live Google execution is
Phase 5.

### Wire and telemetry

The wire parser accepts normalized path plus headers and recognizes:

- real gRPC paths such as
  `/google.ads.googleads.v25.services.GoogleAdsService/SearchStream`;
- REST paths such as `/v25/customers/123/googleAds:searchStream`;
- only additional wire forms backed by retained official logged-path fixtures.

Under accepted P-008, `x-goog-api-client` is accepted and tested as accompanying metadata but is
never mistaken for an API version. Wire parsing returns a typed MATCH or UNKNOWN result; missing
components, header-only input, and conflicting signals return UNKNOWN with a reason and the original
input provenance, never absence.

Telemetry accepts RFC 4180 CSV whose header is `Method`, `method`, or a qualified header ending in
`.method`, matching the Google Ads API Dashboard Methods table and Cloud Monitoring CSV export. Each
method cell must contain the documented fully-qualified
`google.ads.googleads.vNN.services.Service.Method` value. Other metric columns are preserved as
uninterpreted input. Every malformed row yields a typed issue carrying its row number and raw value
while valid rows remain available; a missing method column is a file-level issue and no row
disappears. Fixtures cite the Google Ads sunset-page Methods example and Cloud Monitoring's
documented Download CSV workflow.

### ProofScope and Exposure Map completion

The full lattice hash and existing surface hash are composed into
`provider_contract_hash = sha256({surface_hash, lattice_hash})`; neither can replace the other.
Property tests mutate every surface field and every lattice node/fact and prove the proof key moves.
The scan stores lattice/target metadata only in the existing non-frozen `runs.closure_json`; it does
not add to ProofScope or overload the run target. Exposure reads only stored run data and never
reopens a pack.

The frozen Exposure Map is completed by normalizing version labels, printing per-version site
counts plus `UNKNOWN (n)` and labelling the pack with the stored Change Pack lattice hash. A target
is shown only when client-compatibility data resolves the installed SDK line; unresolved or multiple
incompatible SDK lines print an explicit UNKNOWN target rather than hardcoding v25. The extra final
`EXCLUDED` expansion line is removed while its Discovery count remains, per P-004.

### CLI and packaging

`hops pack verify google_ads` verifies manifest hashes, rebuilds catalogs in a temporary location,
compares them byte-for-byte with shipped catalogs, checks the full lattice, performs no network I/O,
and exits nonzero with a precise reason on tampering. A socket-denial guard makes any network attempt
fail the test. `hops pack verify _mock` exercises the same contract-level checks where applicable.
A built-wheel inspection and isolated install prove JSON/JSONL/YAML source and catalog data ship.

## Definition-of-done mapping

| DoD | Observable completion |
|---|---|
| 1. Full protocols and two packs | Runtime conformance suite parametrized over frozen two-node `_mock` and `google_ads`; every slot called and typed; Google supplies multi-hop composition coverage |
| 2. v19-current catalogs from four source families | Seven canonical catalog files; retained raw/normalized hashes and expected inventory counts prove total parsing; facts span proto, field, docs, and compatibility sources with required provenance |
| 3. Cross-check | Agreement, docs-only, and synthetic disagreement tests return PROVEN, DOCUMENTED, and UNKNOWN_PROVIDER_CONTRACT respectively; source contains no AI path |
| 4. Contract oracle | Local catalog field checks, adjacent/non-adjacent diff properties, catalog-hash cache key, and fake-transport validate-only/unavailable behavior pass |
| 5. Wire signature | Under P-008, a corpus of real documented gRPC/REST/header combinations returns typed MATCH/UNKNOWN with no language input; conflicts and header-only ambiguity are explicit |
| 6. Telemetry | CSV Cloud export fixtures parse to exact generic tuples and row-provenance issues; malformed rows are explicit |
| 7. Offline reproducibility | network-disabled rebuild equals shipped bytes; tamper test fails; `hops pack verify google_ads` exits 0 offline |
| 8. Known changes and multi-hop | both v25 lifecycle removals and `Campaign.aca_migration_date_time` are PROVEN; v19→v25 expected composition fixture matches |

## Success measures

Engineering completion requires all seven major catalogs present, 100% manifest entries hash-valid,
100% catalog facts carrying required provenance, two byte-identical clean builds, every computed hop
covered, zero non-validation fake-transport calls, and the full repository suite/static checks green.

Product success is not claimed by shipping code. The post-gate real-repo loop must scan two or three
public Google Ads repositories, preserve every UNKNOWN with a closing instruction, produce
`UNEXPLAINED=0`, and record every genuinely new pattern in `docs/FAILURE_ATLAS.md`. Evidence from that
loop determines Phase 3 fixtures; it does not retroactively rewrite provider facts.

## Non-negotiable invariants

- Laws L1-L11 and all frozen schemas/interfaces/layouts remain intact.
- Generic layers never import `packs/` or name Google Ads.
- Surface and full lattice both move `provider_contract_hash`.
- No hand-authored authoritative diff, LLM adjudication, unpinned source, or silent disagreement.
- Every network response is cached and SHA-256-bound before parsing; offline verification performs
  no network access.
- No live credentials and no callable non-validation mutation path.
- `UNKNOWN_PROVIDER_CONTRACT` and `ORACLE_UNAVAILABLE` are pack result codes only, never Candidate
  statuses or verdict values.
- Unsupported, ambiguous, malformed, missing, or conflicting provider information fails closed to
  an explicit unknown/unavailable result.
- Canonical outputs are deterministic, sorted, content-addressed, and timestamped only by the fixed
  source retrieval metadata.
- Existing unrelated working-tree changes are preserved.
- No comments/docstrings outside the protocol exception and tool-required suppressions.

## Authority

The implementation may inspect and refactor scoped internals, use read-only official network
sources, create/update source snapshots and required JSONL fixtures, add tests, run local tools,
and make reversible implementation choices. It may not add a dependency, use credentials, issue a
provider write, change a frozen schema/contract meaning, commit, push, publish, deploy, or release
without explicit human approval.

## Outside scope

No obligations, repair transforms, repair tools, language rule files, dynamic capture, sentinel,
verification verdict, Receipt, live Google oracle, scheduled automation, dashboard, recommendation
engine, or generic multi-provider framework beyond the two required implementations.

## Risks, assumptions, and alternatives

1. **Highest: stale/incomplete provider facts.** Mitigation: official primary sources, pinned
   retrieval metadata, full manifest verification, cross-source reconciliation, and conflict
   preservation. A partial catalog cannot pass verification.
2. **False rename composition.** Mitigation: compose only unique structured mappings corroborated by
   subject deltas; all ambiguity becomes UNKNOWN_PROVIDER_CONTRACT.
3. **Header version confusion.** Mitigation: never infer the endpoint from client-library version
   tokens in `x-goog-api-client`.
4. **Proof reuse after data edits.** Mitigation: every source/catalog/lattice and surface mutation
   has a property test proving the ProofScope key changes.
5. **Online-only build.** Mitigation: network-disabled verification uses only shipped source inputs.
6. **Data volume/package cost.** Keep normalized source snapshots rather than redundant raw site
   chrome while retaining upstream byte hashes and complete machine-relevant facts.

Alternatives rejected: one hand-built v22→v25 diff; treating minor releases as breaking nodes;
querying live FieldService at gate time; trusting docs without proto/field cross-check; deriving API
version from client library metadata; and introducing a provider framework beyond the two packs.

## Verification and evidence

The final report must include command, output, and exit code for:

```text
uv run hops pack verify google_ads
uv run pytest -q tests/unit/test_pack_conformance.py
uv run pytest -q tests/unit/test_google_ads_changes.py tests/property/test_change_composition.py
uv run pytest -q tests/unit/test_google_ads_contract.py
uv run pytest -q tests/unit/test_google_ads_wire.py tests/unit/test_google_ads_telemetry.py
uv run pytest -q tests/integration/test_google_ads_change_pack.py
uv run pytest -q
uv run pytest -q tests/unit/test_imports.py tests/unit/test_no_provider_leak.py
uv run ruff format --check .
uv run ruff check .
uv run pyright
git diff --check
```

Evidence also includes the compiler build report with all source and catalog hashes, ten catalog or
diff facts spanning all source kinds, the v19→v25 composition example, the fake transport call log,
the logged-path corpus result, two catalog-build directory hashes, and a network-denied offline run.

## Release, learning, and architecture record

This phase is local and unreleased. Rollback is removal of Phase 2 code/data while the Phase 1 gate
remains independently valid. No migration or credential handling exists. P-005 through P-008 already
record the architectural decisions; no new ADR is needed unless implementation forces a different
trust boundary, data meaning, or compatibility promise.

After the Phase 2 fresh gate says `GATE: PASS`, run the required fresh real-repo loop. Continue only
when every encountered item has one of the four UNKNOWN dispositions and new patterns are converted
to anonymized fixtures or explicitly scheduled for the correct later phase.

## Working method and escalation

Inspect first, make routine reversible choices autonomously, preserve unrelated edits, and continue
through evidence, fresh gate audit, and real-repo loop. Stop only for a frozen-contract conflict,
an unsafe/non-validation call, missing authoritative source that would otherwise be guessed, a new
dependency, credential/cost requirement, or an excluded feature. Report exact evidence and the
smallest required human decision.

## Open questions — all answered

1. **What is “current”?** v25 major at the pinned 2026-09-04 retrieval; latest published minor data
   refreshes the v25 node. Future releases enter through the same manifest-driven pipeline.
2. **Are sunset v19-v21 nodes included?** Yes; the explicit v19-current requirement and migration
   detection need historical nodes even when live calls no longer succeed.
3. **Does this gate call GoogleAdsFieldService live?** No. It ingests the official public field
   reference generated from that catalog; v19-v21 use pinned, version-checked archives of the
   original official pages. Live credentialed comparison is Phase 5.
4. **Can `x-goog-api-client` alone select an API version?** No. It is accompanying metadata only;
   a versioned request target is required and disagreement returns typed UNKNOWN.
5. **How are docs-only mappings treated?** DOCUMENTED, and only uniquely composable mappings may be
   followed. Ambiguity or source conflict is UNKNOWN_PROVIDER_CONTRACT.
6. **Is a new dependency required?** No. Parsing, hashing, canonicalization, CSV/JSON, and proto
   inventory extraction use the standard library and existing project utilities.
7. **What happens to Phase 1’s uncommitted remediation?** It remains preserved on this branch, is
   itemized separately in `dev/context.md`, and is the exact tree that earned the Phase 1 gate pass;
   no commit/push occurs without explicit approval.
