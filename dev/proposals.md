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
| **Status** | OPEN |

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

**Decision.** Pending — needs the repository owner. The code has shipped this way since Phase 1
implementation; a REJECTED decision means moving the definition and re-running the Phase 1 gate.

---

*(P-003 open)*
