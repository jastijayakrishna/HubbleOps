# Plan — Phase <N>

Written in **plan mode**, before any edit. Overwritten at the start of each phase (the previous
phase's plan is in git history).

**Every OPEN QUESTION must be answered in this file before leaving plan mode.** A guessed answer is
a design decision you didn't make. Then a fresh session runs
[plan review](../prompts/cross-cutting/plan-review.md) against it.

---

## Approach

*(how each Definition-of-Done item will be met — the smallest plan that meets all of them)*

## Files touched

| Path | New / changed | Why |
|---|---|---|

## Law check

| Law | How this plan respects it |
|---|---|
| UNEXPLAINED_CANDIDATES = 0 | |
| UNKNOWN ≠ UNEXPLAINED | |
| UNKNOWN conservation | |
| Proof bound to ProofScope; new SHA → new proof | |
| Dependency direction (generic layers never import `packs/`, never name a provider) | |
| Fail closed — no `except: pass`, no silent fallback | |
| AI evidence is `DERIVED_AI_EVIDENCE`, never decisive alone | |
| Memory reduces work, never proof | |

## Evidence plan

*(the exact commands that will be run and pasted at the end — must cover EVIDENCE REQUIRED)*

## OPEN QUESTIONS

*(numbered. Design-changing questions get answered here — in writing — before implementation
starts. Delete none; answer them inline.)*

1.
