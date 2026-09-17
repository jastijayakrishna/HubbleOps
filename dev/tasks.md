# Tasks

Updated before every session ends. Phase-level status lives in
[docs/BUILD_ORDER.md](../docs/BUILD_ORDER.md); this file is the working queue.

## Now

- [x] **Week 1 audit fixes: seven fail-closed defects closed on `phase-06-audit-fixes`
      (2026-09-16).** Branch cut from `phase-06-repository-intelligence`; ten commits, one per
      defect plus three closing review gaps and one for the fixtures. Full suite on a quiet tree:
      **1359 passed, 1 skipped** in 23:10, the skip being `test_indexers.py:549` (scip-typescript
      is not on PATH, pre-existing). Six new directories under `tests/adversarial/`, all six
      executed. One item is PARTIALLY fixed and carries **P-038**; everything else is closed.
- [ ] **Rule on P-038 (`Falsifier.applies`).** Until it is ruled on, no capture-less
      `hops verify` on `google_ads` can reach `VERIFIED_FOR_SCOPE`: seven of eight falsifiers are
      `NOT_RUN`, every `NOT_RUN` is unresolved, and `verdict.decide` turns any unresolved entry
      into UNKNOWN. The audited hole (a skipped falsifier counting as holding) is closed; the
      replacement over-refuses, and closing it correctly needs the frozen `Falsifier` sub-protocol
      to gain one member. Kept rather than reverted because
      `Cost(FALSE_VERIFIED) ≫ Cost(UNKNOWN)`.

### Found during week 1

- [ ] **`hubbleops/app/verification.py:596` reads with universal newlines too.**
      `first_party_sources` has the same `read_text(encoding="utf-8")` that defect 2 fixed in
      `migration.py`. It feeds obligation `current_state` strings and is never written back, so it
      corrupts nothing today; it is the same latent shape one refactor away from mattering.
- [ ] **`splitlines` splits on more Unicode boundaries than ripgrep counts.** Every pack transform
      uses `text.splitlines(keepends=True)`, which also breaks on a lone `\r`, `\v`, `\f`, `\x85`,
      ` ` and ` `. Observers number lines by `\n` only, so a file carrying any of those
      inside a literal makes `_line_span` target a different line than the one the evidence names.
      Unchanged by the CRLF fix — `\r\n` was always one break either way — but now the only
      remaining terminator disagreement.
- [ ] **`--state-dir` resolves two ways.** Every command that opens the store uses
      `Path(args.state_dir).resolve()` (CWD-relative); `prepare-pr` resolves its obligations
      default, and now its store, with `_repository_state_dir(args, root)` (repo-relative, which
      §19 implies is correct for `.hubbleops/`). With `--repo .` they coincide. With a relative
      `--state-dir` and a `--repo` elsewhere, `hops verify` writes one directory and
      `hops prepare-pr` reads another, and the new receipt-binding check refuses an honest
      receipt. Fail-closed, so it is a usability defect rather than a proof defect, but the two
      resolutions should become one.
- [ ] **An identical re-scan overwrites a decided candidate row.** `hops decide` now persists the
      decided status, but a later `hops scan` of the same tree writes a fresh ledger for the same
      `run_id` and puts the row back to UNKNOWN with no guard and no record. The decision survives
      in `.hubbleops/decisions.yml` and is re-applied at verify time, so nothing is lost, but the
      store is not yet the place the answer lives.
- [ ] **`hops guard --install` exits FAILED on a repository with no retired surface.** `--install`
      writes the workflow, then the run refuses because nothing was loaded. The pair is correct
      fail-closed behaviour — the exit code reports the guard, not the install — but a user
      installing the guard before their first migration sees a failure for a command that
      succeeded.

- [x] **First-signal corpus: ten independent families pinned, harness built and tested
      (2026-09-14).** 259 unique repositories discovered, 48 cloned and verified, 33 eligible,
      4 barred as the engine's own development set, 29 available, 10 pinned in
      `dev/corpus/families.json` covering php/python/typescript and every source version
      v19…v24. The STOP condition did not fire. `dev/corpus/definitions.json` and
      `dev/corpus/matching-rules.json` were written before the first run and not edited during
      it. Harness at `tests/corpus/` with `run`, `score`, `label`, `challenge`,
      `decision-report` and `check`; 40 unit tests; ruff and strict pyright clean. Arm A
      refuses to run unless engine tree, pack tree, `uv.lock` and both tool hashes match
      `dev/engine-v0.json`.
- [ ] **Build S1, the third label source.** Type-check each family twice with the same checker
      and settings, once against the source-version SDK and once against v25, and take a
      diagnostic present under v25 and absent under the source version as an actionable signal.
      Record per file: checked, any/mixed typed, suppressions present, unresolved imports,
      checked namespace reached; a file failing any of those is UNADJUDICATED by S1, never
      clean. This is the only source independent of the version-literal surface that both S2
      and arm A read, so until it exists the recall numbers rest on two sources that share a
      detection idea. Python first (pyright is already pinned); PHP needs php and composer,
      which this machine does not have.
- [ ] **Get the labels signed.** Every scorecard produced so far carries `labels_signed=false`
      and is provisional by its own definition. The owner must review every disagreement
      between sources and between a source and the blind audit, and sign. No number here is
      settled until that happens.
- [ ] **Run the blind audit.** A fixed-seed sample of the regions all sources call clean and of
      the sites two or more sources agree on, audited without sight of either arm's output, to
      estimate how often a clean region is wrongly clean. Not built.
- [ ] **Show the three decision reports to two intended users.** The reports render
      (`.hubbleops/artifacts/corpus/decisions/`) and name a single next action each. Whether a
      user finds that action unaided is a question only the two users can answer, and neither
      has seen them.
- [ ] **Arm C proper.** Needs a validation-only Google Ads developer token and OAuth refresh
      token in `~/google-ads.yaml`. Until then only `C-offline` exists and its numbers are not
      arm C's. See the two Windows installer defects recorded in `dev/context.md`.
- [x] **FA-069 … FA-072, FA-074 closed; FA-073 narrowed. Engine frozen as `engine-v0`
      (2026-09-13).** PART ELEVEN's four mechanisms integrated into their consumers; eleven
      commits from `engine-v0-pre` (`e5e1237`). FA-069 was misdiagnosed: a background
      `pip install .` moved the tree under a running scan, and the closure does enumerate
      generated directories — the row and every `dev/` sentence repeating it are corrected.
      FA-070 closed (the closure emits the globs, the text observer passes them through).
      FA-071 closed (absence judged over the sites the scan left open). FA-072 closed (a bare
      leaf is watched only when unambiguous and hits only where the removed subject's parent
      is referenced) — the consumer check now **PASSes** on tap-google-ads. FA-074 closed (an
      uncomposable version becomes a HUMAN obligation; the map names `version:v9->v25 12
      sites · 3 files human`). `setup.py` `install_requires` is read, so the map names
      `google-ads 30.1.0 · v25 python minimum 31.2.0 · below floor`, and migrate now
      discharges 9 rather than calling six already-satisfied obligations human work.
      FA-073's first half closed after the freeze: the layout honours the test command a
      repository declares in CI when no pytest configuration file declares one, so
      tap-google-ads now runs `tests/unittests` and its frozen baseline is **69 PASS / 4
      FAIL** where it was `EXECUTION_FAILED` 0 / 42. That exposed the second half, which is
      its own task below. Chain rerun verdict **FAILED** on reasons that are all true of the
      tree: 27 v9 obligations OPEN, the `per_call_version_override` falsifier on the same
      three `spikes/` files, and four pre-existing red tests the conjunct cannot yet tell
      apart from tests the migration broke; prepare-pr refused the receipt.
- [ ] **Run the frozen suite against the base SHA so a red baseline is not a candidate
      failure (FA-073, second half).** Now that the CI-declared test command is honoured, the
      tap-google-ads frozen baseline runs — `tests/unittests`, **69 PASS / 4 FAIL** — and
      `radius.frozen_report` judges `passed = failed == 0`, so those four report as
      `frozen_baseline_tests_pass is FAIL` and the verdict is FAILED. The four are
      date-dependent conversion-window tests that fail on the base SHA identically; the
      migration broke nothing. The conjunct cannot tell the two apart without running the
      frozen suite on the base SHA first, which is what closes this. Until then a repository
      with any pre-existing red test cannot reach VERIFIED_FOR_SCOPE however correct the
      repair is. `hops verify` already stages the base tree, so the run has an obvious home.
- [ ] **Triage the 91 tap-google-ads UNKNOWNs into the P-024 baseline shape.**
      `tests/fixtures/real_repo/tapga_identities_before.json` and
      `tests/integration/test_tapga_identity_conservation.py` now guard identity —
      all 647 candidates at `5b6a201`, none may disappear. What they deliberately do not
      carry is the per-UNKNOWN `root_cause` and `expected_future_disposition` the Dub and
      GLNA baselines have, because those are triage against named atlas rows and inventing 91
      of them would freeze a judgement nobody made. Do the triage, then fold this repository
      into `test_real_repo_conservation` beside the other two.
- [ ] **Tier 3c (PART TEN of [dev/plan.md](plan.md)) — stopped at Q32.** DoD 1 is designed
      (attestation inside Evidence `value`, no schema change, enforcements E1–E6); no AI call
      path exists and none is written until the owner answers Q32. Unblocked by any answer and
      next to build: C0 (attestation + store guards + layer rules), C1 (the 22 ambiguous rows
      gain identity; `hops decide --pack-row`), C2 (sunset: `--as-of`, days-to-sunset,
      `hops impact --release/--sunset-within/--install-workflow`), C3 (sentinel log-line proxy
      input, `hops promote --from-sentinel`). Contingent on a yes: C4 (client, prompts, four
      producers and judges). Then C5: measurement, red team with three AI-path corruptions,
      spec audit. Ground truth before: Dub 327 · 4 · 73 · 32 · 0, GLNA 1143 · 259 · 218 · 333
      · 0, measured on a fresh `--state-dir`; the default state file is store schema v1 and is
      refused by this build.
- [ ] **Gate audit on `0aa558b` — `GATE: FAIL`.** Blockers, in weight order:
      **(1) a Receipt survives a new SHA.** `prepare-pr` binds only to
      `migration_audit.candidate_sha`; it computes `content_id(proof_scope)` and never compares it
      to the tree ([hubbleops/proof/memory.py](../hubbleops/proof/memory.py):61-67), so forging
      that one string publishes a `VERIFIED_FOR_SCOPE` PR body for a tree nothing verified —
      reproduced end to end, exit 0, `proof_scope.repo_sha 01cab16` against `HEAD c5b0cba`.
      Breaks L4 and §17, and it is the product promise inverted.
      **CLOSED on engine-v0**: the ProofScope's `repo_sha` and the audit's `candidate_sha` are
      compared to each other before either is compared to HEAD, a receipt recording no
      `repo_sha` is refused rather than treated as unbound, and
      `tests/integration/test_phase5_verify.py` (`…forged_candidate_sha…`) forges the field
      against a moved tree and asserts prepare-pr exits non-zero and writes no body.
      **(2) the injected `ContractOracle` is never called.** `oracle` occurs once in
      [hubbleops/obligations/engine.py](../hubbleops/obligations/engine.py):42, as a parameter;
      validation lives in `app/exposure.py` and `hops migrate` never reaches it. DoD 1, TRAP 4.
      **CLOSED**: the engine validates every request skeleton against the target through the
      injected oracle; a rejection is HUMAN work carrying the oracle's reason, and an oracle
      that cannot decide, raises, or accepts without naming an authority yields
      PRESERVE_UNKNOWN. `validated_outcome` moved to `core/verification.py` so verify and the
      engine harden identically, and `static_request` to `core/requests.py` so the engine
      never imports the layer that judges it. migrate now injects the offline `pack.contract`
      rather than the live `verification_contract()`: obligations must be reproducible from
      the tree, and exposure already built them offline.
      **(3)** DoD 5's `python_pinned_v22` migrate → verify chain has no test; `migrate` appears in
      `tests/` only under `--pack _mock`.
      **CLOSED**: `tests/integration/test_chain.py` runs the fixture migrate → commit →
      verify under `--pack google_ads`, asserts the v22 call site is rewritten and the
      obligation carries `version:v22->v25` rather than a repository-wide current version,
      and asserts the verdict verify wrote — including that a repository shipping no tests
      never earns VERIFIED_FOR_SCOPE.
      **(4)** `precise_indexes` changes FROZEN §3.2 with no ACCEPTED proposal.
      **RAISED as P-036, still OPEN**: only the repository owner can accept a change to a
      frozen surface. P-033's bullet recorded it as a standing deviation, which is not how a
      frozen surface changes; that bullet now points at the proposal.
      **(5)** `GENERIC_LAYERS` in `tests/support.py` omits `repair`, so the leak test this phase
      names for `repair/` never looks there.
      **CLOSED**: the scanned set is derived from the tree — every package under `hubbleops/`
      with an `__init__.py` that is not `app/` or `packs/` — so a layer added tomorrow is
      inside law L5 the day it lands. `repair/` was clean; nothing had ever checked.
      27 of 28 Law experiments fail closed. The audit is itself incomplete and must not be re-run
      as-is: item (a) was never executed by an auditor, L7 was never tested, and 17 of 31
      ARCHITECTURE sections were never classified. One critic reason did NOT reproduce — `hops
      migrate` does not write `.hubbleops/decisions.yml`; `app/migration.py` never names it and the
      writers are `hops decide`, `hops guard` and `hops prepare-pr`.
- [x] **The checks workflow ran for the first time and is green.** It had never executed
      before 2026-09-13 because nothing had been pushed; all five of its first runs died at
      `Install ast-grep`, since `unzip -d` creates only the final path component and
      `/home/runner/.local` does not exist on the runner, so Lint, Format, Types, Tests and
      the sentinel step had never once run. Three fixes: `mkdir -p` before the unzip and the
      archive into `RUNNER_TEMP` rather than the checkout, where a scan would see it;
      `uv sync --frozen --all-packages`, because the plain form syncs only the workspace root
      and the sentinel step then cannot import its own package; and the executable bit on the
      binary `_vendor` fakes in [tests/unit/test_toolchain.py](../tests/unit/test_toolchain.py),
      which Windows never needed because `os.access` calls every existing file executable
      there. On ubuntu-latest: ruff clean, 227 files formatted, pyright `0 errors`,
      **1141 passed / 43 skipped**, sentinel 12 passed, cost job green. Note the platform gap
      — 43 tests skip on Linux against 3 on Windows, so a green CI is not the local suite.
- [x] **Fixture staging defeated git's stat cache**: `_write_tree` in
      [tests/phase5_support.py](../tests/phase5_support.py) staged the base and candidate
      trees with `shutil.copy2`, which preserves the source mtime. Both `client.py` fixtures
      are 373 bytes, so on a fresh checkout they share an mtime second, `git add --all` read
      them as unchanged, and the candidate commit reused the base blob — the verifier then
      correctly reported v1 residue against a tree that really did carry v1. A pristine clone
      failed 10 of 38 (8 in `test_phase5_verify.py`, 2 adversarial); with `copyfile` the same
      clone passes 38. Long-lived worktrees hid it because their fixture mtimes had drifted
      seconds apart. `shutil.copytree` elsewhere in `tests/` has the same property and is not
      yet known to bite; it wants the same treatment if a staged fixture ever goes quiet.
- [x] **Tier 3a Q29-Q31** answered by delegation ("do whatever is best for customers") on
      2026-09-13; the choices are in PART EIGHT PROGRESS of [dev/plan.md](plan.md).
- [x] **W0** `tests/fixtures/real_repo/dub_unknowns_before.json` frozen (100 entries, 3 retired,
      6 re-keyed); baseline test parameterised over both repositories.
- [x] **W1** four platform wheels with pinned hashes; `scanner_version` binds both binaries'
      sha256; a swapped vendored binary stops the scan. Posix wheels built on Windows carry
      0644 on the binaries (NTFS cannot chmod) and fail closed at runtime: release builds of
      the three posix wheels must run on a posix host.
- [x] **W2** `hops exposure --install-workflow` (`proof/exposure_workflow.py`).
- [x] **W3** lattice judgement in the resolver, target judgement on the map's QUERIES block,
      offline contract everywhere, printed at the end of `hops scan --target`.
- [x] **W4** all mechanisms (FA-054 … FA-060) plus `.env*` as CONFIG. Dub UNKNOWN 103 → 32,
      three byte-identical scans, 0 baseline violations; GLNA 358 → 333, 259 AFFECTED kept.
- [x] **W5** case-preserving namespace transform, non-semver pins stay HUMAN, bound references
      edit their import line, SATISFIED outcome, decided request sites, P-032 proposed. GLNA:
      migrate 258/258, verify twice → FAILED with the same body hash, prepare-pr refuses it.
- [x] **W6** `tests/integration/test_ship_path.py` (35 s on win_amd64).
- [x] **W7** first gate audit `GATE: FAIL` on three blockers (stale-SHA receipt accepted by
      prepare-pr, verdict trusted as written, unrecorded GLNA re-key with no real-scan test);
      all three closed with tests. Re-audit: blockers confirmed closed, `GATE: FAIL` on the
      prepare-pr → guard seam, now fixed (PR body is run output; textually present patterns are
      deferred, not retired; mock `version=` default is a carrier; guard uses the vendored rg).
- [x] **Tier 3b spike** (PART NINE of [dev/plan.md](plan.md)): five indexers measured without
      installing any repository's dependencies; TypeScript/TSX/JavaScript and Python GO, PHP,
      Java and .NET recall-only with the enabling sentence on the map.
- [x] **B1** `core/precise.py`: dependency-free SCIP reader, `SymbolIndex`, descriptor parser;
      47 unit + property tests, project_root never exposed.
- [x] **B2** `graph/indexers.py`: `ScipTypescript` (staged copy, derived tsconfig, synthesized
      manifest, entry-script identity, version-mismatch refusal) and `ScipPython` (Linux only,
      heap sized, refuses Windows with the reason); 24 + 4 tests with a fake indexer plus one
      real-tool test that runs when `scip-typescript` is on PATH (it passed here).
- [x] **B3** graph integration: callers by symbol (a name caller is removed only when its
      symbol belongs to another definition in the graph), barrel, alias and renamed imports
      resolved to their definition, identifier bindings by symbol to first-party definitions
      only; per-caller hop suffix `by symbol`. Six revert checks each fail their test when
      the mechanism is removed.
- [x] **B4** `precise` per language and the indexer status sentence on the map and in the
      export; `scanner_version` carries `;scip-typescript=<version>+sha256:<entry>` when the
      indexer ran.
- [x] **B5** ground truth after: GLNA 1143 / 259 / 218 / 333 UNKNOWN / 0 unexplained,
      javascript precise 873 of 891; Dub 327 / 4 / 73 / 32 / 0, typescript 2,346 of 2,348 and
      tsx 1,991 of 1,991 precise; candidate ids and statuses identical to before on both.
- [x] **B6a** held-out `google/ads-api-report-fetcher` `2419122`: first scan found FA-064 (a
      U+0085 in a minified bundle split ripgrep's JSON stream); every line-delimited JSON
      stream (ripgrep, capture events, proxy events, coverage report, install attestation)
      now splits on `\n` only and a corrupt coverage line is a named failed case.
- [x] **B7** spec-auditor `GATE: FAIL` (2026-09-13) closed: a forced indexer failure now
      carries `+forced-recall-only` in `scanner_version` so it never shares a ProofScope with
      a successful run; a package symbol's namespace version is read from the whole symbol
      and an alias binding that carries a version is never replaced by a versionless one;
      `PreciseSite` has direct tests and a revert check (six in total); byte offsets are
      converted to character columns before symbol lookup; forced status text carries no
      host path; `__all__` complete; P-030 DECIDED, P-033 records the `ObserverContext`
      field, the "behind a flag" deviation and the §10 listing.
- [x] **B8** red-team `RED-TEAM: BROKEN` (2026-09-13) closed: FA-065 (ambient `@types` moved a
      verdict under one ProofScope → external symbols dropped, `typeRoots`/`types` forced
      empty), FA-066 (interface dispatch deleted a true caller → removal only on another
      definition's symbol, per-caller `by symbol` provenance), FA-067 (renamed imports →
      positional lookup); fabricated versions from symbol strings gone with the package
      branch; `ts_dispatch` fixture; the map's precise line now counts rewritten configs and
      dropped external occurrences.
- [x] **B6b** held-out rerun on the final bytes (FA-068): 5,380 candidates, 501 UNKNOWN, 0
      unexplained, identical with and without the indexer; typescript 62 of 63 precise.
- [x] **B9** re-audit on the final bytes `GATE: PASS`; its minors closed: dead precedence
      arm removed, a file whose every occurrence was external no longer counts `precise`, the
      missing-indexer sentence carries no host path, P-030 status line DECIDED, a non-record
      or truncated coverage report line is a named failed case.
- [ ] Recorded, not done: `entry_script` falls back to hashing the launcher when the package
      entry is absent (append a marker or refuse); SCIP `position_encoding` unread;
      `verify/runners.py:242` `except … pass` around the junction fallback;
      `app/migration.py:119` `except Exception` drops a version's diff silently; the second
      console script `hubbleops` in `pyproject.toml` has no proposal.
- [ ] Next real-repo-loop input: the 501 held-out UNKNOWNs (not this tier's), the Apps Script
      bundles the compiler cannot parse (5 of 7 javascript files), and `scip-python` in the
      Linux sandbox image so Python repositories get the same layer.
- [ ] P-034 (vendoring the indexers) awaits the owner; until then precision exists only where
      `scip-typescript` is on PATH, and the map says so.
- [ ] P-035 (three planes; the WORK plane as a producer/judge agent harness; provider-native
      specialists pinned in the existing `repair_tools()` slot; the four-arm release gate) awaits
      the owner. Its `docs/ARCHITECTURE.md` §22 diff is drafted in the proposal and deliberately
      not applied. Three holes it names are open in the tree today whatever the owner decides:
      `verify/`'s import guard is two exhaustive-by-name denylists, so a model client or MCP client
      is importable; no plane is recorded anywhere, so "planes, never mixed" is unfalsifiable; and
      §8.2's repair tool allowlist has no implementation in product code (`hubbleops/repair/` is
      `__init__.py` plus `deterministic.py`) — the live `.claude/hooks/guard.py` rule protects the
      authoring session, not the repair worker, and `docs/HOOKS.md:5-7` is stale in saying the hooks
      are not active.
- [ ] Fresh gate audit on the final bytes; nothing merges before it returns `GATE: PASS`.
- [ ] Owner decisions the audit raised: the store's L10 guard fires only on a status transition
      (a candidate born closed on AI-only evidence is not refused; unreachable while P-009 keeps
      triage disconnected); the guard hook's frozen-schema bypass keyed to a `| **Status** |
      ACCEPTED |` line is self-certifying.
- [ ] Add `vendored_binary_swapped` and `decided_request_site_forged` to the adversarial corpus
      as directories once the corpus harness accepts a decisions file; both behaviours are
      locked by unit and CLI tests today.
- [ ] GLNA closures the Receipt names: builder-skeleton extraction for the first-party GAQL
      builder (32 request sites never reach the oracle); the response-consumer check's 617
      "assembles a name from parts this build cannot resolve" sites; dependency provisioning
      under P-032 so the frozen suite can run.
- [ ] Publish the wheels (PyPI name `hubbleops` or a release asset) so `uvx --from
      "hubbleops==0.1.0"` in the generated Action resolves; approval boundary.
- [x] **Q25-Q28 answered** by the owner's instruction of 2026-09-12 to fix all of PART SEVEN now;
      recorded in [dev/plan.md](plan.md).
- [x] **F1** catalog reconciliation (FA-049): one complete source resolves a field; only two
      present sources that disagree make it unknown. Unresolved v25 fields **1,407 → 140**;
      v23→v24 CHANGED/UNKNOWN **1,238 → 27**; oracle accepts `SELECT customer.id FROM customer`
      and metric/segment queries at CATALOG authority. Lattice `5b31c7c8… → 4555e93f…`.
- [x] **F2** `tests/property/test_contract_diff_invariants.py`: no CHANGED fact across any
      adjacent pair has equal before/after; replacements are REMOVED facts naming a subject
      present in the target.
- [x] **F3** release-notes migration-table projection in `refresh.py` (`Initial state | New
      state | Change type | Implementation guidance`); two genuine subject-level replacements land
      (v24 `Campaign.video_brand_safety_suitability → Customer.…`, v25
      `GenerateCreatorInsightsRequest.search_brand → search_topics`); 22 ambiguous rows are
      counted as `unresolved_replacement_rows`, never guessed. Docs re-normalized offline from the
      retained hash-pinned upstream bytes.
- [x] **F4** `--target` on scan and exposure; MIGRATION FINDINGS (effective version, SDK floor,
      sunset, required changes grouped by finding); AFFECTED grouped by carrier (FA-050).
- [x] **F5** P-024 one observation → one candidate (194 double claims gone); DOCUMENTATION,
      STYLESHEET and lock-file matches resolve by role (Q28); file-level version binding only
      where the structural layer adjudicated the site as code (P-023 kept); qualified
      `ads_failure_detail` shape. `google-listings-and-ads`: UNKNOWN **672 → 358**,
      candidates 1,364 → 1,143, NOT_AFFECTED 110 → 193, unexplained 0 → 0. Baseline re-frozen
      under the new identity with permitted sets intersected, never widened.
- [x] **F6** impact/migrate: affected paths are DETERMINISTIC and HUMAN obligations only
      (474 → 39 on the real repo), findings grouped by change id, carried UNKNOWNs grouped by
      closing instruction (FA-051).
- [x] **F7** `phpunit` runner; every declared runner runs and merges into one SuiteRun; a
      declared-but-missing runner sinks the run (FA-052). PHPUnit without a coverage driver is
      unverified on this machine and fails closed.
- [x] **F8** scan → exposure → impact on `google-listings-and-ads` (`b43b322`) and `dubinc/dub`
      (`b8866f4`): Dub 471 → 326 candidates, UNKNOWN 249 → 103, one deterministic edit at
      `apps/web/lib/integrations/google-ads/api.ts:60`, effective V22 sunsetting 2026-10.
      `hops verify` on either repo still needs their dependencies installed in the sandbox
      (`vendor/`, `node_modules/`); not run here.
- [ ] Decide P-030 (per-language precise indexers) and P-031 (field service as primary source).
- [ ] AI-authored edits and triage stay closed under P-009 until the attestation boundary is
      decided; nothing in this session routed AI output anywhere.
- [x] Bound-reference obligations: an AFFECTED reference whose version comes from its import
      binding or its file's single version literal now carries a deterministic `version:`
      obligation naming the literal's site as the edit site. Real repo: unresolved versions
      29 → 0, deterministic edits 230 → 259. Final full suite **990 passed**.

- [x] Close the coverage failures the `dubinc/dub` real-repo scan exposed (FA-040 … FA-046,
      P-026). Seven classes, each with its own eval under `tests/fixtures/coverage/` and
      `tests/property/test_coverage_invariant.py`; reverting any one fix fails its own eval
      (7/7 checked). The class that mattered: the text and structural channels held two
      hand-maintained copies of one GAQL resource list, so §2's observer-independence assumption
      failed and `FROM conversion_action` produced **no candidate at all**. The resource set is now
      derived from the catalog that already knew it.
- [ ] Decide FA-045's residue volume with the owner. Dub's UNKNOWN count moved 111 → 251, of which
      129 are `CONTRACT_SURFACE_UNRESOLVED`. Every one names a real provider surface and none is a
      guess, but 129 is a triage load, and P-013 grouping is the lever. This is a presentation
      decision, not a soundness one — do not "fix" it by dropping candidates.
- [ ] Reduce the 65 UNKNOWNs that are one token claimed by two surface categories (FA-035). The
      alias half is now accurate (it names the resolved in-repo file); the `package_names` half
      still fires on every `@/lib/integrations/google-ads/...` path segment. Needs the recorded
      decision about claim identity that FA-035 asks for.

- [x] Audit Phase 5 against its DEFINITION OF DONE and fix what it found (PART FIVE of
      [dev/plan.md](plan.md)). Two defects in the `unknown_conservation_pass` conjunct, both fixed:
      **D-001** `decision.apply` compared a decision's run and ProofScope against the candidate run
      instead of the run it was recorded in, so every `hops decide` record was refused and
      `hops verify --decisions` aborted without a Receipt; **D-002** `conserve.compare` decided "new
      evidence" by record id, and `evidence_identity` hashes `run_id`, `proof_scope_hash` and
      `repo_sha`, so every candidate-run record read as new and the conjunct could never fail.
      Added `core.evidence.observation_identity`, eight tests, and the
      `request_hidden_from_oracle` corruption. Evidence: full suite **712 passed, 0 failed** in
      39m55s, adversarial corpus **16 passed**, strict Pyright **0 errors**, Ruff lint and format
      clean. **Q20** (is a relocated UNKNOWN dropped or conserved?) is raised and undecided
- [x] Finish and verify P-021's credential-free Google Ads Mutate path
- [x] Close every finding of the P-021 tree audit, including FA-033: the Receipt and the frozen
      `receipt.json` description both denied that `CATALOG` authority proves any mutate shape while
      P-021 granted exactly that. Fixed at the root under P-022 — `proof/` quotes the accepting
      pack's stated scope instead of asserting one — plus the stale `repair/agent.py` and hosted-App
      entries in `docs/ARCHITECTURE.md` §8.2/§17 and its repo map, the `merge_group` trigger in
      `docs/BUILD_ORDER.md` that the shipped Action already satisfies, the missing `prepare-pr` verb
      and the absent `tests/heldout` in the CLAUDE.md map, and the two misreported counts below.
      Final measured current-tree evidence: full host suite **751 passed, 0 skipped, 0 failed** in
      11m15s; adversarial/property gate **106 passed** (16 adversarial over a 13-corruption corpus,
      90 property); standalone Sentinel **12 passed**; Ruff lint and format clean; strict Pyright
      **0 errors, 0 warnings**. The superseded record claimed 26 for the adversarial/property gate
      and 16 corruptions; neither was reproducible, and both are corrected here
- [x] Turn the ordinary-application loop's 752 UNKNOWNs into a cause-ranked disposition table. The
      run's output was gone, so the scan was reproduced from a fresh read-only clone of
      `woocommerce/google-listings-and-ads` at `b43b322` and matched every number exactly (2,061
      closure entries, 1,315 candidates, 230/1/306/26/752, 0 unexplained) in 2m07s. The 752 group
      into **eight causes on 488 distinct locations**; 704 of them (93.6%) are one defect — the
      recall layer emits a candidate per lexical occurrence with no syntactic role — and only **4
      (0.53%) are genuinely undecidable**. Recorded as FA-034 through FA-038. Group counts reconcile
      to the ledger and to `hops exposure`'s own closing-instruction grouping (4+19+497+24+208=752)
- [x] Freeze the 752 before touching anything: `tests/fixtures/real_repo/glna_unknowns_before.json`
      carries every candidate id, location, claim, winning evidence, closing instruction, root cause
      and **permitted-disposition set**, enforced by `tests/unit/test_real_repo_baseline.py`.
      Evidence: **9 passed**; full unit suite **499 passed** in 3m04s; Ruff lint and format clean;
      strict Pyright **0 errors, 0 warnings**. The baseline's foundation is measured, not assumed —
      two scans of the same commit gave **byte-identical ledgers** (`sha256 ee807fc0…`), identical
      ProofScope and candidate ids, all 752 conserved
- [x] Research the prior art rather than guessing at it (OpenRewrite/Moderne type attribution,
      Sourcegraph precise-vs-search-based and its silent fallback, CodeQL models-as-data, the
      Soundiness Manifesto) and write the root-cause programme as PART SIX of
      [dev/plan.md](plan.md): nine mechanisms M1-M9, the invariant set, mutation testing, held-out
      repositories, and the gate
- [x] Raise **P-023** (the structural layer adjudicates the recall layer — no frozen schema change
      needed), **P-024** (one observation must not become several independent candidates — touches
      candidate identity, the largest blast radius in the programme), and **P-025** (a request claim
      and a response read are different claims)
- [x] Q21-Q24 answered by the owner on 2026-09-11 and recorded in PART SIX and in the decisions
      section of [dev/context.md](context.md)
- [x] Implement **P-023** (M1 comment adjudication + M3 import-table symbol binding + the M4
      handoff): `comment` nodes in the graph, structural adjudication carrying node kind and symbol
      binding as `PROVEN` evidence, `("structure", "text")` precedence. No frozen schema changed.
      Measured on the baseline commit: **752 → 624 UNKNOWN** (19 → AFFECTED, 109 → NOT_AFFECTED),
      1,315 candidates unchanged, unexplained 0, **0 absent, 0 non-permitted, 0 AFFECTED
      downgraded**, four undecidable sites still UNKNOWN, decided share 17.6% → 27.3%. All 109
      NOT_AFFECTED audited by an independently written comment detector: **zero false safes**.
      8 fixture regressions + 6 invariants + 1 strict xfail for P-024. Full suite **774 passed,
      1 xfailed, 0 failed** in 14m09s; Ruff clean over 225 files; strict Pyright **0 errors**
- [ ] Finish P-023's remainder: Q22 string-literal reachability (the enclosing definition must be
      graph-reached before a literal may resolve) and Q21's documentation-drift count on the
      Exposure Map. 271 recall UNKNOWNs and 97 adjudicated-but-unresolved ones are still open
- [ ] Implement M4 wrapper following for the 69 first-party bindings (FA-039). They currently carry
      a closing instruction naming the wrapper; following it to a provider sink, or proving there is
      none, is what closes them. A first-party binding must never resolve to NOT_AFFECTED on the
      strength of the binding alone
- [ ] Implement **P-024** (shape accepted: one candidate, many claims) after the above, and
      re-freeze `tests/fixtures/real_repo/glna_unknowns_before.json` under the new identity with an
      explicit old-id to new-id mapping. The strict `xfail` in the invariants file retires itself
- [ ] Prove transfer on held-out repositories before claiming any mechanism is fixed. GLNA is the
      development repository; overfitting to it is the named risk of the whole programme
- [ ] Run the fresh-session [gate audit](../prompts/cross-cutting/gate-audit.md) for Phase 5 on the
      fixed tree — the builder-side audit above is not the gate, and only a spec-auditor session's
      final `GATE: PASS` counts
- [x] Finish the compressed Tier 0-2 implementation: accepted P-012/P-015/P-018/P-019; added
      source-bound `hops decide`, integrity-checking `hops replay`, full-rescan `hops impact`,
      cumulative `hops guard`, `hops prepare-pr`, Proof Pack PR rendering, repo-resident memory, and
      read-only exact-SHA Actions; ran the adversarial corpus and the ordinary-application real-repo
      loop; preserved every explicitly deferred architecture item
- [x] Verify the finished tree: adversarial/Phase-5 property gate **25 passed** across 12 deliberate
      corruptions; full suite **700 passed, 0 skipped** in 40m19s; standalone Sentinel **12 passed**;
      Ruff lint and format clean; strict Pyright **0 errors**; `git diff --check` clean
- [x] Build the Repository Intelligence Engine's first five pieces on branch
      `phase-06-repository-intelligence`, each with the measurement that justified it (PART THREE of
      [dev/plan.md](plan.md)): `FileRole` classification, one-parse-per-language `query_all`, the
      resolution budget that replaced `MAX_CALL_DEPTH` as a proof boundary, `ResolutionCache`
      compositional summaries, and the Exposure Map's `DISCOVERY COMPLETENESS` section
- [x] Raise **P-015** (candidate states) and **P-016** (one scan, many providers) rather than editing
      frozen surfaces in place, and **P-017** (Exposure Map completeness section) which is accepted
      and implemented
- [x] Accept P-017's additive customer-facing completeness wording under the owner's instruction to
      finish the remaining build completely
- [x] Teach `.claude/hooks/guard.py` to permit a frozen-schema edit only when an ACCEPTED proposal
      names that exact schema path; P-012 and P-015 no longer bypass or deadlock the approval rule
- [x] Retire Q14 as a current milestone blocker: the 20,000-step budget produced **0 exhaustions** on
      the 2,061-entry ordinary application real-repo loop. A larger monorepo measurement remains a
      future trigger, not proof required by the compressed Tier 0-2 milestone
- [x] Answer **Q16**: run the real-repo loop before §11 cross-language edges, so the edges are
      aimed at real patterns rather than guessed ones. Four admissible company-owned repos are
      already verified to exist: `airbytehq/airbyte`, `woocommerce/google-listings-and-ads`,
      `singer-io/tap-google-ads`, plus one more to source. Selected and ran
      `woocommerce/google-listings-and-ads` at `b43b322771071ed88d5a817422dd222acbaa5f33`
- [x] Preserve the explicit deferral of §8 SCC collapse, §11 cross-language edges, §10 type-based
      framework models, §3 compiler/type frontends, and §17/§18 incremental work. PART TWO excludes
      them until their measured triggers fire; no speculative machinery landed

- [x] Plan the compression of Phases 6-10 into three tiers on the owner's instruction, and record it
      as PART TWO of [dev/plan.md](plan.md). Measured what justified it: composed v22->v25 is ~1,577
      ADDED, 6 CHANGED and 174 REMOVED, so the migration is dominated by deterministic work; and
      `repair_class` already enumerates `HUMAN`, so shipping without the repair agent is sanctioned
      by the frozen schema rather than a degradation of it
- [x] Raise and accept **P-014**: `Transform` gains `failure_class`, `precondition`, `apply` and
      `postcondition`, with `TransformInput`/`TransformOutput` in `core/repair.py` following P-011's
      placement. `ToolSpec` stays deferred and the `PROVIDER_TOOL`/`AGENT` classes route to `HUMAN`
- [x] Implement `obligations/engine.py`: obligations keyed by (candidate, that candidate's own
      effective version, target), per-version composed change sets, `DETERMINISTIC` for a
      replacement-bearing change, `HUMAN` for a removal with no announced replacement, and
      `PRESERVE_UNKNOWN` for anything the diff cannot map. Ten tests pass
- [x] Raise and accept **P-013**, and implement it: the Exposure Map's UNKNOWN section groups by
      closing instruction, ranks by sink proximity, expands while a ten-site budget lasts and
      collapses the rest behind `[expand]`. The load-bearing test asserts grouped site counts sum to
      the ungrouped UNKNOWN count, so grouping can never lose a candidate. Found and fixed a real
      defect on the first run: the budget was checked before adding a group, so a 40-site group
      slipped through expanded
- [x] Close the P-012 guard blocker with `_accepted_proposal_names()` and an exact-path regression;
      non-proposed frozen schema writes remain denied
- [x] **Retired by P-020.** The Google Ads test-account MCC and developer token are no longer needed
      by anyone. A complete version catalog now yields `VALID` under `CATALOG` authority, so a
      credential-free read-only engagement reaches `VERIFIED_FOR_SCOPE` with the authority stated on
      the face of the Receipt. Live provider authority stays an opt-in upgrade
- [x] Build offline `Mutate` shape validation against the catalog's `message` and `proto_field`
      facts (P-021). Nested protobuf JSON fields, enums, messages and container cardinality are
      checked recursively; ambiguity and catalog gaps remain UNKNOWN; a configured live transport
      still outranks catalog authority. Focused verifier/contract evidence: **130 passed**
- [x] Record the live-transport limitation: it is exercised only against `_mock`, stays opt-in, and
      no Receipt, prompt or document calls `LIVE` authority provider-proven without a real run
- [x] Wire P-012: `verification_inputs_hash` and
      `oracle_context_hash` as explicit ProofScope fields, `ContractOracle.context_hash()`, and the
      verifier refusing an obligation, decision, capture or oracle result whose declared scope does
      not match the recomputed manifest
- [x] Implement Tier 1's repair half: `repair/deterministic.py` (a generic precondition -> apply ->
      post-check runner that threads text through several obligations on one file, reverts on a
      failed post-check, and turns a throwing transform into `TRANSFORM_FAILED` rather than a crash);
      `packs/google_ads/repairs.py` with four transforms — version literal, REST path, subject
      rename, SDK pin; and `hops migrate` in `app/migration.py`. Proved end to end on the
      `python_pinned_v22` fixture: `version="v22"` -> `version="v25"` and `google-ads==22.1.0` ->
      `google-ads==31.2.0`, the latter read from the catalog's own `client_compatibility` minimum
      rather than guessed. 26 tests
- [x] Fix the defect that first end-to-end run exposed: a dependency pin is AFFECTED but carries no
      `call_version` evidence, so requiring a resolvable effective version turned every SDK-pin
      obligation into `EFFECTIVE_VERSION_UNRESOLVED` and the pin was never bumped. A dependency
      candidate now earns its obligation directly against the target. Regression test added
- [x] Bring `packs/_mock` to transform parity and replace the pack-conformance placeholder that
      asserted `repair_transforms() == []` ("ship in Phase 6") with the falsifier block's real shape:
      non-empty, protocol-conforming, unique names, every `failure_class` set. `repair/` is now
      exercised by two independent pack implementations, so the abstraction has evidence rather than
      a claim. `repair_tools()` stays `[]` on both and the assertion now names why
- [x] Keep `hops migrate` working-tree-only. Automatic candidate commits are deliberately outside
      scope because committing on a user's behalf is hard to reverse and
      `hops verify <base> <candidate>` works on any two SHAs the user makes
- [x] Tier 0 remainder: `hops decide` (the Exposure Map advertises a verb that does not exist) and
      the adversarial-suite artifact rendering FA-019 and its permanent regression
- [x] Tier 2: `proof/pr_body.py`, `proof/guard.py` with `.hubbleops/retired.yml`, the `.hubbleops/`
      writes, the GitHub Action replacing the hosted App, and `hops impact` over a full rescan
- [x] Pick and run the third real-repo-loop target: ordinary application repository
      `woocommerce/google-listings-and-ads`, not API documentation or a client library. The scan
      accounted 2,061 closure entries and 1,315 candidates with zero unexplained candidates

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
- [x] Run the [red-team](../prompts/cross-cutting/red-team.md) against the finished authority. It
      found a **P0** — a split string literal (`"campaigns." + "legacy"`) walked past the
      response-consumer check and a broken migration reached `VERIFIED_FOR_SCOPE` — plus three checks
      that passed by having nothing to look at. All closed, recorded as FA-019 and FA-020, and the
      corruption is kept as a permanent regression
- [x] Start the verifier container for the first time rather than only asserting its policy. A
      Debian-based image's `dash` has no `ulimit -u`, so the mandatory process bound could not be
      applied and the verifier could not start at all. Moved to a distinct Alpine digest, measured
      applying all six rlimits
- [x] Add defence in depth for the response-consumer class: independent candidate-source extinction
      now catches exact, split and multiline removed or renamed subjects in addition to the graph
      consumer analysis, with both defences asserted on the adversarial fixtures
- [x] Run the 2026-09-09 audit-and-repair pass. It closed binary containment, deleted/top-level blast
      radius, lockfile collateral, all-skipped/abnormal frozen-suite, disarmed/malformed check,
      bounded-input, Phase-4 capture-schema, dynamic extinction, nested-shape, language-coverage and
      verifier-marker gaps. Focused Phase 5: 179 passed, 8 Podman skips; full suite: 586 passed,
      40 skips; Ruff, format and strict Pyright clean; all four Law attacks failed closed
- [x] Decide and implement **P-012**. Base/version selection, obligations, decisions, captures and
      live oracle context can change the verdict without moving the frozen ProofScope. Until all are
      bound, Phase 5 cannot make a reusable proof claim; all are now manifest-bound and regression
      tested
- [ ] Rerun a fresh independent [gate audit](../prompts/cross-cutting/gate-audit.md) for Phase 5.
      P-012 and P-020 are both closed, so nothing external blocks it. The audit must also execute the
      eight rootless-Podman runtime isolation tests rather than count their environment skips
- [ ] Repeat the Phase 5 [real-repo loop](../prompts/cross-cutting/real-repo-loop.md) after the fresh
      gate. The pre-gate loop is complete and produced FA-026 plus the 2,061-entry corpus, but only a
      literal `GATE: PASS` permits the merge to `main` and the `v0.5` tag
- [ ] Build the held-out corpus `docs/ARCHITECTURE.md` §13 names: ugly repositories never used during
      development. It has never existed — `tests/heldout/` is absent — so the P-021 tree audit removed
      it from the CLAUDE.md repo map rather than let the map assert a directory that is not there.
      §13 keeps it as the intended category. It cannot be fabricated from repos already used here;
      sourcing it is the work. *Trigger:* the Phase 10 pilot, or the first gate that needs evidence
      the rules generalize beyond the fixtures they were written from
- [x] Closed by P-020. Phase 5 ships no live Google Ads oracle run and no longer needs one: a
      complete catalog decides `Search` requests under `CATALOG` authority with no credentials
      anywhere. The live path remains exercised only against `_mock` and stays opt-in

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
- [x] Merge `phase-04-dynamic-capture-and-sentinel` to `main` and tag `v0.4` locally; repository
      history verifies both `main` and `v0.4` at `1470ce8`. Nothing was pushed

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

- [x] Collapse the 18 per-language ast-grep invocations into one multi-rule pass. Each
      invocation reparses every file, so a four-language repository is parsed 73 times. Parallelism
      was measured and rejected: 1.0-1.1x, because ast-grep already saturates the CPU internally.
      Match-equivalence was proved before the one-parse-per-language implementation landed

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
- [ ] Phase 5 — verification authority *(implemented and audit-hardened; P-012/P-020/P-021 closed; the
      2026-09-11 builder-side DoD audit found and fixed D-001 and D-002, so the fresh gate audit and
      the real-repo loop must run against the fixed tree before merge and `v0.5`)*
- [ ] Phase 6 — obligations + repair
- [ ] Phase 7 — proof pack + PR + guard
- [ ] Phase 8 — incremental system
- [ ] Phase 9 — mock pack conformance
- [ ] Phase 10 — pilot hardening

Each gate is: plan → plan review (fresh) → implement → evidence → gate audit (fresh) → real-repo
loop (from Phase 2).

## Recurring

- [ ] Reseed `tests/corpus/baselines/` from the `baselines` job (`workflow_dispatch` with
      `record_baselines`) whenever a pinned tool version in that job changes; a tool bump moves the
      counts it enforces, and a baseline recorded on another machine is not evidence.
- [ ] Weekly: [spec-drift audit](../prompts/cross-cutting/spec-drift-audit.md)
- [ ] Nightly from Phase 5: [red-team](../prompts/cross-cutting/red-team.md) — now live, since the
      authority it attacks exists

## Blocked / parked

- P-009 remains parked while AI triage is default-off and disconnected.
- P-016 (one scan, many providers) is PROPOSED and undecided.

Nothing is blocked on a credential. P-012 is implemented, P-020 retired the live-oracle requirement,
and P-021 covers catalog-provable Mutate shapes. The red-team rerun is green. What Phases 5, 6 and 7
still need is a fresh gate audit each and separately authorized repository publication.
