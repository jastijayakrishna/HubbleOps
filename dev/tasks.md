# Tasks

Updated before every session ends. Phase-level status lives in
[docs/BUILD_ORDER.md](../docs/BUILD_ORDER.md); this file is the working queue.

## Now

- [x] Merge Phase 4 to `main` and tag `v0.4` on the owner's instruction, recording that the merged
      bytes rest on a green full suite rather than a second gate audit, and that the merge is the
      decision on P-010
- [x] Plan Phase 5 and answer every open question in `dev/plan.md` before implementing
- [x] Raise and decide **P-011**: `Falsifier` gains `failure_class` and `check()`. A conjunct of the
      frozen verdict rule wired to a constant is a weakened verdict rule, which is an approval
      boundary, so the protocol had to get the shape it deferred to this phase
- [x] Implement Phase 5: `verify/{gitdiff,audit,oracle,radius,coverage,suites,behavior,falsify,
      conserve,verdict,authority}.py`, `proof/receipt.py`, a real `sandbox/verifier_image.py`,
      `hops verify`, and eight Google Ads falsifiers
- [x] Fix the five defects the first end-to-end run found: the response-consumer check flagged
      dict-literal test data as a read; the oracle, the shape differential and both packs'
      falsifiers all looked for request text under keys the observer does not write, so three checks
      passed vacuously; obligation reconciliation could not see a field named inside a query;
      containment resolved obligation sites only through run-scoped evidence ids; and the rescan's
      run id came from the temporary worktree path, so two verifications of the same two commits
      produced different receipts
- [ ] Run the [red-team](../prompts/cross-cutting/red-team.md) against the finished authority, then a
      fresh-session [gate audit](../prompts/cross-cutting/gate-audit.md), then the Phase 5
      [real-repo loop](../prompts/cross-cutting/real-repo-loop.md). Only a literal `GATE: PASS`
      permits the merge to `main` and the `v0.5` tag
- [ ] Phase 5 ships no live Google Ads oracle run: no test-account credentials exist on this machine,
      so `google_ads` verification reports `ORACLE_UNAVAILABLE` and caps at UNKNOWN by design. The
      oracle path itself is exercised end to end against the `_mock` pack with real request hashes.
      Supply credentials via env to close this

- [x] Install rootless Podman for Phase 4 — 4.9.3 in Ubuntu WSL, measured rootless, local,
      uid/gid-remapped and seccomp-enabled; the host's rootful Docker Desktop engine is refused
- [x] Implement Phase 4: `sandbox/`, `observe/dynamic/`, `observe/telemetry.py`, `hops capture` in
      proxy and hook modes, `hops promote`, and the standalone `hubbleops-sentinel` package
- [x] Fix the five defects found while completing Phase 4, each a confident absence in place of a
      named uncertainty: V8 cannot start under the address-space limit that was carrying the memory
      bound (FA-011); an uninstalled hook reported "no events" (FA-012); a failed TLS interception
      reported "no events" (FA-013); telemetry reconciled against a static-only ledger and
      manufactured `TELEMETRY_UNEXPLAINED` for tuples the run had just observed; the sentinel wheel
      could not build at all
- [x] Add the Phase 4 evidence the prompt requires: DI-fixture hook capture with a recorded wrapper
      chain, the same fixture in proxy mode producing an equivalent candidate, a deliberately
      unmatched telemetry tuple, the sentinel installed from its wheel and smoked in both modes, a
      promotion round trip, and `test_no_provider_leak` over `observe/dynamic`
- [x] Run the Phase 4 [real-repo loop](../prompts/cross-cutting/real-repo-loop.md) including
      `hops capture`. Static results are unchanged from Phase 3 on both pinned repositories and
      both captures failed closed on absent dependencies with zero unexplained candidates. Found
      FA-014 (a read-only worktree metadata directory left state inside a prospect repository) and
      recorded FA-015 (package-manager egress is correctly denied and named)
- [x] Run the repaired tree through a new fresh-session
      [gate audit](../prompts/cross-cutting/gate-audit.md) for Phase 4. The first completed audit's
      five findings are repaired. The next audit passed all executable checks but returned
      `GATE: FAIL` because preflight `git status` bypassed the transcript and setup-failure records
      were discarded with the temporary attempt. Preflight Git is now bounded and transcripted;
      failures persist hash-bound Git/engine/proxy records plus their request and error manifest.
      The final fresh audit on `e408545` returned literal `GATE: PASS`: 430 tests, all 33 runtime
      integrations, standalone package checks, Law attacks, and the repeated real-repo loop passed
- [ ] Merge `phase-04-dynamic-capture-and-sentinel` to `main` and tag `v0.4` locally after the
      literal `GATE: PASS`; do not push. **Reopened**: this was recorded as done and was not done.
      `main` is still at `7ff86d1` "Merge Phase 3 wrapper engine" and the newest tag is `v0.3`.
      It now also needs a fresh gate, because the hardening below changed the bytes the Phase 4
      gate passed on

- [x] Profile the scan and repair what the profile found, not what looked slow. `source_closure`
      was 80% of a scan: it opened, read and SHA-256'd every file under `.venv` and every file of
      HubbleOps's own `.hubbleops/` run output, then classified them as excluded. Both are now
      pruned at the walk and accounted as one `UNSCANNED` entry each. Same-interpreter A/B on this
      repository: 17.49 s → 2.16 s, 14,369 → 1,586 entries, 8.1x. Raised as P-010 because
      `tree_hash` semantics change
- [x] Add the structured run log `docs/ARCHITECTURE.md` §12 requires and the code never had —
      `core/runlog.py`, keyed by run_id/component/duration/outcome, JSON to stderr, silent unless
      `HOPS_LOG` is set so existing CLI output is byte-identical
- [x] Run the three observers concurrently with results consumed in submission order, so the
      ledger stays byte-identical; `test_two_consecutive_scans_are_byte_identical` still passes
- [x] Give `sandbox/` one public surface. `app/capture.py` reached into seven of its nine modules
      and assembled `RunSpec` by hand; it now imports `hubbleops.sandbox` only. The typed JobSpec
      consolidation belongs in Phase 5 when verification adds the second caller
- [x] Enforce the boundaries rather than trusting them: zero import cycles, cross-layer imports
      address a layer surface, and a tolerated deep-import list that fails when it goes stale
- [x] Defer `jsonschema` behind its cached constructors; CLI import 739 ms → ~400 ms
- [x] Add one total bounded JSON parser (`core.records.parse_json`) and route the untrusted
      workload paths through it. The six ad-hoc `(JSONDecodeError, UnicodeDecodeError)` sites all
      missed `RecursionError`; deep nesting is now a named reason, never a crash
- [x] Set `busy_timeout` on the store connection so a reader waits for the writer

- [x] Repair the regression the new run log caught on its first real scan: pruning the closure left
      ripgrep walking `.hubbleops/uv-cache/`, which matched a path the closure no longer enumerated
      and correctly stopped the scan. `SourceClosure.search_exclusions()` is now the one source of
      truth both the enumerator and the searcher read. Recorded as FA-017
- [x] Batch analyzer paths under a 24,000 character budget. Measured before: 800 files failed with
      `TOOLING_MISSING` naming a tool that was installed. After: 4,000 files pass. A refused start
      with the executable present is now `TOOLING_FAILED` naming the argument length. FA-016
- [x] Index the import graph by path and id. Wrapper-walk lookups scanned every match in the
      repository and filtered by path; `SourceRange.contains` was called 1.5 M times per build.
      Graph build 5.64 s → 3.39 s on 60 files, and the complexity class changed, so the saving grows
      with repository size. FA-018
- [x] Bound the analyzer's output and match count so a pathological rule fails closed with a named
      reason instead of exhausting memory
- [x] Add `tests/property/test_cost_budgets.py`: deterministic counter assertions (batch counts,
      invocation counts, entries enumerated) that catch a cost regression on any machine without
      timing anything, and `tests/property/test_adversarial_trees.py`: Hypothesis-generated hostile
      repository trees asserting the closure is total, deterministic, and never enumerates what it
      pruned
- [x] Add `.github/workflows/ci.yml` so lint, format, types, tests and the cost budgets run without
      anyone remembering. Inert until the repository is first pushed

- [ ] Consider collapsing the 18 per-language ast-grep invocations into one multi-rule pass. Each
      invocation reparses every file, so a four-language repository is parsed 73 times. Parallelism
      was measured and rejected: 1.0-1.1x, because ast-grep already saturates the CPU internally.
      One `scan` over a merged rule file is the real fix and needs a match-equivalence proof first

- [x] Owner accepted P-008: correct frozen P-006 so Google Ads endpoint versions come from the
      gRPC/REST request target, while `x-goog-api-client` remains metadata and ambiguity emits a
      typed UNKNOWN instead of silence
- [x] Fresh-session review of the answered Phase 2 plan; final independent review returned `PLAN: PASS`
- [x] Implement and verify the full Phase 2 Google Ads and `_mock` ProviderPacks, v19-v25 offline
      source/catalog lattice, computed diff composition, safe oracle, wire/telemetry adapters,
      ProofScope composition, Exposure Map completion, fixtures, and `hops pack verify`
- [x] Fresh Phase 2 gate audit returned `GATE: PASS`; the first real-repo loop is complete and
      every NEW_PATTERN is recorded in `docs/FAILURE_ATLAS.md`
- [x] Plan, review, implement, and verify the Phase 3 provider-neutral structural observer,
      deterministic import/symbol graph, five-hop wrapper walk, query skeletons, fail-closed
      structural coverage, Google Ads and `_mock` ast-grep rules, and default-off AI triage boundary
- [x] Complete the Phase 3 real-repo loop and final fresh audit: FA-010 is closed by the anonymized
      parent-relative TypeScript import fixture and generalized local-import normalization; the
      post-loop auditor returned literal `GATE: PASS`

- [x] Finish the four Phase-2-owned carryovers: `Detected` site counts (P-005),
      `<changes_hash>` label, the extra `EXCLUDED` line (P-004), and `wire_signature`/`versions()`
      on the `ProviderPack` Protocol
- [x] Preserve an `rg` hit on an enumerated-but-unscannable path as evidence instead of discarding it
- [x] Fix the four new minor findings from the fourth audit: a symlinked directory yields no closure
      entry (N-6), one unreadable file aborts the whole scan instead of becoming `FILE_UNSCANNED`
      (N-7), the `_mock` map header has a double space (N-8), and the guard hook allows
      `# pragma: no cover`, which is not one of CLAUDE.md's four comment exceptions (N-9)
- [x] **Phase 3, before the AI residue filter ships**: raise P-009 for evidence provenance
      (**F-6**). The store proves an evidence id matches its content but cannot prove `derivation`
      is truthful, so a caller can label AI-derived evidence `OBSERVED` and close an UNKNOWN. Needs
      attestation, which touches the frozen Evidence schema or the Observer contract. P-009 remains
      OPEN and AI triage remains default-off and disconnected pending the owner's decision
- [x] Fix **M-7**: `write_evidence` commits before any candidate exists and `latest_run` does not
      exclude `finished_at IS NULL`, so a committed state can carry unexplained evidence (L1 letter).
      Evidence is now staged and persisted atomically with its candidates
- [x] Fix the three minor findings from the fifth audit: `start_run` silently keeps the first
      ProofScope when a `run_id` restarts under a second one (m-2), and every `close_with` names
      `hops decide`, a verb that does not exist until Phase 7 (m-3)
- [x] Reject a caller-supplied Candidate id unless it is re-derived from every attached Evidence
- [x] Remove the unapproved Markdown fixture by renaming it to `reporting.txt`
- [x] Run the [gate audit](../prompts/cross-cutting/gate-audit.md) against the repaired Phase 1 tree. Phase 1 merged
      and tagged `v0.1` without a passing gate, by the repository owner's decision after seven
      `GATE: FAIL` runs; the final fresh audit returned `GATE: PASS`
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
- [x] Install `ast-grep` before Phase 3
- [x] Fix the second gate audit's findings: silent media binaries, evidence-existence at the store
      boundary, and eleven non-blocking items
- [x] Fix the first gate audit's findings: recall over manifests, L3 enforcement, and eight
      non-blocking items
- [x] Supply `docs/ARCHITECTURE.md` — the frozen build document
- [x] `git init` this directory
- [x] Install toolchain: `uv`, `ruff`, `pyright`, `rg`
- [x] Activate `.claude/settings.json` per [docs/HOOKS.md](../docs/HOOKS.md)

## Phase gates

- [x] Phase 1 — scan + exposure + ledger *(fresh final audit: `GATE: PASS`)*
- [x] Phase 2 — Google Ads pack + Change Pack version lattice *(fresh `GATE: PASS`; first real-repo loop complete)*
- [x] Phase 3 — wrapper engine *(post-loop fresh `GATE: PASS`; FA-010 closed)*
- [x] Phase 4 — dynamic capture + sentinel *(fresh `GATE: PASS` on `e408545`; merged to `main` and
      tagged `v0.4` on 2026-09-08 by the owner's instruction, on a green full suite rather than a
      second audit of the hardening commits)*
- [ ] Phase 5 — verification authority *(implemented; red-team, fresh gate audit and real-repo loop
      still owed before merge and `v0.5`)*
- [ ] Phase 6 — obligations + repair
- [ ] Phase 7 — proof pack + PR + guard
- [ ] Phase 8 — incremental system
- [ ] Phase 9 — mock pack conformance
- [ ] Phase 10 — pilot hardening

Each gate is: plan → plan review (fresh) → implement → evidence → gate audit (fresh) → real-repo
loop (from Phase 2).

## Recurring

- [ ] Weekly: [spec-drift audit](../prompts/cross-cutting/spec-drift-audit.md)
- [ ] Nightly from Phase 5: [red-team](../prompts/cross-cutting/red-team.md) — now live, since the
      authority it attacks exists

## Blocked / parked

- Nothing is parked. Production-services accounting now prints `N/M` whenever a telemetry or
  sentinel observer is in the ProofScope, and "not in this ProofScope" only when neither is.
