# Context

Rolling state of the build. **Updated before every session ends** (a Law in `CLAUDE.md`).
Read by every phase prompt. Keep it short — this is what the next session wakes up knowing.

## Where we are

| | |
|---|---|
| **Current phase** | 1 — source closure, text/dependency observers, ledger, exposure map |
| **Branch** | `phase-01-source-closure-and-ledger` (not merged; nothing lands on `main` until the gate audit says `GATE: PASS`) |
| **Last gate passed** | none — the fifth Phase 1 [gate audit](../prompts/cross-cutting/gate-audit.md) confirmed F-3, M-2 and M-4 fixed and blocked on F-4, a hole in the M-3 fix itself. F-4, M-5, M-6 and m-1 are now fixed and the re-run is pending |
| **Next action** | re-run the gate audit; on `GATE: PASS`, merge to `main` and tag `v0.1`, then start Phase 2 |

Phase 1 is implemented and green: `hops scan <repo> --pack <name>` and `hops exposure` produce a
deterministic ledger and Exposure Map with `UNEXPLAINED_CANDIDATES = 0` on all six fixtures.
165 tests pass, 1 skips; `ruff check`, `ruff format --check` and `pyright` (strict) are clean.
`pyright` now covers `tests/` and `.claude/` as well as `hubbleops/`, excluding `tests/fixtures/`,
which is deliberately-broken third-party sample code and an input to the scanner rather than source.

The second gate audit found two ways the ledger could lose a candidate. The text observer skipped
`file_unscanned` evidence for any path whose suffix looked like media, so a `.png` carrying provider
strings produced no candidate at all — absence as silence, which §6.1 forbids. And the store's L3
guard compared only evidence-id *sets*, so a 64-hex string referencing no stored row counted as new
evidence and closed an UNKNOWN. Both are now enforced and covered by tests.

The third gate audit confirmed every earlier finding fixed and found two more, both blocking:

- **F-1 — a call site vanishes with no candidate.** `closure/source_closure.py` decides "binary"
  from the first `SNIFF_BYTES = 8192` only, while `rg` scans the whole file and silently quarantines
  anything with a NUL anywhere. `observe/text.py` passes no `--text`/`--binary` and drops the `end`
  event carrying `binary_offset`, so the two disagree in silence. Reproduced: a 14756-byte file with
  a NUL at 14700 and a `v22` call at line 702 is classified INSIDE, searched by `rg` for
  `bytes_searched: 0`, and yields no AFFECTED, no UNKNOWN and no `FILE_UNSCANNED`. Same class as the
  second audit's F1 — that fix closed the closure-says-UNSCANNED side, this is the
  closure-says-scannable / rg-says-binary side.
- **F-2 — two different recall surfaces share one proof key.** `app/cli.py` builds the ProofScope
  from `repo_sha, tree_hash, dependency_resolution_hash, scanner_version` only; the SurfaceSpec, the
  one input that decides recall, never enters, and `run_id_for` takes the pack *name*, not its hash.
  `provider_contract_hash` and `rules_hash` are `required` in the frozen schema and are left `None`.
  Editing `surface.yaml` between two scans of the same tree yields the identical ProofScope and
  `run_id`: `ledger.json` is overwritten in place at 7 candidates while the DB and Exposure Map
  report the 12-candidate union and attribute all 12 to a surface that could only produce 7.
  `dev/context.md` recorded leaving these fields null as a decision; this consequence was not
  recorded, and it goes live the moment Phase 2 edits `surface.yaml`.

The fourth gate audit confirmed F-1 fixed across an eight-case NUL/encoding matrix and a 24-path
closure↔ripgrep differential, and confirmed F-2's write side fixed. It blocked on **F-3**, the read
half of the same finding: `hops exposure` re-derived the pack hash by loading `surface.yaml` at
render time, so every map over a stored run printed whichever surface was on disk rather than the
one the run recorded — a false provenance claim, printed silently beside the correct ProofScope. The
read path now uses the run's stored `provider_contract_hash` and never consults the pack.

Four MAJOR findings beside it are also fixed. **M-1**: binding the surface loaded a frozen field
with a meaning §7.1 gives the Change Pack lattice — raised and decided as P-007 (compose, never
replace). **M-3**: `scanner_version` was the package version, which nobody bumps per commit, so the
F-1 fix changed what the scanner reported for a tree while its ProofScope hash and run id stood
still and the store merged two disagreeing builds under one key; it now carries a fingerprint of the
sources that decide recall. **M-2**: the L3 guard read only committed rows, so a single
`write_candidates` call carrying an UNKNOWN and its closure for one id closed it with no new
evidence; the guard now sees earlier records in its own batch. **M-4**: L10 was documented in the
evidence schema and enforced nowhere, so `DERIVED_AI_EVIDENCE` alone could close an UNKNOWN; a
closure whose new evidence is entirely AI-derived is now refused.

The fifth gate audit confirmed F-3, M-2 and M-4 fixed and blocked on **F-4**, a hole in the M-3 fix
itself: `OBSERVATION_SOURCES` left out the module that composes the observer list, so deleting an
observer from the scan left the ProofScope and `run_id` byte-identical while the ledger lost a
candidate — F-2 reopened one layer up. The fingerprint also keyed its dict by basename, so two
sources sharing a filename collided and one silently left the proof key (**m-1**). Both are fixed:
the composing module is in the set, and the key is the path relative to the sources' common root.

Two further MAJOR findings from that audit are fixed. **M-5**: the store upserted evidence on
conflict and never re-derived the id, so a row could be rewritten under its own id with a different
derivation — relabelling `DERIVED_AI_EVIDENCE` as `OBSERVED` and walking straight past the L10 guard
added for M-4. An evidence id is now verified to be the hash of its own content. **M-6**: P-007 was
recorded in proposals and ARCHITECTURE.md but the two artefacts a Phase 2 session actually reads —
`core/schemas/proof_scope.json` and the Phase 2 prompt — still described `provider_contract_hash` as
the Change Pack hash alone, the reading P-007 rejected; both now carry the composition rule.

**M-7 is still open and deliberately deferred**: `write_evidence` commits before any candidate
exists and `latest_run` does not exclude runs with `finished_at IS NULL`, so there is a committed
persisted state carrying unexplained evidence, which violates the letter of L1. It fails loud rather
than silent — the map prints `Unexplained 1` — but it wants either an atomic evidence+candidate
write or an unfinished-run filter before Phase 2 leans on the store.

Five non-blocking findings also stand: the `Detected` line still lacks per-version site counts and
`UNKNOWN (n)` (P-005); `Pack google_ads@<hash>` prints the surface hash where §4/§16 specify
`<changes_hash>`, unlabelled; the extra `EXCLUDED (with evidence)` line exceeds P-004's "nothing else
in §4 changes"; the `ProviderPack` Protocol is missing `wire_signature` and `versions()` added to
frozen §3.1 by P-005/P-006; and `observe/text.py` silently `continue`s an `rg` hit on an
enumerated-but-unscannable path, discarding the matched content with no record of its own.

## Blocking

- Nothing blocks Phase 2. `ast-grep` is not installed yet — Phase 3 needs it, Phase 1 does not.
- `docker`/`podman` not installed — Phase 4 needs them.
- `rg` was not on this machine at the start of Phase 1; the official 14.1.1 binary is now at
  `~/.local/bin/rg.exe`. The scan refuses to run without it (`TOOLING_MISSING`), by design.
- `pyright` is a dev dependency and pinned in `uv.lock` (1.1.411). It used to run only from a
  machine-global install, so "pyright is clean" was not a reproducible claim.

## Decisions that carry forward

*(record here anything a later phase must not re-litigate — with the phase it was decided in)*

**Frozen-surface decisions (Phase 1).**
- **P-003 ACCEPTED.** `SurfaceSpec` is defined in `core/surface.py`, not `packs/_protocol.py` as
  Phase 1 DoD 2 words it. Law L5 outranks a checklist sentence: a generic layer that cannot import
  a pack cannot be shaped around one provider, and that is the property the proof rests on.
  `packs/_protocol.py` re-exports it in `__all__`, so a pack author still writes
  `from hubbleops.packs._protocol import SurfaceSpec`. Do not re-litigate this in a later phase.
- **P-004 ACCEPTED.** The §4 DISCOVERY block gains `Excluded (evidence)` and `Human required`.
  The frozen layout lists only three of the five candidate statuses, so a repository with
  candidates in the other two renders counts that do not sum to `Candidates found` — a candidate
  disappearing from the one artefact the customer reads. Everything else in §4 renders exactly as
  frozen, glyphs included.

**Pre-Phase-2 design decisions (2026-09-03) — read before starting Phase 2.**
- **P-005 ACCEPTED.** The version model is a lattice, not a pair. v22→v25 was the first commercial
  target, not the design: real repos run several versions at once. Obligations key off
  `(candidate, candidate's own effective version, target)`, never one repo-wide "current version".
  The Change Pack is a catalog per supported version plus a *computed*, cached, hash-addressed diff
  between any two — composed across consecutive versions for a multi-hop gap, `UNKNOWN_PROVIDER_
  CONTRACT` where a mapping does not compose — not one hand-built `changes_v22_v25.jsonl`. Default
  `--target` is `latest` supported by the resolved SDK line. `docs/ARCHITECTURE.md` §3.1, §4, §7.1,
  §7.2 and Phase 2/6/10 prompts are updated to match. `obligation.json` will need an
  effective-version field before Phase 6 writes a real obligation — not added yet; that schema
  change needs its own proposal when Phase 6 starts.
- **P-006 ACCEPTED.** No universal semantic analyzer exists (stack graphs, Sourcegraph, Semgrep,
  Kythe all converge on shared-engine + thin per-language rules + a non-structural fallback), so
  "language-agnostic" and "reliable" are only compatible if completeness comes from channels that
  read no source at all. `ProviderPack` gains `wire_signature` — regexes over a request's path and
  headers into `(service, method, version)`, zero per-language work. The sentinel and dynamic
  capture gain **proxy mode** (egress proxy or the client library's own request logging, parsed by
  `wire_signature`) as the *default* alongside per-language hook mode. The structure observer emits
  `STRUCTURE_UNSUPPORTED` for every `INSIDE` file in a language with no rule set, rather than
  silence — a coverage gap changes how much lands in `UNKNOWN`, never whether a usage is missed.
  Initial rule languages stay Python/PHP/JS/TS (Google Ads' actual client-library order); Java/C#
  later; nothing else until a customer needs it. `docs/ARCHITECTURE.md` §2, §3.1, §6.4, §6.5, §6.6,
  §15 and Phase 2/3/4/10 prompts are updated to match. The Observer contract (§3.2) is unchanged —
  proxy mode is `observer="sentinel"` evidence, not a seventh observer name.

**Layout and naming (Phase 1).**
- The Python package is `hubbleops/` at the repo root, containing `app/ core/ closure/ observe/
  store/ packs/`; `tests/ docs/ dev/ packages/` are its siblings. This is `ARCHITECTURE.md` §10's
  indentation read literally, and it is what makes the import name `hubbleops` and the sentinel law
  ("never imports `hubbleops.*`") true. `CLAUDE.md`'s repo-map line is shorthand for the module tree.
  Schemas therefore live at `hubbleops/core/schemas/*.json`.
- `SurfaceSpec` is **defined in `core/surface.py`** and re-exported by `packs/_protocol.py`.
  DoD 2 asks for it in `_protocol.py`, but law L5 forbids `observe/` from importing `packs/`, and
  `observe/text.py` must name the type in its signature. The law wins; `_protocol.py` stays the
  pack-facing name and holds the `ProviderPack` Protocol. Recorded as **P-003** in
  `dev/proposals.md`, still awaiting the owner's decision line.
- An observer's signature is `scan(closure, ctx)` and the surface reaches it on
  `ObserverContext.surface`. DoD 4 and 5 write it as `scan(closure, surface)`; frozen
  `ARCHITECTURE.md` §3.2 writes it as `scan(self, closure, ctx)` with "ObserverContext carries only
  pack-provided parts (surface, rules, hooks) and run metadata". The frozen interface wins — it is
  the document `CLAUDE.md` says overrides — and the DoD's requirement that the pack's surface be
  what an observer scans against is satisfied either way. Phase 3's structure observer and Phase 4's
  dynamic and telemetry observers take the same two arguments; rules and hooks join `surface` on the
  context rather than growing the parameter list.

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
- The text observer drops nothing. An earlier build skipped identifier and package patterns inside
  recognised manifests to stop the dependency observer and the recall layer producing two candidates
  at one line; the Phase 1 gate audit showed that this let a `package.json` naming the surface
  package in a script string be reported `NOT_AFFECTED_WITH_EVIDENCE` off the lock alone — a proof
  of absence over a tree ripgrep had already matched. Recall wins: every hit becomes evidence, and
  the two claims stay separate because they answer different questions. `_package_reference` in the
  resolver says which of the two a manifest location is, so its `close_with` points at the lock file
  rather than at a build script.
- `store/sqlite.py` is where the laws are mechanically enforced on the way to disk, not only in the
  builder that happens to call it today: an open candidate (`UNKNOWN`, `HUMAN_REQUIRED`) closes only
  when the write attaches an evidence id the stored row did not have (L3); no write may drop an
  attached evidence id (L1, and it is what stops L3 being bypassed by shrinking the set first); a
  record's `proof_scope_hash` must equal its run's (L4); `candidates.status` carries a SQL `CHECK`
  over the frozen enum so raw SQL cannot write a sixth status; and `PRAGMA user_version` is stamped,
  so a database written before these constraints existed is refused instead of silently trusted.
  Phase 7's `hops decide` closes an UNKNOWN by recording the decision as evidence and attaching it —
  that is the "recorded human decision" half of L3, and it needs no new closing mechanism.
  Two guards were added after the second gate audit: every `evidence_id` a candidate cites must
  already exist in the `evidence` table for that run (an id that references nothing is not
  provenance and cannot close an UNKNOWN), and `finish_run` refuses to mark a run complete while any
  persisted evidence row is attached to no candidate. L1 now holds at the store boundary, not only
  in `observe/ledger.py`.
- A file's suffix is never evidence about its content. The closure still tells `binary_media` and
  `binary_opaque` apart, because the reason a customer reads should say which it is, but both emit
  `file_unscanned` evidence and both resolve to `UNKNOWN` with a closing instruction. The media
  reason no longer claims the file "cannot carry an executable provider call" — that is the very
  thing the scanner could not check.
- `observe/resolver.py` fails closed when a location-bound claim cites a path the closure never
  classified (`PathNotInClosure`). It used to default the classification to `INSIDE`, which is a
  silent assumption on a safety question: first-party and therefore repairable.
- A tool timeout exits `5` (`EXIT_UNKNOWN`) and prints `UNKNOWN: <reason>`. It used to exit `4`
  alongside real failures, which contradicted L9's "timeout → UNKNOWN with reason".

## Open threads

- `docs/FAILURE_ATLAS.md` is still empty. It fills from the real-repo loop, which starts after the
  Phase 2 gate.
- The Exposure Map prints `Target` and the production-services line as "not in this ProofScope"
  because Phase 1 has no Change Pack and no telemetry observer. Phase 2 and Phase 4 fill them.
- The `Detected` line lists every version literal found (it already refuses to collapse a
  multi-version repo into one label) but not yet per-version site counts or an explicit
  `UNKNOWN (n)` bucket, unlike the amended §4 example (P-005/P-006). `ledger.location_of(candidate)`
  already resolves each candidate's winning version, so the data exists; `_detected_versions()` in
  `app/exposure.py` just doesn't group by it yet. Same status as `Target` — aspirational until a
  phase that touches `exposure.py` for another reason picks it up. Checked against a live scan
  (2026-09-03): current output is `v22, sdk 17.1.0`, not wrong, just uncounted.
