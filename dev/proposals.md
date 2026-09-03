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

---

*(no open proposals)*
