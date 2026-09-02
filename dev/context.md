# Context

Rolling state of the build. **Updated before every session ends** (a Law in `CLAUDE.md`).
Read by every phase prompt. Keep it short — this is what the next session wakes up knowing.

## Where we are

| | |
|---|---|
| **Current phase** | 1 — source closure, text/dependency observers, ledger, exposure map |
| **Branch** | `phase-01-source-closure-and-ledger` (not merged; nothing lands on `main` until the gate audit says `GATE: PASS`) |
| **Last gate passed** | none |
| **Next action** | fresh-session [gate audit](../prompts/cross-cutting/gate-audit.md); on `GATE: PASS`, merge to `main` and tag `v0.1`, then start Phase 2 |

Phase 1 is implemented and green: `hops scan <repo> --pack <name>` and `hops exposure` produce a
deterministic ledger and Exposure Map with `UNEXPLAINED_CANDIDATES = 0` on all six fixtures.
146 tests pass; `ruff check` and `pyright` (strict) are clean.

## Blocking

- Nothing blocks Phase 2. `ast-grep` is not installed yet — Phase 3 needs it, Phase 1 does not.
- `docker`/`podman` not installed — Phase 4 needs them.
- `rg` was not on this machine at the start of Phase 1; the official 14.1.1 binary is now at
  `~/.local/bin/rg.exe`. The scan refuses to run without it (`TOOLING_MISSING`), by design.

## Decisions that carry forward

*(record here anything a later phase must not re-litigate — with the phase it was decided in)*

**Layout and naming (Phase 1).**
- The Python package is `hubbleops/` at the repo root, containing `app/ core/ closure/ observe/
  store/ packs/`; `tests/ docs/ dev/ packages/` are its siblings. This is `ARCHITECTURE.md` §10's
  indentation read literally, and it is what makes the import name `hubbleops` and the sentinel law
  ("never imports `hubbleops.*`") true. `CLAUDE.md`'s repo-map line is shorthand for the module tree.
  Schemas therefore live at `hubbleops/core/schemas/*.json`.
- `SurfaceSpec` is **defined in `core/surface.py`** and re-exported by `packs/_protocol.py`.
  DoD 2 asks for it in `_protocol.py`, but law L5 forbids `observe/` from importing `packs/`, and
  `observe/text.py` must name the type in its signature. The law wins; `_protocol.py` stays the
  pack-facing name and holds the `ProviderPack` Protocol.

**Data model (Phase 1, all inside the frozen schemas).**
- `Evidence.observer` is the closed six-value §3.2 set. `closure/` is not an observer: it classifies
  and records a reason, and `observe/text.py` — the component that reads file bytes — emits the
  `file_unscanned` evidence. Every candidate is therefore evidence-backed and has a location.
- `Candidate.id = sha256({provider, claim_type, claim_key})` — the identity tuple, **not** the whole
  record. If the ID covered `evidence_ids`, attaching a second observer's evidence would change it
  and the candidate would "disappear", breaking L1.
- `Evidence.id = sha256(record minus id)`. `Evidence.source_hash` is the **file blob hash**, which is
  what §14's fact cache and binding store will be keyed on in Phase 8.
- `run_id = sha256({proof_scope_hash, provider, verb, target})` — derived, never random. Wall-clock
  timestamps live only in the `runs` row, never in a hashed record or in the ledger export, so two
  scans of an unchanged tree are byte-identical and `hops replay <run_id>` is meaningful.
- `claim_type` is pattern-constrained (`^[a-z][a-z0-9_]*$`), not an enum: §5 names four claim types
  but never closes the set, and Phase 1 already needs `file_unscanned` and `dependency_state`. The
  **resolver** is exhaustive instead — an unregistered `claim_type` raises `UnknownClaimType`.
- ProofScope fields a phase has not bound are `null`, never a sentinel. Phase 1 binds `tree_hash`
  (always, git or not), `repo_sha` (git HEAD, or null for a non-git tree — never substituted),
  `dependency_resolution_hash`, and `scanner_version` (hubbleops + rg version).

**Phase-1 status rules (Phase 1) — these change as observers land, the laws do not.**
- `AFFECTED`: a resolved version literal at a version carrier, or a surface package resolved to a
  concrete version.
- `UNKNOWN`: provider surface present but the version is not statically resolvable — a runtime
  config key, an unpinned dependency with no lock, a request-language anchor, a recall-layer
  identifier hit, an unreadable file, an unparsable manifest. Each carries a `close_with`. Phase-1
  UNKNOWN counts are *expected* to be high: only two of five observer channels exist. Phase 3
  (structure) and Phase 4 (dynamic/telemetry) are what close them.
- `NOT_AFFECTED_WITH_EVIDENCE`: **only** where a lock file parsed cleanly and contains no surface
  package. A manifest alone never proves absence (trap 1).
- `EXCLUDED_WITH_EVIDENCE`: location-bound hits inside `VENDORED / GENERATED / SUBMODULE /
  EXTERNAL_BOUNDARY`. The exclusion is *not* applied to `sdk_installed`, so a vendored SDK's version
  is still claimed off its own manifest — excluding the copy never hides what it pins.
- `HUMAN_REQUIRED` is representable but no Phase-1 rule emits it; it needs `hops decide` (Phase 7).

**Mechanics (Phase 1).**
- The PreToolUse hook reads the current phase from the **git branch name** (`phase-NN-<slug>`, already
  a Law), so no new state file exists. An unparseable branch is treated as the most restrictive
  phase. The repair restriction keys off `HUBBLEOPS_ROLE=repair`, which the Phase-6 runner will set.
- Run state lives in `--state-dir`, default `<cwd>/.hubbleops` (`hubbleops.sqlite` + `artifacts/`,
  both already git-ignored). Scanning a repository never writes into it.
- `observe/text.py` runs **one** ripgrep invocation with every surface pattern as a separate `-e`,
  then attributes each matching line to patterns in Python. Named groups are stripped for ripgrep
  (it rejects duplicate group names across alternated patterns) and re-applied in Python to extract
  the version. A line ripgrep reports that no pattern claims raises `ToolingFailed` rather than being
  dropped. This is ~40× faster than one invocation per pattern and was verified to produce
  byte-identical ledgers across all 12 pack × fixture combinations.
- The text observer skips identifier and package patterns inside recognised manifests: the
  dependency observer owns those files, and overlapping recall hits produced contradictory
  candidates at the same line.

## Open threads

- `docs/FAILURE_ATLAS.md` is still empty. It fills from the real-repo loop, which starts after the
  Phase 2 gate.
- The Exposure Map prints `Target` and the production-services line as "not in this ProofScope"
  because Phase 1 has no Change Pack and no telemetry observer. Phase 2 and Phase 4 fill them.
