# Context

Rolling state of the build. **Updated before every session ends** (a Law in `CLAUDE.md`).
Read by every phase prompt. Keep it short — this is what the next session wakes up knowing.

## Where we are

| | |
|---|---|
| **Current phase** | Compressed Tiers 0-2 are implemented through Phase 7 on top of an unmerged Phase 5. Phase 4 is merged to `main` and tagged `v0.4` |
| **Branch** | `phase-06-audit-fixes`, cut from `phase-06-repository-intelligence` on 2026-09-16 for the week-1 audit fixes; that branch was itself cut from `phase-05-verification-authority` (not from `main`, because the obligation engine and deterministic repair it carries are prerequisites and Phase 5 has not merged) |
| **Last gate passed** | Phase 4, on `e408545`. The post-gate hardening changed closure semantics after that audit, so the `v0.4` merge carries the owner's explicit merge instruction of 2026-09-08 rather than a fresh `GATE: PASS` on the merged bytes. Evidence taken immediately before the merge: `pytest -q` → **464 passed, 1 skipped** on the full tree |
| **Engine baseline** | `engine-v0`, frozen 2026-09-13. Its scope is `dev/engine-v0.json`: lattice `4555e93f…`, ripgrep and ast-grep sha256, `uv.lock` hash, verifier image, Python 3.12.14, and the exact harness commands. Any change that moves a real-repo count is measured `engine-v0` → new before it is believed |
| **Next action** | Tier 3b (PART NINE of `dev/plan.md`) is built on the owner's instruction to decide without asking; P-034 (vendoring the indexers) and P-035 (the three planes and the WORK-plane agent harness) await the owner. Tier 3a (PART EIGHT of `dev/plan.md`) is built on the owner's delegation of Q29-Q31. Remaining before merge: a fresh spec-auditor gate audit, publication of the wheels (an approval boundary, not performed), and the three GLNA gaps below. No merge, tag, publication, or deployment was requested or performed; `main`, the six phase branches and tags `v0.1`-`v0.4` were pushed to `origin` on 2026-09-13 at the owner's instruction, as a backup before the working copy moved. |

**WEEK 1 AUDIT FIXES (2026-09-16), branch `phase-06-audit-fixes`.** An independent audit on
2026-09-14 demonstrated seven fail-closed defects. All seven were reproduced against the tree at
`bd24d9a` before anything was changed, and each fix carries the test that fails without it. Full
suite on a quiet tree: **1359 passed, 1 skipped** in 23:10 (`test_indexers.py:549`, scip-typescript
not on PATH, pre-existing).

What closed: `observation_identity` no longer varies with the enclosing file's blob hash, so a
one-byte edit can no longer manufacture the "new evidence" that closes an UNKNOWN. `migrate` reads
with `newline=""` and stops converting every CRLF in a touched file to LF — which mattered twice
over, because each rewritten line was also a hunk `zero_unexplained_hunks` could never contain.
`prepare-pr` refuses a receipt no finished verify run in the store recorded, checked by re-deriving
the run id from the receipt's own ProofScope and SHA pair and comparing `document.body_hash()`; a
hand-edited receipt previously produced a VERIFIED_FOR_SCOPE Proof Pack at exit 0. `guard` refuses
instead of printing PASS when it loaded no pattern, naming the file it read and whether that file
was absent or empty. The frozen baseline counts the coverage plugin's per-test rows instead of
pytest's terminal prose, so eight collection errors are no longer sixteen failed tests and the
"left no readable report" branch — unreachable for pytest before — now fires with zero counts.
`hops decide` writes the decided candidate through the store, and the transition guard accepts a
close only on an adjudication record whose content hashes to its own id and binds to evidence the
candidate carries at that path, line and blob.

**What it uncovered.** Three things a fix opened or exposed, all closed in the same branch: a row a
human decided was unguarded on every later write, so a write naming no decision could flip it;
`prepare-pr` could write an empty `retired.yml` and install a CI guard against it, failing every
pull request with no remedy once the 4a refusal landed; and the adversarial manifests carried
`expected_verdict` and `reason_contains` fields that no driver asserted. Five further defects are
recorded in `dev/tasks.md` under "Found during week 1" and were not fixed: the same universal-
newline read in `app/verification.py:596`, `splitlines` breaking on Unicode boundaries ripgrep does
not count, `--state-dir` resolving CWD-relative in `verify` and repo-relative in `prepare-pr`, a
re-scan overwriting a decided candidate row, and `guard --install` exiting FAILED on a repository
with no retired surface yet.

**The one item that is only partially fixed, and why — P-038.** The brief specified
`NOT_APPLICABLE` as "the change sets contain no subject of that falsifier's `failure_class`". That
predicate is not computable in `verify/`: nothing on `ChangeSet` or `SubjectChange` carries a
failure class, and mapping a subject to one is pack knowledge L5 forbids the generic layer to hold.
What shipped is the conservative rule the generic layer can compute — `NOT_APPLICABLE` only when
the Change Pack changes no subject and no version — so on `google_ads` seven of eight falsifiers
are `NOT_RUN`, every `NOT_RUN` is unresolved, and `verdict.decide` turns any unresolved entry into
UNKNOWN. **No capture-less `hops verify` on `google_ads` can currently reach VERIFIED_FOR_SCOPE.**
It was kept rather than reverted because reverting restores the audited hole and
`Cost(FALSE_VERIFIED) ≫ Cost(UNKNOWN)`. P-038 proposes the one protocol member that fixes it. The
owner rules.

**FIRST-SIGNAL CORPUS built (2026-09-14). Ten independent families pinned; the STOP condition did
not fire.** The brief asked for ten independent Google Ads repository families and said to stop
rather than fill the denominator. Eight discovery modalities over public GitHub produced 364 raw
candidates and **259 unique repositories**; 48 were cloned and verified deterministically; **33 met
the fixed eligibility definition**; four (`dubinc/dub`, `woocommerce/google-listings-and-ads`,
`singer-io/tap-google-ads`, `google/ads-api-report-fetcher`) are barred as the engine's own
development set, leaving **29 available**. Ten were selected from twenty-nine, not scraped together
to reach ten. `dev/corpus/families.json` pins them with SHA, evidence lines, install command,
licence and last commit: php 3 (v19, v23, v24), python 4 (v20, v21, v22, v24), typescript 3
(v20, v23, v24) — every source version v19…v24 represented.

Two things make the count defensible. Version detection runs a second, **provider-neutral** pass
beside the pack's own eleven carriers, because a carrier-only scan misses `API_VERSION = "v24"` —
the pack declares `GOOGLE_ADS_API_VERSION` and `ADS_API_VERSION` but not the bare `API_VERSION`
that `singer-io/tap-google-ads` actually uses, and that repository reported "no marker" until the
neutral pass was added. And independence is decided by **content, not GitHub's fork flag**, with the
detector calibrated on a known fork cluster before it was trusted: it grouped `singer-io` with
`biron-bi`, `health-union` and `peliqan-io` at name-Jaccard 0.851–1.000, separately grouped
`Matatika` with `hotgluexyz` and `ticketswap` at containment 1.000, never linked the two lineages,
and left `vibeus/tap-google-ads` unlinked. Across the ten pinned families no pair reaches
name-Jaccard above 0.000.

**Written before any run and not edited during it:** `dev/corpus/definitions.json` (the "handled"
definition, the four disjoint outcomes, the five attribution stages, the metric definitions) and
`dev/corpus/matching-rules.json` (four match rules M1–M4, the mechanism-compatibility table, the
counting laws, the arm-A claim-type mapping). The harness is `tests/corpus/`, run as
`uv run python -m tests.corpus <verb>`. It is **not** `hops corpus`: adding a verb to the engine CLI
would move `engine_tree` off the `d78cc87c…` that `dev/engine-v0.json` pins, which is the hash arm A
checks before it agrees to run. **P-037** records the finding and the three-line change if the owner
wants the literal command. Arm A refuses to run unless engine tree, pack tree, `uv.lock` and both
tool binaries hash-match the frozen scope; `check` prints `SCOPE OK` on this machine.

**Environment, recorded before the run rather than discovered after it:** this machine has node,
npm, pnpm, yarn, python, uv and docker, and has **no php, composer, poetry, pipenv or make**. Every
PHP family therefore fails its documented install and records `SETUP_FAILED` attributed to the
environment. `hops scan` is SCAN-plane and needs no repository dependency, so the harness runs the
scan anyway and discovery and classification are still measured on those families — the outcome is
honest and the recall is not thrown away.

**The harness was audited by a separate agent, as `CLAUDE.md` requires, and it found nineteen
defects — most of them real and several of them moving the headline numbers.** The audit checked the
code against the two fixed spec files rather than against intent, and every finding it reported was
reproduced before being accepted. What it caught, and what changed:

- **`false_verification` never consulted `repaired`.** It was computed as "a label went unmatched",
  not the fixed definition's "a COMPLETED outcome that leaves an actionable labelled site
  unrepaired". This is the one metric carrying the Clopper-Pearson bound, and the error ran in the
  flattering direction: found-but-unrepaired was invisible. Now both halves count.
- **A finding could claim only one label**, so one correct finding spanning two labelled lines
  turned the second into a miss and manufactured a stage attribution. The fixed rule constrains
  *labels* ("a label is matched at most once"), not findings.
- **Obligations were never read.** `arm_finding_extraction.A.actionable_set` is "candidates with
  status AFFECTED, plus every obligation the exposure map raises", and only the ledger was parsed.
  Re-deriving from the persisted `obligations.json` moved the three PHP families from 306/161/466
  findings to 372/224/565, roughly doubling arm A's actionable set — a gap that had been
  **penalising arm A**.
- **The five attribution stages were not tested in the stated order**, so counts that belong in
  `discovery` were landing in `verification`.
- **An UNKNOWN sitting on an actionable label was charged as both a miss and a classification
  failure**, against the fixed rule's `unknown_is_not_a_miss`. Such a label is now conserved out of
  the denominator and reported in its own column, which is what "forcing it either way would score
  the engine for a law it is required to obey" means.
- **`HUMAN_ACCEPTED_RISK` candidates were dropped** before any bucket, making a site the arm had
  seen and a human had ruled on look like a site the arm never saw.

**Three corrections of my own work, all now rules in `CLAUDE.md`.** (1) The harness was about to run
`pip install -e .` and `pip install -r requirements.txt` against the host interpreter; the run was
stopped before it reached those two families and installs now happen in a per-family `uv venv` with
`PIP_REQUIRE_VIRTUALENV=1`. The system Python was verified clean afterwards. (2) Failure attribution
treated any finding anywhere in a file as having seen every site in it; the fixed definition says
"at that site". (3) **S2 was charging arm A for its own blind spots, twice over.** It first scored
versionless surface lines as false positives; then, once those were UNSETTLED, it scored *comments
and docstrings* as actionable — the four "misses" that produced a Python recall of 0.000 were
`# v24 dropped impressions/click_through_rate`, `"""Since Ads API v24 the forecast takes no
per-keyword bids"""` and two more of exactly that shape. Arm A was right not to flag them. S2 now
separates code from prose: a version token in code is ACTIONABLE, one in a comment or docstring is
CLEAN, one in a README or CHANGELOG is UNSETTLED because whether a migration must update
documentation is the owner's call and not S2's, and **every other line in an adjudicated file is
UNSETTLED** — because S2 rules on version tokens and nothing else, whatever reason an arm gives for
flagging a line. That last change is what stopped GAQL obligations (`SELECT campaign.id FROM
campaign`) from being counted as arm A errors: they are legitimate findings S2 simply cannot judge.

**The first arm A run was discarded: three of its ten outcomes were measuring the harness, not the
engine.** The run completed all ten families and reported 0 COMPLETED, 5 SETUP_FAILED and 2
ENGINE_FAILED. Reading the per-stage detail rather than the outcome showed that most of it was mine:

- **The per-family venv was created inside the repository under test.** That makes the tree differ
  from HEAD, and `hops migrate` correctly fail-closes: "the working tree differs from HEAD, so
  obligations derived from it would bind to a ProofScope no commit reproduces". Six families hit it.
  The engine's refusal is right; the harness was wrong to hand it a dirty tree. The venv now lives
  beside the repository, never in it.
- **Isolating the per-family install took three attempts, and the first two each cost a run.** A
  Windows `;`-separated PATH was handed to a `bash -lc` process, which broke it outright and produced
  `python: command not found` on two families whose install commands were fine. Rebuilding the PATH
  POSIX-style did not fix it either, because `bash -lc` is a login shell and rebuilds PATH from the
  profile, discarding anything the parent sets. What works is exporting inside the command
  (`export PATH="/c/…/venv/Scripts:$PATH"; <install>`), which was checked against a live shell before
  the third run rather than after it. That check also caught the next failure before it cost
  anything: `uv venv` does not seed pip, so `python -m pip install -e .` would have failed with
  "No module named pip" on a third family. The venv is created with `--seed`, and python, pip, node,
  npm, yarn and uv were all confirmed to resolve inside the isolated environment before the run
  started.
- **A 900-second per-install cap that no definition contains was deciding an outcome.**
  `klosk/adloop`'s `uv sync --all-extras` was killed at 900.1s and recorded SETUP_FAILED at the
  harness's cap rather than at the definition's 30-minute budget. The install now gets the whole
  remaining budget, and every bound the harness imposes that the definitions do not is declared in
  the scorecard's `harness_parameters` block.
- **Two install commands in the pin were simply wrong.** `npm ci` was pinned for
  `Opteo/google-ads-api`, which ships `yarn.lock` and no `package-lock.json`, so npm refused before
  doing anything; and for `google-marketing-solutions/giga`, whose committed lockfile is out of sync
  with its own `package.json` upstream ("Missing: typescript@5.9.3 from lock file"). `families.json`
  is version 2: same repositories, same SHAs, two install commands corrected with the reason
  recorded. The giga lockfile drift is a real property of that repository and is kept, not hidden.

One genuine engine-facing finding survived the cleanup and is the reason the harness now commits the
post-install state: **the tree the documented install produces is not a tree the engine will
migrate.** `npm install` rewrites `package-lock.json`, so the "fresh clone plus documented install"
state the definition names is dirty by construction, and `hops migrate` refuses it. The harness now
commits that state and uses it as the base SHA, which is what a real user's tree looks like anyway;
the underlying gap is recorded rather than fixed, because the engine is frozen.

**Label sources: all three built, and only one of them runs everywhere.** S2 mines each repository's own history for a commit that
replaced one Google Ads version with another and adjudicates only the files such a commit touched,
detecting sites with the provider-neutral pattern so a site the pack cannot see still counts against
the arm. S3 seeds one mutation per declared version carrier at a fixed seed; a permanent test proves
every one of the twelve seeded templates is matched by the pack's own carrier regex, because a
seeded label the pack cannot match would be scored as a miss the engine never had a chance to make.
**S1 is built for Python.** It installs two SDKs per family — a release carrying the family's source
version and `google-ads==31.2.0` for v25 — introspects which `google.ads.googleads.vNN` packages each
release actually carries rather than trusting a version table, and type-checks the family's
first-party Python twice with the same checker and settings. A diagnostic present under v25 and
absent under the source version is an actionable site. Per file it records checked, unresolved
imports, suppressions present and whether the Google Ads namespace resolved; a file failing any of
those is UNADJUDICATED by S1, never clean. **S1 does not run on PHP** (no php, no composer on this
machine) **or on TypeScript** (no `google-ads-api` major carries v25 yet), and both are recorded as
coverage gaps rather than as clean regions.

**S3 now plants a removed-field GAQL query per family, grounded outside the engine.** The pack's own
change data may not be a label source — it is the thing under test — so the removed field is found by
diffing the protos of Google's published client library between the source release and 31.2.0, and
the mutation is planted only when that diff proves the field is present in the source version and
absent in v25. If no such field is found, nothing is planted and the omission is recorded. This is
the only source that can judge a GAQL finding at all, which is where the migration risk actually
sits and where 143 arm A findings previously sat unadjudicated.

**Arm C could not be run as specified and was not faked.** It needs a Google Ads developer token and
OAuth refresh token in `~/google-ads.yaml` for the `google-ads-a2a-service` MCP sidecar; that file
is absent and no `GOOGLE_ADS_*` variable exists. On the owner's instruction the plugin was installed
and a credential-free arm runs as **`C-offline`, never as `C`**, with the degradation carried on
every number. Installing it found two defects in `googleads/google-ads-api-developer-assistant`
v4.0.0 on Windows: `install.ps1`'s Python probe passes `print(f"...")` through PowerShell 5.1, which
strips the inner quotes so the probe always raises `SyntaxError` and the script reports "Python 3.10
or higher is required" against a compliant 3.11.9; and it calls
`claude plugin marketplace add $ProjectDirAbs`, which the CLI rejects because an absolute Windows
path is not `owner/repo`, `https://…` or `./path`. Both were worked around by hand; the plugin then
loads headless and exposes all ten slash commands.

**ENGINE v0 — PART ELEVEN integrated and frozen (2026-09-13), tags `engine-v0-pre` … `engine-v0`.**
The four PART ELEVEN mechanisms were wired into their consumers and the result tagged. `engine-v0-pre`
is `e5e1237`, the tree before any of it. Eleven commits, one per mechanism. The seams that were
missing: `observe/text.py` now takes `closure.search_exclusion_globs()` and passes the globs to
ripgrep unmodified; `verify/suites.py` records `runners.execution_failure(...)` as the
EXECUTION_FAILED reason; `app/exposure.py` consumes `migration.Composition` and passes
`uncomposable` into `ObligationInputs`; and `detected_versions` draws only from claims that name a
version the provider runs, so a `google-ads==30.1.0` pin never reaches version composition — the
distinction is the claim type (`dependency_state`, `sdk_installed`), never a version-string pattern.

Measured on three real repositories, same SHAs, same machine, `engine-v0-pre` → `engine-v0`
(candidates · AFFECTED · NOT_AFFECTED · UNKNOWN · unexplained):

| Repository | before | after |
|---|---|---|
| `dubinc/dub` `b8866f4` | 327 · 4 · 73 · 32 · 0 | 327 · 4 · 90 · 15 · 0 |
| `woocommerce/google-listings-and-ads` `b43b322` | 1143 · 259 · 218 · 333 · 0 | 1143 · 259 · 457 · 94 · 0 |
| `singer-io/tap-google-ads` `5b6a201` | 647 · 37 · 120 · 479 · 0 | 647 · 38 · 507 · 91 · 0 |

Every baseline AFFECTED candidate id is still AFFECTED on all three (0 of 4, 0 of 259, 0 of 37
lost), `test_real_repo_conservation` is green with both frozen checkouts present, and unexplained
stays 0. Candidate identity is unchanged on Dub and GLNA. On tap-google-ads four ids changed and
all four are accounted for: `setup.py` is now a manifest, so `google-ads` at lines 5, 16 and 32
moves from `surface_reference` to `package_reference` — same site, same subject, new claim — and
the `dependency_state` `NO_MANIFEST` UNKNOWN is answered by the `sdk_installed` AFFECTED it was
asking for (`google-ads 30.1.0`, below the v25 python minimum 31.2.0). That one AFFECTED is the
whole 37 → 38. The PART ELEVEN forecast (tap-google-ads ≈ 42, GLNA ≈ 17, Dub ≈ 13) was optimistic:
the real residue is roughly twice that on two of the three, and it is honest residue — `segments.device`
changes at v20, so every anchor naming it stays UNKNOWN with the boundary in its closing instruction.

Three consecutive scans of tap-google-ads export byte-identical ledgers
(`4563c613c534fa29…`); the only textual difference between the three summaries is the
`--state-dir` path passed on the command line.

**End-to-end on a real company repository (2026-09-13) — FA-069 … FA-075.** On the owner's
instruction, `singer-io/tap-google-ads` `5b6a201` (Stitch; Python; `API_VERSION = "v24"`) was
taken through scan → exposure → migrate → local commit → verify → prepare-pr, with its
dependencies in a scratch venv via `HOPS_VERIFY_PYTHON`; no PR was raised. Two fail-closed
stops before any judgement: a background `pip install .` wrote `build/` into the tree after the
closure had walked it, so ripgrep matched a path the closure had never enumerated (FA-069 — a
race against a moving tree, not a closure defect; the closure does enumerate generated
directories), and the verifier's worktree `.git` pointer file carries the checkout path
`tap-google-ads`, which matches the surface (FA-070; worked around by renaming the directory).
Then: 647 candidates · 37 AFFECTED · 479 UNKNOWN (74%) · 0 unexplained; migrate made the one
correct edit and mis-reported six satisfied obligations as human work; verify returned
**FAILED** in 26 s with one true reason (nine `version="v9"` spike scripts, caught only by the
falsifier because no obligation exists for a version below the lattice floor, FA-074) and three
product defects: the `absent:v24` audit counts a CHANGELOG line (FA-071), the consumer check
flags `customer_id`, `campaign`, `update` … as removed-field reads (FA-072, P-027), and the
frozen suite runs the whole `tests/` tree instead of CI's `tests/unittests`, exiting 2 at
collection (FA-073; run directly the unit suite is 69 passed / 4 failed on base, date-dependent).
prepare-pr refused the FAILED receipt. Conclusion recorded in FA-075: the chain runs to an honest
Receipt in under a minute, and no real repository can reach VERIFIED_FOR_SCOPE until FA-071 …
FA-074 close. Nothing outside `dev/` and `docs/FAILURE_ATLAS.md` changed; the checkout lives at
`.hubbleops/artifacts/e2e/tapga` on local branch `hubbleops-v25`.

**Tier 3c planned and stopped at the P-009 boundary (2026-09-13) — PART TEN of `dev/plan.md`.**
The attestation record for AI artefacts is designed as fields inside Evidence `value` under
`derivation: DERIVED_AI_EVIDENCE` (model id, prompt hash, input hashes, output hash, judge kind
and outcome), with no schema change and six mechanical enforcements (E1–E6) the store and the
import tests carry. No AI call path exists; Q32 asks the owner whether one may (and with which
client) before C4 is written. The four uses each have a named deterministic judge; the sunset
calendar, `hops impact --release` with a daily read-only Action, and the log-line proxy mode of
the sentinel plus `hops promote --from-sentinel` are designed and need no answer. Ground truth
re-measured on the new tree with a fresh state directory (the default `.hubbleops/hubbleops.sqlite`
is store schema v1 and this build refuses it): Dub 327 · 4 · 73 · 32 · 0 and GLNA 1143 · 259 ·
218 · 333 · 0, identical to the Tier 3b baseline. The ground-truth checkouts were absent from
`C:\dev\HubbleOps` after the move and were re-cloned at their pinned SHAs under
`.hubbleops/artifacts/phase7-real-repos/`. Nothing outside `dev/` changed; the uncommitted
prepare-pr ProofScope fix from the gate blocker (1) is still in the tree, untouched.

**Phase 6 gate audit ran on `0aa558b` and returned `GATE: FAIL` (2026-09-13).** PR #1
(`phase-06-repository-intelligence` → `main`, 45 commits) is green and `mergeable_state: clean`,
and was NOT merged: CI proves the tests pass, not that the tree conforms. The blocker that matters
is that a Receipt survives a new SHA — `prepare-pr` never compares the ProofScope it computes to
the tree, so one forged string publishes a `VERIFIED_FOR_SCOPE` PR body for an unverified tree.
The five blockers are in [dev/tasks.md](tasks.md). Nothing was merged, tagged or waived.

**Working copy moved off OneDrive (2026-09-13).** The tree lives at `C:\dev\HubbleOps`; the
OneDrive copy is left untouched as a fallback. Content is identical — same tree hash
`592ba5b`, `git diff` empty; the only differences were CRLF-vs-LF working-tree bytes and two
empty directories git cannot track. Use `uv sync --all-packages`, not `uv sync`: the plain
form leaves `hubbleops-sentinel` uninstalled. OneDrive was worth leaving — every file was a
Files-On-Demand placeholder, and SQLite WAL sidecars plus `.git` internals are exactly what a
sync client corrupts.

**Tier 3b built (2026-09-13) — precise indexers behind the graph, spike first (PART NINE of
`dev/plan.md`).** Five indexers were run without installing any repository's dependencies and
the numbers decided P-030: `scip-typescript` 0.4.0 indexes `google-listings-and-ads` (891
documents, 74,928 occurrences, 7 s) and Dub (4,340 documents, 575,731 occurrences, 41 s) from a
staged copy with a derived tsconfig; `scip-python` 0.6.6 does not start on Windows and needs a
12 GB heap for the generated `google-ads-python` client (8,532 documents, 1,906,775 occurrences,
1,733 s in WSL); `scip-php` needs PHP and composer (neither exists here); `scip-java` needs a full
Gradle build that could not even download its distribution; `scip-dotnet` indexes
`google-ads-dotnet` (5,852 documents, 5,548,842 occurrences, 448 s) only after restoring NuGet
itself. GO: TypeScript, TSX, JavaScript, Python (Linux). NO-GO, recall-only with the enabling
sentence on the map: PHP, Java, .NET. What landed: `core/precise.py` (dependency-free SCIP
reader and `SymbolIndex`), `graph/indexers.py` (`ScipTypescript`, `ScipPython`, staged copies,
entry-script identity, version-mismatch refusal), symbol-bound callers, barrel and alias imports
resolved to their definition, identifier bindings by symbol, `precise` per language and the
indexer status sentence on the map, and `;scip-typescript=<version>+sha256:<entry>` in
`scanner_version`. Ground truth on the final bytes, indexer on PATH: GLNA 1,143 candidates ·
259 AFFECTED · 218 NOT_AFFECTED · 333 UNKNOWN · 0 unexplained, javascript precise 873 of 891,
php recall-only; Dub 327 · 4 · 73 · 32 · 0, typescript precise 2,346 of 2,348, tsx 1,991 of
1,991; candidate ids and statuses identical to the pre-change baseline on both repositories,
three consecutive Dub scans byte-identical. Six revert checks fail their tests. The held-out
transfer (`google/ads-api-report-fetcher` `2419122`) found FA-064 on its first scan: a U+0085 in
a minified bundle split ripgrep's JSON stream and the text observer died with a traceback; every
line-delimited JSON stream now splits on `\n` only. The spec audit returned `GATE: FAIL` (a
forced indexer failure shared its ProofScope with a success; a versionless symbol could replace
a versioned alias binding; no direct test of identifier adjudication) and the red team returned
`RED-TEAM: BROKEN` (host-ambient `@types` moved a verdict under one ProofScope, FA-065;
interface dispatch deleted a true caller and closed an UNKNOWN by deleting evidence, FA-066;
renamed imports never bound, FA-067). All of it is closed with fixtures and tests: external
symbols are dropped so the index is a function of the tree and the pinned indexer, a caller is
removed only when its symbol belongs to another definition in the graph, provenance is per
caller, the forced path carries `+forced-recall-only`, and the package branch of adjudication
is gone. Nothing was vendored (P-034 records the cost); no commit, push, or publication was
requested or performed.

**Tier 3a built (2026-09-13).** The customer path is real: `uv build --wheel` under
`HUBBLEOPS_WHEEL_PLATFORM` produces platform wheels vendoring ripgrep 14.1.1 and ast-grep 0.45.0
with pinned sha256 (`toolchain.json`, `hatch_build.py`, `core/toolchain.py`); the scan binds
`rg=…+sha256:` and `ast-grep=…+sha256:` into `scanner_version`, so a swapped binary is a new
ProofScope and a hash mismatch stops the scan (`TOOLING_MISSING`, never a PATH fallback).
`tests/integration/test_ship_path.py` proves wheel → isolated `uv tool install` → `hops scan` →
`hops exposure` with `rg` and `ast-grep` stripped from PATH in 35 s on win_amd64. Timed cold
path on the ground truth with the same stripped PATH and the win_amd64 wheel: Dub `git clone` →
map **183 s** (scan 171 s), `google-listings-and-ads` **126 s** (scan 104 s); wheel build 6.5 s
from a warm cache, `uv tool install` 3.3 s. `hops exposure
--install-workflow` writes a `pull_request` Action with `contents: read` only. Every resolved
request skeleton is judged during the scan against every lattice version with the offline
`pack.contract` (the map never builds a transport from the environment any more) and the map's
QUERIES block shows one verdict per request site against the target; `hops scan --target` prints
it. Dub's residue: UNKNOWN **103 → 32** on twelve named mechanisms (FA-054 … FA-060), three
consecutive scans byte-identical, 0 unexplained, 0 violations of the re-frozen Dub baseline
(`tests/fixtures/real_repo/dub_unknowns_before.json`: 3 substring identities retired, 6
credential observations re-keyed from config to identifier claims, permitted sets carried, never
widened). GLNA: UNKNOWN 358 → 333, all 259 AFFECTED preserved; four Merchant API sinks frozen
RUNTIME_ONLY were relabelled FA-058 in the fixture with the reason recorded, and one baseline id
(`AdsMissingEuDeclarationQuery.php:31`) was already absent before this session.

The GLNA chain on the final bytes: `hops migrate` discharges **258 of 258** namespace sites
(case-preserving `V23 → V25`; bound references edit the import line that carries their version;
an obligation whose required state an earlier edit already wrote is SATISFIED, not human work) and
leaves one for a human, the `dev-legacy-v32.1.0` composer pin, which the pin transform now refuses
to rewrite into a release that does not exist. `hops verify --from v23 --to v25` twice → `VERDICT
FAILED` with the identical `receipt_body_hash 12912e9af172451dbb078ebf8147c0016cccb651eb2be1055e9b3ff53d7ae04d`,
and `hops prepare-pr` refused the FAILED receipt, as §17 requires. The failure is real and
the Receipt names it: the pin supports no target; removed enum subjects (`ACQUISITION`,
`CYO_INCENTIVE`) are still read in unit tests and e2e mocks; two `v20` per-call sites outside the
v23 scope survive (`tests/e2e/utils/mock-requests.js:919`, a proxy mock JSON). **Not every
conjunct is decided on this repository**, exactly as Q29/Q30 predicted: the frozen suite is
`TOOLING_MISSING` (jest and phpunit are declared and not installed; PHPUnit's bootstrap also needs
the WordPress test library and a database), the oracle reached none of the 32 request sites
(first-party GAQL builder, Merchant API client, JS metric anchors), and the response-consumer
check has 617 sites that assemble a name from parts this build cannot resolve. P-032 records
where dependency provisioning belongs; builder-skeleton extraction and a resolvable
name-assembly check are the next two closures on this repository.

Evidence on the final bytes: full suite **1085 passed, 0 failed** (`uv run pytest -q`, 17m23s,
including the two real-repo conservation scans); strict Pyright **0 errors, 0 warnings** over `hubbleops`
and `tests`; Ruff lint and format clean over 206 files; `hops pack verify google_ads` passes;
revert check **8 of 8** new mechanisms fail their own tests when reverted, every file restored
byte-for-byte.

**The first gate audit returned `GATE: FAIL` on three blockers, all closed the same night.**
(1) `hops prepare-pr` accepted a Receipt bound to another SHA: `proof/memory.prepare` now refuses
when the repository HEAD is not the receipt's `candidate_sha` ("a new SHA kills the old proof").
(2) The Receipt's verdict was trusted as written: `proof/pr_body.eligible` now requires
`verdict_holds`, which re-derives eligibility from the recorded conjuncts (every check section
passed with nothing unresolved, the frozen suite executed and COMPLETED with passing tests, oracle
authority CATALOG or LIVE with no INVALID result, falsifiers PASS or SKIPPED, zero unexplained
hunks, no reasons, and UNKNOWN_BLAST empty exactly when the verdict is VERIFIED_FOR_SCOPE); the
end-to-end chain test proves both refusals. (3) One GLNA baseline id was a pre-existing,
unrecorded re-key (the anchor subject became the resource subject under P-026): it is now
recorded under `identity_migration.rekeyed_identities`, and
`tests/integration/test_real_repo_conservation.py` scans both pinned checkouts whenever they are
present and asserts zero conservation violations, so a vanished candidate on the ground truth
fails the suite on this machine. The audit's remaining notes: the host-execution deviation from
§9's "own image" is recorded in P-032; the two W7 corruptions are locked by unit and CLI tests
rather than corpus directories; the L10 store guard fires only on a transition (unreachable from
the pipeline while AI triage is disconnected under P-009) and the guard hook's proposal-status
bypass is self-certifying, both left open for the owner.

**The re-audit closed all three and found the §17/§18 seam red by construction**: the tree
`hops prepare-pr` leaves behind failed its own Backslide Guard, twice over. The PR body was
written at the repository root and names the old version, so the guard flagged it; the body is
run output and now lands under `.hubbleops/artifacts/`. And a removed field name still written
as test data (a dict-literal key the audit deliberately treats as data, not a read) was retired
anyway, so the rg-only guard fired on the very tree the Receipt verified; `prepare-pr` now defers
any pattern the verified tree still contains textually (`NOT RETIRED … still written at N sites`),
retires only what the tree is clean of, and the chain test asserts `hops guard` passes on the
prepared tree. The same seam exposed a mock-pack gap (a `def __init__(…, version="v1")` default
was never a carrier, so the machine migration left it behind while verify said VERIFIED); the
mock surface gained `client_version_default`. The guard also resolves ripgrep through the vendored
toolchain now, so the generated guard Action runs on a runner with nothing but uv. P-033 records
the §6.4 (no hop cap) and §11 (no NetworkX, no coverage.py) deviations the auditor found
unwritten. A third audit has not been run; nothing merges until one returns `GATE: PASS`.

Three things a later session must not undo: the resolver judges skeletons against the lattice and
the map judges against the target, both offline; a generic sink is explained only when no provider
link, direct or imported, exists anywhere on its chain; the frozen baselines carry re-keys and
relabels as recorded entries, never as widened permitted sets.



**Tier 3a plan (2026-09-12) — PART EIGHT of `dev/plan.md`.** Ground truth re-measured on the
working tree: Dub 326 candidates / 103 UNKNOWN in 1m39s, `google-listings-and-ads` 1,143 / 358
in 1m34s, both 0 unexplained. Six chain gaps were found by running the verbs rather than reading
them: `hops migrate --dry-run` claims **no transform on either repository** (the version-literal
transform is case-sensitive, so `Google\Ads\GoogleAds\V23` never matches `v23`, and Dub's edit
site is the template line rather than the `constants.ts:20` literal the walk already found);
the frozen suite runs on the host with `bounded_process`, never inside the verifier image; the
map validates through `pack.verification_contract()`, which would reach the network with
credentials in the environment; `.tsx` files are counted structurally supported and never parsed
(`-l typescript` is forced; `-l tsx` finds 26 imports where typescript finds 0); 32 GLNA request
sites (first-party GAQL builder, Merchant API client, JS metric anchors) never reach the oracle;
and this machine has no PHP, so the frozen PHPUnit suite is `TOOLING_MISSING`. Dub's 103
UNKNOWNs are classified into twelve mechanisms with every site listed; eight are closable with
evidence (expected 103 → 28, or 41 if generic HTTP sinks must stay UNKNOWN), and three classes
stay UNKNOWN by design (string slugs, a v22 test fixture, unbound contract surfaces). Nothing
was implemented; no file outside `dev/` changed.

**Diagnosis of the "1 issue, hundreds of UNKNOWNs" complaint (2026-09-12) — PART SEVEN of
`dev/plan.md`.** The owner asked why the product reports one issue and hundreds of UNKNOWNs
where an AI reviewer reports four findings. Measured on a fresh scan of
`google-listings-and-ads` (1,364 candidates, 249 AFFECTED, 672 UNKNOWN) and on `hops impact`:
the root is pack data, not the engine. `_reconcile_field` marks **1,407 of 2,995 v25 fields**
(every metric, every segment, `customer.id`) `UNKNOWN_PROVIDER_CONTRACT` because the proto
projection is missing for them, so the oracle refuses `SELECT customer.id FROM customer`, 1,086
of 1,238 v23→v24 "changed" facts are byte-identical, and `impact` tells the user to "resolve
what replaces `metrics.clicks` in v25". Second, the scan never reads the target contract, so
the Exposure Map cannot show a contract-level finding at all; its AFFECTED unit is a
version-carrying line (229 of 249 are `use ...V23` statements). Third, `verify` is pytest-only,
so no PHP/JS repository can reach a Receipt. Programme F1-F8 and questions Q25-Q28 are in
PART SEVEN.

**All of F1-F8 landed the same day, on the owner's instruction.** Evidence on the final bytes:
full suite **990 passed, 0 failed** in 14m33s; strict Pyright **0 errors** over `hubbleops` and
`tests`; Ruff lint and format clean over 200 files. Pack lattice is now
`4555e93f3377db7e59de8d46f85c6a02c85b652f2a16df3309d8cc1945c4edca` (was `5b31c7c8…`); every
earlier proof re-scopes, which is correct. Catalog: unresolved v25 fields 1,407 → 140, adjacent
CHANGED/UNKNOWN 1,238 → 27, two genuine subject replacements now carried, 22 ambiguous
release-notes rows counted and never guessed; the oracle accepts `SELECT customer.id FROM
customer` at CATALOG authority. `google-listings-and-ads` at `b43b322`: candidates 1,364 → 1,143,
UNKNOWN 672 → 358, NOT_AFFECTED 110 → 193, unexplained 0 → 0; the map now reads "V23 · 229 sites ·
37 files", names the SDK floor (33.6.0, `floor unknown` for `dev-legacy-v32.1.0`) and sunsets;
impact reports 259 deterministic edits, 0 human, 0 unresolved versions, 39 affected paths (was
474). `dubinc/dub` at `b8866f4`: candidates 471 → 326, UNKNOWN 249 → 103, effective V22
sunsetting 2026-10, one deterministic edit. Atlas rows FA-049 … FA-053; proposals P-030 and
P-031 raised. Five things a later session must not undo are listed under PROGRESS in PART SEVEN.
Not done: `hops verify` on either real repository (their dependencies are not installed in the
sandbox); PHPUnit without a coverage driver is unverified on this machine and fails closed.
Nothing was committed.

**Coverage closure (2026-09-12) — FA-040 … FA-048, P-026.** A real-repo scan of `dubinc/dub` at
`b8866f4` found observable Google Ads surface disappearing with **no candidate at all**, which
contradicts §2's own promise that structural gaps only move work into UNKNOWN. Seven classes fixed
architecturally, two more found on held-out repositories. Evidence on this tree: full host suite
**820 passed, 1 xfailed, 0 failed** in 11m52s; strict Pyright **0 errors, 0 warnings**; Ruff check
and format clean; revert-check **7 of 7** reverted fixes fail their own eval; three consecutive
scans of one fixture byte-identical. Dub: 333 → 471 candidates, UNKNOWN 111 → 249,
EXCLUDED_WITH_EVIDENCE 0 → 3, UNSCANNED 10 → 7, UNEXPLAINED **0 → 0**, scan 59s → 2m21s. No commit,
merge, tag, push, or deployment was requested or performed.

**The Phase 5 DoD audit (2026-09-11) — D-001 and D-002.** An audit of the shipped Phase 5 against
its own DEFINITION OF DONE found two defects, both in `unknown_conservation_pass`, both fixed. The
full account is PART FIVE of `dev/plan.md`.

`decision.apply` checked a decision's `run_id` and `proof_scope_hash` against the ledger it was
applying to — the *candidate* run — while `read_decisions` had already validated it against the
*base* run it was recorded in. Base and candidate are never the same run, so every decision
`hops decide` writes was refused and `hops verify --decisions` aborted without producing a Receipt.
The recorded-human-decision half of L3 had never worked. The deciding identity is now an explicit
parameter of `apply`; the blob-drift guard is what carries a decision across SHAs and still refuses
one whose source moved.

Worse: `conserve.compare` decided "new evidence" by comparing evidence record ids, and
`evidence_identity` hashes `run_id`, `proof_scope_hash` and `repo_sha` alongside the observation.
Those three always differ between base and candidate, so every candidate-run record counted as new
and the violation branch could not fire for any candidate carrying evidence. One of the nine verdict
conjuncts was always true in production. `core.evidence.observation_identity` — the record minus
`id`, `run_id`, `proof_scope_hash`, `repo_sha` — now decides it, so "new evidence" means a new
observation; `source_hash` and `value` stay in the key, so a changed file is genuinely new evidence.

Both defects passed their unit tests because those tests built base and candidate ledgers under one
synthetic run id, a state production never reaches. **A cross-run rule needs a cross-run fixture.**
Three things a later session must not undo: "new evidence" stays scoped to the evidence attached to
the closing candidate, because the looser reading restores the vacuity the fix removed; the three
new decision-channel integration tests are the only end-to-end proof that L3's decision half works;
and Q20 (is a relocated UNKNOWN dropped or conserved?) is open and deliberately unchanged.

**The credential boundary (2026-09-11) — P-020.** The owner ruled that HubbleOps engages a customer
repository read-only and asks for no provider credentials. The audit that forced it: the generated
Action required the customer to place five Google Ads OAuth secrets in their own repository, and
those credentials carry read *and* write authority because Google issues no validate-only
credential — only our own source setting `validate_only = True` constrained them. Meanwhile eight of
the nine conjuncts of the verdict rule never needed a network at all.

`_validate_fields` now returns a positive `VALID` attributed to `CATALOG` authority when the target
catalog's `field_inventory` asserts completeness and every field resolves; a configured live
transport still outranks it and returns `LIVE`. `ValidationResult`, `OracleOutcome` and `OracleCheck`
carry the authority, and `verify/oracle.py` refuses an acceptance that names none — an unstated
strength is not proof. The Receipt gained the required top-level `oracle_authority`, so no Receipt
can stay silent about the strength of its own oracle evidence, and both `receipt.md` and the PR body
state it. Answers to Q17/Q18/Q19 are in PART FOUR of `dev/plan.md`.

Three things a later session must not undo. Catalog authority proves only that a named field exists
in a complete inventory: it proves nothing about selectability, filterability, segmentation,
resource pairing or date ranges, and the reason string says so on every result. P-021 gives
`GoogleAdsService.Mutate` a catalog-authority path only when every supplied protobuf JSON field and
container shape recursively resolves through `message` and `proto_field` facts. It does not claim
required-field, oneof, resource, permission or business-rule semantics; ambiguity stays UNKNOWN.
The live transport is still exercised only against `_mock`: nothing may describe `LIVE` authority
as provider-proven until a real run exists.

**Where that scope is allowed to live (2026-09-11) — P-022, FA-033.** The two paragraphs above are
the *pack's* statement, and the pack is the only place allowed to make it. P-020 had also written a
fixed version of it into `proof/receipt.py` and into the `receipt.json` `oracle_authority`
description, both ending "or any mutate shape". P-021 then made a validated mutate shape a CATALOG
acceptance, so the Receipt and the frozen schema began denying what the same build proved, and no
test could notice because none pinned the sentence. `proof/` now renders the distinct stated
`reason` of every accepted CATALOG check — sorted, deduplicated, one `scope:` line each — and a
CATALOG acceptance whose checks state no scope renders as proving nothing rather than falling
silent. A later session must not reintroduce a generic scope sentence: the boundary test
`test_no_generic_surface_states_what_a_providers_catalog_authority_covers` fails any generic surface
that names a provider capability, and it is the reason this class of drift cannot recur.

**Completion sweep (2026-09-11).** P-012 now binds every verdict-affecting input and non-secret
oracle context into ProofScope; P-015/P-018 preserve provider reference data, unsupported files,
unscanned files and human-accepted risk through ledgers, obligations and Receipts; SQLite schema v2
refuses an old constraint instead of rewriting provenance. `hops decide`, `replay`, `impact`, `guard`
and `prepare-pr` now exist. Phase 7 writes the complete PR body, cumulative retired-surface guard,
repo-resident memory, and read-only exact-SHA pull-request/merge-queue Actions. P-019 keeps generated
credential bindings provider-owned while generic Proof code sees only validated inert names.

The ordinary-application real-repo loop ran on `woocommerce/google-listings-and-ads` at
`b43b322771071ed88d5a817422dd222acbaa5f33`: 2,061 closure entries, 1,315 candidates, 230 affected,
1 not affected, 306 unsupported, 26 unscanned, 752 unknown, zero unexplained and zero resolution-
budget exhaustions. The first detached worktree exposed Windows long-path failure FA-026; scoped
`core.longpaths=true` on every transcripted Git invocation closed it. The corrected PHP hook smoke
ran in the pinned rootless image with network `none`, a read-only repository, exit 0 and an installed
hook; because the benign workload emitted no provider request, the ledger correctly retained
`UNKNOWN_DYNAMIC` instead of claiming absence.

**What those 752 UNKNOWNs actually are (2026-09-11).** The loop's output was gone, so the scan was
reproduced from a fresh read-only clone at the same pin and matched every number exactly — 2,061
closure entries, 1,315 candidates, 230 AFFECTED, 1 NOT_AFFECTED, 306 UNSUPPORTED, 26 UNSCANNED, 752
UNKNOWN, 0 unexplained — on HubbleOps `66e5b75` plus the uncommitted worktree. **Scans are
reproducible across clones; only the run id moved, because it hashes the absolute target path.**

Grouped by winning evidence record, the 752 are **eight causes, not 752 sites**, and they sit on only
**488 distinct source locations**. Two causes are 704 of them (93.6%), and both are the same defect:
the text observer emits one candidate per *lexical occurrence* of a surface identifier and records no
syntactic role, so an import, a docblock, a type hint, a stylesheet selector, a help-centre URL and a
UI slug each raise a candidate indistinguishable from a call. Only **4 candidates (0.53%) are
genuinely undecidable** — `UNRESOLVED_SYMBOL` on a function parameter, which needs dynamic capture.
The five patterns are FA-034 through FA-038.

Four findings a later session should not have to rediscover. The application's own domain vocabulary
*is* the provider's name, so recall matches scale with how much the product talks about the provider
rather than with how much it calls it. One token (`google-ads`) sits in both `identifiers` and
`package_names`, so 194 (path, line, subject) triples raise two candidates each — 25.8% of all
UNKNOWNs is one observation counted twice. Sixteen sites are one join short of AFFECTED: the
construction site and its versioned import are separate candidates nothing binds, and a further 110
bind to a *first-party* import, which is positive evidence the token is not a provider symbol.
And the repository is uniformly on **V23** across all 229 `call_version` records, so none of these
UNKNOWNs is version ambiguity — the pack simply cannot map the lock's `dev-legacy-v32.1.0` to an API
version, which is FA-009 again.

The highest-leverage group is the 496 `surface_reference` recalls: 416 of them are in
structurally-supported languages, so recording the containing node kind as evidence is enough to
dispose of them. **Closing that one group alone moves the decided share from 17.6% to 55.3%.** The
closer must stay evidence-based — a name-based exclusion list would hide real usage, which is the
failure the laws exist to prevent.

**The root-cause programme (2026-09-11) — PART SIX of `dev/plan.md`.** The owner directed that these
be fixed as *mechanisms*, not symptoms: extract every generic failure mechanism, fix it once, encode
it as evidence plus regression plus invariant, then prove it transfers to repositories never used to
develop it. The optimisation is stated explicitly — maximise resolved candidates subject to
`false_safe = 0`, `unexplained = 0`, `candidate_loss = 0`. **752 → 4 is the target; 752 → 0 would be
evidence of a defect.**

The 752 are frozen before any change in `tests/fixtures/real_repo/glna_unknowns_before.json`: every
candidate id, location, claim, winning evidence, shipped closing instruction, root cause, and the
**set of dispositions it is permitted to reach**. `tests/unit/test_real_repo_baseline.py` (9 tests)
enforces it, including the two failures that matter — a candidate that vanishes, and a
`RUNTIME_ONLY` site forced closed. Root-cause totals: FA-034 497, FA-035 208, FA-009 24, FA-038 19,
RUNTIME_ONLY 4.

The baseline rests on a measured fact: **two scans of the same commit produced byte-identical
ledgers** (`sha256 ee807fc0…`), identical ProofScope, identical candidate ids, and all 752 conserved.
Candidate identity carries no run id, so conservation is checkable rather than aspirational.

Nine mechanisms are named in PART SIX (M1 recall matches carry no syntactic role; M2 one observation
becomes two candidates; M3 missing symbol binding; M4 first-party wrappers shadowing provider names;
M5 non-code carriers; M6 SDK release to API version; M7 request and response conflated; M8 embedded
language parse failure; M9 the irreducible four). **P-023, P-024 and P-025 are raised and OPEN.**
P-023 needs no frozen-schema change at all — `claim_type` is an open string, `value` is "any JSON
value shaped by claim_type", `observer` already contains `structure` and `confidence` already
contains `PROVEN` — so the structural layer can adjudicate the recall layer through the resolver's
existing per-claim precedence. P-024 is the large one: it touches candidate identity and therefore
every stored id, and it must land *after* P-023 so its effect is measured against an adjudicated
corpus.

The prior art was researched rather than assumed, and two points are load-bearing. OpenRewrite's
type attribution is the right idea but needs the full compilation classpath, which a read-only
customer engagement does not have — so HubbleOps takes import-table symbol binding instead, which
resolves the measured cases without a build. Sourcegraph's documented failure mode is that precise
navigation **silently falls back to search-based** when the index is missing; HubbleOps must never
acquire that behaviour, and P-023 is written so that a file the structure observer did not parse
produces no adjudication and the text claim survives as UNKNOWN. Absence of adjudication is never a
disposition.

**Q21-Q24 were answered by the owner on 2026-09-11.** Q21: a token proved by parse to sit inside a
comment resolves `NOT_AFFECTED_WITH_EVIDENCE`, with a separate documentation-drift count so stale
docs stay visible without being a safety claim. Q22: a string literal resolves only when its
enclosing definition is itself reached by the graph and no path to a sink exists, so an incomplete
graph reads as uncertainty rather than safety. Q23: one candidate per observation carrying multiple
claims. Q24: three held-out repositories, at least one not PHP.

**P-023 is ACCEPTED and implemented (M1 comments + M3 symbol binding + M4 handoff).** The graph
gained `comment` nodes (verified present in all four supported languages); `observe/structure.py`
adjudicates every recall match in a file it parsed, emitting `PROVEN` evidence carrying the
containing node kind and the symbol binding; `CLAIM_PRECEDENCE["surface_reference"]` is now
`("structure", "text")`, so the adjudication outranks the recall through the machinery the resolver
already had. **No frozen schema changed.**

Measured on the frozen baseline's own commit: **752 UNKNOWN → 624**, with 19 resolved to AFFECTED
and 109 to NOT_AFFECTED_WITH_EVIDENCE. Candidates unchanged at 1,315, unexplained 0, **0 absent, 0
in a non-permitted status, 0 previously-AFFECTED downgraded**, and the four statically undecidable
sites still UNKNOWN. All 109 NOT_AFFECTED sites were audited by an independent comment-detection
method written separately from the product code: **zero false safes**. Decided share 17.6% → 27.3%.
Full suite **774 passed, 1 xfailed, 0 failed** in 14m09s; Ruff lint and format clean over 225 files;
strict Pyright **0 errors, 0 warnings**.

Four things a later session must not undo. A file the structural layer did not parse produces no
adjudication and the recall UNKNOWN survives — **absence of adjudication is never a disposition**,
which is precisely the Sourcegraph silent-fallback failure. A line carrying the subject in both code
and a comment is not adjudicated at all, because the per-occurrence verdicts disagree and the
unresolved reading is the safe one. A token that is merely a *substring* of a bound alias stays
UNKNOWN, because substring-of-alias reasoning is unsound — `tests/fixtures/phase1/vendored_sdk` pins
that asymmetry, and its one expectation change (UNKNOWN → AFFECTED on a genuine V22 provider import
line) was verified by reading the source before it was recorded. And a first-party binding is never
a safety verdict: 69 sites now carry a closing instruction naming the wrapper to follow (FA-039).

Still open under the accepted proposal: Q22's string-literal reachability, Q21's documentation-drift
count, and P-024, whose shape is accepted but which must land after P-023 and requires re-freezing
the baseline under the new identity. The P-024 invariant is already a **strict xfail** in
`tests/property/test_adjudication_invariants.py`, so it fails the suite the day the behaviour
changes and cannot be forgotten. **No held-out repository has been run yet, so no claim of transfer
is made.**

**Repository Intelligence Engine (2026-09-09).** The owner directed a re-architecture: one
provider-neutral repository model, with provider packs mapping external API contracts onto it, so
provider logic never reimplements discovery, traversal, indexing, propagation, caching or
completeness accounting. The full plan and the measurements behind it are PART THREE of
`dev/plan.md`. Two new invariants govern it: **no optimization may silently reduce candidate
coverage**, and **no hop limit determines safety**.

What forced it, measured rather than assumed: a scan of *this repository's own tree* did not finish
in 25 minutes, because 196 MB of provider catalogs carrying 287,240 surface matches were classified
`INSIDE` and fed to source-code AST analysis. Five things landed, each with its own evidence.

**Roles.** `FileRole` on every closure entry. Bulk roles (`DATA`, `SNAPSHOT`) are enumerated and
counted but never AST-analysed; the text observer emits one `bulk_data_reference` per bulk file and
the resolver returns `EXCLUDED_WITH_EVIDENCE` naming the role. Self-scan **>25 min → 52 s**,
candidates 9,437 → 1,858, unexplained 0 throughout. Size alone was not enough — the sub-256 KB
`proto_v*.json` blobs needed a density collapse past 100 records per file, which turned 7,926 JSON
UNKNOWNs into 38 accounted candidates. **The role is a disposition input, never a filter**: the file
is always enumerated, its matches always counted, and the closing instruction says how a data file
that first-party code actually loads becomes a call site instead.

**One parse per language.** `AstGrep.query_all` runs a single multi-rule `scan`, demultiplexing by
`ruleId`, in place of 18–20 `run -p` invocations. Proven identical before switching: python
60,210 = 60,210, php 263 = 263. Six patterns the rule engine rejects in `scan` mode are held back by
an explicit partition, and a test asserts that partition is exactly right so an ast-grep upgrade
fails loudly instead of silently degrading. Honest result: 3.22× at 200 files, 1.35× at 800, ~0.76×
on few-large-file trees, neutral end to end. **§2 is not the big lever; roles were.**

**The hop limit is gone as a proof boundary.** `MAX_CALL_DEPTH = 5` is replaced by
`ResolutionBudget`; exhausting it yields `RESOLUTION_BUDGET_EXHAUSTED`, never `NOT_AFFECTED`. The
`seen` frozenset already guaranteed termination, so the depth cap was only ever cost control. The
6-hop `depth_6` fixture now resolves. Testing this found a real FA-020-class defect: an exhausted
value was dropped from the record set entirely, and when kept was labelled
`CONTRACT_VALIDATION_DEFERRED` — an exhausted walk reading as a *validated request*. Both fixed.

**Compositional summaries.** `ResolutionCache` keyed by `(path, offset, text, owner)`. It stores hop
*suffixes*, so a reused summary carries the calling wrapper chain rather than the cached one — that
is what keeps the audit trail honest under reuse. It never stores a path-dependent terminal
(`AMBIGUOUS_CYCLE`, `RESOLUTION_BUDGET`), which is what keeps it sound. **175 s → 55 s and 28 → 0
budget exhaustions, with results identical to the uncapped run.** Budget sensitivity disappeared
entirely: 0 exhaustions at 20k, 200k and 2M. This is the change that made the fixed point affordable.

**Discovery completeness (P-017).** The Exposure Map gained a section stating what the *search*
proved: corpus accounting, analysis reach, resolution frontier, role census, and an explicit
`DISCOVERY_COMPLETE` / `DISCOVERY_INCOMPLETE` verdict naming each unmet reason. It earned its place
on the first run by surfacing 28 budget exhaustions that were otherwise invisible. The verdict is
about the search, never the repository's safety: `DISCOVERY_COMPLETE` alongside 400 preserved
UNKNOWNs is a legitimate result, because L2 makes a preserved UNKNOWN a correct outcome.

**P-015 and P-018 are implemented.** Candidate and Receipt accounting now distinguish
`PROVIDER_REFERENCE_DATA`, `UNSUPPORTED`, `UNSCANNED`, and `HUMAN_ACCEPTED_RISK`; `FIXED` and
`VERIFIED` remain deliberately excluded so a scan cannot assert a verification-time fact. P-016
(one scan, many providers — one corpus and index, N ledgers and N ProofScopes, no composite scope)
remains a future optimization outside the compressed milestone.

**Compression of Phases 6-10 (2026-09-09).** The repository owner directed a compression to a
pilot-ready product; the tier plan is PART TWO of `dev/plan.md`. What justified it, measured rather
than assumed: the composed v22->v25 contract diff is ~1,577 ADDED, 6 CHANGED and 174 REMOVED, so the
migration is dominated by version literals, generated namespaces, REST paths and the SDK pin, all
deterministic; `repair_class` in the frozen `obligation.json` already enumerates `HUMAN`, so shipping
without the LLM repair agent is sanctioned by the schema rather than a degradation of it; and
`hops verify` takes two SHAs and an obligations file, so it never cared who wrote the candidate.
Cut: `repair/agent.py` and the sandboxed repair loop, the hosted GitHub App (a committed Action in
the customer's own runner satisfies every §17 clause and removes an enterprise security review), all
of Phase 8's machinery (`hops impact` ships over a full rescan), and all of Phase 9. Every cut defers
cost, never proof. **A compressed scope is not a compressed gate** — each tier still needs a fresh
audit, the red-team and a real-repo loop.

Landed on this branch under that plan: **P-014** (`Transform` gains `failure_class`, `precondition`,
`apply`, `postcondition`, with `TransformInput`/`TransformOutput` in `core/repair.py` following
P-011's placement so `repair/` never imports `packs/`); the **obligation engine**
(`obligations/engine.py`, keyed by candidate and that candidate's *own* effective version against one
target, `DETERMINISTIC` only where the diff names a replacement, `HUMAN` for a removal with none,
`PRESERVE_UNKNOWN` for anything that does not compose); and **P-013** (the Exposure Map's UNKNOWN
section groups by closing instruction, ranks by sink proximity, and collapses past a ten-site
budget). Two things a later session must not undo: the grouping test asserts grouped site counts sum
to the ungrouped UNKNOWN count, because a rendering that can lose a candidate is an L1 violation with
a delay; and the group-expansion budget is checked *including* the candidate group, not before it —
the first implementation checked before, so a 40-site group slipped through fully expanded.

Tier 1's repair half also landed. `repair/deterministic.py` is a generic runner — precondition,
apply, post-check — that threads text through several obligations touching one file, reverts on a
failed post-check without writing, and converts a throwing transform into `TRANSFORM_FAILED` instead
of a crash. `packs/google_ads/repairs.py` holds four transforms: version literal, REST path, subject
rename and SDK pin. `hops migrate` (`app/migration.py`) scans, builds obligations, runs the
transforms, writes the changed files and emits the obligations JSON that `hops verify --obligations`
already consumes. It prints, and returns, no verdict.

Proved end to end on `tests/fixtures/phase1/python_pinned_v22`, copied out of the repository:
`version="v22"` became `version="v25"`, and `google-ads==22.1.0` became `google-ads==31.2.0` — the
latter read from the catalog's own `client_compatibility.minimum_versions` for v25, never guessed.
Three things a later session must not undo. The SDK pin's minimums are keyed by *target version*
rather than captured at construction, so `repair_transforms()` keeps the frozen no-argument
`ProviderPack` signature and a non-default `--target` still gets the right floor. A transform whose
`precondition` returns false is not a failure: the obligation stays open and the runner reports
`NO_TRANSFORM`, which is how a language with no documented minimum (JavaScript has none) correctly
declines instead of inventing a pin. And `hops migrate` writes the working tree but does not commit,
because committing on a user's behalf is hard to reverse and `hops verify` works on any two SHAs.

`packs/_mock` gained a transform of its own at the same time, because the pack conformance test
asserted `repair_transforms() == []` with the message "repair transforms and tools ship in Phase 6" —
a placeholder asserting the feature's *absence*. Replacing it with the falsifier block's shape
(non-empty, protocol-conforming, unique names, every `failure_class` set) meant `_mock` had to reach
parity, which is the better outcome anyway: `repair/deterministic.py` is now exercised by two
independent pack implementations rather than one, so the abstraction has evidence rather than a
claim. `repair_tools()` stays `[]` on both, and the assertion now says why — `ToolSpec` is deferred
until a `PROVIDER_TOOL` class ships. Note the frozen-dataclass trap: `Transform` declares writable
attributes, so a member annotated `Transform` cannot be a `frozen=True` dataclass; `MockRemovedField`
had already solved this with plain `@dataclass(slots=True)`.

The first end-to-end run exposed a defect worth remembering: a dependency pin is AFFECTED but carries
`sdk_installed` evidence, not `call_version`, so requiring a resolvable effective version turned
every SDK-pin obligation into `EFFECTIVE_VERSION_UNRESOLVED` and the pin was silently never bumped.
A dependency candidate now earns its obligation directly against the target. **The general shape:
"what version is this site on" is not one question — a call site answers it from a literal, a
dependency answers it from a compatibility table, and code that assumes the first silently drops the
second.**

**P-012 is implemented.** ProofScope carries explicit `verification_inputs_hash` and
`oracle_context_hash` fields rather than overloading `build_config_hash`. The canonical input
manifest binds base and candidate SHAs, version pair, obligations, decisions, captures and frozen
suite identity; every source-bound input is validated before use. The oracle context binds the
implementation, transport and non-secret authority context, and a context change during validation
fails closed. `.claude/hooks/guard.py` now allows a frozen schema write only when an ACCEPTED
proposal names that exact path, so the proposal route works without weakening the default denial.

Phase 5 (2026-09-08): `hops verify <base> <candidate> --pack <name>` ships. `verify/` holds the six
checks and the pure verdict; `proof/receipt.py` writes `receipt.json` and `receipt.md` in the §16
layout; `sandbox/verifier_image.py` stops being a stub.

Five things a later phase must not undo.

- **The frozen suite is the base's, staged over candidate source.** `verify/suites.py` copies the
  candidate tree to a scratch workspace and then copies the base SHA's test directories over it. A
  candidate that weakens or deletes its own assertions still faces the base's. Candidate tests run
  separately and are recorded as evidence; they never touch the verdict.
- **Coverage is execution-derived.** A stdlib `sys.monitoring` line tracer, injected as a mounted
  pytest plugin, emits one JSON line per test naming the files that test actually executed. No new
  dependency and it works with `network=none`. There is no `test_foo.py → foo.py` heuristic anywhere
  in the phase, and a language the tracer cannot follow is `COVERAGE_UNSUPPORTED`, which puts its
  modules in `UNKNOWN_BLAST` rather than assuming them covered.
- **The diff comes from `git`, inside `verify/`.** `verify/gitdiff.py` shells out itself and
  cross-checks `--unified=0` against `--numstat`, so no caller can substitute a change manifest. A
  hunk under `hubbleops/verify/`, `hubbleops/proof/` or the state directory is never COLLATERAL.
- **An obligation locates itself by `current_state`.** Evidence ids are run-scoped, so resolving an
  obligation's site only through them fails across runs. Containment reads the `file:line` the frozen
  `obligation.json` already says `current_state` carries, and uses evidence ids when they resolve.
- **The rescan's run id is the commit, not the worktree.** It used to be the temporary checkout path,
  so two verifications of the same two commits produced different evidence ids and different
  receipts. `scan_repository` now takes `run_target`, and verification passes `commit:<sha>`.

Determinism is asserted, not assumed: `receipt_body_hash` is the content id of the receipt with
every timestamp field stripped, and two runs of one scope must agree on it.

**The Phase 5 red-team found a P0 and it is closed.** A response reader that keeps a removed field
by writing `"campaigns." + "legacy"` defeated the consumer check, which only ever compared whole
atoms. Every other conjunct was honestly clean, so a migration that raises `KeyError` against a real
response reached `VERIFIED_FOR_SCOPE`. Recorded as **FA-019**; the check now reads the graph's
`concatenation` and `format` nodes, reassembles a name whose parts are all literals, and emits a
named UNKNOWN when an assembly mixes in something it cannot resolve. The corruption is kept as a
permanent regression at `tests/adversarial/split_literal_response_read`.

The same run found three checks that passed by having nothing to look at (**FA-020**): zero requests
reaching the oracle while the Change Pack changes subjects, a falsifier set entirely skipped for want
of its failure classes, and a base UNKNOWN whose candidate id simply vanished. Each now reports
itself unresolved, capping the verdict at UNKNOWN. **The general lesson, which later phases inherit:
a conjunct of the frozen verdict rule that cannot fail is a conjunct that is not there.** When adding
a check, add the case where it has no input, and make that case unresolved rather than passing.

Starting the verifier container for the first time also found the image wrong: a Debian-based Python
has `dash` as `/bin/sh`, whose `ulimit` has no `-u`, so the mandatory process bound could not be
applied and every verifier run refused to start. The capture images are Alpine; the verifier is now a
distinct Alpine digest, measured applying all six rlimits. Do not move it to a Debian base.

The response-consumer class now has two independent defences. The graph consumer analysis catches
literal, getter, concatenated and formatted reads; the candidate audit independently scans every
non-test source for removed and renamed subjects with a trie matcher that tolerates split literals
without matching identifier superstrings. Both defences are asserted against the exact and split
corruptions.

Phase 5 gate hardening (2026-09-09) closed eleven additional false-proof paths. Binary diffs now
materialize containment hunks; top-level edits and deleted base definitions enter the blast radius;
lockfiles are collateral only to dependency obligations; a frozen suite with no passing test and an
abnormal pytest exit are unresolved; empty, skipped, throwing or malformed falsifiers and oracles
cannot pass silently; every verification input is bounded and shape-validated; capture input is
validated against Phase 4's frozen event schema and its `request_text` is reconstructed for the
oracle; dynamic versions and removed subjects reach audit and pack falsifiers; request shapes include
the API version and recursively traverse objects inside arrays; and source-language coverage uses
the graph's complete suffix map. Focused Phase 5 result: **179 passed, 8 skipped**. Full result:
**586 passed, 40 skipped**. Ruff, format, and strict Pyright are clean.

The same audit found one frozen-boundary defect and therefore ended **GATE: FAIL**. The candidate
tree alone determines the current verification ProofScope; changing the base, selected versions,
obligations, decisions, captures, or live oracle authority can change the verdict without changing
the scope hash. P-012 is OPEN with the required input-manifest and oracle-context design. A bare
capture is used as explicit verification input but is not promoted into source-bound ledger evidence.
At that point no Google Ads credentials were present and the pre-P-020 prompt still required a live
oracle run. P-020 later retired that requirement in favor of explicitly labelled catalog authority.

Phase 4 merge (2026-09-08): merged to `main` as a `--no-ff` commit and tagged `v0.4`, on the
repository owner's instruction. Two things a later session must not misread. First, the merged bytes
did not carry their own fresh `GATE: PASS` — `e408545` did, and eight hardening commits landed after
it; the full suite was green at the merge and that is the evidence the merge rests on. Second,
**P-010 is ACCEPTED** as of that merge: environment and run-output directories are accounted as one
`UNSCANNED` closure entry each rather than enumerated, so `tree_hash` no longer moves when a
virtualenv is rebuilt or a second scan writes artifacts. Do not re-litigate it.

Scale and reliability hardening (2026-09-08): profiling the scan a second time found three defects
the first pass missed, and the run log added the day before caught a fourth on its first real run.

- **FA-016, the worst of them.** Every source path was passed on one command line, so past roughly
  500-800 files the operating system refused to start ast-grep — and the failure was reported as
  `TOOLING_MISSING` naming a tool that was installed and working. A customer with a normal-sized
  repository could not scan it and was told to install something they already had. Paths are now
  batched under a 24,000 character budget against the 32,767 character Windows `CreateProcess`
  limit; measured 800 files failing before and 4,000 passing after.
- **FA-017**, caught by the new structured log on the first real scan: closure pruning left ripgrep
  walking `.hubbleops/uv-cache/`, it matched a path the closure no longer enumerated, and the scan
  correctly stopped. Two hand-maintained exclusion lists for one invariant.
  `SourceClosure.search_exclusions()` is now the single source both read.
- **FA-018.** Wrapper-walk lookups scanned every match in the repository and filtered by path;
  `SourceRange.contains` ran 1.5 M times per build. The graph now indexes by path and id. Build
  5.64 s → 3.39 s on 60 files, and because the complexity class changed the saving grows with
  repository size.

Also: analyzer output and match counts are bounded, so a pathological rule fails closed with a named
reason instead of exhausting memory; `tests/property/test_cost_budgets.py` asserts deterministic
counters — batch counts, invocation counts, entries enumerated — which catch a cost regression on any
machine without timing anything; `tests/property/test_adversarial_trees.py` generates hostile trees
with Hypothesis and asserts the closure is total, deterministic, and never enumerates what it pruned;
and `.github/workflows/ci.yml` runs the checks, inert until the repository is first pushed.

Two things measured and deliberately **not** done. Running the 18 ast-grep queries concurrently was
1.0-1.1x, because ast-grep already saturates the CPU internally, so the change was reverted rather
than kept as dead complexity. Collapsing those 18 invocations into one multi-rule pass is the real
remaining win — a four-language repository is parsed 73 times — and it stays open because it needs a
match-equivalence proof first.

One caveat on the numbers: `hops scan . --pack google_ads` on this repository is pathological, not
customer-representative. It produced 289,391 text records because the Google Ads pack's own catalog
data is in the tree, so HubbleOps was scanning its own surface definition.

Post-Phase-4 hardening (2026-09-07): eight measured defects repaired on the Phase 4 branch, none of
them a design change. The scan was profiled rather than guessed at, and the profile overturned the
first diagnosis: `source_closure.build` was 80% of a scan because it opened, read and SHA-256'd
every file under `.venv` and every file of HubbleOps's own `.hubbleops/` run output before
classifying them as excluded — 14,369 entries walked where 1,586 are real. Both directory classes
are now pruned at the walk and accounted as one `UNSCANNED` entry each, so UNEXPLAINED stays 0 and a
rebuilt virtualenv no longer moves the ProofScope. Same-interpreter A/B: 17.49 s → 2.16 s, 8.1x.
Raised as **P-010 (OPEN)** because `tree_hash` semantics change; invalidation is automatic via
`scanner_fingerprint`. `node_modules`, `vendor`, `third_party` and `site-packages` are deliberately
still hashed per file, on FA-015 grounds.

Seven smaller repairs: `core/runlog.py` supplies the structured run log §12 has always required and
the code never had, keyed by run_id/component/duration/outcome, silent unless `HOPS_LOG` is set;
the three observers now run concurrently with results consumed in submission order, so the ledger
stays byte-identical; `sandbox/` has one public surface and `app/capture.py` no longer reaches into
seven of its nine modules; `test_imports.py` now proves zero import cycles and that cross-layer
imports address a layer surface, with a tolerated deep-import list that fails when it goes stale;
`jsonschema` is deferred behind its cached constructors, taking CLI import from 739 ms to ~400 ms;
`core.records.parse_json` is one total bounded parser and the untrusted workload paths use it — the
six ad-hoc `(JSONDecodeError, UnicodeDecodeError)` sites all missed `RecursionError`; and the store
sets `busy_timeout`. Suite: **452 passed, 1 skipped** (was 428), Ruff, format over 112 files, and
strict Pyright all clean. The Phase 4 `GATE: PASS` was taken on `e408545`, before these bytes, so a
fresh gate audit is required again before merge.

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

The post-loop builder audit closed three further gaps before the final independent gate. The proxy
now applies all six mandatory rlimits through a container-only launcher, verifies the live values
from an in-container attestation, and caps its Podman log. Python, Node, PHP, and sentinel hook stacks
all retain an explicit truncation frame, and sentinel import preserves that observation with a named
`STACK_TRUNCATED` issue. Sentinel ingestion now compares the producer sidecar's adapter, schema, and
corpus hashes—not only local pack bytes—to the pack-owned compatibility contract, checks the declared
limits, reads inputs with hard bounds, and retains bounded copies of both inputs on failure. The
real-runtime sandbox and capture suites pass all 31 probes after these changes.

The first completed independent post-loop audit returned `GATE: FAIL` on five concrete gaps. A
production evidence row could carry the placeholder path `.` and bypass a current-source hash check;
promotion now requires a repository stack frame whose captured path and source hash match the live
file. The proxy test allowlisted a public name but never proved a reachable private TLS exception;
the integration fixture now runs a separate self-signed HTTPS service on the isolated egress network,
binds its actual container, network, address, hostname, and port into the execution manifest, and
permits only that exact tuple. The standalone hook recognized repository frames only below literal
`/workspace`; it now derives paths from an explicit confined repository root. Dynamic and sentinel
observations now use `OBSERVED_NOT_STATIC` only when no static candidate maps. Finally, Git, engine,
and proxy invocations join workload invocations in bounded JSONL transcripts with exact argv,
duration, exit, outcome, and hashed/truncated stdout and stderr. The repaired tree passes 428 tests
with one future-phase skip when the full suite has rootless runtime access, all 31 rootless probes, 76
focused tests with one environment skip, 12 independent sentinel tests, both isolated-wheel command
smokes, Ruff, formatting over 148 files, strict Pyright, and diff checks.

The next fresh audit independently passed the full 428-test suite with one future-phase skip, all 31
rootless probes, every literal plan command, package isolation, static checks, five repaired-blocker
experiments, Law attacks, and the real-repo record, but correctly returned `GATE: FAIL` on one final
transcript boundary. Preflight `git status` still ran directly and setup failures discarded the
in-memory records with the temporary attempt. Capture now journals that preflight through the same
bounded process runner and includes it in successful `git-commands.jsonl`. Any later setup failure
persists a content-addressed failure directory containing hash-bound Git, engine, and proxy JSONL
transcripts plus the exact request and bounded error manifest; the raised error identifies that
directory. Missing-engine and dirty-repository attacks prove both failure paths persist before they
fail closed. The final fresh audit on these bytes returned literal `GATE: PASS`: 430 tests with one
future-phase skip, 33 live runtime integrations, every literal plan command, isolated sentinel
build/install/smokes, five Law attacks, static quality checks, and the repeated real-repo loop passed.

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

- Nothing blocks Phase 4; it is merged and tagged `v0.4`. Its fresh audit returned literal
  `GATE: PASS` on `e408545`, and the hardening that followed is covered by the green full suite.
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
- **P-035 is OPEN, awaiting the owner.** It proposes `docs/ARCHITECTURE.md` §22 (the three planes,
  the WORK plane as a producer/judge agent harness, evidence classes, the trust boundary, and the
  four-arm release gate) with a ten-line diff drafted and deliberately not applied. Until it is
  decided the planes stay a `CLAUDE.md` convention, `repair_tools()` stays `[]`, no provider-native
  specialist is installed or credentialed, and the verifier's import guard stays a denylist.
- `rg` was not on this machine at the start of Phase 1; the official 14.1.1 binary is now at
  `~/.local/bin/rg.exe`. The scan refuses to run without it (`TOOLING_MISSING`), by design.
- `pyright` is a dev dependency and pinned in `uv.lock` (1.1.411). It used to run only from a
  machine-global install, so "pyright is clean" was not a reproducible claim.

## Decisions that carry forward

*(record here anything a later phase must not re-litigate — with the phase it was decided in)*

**Engine-v0 decisions (2026-09-13).**
- **The resolver's question is "does any version in this pack's lattice change this subject?", not
  "which version runs at this site?"** A subject present in every lattice version with equal
  breaking attributes cannot be affected by a migration this pack can perform, so a recall match
  naming only such subjects is NOT_AFFECTED_WITH_EVIDENCE with the lattice as its evidence. This
  is a corrected claim table, not a relaxation: the rule fires only where a handler already
  returned UNKNOWN, only for subjects the pack itself names, and a subject that changes at any
  boundary keeps its UNKNOWN and gains the boundary version in its closing instruction. Do not
  re-derive it as an "unknown reduction" — unknown count is a diagnostic, never a target.
- **The lattice is a third mover of a candidate**, beside a structural parse and a source-closure
  role. Tests that assert a recall match "stays UNKNOWN" are asserting the old table; assert the
  law each one guards instead (no adjudication record, no borrowed file version, the parse failure
  still its own UNSCANNED candidate).
- **A package version is not a provider API version, and the difference is the claim type.**
  `dependency_state` and `sdk_installed` describe an installed package, so `provider_versions`
  returns nothing for them. Never separate the two with a version-string pattern in generic code:
  a provider whose API versions look like package versions must still work.
- **A version the pack cannot compose a diff from is recorded with a reason, never skipped**, and
  becomes a HUMAN obligation, so an AFFECTED site never carries zero obligations. `change_sets_for`
  catches `PackDataError` only; anything else propagates.
- **The closure owns the search set.** It emits the globs themselves and every searcher passes them
  through unmodified. A caller that reformats an exclusion is how `.git`-as-a-file was searched.
- **A scan runs on a quiet tree.** FA-069 was a background `pip install .` writing into the tree
  between the closure walk and the ripgrep run, not a closure defect. Stopping is correct: once the
  evidence set and the search set describe different trees, every count is a count of two trees.

**Phase-5 decisions (2026-09-08).**
- **P-011 ACCEPTED.** `Falsifier` gains `failure_class` and `check(FalsifierInput) ->
  FalsifierOutcome`, with both types in `core/verification.py` so `verify/` never imports `packs/`.
  `Transform` and `ToolSpec` carry the identical deferral and are deliberately untouched; Phase 6
  raises its own proposal with the repair loop's evidence in hand.
- The neutral verification vocabulary lives in `core/verification.py`, following `core/surface.py`
  and `core/observer.py`. `app/` converts the pack's contract diff into a `ChangeSet` and passes an
  `OracleView` and `FalsifierView`s in. Do not move these into `verify/`.
- `bounded_process`, `command_record` and `command_transcript` live in `core/process.py`;
  `sandbox/runner.py` re-exports them. `verify/` may not import `sandbox/runner`, and one safety
  property implemented twice is two places to get it wrong.
- **Verdict precedence is FAILED > UNKNOWN > HUMAN_REQUIRED > VERIFIED_FOR_SCOPE.** A definite
  failure is more informative than an absence, so a candidate that both fails a falsifier and has an
  unavailable oracle is FAILED. `UNKNOWN_BLAST ≠ ∅` with every other conjunct true is the only route
  to HUMAN_REQUIRED.
- Obligations are an **input** to Phase 5, not an output: read from `--obligations` and validated
  against the frozen schema. With none supplied, containment requires every hunk to be
  `COLLATERAL(reason)`, which is the strict reading. Phase 6 wires the engine's output into the same
  input; it does not change the containment rule to be lenient.
- `app -> hubbleops.sandbox.verifier_image` stays a tolerated deep import. Re-exporting the verifier
  image through `sandbox/__init__.py` would hand `verify/` a route to the rest of the sandbox, which
  is the boundary the Law exists to hold.
- `verify/tests.py` is named `verify/suites.py`, and its record types are `SuiteRun`/`SuiteCase`, so
  pytest does not try to collect the verifier. Do not rename them back.
- Three tempting shortcuts were rejected: a name-based test-to-module map (trap 2), treating "tests
  pass" as "radius covered" (trap 3), and letting `app/` compute `falsifiers_pass` and hand the
  verifier a boolean (which would move an authority check outside the authority).
- **Versions are never guessed.** `from` is the version the base rescan detected and `to` is the
  latest in `pack.versions()`, both overridable by `--from`/`--to`. Detection that is ambiguous or
  empty raises rather than picking one; the error names `--from`.
- **`hops verify` has no `--force`.** It fell back to an empty import graph when ast-grep was
  missing, which emptied the reachable set and made the blast radius vacuously clean. A verification
  that cannot build the graph cannot compute a radius. Do not reintroduce a degraded verify mode.
- The verifier's isolation is proved twice: `tests/unit/test_isolation.py` for the policy and
  `tests/integration/test_verifier_runtime.py` for a real container, which skips without Podman.
  The verdict never depends on whether the container ran — it depends on whether the checks ran.

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
- Dynamic and sentinel promotion provenance is source-bound at observation time. A repository frame
  without a captured source hash is not promotion evidence, and source drift invalidates promotion.
- Private destinations stay denied except for the test-only fixture object supplied directly to the
  capture API. The public CLI cannot declare an exception, and the actual fixture identity is bound
  into the execution manifest.
- Every Git, engine, proxy, and workload subprocess has a bounded, hash-preserving transcript in the
  run artifacts. Preflight Git uses the bounded runner too. A truncated rendering retains the hash
  and byte size of the captured stream, and setup failures persist their available transcripts and
  bounded error manifest before they fail closed.

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
  builder that happens to call it today: an open candidate (`UNKNOWN`, `HUMAN_REQUIRED`,
  `UNSUPPORTED`, `UNSCANNED`) closes only
  when the write attaches an evidence id the stored row did not have (L3); no write may drop an
  attached evidence id (L1, and it is what stops L3 being bypassed by shrinking the set first); a
  record's `proof_scope_hash` must equal its run's (L4); `candidates.status` carries a SQL `CHECK`
  over the accepted enum so raw SQL cannot invent a status; and `PRAGMA user_version` is stamped,
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
  `file_unscanned` evidence and both resolve to `UNSCANNED` with a closing instruction. The media
  reason no longer claims the file "cannot carry an executable provider call" — that is the very
  thing the scanner could not check.
- `observe/resolver.py` fails closed when a location-bound claim cites a path the closure never
  classified (`PathNotInClosure`). It used to default the classification to `INSIDE`, which is a
  silent assumption on a safety question: first-party and therefore repairable.
- A tool timeout exits `5` (`EXIT_UNKNOWN`) and prints `UNKNOWN: <reason>`. It used to exit `4`
  alongside real failures, which contradicted L9's "timeout → UNKNOWN with reason".

**Precise-indexer decisions (2026-09-13, Tier 3b, decided by measurement under the owner's
"do not ask" instruction).**
- **A precise index is an additive layer over the same graph, never a replacement for search.**
  Symbol-bound callers remove a name-and-arity caller only when its symbol belongs to **another
  definition in the graph** (FA-066); a declaration, local or unreadable symbol keeps the caller.
  Provenance is per caller (`… at path:line by symbol`), never per definition. Files without an
  index keep every claim exactly as today, and the map prints `precise=` per language with the
  indexer identity or the sentence naming what would enable it.
- **The indexer never runs on the customer tree, and the index is a function of the tree.**
  Staged copy, synthesized root manifest, output outside the copy, `project_root` never read
  out of the index (FA-061, FA-062); `typeRoots`/`types` forced empty and every occurrence not
  owned by one of the repository's own manifests dropped before use (FA-065). This is what keeps
  two hosts from producing two verdicts under one ProofScope.
- **The precise layer never binds to a package.** External symbols do not survive the filter;
  the dependency observer owns packages and their versions. A first-party symbol whose
  definition the indexer skipped adjudicates nothing.
- **Absence is recall-only; failure of a present indexer stops the scan unless forced.** A
  timeout-dependent fallback would make two runs of one ProofScope disagree, so it is not silent
  and not automatic. `scip-python` refuses Windows with the reason as TOOLING_MISSING.
- **Identity binds the entry script; vendoring binds the tree.** `;scip-typescript=<version>
  +sha256:<entry>` enters `scanner_version` today; P-034 is the path to an archive hash.
- **Line-delimited JSON is split on `\n` only** (FA-064): `str.splitlines()` breaks on U+0085,
  U+2028, U+2029, VT and FF inside a matched line, and a held-out repository carried one.

**Coverage-closure decisions (2026-09-12, from the `dubinc/dub` audit; P-026 ACCEPTED).**
- **A recall surface derived from a hand-maintained list is a defect, not a configuration choice.**
  The text and structural channels each held their own copy of the GAQL resource enumeration, so
  §2's independence assumption failed and a usage vanished with no candidate at all. The resource
  set is now derived from the provider catalog. Do not reintroduce a literal resource list in
  either `surface.yaml` or a rule bundle; `test_the_query_resource_set_is_derived_from_the_catalog`
  fails if anyone does.
- **`VersionCarrier.scope` distinguishes `wire` from `sdk`, and the loader refuses a `wire` carrier
  that restricts languages.** A wire identifier (proto namespace, gRPC method path, versioned REST
  target) is readable in every language by construction (§6.6, P-008). Do not re-add a
  language-gated copy of one.
- **A wire namespace seen only by the text observer names its version and stays UNKNOWN.** It is not
  AFFECTED: `google.ads.googleads.vNN` appears in type URLs, fixtures and docs as data. This is what
  reconciles FA-007 (a quoted namespace must not become a live call version) with FA-041 (the
  version must still be read in every language). The discriminator is *request target*, not
  language: `per_call` wire carriers stay AFFECTED, `sdk_default` ones do not.
- **Provider context is transitive over resolved in-repo imports, and gates only the *generic*
  contract-surface shapes.** A shape unambiguous on its own (`auth_scope`, `resource_name`) is
  ungated and fires anywhere. Emitting every shape repository-wide is rejected — it trades a silent
  miss for destroyed precision, which is the wrong trade in both directions.
- **Closure enumeration and reads both use extended-length paths on Windows.** Enumerating without
  it and searching with ripgrep (which handles them) makes the two disagree and stops the scan
  fail-closed — observed on `googleads/google-ads-python` at a 273-character path.
- **Held-out repositories earn their keep.** Fixing the long-path abort (FA-046) exposed FA-048: a
  package-relative import (`from . import x`) crashed module resolution with a `ValueError` instead
  of failing closed. No design fixture used a relative import, so only a repository we did not
  design against could find it. Keep at least one held-out repo per language in the loop, and treat
  a traceback from an observer as an L9 defect, never as a bad input.
- **A definition query that misses a language's ordinary function forms is a recall defect, not a
  coverage preference.** `LANGUAGE_QUERIES` for TypeScript and JavaScript captured only
  `function f(){}`, `async function f(){}` and `const f = (p) => b`, so async arrows, generics,
  class methods and object methods were not `Definition`s; the enclosing function of a sink was
  invisible, the wrapper walk could not reach callers, and argument literals never resolved.
  Parameters were positional-only, so a destructured parameter (`({path, method})`) never bound.
  Both are fixed; a new language form added later owes the same two properties.
- **Module resolution is language-configured, not convention-guessed.** `tsconfig.json` /
  `jsconfig.json` `paths` and workspace layouts are read, so an in-repo import such as `@/lib/...`
  resolves inside the repository instead of reporting "outside this repository" and flooding
  UNKNOWN. A resolver that cannot read a language's own module configuration reports every aliased
  import as external, which is a silent recall loss dressed as a boundary.

- **A baseline count is a count of one environment, and the precise layer is part of that
  environment.** The CI baseline gate (`tests/corpus/baseline_gate.py`, third `baselines` job)
  clones the three pinned repositories, scans each with the state directory outside the clone, and
  fails on any movement against `tests/corpus/baselines/`. Its Linux numbers reproduce the owner's
  recorded `engine-v0` figures exactly on `dubinc/dub` (327 · 4 · 90 · 15 · 0) and
  `woocommerce/google-listings-and-ads` (1143 · 259 · 457 · 94 · 0), and differ on
  `singer-io/tap-google-ads` (647 · 38 · 507 · 91 · 0 → 651 · 43 · 500 · 97 · 0). The delta is the
  whole point: the recorded figures were measured on Windows, where `scip-python` does not start,
  and the runner's Linux environment indexes it. The only repository that moves is the only pure
  Python one. Baselines are therefore recorded by the runner in the environment that enforces them,
  never transcribed from another machine.
- **A test fixture written with `write_text` is not the same bytes on two platforms.** Python's
  text mode translates `\n` to `\r\n` on Windows, so a fixture written that way hashes differently
  there, and `test_the_closure_of_a_tree_without_a_generated_directory_keeps_its_digest` pinned the
  CRLF digest `d861a0b6…` — green on the owner's machine, red on any Linux runner. The fixture now
  passes `newline="\n"` and the constant is the LF digest `c76fb604…`, which both platforms now
  produce. Any fixture whose bytes feed a digest pins its newline; the closure itself was never
  wrong, since a CRLF file genuinely is a different file.
- **The `engine-v0` figures for `singer-io/tap-google-ads` are now the Linux ones:
  651 candidates · 43 AFFECTED · 500 NOT_AFFECTED · 97 UNKNOWN · 0 unexplained.** The Windows
  figures 647 · 38 · 507 · 91 · 0 are superseded and must not be used as a comparison point; a
  measured `engine-v0` → new delta taken against them would attribute the precise Python index to
  the change under test. `dubinc/dub` and `woocommerce/google-listings-and-ads` are unchanged.
- **`dev/engine-v0.json` is generated by the runner, not hand-written, and was regenerated once on
  Linux.** The committed file previously carried Windows tool pins (`C:\...\rg.EXE`, an
  `ast-grep.CMD` shim) that identify the engine on exactly one machine and no other. It now carries
  the Linux resolutions the gate actually enforces under, its `ast_grep` digest matches the pinned
  `manylinux_2_35_x86_64` `binary_sha256` in `toolchain.json`, and it records the two SCIP indexers
  — `scip-python` at `0.6.6+sha256:ce60dd39…`, `scip-typescript` as `TOOLING_MISSING`, which is why
  Dub's TypeScript is recall-only in both environments. CI never rewrites this file: the
  `baselines` job regenerates it to `$RUNNER_TEMP` and uploads it, because a frozen record that is
  rewritten on every push is not a frozen record. Regeneration onto the committed path is a
  deliberate act (`--engine-scope dev/engine-v0.json`). One consequence is recorded openly: the
  regenerated `source` block names the commit it was generated from (`9a4a8dd`, branch
  `phase-06-audit-fixes`), while `tag` and `frozen_on` are preserved from the old file, so those
  two fields no longer describe the same tree. Re-tag or pin `source` if that matters before
  `engine-v0` is quoted again.
- **`scip-python` shells out to `pip`, and `uv sync` seeds no pip.** A CI-mirror run failed with
  `TOOLING_FAILED: scip-python: Could not find valid pip command`, and `hops scan` exited 4 rather
  than degrading — correct fail-closed behaviour that would have been a red job on first push. The
  `baselines` job runs `uv pip install pip` after `uv sync`. This is the third form of the venv
  lesson already in this file; the environment fix was verified in a live container before a run
  was spent on it.

**Provider field names in data files: the Airbyte shape does not recur (2026-09-17).** Before
building a search for field names carried outside query literals (tasks 3 and 8), the ten pinned
families and the three baselines were surveyed with one command each, run on the pinned clones
under `.hubbleops/artifacts/corpus/clones/`:

```
rg -n --no-heading -e 'metrics\.[a-z_]+' -e 'segments\.[a-z_]+' -e 'campaign\.[a-z_]+' \
   -g '*.{yml,yaml,json,toml,csv}' -g '!**/node_modules/**' -g '!**/vendor/**' \
   -g '!package-lock.json' -g '!composer.lock' -g '!yarn.lock' -g '!poetry.lock' -g '!uv.lock' \
   <family-root>
```

A hit counts as outside a query literal when its own line carries no `SELECT`; a hit under
`tests/`, `fixtures/`, `mocks/`, `examples/` or `testdata/` is reported separately, because a JSON
test mock is not the shape.

| family | data-file hits | outside SELECT | non-test | verdict |
|---|---|---|---|---|
| php-sylius-plugin | 0 | 0 | 0 | no |
| php-laravel-conversions | 0 | 0 | 0 | no |
| php-laravel-rest | 0 | 0 | 0 | no |
| py-mcp-server | 0 | 0 | 0 | no |
| py-google-shopping | 0 | 0 | 0 | no in the surveyed kinds; **yes in `.sql`** — `acit/views/main_view.sql:59` |
| py-spend-killswitch | 0 | 0 | 0 | no |
| py-ai-adops-cli | 2 | 0 | 0 | no — both are English prose in `tests/evals/read.json:74` |
| ts-node-client | 0 | 0 | 0 | no |
| ts-asset-manager | 0 | 0 | 0 | no |
| ts-keyword-app | 0 | 0 | 0 | no |
| baseline dubinc/dub | 0 | 0 | 0 | no |
| baseline woocommerce/google-listings-and-ads | 0 | 0 | 0 | no |
| baseline singer-io/tap-google-ads | 0 | 0 | 0 | no |

The detector was calibrated before the silence was believed: the same command on
`airbytehq/airbyte` `airbyte-integrations/connectors/source-google-ads` returns **901** hits, which
is the repository the shape was measured on. A detector that has never fired proves nothing by
staying silent; this one fires.

One real find the mandated extension list does not cover. `google/ads_oneshop`
(`py-google-shopping`) keeps Google Ads field names in **BigQuery view definitions** —
`acit/views/main_view.sql:59` `A.segments.productMerchantId`, and five more, plus
`extensions/merchant_excellence/all_metrics.sql:314`. They are camelCased BigQuery column paths
over a landed Ads table, not GAQL, so no GAQL-shaped search finds them and a v25 field removal
breaks the view silently. One family of thirteen, one file kind: `.sql`.

A second pass over `*.py`, `*.php`, `*.ts`, `*.tsx`, `*.js`, `*.jsx` (same patterns, excluding any
hit within four lines of a `SELECT`) finds field names in code constants on several families —
`AdsCampaign.php:147` `$query->where( 'campaign.status', … )` on GLNA, `streams.py:851`
`filter_param="campaign.id"` on tap-google-ads — but that is the query-builder surface the pack
already observes, not field names read from data, and the pass is heavily polluted by
`segments.length` and `metrics.record_counter`. It is not evidence for this question.

**Conclusion: the Airbyte shape is one repository, not a family property.** Tasks 3 and 8 —
building a search for provider field names outside query literals — earn effort on `.sql` view
definitions (one family) and nowhere else in the corpus. Do not build the YAML/JSON schema-key
search on the strength of Airbyte alone.

**Correction, same day, verified by scan.** The survey's code-file pass set aside bare field-name
strings handed to query builders as "the surface the pack already observes". It is not. A scan of
the pinned `singer-io/tap-google-ads` clone reproduces the engine-v0 count (647 candidates);
`tap_google_ads/streams.py` carries 56 of them, none between lines 845 and 925 where
`filter_param="campaign.id"` appears three times, and none mentioning `campaign.id`. GLNA's
`$query->where( 'campaign.status', … )` is the same shape. It costs nothing on v22→v25 because both
subjects are CHANGED only by "composed consecutive mapping" (hop composition, not a contract
change), which is also why a Delta Radar must search REMOVED and renamed subjects only. So the
radar (task 3) and the consumer check (task 8) keep their Phase 4.2 timing, justified by two
measured corpus shapes — bare strings into builders, landed-table `.sql` — not by Airbyte.

**Ownership rule (2026-09-17).** Every task session owns every defect it meets: fixed in that
session, own commit, before "done". No reporting back and stopping, no deferring to a later task,
no handing the operator a choice. Only a frozen surface stops work, and then only the part that
depends on it. Added to CLAUDE.md "How to work".

**A manifest read but not evaluated is now a record, never silence (2026-09-17).**
`_parse_setup_py` read `install_requires` and `_literal_requirements` returned `[]` for anything
that is not a list or tuple of string constants, so `install_requires=MAIN_REQUIREMENTS` — the
shape Airbyte's Google Ads connector shipped for about two years across five API migrations — read
as zero dependencies with no signal. Row two of the coverage rule treats a cleanly parsed manifest
as a reading of its dependencies; that file parses cleanly and reads nothing.

Every parser in `deps.py` was audited for the same hole and each is closed the same way: a
declaration the parser reads but cannot statically evaluate emits an `UnresolvedExpression` naming
the field, the expression, the file and the line, which `scan` emits as a `dependency_state` record
with `state = UNEVALUATED_DEPENDENCIES` and the resolver turns into an UNKNOWN with a `close_with`.
Nothing is evaluated: `ast.unparse` renders the expression and the module is never executed.

| parser | the hole | note |
|---|---|---|
| `_requirement` (shared by `requirements*.txt`, `constraints*.txt`, pyproject, setup.py, setup.cfg) | `-r`/`-c`/`-e` includes; URL and `git+` requirements; any non-empty non-comment line the PEP 508 regex rejects | `--index-url`, `--hash`, `-f` declare no dependency and stay silent by design |
| `pyproject.toml` | `project.dynamic = ["dependencies"]` — the backend supplies them, the file declares none; a non-string array entry | the modern form of the same defect |
| `poetry.lock`, `uv.lock` | a `[[package]]` with no `name` | |
| `setup.cfg` | entries the regex rejects, which is exactly setuptools' `file:` and `attr:` directives | |
| `setup.py` | non-literal `install_requires`; a list element that is not a string constant; a computed `extras_require` | the named defect |
| `package.json`, `composer.json` | a section that is not a mapping; a dependency whose spec is not a string | |
| `package-lock.json`, `npm-shrinkwrap.json` | a locked entry (v3 `packages` or legacy `dependencies`) with an empty spec | the `""` root key is the project, not a drop |
| `yarn.lock` | a resolution block that never carries a `version` line | the specs were held and silently overwritten by the next header |
| `pnpm-lock.yaml` | `packages` that is not a mapping; a key yielding an empty package name | |
| `composer.lock` | a locked package with no `name` | |
| `pom.xml` | a `<dependency>` missing `<groupId>` or `<artifactId>`; a coordinate whose group or artifact is a `${property}` | the second was kept under a name no surface package can match, which is silence wearing a name |
| `build.gradle(.kts)` | any dependency declaration with no literal `group:artifact:version` — `"g:a:$ver"`, the map form, a version-catalog alias, a two-part BOM coordinate | `project(...)`, `files(...)`, `fileTree(...)` declare no external package and stay silent by design |
| `gradle.lockfile` | a non-empty line that is neither a comment, `empty=`, nor a coordinate | |
| `packages.lock.json` | a framework block that is not a mapping; a locked entry with an empty spec | |
| `packages.config` | a `<package>` with no `id` | |
| `*.csproj`, `*.vbproj` | a `<PackageReference>` with no `Include` (the `Update=` form); an `Include="$(Property)"` | |
| `go.mod` | a line inside a `require (...)` block that is not a resolvable require | `replace`/`exclude` blocks declare no requirement |
| `go.sum` | a non-empty line that is not a checksummed module line | |
| `Gemfile` | `gemspec`, `eval_gemfile`, `instance_eval` | the gems live in the file the directive loads |
| `Gemfile.lock`, `Pipfile.lock` | none — the name always survives, so a missing version is UNKNOWN, not silence | |
| `_parse` dispatcher, `_read_text` | already fail-closed: unknown filename and undecodable bytes both reach `UNPARSABLE` | |

Two decisions worth keeping. **The claim key was not changed.** `dependency_state`'s key is
`{state}:{path}`, so every unevaluated expression in one file groups into one candidate carrying
every record's evidence id; per-expression evidence survives, the candidate count grows by at most
one per manifest, and `core/candidate.py` needed no edit. **A manifest's unread expression does not
weaken a lock that did resolve.** The lock is the installed set and its absence claim stays PROVEN;
only a lock whose *own* entries did not fully resolve is dropped from `locked_ecosystems` and
`locks_for`, which is how the coverage rule sees the record rather than being told about it.

No count moved. `tap-google-ads` 651/43, `dub` 327/4, `google-listings-and-ads` 1143/259 on the
Linux runner, `GATE: PASS`, and all 651 tap-google-ads identities and statuses byte-identical. The
proof scope moves, as it must: `resolution_hash` now carries the unresolved set. Two under-readings
found in the same audit are their own commit — `setup.py` never read `setup_requires` or
`tests_require`, and `Pipfile` never read the `version` key of a table spec.

**tap-google-ads Windows 38 ⊆ Linux 43, and the cause is not a defect (2026-09-17).** The committed
engine-v0 export (`tests/fixtures/real_repo/tapga_identities_before.json`, 647 identities, 38
AFFECTED) against a Linux ledger export at the same SHA (651 candidates, 43 AFFECTED): **every
Windows AFFECTED is AFFECTED on Linux, and all 647 Windows identities are present on Linux.** The
delta is four Linux-only `UNRESOLVED_PARAMETER` candidates from the wrapper walk in
`tap_google_ads/streams.py`, plus seven status moves off `NOT_AFFECTED_WITH_EVIDENCE` — five to
AFFECTED in `spikes/`, two to UNKNOWN in `client.py` — all of them `login_customer_id` bindings the
precise layer resolves. The cause is `indexers.py:283`: `scip-python.version()` raises
`ToolingMissing(WINDOWS_START_FAILURE)` unconditionally on `win32`, so **every** Windows scan is
recall-only for Python. It is deterministic, not intermittent, and it is *recorded* — the scan's
`index_status` says `recall-only: scip-python does not start on Windows` — so it is a platform
limitation the Exposure Map already carries, not silence. A Windows run of the baseline gate
reproduces 647/38 exactly. Windows is not engine-v0's platform; `dev/engine-v0.json` is Linux.

Two smaller Windows-only observations from the same run, neither an engine defect: `dub` reads
`unsupported=207 unscanned=8` on Windows against `206/9` on Linux, same total 327, the same
recall-only cause class; and the `google-listings-and-ads` clone cannot be checked out under a deep
Windows temp path at all (`Filename too long` on four `js/src/...` paths), which is MAX_PATH in the
harness's work directory, not the closure. Measure real-repo counts in the Linux container.

**The baselines gate is green twice on GitHub's runner (2026-09-17).** Run 18 at `0a9e4ca`
([35220888682](https://github.com/jastijayakrishna/HubbleOps/actions/runs/35220888682)) and run 19
at `d0a9fee`, this session's engine change
([35243847832](https://github.com/jastijayakrishna/HubbleOps/actions/runs/35243847832)) — `checks`,
`baselines` and `cost` all success on both. The gate compares all eleven counts on all three pinned
repositories and exits non-zero on any move, so `GATE: PASS` on two separate runner allocations,
before and after the change, *is* the identical-counts evidence. It also settles the reseed
question: GitHub's apt ripgrep agrees with the committed baselines, so `record_baselines` was never
needed. Both were push-triggered; see `dev/tasks.md` for why `workflow_dispatch` was unreachable.

**A determinism test failed once and did not reproduce (2026-09-17).**
`tests/unit/test_structure.py::test_compositional_summaries_change_cost_but_never_a_single_record`
— two scans of the same fixture, the second with `ResolutionCache.store` disabled, asserting the
structural evidence is identical — failed in one `pytest tests/unit` run. It passed in its own
file, in two further full unit runs, and in a probe that scanned the fixture eight times (four
cached, four with `store` disabled) producing byte-identical evidence every time. The observation is
not explained, so it is not closed: it is in `dev/tasks.md`, and the test now names the path, line
and claim type of every record that differs instead of dumping twelve dicts pytest cannot diff.

## Open threads

- Phase 4 is merged to `main` and tagged `v0.4`; nothing was pushed.
- Phase 5 needs a fresh independent gate audit and a post-gate real-repo-loop repeat before it
  merges. The current full host suite is 745 passed with all rootless runtime checks executed; the
  expanded adversarial corpus is 13 corruptions, run by 16 adversarial tests, and stays permanent.
- P-020 and P-021 make supported Search and catalog-provable Mutate requests credential-free under
  explicitly labelled `CATALOG` authority. The live transport remains opt-in and is exercised only
  against `_mock`; nothing claims provider-proven `LIVE` authority without a real provider run.
- Phases 6 and 7 are implemented but unmerged. The explicitly deferred SCC, cross-language,
  type-model, compiler-frontend and incremental machinery remains outside the compressed milestone;
  Phase 8 still requires an `incremental == clean` proof before any cache may influence proof.
- P-009 must be decided before AI triage is operationally connected; no Phase 3 or Phase 4 runtime
  path reaches it.
- P-035 raises the plane and agent-harness boundary that P-009 sits inside, so read the two
  together: P-009 governs who may mint a record, P-035 governs which plane may run an actor at all.
  P-035 also names the held-out corpus (`tests/heldout/`, still absent, `dev/tasks.md:469`) as a
  release blocker rather than a standing task, because the four-arm benchmark cannot run without it.
- The Exposure Map production-services line is live: it prints `N/M` once a telemetry or sentinel
  observer is in the ProofScope, and the "not in this ProofScope" wording only when neither is.
