# HubbleOps

You are building HubbleOps: a Truth Engine + independent Verification Authority for third-party API migrations. Provider #1: Google Ads (v22→v25). The product is two proofs: (1) we found every observable usage, (2) the repair broke nothing else. Read docs/ARCHITECTURE.md before any task; it overrides anything you believe.

## Laws (never violate; stop and say so if a task requires it)
- UNEXPLAINED_CANDIDATES = 0 at every persisted state. A candidate never disappears; it gets a status.
- UNKNOWN ≠ UNEXPLAINED. A genuinely undecidable UNKNOWN with a precise closing instruction is a CORRECT result. Never force an UNKNOWN closed to satisfy a gate.
- UNKNOWN conservation: an UNKNOWN closes only with new evidence or a recorded human decision.
- Every proof is bound to a ProofScope hash (tree, deps, contract, tool versions). A new SHA kills the old proof; a new proof is required. Never write "repo verified"; write VERIFIED_FOR_SCOPE.
- Dependency direction: app/ selects a ProviderPack and passes its SurfaceSpec, RuleSet, ContractOracle, ChangeCompiler, TelemetryAdapter, CaptureHooks, RepairTransforms, RepairTools, Falsifiers INTO the generic layers as parameters. core/, observe/, graph/, obligations/, verify/, proof/, store/, sandbox/ never import packs/ and contain no provider names, hostnames, package names, or pack paths. Enforced by tests/unit/test_imports.py and tests/unit/test_no_provider_leak.py.
- verify/ never imports repair/ or sandbox/runner; it uses sandbox/verifier_image only.
- Memory may reduce work, never proof.
- Verdicts: VERIFIED_FOR_SCOPE, HUMAN_REQUIRED, UNKNOWN, FAILED. Never SAFE.
- verify/verdict.py is pure and total. VERIFIED_FOR_SCOPE iff audit_pass ∧ oracle_all_accepted ∧ zero_unexplained_hunks ∧ UNKNOWN_BLAST = ∅ ∧ request_shape_differential_pass ∧ response_consumer_check_pass ∧ frozen_baseline_tests_pass ∧ falsifiers_pass ∧ unknown_conservation_pass. Candidate tests are evidence; frozen base-SHA tests are proof.
- Fail closed: parse failure → FILE_UNSCANNED; missing tool → TOOLING_MISSING; resolution failure → *_UNKNOWN; timeout → UNKNOWN with reason. No `except: pass`. No silent fallback that changes safety semantics.
- AI-derived evidence is DERIVED_AI_EVIDENCE and cannot alone change a candidate's status or close an UNKNOWN.
- sandbox/ (runner, image, limits, network, mounts, capture) serves dynamic capture and repair. Verification uses its own image (sandbox/verifier_image.py). Verifier isolation is never a mode of the repair runner.
- packages/hubbleops-sentinel never imports hubbleops.*. Its output is observational evidence (observer="sentinel"), never a verdict.
- REAL-REPO LOOP from the Phase 2 gate onward: run current HubbleOps on prospect/public repos → UNKNOWN patterns → anonymized fixtures → better rule/observer/falsifier → rerun gates. No repo-specific hacks. No frozen-architecture changes.

## Frozen (propose changes in dev/proposals.md; never redesign in place)
Schemas: Evidence, Candidate, Obligation, ProofScope, Receipt (core/schemas/*.json).
Interfaces: Observer, ProviderPack (and its sub-protocols), Memory (docs/ARCHITECTURE.md §3, §5).
Verdict function.

## Stack
Python 3.12+, uv, ruff, pyright strict, pytest + hypothesis. Binaries: ripgrep, ast-grep (YAML rules), git worktree, rootless docker/podman. SQLite WAL. JSON Schema for all records. No new frameworks, databases, or services without dev/proposals.md.

## Names
Distribution and import name `hubbleops`; sensor package `hubbleops-sentinel`. Console script is `hops` — every command is `hops <verb>` (scan, exposure, capture, promote, verify, migrate, decide, guard, impact, replay, prepare-pr, pack verify). Repo-local state lives in `.hubbleops/`.

## No bloat (hard rule — violating it is a defect, not a style opinion)
- NO COMMENTS in any code file. No headers, no section dividers, no restatement of the line below, no "why" that a name could carry. Rename the thing instead. Only four exceptions: JSON Schema `description` fields (they are data), the `packs/_protocol.py` Protocol docstrings that Phase 2 explicitly requires, a `# noqa`/`# type: ignore` a tool demands, and a non-obvious external constraint that cannot be expressed in code (a provider bug, a spec quirk) — one line, stating the constraint.
- NO NEW .md FILE unless a phase's DEFINITION OF DONE names it or a human asked for it by name. No summaries, no session notes, no per-package READMEs, no IMPLEMENTATION.md / NOTES.md / CHANGELOG.md, no write-up of what you just did. Evidence goes in the terminal. Running state goes in dev/context.md and dev/tasks.md. Frozen-surface changes go in dev/proposals.md. Real-world patterns go in docs/FAILURE_ATLAS.md. Nothing else gets a file.
- Applies to every phase, every session, forever. When in doubt, do not create the file and do not write the comment.

## Git
- One branch per phase, cut from `main`: `phase-NN-<slug>`, matching the prompt filename (Phase 1 → `phase-01-source-closure-and-ledger`). Nothing lands on `main` until that phase's gate audit says `GATE: PASS`.
- Tag on `main` right after the merge: Phase 1 → `v0.1`, Phase 2 → `v0.2`, … Phase 9 → `v0.9`. Phase 10 is the release: `v1.0`.
- One commit per logical change. Never bundle unrelated work into one commit; never split a single change across commits that do not each stand on their own.
- Write commit messages like a person: say what changed, and why if it is not obvious. "Add dependency observer for lockfile formats", "Fix version resolution when the lock file is missing". Never "fix stuff", "update code", "changes", "fix so on".
- NEVER put `Co-Authored-By: Claude`, "Generated with Claude Code", an emoji footer, or any AI attribution in a commit message, tag, or PR body. Not once, not in any phase.
- PUSH ONLY WHAT THE PROJECT NEEDS. Stage explicit paths. Never `git add -A`, never `git add .`, never `git commit -a`. Run `git status --short` first and stage the files you actually changed, one by one.
- Committed: source, schemas, prompts, docs the phases name, tests, fixtures, and pack data that carries a source hash. Plus `.hubbleops/{surface.yml,bindings.json,decisions.yml,retired.yml}`, which Phase 7 commits with provenance.
- Never committed: run output of any kind — artifacts/, ledgers, receipts, capture events, SQLite files, coverage, logs, sandbox output, worktrees, caches — plus credentials, editor and OS files, scratch, and anything a tool regenerates. If it appears in `git status` and is not part of the change you are making, ignore it or delete it. Never commit a file "just in case".

## How to work
- Plan before editing. Write open questions to dev/plan.md and stop; never guess on design-changing questions.
- Smallest complete change. Preserve working code. A second real implementation earns an abstraction; not before.
- Fix root causes; never suppress errors, weaken tests, or fake data to pass a check.
- Evidence over assertion: paste the command and its output for every "done". Never claim a test passed unless you ran it.
- Determinism: same inputs → byte-identical outputs. Sort, stamp, seed; add a test.
- Update dev/context.md and dev/tasks.md before ending a session; a Stop hook blocks completion if you changed anything and did not. Before a phase closes, move every answered OPEN QUESTION from dev/plan.md into the decisions section of dev/context.md — the next phase overwrites dev/plan.md, and a fresh session inherits nothing but these files.

## Repo map
app/ (pack registry + cli)  core/  closure/  observe/  graph/  obligations/  sandbox/  repair/  verify/  proof/  store/  packs/{_protocol.py,_mock,google_ads}  packages/hubbleops-sentinel/  tests/{unit,property,fixtures,adversarial,integration}  docs/  dev/

## How every session runs
- One brief: goal, constraints, acceptance. Auto mode, no plan mode. Ambiguity that changes the work: stop, one line.
- Subagents own disjoint files in worktrees; the coordinator integrates and runs the suite once on a quiet tree.
- No claim without the pasted command and output. Generators never grade their own work; a separate check or agent does.
- Every correction made in chat becomes a rule here before the session ends.
- Planes, never mixed: SCAN (no network, no credentials) · WORK (sandbox, allow-listed network, scoped credentials, agents allowed) · VERIFY (own image, controlled inputs, no agent authority). Only verify/ writes VERIFIED_FOR_SCOPE.
- Evidence classes: a tool result the harness executed = DERIVED_DETERMINISTIC; a live provider result with request hash, account, API version and timestamp = provider evidence at LIVE authority, scoped to the operation tested; anything a model or provider specialist says or writes = DERIVED_AI_EVIDENCE, attested, never closes an UNKNOWN alone.
- Provider-native specialists (Google Ads MCP server, Developer Assistant plugin) are pinned repair_tools in the WORK plane with validation-only credentials by default; they never touch the ledger, the store, or verifier inputs.
- Agents get better by the loop, not by the spec: run the corpus, read the failures, change one tool or one instruction, rerun, paste the delta. One change per commit, delta in the message.
- Unknown reduction is a diagnostic, never an acceptance criterion. The acceptance criterion is complete, correct handling of unfamiliar eligible repositories with bounded human effort, measured on labeled families the engine was never tuned on.

## Engine baseline
- `engine-v0` is the frozen engine; its scope is `dev/engine-v0.json` (tag, tree, lattice, tool hashes, `uv.lock`, verifier image, Python, harness commands). A change that could move a real-repo count is measured `engine-v0` → new on Dub, GLNA and tap-google-ads before it is believed, and every baseline AFFECTED must survive.
- A scan runs on a quiet tree. Never scan a tree a build, install or generator is writing into: once the closure's evidence set and the searcher's set describe different trees, every count is a count of two trees, and stopping is the only correct answer.
- A test that asserts a candidate's *status* is asserting a claim table, which is allowed to change. Assert the law the test guards — no adjudication record, no borrowed version, the fail-closed candidate still present — and name what moved the candidate. Never relax a resolver to keep a status assertion alive.

## Corpus and scoring
- The corpus is `dev/corpus/`: `definitions.json` (the "handled" definition, the four outcomes, the five attribution stages), `matching-rules.json` (how a finding matches a label), `families.json` (the pinned families), `labels/`. Definitions and matching rules are written BEFORE the first scored run and never edited mid-run; a change bumps their version, discards every scorecard produced under the old one, and forces a full rerun of every arm on every family.
- The four outcomes — COMPLETED, HUMAN_REQUIRED, SETUP_FAILED, ENGINE_FAILED — are disjoint and are never merged, averaged, or collapsed into a pass rate. Every rate names which outcomes are in its numerator. A correct refusal is HUMAN_REQUIRED, never ENGINE_FAILED.
- The engine's own development set — `dubinc/dub`, `woocommerce/google-listings-and-ads`, `singer-io/tap-google-ads`, `google/ads-api-report-fetcher` — is barred from the scored corpus. The acceptance criterion is measured on families the engine was never tuned on.
- Never fill a denominator. If fewer independent families exist than a brief asks for, say the number that exists and stop. A fork, a template instance or a vendored copy is one family, not two.
- An independence or lineage detector is not evidence until it has been calibrated on known positives. A detector that has never fired proves nothing by staying silent.
- A label source rules only on the claim class it has competence for, and everything else in an adjudicated file is UNSETTLED: counted, reported, excluded from every numerator and denominator, never a negative. A source that reads version tokens has no view of a GAQL selection or a removed field, and charging the arm for what the source cannot see measures the source, not the arm. Recall without its adjudicated coverage beside it is meaningless and is never reported alone.
- A measured run owns the machine. Runtime is a scorecard column, so running the test suite, the type checker or another scan beside it corrupts that column: `php-sylius-plugin` took 569s beside a busy machine and 159s alone, same repository, same SHA, same engine. Do quiet work during a measured run, or do none.
- Verify an environment fix against a live shell before spending a run on it. Three successive attempts to isolate the per-family install were each plausible and each wrong — a Windows `;` PATH handed to bash, then a PATH export that `bash -lc` discarded because a login shell rebuilds it, then a venv with no pip because `uv venv` does not seed one. Each cost a full run to discover and none needed one to check.
- Before a number is believed, ask which of the harness, the labels and the engine it is measuring. The first run of the first-signal corpus reported two ENGINE_FAILED families and a Python recall of 0.000; all three were the harness and the labels — a venv written inside the repository under test, a Windows PATH handed to a bash process, and comments scored as migration sites. An outcome column measuring its own harness is worse than no column.
- Run a repository's documented install inside a per-family isolated environment, never against the host interpreter. A corpus that pollutes the machine it measures on is not repeatable, and `pip install -e .` into the system Python is a defect, not a shortcut.
- A degraded arm is never reported as the arm. If the competitor's required tooling is missing, run what can be run, name it something else (`C-offline`, not `C`), and carry the degradation on every number it produces.
- Unsigned labels produce a provisional scorecard. The owner reviews every disagreement and signs the label set before any number is quoted as settled.
