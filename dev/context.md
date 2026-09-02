# Context

Rolling state of the build. **Updated before every session ends** (a Law in `CLAUDE.md`).
Read by every phase prompt. Keep it short — this is what the next session wakes up knowing.

## Where we are

| | |
|---|---|
| **Current phase** | 0 — repo scaffolding |
| **Last gate passed** | none |
| **Next action** | supply `docs/ARCHITECTURE.md`, then start Phase 1 in plan mode |

## Blocking

- `docs/ARCHITECTURE.md` is a placeholder. Every phase prompt cites its sections by number.
  Phase 1 cannot start until it is filled in.
- `.claude/settings.json` not created — see [docs/HOOKS.md](../docs/HOOKS.md). The hook commands
  target trees that don't exist yet; activate at the start of Phase 1.
- This directory is not its own git repo.

## Decisions that carry forward

*(record here anything a later phase must not re-litigate — with the phase it was decided in)*

## Open threads

*(anything half-finished at session end, with enough detail to resume cold)*
