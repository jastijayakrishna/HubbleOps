# Tasks

Updated before every session ends. Phase-level status lives in
[docs/BUILD_ORDER.md](../docs/BUILD_ORDER.md); this file is the working queue.

## Now

- [ ] Fix the five non-blocking findings carried since the third audit: `Detected` site counts
      (P-005), `<changes_hash>` label, the extra `EXCLUDED` line (P-004), `wire_signature`/
      `versions()` on the `ProviderPack` Protocol, and the discarded `rg` hit in `observe/text.py`
- [ ] Fix the four new minor findings from the fourth audit: a symlinked directory yields no closure
      entry (N-6), one unreadable file aborts the whole scan instead of becoming `FILE_UNSCANNED`
      (N-7), the `_mock` map header has a double space (N-8), and the guard hook allows
      `# pragma: no cover`, which is not one of CLAUDE.md's four comment exceptions (N-9)
- [ ] **Phase 3, before the AI residue filter ships**: raise a proposal for evidence provenance
      (**F-6**). The store proves an evidence id matches its content but cannot prove `derivation`
      is truthful, so a caller can label AI-derived evidence `OBSERVED` and close an UNKNOWN. Needs
      attestation, which touches the frozen Evidence schema or the Observer contract
- [ ] Fix **M-7**: `write_evidence` commits before any candidate exists and `latest_run` does not
      exclude `finished_at IS NULL`, so a committed state can carry unexplained evidence (L1 letter).
      Wants an atomic evidence+candidate write or an unfinished-run filter
- [ ] Fix the three minor findings from the fifth audit: `start_run` silently keeps the first
      ProofScope when a `run_id` restarts under a second one (m-2), and every `close_with` names
      `hops decide`, a verb that does not exist until Phase 7 (m-3)
- [ ] Run the [gate audit](../prompts/cross-cutting/gate-audit.md) against `main`. Phase 1 merged
      and tagged `v0.1` without a passing gate, by the repository owner's decision after seven
      `GATE: FAIL` runs; the eighth was never run against the tree that fixes F-8
- [x] Fix **F-8**: the scanner fingerprint covers every module in the package, discovered not listed
- [x] Fix **F-5**/**F-7**: every surface field is tested to reach the proof key, and one unreadable
      file no longer throws the whole scan away
- [x] Fix **F-4**/**m-1**: the fingerprint covers the module that chooses the observers and keys by
      path, not basename
- [x] Fix **M-5**/**M-6**: an evidence id is verified to be the hash of its content; P-007 reaches
      the schema description and the Phase 2 prompt
- [x] Fix **F-3**: the Exposure Map renders the surface the run recorded, not the pack on disk
- [x] Fix **M-1**/**M-3**/**M-2**/**M-4**: P-007 decided; `scanner_version` fingerprints the
      observation pipeline; the store's transition guard enforces L3 within a batch and L10 against
      AI-only closures
- [x] Fix **F-1** (NUL anywhere, not just in the sniff window) and **F-2** (the surface binds into
      the ProofScope)
- [x] Third gate audit run — `GATE: FAIL` on F-1 and F-2; fourth — `GATE: FAIL` on F-3 alone
- [x] Write the decision lines on **P-003** and **P-004** in [dev/proposals.md](proposals.md) —
      both ACCEPTED; no proposal is open
- [x] Record **P-005** (version lattice) and **P-006** (language-agnostic wire channel) in
      [dev/proposals.md](proposals.md), both ACCEPTED, and apply them to
      `docs/ARCHITECTURE.md` and the Phase 2/3/4/6/10 prompts
- [x] Merge `phase-01-source-closure-and-ledger` to `main`, tag `v0.1` — done 2026-09-03 with the
      gate waived, not passed
- [ ] Install `ast-grep` before Phase 3; rootless docker/podman before Phase 4
- [x] Fix the second gate audit's findings: silent media binaries, evidence-existence at the store
      boundary, and eleven non-blocking items
- [x] Fix the first gate audit's findings: recall over manifests, L3 enforcement, and eight
      non-blocking items
- [x] Supply `docs/ARCHITECTURE.md` — the frozen build document
- [x] `git init` this directory
- [x] Install toolchain: `uv`, `ruff`, `pyright`, `rg`
- [x] Activate `.claude/settings.json` per [docs/HOOKS.md](../docs/HOOKS.md)

## Phase gates

- [x] Phase 1 — scan + exposure + ledger *(implemented and green; two gate audits have returned
      `GATE: FAIL`, every code finding fixed, re-run pending on the two open proposals)*
- [ ] Phase 2 — Google Ads pack + Change Pack version lattice *(first real-repo loop after this gate)*
- [ ] Phase 3 — wrapper engine
- [ ] Phase 4 — dynamic capture + sentinel
- [ ] Phase 5 — verification authority *(+ red-team, nightly from here)*
- [ ] Phase 6 — obligations + repair
- [ ] Phase 7 — proof pack + PR + guard
- [ ] Phase 8 — incremental system
- [ ] Phase 9 — mock pack conformance
- [ ] Phase 10 — pilot hardening

Each gate is: plan → plan review (fresh) → implement → evidence → gate audit (fresh) → real-repo
loop (from Phase 2).

## Recurring

- [ ] Weekly: [spec-drift audit](../prompts/cross-cutting/spec-drift-audit.md)
- [ ] Nightly from Phase 5: [red-team](../prompts/cross-cutting/red-team.md)

## Blocked / parked

- Telemetry and production-services accounting in the Exposure Map print "not in this ProofScope"
  until Phase 4 supplies a `TelemetryAdapter`; the `Target` line waits on the Phase 2 Change Pack.
