# Plan — closing the migrate → verify → prepare-pr chain

## The outcome under construction

One obligations file written by `hops migrate` is accepted by `hops verify`, reconciled
obligation by obligation, and rendered by `hops prepare-pr`, with no hand-editing between
the verbs. Today no such run exists: `migrate` is correct on its own and every joint after
it is broken.

## Ground truth measured this session

All of the following is this tree, `phase-06-repository-intelligence` at `66e5b75` plus the
uncommitted working set, through the real library entry points.

**The chain, end to end, on a git copy of `tests/fixtures/phase5/base` with `_mock`.**
`hops migrate` → commit → `hops verify` stops at the first joint, and stopping each joint in
turn exposes the next:

1. `VerificationInvalid: the base tree detects nothing; pass --from …` — on `_mock` as well as
   on Google Ads, so this is not a provider-specific detection gap.
2. With `--from v1 --to v2`: `VerificationInvalid: obligation input is not bound to the base
   ProofScope` — migrate scope `3717…`, verify base scope `fc1e…`.
3. With the obligation records stamped to the base-worktree identity (probe only — no code
   does this, and none will): `VERDICT FAILED`, with five distinct causes:

```
  migration_audit            FAIL   removed subject still present: campaigns.legacy at reporting.py:3 and :12
                                    13 / 13 obligations UNRECONCILABLE
  contract_oracle            PASS
  diff_containment           FAIL   unexplained hunk client.py@5,1+5,1
  frozen_baseline_tests      PASS   5 passed
  request_shape_differential PASS
  response_consumer_check    FAIL   response handling still reads campaigns.legacy at reporting.py:12
  falsifiers                 FAIL   removed_field_in_request: a request still names a removed subject
  unknown_conservation       PASS
```

**Closure identity.** A detached worktree's `.git` is a *file*; the closure filters `.git`
only out of `dirnames`, never out of `filenames`:

```
worktree .git is a file: True
main  entries: ['a.py']
wtree entries: ['.git', 'a.py']
EQUAL: False
```

**Repair sites.** `_mock`'s `MockVersionLiteral.apply` rewrites `subject.text` whole-file.
Two obligations (`client.py:1`, `client.py:14`) produced three edits (lines 1, 5, 14), one
`APPLIED`, one `NO_TRANSFORM`, and the line-5 hunk is unexplained.

**Consumer-check watch set.** Larger than the brief recorded, because `consumers()` watches
renamed subjects as well as removed ones:

```
removed 236 changed 1346
distinct leaves 997
generic leaves watched: ['customer_id', 'end_date', 'id', 'name', 'resource_name',
                         'start_date', 'status', 'type', 'value']
```

`id`, `name`, `value`, `type` and `status` are watched as bare identifiers in read position
anywhere in the tree. `response_consumer_check_pass` is therefore false for essentially any
repository, not only a Google Ads one.

**Uncommitted.** 69 files, 4,262 insertions above `66e5b75`; `app/{decision,impact,replay}.py`,
`proof/{guard,memory,pr_body}.py`, `tests/unit/test_phase7_delivery.py` and six other test
paths untracked.

---

## The classes, and the fix for each

Each item names the *class*. Where a fix would only repair the measured instance it is
written as a proposal instead.

### C-1 — Control metadata enters the source closure when it is a file

`closure/source_closure.py` drops `.git` in the `dirnames` loop (line ~364) and never in the
`filenames` loop (line ~382). A detached worktree, a submodule checkout, and a `git
worktree`-managed tree all carry `.git` as a pointer file, so `tree_hash` — hashed over every
entry — differs from the working tree's for the same commit.

**Class:** the closure filters control entries by *directory-ness*, not by *identity*.
**Fix:** filter the control entry in both loops. In `closure/`, never in `verify/` (trap 2).
**Test:** closure of a detached worktree at SHA X is byte-identical to the closure of a clean
working tree at SHA X — an equality, not a `.git` assertion, so the test survives the next
control-file shape.

### C-2 — Scan identity is the filesystem path

`app/cli.py:321` `identity = run_target or str(resolved)`; `run_id = sha256({scope_hash,
provider, verb, target})`. `verify` passes `commit:<sha>`, `migrate` passes nothing, so the
run ids never match even once C-1 makes the scopes match.

**Class:** run identity is a function of where the tree happens to sit on disk.
**Fix:** derive it from what was scanned — `commit:<repo_sha>` when the closure bound one,
else `tree:<tree_hash>`. `run_target` stays as an override, and verify's existing
`commit:<sha>` becomes the same string rather than a divergent one. Nothing anywhere rewrites
an id or a scope hash after computing it (trap 8).
**Depends on** the dirty-tree answer (Q1): a working tree that differs from `HEAD` has a
different `tree_hash`, so `commit:<sha>` would name a scope that is not the commit's.

### C-3 — The obligation method grammar lives in two places and they disagree

`verify/audit.py:18` executes `absent:`, `present:`, `version:`. `obligations/engine.py`
emits English at lines 93, 111, 128, 144, 163, 167, 211, 236. Measured: 13 / 13
UNRECONCILABLE, and `Audit.report()` still says `passed=True` because UNRECONCILABLE lands in
`unresolved`, not in `reasons` — so the receipt prints `MIGRATION AUDIT PASS` beside 13
unreconciled obligations while the verdict is capped at UNKNOWN.

**Class:** one vocabulary encoded twice, with no mechanism that fails when the two disagree.
**Fix:** one generic home that both import; constructors on the emit side, a parser and an
executor registry on the audit side. A property test asserts *every* method the engine can
emit is executable, so the two cannot drift again. The grammar must grow members for the
obligations that exist and have no method today — the SDK pin and the carried UNKNOWN (Q2).
**Also:** the audit's headline must account for its own `unresolved` set, so `PASS` can never
sit beside unreconciled obligations. No verdict-function change; `UNKNOWN iff any input
unresolvable` already holds, only the rendering lied.

### C-4 — "Which version does this evidence claim" has three readers and three answers

`app/verification.py:570` `_detected` reads `value.version` / `value.detected`.
`obligations/engine.effective_version` reads `provider_subject`.
`verify/audit._version_values` reads all of them plus `value.versions` and `value.target`.
Google Ads `call_version` evidence carries the version in `provider_subject`, so `_detected`
returns nothing and every repository is told to pass `--from`. Measured on `_mock` too.

**Class:** three readers of one fact.
**Fix:** one function in a generic layer, used by all three. Detection stays honest —
ambiguous or empty still raises and names `--from` (Phase-5 decision, unchanged). Never
derive the base version by guessing when detection fails (trap 4).

### C-5 — The obligation names where the usage was observed, not where the edit must be made

Two instances of one class:

- `_mock`: the transform rewrites the whole file, so one obligation's repair silently edits
  lines no obligation names → unexplained hunk, and the second obligation reports
  `NO_TRANSFORM` although its site was in fact repaired.
- Google Ads / Dub: `app/migration._locate` anchors at the candidate's own line and
  `packs/google_ads/repairs.py` reads only that line, so an obligation at `api.ts:60` whose
  literal lives at `constants.ts:20` gets `NO_TRANSFORM` and nothing is written.

The resolved site is already in the evidence: `value.paths[]` carries `path`, `range`,
`literal`, `terminal == "LITERAL"`. Verified on the `_mock` ledger.

**Class:** an obligation carries an observation site, and the repair needs an edit site.
**Fix:** resolve the edit site from `value.paths[]` (the LITERAL terminal whose literal
carries the from-version), carry it on the obligation, open *that* file at *that* line in
`transform_requests`, and make every transform edit only its declared site. `verify/radius`
containment already maps a hunk by a `path:line` named in `current_state`, so the constants
hunk maps to the obligation without a containment change — containment is never matched by
path alone (trap 3). N call sites resolving to one literal produce N obligations naming one
edit site and therefore one edit; the first applies and verify discharges all N by the
`absent:` check at that site, because discharge is verify's judgement and never the repair
report's. Shape of the carry: Q5.

### C-6 — The consumer check watches bare leaf names

`verify/behavior.consumers` adds `change.subject.rsplit(".", 1)[-1]` for every removed *and*
renamed subject: 997 leaves on v22→v25, including `id`, `name`, `value`, `type`, `status`,
`customer_id`, `resource_name`, `start_date`, `end_date`. `_is_read_position` only excludes a
name immediately followed by `:`, which rejects a dict key and accepts a Python keyword
argument, so `service.search(customer_id=…)` is a hit.

**Class:** a check that matches on an unqualified name cannot tell a provider response field
from any identifier in the language.
**This narrows a verdict conjunct, so it is a proposal, not an edit** (approval boundary).
The qualified-subject watch is sound and stays: `_mock` fires correctly on
`campaigns.legacy`. The proposal concerns the bare-leaf watch only, and it must not become a
check that watches nothing (trap 5). Design in Q3; every corruption in `tests/adversarial`,
including the split-literal P0 and FA-019/FA-020, must still be rejected, proved by the
revert-check table rather than by assertion.

### C-7 — The frozen baseline runner is pytest, hard-wired

`verify/suites.py` discovers only top-level `tests|test|spec|__tests__` directories and only
`.py` test files, and `_argv` runs `python -m pytest`; `verify/coverage.py` is a pytest
plugin over `sys.monitoring`. Dub's suite is vitest under `apps/web/tests`. Result:
`NO_FROZEN_TESTS` → `frozen_baseline_tests_pass` false → FAILED regardless of the diff.

**Class:** the verifier has one runner wired in, so a repository with real tests it cannot
drive is judged as one with no tests.
**Fix:** a runner abstraction inside `verify/` — these are ecosystem facts, not provider
facts, so they stay out of `packs/` and out of the pack protocol. Discovery walks below the
root and reads the repository's own declaration (`package.json` `scripts.test`, vitest/jest
config). The repository's own runner is used and nothing is installed into the customer tree.
A suite whose runner is absent stays `NO_FROZEN_TESTS` and the receipt names the missing
runner — "no JS coverage" is never treated as covered (trap 6), and
`radius.blast`'s existing treatment of uncoverable modules as `UNKNOWN_BLAST` is kept.
Coverage source: Q4.

### C-8 — A method-less static request reaches the oracle

The static GAQL skeleton at `static:src/reporting.py:3` arrives with no service and no method
and returns `UNKNOWN_PROVIDER_CONTRACT` "validation requires a GoogleAdsService method and
request mapping", so `ORACLE_UNAVAILABLE` caps the verdict. The same fixture's `_mock`
equivalent passes, so this is Google-Ads-specific today.

**Reading: this is a discovery gap, not an oracle gap.** §9B validates *requests*. A query
string with no service and no method is not a request; it is a fragment. The structure
observer already resolves the skeleton and already knows the sink that consumes it, so
binding `QUERY` to the executing `service.search` call is discovery's work. Where the walk
cannot bind it, the correct result is a preserved UNKNOWN whose closing instruction is
"capture the executed request", not an oracle that accepts a method-less shape. Confirm at
Q6 before implementing; do not paper over it by accepting method-less requests.

### C-9 — `migrate` and `prepare-pr` do not agree on where the obligations file lives

`proof/memory.prepare` requires `<repo>/.hubbleops/obligations.json`; `hops migrate`
defaults to `<cwd>/.hubbleops/obligations.json` (`app/cli.py:51`, `:927`). Running `hops
migrate <repo>` from anywhere but inside `<repo>` breaks `prepare-pr`.

**Class:** a state directory that follows the current working directory rather than the
repository the verb names.
**Fix:** for every verb that takes a repository, the default state directory resolves against
that repository. This is a defaults change with no proof semantics attached; recorded here,
not raised as a proposal.

### C-10 — The PR body restates the receipt

`proof/pr_body.render` prints verdict, authority, SHAs, ProofScope, a blast table and the
receipt text. It has no P-017 discovery-completeness section, no obligation list, no P-013
grouped UNKNOWNs, and never shows `EXCLUDED_WITH_EVIDENCE` adjacent contracts. Phase 7 DoD 1
names discovery, obligations, oracle results, blast radius, falsifiers, UNKNOWNs with closing
instructions, and ProofScope hashes.

**Fix:** render the Exposure Map's discovery section and the obligation reconciliation table
as first-class sections; the receipt block stays as the appendix it already is, not as the
body's content (trap 10). Layout: Q9, an open middle rather than a blocker.

### C-11 — A removed subject becomes an obligation only at an AFFECTED candidate

Measured on phase5: `campaigns.legacy` at `reporting.py:3` is a carried UNKNOWN
(`request_text`), and `reporting.py:12` (`row["campaigns.legacy"]`) produces no candidate at
all. `obligations/engine._subject_drafts` runs only for `status == "AFFECTED"`, so neither
site gets an obligation, migrate leaves both, and the audit's independent source-extinction
scan — which reads *all* candidate source, not only AFFECTED sites — fails on both.

**Class:** the engine and the audit disagree about what must change. The audit's extinction
scan is the stricter and, under L1, the correct one.
**Consequence:** DoD 1 (phase5 → `VERIFIED_FOR_SCOPE` through the real verbs) is not
reachable without closing this. Q7 decides how. Emitting an obligation at an UNKNOWN
candidate does **not** change that candidate's status, so L2 and L3 are untouched; the
candidate stays UNKNOWN and conservation still carries it. `reporting.py:12` is exactly
P-025's response-read class, which is OPEN.

---

## Order of work

Tests first at every step; each step's evidence is a pasted command and its output.

1. **The failing end-to-end test** (DoD 1). `tests/integration`: copy `tests/fixtures/phase5`
   into a git repo, run `hops migrate`, commit, `hops verify --obligations <migrate's file>`,
   `hops prepare-pr`; assert `VERIFIED_FOR_SCOPE`, every obligation `DISCHARGED`, PR materials
   written. It fails on the current tree — the measurement above is what it will print.
2. **C-1, C-2** — identity. Then the same commit scanned two ways is one ProofScope and one
   run id (DoD 3).
3. **C-4** — one version reader. `--from` becomes unnecessary on both packs, and stays
   available.
4. **C-3** — one grammar, both sides, plus the audit headline. Then every obligation
   reconciles or names why it cannot.
5. **C-5** — edit sites. New anonymized fixture (fixture-writer) for the constant-hoisted
   literal; prove on Dub.
6. **C-11** — subject obligations beyond AFFECTED, per Q7.
7. **C-6** — consumer check, per the accepted proposal, with the revert-check table.
8. **C-7** — the runner abstraction and JS coverage, per Q4.
9. **C-8** — oracle mapping, per Q6.
10. **C-9, C-10** — paths and body.
11. Determinism triple, red-team, FAILURE_ATLAS rows and fixtures, commits.

`python_pinned_v22` is never the end-to-end proof (trap 7): it has no test suite, so it must
never reach `VERIFIED_FOR_SCOPE`, and a run in which it does is the bug. Its role is DoD 2 —
the three verbs by hand reaching a verdict whose only reasons are its own.

## Proposals this raises

- **P-027 — the response-consumer check must bind a leaf to a response read.** Narrows a
  verdict conjunct; approval boundary. Blocked on Q3.
- **P-028 — the frozen baseline suite is runner-plural.** Does not narrow a conjunct (it
  turns `NO_FROZEN_TESTS`-because-unsupported into a real result and leaves
  `NO_FROZEN_TESTS`-because-missing alone), but it adds a runner the verifier drives, which
  is an approval boundary on "any new dependency or runner". Blocked on Q4.
- **P-025** is already OPEN and C-11 depends on its shape.

Nothing here changes `core/schemas`, the `ProviderPack` protocol, or `verify/verdict.py`.
If Q5 is answered "a named field on the obligation", that becomes a third proposal under
`core/schemas` and an approval boundary of its own.

---

## PROGRESS — 2026-09-12

Landed, each with a test that fails when the fix is reverted:

| Class | State | Evidence |
|---|---|---|
| C-1 control metadata in the closure | done | `test_a_detached_worktree_and_a_working_tree_of_one_commit_share_a_closure` |
| C-2 scan identity | done | `test_one_commit_scanned_two_ways_is_one_proof_scope_and_one_run` (DoD 3) |
| C-3 one method grammar | done | `tests/unit/test_obligation_methods.py`, 13 tests (DoD 4) |
| C-4 one version reader | done | `--from` is no longer needed on either pack |
| C-5 edit sites | done | phase5 containment 4 hunks → 4 obligations, 0 unexplained |
| C-6 consumer check (P-027) | done | new `aliased_leaf_response_read` fixture; 17/17 adversarial |
| C-8 oracle sink binding | done | the pinned fixture's request now reaches a decision |
| C-9 obligations path | done | migrate and prepare-pr share one default and one override |
| C-11 subject obligations beyond AFFECTED | done | phase5 `MIGRATION AUDIT PASS` |
| — engine vs audit subject semantics | done | a `CHANGED` fact with no replacement no longer demands absence |

**DoD 1 met.** `tests/integration/test_chain.py` runs `hops migrate` → commit → `hops verify` →
`hops prepare-pr` on a copy of `tests/fixtures/phase5` and asserts `VERIFIED_FOR_SCOPE` with every
obligation `DISCHARGED`. Full evidence: `663 passed, 1 xfailed` across unit, property, adversarial
and the chain.

**DoD 2 in progress.** On `python_pinned_v22` the chain now reaches `UNKNOWN` with a clean audit
(`21 total · 21 resolved · 0 open · 0 unexplained`), `MIGRATION AUDIT PASS`, `response-consumer
check PASS`, and `UNKNOWN_BLAST src/reporting.py` — no reason names binding, reconciliation,
detection or a false consumer hit. What is left is two unscoped sets, raised as **P-029**.

Not started: C-7 (runner plurality, P-028), C-10 (PR body), DoD 8 (Dub), DoD 9 (determinism triple),
DoD 10 (atlas rows and fixtures), DoD 11 (commits), DoD 12 (red team).

Found and recorded, not fixed (out of this brief's scope, worth a fixture):
a version literal in a **default parameter value** (`def __init__(self, token, version="v1")`) is a
carrier no call-shaped regex sees, so `client.py:5` in the phase5 fixture is never observed and never
repaired. Discovery gap in pack data, not an engine gap.

## ANSWERED — 2026-09-12, repository owner (Jaya Krishna J)

- **Q1 → (a) refuse a dirty tree**, naming the files.
- **Q2 → `core/verification.py`, and every obligation reconciles.** A `PRESERVE_UNKNOWN`
  obligation reconciles through a conservation method, not by exemption.
- **Q3 → (b) with (c)**: gate the bare-leaf watch on provider evidence reaching the site, and
  fix the read-position test so a keyword argument or named parameter is not a read. The
  qualified-subject watch is unchanged. Ships as P-027.
- **Q4 → one aggregate `SuiteCase` per runner-suite.** Ships as P-028.
- **Q5 → (a) the edit site is named in `current_state`.** No `core/schemas` change.
- **Q6 → discovery binds the skeleton.** The oracle never validates a method-less shape.
- **Q7 → (a) emit a subject obligation wherever attached evidence names a removed subject**,
  whatever the candidate's status; the candidate's status is untouched.
- **Q8 → the layout as planned.**

## The questions as asked

**Q1 — `hops migrate`'s dirty-tree policy.** *(approval boundary)* Obligations are bound to
the scope of the tree that was scanned. If migrate scans a working tree that differs from
`HEAD`, that scope names no commit, and `hops verify --base HEAD` can never match it.
(a) Refuse a dirty tree, naming the files; (b) scan `HEAD` in a detached worktree and write
repairs to the working tree; (c) bind to `tree:<tree_hash>` and let verify accept a base whose
tree hash matches. Recommend **(a)** — it is the only one where the thing proved and the thing
committed are the same bytes, and it needs no new acceptance rule inside verify.

**Q2 — where the method grammar lives, and its members.** `core/verification.py` already holds
the neutral verification vocabulary by the Phase-5 decision, and both `obligations/` and
`verify/` may import `core/`. Recommend it there. Members: `absent:<subject>`,
`present:<subject>`, `version:<version>` exist; this work needs at least a dependency-pin
method for the SDK-bump obligation and a conservation method for the `PRESERVE_UNKNOWN`
carry, since both are emitted today and neither is executable. Confirm the home, and confirm
that a `PRESERVE_UNKNOWN` obligation reconciles against `verify/conserve.py` rather than being
exempt from reconciliation.

**Q3 — the consumer check's new shape** *(approval boundary, P-027)*. The qualified-subject
watch stays. For the bare leaf, which of: (a) watch a leaf only where the site is a
response-read claim (needs P-025 decided); (b) watch a leaf only where provider evidence
already reaches that file or that object; (c) keep the leaf watch and fix only the read-position
test so keyword arguments and named parameters are not reads; (d) drop the leaf watch and rely
on the qualified watch plus the falsifiers. Recommend **(b) with (c)**: (c) alone still fires
on `row["id"]`, and (d) alone loses FA-019's consumer coverage. (a) is the right long-run
shape and should be where this lands once P-025 is decided.

**Q4 — where JS coverage comes from** *(approval boundary, P-028)*. vitest and jest report
coverage for a run, not per test. Recommend one aggregate `SuiteCase` per runner-suite whose
`files` is the union of covered files and whose outcome is `passed` only if the whole suite
passed. This is sound because `frozen_baseline_tests_pass` is a conjunct: if any frozen test
fails the verdict is FAILED and the radius no longer decides anything, and if all pass the
union *is* the union over passing tests. The alternative — one runner invocation per test file
— is exact and O(N) slower. Confirm the aggregate is acceptable as proof, because it is a
proof-granularity decision, not an implementation detail.

**Q5 — how the edit site is carried on the obligation.** `obligation.json` is frozen with
`additionalProperties: false`, and `current_state` is described as "What is there now, at
file:line". (a) Name the edit site in `current_state` prose, which `verify/radius`'s `SITE`
regex already parses, with the observation site named alongside it; (b) add a frozen
`edit_site` field, which is a `core/schemas` change and its own proposal and approval
boundary. Recommend **(a)**: it needs no schema change, containment already reads it, and a
reviewer reads the same sentence the machine does.

**Q6 — who owns the method-less static request.** Confirm the reading in C-8 — discovery binds
the query skeleton to its executing call, and an unbindable skeleton stays a preserved UNKNOWN
— rather than teaching the oracle to validate a request with no service and no method.

**Q7 — does the engine emit a subject obligation at a non-AFFECTED candidate?** Required for
DoD 1. Today only AFFECTED candidates reach `_subject_drafts`, while the audit's extinction
scan reads all candidate source, so the two disagree and phase5 cannot reach
`VERIFIED_FOR_SCOPE` through the real verbs. Options: (a) emit a subject obligation wherever
attached evidence names a removed subject, whatever the candidate's status — the obligation's
`repair_class` still routes `UNKNOWN_PROVIDER_CONTRACT` to `PRESERVE_UNKNOWN`, and the
candidate's status is untouched; (b) keep the engine as it is and accept that a repository
with a removed subject at an UNKNOWN site can never be verified without a human decision;
(c) decide P-025 first so `reporting.py:12` has a claim, and revisit. Recommend **(a)**, which
is the reading that makes the engine and the audit agree on one definition of "must change",
with (c) landing afterwards for the response-read site specifically.

**Q8 — PR body layout.** Open middle, not a blocker. Intended order: verdict and authority;
ProofScope and SHAs; P-017 discovery completeness; discovery counts; obligations with their
reconciliation status; oracle results; blast-radius table; falsifiers; P-013 grouped UNKNOWNs
with closing instructions; `EXCLUDED_WITH_EVIDENCE` adjacent contracts; receipt as appendix.
Say so now if a different order is wanted.

---

## PART SEVEN — why the product reports "1 issue and hundreds of UNKNOWNs" (2026-09-12)

Measured on this tree (`66e5b75` plus the working set) against a fresh read-only scan of
`woocommerce/google-listings-and-ads` at `b43b322`, plus `hops impact` on the same tree, plus
direct calls into the pack. Suite evidence on the same bytes: unit + property + adversarial
**671 passed, 1 xfailed** in 5m14s. Scan: 1,364 candidates, 249 AFFECTED, 672 UNKNOWN,
306 UNSUPPORTED, 26 UNSCANNED, 110 NOT_AFFECTED, 0 unexplained, 2m10s.

### The root cause, in order of blast radius

**R1 — 47% of the field catalog declares itself undecidable, and every layer downstream inherits it.**
`packs/google_ads/changes.py:_reconcile_field` marks a field `UNKNOWN_PROVIDER_CONTRACT` unless it
has *exactly one* proto record and *exactly one* Query Builder record. The proto projection carries
1,728 field records against the Query Builder's 2,995, so **1,407 of 2,995 v25 fields** (all 337
`metrics.*`, all 166 `segments.*`, 904 attributes including `customer.id`) are "conflicted" when
nothing conflicts — the second source is merely absent. Measured consequences:

- Oracle: `SELECT customer.id FROM customer` → `UNKNOWN_PROVIDER_CONTRACT: field customer.id has
  conflicting provider sources`. Any query naming a metric or segment cannot be accepted, so
  `oracle_all_accepted` is unreachable for a real report query.
- Diff: v23→v24 has 1,238 `CHANGED/UNKNOWN_PROVIDER_CONTRACT` facts and **1,086 of them have
  byte-identical before/after attributes**. v22→v25: 1,181 of 1,346 CHANGED are UNKNOWN.
- Obligations: `hops impact` on the real repository emitted 21 subject obligations, every one
  `PRESERVE_UNKNOWN`, reading "resolve what replaces `metrics.clicks` / `segments.date` /
  `campaign.advertising_channel_type` in v25". None of those fields changed. This is a false
  alarm presented as a migration finding.
- Repair: **0** REMOVED facts carry a `replacement` (the 38 `upgrade_guidance` facts are proto
  file topics, not subject mappings), so no subject removal can ever be DETERMINISTIC.

**R2 — the scan never consults the target contract.** `scan_repository` takes no target;
AFFECTED means "this line carries a version literal or an SDK pin". "You select
`campaign.start_date`, removed in v23" exists only as an obligation inside `migrate`/`impact`
and as an audit failure inside `verify`. The report a user reads first cannot list a single
contract-level finding. This is the unit mismatch behind "AI found 4, we found 1": an AI's
unit is a migration finding; the Exposure Map's unit is a version-carrying line.

**R3 — counting unit is the line, not the finding.** 229 of 249 AFFECTED are
`use Google\Ads\GoogleAds\V23\...` statements in 38 files: one finding ("this repository is on
V23") printed 229 times. The 672 UNKNOWNs sit on 449 locations: 194 are one `google-ads` token
claimed twice (FA-035, undecided); 85 are in `.md`, 32 in `.lock`, 16 in `.scss`, 20 in `.json`;
290 say "resolve the version at this call site" for identifiers in files whose own imports
declare V23; 69 are the first-party `GoogleAdsClient` wrapper (FA-039); 29 are the
`ads_failure_detail` shape matching the bare word `errorCode` in JavaScript. All 229
`call_version` records agree on V23 and the map never says so; its header reads
`Target UNKNOWN (SDK compatibility unresolved)` because the composer pin `dev-legacy-v32.1.0`
maps to no API version (FA-009).

**R4 — `impact` reports 474 "affected paths", 301 of them images, JSON mocks and markdown.**
1,004 of its 1,274 obligations are carried UNKNOWN/UNSUPPORTED/UNSCANNED candidates, and their
paths are rendered as affected.

**R5 — `verify` cannot reach VERIFIED_FOR_SCOPE on any PHP or JavaScript repository.** The
frozen suite runner is pytest only (C-7 / P-028, not started), so a PHPUnit or vitest suite is
`NO_FROZEN_TESTS` and the verdict is FAILED regardless of the diff. The chain is proven end to
end only on the `_mock` fixture.

### What a correct report for this repository says

Effective version V23 (229 import sites, 38 files; composer pins `googleads/google-ads-php`
`dev-legacy-v32.1.0`; v25 requires PHP client ≥ 33.6.0; v25 sunsets 2027-08). Findings:
(1) SDK pin below the v25 floor — deterministic; (2) 229 namespace imports V23→V25 —
deterministic; (3) fields used against the v23→v25 diff: 0 removed fields in use, N changed
fields listed with what changed; (4) 187 GAQL sites, each validated against the v25 catalog or
carrying a real reason; (5) residual UNKNOWNs grouped by what closes them, with the 4 genuinely
runtime-only sites named. Then `migrate` performs (1) and (2), and `verify` runs the frozen
PHPUnit suite and the oracle over every query.

### Programme (root cause first; each step lands with a test that fails when reverted)

- **F1 catalog reconciliation.** A field with a complete Query Builder record and no proto
  projection is `RESOLVED` at `DOCUMENTED` confidence; `PROVEN` when the proto corroborates;
  `UNKNOWN_PROVIDER_CONTRACT` only when two present sources disagree. Fix the proto projection
  for `common/metrics.proto` and `common/segments.proto` so they corroborate. Rebuild catalogs
  (lattice hash moves; every proof re-scopes, which is correct). Expected: unresolved fields
  1,407 → ~140 (the real type disagreements); oracle accepts metric and segment queries;
  v23→v24 CHANGED/UNKNOWN 1,238 → ~150.
- **F2 diff invariant.** A subject whose attributes are equal across versions is never
  CHANGED. Property test over every adjacent pair.
- **F3 replacements.** Parse the upgrade guide's subject-level renames and replacements into
  `replacement` on REMOVED facts. Removals with no documented replacement stay HUMAN.
- **F4 target-aware Exposure Map.** `hops scan` and `hops exposure` take `--target` (default:
  pack latest). New section MIGRATION FINDINGS computed from the ledger and the change set,
  grouped by finding with site counts: effective version per repository and per file, SDK pin
  versus floor, subjects in use that are REMOVED or CHANGED, query validation results, sunset.
  The ledger stays target-free; only the rendering reads the change set. AFFECTED lines stay in
  the ledger (L1) and render as "V23 namespace import · 229 sites · 38 files".
- **F5 UNKNOWN volume, by mechanism.** Decide P-024 (one observation, one candidate; removes
  the 194 double claims). Non-code carriers by role, as DATA already is: `DOCUMENTATION`,
  stylesheet and lock-file matches resolve `NOT_AFFECTED_WITH_EVIDENCE` naming the role, with a
  documentation-drift count as Q21 decided for comments. File-level version binding: an
  identifier in a file whose only version evidence is a single version resolves to that version
  through the P-023 binding path, with the import line as evidence. Tighten
  `ads_failure_detail` to a qualified shape.
- **F6 impact and migrate rendering.** Affected paths = paths of DETERMINISTIC and HUMAN
  obligations. Carried UNKNOWNs render through the P-013 grouping, never as affected paths.
- **F7 runner plurality (P-028).** PHPUnit and vitest/jest as frozen-suite runners per Q4.
- **F8 held-out proof.** Run scan → exposure → migrate → verify → prepare-pr on
  `google-listings-and-ads` and `dubinc/dub`; gate on zero subject obligations for unchanged
  fields, every GAQL site accepted or carrying a named reason, and a Receipt on both.

### ANSWERED — 2026-09-12, repository owner (Jaya Krishna J), by instruction to "fix all of them now"

- **Q25 → yes.** A field resolved by one complete source is RESOLVED at DOCUMENTED confidence;
  a conflict needs two present sources that disagree. P-031 raised for the field service.
- **Q26 → target-aware rendering over a target-free ledger.** `--target` on scan and exposure;
  nothing target-specific persisted in the ledger or ProofScope.
- **Q27 → P-024 ACCEPTED.** One observation, one candidate; the real-repo baseline is re-frozen
  under the new identity with permitted-disposition sets preserved.
- **Q28 → NOT_AFFECTED_WITH_EVIDENCE with a role reason.** No new status; documentation,
  stylesheet and lock-file matches resolve by role. P-030 raised for precise indexers; the AI
  edit/triage route stays closed under P-009 until the attestation boundary is decided.

### PROGRESS — 2026-09-12, same day

All of F1-F8 landed; the per-item evidence is in `dev/tasks.md` and the atlas rows FA-049 to
FA-053. What a later session must not undo: absence of a catalog source is never a conflict; a
CHANGED fact never has equal before/after; replacements come only from rows whose both sides
resolve uniquely, with the rest counted; the ledger stays target-free while the map reads the
target; one observation is one candidate; file-level version binding requires a structural
adjudication of the site; a declared-but-missing test runner sinks the merged suite. Left open:
bound-reference obligations (`EFFECTIVE_VERSION_UNRESOLVED` for references whose version comes
from the file), P-030, P-031, and running `hops verify` on the two real repositories with their
dependencies installed in the sandbox.

### Open questions for the owner (approval boundaries), as asked

- **Q25** F1 changes what CATALOG authority means: a field resolved by one complete source,
  corroborated by proto where the projection exists. P-020 made CATALOG acceptance depend on
  "every field resolves"; this is the definition of "resolves". Accept?
- **Q26** F4 gives `scan` a target. The ledger and ProofScope are unchanged; the run id already
  carries provider, verb and identity. Confirm that a target-aware rendering over a
  target-free ledger is the intended shape, rather than a target-bound ledger.
- **Q27** P-024 claim identity (FA-035) — needed for F5; it changes candidate ids, so the frozen
  baseline must be re-frozen under the new identity.
- **Q28** Non-code carriers: is `NOT_AFFECTED_WITH_EVIDENCE` with a role reason the right
  disposition for documentation, stylesheet and lock-file matches, or does this need a status
  of its own (frozen `candidate.json` enum, so a proposal)?

## PART EIGHT — Tier 3a: ship path and the first real Receipt (2026-09-12)

### Outcome

A customer goes from `git clone` to a target-aware Exposure Map in under five minutes with
Python 3.12 and git only, and `google-listings-and-ads` produces a Receipt whose every
conjunct is decided and whose `receipt_body_hash` repeats across two runs.

### Ground truth measured this session

Tree: `phase-06-repository-intelligence` at `66e5b75` plus the uncommitted F1-F8 working set.
Both prospect checkouts are clean at their pinned commits (`git status --short` empty).

```
uv run hops scan .hubbleops/artifacts/phase7-real-repos/dub --pack google_ads --target v25
  Commit b8866f413cec065438d6e5faabbd9dac7d1ceea5   ProofScope ps_43036ba3
  candidates 326 · affected 2 · not affected 4 · excluded 3 · unsupported 207 · unscanned 7
  unknown 103 · unexplained 0 · evidence 370                      real 1m39.150s
uv run hops scan .hubbleops/artifacts/phase7-real-repos/google-listings-and-ads --pack google_ads --target v25
  Commit b43b322771071ed88d5a817422dd222acbaa5f33   ProofScope ps_8915f415
  candidates 1143 · affected 259 · not affected 193 · excluded 1 · unsupported 306 · unscanned 26
  unknown 358 · unexplained 0 · evidence 1371                     real 1m34.210s
uv run hops migrate <dub> --pack google_ads --target v25 --dry-run
  obligations 319 · discharged 1 · open for a human 1 · files that would change 0
  NO_TRANSFORM apps/web/lib/integrations/google-ads/api.ts (line 60 is a template with the
  hole GOOGLE_ADS_API_VERSION; the v22 literal is written at constants.ts:20)
uv run hops migrate <google-listings-and-ads> --pack google_ads --target v25 --dry-run
  obligations 950 · discharged 0 · open for a human 259 · files that would change 0
  NO_TRANSFORM on all 258 version:v23->v25 sites and on composer.lock
```

Tool state on this machine: `ripgrep 14.1.1 (rev 4649aa9700)`, `ast-grep 0.45.0`, `uv 0.12.9`,
`uv run python` 3.12.14 (PATH python is 3.11.9), `podman 4.9.3` via WSL, `docker 29.7.2`,
`node v22.12.0`, `pnpm 10.33.4`, **no `php`, no `composer`**. Neither prospect has its
dependencies installed (`vendor/`, `node_modules/` absent). The pinned release assets all answer
200 except `ast-grep app-x86_64-unknown-linux-musl.zip` (404; the gnu build is used).

### What the ground truth says the chain is missing

- **M1 — the deterministic repair claims nothing on either real repository.**
  `VersionLiteralTransform` matches `\bv23\b` case-sensitively; the PHP namespace carrier is
  `Google\Ads\GoogleAds\V23\...`, so all 258 sites are `NO_TRANSFORM` and `hops migrate` writes
  no file. §3.1 names "namespace rename" as a pack transform; it does not exist. On Dub the edit
  is attempted at the template line `api.ts:60` although the structural walk already found the
  literal at `constants.ts:20` (evidence value `paths[2].literal = "v22"`).
- **M2 — the frozen suite runs on the host, not in the verifier image.** `verify/suites.py`
  executes the repository's own runner through `bounded_process`; the container is fingerprinted
  into the ProofScope and proved isolated, but the tests never enter it. Dependencies come only
  from the customer checkout (`runners.link_dependencies`). Nothing installs anything, by P-028.
- **M3 — `exposure.findings` validates with `pack.verification_contract()`**, which builds a
  transport from the environment; with credentials in the environment a map rendering would go
  to the network. Scan and exposure must use the offline `pack.contract` (catalog authority).
- **M4 — `.tsx` files are counted as structurally supported and never parsed.** `.tsx` maps to
  `typescript` and ast-grep is forced `-l typescript`; on `page-client.tsx` a forced typescript
  run returns 0 import matches and `-l tsx` returns 26. Every recall match in a `.tsx` file is
  therefore unadjudicated while coverage reports it supported. New atlas row.
- **M5 — three GLNA request-site classes never reach the oracle** (32 evidence records):
  GAQL built by the first-party fluent builder `AdsQuery`/`AdsReportQuery` (`->columns()`,
  `->from()`, `->where()`), the Merchant API client (`src/API/Google/Mapi/MerchantApiClient.php`,
  a different Google API), and `metrics.*` anchors in JavaScript adapters. Each becomes an
  `oracle undecided`/`never reached the oracle` unresolved entry, so the verdict is UNKNOWN
  whatever the diff says.
- **M6 — without a PHP interpreter the frozen PHPUnit suite is `TOOLING_MISSING`**, which
  `radius.frozen_report` renders unresolved (verdict UNKNOWN). Correct, and it means DoD 5 cannot
  be met on this machine until Q29 is answered.

### Dub's 103 UNKNOWNs by mechanism (from the ledger export, every site listed in the scratch log)

| # | mechanism | sites | disposition after this tier |
|---|---|---|---|
| a | `google-ads` inside an import specifier the structural layer resolved to a first-party file (`binding_target` in-repo) | 22 | NOT_AFFECTED_WITH_EVIDENCE naming the resolved module |
| b | the same in `.tsx` files, unadjudicated because of M4 | 2 | as (a) once tsx parses |
| c | `customers/…`, `conversionActions/…`, `FROM clickevent`, `FROM tinybird` in Stripe/Shopify/test files whose only provider context is a first-party import (`context_paths` grows through imports) | 17 | NOT_AFFECTED_WITH_EVIDENCE: no provider context in the same file |
| d | credential keys `GOOGLE_ADS_DEVELOPER_TOKEN`/`CLIENT_ID`/`CLIENT_SECRET` read via `process.env` or declared in `.env.example` | 9 | NOT_AFFECTED_WITH_EVIDENCE: configuration, never a version |
| e | `ADS_API_VERSION` matched as a substring of `GOOGLE_ADS_API_VERSION` | 3 | retired by identity migration into the enclosing key's candidate |
| f | `GOOGLE_ADS_API_VERSION` at its definition `= "v22"`, its import, and its use in the URL template | 3 | definition → AFFECTED v22 (the true edit site); import → first-party; use → explained by the structural resolution on the same line |
| g | `fetch`/`post` sinks in Shopify Playwright helpers with no provider context in the file or on the wrapper chain | 13 | NOT_AFFECTED_WITH_EVIDENCE: generic HTTP sink whose skeleton and chain name no provider surface |
| h | `CONTRACT_VALIDATION_DEFERRED` skeletons at `api.ts:274/375/420` | 3 | judged by the offline oracle across the lattice → AFFECTED with accepting versions |
| i | text recall on a line the structural layer resolved (the three query lines; host + `googleads` on `api.ts:60`) | 5 | NOT_AFFECTED_WITH_EVIDENCE naming the structural candidate |
| j | `google-ads` as a route path, slug, redis key or logger key inside a string literal | 14 | stays UNKNOWN (no observer proves a string inert; closing instruction: recorded decision) |
| k | test fixture `type.googleapis.com/google.ads.googleads.v22.errors.GoogleAdsFailure` (5 candidates on one line) and `'login-customer-id'` in the same test | 6 | stays UNKNOWN (a v22 artifact in test data) |
| l | contract surfaces in provider-context files (`"developer-token"`, `googleAds:searchStream`, `customers:listAccessibleCustomers`, auth scopes, `errorCode?.authorizationError`, `googleAds:oauth:refresh`, `customers/${…}/conversionActions`) | 8 | stays UNKNOWN (CONTRACT_SURFACE_UNRESOLVED; binding these to catalog subjects is later work) |

Expected: 103 → 28 if (g) holds, 41 if it does not; the DoD bound is 40, so (g) is in scope.
No status is forced: every closure names the evidence (a resolved import target, the file's
context inventory, an environment-read node, an oracle result at CATALOG authority) or a role.

### Decisions taken in this plan (not questions)

- `config_env_keys` means keys whose value can select the API version (§3.1 lists env/config
  keys under *Version carriers*). Credential keys move to `identifiers`. A new carrier
  `config_literal_version` (`(GOOGLE_ADS_API_VERSION|GOOGLE_ADS_VERSION|ADS_API_VERSION)\s*[=:]\s*["']?(?P<version>v\d+)`,
  slot `config`, languages any) makes `constants.ts:20` the AFFECTED site it is. Pack data only.
- Config-key and identifier patterns match on identifier boundaries. The three substring
  candidates retire through the baseline's `identity_migration`, carried by the enclosing key.
- The Merchant API (`merchantapi.googleapis.com`, `shoppingcontent.googleapis.com`) is declared
  an adjacent contract of the Google Ads pack, like Data Manager. Pack data; a Google API with
  its own lattice, not a repository-specific rule.
- Dub's residue candidates in import-only-context files are still raised and then explained,
  never dropped, so the frozen baseline's "absent is a violation" holds.
- Toolchain binaries ship inside platform wheels under `hubbleops/_toolchain/<platform>/`; the
  runtime manifest carries name, version and sha256 only. Download URLs live in a build-time file
  at the repository root, so no generic module carries a hostname. Lookup order: vendored binary
  for the running platform (sha256 verified, mismatch fails closed as `TOOLING_MISSING`), then
  PATH (sha256 of whatever ran is recorded). `scanner_version` binds `rg=<version>+sha256:<hex>`
  and `ast-grep=<version>+sha256:<hex>` either way. `uvx hubbleops` needs a console script
  named `hubbleops`; it is added beside `hops`, same entry point.
- The Exposure Action is generated by `hops exposure --install-workflow --pack <p> --target <t>`,
  writes `.github/workflows/hubbleops-exposure.yml`, runs on `pull_request` with
  `permissions: contents: read`, installs nothing but uv, runs `uvx --from "<source>" hops scan .`
  then `hops exposure`, prints the map to the job log and uploads `ledger.json` and the map as
  an artifact. `<source>` defaults to `hubbleops==<__version__>` and is overridable with
  `--source`; publication to an index is not performed here (approval boundary).
- Per-query oracle judgement has two halves and both are offline: the resolver judges every
  resolved skeleton against every lattice version with `pack.contract` (target-free, bound by
  `provider_contract_hash`) and resolves the candidate AFFECTED with the accepting versions or
  UNKNOWN with the rejecting reason; the obligation engine judges against the target and the map
  renders one line per query. `hops scan --target` prints the MIGRATION FINDINGS and QUERIES
  blocks at the end of the scan, so the judgement is visible during the scan while the ledger
  stays target-free (Q26).
- Sandbox dependency provisioning is proposed as P-032 and not executed without the owner's yes.

### Ordered work (each item lands with a test that fails when the mechanism is reverted)

**W0 — freeze Dub's baseline before touching the scanner.**
`tests/fixtures/real_repo/dub_unknowns_before.json` in the GLNA fixture's shape (repo_sha,
scanner, proof_scope_hash, ledger_counts, 103 entries with root cause and permitted set).
`tests/unit/test_real_repo_baseline.py` parameterised over both fixtures. After every scanner
change the conservation script runs over the fresh export and is pasted.

**W1 — vendored toolchain (DoD 1).** Owner: subagent A.
- `toolchain.json` (root, build-time): per tool × platform: url, archive sha256, member, binary sha256.
- `hatch_build.py` (root): hatchling custom hook; `HUBBLEOPS_WHEEL_PLATFORM` ∈ {`win_amd64`,
  `manylinux_2_35_x86_64`, `macosx_11_0_arm64`, `macosx_10_12_x86_64`} selects the tag; archives
  are fetched into `HUBBLEOPS_TOOLCHAIN_CACHE` (default `.hubbleops/toolchain-cache/`), both
  hashes verified, binaries force-included at `hubbleops/_toolchain/<tag>/` with a `manifest.json`;
  unset → today's pure wheel. `pyproject.toml`: the hook, the `hubbleops` script.
- `hubbleops/core/toolchain.py`: `platform_tag()`, `locate(tool)` → `ToolBinary(path, origin,
  sha256)`, `identity(tool, version_text)`. `observe/text.py` and `graph/imports.py` call it;
  `app/cli.scan_repository` binds the identities into `scanner_version`.
- Tests: `tests/unit/test_toolchain.py` (vendored preferred over PATH; hash mismatch fails
  closed; PATH fallback records the hash; platform tag mapping), `tests/integration/test_cli.py`
  (`scanner_version` carries `+sha256:` for both tools).
- Evidence: `uv build --wheel` for the current platform, `unzip -l` of the wheel, and the timed
  cold path on both prospects (`git clone` → `uv tool install <wheel>` → scan → exposure) with
  PATH stripped of `rg` and `ast-grep`.

**W2 — Exposure Action (DoD 2).** Owner: subagent B.
`hubbleops/proof/exposure_workflow.py` (`workflow(provider, target, source)` and `install`),
`hops exposure --install-workflow --pack --target [--source] [--repo]` in `app/cli.py`. Tests in
`tests/unit/test_phase7_delivery.py`: `contents: read` only, `pull_request` trigger, pinned
source, no secret reaches the file, YAML-injection refusal as `memory.workflow` already has.

**W3 — per-query oracle judgement (DoD 3).** Owner: coordinator.
- `app/cli.scan_repository`: `validations = contract.validate(skeleton, v)` for every resolved
  structural skeleton and every lattice version, with `pack.contract` (offline); passed to
  `ledger.build(..., validations=)` as a plain mapping keyed by evidence id. `observe/ledger.py`
  hands it to `resolve_claim` like `roles` and `path_versions`; `_request_text` resolves
  CONTRACT_VALIDATION_DEFERRED → AFFECTED "GAQL over `customer_client` accepted at CATALOG
  authority in v19…v25" or UNKNOWN naming the rejecting field.
- `app/exposure.py`: `findings()` uses `pack.contract` (M3) and gains `queries`; new block
  `QUERIES  → v25` with one line per query: `path:line  ACCEPTED (CATALOG)` /
  `REJECTED <reason>` / `UNDECIDED <reason>` / `HOLE <names>` and the totals line.
  `hops scan --target` renders MIGRATION FINDINGS and QUERIES after the summary.
- Tests: `tests/unit/test_exposure.py` (one line per query, totals add up, byte-identical on two
  renders, no network transport is ever constructed: the oracle is `pack.contract`),
  `tests/unit/test_resolver.py` (accepted → AFFECTED with versions; rejected → UNKNOWN naming the
  field; a hole never reaches the oracle).

**W4 — Dub residue (DoD 4).** Owner: subagent C for 4a–4c (observe/text.py, graph/imports.py,
observe/structure.py, pack data), coordinator for 4d–4g (observe/resolver.py, engine edit site).
- 4a M4: `.tsx` → language `tsx`; `AST_GREP_LANGUAGES["tsx"] = "tsx"`; pack ships `rules/tsx.yml`
  (`language: Tsx`, otherwise the TypeScript rule) plus `rules/tests/tsx-test.yml`;
  `SUPPORTED_LANGUAGES`, capture hooks and runner language sets include `tsx`. Atlas FA-054.
- 4b first-party import: `_adjudicated_reference` with `node_kind == import` and an in-repo
  `binding_target` and no `binding_versions` → NOT_AFFECTED_WITH_EVIDENCE.
- 4c direct provider context: the text observer records `context_scope` on every gated
  observation (`direct` when the file itself carries a context hit on a line that is not an
  import resolving in-repo; `imported` otherwise); `context_paths` no longer grows through
  imports for gating, but every previously raised candidate is still raised. The resolver
  explains `imported`-scope `contract_surface` and `request_resource` matches.
- 4d config keys: pack data as decided above; structure adjudicates environment reads
  (`process.env.X`, `os.environ[...]`, `getenv(...)`, `$_ENV[...]`) as node kind
  `environment_read`; resolver: key ∈ `config_env_keys` → UNKNOWN runtime config (unchanged);
  otherwise NOT_AFFECTED_WITH_EVIDENCE "configuration, never a version". CONFIG-role
  declarations of non-version keys resolve by role.
- 4e generic sinks: the resolver receives the per-file direct-context set; a structural
  `request_text` whose skeleton fragments name no provider host, carrier or request-language
  shape, in a file without direct context, on a chain whose files have none, resolves
  NOT_AFFECTED_WITH_EVIDENCE listing what was checked.
- 4f structural coverage of a text line: a text `request_text`/`endpoint_reference`/
  `surface_reference` candidate on a line where a structural candidate resolved a version or a
  skeleton is explained by that candidate's id.
- 4g edit site: `_version_drafts` names the literal's site from `paths[].literal` when the
  carrier is computed; `transform_requests` edits there.
- Tests: `tests/unit/test_unknown_mechanisms.py` one test per mechanism, each asserting the
  before/after resolution on a fixture line; `tests/property/test_adjudication_invariants.py`
  gains "absence of adjudication is never a disposition" for every new branch;
  `tests/fixtures/coverage/tsx_import/` for 4a with its expected candidates.
- Evidence: fresh scan of both prospects, counts pasted, conservation over both baselines
  (`glna_unknowns_before.json` with permitted sets intersected, `dub_unknowns_before.json`),
  and every AFFECTED candidate preserved.

**W5 — repair chain and the Receipt (DoD 5).** Owner: coordinator.
- `packs/google_ads/repairs.py`: `VersionLiteralTransform` becomes case-preserving on the
  letter (`V23` → `V25`, `v23` → `v25`) and declines when the line's token case does not match
  the carrier's; `SdkPinTransform` declines non-semver pins (`dev-legacy-v32.1.0` stays HUMAN).
  Tests in `tests/unit/test_deterministic_repair.py`.
- `dev/proposals.md` P-032 — sandbox dependency provisioning for verify: the suite runs in the
  staged workspace copy, never the customer tree; provisioning is a separate verb step before
  verify that runs the ecosystem's own installer against a pre-populated offline cache
  (`composer install --no-dev` from a mirrored `~/.composer/cache`, `pnpm install
  --frozen-lockfile --offline` from a pnpm store) or, when the owner allows it, against an
  allowlisted registry set through the existing egress proxy, recording lockfile hash, installer
  identity and every fetched package hash into `verification_inputs_hash`. Verify itself never
  opens a socket; a provisioning transcript is an input like a capture manifest.
- `verify/oracle.py`: a request site whose candidate carries a recorded human decision is listed
  under decisions, not under `unreachable` (L3's decision channel). `verify/verdict.py` untouched.
- Then, only after Q29 and Q30 are answered: `hops migrate` → commit in a scratch clone →
  `hops verify` → `hops prepare-pr` on `google-listings-and-ads`, twice, with both
  `receipt_body_hash` values pasted.

**W6 — five-minute integration test (DoD 6).** `tests/integration/test_ship_path.py`: builds
the current-platform wheel with the toolchain cache, installs it with `uv tool install` into a
temporary `UV_TOOL_DIR`, strips `rg` and `ast-grep` from PATH, runs `hops scan` on
`tests/fixtures/phase1/monorepo_workspace/repo` with `--pack google_ads --target v25` and
`hops exposure`, asserts the wall clock under 300 s, the `+sha256:` identities, and the QUERIES
block. A cold toolchain cache needs the network once, at build time, never at scan time.

**W7 — gate.** Three consecutive byte-identical scans of one fixture; the revert check per
mechanism (revert the file, run its test, expect failure, restore); full suite on a quiet tree;
ruff, pyright strict, `hops pack verify google_ads`; spec-auditor and red-team with two new
corruptions (`vendored_binary_swapped`: a wheel whose `rg` hash mismatches its manifest must fail
closed; `decided_request_site_forged`: a decision on a request site recorded under a foreign run
must not count as reached); atlas rows; `dev/context.md` and `dev/tasks.md`.

### PROGRESS — 2026-09-13, on the owner's instruction to build

The owner answered the three questions with "do whatever is best for customers and the product".
Q29: no PHP was installed; GLNA's PHPUnit bootstrap needs the WordPress test library and a
database, which no installer provides, so the frozen-suite conjunct stays unresolved here and
P-032 says where such suites run. Q30: the decision channel landed in `verify/oracle.py`
(a request site closed by a recorded decision is `decided`, never `unreachable`); builder-skeleton
extraction is not built. Q31: reading one, tightened to files with no provider link at all,
direct or imported, on the whole chain.

Landed: W0 (`dub_unknowns_before.json`, 100 entries, 3 retired by identifier-boundary keys, 6
re-keyed when credentials became identifiers), W1 (four platform wheels, real hashes,
`scanner_version` binds `rg=…+sha256:` and `ast-grep=…+sha256:`, a swapped binary stops the scan),
W2 (`hops exposure --install-workflow`), W3 (lattice judgement in the resolver, QUERIES block,
offline contract everywhere), W4 (all eight mechanisms plus FA-054 tsx parsing and `.env*` as
CONFIG), W5 (case-preserving namespace transform, non-semver pins stay HUMAN, bound references
edit their import line, an obligation whose required state already holds is SATISFIED, P-032
proposed), W6 (`tests/integration/test_ship_path.py`, 35 s on win_amd64).

Measured on the final bytes: Dub 326 → 327 candidates (one React file now honestly UNSCANNED),
UNKNOWN **103 → 32**, three consecutive scans byte-identical, 0 unexplained, 0 baseline
violations, one AFFECTED became NOT_AFFECTED by design (a first-party import line the file-level
binding had borrowed a version for). GLNA 1,143 candidates, UNKNOWN **358 → 333**, all 259
AFFECTED preserved, the one baseline violation (`AdsMissingEuDeclarationQuery.php:31`) was
already absent before this session. Four GLNA Merchant API sinks frozen RUNTIME_ONLY were
relabelled FA-058 on the new file-context evidence, recorded in the fixture rather than widened
silently. Migrate on GLNA: 229 of 258 namespace sites discharged before the SATISFIED and
bound-site fixes; the chain result on the final bytes is in `dev/context.md`.

### OPEN QUESTIONS (answered by delegation on 2026-09-13; kept for the record)

- **Q29 — the PHP toolchain for the GLNA frozen suite.** This machine has no `php` and no
  `composer`, so the frozen PHPUnit suite is `TOOLING_MISSING`, `frozen_baseline_tests` is
  unresolved and the verdict is UNKNOWN. Installing PHP 8 and composer (host or a PHP verifier
  image) is a new binary, an approval boundary. (a) Approve PHP 8.3 + composer on this machine;
  the suite then runs in the staged workspace copy under P-032 with `vendor/` provisioned from
  an offline composer cache. (b) Accept an UNKNOWN Receipt with the conjunct named, and DoD 5's
  "every conjunct decided" is deferred. (c) Build a PHP verifier image (a second pinned image
  under `sandbox/verifier_image.py`). Recommendation: (a) now, (c) when the Action must run it.
- **Q30 — the 32 GLNA request sites the oracle never reaches.** (a) Build fluent-builder
  skeleton extraction: the pack declares builder method names (`columns`/`select` → fields,
  `from` → resource, `where`/`order_by` → fields) and the structure observer composes a skeleton
  from literal arguments along the chain, holes for the rest; the oracle then judges them like
  any skeleton. Generic, honest, about two days, and it is what closes this class on every
  builder-style repository. (b) The owner records a decision per site with `hops decide`, and
  verify treats a decided site as reached (the W5 change). (c) Accept an UNKNOWN Receipt.
  Recommendation: (a), with (b) available for the residue (a) cannot resolve. Without one of
  these, no Receipt on this repository can have `oracle_all_accepted` decided.
- **Q31 — Dub's Shopify HTTP sinks (mechanism g).** Reading one: a generic-verb sink with no
  provider surface in its skeleton and no direct provider context in the file or on its chain is
  NOT_AFFECTED_WITH_EVIDENCE (the plan's reading; Dub lands at 28). Reading two: any structural
  sink match stays UNKNOWN until captured, because a hole could hide the host (Dub lands at 41,
  above the DoD bound). The two readings differ on whether "no provider context anywhere on the
  chain" is evidence. Recommendation: reading one, with the checked chain listed in the reason.

## PART NINE — Tier 3b: precise indexers behind the graph (2026-09-13)

### Outcome

The structure observer resolves callers, import targets and identifier bindings by **symbol**
wherever a proof-bound precise index covers the file, by **name** everywhere else, and the map
says which. A file with no precise index keeps its recall claims exactly as today and the
coverage block names the indexer that would change that. Nothing pretends; search stays the net.

### Ground truth on this tree before any change (2026-09-13, quiet tree)

```
uv run hops scan .hubbleops/artifacts/phase7-real-repos/google-listings-and-ads --pack google_ads
  Commit b43b322771071ed88d5a817422dd222acbaa5f33   ProofScope ps_73400f54   real 1m33.467s
  candidates 1143 · affected 259 · not affected 218 · excluded 1 · unsupported 306 · unscanned 26
  unknown 333 · unexplained 0 · evidence 1371
  UNKNOWN by file type: php 206 · js 112 · json 12 · sh 3
uv run hops scan .hubbleops/artifacts/phase7-real-repos/dub --pack google_ads
  Commit b8866f413cec065438d6e5faabbd9dac7d1ceea5   ProofScope ps_a66c7e73
  candidates 327 · affected 4 · not affected 73 · excluded 3 · unsupported 207 · unscanned 8
  unknown 32 · unexplained 0 · evidence 376
  UNKNOWN by file type: ts 31 · tsx 1
```

The prompt's figures (358 and 103) predate Tier 3a; these are the numbers every after-count
compares against. Dub's 32 are: 17 provider-name string slugs (URL paths, redis keys — UNKNOWN by
design, PART EIGHT), 11 unbound contract surfaces, 2 `GOOGLE_ADS_API_VERSION` reads (the import
at `api.ts:2` and the literal definition at `constants.ts:20`), 1 oracle-undecided query, 1 wire
namespace in a test fixture. GLNA's 112 JavaScript UNKNOWNs are 'google-ads' slugs in test data,
`item.metrics.conversions` property reads matched as GAQL anchors, and package names in strings.
No precise index decides a string literal, so the honest expectation is: UNKNOWN does not rise,
a handful of symbol-bound sites close, and the gain is precision of what is already claimed —
exact callers instead of name-and-arity, exact import targets across barrels and aliases,
first-party versus package bindings named by symbol — plus the map's per-language truth.

### SPIKE — measured, dependencies never installed in any repository

Indexers pinned in the scratchpad only (nothing entered the tree): `@sourcegraph/scip-typescript`
0.4.0, `@sourcegraph/scip-python` 0.6.6, `scip` CLI v0.10.0 (linux, `scip-code/scip`),
Temurin JDK 17.0.20.1, coursier `cs`, .NET SDK 8.0.425 portable + `scip-dotnet` tool. Counts are
from a 60-line SCIP wire-format reader written for the spike (`scratchpad/scipread.py`), which
becomes `graph/precise.py`.

| Language / indexer | Repository | Ran? | Documents | Occurrences (defs / refs) | Wall | Needs |
|---|---|---|---|---|---|---|
| TypeScript+JS — scip-typescript 0.4.0, `--infer-tsconfig` | GLNA `b43b322` (893 js) | yes, Windows | 891 | 74,928 (21,291 / 53,637) | 7.4 s, 7.2 MB | node ≥16; writes `tsconfig.json` **into the tree** (removed by hand; `git status` clean after) |
| same, `--infer-tsconfig` at the workspace root | Dub `b8866f4` (2,348 ts + 1,992 tsx) | yes, Windows | 4,340 | 575,731 (159,271 / 416,460) | 40.7 s, 51 MB | same; cross-file references 32,872; the `@/lib/upstash` alias resolved, `@/lib/prisma` did not (partial without the real `paths`) |
| same, `--pnpm-workspaces` | Dub | **no on Windows** (`projects ["C"]`: the path is split at the drive colon); **partial on Linux** | 140 | 16,700 (2,617 / 14,083) | 6.6 s | every workspace `tsconfig.json` `extends` a workspace package (`tsconfig/nextjs.json`) that only `node_modules` provides, so `apps/web` indexes nothing |
| Python — scip-python 0.6.6 | Windows native | **no**: `SyntaxError: Invalid regular expression` from `new RegExp(path.sep)` at startup | — | — | — | Linux only |
| same, WSL Ubuntu 24.04, node 18 | `googleads/google-ads-python` `f068d59` (8,532 files) | default heap: **OOM** (signal 6 at 2.27 GB RSS after 118 s); with `--max-old-space-size=12288`: **yes** | 8,532 | 1,906,775 (334,694 / 1,572,081), 878 external symbols | 1,733 s, 10.1 GB RSS, 334 MB | Linux, node, a heap sized to the tree (a generated client is the worst case; a customer repository is a few hundred files); no venv needed to start |
| PHP — scip-php (davidrjenni, 19 stars) | GLNA | **not run**: no `php`, no `composer` on this machine or in WSL (sudo needs a password) | — | — | — | PHP ≥ 8.1, composer, and the repository's `composer install` (it resolves through the project autoloader); P-032 provisioning in the sandbox |
| Java — scip-java via `cs launch --contrib scip-java` | `googleads/google-ads-java` `6c240f2` (Gradle 8.10) | **no**: `gradlew --init-script … scipCompileAll` needs the Gradle distribution and every dependency; the wrapper download timed out twice | — | — | 18–30 s to fail | JDK 17 + a full Gradle/Maven build with the semanticdb javac plugin, i.e. network and compilation of the entire generated client |
| .NET — scip-dotnet (tool) on `Google.Ads.GoogleAds.sln` | `googleads/google-ads-dotnet` `6c240f2`… (14 csproj) | **yes**, but it **restored 11 NuGet projects itself** before indexing (network egress the scan may never perform) | 5,852 | 5,548,842 (540,479 / 5,008,363) | 448 s, 505 MB; `git status` clean afterwards | .NET SDK 8; a solution or project path (a bare directory is refused); `--skip-dotnet-restore` to forbid egress, which then needs a provisioned package cache |

### GO / NO-GO (decided from the table; the owner delegated decisions on 2026-09-13)

- **TypeScript, TSX, JavaScript — GO.** One indexer, three languages, no dependencies needed
  to produce a usable index, seconds not minutes. Constraints the adapter must honour: run only
  on a **staged copy** (the indexer writes `tsconfig.json` into the project); derive that
  tsconfig from the repository's own file with unresolvable `extends` dropped and `paths`,
  `baseUrl`, `include`, `jsx`, `allowJs` kept, so aliases resolve without `node_modules`; never
  use `--pnpm-workspaces`/`--yarn-workspaces` (broken on Windows, empty without deps); bind
  `scip-typescript=<version>+sha256:<entry script>` into `scanner_version`.
- **Python — GO, Linux-hosted only.** The 12 GB run produced a complete index of the largest
  generated Python client there is; the runner sets the heap itself and refuses to start on
  Windows with the reason. Neither ground-truth repository has Python, so this lands as the
  adapter plus a fixture whose index was generated in WSL, and the map reports the exact
  sentence on this host: `recall-only: scip-python fails to start on Windows`.
- **PHP — NO-GO on this machine.** The map says `php: recall-only; precise indexing needs PHP
  8.1+, composer and the repository's composer install (scip-php)`. GLNA's 206 PHP UNKNOWNs
  therefore stay exactly where they are, by evidence.
- **Java — NO-GO.** `java: recall-only; precise indexing needs a JDK and a full Gradle or Maven
  build with the semanticdb plugin (scip-java)`.
- **.NET — NO-GO for the scan path** even if the index below succeeds: the indexer restores
  packages (egress) unless told not to, and then needs a provisioned cache. `csharp: recall-only;
  precise indexing needs the .NET SDK, a solution path and a restored package cache (scip-dotnet)`.

### Design (open middle, decided)

**Adapter shape.** `graph/precise.py` is provider-neutral and language-neutral: a `SymbolIndex`
built from SCIP bytes by a dependency-free wire-format reader (Index → Document → Occurrence;
paths normalised to `/`; ranges to line/character; roles as the SCIP bitmask). It answers three
questions: `symbol_at(path, line, column)`, `definition_of(symbol) → (path, range) | None`, and
`references(symbol) → occurrences`. `graph/indexers.py` runs one pinned indexer per language
family on a staged copy: `Indexer(name, languages, version(), command(staged_root, output))`;
the binary is located through `core/toolchain.locate` (PATH or an explicit path, never
vendored yet — shipping node plus the package is a new binary and is raised as P-033 with the
measured sizes). A missing indexer is not an error: the language is recall-only and the
coverage block says so. A present indexer that fails is `TOOLING_FAILED` for that language,
also visible, never silent. No network call: the indexer runs offline on the copy.

**Where precision enters the graph.** `ImportGraph` gains an optional `SymbolIndex` per
language. Three call sites change, each keeping the name path as the visible net:
1. `callers(definition)`: with an index covering the definition's file, callers are the calls
   whose callee occurrence carries the definition's symbol; the hop reads `callers by symbol`.
   Without one, name-and-arity as today; the hop reads `callers by name`.
2. `ImportBinding.target_path` and `_import_assignment`: the imported symbol's definition
   document wins over the module-string resolver (barrels, re-exports, aliases the tsconfig
   reader cannot see).
3. `_bound` / `first_party_definition`: an identifier occurrence whose symbol is defined in the
   index binds to that file; one whose symbol names a package (`npm <name> <version>`) binds to
   that package at that version, recorded in `binding_target`; a site with no occurrence at all
   is unchanged (a string literal has no symbol, and absence of adjudication is never a
   disposition).

**Proof binding.** `scanner_version` gains one segment per indexer that ran
(`;scip-typescript=0.4.0+sha256:…`). The index's own `tool_info.version` must equal the
binary's reported version or the index is refused (`TOOLING_FAILED`). `StructuralCoverage`
gains `precise` per language (files covered by an index) and `precise_index` (the indexer
identity or the sentence naming what would enable it). The Exposure Map prints both. No schema
changes: ProofScope already carries `scanner_version` as free text; Evidence `value` carries
the new `binding_target` and hop wording.

**Fixture.** `tests/fixtures/precise/ts_symbol_binding/repo`: a `tsconfig.json` with `paths`,
a barrel `lib/index.ts` re-exporting `lib/version.ts`, two same-named functions in different
files with the same arity, one caller of each, and an aliased import through the barrel. Its
index is committed as `index.scip` with `index.json` recording the indexer version, the entry
script sha256 and the command, and a test regenerates and compares the parsed structure when
the indexer is on PATH (skips with the reason otherwise, never silently).

**Held-out transfer (DoD 4).** `google/ads-api-report-fetcher` (TypeScript + Python, Google-
owned, GAQL-heavy), cloned once at HEAD after the work is done, scanned once with and once
without the indexer, numbers written to `docs/FAILURE_ATLAS.md` untouched.

### Ordered work

- **B1** `graph/precise.py`: reader + `SymbolIndex`; property test that encoding-then-reading
  is the identity over random small indexes (a test-side encoder); Windows `\` normalisation.
- **B2** `graph/indexers.py`: `scip-typescript` runner on a staged copy with the derived
  tsconfig; version + sha256 identity; timeout → `ToolingTimeout`; refusal when the index's
  tool version disagrees. `scip-python` runner shaped the same, Linux-only, with the heap flag.
- **B3** graph integration (callers, import targets, bindings) with hop wording; revert check:
  the fixture's same-name caller test fails when symbol callers are removed.
- **B4** coverage + ProofScope + map: `precise` counts and the enabling sentence per language;
  `scan_repository` runs the indexers it finds and records each in `scanner_version`.
- **B5** ground truth: both repositories before/after, three consecutive byte-identical scans,
  UNKNOWN not risen, every AFFECTED preserved; baseline permitted sets intersected if ids move.
- **B6** held-out transfer run, atlas rows, P-033, `dev/context.md`, `dev/tasks.md`.

### OPEN QUESTIONS

None that change the work: every decision above follows from a measured row. The single
approval boundary touched — shipping an indexer binary — is not crossed; P-034 records what
crossing it would cost.

### PROGRESS — 2026-09-13, same day

B1-B5 landed; evidence per item is in `dev/tasks.md` and the numbers in `dev/context.md`.
Decisions a later session must not undo:
- The indexer never runs on the customer tree: staged copy, synthesized root manifest, output
  outside the copy, `project_root` never read out of the index (FA-061).
- The staged tsconfig is the repository's own minus unresolvable `extends`; workspace flags are
  never passed (FA-062). Every tsconfig directory is a project.
- Symbol callers only ever *remove* a name-and-arity caller when its symbol belongs to another
  definition in the graph (FA-066); a declaration, local or unreadable symbol keeps the
  caller. Precision never trades recall.
- The precise layer never binds to a package: external symbols are dropped before the graph
  sees the index (FA-065), and the dependency observer owns packages and their versions.
- Absence of an indexer is recall-only, visible per language; failure of a present indexer stops
  the scan unless `--force`, and the forced map says so. Never a silent fallback.
- `scip-python` refuses Windows with the reason as TOOLING_MISSING; the runner sets the heap.
- The identity binds the indexer's entry script; P-034 is the path to binding the whole tree.

The red team (2026-09-13) broke the first cut in three places, each now a fixture and an atlas
row: host-ambient `@types` reached the index and moved a verdict under an unchanged ProofScope
(FA-065 — external symbols are now dropped and type roots disabled, so the index is a function
of the tree and the indexer); interface dispatch deleted a true caller and closed an UNKNOWN by
deleting evidence (FA-066 — a caller is removed only when its symbol belongs to another
definition in the graph, provenance is per caller); renamed imports never bound (FA-067 —
positional lookup). Residue left open, on purpose: SCIP `position_encoding` is not read and
columns are compared as character offsets (ast-grep characters, TypeScript UTF-16 units, equal
outside astral planes), a mismatch degrades to name binding, proved on astral fixtures; a
first-party symbol whose definition document the indexer skipped (files over its 1 MB limit)
adjudicates nothing; the index is trusted as produced by the pinned indexer on a staged copy,
with no cross-check against the source text; the indexer subprocess inherits the environment
and network isolation is not enforced on the host (the sandbox image is where that belongs).

The held-out transfer found FA-064 on its first scan (a U+0085 in a minified bundle split
ripgrep's JSON stream and the text observer died with a traceback); the fix is a `\n`-only split
of every line-delimited JSON stream. Held-out numbers on the final bytes, run once (FA-068):

```
google/ads-api-report-fetcher 2419122 · with scip-typescript on PATH        real 0m58.5s
  candidates 5380 · affected 5 · not affected 454 · excluded 4323 · reference 7
  unsupported 90 · unknown 501 · unexplained 0
  typescript supported=63 precise=62 · javascript supported=7 precise=2 · python recall-only
  configs rewritten without their extends 2 · external symbol occurrences dropped 2437
same repository without the indexer: identical counts; git status clean after both
```

Final evidence on the same `hubbleops/` bytes: `uv run pytest -q` 1,181 passed, 2 failed in
22m03s — both stale test expectations from the indexer being on PATH (the ship-path test now
strips `scip-typescript` like `rg` and `ast-grep`; the real-indexer test compares first-party
occurrences), fixed in the two test files only and rerun: `test_ship_path.py` 1 passed,
`test_indexers.py` 25 passed. Three Dub scans byte-identical (`c285c311…`), Dub 327 · 4 · 73 ·
32 · 0, GLNA 1143 · 259 · 218 · 333 · 0; `uv run pyright hubbleops tests` 0 errors; `ruff check`
and `ruff format --check` clean over 219 files; `hops pack verify google_ads` OK, lattice
unchanged.

## PART ELEVEN — the resolver asks the wrong question (2026-09-13, supersedes PART TEN's order)

### What the three repositories say, measured

Every UNKNOWN on every repository was classified against the pack's own catalogs
(`catalog_v19 … v25.jsonl`) with one script, no tuning:

| Repository | UNKNOWN | decidable today from the lattice alone | what is left |
|---|---|---|---|
| tap-google-ads `5b6a201` | 479 | 225 GAQL field lines whose field is identical in every lattice version · 211 provider identifiers/config keys that carry no version · 1 dependency state (setup.py not read) | 26 non-request skeleton junk · 16 anchors absent from every catalog |
| google-listings-and-ads `b43b322` | 333 | 272 surface identifiers · 31 package names · 13 stable fields | 10 contract surfaces · 4 absent anchors · 3 versioned call sites |
| dub `b8866f4` | 32 | 17 surface identifiers · 2 config keys | 11 contract surfaces · 2 request/call sites |

### The root cause, in one sentence

Every claim handler in `observe/resolver.py` answers "which version runs at this site?", and
returns UNKNOWN when no version is visible; the migration question is "does any version in this
pack's lattice change this subject?", which the pack can answer for 90% of those sites without
knowing the site's version at all. A field that exists with the same shape in v19 … v25 cannot
be affected by any migration this pack can perform; an SDK class name, package name, host or
credential key carries no version and no change set names it. Calling these UNKNOWN is not
conservatism, it is a wrong claim table, and it is why every repository reads 30–75% UNKNOWN.

Three smaller defects compound it, all found by the same run (FA-069 … FA-074):
`change_sets_for` swallows a diff the pack cannot compose (`except Exception: continue`), so a
version below the lattice floor (v9) yields no obligation at all; the audit's `absent:` check
counts a CHANGELOG line; the consumer check flags any leaf name a removed subject shares.

### The mechanism (generic; pack data in, no provider names in generic code)

- **Lattice stability, computed once per scan by `app/stability.py` from the injected pack:**
  for every catalog subject, `STABLE` when present in every lattice version with equal breaking
  attributes (kind, data type, selectable/filterable, category), `CHANGES_AT:<versions>` when
  it appears, disappears or changes at some boundary, `UNDECIDED` when any version records it
  `UNKNOWN_PROVIDER_CONTRACT`. Surface identifiers (identifiers, hosts, package names, config
  keys that are not version carriers) are `STABLE` unless a documented-change or diff subject
  names them. Passed into `ResolutionContext.stability`; the ledger and ProofScope stay
  target-free because the lattice is already in the changes hash.
- **Disposition rule, applied only when a handler returns UNKNOWN:** every subject the claim
  names is `STABLE` → `NOT_AFFECTED_WITH_EVIDENCE`, reason "carries no version and is stable
  across v19…v25; no migration inside this pack's lattice changes it" (request anchors, surface
  references, package references, non-carrier config keys, contract surfaces that map to a
  catalog subject). Any subject `CHANGES_AT` → stays UNKNOWN with the closing instruction naming
  the boundary version, which is the honest residue. `ABSENT`/`UNDECIDED` → unchanged. Version
  carriers, call sites, holes, dependency state and generic sinks are untouched.
- **Below the floor:** a version the pack cannot compose a diff from is recorded, never skipped;
  the engine emits a HUMAN obligation ("v9 is below this pack's lattice floor; migrate by hand
  or delete") so AFFECTED never has zero obligations and the map's Required changes names it.
- **Audit absence by role;** consumer check binds a leaf to its parent name in the same file
  (P-027 minimal); suite layout honours declared `testpaths`.
- **Closure:** GENERATED directories are enumerated and classified, `.git` is excluded as file
  or directory; `setup.py`/`setup.cfg` `install_requires` are manifests.

Expected on the ground truth, stated before the change: tap-google-ads UNKNOWN 479 → ≈ 42,
GLNA 333 → ≈ 17, Dub 32 → ≈ 13; AFFECTED unchanged on all three; 0 unexplained; every frozen
baseline id present with a permitted status (NOT_AFFECTED_WITH_EVIDENCE is permitted for
surface_reference). Verify on tap-google-ads: audit and consumer conjuncts no longer fail on
noise; the true reason (v9 spikes) becomes a HUMAN obligation.

### Ordered work (disjoint ownership; coordinator wires `app/cli.py` and runs the suite once)

- **R1** `app/stability.py` + `resolver.py` disposition + `ledger.py` context; tests.
- **R2** `migration.py` fail-closed composition + engine HUMAN obligation below floor +
  satisfied-obligation accounting in migrate; tests.
- **R3** closure GENERATED enumeration and `.git` file; deps `setup.py`/`setup.cfg`; tests.
- **R4** audit absence by role; consumer leaf binding; runners `testpaths`; tests.
- **R5** full suite, three repositories before/after, three byte-identical scans, revert checks,
  atlas rows closed, context/tasks.

### RESULT — integrated and frozen as `engine-v0` (2026-09-13)

Done, with the measured numbers in `dev/context.md`. The forecast above was optimistic on two of
three: tap-google-ads 479 → **91** (not ≈42), GLNA 333 → **94** (not ≈17), Dub 32 → **15** (≈13).
The gap is honest residue the forecast did not price — `segments.device` changes at v20, so every
anchor naming it keeps its UNKNOWN and gains the boundary in its closing instruction, which is the
rule working, not failing. AFFECTED is unchanged on all three and every baseline AFFECTED id
survives; 0 unexplained everywhere; `test_real_repo_conservation` green with both checkouts.
tap-google-ads gains one AFFECTED — the `google-ads==30.1.0` pin in `setup.py`, invisible before.
On verify the audit residue is `none` and the consumer check **PASSes**; the v9 spikes are 27 HUMAN
obligations and the verdict is FAILED on them, on their falsifier, and on the frozen baseline
(FA-073, which needs CI configuration read and is still open).

## PART TEN — Tier 3c: AI with attestation, sunset calendar, Sentinel in one line (2026-09-13)

### Outcome

AI contributes only where a mechanical judge decides; every AI artefact is attested inside the
proof with no schema change; a connected repository learns about a new Google Ads version the
day the pack that carries it ships; production evidence is one command away. Default-off stays
the shipped default. Nothing in this part touches `verify/verdict.py`, `core/schemas/`, or the
ProviderPack protocol.

### Tree state before this part

Uncommitted from the Phase 6 gate blocker (1), not Tier 3c work, kept as is:
`hubbleops/proof/memory.py` (prepare-pr refuses a Receipt whose ProofScope `repo_sha` is not the
audit's `candidate_sha`) and its test in `tests/integration/test_phase5_verify.py`. The
ground-truth checkouts were absent from `C:\dev\HubbleOps` after the move off OneDrive; both
were re-cloned at their pinned SHAs (`b43b322771071ed88d5a817422dd222acbaa5f33`,
`b8866f413cec065438d6e5faabbd9dac7d1ceea5`) under `.hubbleops/artifacts/phase7-real-repos/`.
`scip-typescript` is not on PATH on this machine; PHP and composer do not exist; node, pnpm and
rootless podman do.

### Ground truth before any change (quiet tree, no indexer on PATH)

The default state file `.hubbleops/hubbleops.sqlite` (2026-09-09) is store schema v1 and this
build refuses it (`STORE_SCHEMA_MISMATCH`); it is run output, left in place, and every
measurement below uses a fresh `--state-dir` in the session scratchpad.

```
uv run hops scan .hubbleops/artifacts/phase7-real-repos/dub --pack google_ads --state-dir <scratch>/state --export <scratch>/before/dub.json
real 2m10.650s   Candidates found 327 · UNKNOWN 32 · Unexplained 0
counts: affected 4 · not_affected_with_evidence 73 · unknown 32 · unsupported 207 · unscanned 8 · excluded_with_evidence 3 · unexplained 0
uv run hops scan .hubbleops/artifacts/phase7-real-repos/google-listings-and-ads --pack google_ads --state-dir <scratch>/state --export <scratch>/before/google-listings-and-ads.json
real 1m14.219s   Candidates found 1143 · UNKNOWN 333 · Unexplained 0
counts: affected 259 · not_affected_with_evidence 218 · unknown 333 · unsupported 306 · unscanned 26 · excluded_with_evidence 1 · unexplained 0
php recall-only: precise indexing needs PHP 8.1+, composer and the repository's composer install (scip-php)
```

Identical to the Tier 3b baseline (Dub 327 · 4 · 73 · 32 · 0; GLNA 1143 · 259 · 218 · 333 · 0).
Neither run has a dynamic or sentinel observer in its ProofScope, so there are zero
`UNKNOWN_DYNAMIC` sites today; DoD 4's closing is measured against a capture run, not these
scans.

```
uv run hops impact .hubbleops/artifacts/phase7-real-repos/google-listings-and-ads --pack google_ads --target v25
  deterministic 259 · human 0 · preserve_unknown 665 · unresolved version 0 · affected paths 39
uv run hops impact .hubbleops/artifacts/phase7-real-repos/dub --pack google_ads --target v25
  deterministic 2 · human 0 · preserve_unknown 247 · unresolved version 0 · affected paths 2
```

The obligation engine classes **zero** obligations HUMAN on either repository. The one item
`hops migrate` leaves for a human on GLNA (the `dev-legacy-v32.1.0` composer pin) is a
DETERMINISTIC obligation whose transform refused its precondition, not a HUMAN class. So use
(a)'s input is defined as *what `hops migrate` leaves undone*: HUMAN-class obligations plus
DETERMINISTIC obligations whose transform refused, each with the refusal reason as input.
Measured on this ground truth the producer has exactly one input, and its judge is
`TOOLING_MISSING` here; the count is stated as such, never padded.

### DoD 1 — the attestation record (P-009, no schema change)

One AI artefact is one Evidence record. `derivation` is `DERIVED_AI_EVIDENCE`, `confidence` is
`INFERRED`, `claim_type` is `ai_attestation`, `observer` is `structure` (the reading `ai_triage`
already uses: §6.4 places the AI residue filter inside Observer C; the closed observer set is
frozen and gains nothing). `path`/`source_hash` name the first input file, as today. The record
is written once, after the judge has run, and is never mutated. `value` carries:

```
value:
  attestation:
    version: 1
    use: repair_draft | rule_draft | mapping_proposal | evidence_recipe
    model_requested: <configured model id>
    model_id: <exact id the provider returned for this completion>
    prompt_template_id: <use>@<n>
    prompt_template_hash: sha256 of the template bytes shipped in hubbleops/ai/prompts/
    prompt_hash: sha256 of the rendered prompt
    input_hashes: {label: sha256}      sorted; obligation/candidate/row id, file blobs,
                                       change-set hash, catalog hashes the judge will read
    output_hash: sha256 of the canonical model output
    judge:
      kind: repair_judge@1 | rule_judge@1 | mapping_judge@1 | recipe_judge@1
      outcome: ACCEPTED | REJECTED | UNJUDGED
      reasons: [sorted strings]
      evidence_ids: [ids of the deterministic records the judge produced or consumed]
  subject: {use-specific ids: obligation_id, candidate_id, row_id}
```

Not carried: the prompt text and, for REJECTED, the output bytes (hash only; the draft is
discarded). ACCEPTED output lives where its judge put it (the tree, `.hubbleops/surface.yml`,
the pack's decided-mappings file, the run's artifacts) and the ledger binds it by hash.

Enforced mechanically, each with a test that fails when reverted:
- **E1** `derivation == DERIVED_AI_EVIDENCE` ⇔ `value.attestation` present and valid. The store
  refuses an attestation under any other derivation (a downgrade to OBSERVED) and an AI
  derivation without one (unattested). Shape validation lives in `hubbleops/ai/attest.py`, not
  in `core/schemas/`.
- **E2** an attestation whose `judge.outcome` is not ACCEPTED appears in no obligation's and no
  candidate's `evidence_ids`; an ACCEPTED one appears only next to the judge's own
  deterministic records, so L10's existing "AI alone never closes" check keeps its meaning.
- **E3** the resolver has no claim-table row for `ai_attestation`; an attestation never
  participates in status resolution. `ai_triage` moves onto the same attestation path.
- **E4** import direction: `hubbleops/ai/` imports `core/` only and no pack; `verify/`,
  `observe/`, `obligations/`, `proof/`, `store/` never import `hubbleops.ai`; only `app/`
  constructs a model client, and only under an explicit `--ai` flag. `tests/unit/test_imports.py`
  and `test_no_provider_leak.py` cover the new layer.
- **E5** `hops scan` and `hops verify` cannot reach the client: neither code path imports it, and
  the ship-path test asserts no network from either verb.
- **E6** the Receipt is untouched (its schema is closed and frozen). "Attested in the proof"
  means: the ledger the ProofScope binds carries the records, and an obligation an ACCEPTED
  draft discharged lists the attestation id in its `evidence_ids`, which the Receipt's
  obligations section already prints. `hops migrate` output lists every draft with its outcome
  and attestation id.

What this does and does not do against P-009. It makes every AI record self-describing and
store-checkable, and puts the only client behind one module and one flag. It does not
cryptographically stop an in-process caller from relabelling model output as OBSERVED; that
part of P-009 (producer signing, a key boundary) stays OPEN and is not needed for DoD 1.
P-009 is updated to record this subset as the accepted design once the owner says yes.

**STOP here.** No AI call path exists until the owner answers Q32 below. Everything after this
point in the plan is contingent on that yes.

### DoD 2 — the four uses, each with its judge (contingent on Q32)

| Use | Verb | Producer input | Judge (deterministic; the only thing that can say ACCEPTED) |
|---|---|---|---|
| (a) repair draft for a HUMAN obligation | `hops migrate --ai` | obligation record, the bytes of its edit-site files, the change set | `repair_judge@1`: draft applied on top of the deterministic migration in a worktree; refused before judging if any hunk lies outside the obligation's paths (diff containment, §9.C); then the obligation's `required_state` holds, every request skeleton in the touched files passes the injected oracle, the frozen base-SHA suite runs and passes, the pack falsifiers pass. Any stage unresolved (`TOOLING_MISSING`, `ORACLE_UNAVAILABLE`) → UNJUDGED, never applied |
| (b) rule + fixture draft for a NEW_PATTERN UNKNOWN | `hops draft-rule <candidate_id> --ai` | the candidate, ≤2 files of context, the pack's rule format | `rule_judge@1`: the drafted ast-grep rule parses, matches the UNKNOWN site, matches every must-match and no must-not-match snippet the draft itself supplies, and re-scanning both frozen baselines with the rule active moves no AFFECTED candidate and closes nothing (a rule may only add evidence). ACCEPTED → `.hubbleops/surface.yml` entry `state: drafted` with the attestation id; a human promotes it. The red-team subagent attacks every drafted rule before a human sees it |
| (c) mapping proposal for an ambiguous release-notes row | `hops pack propose-mappings --ai` | the row's cells, the before/after catalog subject lists | `mapping_judge@1`: the proposed `change_subject` exists in the source catalog and the proposed `replacement` in the target catalog and they differ. ACCEPTED means well-formed, never true: `hops decide --pack-row <row_id> --replacement <subject> --by <who>` is the only thing that records the mapping, in pack data with the attestation id, changing the lattice hash |
| (d) the test or capture that would produce an UNKNOWN's missing evidence | `hops recipe <unknown_id> --ai` | the candidate, its closing instruction, ≤2 files | `recipe_judge@1`: the recipe names the candidate's file and site, parses in the repository's language, and when the capture toolchain for that language exists here it runs under `hops capture` and must produce evidence that maps to the candidate; otherwise UNJUDGED. The recipe never decides; the capture it describes does |

The 22 ambiguous rows are today only a count (`migration_table_changes` increments
`unresolved`). They gain identity first: the refresh emits `documented_change` records with
`change_kind: UNRESOLVED` carrying the cells and the digest, re-normalised offline from the
snapshotted raw pages, so the lattice hash moves once and `hops pack verify` still passes.

Measured and pasted: on `google-listings-and-ads`, how many HUMAN obligations the judges
accepted from AI drafts and how many they rejected or could not judge. Prediction, stated now
so it cannot be tuned later: the frozen suite there is `TOOLING_MISSING` (no PHP), so every
repair draft is UNJUDGED and zero are applied; the value shown is that the product says so.

Prompt templates (open middle, decided): plain text under `hubbleops/ai/prompts/`, generic,
provider names arrive through slots filled from pack data; hashed into the attestation.
Client (open middle, for Q32): a `Model` protocol with one method; the real one behind the
optional extra `hubbleops[ai]`, key from the environment only, default model id pinned in
`hubbleops/ai/client.py`, never chosen at run time.

### DoD 3 — sunset calendar and the scheduled impact

- The schedule is already carried as facts: `compatibility_vNN.jsonl` holds `sunset_at` per
  version with the sunset page's URL and digest, and `lattice.json` mirrors it. A test asserts
  the two agree for every version (a lattice date without a sourced fact fails `pack verify`).
- The map's sunset block gains days-to-sunset. The date is an explicit input, never the clock:
  `hops exposure --as-of YYYY-MM-DD` (default today, printed on the map's first line), so the
  ledger and ProofScope stay date-free and byte-identical scans stay byte-identical.
- `hops impact --release <vNN | latest>`: the release is a lattice version; an id the pack does
  not carry fails closed ("this pack does not carry v26; a newer hubbleops carries it"). The
  report gains a RELEASE block (release id, released_at, every effective version's sunset and
  days left as of `--as-of`) and `--sunset-within DAYS` makes the exit code non-zero when an
  effective version is inside the window or already past it, so a scheduled job turns red.
- `hops impact --install-workflow` writes `.github/workflows/hubbleops-impact.yml`: `schedule`
  daily plus `workflow_dispatch`, `contents: read`, runs `hops impact . --pack <p> --release
  latest --sunset-within 90` and uploads the report. Cadence (open middle, decided): daily; the
  run costs about two minutes and a version lands at most once a month. The Action installs
  `hubbleops` with a floor and no ceiling inside the current major, because a pinned version
  carries a pinned lattice and would never learn a new release; every report names the
  hubbleops version and lattice hash it was produced by, so it is scoped, not floating.

### DoD 4 — Sentinel in one line

- Proxy mode gains a plain-text input: any log stream whose lines carry a request target the
  pack's wire signature parses (`Method: /google.ads.googleads.vNN.services.X/Y` as every
  official client library logs it, or a versioned REST URL). The timestamp is read from the
  line; a line without one is an issue `TIMESTAMP_MISSING` in the manifest and no event, so the
  customer sees exactly what to fix. NDJSON input keeps working unchanged.
- One command: `uv tool install hubbleops-sentinel` (or pipx). One config line: the client
  library's own request logging pointed at a file; `hubbleops-sentinel --help` prints the line
  per language. Then `hubbleops-sentinel proxy --input <log> --output events.jsonl`.
- `hops promote --from-sentinel <capture_run>`: an UNKNOWN_DYNAMIC site whose (service, method,
  version) the stream observed, and which no other static site claims, gains a
  `production_binding` in `.hubbleops/bindings.json` (run id, evidence ids, event key) and
  resolves in one hop on the next scan; an ambiguous key stays UNKNOWN with the competing sites
  named in the closing instruction. A stream carries no stack, so it promotes bindings, never
  wrapper rules.
- Proof on a ground-truth test suite: attempt Dub (node and pnpm exist) with its Google Ads
  calls logged; if its suite cannot run here without services it needs, the blocker is pasted
  and the proof runs on the fixture suite instead, said plainly as not the ground truth.

### Ordered work (each item lands with a test that fails when its mechanism is reverted)

- **C0** `hubbleops/ai/attest.py` (build + validate), store guards E1/E2, `ai_triage` on the
  attestation path, layer rules E4/E5, unit + property tests. No client. This is DoD 1 and
  lands before the STOP is lifted, since it contains no call path.
- **C1** the 22 rows gain identity (`UNRESOLVED` records, offline re-normalisation); `hops
  decide --pack-row`; lattice re-hashed; `pack verify` green.
- **C2** sunset: lattice/fact consistency test, `--as-of`, days-to-sunset, `hops impact
  --release/--sunset-within/--install-workflow`.
- **C3** sentinel log-line proxy input + help text + `hops promote --from-sentinel`; fixture
  suite proof; Dub attempt.
- **C4** (after Q32 = yes) `hubbleops/ai/client.py` + prompts + the four producers and judges,
  each verb default-off behind `--ai`.
- **C5** measurement on both repositories (before/after counts, three byte-identical scans,
  revert check per mechanism), red team with three new corruptions on the AI path (forged
  attestation under OBSERVED; an ACCEPTED outcome written without the judge's evidence ids; a
  draft that edits a frozen test to make the suite pass), spec audit, atlas rows, `dev/context.md`
  and `dev/tasks.md`.

Subagent ownership (disjoint): C0 → `hubbleops/ai/attest.py`, `hubbleops/store/sqlite.py`,
`hubbleops/observe/ai_triage.py`, `tests/unit/test_attestation.py`, `tests/support.py`;
C1 → `hubbleops/packs/google_ads/refresh.py`, `changes.py`, `hubbleops/app/decision.py`, their
tests; C2 → `hubbleops/app/impact.py`, `exposure.py`, `proof/impact_workflow.py`, their tests;
C3 → `packages/hubbleops-sentinel/**`, `hubbleops/app/promotion.py`, their tests. `app/cli.py`
is coordinator-only. The coordinator integrates and runs the suite once on the final bytes.

### OPEN QUESTIONS

- **Q32 (the STOP required by DoD 1).** May an AI call path exist in this tree at all, and if
  yes: the `anthropic` SDK as the optional extra `hubbleops[ai]` with a pinned default model id,
  or a different client? Two readings produce materially different work: "no" ends Tier 3c at
  C0–C3 with the four verbs absent; "yes" adds C4 and the measured acceptance counts. Nothing in
  C4 is written before this answer.

Decisions taken rather than asked (override in the answer if wrong): confirmed mappings live in
pack data, not the repository's `decisions.yml`, because a provider fact applies to every
repository and must move the lattice hash; rejected drafts survive only as a hash; the scheduled
Action floats within the major so it can learn a release; the sunset date is an explicit input.
