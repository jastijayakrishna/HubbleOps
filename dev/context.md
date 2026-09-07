# Context

Rolling state of the build. **Updated before every session ends** (a Law in `CLAUDE.md`).
Read by every phase prompt. Keep it short — this is what the next session wakes up knowing.

## Where we are

| | |
|---|---|
| **Current phase** | 4 implemented and green — the Phase 4 gate audit and real-repo loop have **not** been run |
| **Branch** | `phase-04-dynamic-capture-and-sentinel`, cut from `main` at `v0.3` |
| **Last gate passed** | Phase 3. The post-real-repo-loop fresh audit returned literal `GATE: PASS` |
| **Next action** | Run the fresh-session [gate audit](../prompts/cross-cutting/gate-audit.md) on this tree, then the [real-repo loop](../prompts/cross-cutting/real-repo-loop.md) including `hops capture`, then a final fresh gate. Only then merge and tag `v0.4`. P-009 must be decided before AI triage receives any operational application, CLI, scan, or store route. No push authorized. |

Phase 4 progress (2026-09-07): the sandbox (runner, image, limits, network, mounts, capture worktree,
proxy, verifier image), the versioned dynamic event schema with generic Python/PHP/Node loaders,
`hops capture` in proxy and hook modes, telemetry reconciliation, `hops promote`, and the standalone
`hubbleops-sentinel` package are implemented. Ubuntu WSL runs Podman 4.9.3 rootless.

Five real defects were found and fixed while completing the phase, each one a case where the system
would have reported a confident absence instead of a named uncertainty:

- **Node capture was impossible.** `ulimit -v` was carrying the memory bound, and V8 cannot start
  under a 512 MiB address-space limit, so hook capture of any JavaScript suite died before the
  first test while Python and PHP passed. `memory_bytes` is now `RLIMIT_DATA` (`ulimit -d`) and
  `address_space_bytes` is a separate, larger `RLIMIT_AS`; both are proved against all three capture
  runtimes in `tests/integration/test_sandbox_runtime.py`. Recorded as FA-011.
- **Hook installation was unattested.** PHP does not apply `auto_prepend_file` to `php -r`, so the
  hook never loaded and capture reported `UNKNOWN_DYNAMIC: test execution emitted no events` — a
  claim about the customer's tests when the truth was that the sensor never ran. Every pack hook now
  writes a per-run nonce to `/hops/output/install.jsonl`, and capture emits `HOOK_NOT_INSTALLED`
  instead. Recorded as FA-012.
- **A failed TLS interception looked like no usage.** When the workload cannot trust the capture CA,
  the proxy logs a handshake failure and observes nothing; capture called that "no events". Failed
  handshakes are now counted from the proxy's own log and reported as `PROXY_INTERCEPTION_FAILED`,
  which suppresses the `UNKNOWN_DYNAMIC` claim. Recorded as FA-013.
- **Telemetry reconciled against a static-only ledger.** A production tuple that the same run had
  just observed dynamically was still reported `TELEMETRY_UNEXPLAINED`, manufacturing an UNKNOWN the
  run held the evidence to close and understating `Production services accounted for N/M`.
  Reconciliation and sentinel candidate mapping now use the static-plus-dynamic ledger.
- **The sentinel wheel could not build.** A redundant `force-include` duplicated the data directory,
  so `uv build` failed outright and DoD 5's "separate pip package" was unbuildable.

Four smaller fixes: `json.loads` over untrusted bytes raised an uncaught `UnicodeDecodeError` in five
places (one non-UTF-8 byte aborted a whole capture instead of becoming a named issue); the proxy's
readiness was inferred from its certificate rather than its listener, and its log was buffered so the
listening line was invisible; the PHP loader mount exposed all of `observe/dynamic/` to the workload
and the CA copy was written into a directory the proxy container had mounted read-write; and the
provider-leak scan did not read the `.ini`, `.cjs` or `.php` asset types Phase 4 introduced.

The Phase 4 real-repo loop ran `scan`, `exposure` and `capture` read-only at the pinned commits.
Static results are unchanged from Phase 3, so nothing Phase 4 added altered what a scan finds:
`mcp-google-ads` again produced 422 candidates, 9 AFFECTED, 413 preserved UNKNOWNs and 434 evidence
records; `google-ads-api` again produced 2,471 candidates, 3 AFFECTED, 2,066 evidence-backed
exclusions, 402 preserved UNKNOWNs and 2,488 evidence records. Both remain at zero unexplained.

`capture` is new and behaved as the plan requires. Neither repository has its dependencies
installed and none were installed, so both runs ended `CAPTURE_EXECUTION_FAILED` with the missing
imports preserved in the stderr artifact — never a reason to enable network. `google-ads-api`
additionally proved the egress policy against a real package manager: `npm test` reached for
`registry.npmjs.org`, the deny-all proxy observed and denied it (`DESTINATION_NOT_ALLOWLISTED`),
and because it is not provider traffic it became a named `UNKNOWN_WIRE_SIGNATURE` rather than a
dropped record. Zero unexplained candidates in both captures. No prospect repository was modified,
verified after every run.

The loop found one defect, FA-014: Git refuses to delete its own worktree metadata when that
directory carries the read-only attribute, so the first capture left state inside a repository we
promise not to touch, and the second failed closed rather than leaving it. `shutil.rmtree` with
`ignore_errors=True` was hiding the read-only case that `rm -rf` handles. Removal now retries,
clears the attribute, prunes, and still fails closed if the metadata does not return to its prior
state; the attestation records `recovered`. It fired on both pinned repositories, so it is
load-bearing. FA-015 records the package-manager egress pattern as a deliberate PRESERVED UNKNOWN:
dismissing non-provider hosts automatically was rejected, because an incomplete pack host list
would then hide real usage.

Phase 3 progress (2026-09-06): the provider-neutral structural observer, deterministic import/symbol
graph, five-hop wrapper walk, inheritance/decorator/factory/registry propagation, query skeletons,
cross-service boundaries, fail-closed per-file coverage, Google Ads Python/PHP/JavaScript/TypeScript
rules, `_mock` rules, and default-off AI triage boundary are implemented. Rule bytes and ast-grep
identity bind into the ProofScope. The final suite passed 342 tests with 1 environment skip; Ruff,
formatting, strict Pyright, import/provider-leak checks, both rule-test packs, and diff checks passed.
The fresh post-loop audit independently returned literal `GATE: PASS`.

The Phase 3 real-repo loop reran current HubbleOps read-only at the Phase 2 pinned commits.
`mcp-google-ads` produced 422 candidates, 9 AFFECTED, 413 preserved UNKNOWNs, 434 evidence records,
and zero unexplained candidates. `google-ads-api` produced 2,471 candidates, 3 AFFECTED, 2,066
evidence-backed exclusions, 402 preserved UNKNOWNs, 2,488 evidence records, and zero unexplained
candidates. Every preserved UNKNOWN has a precise closing instruction. A parent-relative TypeScript
import through a local alias was the only NEW_PATTERN; FA-010 and its anonymized fixture now prove
the live site as `v24` AFFECTED. No prospect repository was modified.

Phase 2 progress (2026-09-05): full protocols and both packs, v19-v25 offline sources/catalogs,
computed proto comparisons including the v18 baseline, multi-hop contract diffs, validation-only
transport, wire/telemetry adapters, composite ProofScope hash, and Exposure Map completion are built.
The verified source build has 15,617 / 15,737 / 16,217 / 16,507 / 17,224 / 17,573 / 18,243 facts
for v19 through v25 respectively. Current lattice hash:
`5b31c7c83c0ac4b1011603695c56d619cfd1a77c6e2cc3817ae819ea8cdf7d26`.
The final wheel inspection found 1,315 pack files, including all seven catalogs, the manifest, and
1,230 retained upstream payloads. An isolated install with socket APIs blocked verified the full
lattice. The fresh independent audit returned literal `GATE: PASS` with 269 passed and 1 environment
skip; after the real-repo fixtures landed, the final-byte suite passed 299 tests with 2 environment
skips. Ruff, formatting, strict Pyright, import boundaries, provider-leak checks, pack verification,
and diff checks are clean. The Windows unreadable-file test skips when the current process token can
bypass its deny ACL.

Phase 2 source decisions: v20-v25 metadata comes directly from Google's Query Builder schemas,
whose fields_index inventory is fully reconciled against every resource schema. v19 uses complete
version-checked Internet Archive captures of all 169 resources in the official overview; truncated
captures are rejected and alternate captures are tried. No partial v19 inventory was accepted.
Source disagreements remain UNKNOWN_PROVIDER_CONTRACT. Raw upstream bytes are shipped compressed,
alongside original and normalized hashes, inventories and source attribution. The current upgrade
guide plus its archived historical version and the current/archived release notes are parsed into
structured records. SDK compatibility minima/maxima are parsed from the official tables.
Acquisition scratch was moved, without deletion, under `.hubbleops/artifacts/phase2-acquisition/`.
The refresh command accepts version refs, dates and retrieval metadata through `--config`; adding a
major does not require a compiler rewrite. Non-adjacent diff caches also bind intermediate catalogs.
Provider `to replace` wording is resolved only when both identifiers map uniquely to the previous
and current proto subjects; the v19→v20 YouTube lineup replacement is attached to the computed diff.
Malformed telemetry CSV fails closed with explicit row issues.

The required first real-repo loop ran read-only at pinned commits. `mcp-google-ads`
(`461a1d673cf1c369e8b95fc9b5b1d9cb7f3ebb4a`) produced 387 candidates: 208 UNKNOWNs closed with
evidence, 1 preserved UNKNOWN, 107 NEW_PATTERN UNKNOWNs, and zero unexplained candidates.
`google-ads-api` (`e066efba47ebd94c0bda2d06e20af1f84a6e25ac`) produced 2,463 candidates: 9 AFFECTED,
9 UNKNOWNs closed with evidence, 379 preserved UNKNOWNs, 2,060 NEW_PATTERN UNKNOWNs, and zero
unexplained candidates. Ledger/evidence integrity checks found no duplicate ids, missing or orphaned
evidence, source/text mismatches, or artifact/export byte differences. Nine generalized patterns are
recorded in `docs/FAILURE_ATLAS.md`; six anonymized fixture families under `tests/fixtures/phase3`
carry them into Phase 3. No prospect repository was modified.

Phase 1 is implemented and green: `hops scan <repo> --pack <name>` and `hops exposure` produce a
deterministic ledger and Exposure Map with `UNEXPLAINED_CANDIDATES = 0` on all six fixtures.
196 tests pass, 1 skips; `ruff check`, `ruff format --check` and `pyright` (strict) are clean.
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

The sixth gate audit blocked on **F-5**, **F-6** and **F-7**. F-5 and F-7 are fixed. F-5 was the
F-2/F-4 class a third time, as an unguarded invariant: `surface_hash()` hashes `to_mapping()`, which
was hand-synced with the dataclass and covered by no test, so dropping one line from it let two
surfaces differing only in that field share a proof key with the whole suite green. There is now a
test that changes each declared field in turn and names the one that fails to reach the hash, and
`core/surface.py` is in the scanner fingerprint. F-7 was one unreadable file aborting the whole
scan: `rg` exits 2 on a per-file read error and the observer discarded the run, so a single locked
file anywhere in a repo produced no ledger at all — fatal for the Phase 2 real-repo loop. The
observer now reconciles with the closure, proceeding when every path `rg` names is already
classified UNSCANNED and still failing closed when `rg` names a path the closure thought it could
read.

The seventh audit blocked on **F-8**, the same class a fourth time and inside the mechanism meant to
close it: `OBSERVATION_SOURCES` was a hand-maintained tuple of seven modules while the scan leans on
twenty-nine, so `core/records.py` and `core/canonical.py` — which shapes every id — could change what
a scan found while the ProofScope and `run_id` stood still and the store merged two disagreeing
builds. Each earlier fix had added one more entry to the list; the list was the defect. The
fingerprint now covers every `.py` in the package, discovered rather than listed, and a test names
any module that escapes. This over-binds — editing a module the scan never reaches forces a re-proof
it did not need — which is the safe direction: missing a module costs a false proof, binding a
spare one costs a rerun. **Do not reintroduce a curated list here.**

**F-6 is open by judgement, not oversight.** The audit closed the M-5 attack that rewrote an
evidence row under its own id, then found a second one: relabel `DERIVED_AI_EVIDENCE` as `OBSERVED`,
re-derive a matching id, close the UNKNOWN. The id check proves *integrity* — a record matches its
own id — and no store-layer check can prove *provenance*, that `derivation` truthfully describes how
the record was produced. A caller that constructs a record labelled `OBSERVED` is asserting it
observed something; distinguishing a true assertion from a false one needs an attestation mechanism
the architecture does not have. Phase 1's INVARIANTS forbid AI calls and no code path emits
`DERIVED_AI_EVIDENCE`, so the attack needs a caller that does not exist. This is a Phase 3 design
question, to be raised in `dev/proposals.md` when the AI residue filter first produces such evidence
— not a quiet code change, because it touches the frozen Evidence schema or the Observer contract.

**M-7 is closed.** `write_evidence` stages records in memory and `write_candidates` validates and
persists evidence plus candidates in one transaction. Records offered before their run exists are
rejected, staged evidence is rebound immediately before commit, and `finish_run` still refuses any
unexplained evidence. The final Phase 1 gate exercised the pre-run call-order attack directly.

The five non-blocking Phase-1 carryovers are closed. The `Detected` line includes per-version site
counts and `UNKNOWN (n)`; the pack header labels the composite changes hash; the unapproved extra
`EXCLUDED` line is gone; `ProviderPack` has `wire_signature` and `versions()`; and an enumerated but
unscannable `rg` hit is preserved as evidence.

## Blocking

- The Phase 4 gate audit and real-repo loop have not been run. Implementation is complete and the
  suite is green, but no Phase 4 gate has passed and nothing may merge to `main` until one does.
- Nothing blocks the completed Phase 3 gate. `ast-grep` 0.45.0 is installed and its identity is
  proof-bound.
- Podman 4.9.3 runs rootless in Ubuntu WSL and is the only eligible capture engine; the host's
  Docker Desktop engine is rootful and is refused. The engine reports cgroups v1 and ignores
  `--memory`/`--pids-limit`, so POSIX rlimits and the parent wall/output bounds are the enforcement
  that is actually proved.
- Capture state must live in a **short** host directory. A deep path makes a bind-mounted file
  unreadable inside the container (`EIO`), which costs proxy mode its certificate (FA-013).
- P-009 remains OPEN. AI triage is default-off and disconnected; it must not receive an operational
  application, CLI, scan, or store route until the owner decides the producer-attestation boundary.
- `rg` was not on this machine at the start of Phase 1; the official 14.1.1 binary is now at
  `~/.local/bin/rg.exe`. The scan refuses to run without it (`TOOLING_MISSING`), by design.
- `pyright` is a dev dependency and pinned in `uv.lock` (1.1.411). It used to run only from a
  machine-global install, so "pyright is clean" was not a reproducible claim.

## Decisions that carry forward

*(record here anything a later phase must not re-litigate — with the phase it was decided in)*

**Phase-4 decisions (2026-09-07).**
- A memory bound is `RLIMIT_DATA`, never `RLIMIT_AS`. `RLIMIT_AS` is a separate, larger
  address-space bound because managed runtimes reserve address space they never touch. Do not
  collapse the two back into one field to "simplify" the limits.
- A sensor must attest that it installed. Hook mode carries a per-run nonce and proxy mode counts
  failed interceptions, so "the sensor did not observe" and "the workload made no provider call"
  are different, separately named outcomes with different closing instructions. Any future capture
  channel owes the same distinction; without it, zero events silently reads as zero usage.
- Production inputs reconcile against the run's static **plus** dynamic evidence, and never against
  each other, so the accounting is order-independent across multiple production inputs while still
  crediting what this run observed.
- The workload's trust anchor lives in a directory the proxy container cannot write, and the
  proxy is ready only when its listener is up, not when its certificate exists.
- The sentinel's own suite is a separate product run:
  `uv run --package hubbleops-sentinel pytest -c pyproject.toml
  --override-ini="testpaths=packages/hubbleops-sentinel/tests"
  --override-ini="pythonpath=packages/hubbleops-sentinel/src" packages/hubbleops-sentinel/tests`.
  The main suite's `testpaths` deliberately excludes it; that is the independence boundary, not an
  oversight. The main suite still proves the package imports no `hubbleops.*` and emits no verdict.
- `dev/plan.md` named the sentinel hook smoke input `sentinel_hook_input.jsonl`. Hook mode takes a
  Python entrypoint to run, not a JSONL file, so the fixture is
  `tests/fixtures/phase4/inputs/sentinel_hook_app.py`. Phase 4 fixtures are split into `repo/`
  (the capture target, which must stay clean for `_require_clean`) and `inputs/` (command inputs).

**Phase-3 completion decisions (2026-09-06).** Structural language differences live in pack-owned
ast-grep rules; the generic graph consumes neutral captured facts and contains no provider knowledge.
The frozen `request_text` claim type carries structural fragments and holes instead of introducing a
new `request_skeleton` type. Every INSIDE file is conserved as supported, STRUCTURE_UNSUPPORTED, or
FILE_UNSCANNED; forced missing-tool scans never silently shrink the ledger. Ambiguous wrapper paths
stay grouped at the originating candidate and remain UNKNOWN. P-009 is deliberately OPEN: correctly
labelled AI-only closure and same-id relabelling are rejected, while relabel-plus-rehash requires
producer attestation at a frozen boundary. AI triage therefore stays default-off and disconnected.

**Phase-2 completion decisions (2026-09-05).** All questions in `dev/plan.md` are answered. The
offline lattice supports v19-v25, including sunset nodes; v25 is the pinned current major and future
majors use the same manifest-driven refresh pipeline. Public official field-reference data is the
offline catalog source; no live credentialed FieldService call is part of this gate. A versioned
gRPC or REST request target is authoritative, while `x-goog-api-client` is metadata and conflicts
remain typed UNKNOWN. Docs-only mappings are DOCUMENTED and compose only when unique. No new runtime
dependency was added. Phase 1 remediation remains preserved and uncommitted.

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
  pack-facing name and holds the `ProviderPack` Protocol. Recorded as accepted **P-003** in
  `dev/proposals.md`.
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

- Phase 4 needs its fresh gate audit and real-repo loop. The loop must run `scan`, `exposure` and
  `capture` on the two pinned public repositories under `.hubbleops/artifacts/phase2-real-repos/`,
  with a deny-all network, no credentials and no dependency installation; a missing offline
  dependency is a `CAPTURE_EXECUTION_FAILED` UNKNOWN, never a reason to enable network.
- P-009 must be decided before AI triage is operationally connected; no Phase 3 or Phase 4 runtime
  path reaches it.
- The Exposure Map production-services line is live: it prints `N/M` once a telemetry or sentinel
  observer is in the ProofScope, and the "not in this ProofScope" wording only when neither is.
