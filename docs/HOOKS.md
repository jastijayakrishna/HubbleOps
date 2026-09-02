# Mechanical enforcement — `.claude/settings.json`

The Laws in [CLAUDE.md](../CLAUDE.md) are prose. Prose does not stop an agent. These hooks do.

**Not active yet.** No `.claude/settings.json` exists in this repo, because every command below
targets a tree that hasn't been created (`tests/`, `verify/`, `core/schemas/`). Switch it on as the
first act of Phase 1, once those paths exist. Until then this file is the spec.

## Stop — block completion on a red suite

```
uv run pytest tests/unit tests/property -q
```

Non-zero exit blocks the agent from reporting completion. This is the "evidence over assertion" Law
made mechanical.

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
