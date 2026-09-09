# Phase 5 plan — Independent Verification Authority

Branch `phase-05-verification-authority`, cut from `main` at `v0.4`.

Reads: `CLAUDE.md`, `docs/ARCHITECTURE.md` §9 and §9.1, §16, `dev/context.md`.

Ships: `hops verify`, `hubbleops/verify/`, `hubbleops/proof/`, a real
`sandbox/verifier_image.py`, `tests/adversarial/`.

---

## 1. What the authority is, in one paragraph

`hops verify <base_sha> <candidate_sha> --pack <name>` answers one question: *does this candidate
tree discharge the migration without breaking anything else, inside this ProofScope?* It answers it
by rebuilding every fact itself. It reads the base tree, the candidate tree, the Change Pack, and the
injected pack. It reads no repair artifact, no agent manifest, no confidence score, and no previous
Receipt. The output is one of four verdicts and a Receipt bound to a ProofScope hash.

The verdict is a pure function of nine booleans. Everything else in the phase exists to compute those
nine booleans honestly, or to refuse to compute them and say so.

---

## 2. Dependency direction, restated for this phase

The Law: `verify/` never imports `repair/`, `packs/`, or `sandbox/runner`. It may use
`sandbox/verifier_image` and nothing else from `sandbox/`.

That has three consequences the design must absorb.

**(a) The pack reaches `verify/` as neutral parameters.** `app/` is the only importer of `packs/`.
It converts the pack's contract diff into a neutral `ChangeSet`, its falsifiers into neutral
`FalsifierView`s, and passes an `OracleView` for live validation. New file
`hubbleops/core/verification.py` holds those neutral types, exactly as `core/surface.py` holds
`SurfaceSpec` and `core/observer.py` holds the `Observer` protocol.

**(b) `verify/` runs `git` itself.** Trap (1) is "diff from the agent's manifest instead of git".
The only way to be immune is for the authority to shell out to `git diff` in its own process, from
its own module, with no injection point a caller could substitute. `verify/gitdiff.py` does that.

**(c) `bounded_process` moves to `core/`.** `verify/` needs bounded, transcript-preserving
subprocess execution for `git` and for the frozen-test run, and it may not import
`sandbox/runner`. Rather than write a second copy, `bounded_process`, `command_record` and
`command_transcript` move from `sandbox/runner.py` to `hubbleops/core/process.py`, and
`sandbox/runner.py` re-exports them so `sandbox`'s public surface is unchanged. This is the "a second
real implementation earns an abstraction" rule firing exactly once. `core/` is the vocabulary layer;
every layer may import it.

**Worktrees stay in `app/`.** `sandbox.DetachedWorktree` materializes the base and candidate trees.
`app/` may import `sandbox`; `verify/` receives two already-materialized read-only paths.

---

## 3. Module map

```
hubbleops/core/
  process.py          bounded_process, command_record, command_transcript (moved from sandbox/runner)
  verification.py     neutral injection vocabulary: ChangeSet, SubjectChange, OracleView,
                      OracleOutcome, FalsifierView, FalsifierInput, FalsifierOutcome,
                      ObligationView, VerificationInputs, VerificationResult

hubbleops/verify/
  __init__.py         layer surface
  gitdiff.py          git diff --numstat/--unified=0 -> Hunk[], changed paths, blob identity
  audit.py            A. independent rescan + extinction + obligation reconciliation
  oracle.py           B. captured request text -> OracleView.validate, request hash + timestamp
  radius.py           C. Delta -> definitions -> R = reach(G, Delta); diff containment;
                      frozen baseline tests; UNKNOWN_BLAST
  coverage.py         stdlib per-test line tracer + the pytest plugin text it mounts
  behavior.py         D. request-shape differential, response-consumer check
  falsify.py          E. run the injected falsifiers matching detected failure classes
  conserve.py         F. UNKNOWN conservation
  verdict.py          the pure, total verdict function
  authority.py        composes A-F into a VerificationResult (no I/O decisions of its own)

hubbleops/proof/
  __init__.py
  receipt.py          receipt.json (schema-validated) + receipt.md (ARCHITECTURE.md §16 layout)

hubbleops/sandbox/
  verifier_image.py   real ImageSpec, mount policy, forbidden-path refusal, fingerprint

hubbleops/app/
  verification.py     worktrees, two rescans, pack -> neutral conversion, store persistence
  cli.py              `hops verify`
```

---

## 4. The nine booleans, and how each is computed

### 4.1 `audit_pass` — `verify/audit.py`

Independent rescan of the **candidate** worktree with the full observer set (closure, text, deps,
structure, and dynamic when a capture is supplied), using the injected pack. Never reads the
candidate's own ledger, if one exists.

Then three extinction checks against the `ChangeSet` computed from `diff(from_version,
to_version)`:

1. **Old-version residue.** Every `call_version`, `endpoint_reference` and `config_reference`
   candidate whose detected version is the source version must be gone, or carry an
   `EXCLUDED_WITH_EVIDENCE` / `NOT_AFFECTED_WITH_EVIDENCE` status with evidence in the candidate
   rescan. A survivor with `AFFECTED` is `audit_pass = False`.
2. **Removed subjects.** Every `SubjectChange` with `change == "REMOVED"` must have no
   `surface_reference` candidate naming it. A survivor is `audit_pass = False`.
3. **Old namespace / old package constraint.** `sdk_installed` and `package_reference` candidates
   must resolve to a version whose `client_compatibility` admits the target.

**Obligation reconciliation.** Each obligation is reconciled one by one, by id, against the
candidate rescan: its `verification_method` names a check, and the check either finds the required
state (`DISCHARGED`, with the candidate evidence ids that prove it) or does not (`OPEN`). An
obligation that reconciles to `OPEN` is `audit_pass = False`. An obligation whose
`verification_method` this build cannot execute is `UNRECONCILABLE`, which is an *unresolvable
input* and therefore drives the verdict to UNKNOWN, never to PASS.

Obligations are an **input** to Phase 5, not an output: Phase 6 builds the Obligation Engine.
Phase 5 accepts `ObligationView` records (schema-validated against the frozen `obligation.json`)
from the store or from an explicit `--obligations` file. With no obligations supplied, every hunk
must be `COLLATERAL(reason)` to pass containment, which is the correct and strict reading.

### 4.2 `oracle_all_accepted` — `verify/oracle.py`

Every `request_text` candidate in the candidate rescan, plus every captured request in a supplied
dynamic capture, is turned into a request mapping and sent to `OracleView.validate(request,
target_version)`.

- `VALID` → accepted.
- `INVALID` → `oracle_all_accepted = False`, and the provider's error text is carried **verbatim**
  into the Receipt reasons.
- `UNKNOWN_PROVIDER_CONTRACT` → the request is unresolvable → verdict UNKNOWN.
- `ORACLE_UNAVAILABLE` → `ORACLE_UNAVAILABLE` flag set → verdict at most UNKNOWN.

Every result line carries `request_hash` (content id of the canonical request mapping) and an
RFC-3339 `checked_at`. **Determinism:** the timestamp is real and therefore varies, so it lives in
`oracle_results[*].checked_at` only, and is excluded from the Receipt's own content id and from every
comparison. A property test asserts two runs differing only in timestamps produce the same verdict
and the same `receipt_body_hash`.

Live execution: `app/verification.py` builds the live transport from env when the pack exposes one
and the env is complete; otherwise it passes the pack's shipped unavailable transport, and the run
honestly reports ORACLE_UNAVAILABLE. Only validation-only operations are ever issued — this is an
approval boundary, enforced by the pack (`validate_only` is set by the pack's own contract module,
not by `verify/`).

### 4.3 `zero_unexplained_hunks` — `verify/radius.py`, diff containment

`verify/gitdiff.py` runs `git diff --unified=0 --no-color --find-renames <base>..<candidate>` in the
repository and parses it into `Hunk(path, old_start, old_lines, new_start, new_lines, header)`. It
also runs `git diff --numstat` and cross-checks the file set; a mismatch is a tooling failure, not a
silent narrowing.

Each hunk maps to exactly one of:

- an **obligation id**, when the hunk's line span intersects a definition that the obligation's
  evidence cites, or the hunk's path+span intersects the obligation's `current_state` location;
- `COLLATERAL(reason)`, when a named, closed rule explains it: a lockfile regenerated by a
  dependency obligation, an import line inside a file another hunk already maps, a formatting-only
  hunk (byte-identical after whitespace normalization) inside a mapped file;
- otherwise **unexplained**, and `zero_unexplained_hunks = False`.

Hunks under `hubbleops/verify/`, `sandbox/verifier_image.py` and the state directory are never
COLLATERAL: a candidate that edits the authority is unexplained by construction.

### 4.4 `UNKNOWN_BLAST` — `verify/radius.py`

- `Δ` = definitions in the candidate graph whose `SourceRange` intersects a changed hunk, plus every
  definition deleted from the base graph.
- `R = reach(G, Δ)` — the transitive closure over the candidate `ImportGraph`'s call, import and
  class edges, module-level, bounded to `MAX_REACH_HOPS = 5` to match the wrapper walk. Reaching the
  bound is not silence: it emits `REACH_TRUNCATED` and every module at the frontier joins
  `UNKNOWN_BLAST`.
- `C(t)` for a passing frozen test `t` = the set of candidate files whose lines that test executed.
- `UNKNOWN_BLAST = R \ ⋃C(passing frozen tests)`, reported **by module**.

`UNKNOWN_BLAST ≠ ∅` with every other flag true → `HUMAN_REQUIRED` with the module list. That is the
only route to `HUMAN_REQUIRED`.

### 4.5 `frozen_baseline_tests_pass` — `verify/radius.py` + `verify/coverage.py`

**The tests are copied from the base SHA and run against the candidate.** Mechanically: the base
worktree's test directories are copied over the candidate worktree into a scratch tree; the candidate
source stays, the tests are the base's. This is the proof. The candidate's own tests are run
separately, recorded under `candidate_tests`, and **never** enter the verdict. Trap (6) is a named
regression test: a fixture where the candidate weakened its own assertion still fails, because the
base assertion is what runs.

Coverage tooling (OPEN MIDDLE, decided below): a stdlib `sys.monitoring` line tracer, injected as a
`conftest.py` sitecustomize-style plugin mounted read-only into the verifier container. No new
dependency, and it works with `network=none`. It emits one JSON line per test:
`{"test": "<nodeid>", "outcome": "passed|failed", "files": [...]}`. Python only. Any other language
in `R` yields `COVERAGE_UNSUPPORTED` for those modules, which puts them in `UNKNOWN_BLAST` — fail
closed, never "tests pass therefore covered" (trap 3).

Test-to-module mapping is **execution-derived, never name-derived** (trap 2). There is no
`test_foo.py → foo.py` heuristic anywhere in the phase; a test proves coverage of a file only by
having executed a line in it.

### 4.6 `request_shape_differential_pass` and `response_consumer_check_pass` — `verify/behavior.py`

**Request-shape differential.** Given a base capture and a candidate capture (dynamic events, from
Phase 4's schema), each request is reduced to a *shape*: `(service, method, sorted field paths,
version)`, values discarded. Shapes present in base but absent in candidate, or changed, must each
map to an obligation id. An unmapped shape change → `False`. With no captures supplied, the flag is
computed from the static `request_text` skeletons instead, and the Receipt names the weaker source;
if neither is available the input is unresolvable → UNKNOWN, never `True`.

**Response-consumer check.** For every subject in the `ChangeSet` that is `REMOVED` or has a
`replacement`, the candidate graph is searched for a read of the *old* name in response-handling
position: attribute access, subscript with the old name as a literal key, or a destructuring
binding, on a value that flows from a provider call. A hit → `False` with path:line. Resolution
failure on a specific site → that site is unresolvable → UNKNOWN.

### 4.7 `falsifiers_pass` — `verify/falsify.py`

The injected `FalsifierView`s whose `failure_class` matches a class the rescan detected are run.
Each returns `PASS`, `FAIL(reason)` or `UNKNOWN(reason)`. Any `FAIL` → `False`. Any `UNKNOWN` →
unresolvable input → verdict UNKNOWN. A falsifier that matches no detected class is `SKIPPED` and
recorded as such; `falsifiers_pass` is vacuously true only when the Receipt says which were skipped
and why.

### 4.8 `unknown_conservation_pass` — `verify/conserve.py`

`UNKNOWN_after ⊆ UNKNOWN_before ∪ evidence-linked ∪ decision-linked`. Concretely: for every
candidate id that was UNKNOWN in the base ledger and is not UNKNOWN in the candidate rescan, there
must exist candidate-rescan evidence not present in the base ledger, or a recorded human decision
whose evidence id is attached. Otherwise `False`, naming the candidate ids.

Newly-appearing UNKNOWNs are permitted and are not a conservation failure — they are preserved
UNKNOWNs and must each carry a `close_with` instruction, which the frozen Receipt schema already
enforces (`unknowns[*].close_with`, `minLength: 1`).

### 4.9 The verdict — `verify/verdict.py`

Pure, total, no I/O, no clock, no randomness, over a frozen `VerificationResult` dataclass:

```
VERIFIED_FOR_SCOPE iff every flag true and UNKNOWN_BLAST empty and no unresolvable input
HUMAN_REQUIRED     iff every flag true, no unresolvable input, UNKNOWN_BLAST non-empty
UNKNOWN            iff no FAIL, but oracle unavailable or some input unresolvable
FAILED             iff any flag false
```

Precedence, stated once so the function is total and unambiguous: **FAILED > UNKNOWN >
HUMAN_REQUIRED > VERIFIED_FOR_SCOPE.** A candidate that both fails a falsifier and has an
unavailable oracle is FAILED, because a definite failure is more informative than an absence.

Property tests: (i) every generated input maps to exactly one verdict; (ii) flipping any single
pass-flag of a VERIFIED input never yields VERIFIED; (iii) adding a module to `UNKNOWN_BLAST` of a
VERIFIED input yields exactly `HUMAN_REQUIRED`; (iv) the function is order- and time-independent.

**Every flag is wired.** Trap (5) has a mechanical guard: `test_every_flag_reaches_the_verdict`
enumerates the `VerificationResult` boolean fields by reflection and asserts that flipping each one
individually changes the verdict. A check that is computed but not consulted fails that test.

---

## 5. Isolation — `sandbox/verifier_image.py` (DoD 8)

Today the file is a 26-line stub. It becomes:

- `VERIFIER_IMAGE: ImageSpec` — a **distinct** digest-pinned image from every capture image, so the
  verifier and the repair runner can never be the same container.
- `VerifierMounts.build(base, candidate, output)` — returns `Mount`s with the candidate **read-only**,
  the base **read-only**, one writable output directory, `network="none"`.
- `FORBIDDEN_SOURCES` — any mount whose source is under a repair-sandbox attempt directory, an agent
  log directory, or a change-manifest path is **refused** with `VerifierIsolationViolated`, not
  filtered. Refusal, not filtering, so an attempt is loud.
- `fingerprint()` covering image reference, user, network, read-only root and the mount policy, fed
  into `ProofScope.verifier_image_hash`.

`tests/unit/test_isolation.py` proves: the reference differs from every capture image; a
read-write candidate mount is refused; each forbidden source class is refused; `network` is `none`;
and the fingerprint changes when any of those change.

`verify/` imports **only** `sandbox.verifier_image`, asserted by extending the existing
`test_verify_never_imports_the_systems_it_judges` with `hubbleops.sandbox.capture`,
`hubbleops.sandbox.proxy`, `hubbleops.sandbox.image`, `hubbleops.repair` and `hubbleops.packs`.

---

## 6. Receipt — `proof/receipt.py` (DoD 9)

`receipt.json` is built against the frozen `core/schemas/receipt.json` and validated before it is
written; a receipt that would carry `unexplained != 0` is not representable and raises.
`receipt.md` renders §16's exact layout.

`receipt_body_hash` = content id of the receipt with every timestamp field removed. Two runs of the
same scope produce byte-identical `receipt.md` except the oracle timestamps, and identical
`receipt_body_hash`. Tested.

A Receipt carries `proof_scope`, so presenting it for a different SHA is detectable: `hops verify`
recomputes the ProofScope and refuses to reuse a stored receipt whose scope hash differs. That is
adversarial case 11.

---

## 7. Adversarial suite — `tests/adversarial/` (DoD 10)

A fixture repository `tests/fixtures/phase5/` with a base tree, a **correct** candidate that reaches
`VERIFIED_FOR_SCOPE`, and twelve corruptions. Each corruption is a directory holding `patch`,
`expected_verdict.json` (verdict + required reason substring + the stage that must catch it), and
nothing else.

| # | Corruption | Expected | Caught by |
|---|---|---|---|
| 1 | hidden per-call version override | FAILED | audit (extinction) |
| 2 | a frozen-baseline test deleted on the candidate | FAILED | radius (frozen tests are the base's) |
| 3 | an assertion weakened on the candidate | FAILED | radius (base assertion still runs) |
| 4 | an unrelated query changed | FAILED | containment (unexplained hunk) |
| 5 | old generated namespace still imported | FAILED | audit (extinction) |
| 6 | field renamed in the query, old name read in the response | FAILED | behavior (response-consumer) |
| 7 | coverage removed from a radius module | HUMAN_REQUIRED | radius (UNKNOWN_BLAST) |
| 8 | a file under `verify/` edited | FAILED | containment (never COLLATERAL) |
| 9 | a removed resource reintroduced | FAILED | audit (removed subjects) |
| 10 | an UNKNOWN closed without evidence | FAILED | conservation |
| 11 | a stale Receipt presented for a new SHA | FAILED | scope binding |
| 12 | AI-only evidence closing a candidate | FAILED | conservation (L10) |

All twelve are asserted non-VERIFIED **with the right reason**, not merely non-VERIFIED — a
corruption caught for the wrong reason is a latent hole. The red-team subagent runs against this
suite at the gate.

---

## 8. Determinism

Same inputs → byte-identical outputs, with three named exceptions, each isolated and excluded from
every hash and comparison: the oracle `checked_at` timestamps, the run `started_at`/`finished_at`,
and the frozen-test wall durations. Everything else is sorted, stamped and seeded. Reach traversal
iterates sorted definition ids. Hunk order is `git diff` order, which is deterministic for a fixed
pair of SHAs. A property test runs the same verification twice and asserts identical
`receipt_body_hash`.

## 9. Fail-closed table

| Condition | Result |
|---|---|
| `git` missing / diff unparsable | `TOOLING_MISSING` / `TOOLING_FAILED`, verdict UNKNOWN |
| coverage unsupported for a language in `R` | those modules join `UNKNOWN_BLAST` |
| oracle transport unavailable | `ORACLE_UNAVAILABLE`, verdict at most UNKNOWN |
| obligation `verification_method` not executable | `UNRECONCILABLE`, verdict UNKNOWN |
| frozen test run times out | UNKNOWN with reason, never `frozen_baseline_tests_pass = True` |
| capture absent for the behavior differential | static fallback, named in the Receipt; if neither, UNKNOWN |
| reach bound hit | `REACH_TRUNCATED`, frontier joins `UNKNOWN_BLAST` |

No `except: pass`. No fallback that changes safety semantics.

---

## OPEN QUESTIONS — all answered before implementation

**Q1. `Falsifier` in `packs/_protocol.py` is `name: str` only. Phase 5 must actually run falsifiers.
Extending it changes a FROZEN interface.**
**ANSWERED — proposal P-011, ACCEPTED.** `Falsifier` gains `failure_class: str` and
`check(FalsifierInput) -> FalsifierOutcome`, with both types defined in `core/verification.py` so
`verify/` never imports `packs/`. The protocol's docstring already reserved this
("implemented in the verification phase"); the shape is what was deferred, not the existence.
Written up in `dev/proposals.md` before the code lands.

**Q2. Where does the neutral verification vocabulary live?**
**ANSWERED.** `hubbleops/core/verification.py`, following `core/surface.py` and `core/observer.py`.
`packs/_protocol.py` imports from it, never the reverse. `verify/` imports `core/` only.

**Q3. `verify/` needs bounded subprocess execution but may not import `sandbox/runner`. Copy or
move?**
**ANSWERED — move.** `bounded_process`, `command_record` and `command_transcript` move to
`hubbleops/core/process.py`; `sandbox/runner.py` re-exports them so `sandbox`'s public surface and
every existing caller are unchanged. A second copy would be two implementations of one safety
property, which is precisely what the no-bloat rule forbids.

**Q4. Coverage tooling per language (declared OPEN MIDDLE).**
**ANSWERED.** stdlib `sys.monitoring` line tracer for Python, injected as a mounted pytest plugin.
No new dependency; works under `network=none`. Every other language is `COVERAGE_UNSUPPORTED` and its
modules in `R` join `UNKNOWN_BLAST`. Adding a language later is additive and needs no verdict change.

**Q5. Obligations do not exist until Phase 6. What does Phase 5 reconcile against?**
**ANSWERED.** Obligations are an *input*, read from the store or an explicit `--obligations` file and
validated against the frozen `obligation.json`. With none supplied, containment requires every hunk
to be `COLLATERAL(reason)`, which is the strict reading and keeps the phase honest. Phase 6 wires the
engine's output into the same input.

**Q6. Which versions does the migration go from and to?**
**ANSWERED.** `from` = the version the base rescan detected, `to` = the latest version in
`pack.versions()`. Both overridable by `--from`/`--to`. Ambiguous detection is not guessed: it is
unresolvable → UNKNOWN.

**Q7. Verdict precedence when several conditions hold at once.**
**ANSWERED.** FAILED > UNKNOWN > HUMAN_REQUIRED > VERIFIED_FOR_SCOPE. Stated in `verdict.py` as the
one ordering, and proved total by property test.

**Q8. Does the verifier have to run in a container for the phase to be done?**
**ANSWERED.** The isolation *policy* is proved by `tests/unit/test_isolation.py`, which is where the
Law lives. Actual container execution is proved by an integration test that skips when Podman is
absent, matching Phase 4's precedent. The verdict never depends on whether the container ran; it
depends on whether the checks ran, and a check that could not run is UNKNOWN.

**Q9. Live credentials.** The phase prompt requires a live oracle run. `app/verification.py` builds
the live transport only when the pack exposes one and the environment is complete. Without
credentials the run reports `ORACLE_UNAVAILABLE` and caps at UNKNOWN — which is a *correct* result,
not a skipped one. The evidence for the gate is the run log with request hashes in whichever mode
the environment supports, and the Receipt naming the mode.

---

# PART TWO — Compressed Phases 6-10 (2026-09-09)

Phases 6-10 as prompted are four to six weeks. The repository owner directed a compression to a
pilot-ready product. This part supersedes the *scope* of the Phase 6-10 prompts; it supersedes none
of the Laws, the verdict rule, or the gate ceremony. Every deferral below defers cost, never proof,
which is L7 generalized. Phases 8 and 9 move to `docs/BUILD_ORDER.md`'s *Deliberately not built*
list with a named trigger.

## 10. Why this compression is safe

Three measurements decided it.

- **The migration is mostly mechanical.** Composed v22->v25: ~1,577 ADDED, 6 CHANGED, 174 REMOVED.
  Additions break nobody. Version literals, generated namespaces, REST paths and the SDK pin
  dominate the diff by line count and are all deterministic.
- **`repair_class` already enumerates `HUMAN`.** Shipping without the LLM repair agent is sanctioned
  by the frozen `obligation.json`, not a degradation of it.
- **The verifier does not care who wrote the candidate.** `hops verify` takes two SHAs and an
  obligations file, so the same engine sells "we migrate you" and "we check the migration you
  wrote". Both need the obligation engine; neither needs the agent.

## 11. Tier plan

| Tier | Branch | Tag | Contains |
|---|---|---|---|
| 0 | `phase-05-verification-authority` | `v0.5` | P-012, oracle credentials, P-013 grouping, `hops decide`, adversarial artifact |
| 1 | `phase-06-obligations-and-deterministic-repair` | `v0.6` | Obligation engine, `Transform` shape, deterministic transforms, `hops migrate` |
| 2 | `phase-07-proof-pack-and-guard` | `v0.7` | `pr_body`, guard, `retired.yml`, `.hubbleops/` writes, GitHub Action, `hops impact` |

Each tier keeps the full ceremony: plan -> fresh plan review -> implement with evidence -> fresh gate
audit -> red-team -> real-repo loop -> merge -> tag. **A compressed scope is not a compressed gate.**

## 12. Cut, and what replaces each cut

- **`repair/agent.py`, the sandboxed repair loop, `pack.repair_tools()`, the retry heuristics.**
  Replaced by `HUMAN` obligations carrying a precise `required_state`. `ToolSpec` stays deferred;
  `PROVIDER_TOOL` and `AGENT` repair classes route to `HUMAN`.
- **The hosted GitHub App, webhooks, `merge_group`.** Replaced by a committed GitHub Action running
  `hops verify` in the customer's own runner. Satisfies every §17 clause — status bound to the exact
  candidate SHA, never green for `HUMAN_REQUIRED`, new commit kills the proof — and the last clause
  comes free from how Actions fire. It also removes the enterprise security review that a hosted app
  reading private repositories would trigger at pilot stage.
- **All of Phase 8.** `hops impact` ships as a report over a full rescan. The fact cache, bindings,
  reverse index and `--incremental` are the optimization, not the feature. No cache lands until the
  incremental-equals-clean equivalence proof exists.
- **All of Phase 9.** `test_no_provider_leak` and `test_imports` already defend the boundary on every
  run; Phase 9 proves it. Trigger to build: the first paying reason for a second real provider pack.

## 13. Status at 2026-09-09

Landed and green on this branch:

- **P-014 ACCEPTED and implemented.** `core/repair.py` holds `TransformInput`, `TransformOutput` and
  `TransformView`; `packs/_protocol.py` `Transform` gains `failure_class`, `precondition`, `apply`
  and `postcondition`. Follows P-011's placement exactly, so `repair/` never imports `packs/`.
- **The obligation engine.** `obligations/engine.py` `build(ObligationInputs)` keys obligations by
  (candidate, that candidate's own effective version, target), composes per-version change sets,
  routes a replacement-bearing change to `DETERMINISTIC`, a removal with no announced replacement to
  `HUMAN`, and anything the diff cannot map to `PRESERVE_UNKNOWN`. Ten tests, including two effective
  versions against one target, an unresolvable effective version, a missing composed diff, and
  byte-identical rebuilds.
- **P-013 ACCEPTED and implemented.** The Exposure Map's UNKNOWN section groups by closing
  instruction, ranks groups by sink proximity, expands only while a ten-site budget lasts, and
  collapses the rest behind `[expand]`. `--expand` prints everything. Five invariant tests, the load
  bearing one being that grouped site counts sum to the ungrouped UNKNOWN count, so the rendering
  can never lose a candidate.

Blocked:

- **P-012's schema edit.** ACCEPTED in the explicit-fields form, but `.claude/hooks/guard.py` blocks
  every write under `hubbleops/core/schemas/` past phase 1 with no notion of an accepted proposal.
  The agreed fix is `_accepted_proposal_names()`, which permits a schema write only when a proposal
  block both names the exact filename and carries `| **Status** | ACCEPTED |`. P-012's **Touches**
  row and status line were updated to satisfy that rule. The hook edit itself must be made by the
  repository owner; the session's tool policy refuses writes to `.claude/hooks/`.

- **Tier 1's repair half.** `repair/deterministic.py` is the generic precondition -> apply ->
  post-check runner; `packs/google_ads/repairs.py` carries the four transforms; `hops migrate`
  (`app/migration.py`) produces the candidate and the obligations file and never a verdict. Proved
  on `python_pinned_v22`: `v22` -> `v25` at the call site and `google-ads==22.1.0` -> `31.2.0` from
  the catalog's own documented minimum. The SDK minimums are keyed by target version so
  `repair_transforms()` keeps its frozen no-argument signature.

Not started: `hops decide`, `proof/pr_body.py`, `proof/guard.py`, `retired.yml`, `.hubbleops/`
writes, the GitHub Action, `hops impact`, the adversarial artifact, and the P-012 wiring in
`app/verification.py`.

## OPEN QUESTIONS — Part Two

**Q10. Who owns the Google Ads test-account MCC and developer token?**
**OPEN.** `google_ads` verification reports `ORACLE_UNAVAILABLE` and caps at UNKNOWN by design, so
HubbleOps cannot emit a green receipt for the shipped provider at all. Test-account access level is
enough for `validate_only`. This is the longest lead time in the plan and it is not engineering.

**Q11. Which ordinary application repository is the third real-repo-loop target?**
**OPEN.** Both current targets are unrepresentative: one is documentation *about* the API, the other
*is* the client library. Their 413 and 402 preserved UNKNOWNs are what forced P-013, but the true
ratio on a normal application is unmeasured, and it decides how much of P-013's collapsed tail is
real.

**Q12. Is the repair-agent cut permanent, or a deferral with a trigger?**
**PROPOSED: deferral.** Trigger — the first pilot where `HUMAN` obligations exceed 20% of mapped
hunks. Record the trigger or the cut silently becomes a scope decision nobody made.

**Q13. Does `zero_unexplained_hunks` need a degraded mode for a customer-authored migration?**
**ANSWERED — no.** §4.3 requires every hunk to be an obligation id or `COLLATERAL(reason)`, and all
three COLLATERAL rules presuppose an already-mapped hunk. A real diff with zero obligations
therefore cannot reach `VERIFIED_FOR_SCOPE`, which is the strict and correct reading. The obligation
engine is the keystone for both products; no degraded mode is added.
