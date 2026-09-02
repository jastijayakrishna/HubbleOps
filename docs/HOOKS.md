# Mechanical enforcement — `.claude/settings.json`

The Laws in [CLAUDE.md](../CLAUDE.md) are prose. Prose does not stop an agent. These hooks do.

**Not active yet.** No `.claude/settings.json` exists in this repo, because every command below
targets a tree that hasn't been created (`tests/`, `verify/`, `core/schemas/`). Switch it on as the
first act of Phase 1, once those paths exist. Until then this file is the spec.

## Stop — block completion on a red suite or a stale context

Both checks must pass before the agent may report completion.

**1. The suite is green.**

```
uv run pytest tests/unit tests/property -q
```

Non-zero exit blocks completion. This is the "evidence over assertion" Law made mechanical.

**2. `dev/context.md` is current.** A fresh session inherits nothing but the files in this repo, so
a session that changed the build and did not write down what it changed has broken the handoff.

Enforce only when the session actually produced a change — a dirty working tree or a new commit.
A read-only session (plan review, gate audit, spec-drift audit) legitimately changes nothing and
must not be blocked. When it does apply, `dev/context.md` counts as current if it is modified in the
working tree, or touched by a commit on this branch:

```
git status --porcelain dev/context.md
git log main..HEAD --name-only -- dev/context.md
```

Either non-empty passes. Both empty blocks, with:

> `dev/context.md` not updated this session. Write where the build stands, what is blocking, and
> every decision a later phase must not re-litigate — including each answered OPEN QUESTION from
> `dev/plan.md`, which the next phase overwrites.

## PreToolUse (Edit, Write) — reject the edit

| Condition | Reject | Law it enforces |
|---|---|---|
| phase = `repair` | edits under `verify/`, `sandbox/verifier_image.py`, `.hubbleops/decisions.yml` | Repair cannot touch the authority that judges it |
| any phase > 1 | edits under `core/schemas/` | Schemas are frozen |
| always | any file under `packages/hubbleops-sentinel/` that imports `hubbleops.` | Sentinel independence |
| always | any file under `core/ observe/ graph/ obligations/ verify/ proof/ store/ sandbox/` that imports `packs.` **or** contains a provider name (list in `tests/unit/provider_names.txt`) | Dependency direction; no provider leak |
| always | a **new `.md` file** whose path is not named by the current phase's DEFINITION OF DONE, not in `dev/`, and not `docs/FAILURE_ATLAS.md` | No bloat — no unnecessary docs |
| always | a code file containing a comment line, outside the four exceptions in `CLAUDE.md` | No bloat — no comments |

Two of these are phase-dependent, so the hook needs the current phase. Where that lives —
env var, `dev/context.md`, `.hubbleops/` state — is a Phase 1 decision; record it in `dev/plan.md`.

The last two conditions are also covered by `tests/unit/test_imports.py` and
`tests/unit/test_no_provider_leak.py`. The hook catches them at write time; the tests catch them at
gate time. Keep both — the hook is fast feedback, the tests are the proof.

## PreToolUse (Bash) — reject the command

| Condition | Law it enforces |
|---|---|
| `git add -A`, `git add .`, `git add --all`, `git commit -a` | Push only what the project needs — stage explicit paths |
| a commit message containing `Co-Authored-By`, `Generated with Claude Code`, or an emoji footer | No AI attribution |

## PostToolUse (Write) — on changed files only

```
ruff format
ruff check --fix
pyright
```

## Subagents — `.claude/agents/`

| Agent | Model | Tools | Job |
|---|---|---|---|
| [spec-auditor](../.claude/agents/spec-auditor.md) | opus | Read, Grep, Glob, Bash | Section-by-section conformance against `docs/ARCHITECTURE.md`; outputs a table |
| [red-team](../.claude/agents/red-team.md) | opus | Read, Grep, Glob, Bash, Write **under `tests/adversarial/` only** | Builds deliberately wrong migrations |
| [fixture-writer](../.claude/agents/fixture-writer.md) | opus | — | Turns a real pattern into an anonymized fixture + expected outputs + rule/falsifier |
