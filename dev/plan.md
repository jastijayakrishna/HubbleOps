# Plan — Phase 1 — Source Closure + text/dependency observers + Ledger + Exposure Map

Written in **plan mode**, before any edit. Overwritten at the start of each phase (the previous
phase's plan is in git history).

**Every OPEN QUESTION must be answered in this file before leaving plan mode.** A guessed answer is
a design decision you didn't make. Then a fresh session runs
[plan review](../prompts/cross-cutting/plan-review.md) against it.

---

## Approach

One pipeline, assembled in `app/`, run by `hops scan`:

```
pack (surface.yaml) ─► SurfaceSpec ──┐
                                     ▼
closure.build(root) ─► SourceClosure ─► observe.text.scan(closure, surface) ─┐
                                     └► observe.deps.scan(closure, surface) ─┤
                                                                             ▼
                                              observe.resolver ─► observe.ledger.build ─► Ledger
                                                                             │
                                                        store/sqlite + artifacts ─► `hops exposure`
```

Generic layers receive `SurfaceSpec` as a parameter. `app/registry.py` is the only importer of
`packs/`, and it discovers packs by directory listing — no provider name in `app/` either.

| DoD | How it is met |
|---|---|
| 1 — schemas | `core/schemas/{evidence,candidate,obligation,proof_scope,receipt}.json`, draft 2020-12. `core/schema.py` compiles and caches validators; `core/records.py` validates on every construction *and* `store/sqlite.py` re-validates on every write. IDs = SHA-256 hex of canonical JSON (`core/canonical.py`: sorted keys, `(",",":")` separators, UTF-8). |
| 2 — SurfaceSpec + pack | `packs/_protocol.py`: frozen `SurfaceSpec` dataclass (identifiers, hosts, package_names, version_carriers, request_languages, sink_argument_positions, config_env_keys) with `from_mapping`/`to_mapping`, plus the `ProviderPack` Protocol stub. `packs/google_ads/surface.yaml`, `packs/_mock/surface.yaml`. `app/registry.py:load_pack(name)`. |
| 3 — closure | `closure/source_closure.py` walks the root, classifies every path `INSIDE / GENERATED / VENDORED / SUBMODULE / EXTERNAL_BOUNDARY / UNSCANNED` by a fixed, ordered rule list. Unreadable / undecodable / oversize / unclassifiable → `UNSCANNED` with a reason → `FILE_UNSCANNED` candidate. |
| 4 — text observer | `observe/text.py` wraps `rg --json`, one invocation per surface pattern so every match is attributable. Zero provider strings. Evidence: `observer="text"`, `confidence="RAW"`, exact path / line / `source_hash`. |
| 5 — deps observer | `observe/deps.py`: per-ecosystem parsers (python, js, php, java, dotnet, go, ruby) over manifests and locks found in the closure. Names normalised, matched against `surface.package_names`. Unparsable manifest / unpinned spec with no lock / no manifest at all → `DEPENDENCY_STATE_UNKNOWN`. Never "absent" without a parsed lock. |
| 6 — ledger | `observe/ledger.py`: every Evidence attaches to exactly one Candidate (keyed by identity tuple, so attaching never loses provenance). Status comes from the resolver; `store` refuses to persist a candidate whose status is absent (schema `required`). `unexplained` is computed, and a non-zero value raises. |
| 7 — resolver | `observe/resolver.py`: `CLAIM_PRECEDENCE` and `CLAIM_STATUS_RULES` keyed by `claim_type`. No module-level global ranking of evidence kinds exists anywhere. |
| 8 — store | `store/sqlite.py`: WAL, `foreign_keys=ON`, tables `runs / evidence / candidates / obligations / checks / artifacts`; every row carries `run_id` + `proof_scope_hash`. `store/artifacts.py` writes tmp → `fsync` → `os.replace`. |
| 9 — CLI | `app/cli.py` (argparse, plain text, no ANSI): `hops scan <repo> --pack <name>`, `hops exposure`. §4 layout, counts including `Unexplained`, a `close with:` line under every UNKNOWN. |
| 10 — laws as tests | `tests/unit/test_imports.py` (AST-level import check), `tests/unit/test_no_provider_leak.py` + `tests/unit/provider_names.txt`. |
| 11 — property tests | `tests/property/`: unexplained-free ledger (hypothesis), byte-identical reruns, `rg` absent → `TOOLING_MISSING`, two surfaces over one closure. |
| 12 — fixtures | `tests/fixtures/phase1/` × 6 + `expected_candidates.json` each, compared on stable fields. |

Also, per [docs/HOOKS.md](../docs/HOOKS.md): activate `.claude/settings.json` as the first act of
this phase, now that `tests/`, `core/schemas/` and the generic trees exist.

## Files touched

| Path | New / changed | Why |
|---|---|---|
| `pyproject.toml`, `.python-version`, `.gitattributes` | new | uv project, Python 3.12 pin, `hops` console script, LF normalisation so fixture hashes are platform-stable |
| `.claude/settings.json` | new | mechanical enforcement of the Laws (HOOKS.md) |
| `hubbleops/core/canonical.py` `schema.py` `records.py` `ids.py` `errors.py` | new | canonical JSON, validator cache, record constructors, fail-closed error types |
| `hubbleops/core/schemas/*.json` | new | DoD 1 — the five frozen schemas |
| `hubbleops/core/proof_scope.py` | new | ProofScope construction (tree hash, dep resolution hash, tool versions) |
| `hubbleops/closure/source_closure.py` | new | DoD 3 |
| `hubbleops/observe/{text,deps,ledger,resolver}.py` | new | DoD 4–7 |
| `hubbleops/store/{sqlite,artifacts}.py` | new | DoD 8 |
| `hubbleops/app/{registry,cli,exposure}.py` | new | DoD 2, 9 |
| `hubbleops/packs/_protocol.py`, `packs/{google_ads,_mock}/surface.yaml` | new | DoD 2 |
| `tests/unit/*`, `tests/property/*`, `tests/fixtures/phase1/*` | new | DoD 10–12 |
| `dev/context.md`, `dev/tasks.md`, `dev/proposals.md` | changed | session handoff; PyYAML recorded as an approved addition |

## Law check

| Law | How this plan respects it |
|---|---|
| UNEXPLAINED_CANDIDATES = 0 | `ledger.build` attaches every Evidence to a Candidate and asks the resolver for a status for each; it recomputes `unexplained` and raises `UnexplainedCandidates` before anything is persisted. Status is `required` in `candidate.json`, so an unstatused candidate cannot be written even by a future caller. |
| UNKNOWN ≠ UNEXPLAINED | UNKNOWN is a first-class outcome of the Phase-1 status rules (dynamic version keys, unpinned deps, unparsable files, provider surface without a resolvable version). Every UNKNOWN carries a non-empty `close_with`; the gate counts `unexplained`, never `unknown`. |
| UNKNOWN conservation | Phase 1 creates the first ledger, so there is no prior UNKNOWN set to conserve. The property test asserts a rerun over an unchanged closure reproduces the identical UNKNOWN set (the base case of L3). Closing machinery (`hops decide`) is Phase 7. |
| Proof bound to ProofScope; new SHA → new proof | Every row carries `proof_scope_hash`; `run_id` is derived from it, so a changed tree yields a different run and different rows rather than mutating old ones. Phase 1 never writes the word "verified" — `hops scan` reports discovery only. |
| Dependency direction | `app/` is the only importer of `packs/`; `closure/ observe/ core/ store/` take `SurfaceSpec` as a parameter. Enforced by `test_imports.py` (AST) and `test_no_provider_leak.py` (grep), plus a PreToolUse hook at write time. |
| Fail closed | `rg` missing → `ToolingMissing`; file unreadable/undecodable → `FILE_UNSCANNED`; manifest unparsable or unpinned → `DEPENDENCY_STATE_UNKNOWN`; subprocess timeout → `ToolingTimeout`. No bare `except`, no `except: pass`, no default that turns a failure into "nothing found". |
| AI evidence | Phase 1 makes no AI calls. `derivation` accepts `DERIVED_AI_EVIDENCE` in the schema, but no Phase-1 code path emits it and the resolver's status rules ignore that derivation. |
| Memory reduces work, never proof | Phase 1 ships no cache. The store is a record of runs, not an input to them; a rerun recomputes everything. |

## Evidence plan

```
uv run pytest -q
uv run pytest tests/unit/test_no_provider_leak.py -v
uv run ruff check . && uv run ruff format --check .
for f in tests/fixtures/phase1/*/ ; do uv run hops scan "$f" --pack google_ads ; done
uv run hops exposure
uv run hops scan <fixture> --pack google_ads --export a.json
uv run hops scan <fixture> --pack google_ads --export b.json
sha256sum a.json b.json          # must be identical
```

## OPEN QUESTIONS

*(numbered. Design-changing questions get answered here — in writing — before implementation
starts. Delete none; answer them inline.)*

1. **Physical layout: `hubbleops/` package directory, or top-level `app/ core/ …`?**
   **ANSWERED (human, Phase 1): `hubbleops/` package directory.** ARCHITECTURE.md §10 indents
   `app/ core/ closure/ …` under `hubbleops/` while `packages/ tests/ docs/ dev/` sit at column 0,
   and the import name `hubbleops` plus the sentinel law ("never imports `hubbleops.*`") only hold
   if the generic layers are importable as `hubbleops.*`. CLAUDE.md's repo-map line is shorthand for
   the module tree. Schemas therefore live at `hubbleops/core/schemas/*.json`.

2. **YAML: add PyYAML, or write a stdlib-only subset loader?**
   **ANSWERED (human, Phase 1): add PyYAML.** It crosses the stated approval boundary, so it is
   recorded in `dev/proposals.md`. The architecture mandates YAML in three frozen places
   (`packs/*/surface.yaml`, ast-grep rule files in Phase 3, `.hubbleops/surface.yml` in Phase 7); a
   hand-rolled subset parser would have to grow to meet all three. Loaded with `yaml.safe_load`.

3. **Which `observer` values may Evidence carry — is the §3.2 list closed?**
   **ANSWERED: closed.** `evidence.json` restricts `observer` to the six §3.2 names
   (`text deps structure dynamic telemetry sentinel`). `closure/` is not an observer and gets no
   enum value. Consequence: the closure classifies and records the reason, and `observe/text.py` —
   the component that actually reads file bytes in Phase 1 — emits the `file_unscanned` evidence for
   both closure-marked `UNSCANNED` entries and files it fails to read itself. Every candidate is
   therefore evidence-backed and has a path/line for the Exposure Map, and the frozen enum is
   untouched.

4. **What content is a Candidate's ID the SHA-256 of?**
   **ANSWERED: the identity tuple, not the whole record.**
   `candidate.id = sha256(canonical({provider, claim_type, path, line_start, line_end,
   provider_subject}))`. Evidence IDs hash the full record minus `id`. If a candidate's ID covered
   `evidence_ids`, attaching a second observer's evidence would change the ID — the candidate would
   "disappear" and a new one appear, breaking L1's *a candidate never disappears*. Deduplication is
   therefore attachment to a stable key, and `evidence_ids` is an append-only sorted set (trap 3).

5. **Is `run_id` random or derived? Determinism (DoD 11b) says the ledger export must be byte-identical across runs.**
   **ANSWERED: derived.** `run_id = sha256(canonical({proof_scope_hash, pack, verb, target}))`.
   Wall-clock timestamps exist only in the `runs` row, never in the ledger export or in any hashed
   record, so two consecutive scans of an unchanged tree produce identical `run_id`, identical rows
   (idempotent upsert) and a byte-identical export. This also makes `hops replay <run_id>` meaningful
   (§12) and kills trap 4.

6. **What is `Evidence.source_hash` the hash of — the file, or the matched line?**
   **ANSWERED: the file blob** (`sha256` of the file's bytes). §14 keys the fact cache and binding
   store on blob hashes, so Phase 8's invalidation needs exactly this value; a line hash would not
   compose. Path + `line_start`/`line_end` already give the exact location.

7. **Which ProofScope fields can Phase 1 fill, and what happens to the rest?**
   **ANSWERED: fill four, `null` the rest, no sentinels.** Filled: `tree_hash` (always computed from
   sorted `(path, blob_sha)` pairs, so a non-git directory is still bound), `repo_sha` (git HEAD, or
   `null` when the target is not a git repo — never silently substituted), `dependency_resolution_hash`
   (from the deps observer's resolved set), `scanner_version` (`hubbleops` version + `rg` version, so
   Axiom 1's tool-version binding holds). `null`: `build_command`, `build_config_hash`,
   `provider_contract_hash`, `rules_hash` (Phase 2/3), `verifier_version`, `verifier_image_hash`
   (Phase 5). The schema types these as nullable and `required`, so a later phase fills them without
   a schema change — and a `null` reads as "not yet bound", not as "bound to nothing".

8. **Is `claim_type` a closed enum?**
   **ANSWERED: no — pattern-constrained (`^[a-z][a-z0-9_]*$`).** §5 names four claim types in the
   resolution tables but never declares the set closed, and Phase 1 already needs `file_unscanned`
   and `dependency_state`. Freezing an enum that is knowably incomplete would force a frozen-schema
   change in Phase 3. The resolver, by contrast, *is* exhaustive: an unknown `claim_type` raises
   rather than defaulting to a status.

9. **Which statuses can Phase 1 legitimately assign, with no Change Pack and therefore no target version?**
   **ANSWERED: four of the five, on these rules.**
   - `AFFECTED` — a resolved provider version literal at a version carrier (`version="v22"`, REST
     `/v22/`, generated namespace `…v22`), or a surface package resolved to a concrete version.
   - `UNKNOWN` — provider surface present but the version is not statically resolvable (env/config
     key, unpinned dependency with no lock, request-language anchor with no version), plus every
     `FILE_UNSCANNED`. Each carries a `close_with`.
   - `NOT_AFFECTED_WITH_EVIDENCE` — only where a **lock** file for an ecosystem parsed cleanly and
     contains no surface package. A manifest alone is not enough (trap 1).
   - `EXCLUDED_WITH_EVIDENCE` — hits inside `VENDORED / GENERATED / SUBMODULE / EXTERNAL_BOUNDARY`
     regions, with the closure classification as the evidence. The vendored SDK's *version* is still
     claimed by the deps observer off its own manifest, so excluding the copy never hides the fact
     that it pins an executing version.
   - `HUMAN_REQUIRED` is not assignable in Phase 1 — it needs a recorded human decision loop
     (`hops decide`, Phase 7). The schema permits it; no Phase-1 rule emits it.

10. **Where does run state live, given that scanning must not pollute the scanned repository?**
    **ANSWERED: `--state-dir`, default `<cwd>/.hubbleops`.** The SQLite DB and artifacts go to
    `.hubbleops/hubbleops.sqlite` and `.hubbleops/artifacts/<run_id>/`, both already ignored by
    `.gitignore` (`*.sqlite`, `artifacts/`) while `.hubbleops/` itself stays committable for Phase 7.
    Tests pass a tmp dir. Scanning `tests/fixtures/**` therefore writes nothing into the fixtures.

11. **Where does the PreToolUse hook read "the current phase" from?** (HOOKS.md leaves this to Phase 1.)
    **ANSWERED: from the git branch name.** `phase-NN-<slug>` is already a Law in CLAUDE.md, so the
    branch is the single source of truth and no new state file is needed. Unparseable branch (e.g.
    `main`) → treat as the most restrictive phase, so `core/schemas/` edits are rejected outside a
    Phase-1 branch. The repair restriction keys off `HUBBLEOPS_ROLE=repair`, which the Phase-6
    repair runner will set; until then it is simply never on.

12. **Does `observe/text.py` scan VENDORED and GENERATED files at all?**
    **ANSWERED: yes — scan everything, exclude at status time.** §6.2 is recall-first, and a vendored
    SDK copy is exactly where a pinned version hides. Suppressing the scan would make the closure
    classification a silent filter; instead the evidence is recorded and the ledger marks it
    `EXCLUDED_WITH_EVIDENCE` naming the classification. Only `UNSCANNED` entries are not scanned —
    and those become `FILE_UNSCANNED` candidates, so absence is still never silence.
