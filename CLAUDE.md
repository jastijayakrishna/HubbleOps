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
Distribution and import name `hubbleops`; sensor package `hubbleops-sentinel`. Console script is `hops` — every command is `hops <verb>` (scan, exposure, capture, promote, verify, migrate, decide, guard, impact, replay, pack verify). Repo-local state lives in `.hubbleops/`.

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
app/ (pack registry + cli)  core/  closure/  observe/  graph/  obligations/  sandbox/  repair/  verify/  proof/  store/  packs/{_protocol.py,_mock,google_ads}  packages/hubbleops-sentinel/  tests/{unit,property,fixtures,adversarial,heldout,integration}  docs/  dev/
