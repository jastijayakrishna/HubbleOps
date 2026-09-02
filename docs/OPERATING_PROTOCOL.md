# Operating protocol

Every phase runs as: **plan → fresh-session plan review → implement → evidence → fresh-session gate audit → real-repo loop.**

## The loop

1. `docs/ARCHITECTURE.md` (frozen build document) and `docs/BUILD_ORDER.md` are in the repo first.
   Prompts reference sections; they never re-explain.
2. `CLAUDE.md` sits at repo root. Keep it under 130 lines forever.
3. Start every phase in **plan mode** (`claude --permission-mode plan`). Paste the phase prompt.
   The plan goes to `dev/plan.md` with an OPEN QUESTIONS section.
   Answer every question in the file before leaving plan mode.
4. **Fresh session** → run [Plan Review](../prompts/cross-cutting/plan-review.md) on `dev/plan.md`.
   Fix. Then implement in accept-edits mode.
5. Completion is accepted only with **evidence**: command + output pasted.
   "Should work" is a failure.
6. **Fresh session** → run [Gate Audit](../prompts/cross-cutting/gate-audit.md).
   The gate passes only when the auditor says `GATE: PASS`.
7. **[Real-repo loop](../prompts/cross-cutting/real-repo-loop.md)** runs after every gate from Phase 2 onward.
   Phase 10 is pilot hardening, not first contact.
8. Claude updates `dev/context.md` and `dev/tasks.md` before every session ends.

## Cross-cutting prompts

| Prompt | When | Session |
|---|---|---|
| [plan-review.md](../prompts/cross-cutting/plan-review.md) | before implementing, on `dev/plan.md` | fresh |
| [gate-audit.md](../prompts/cross-cutting/gate-audit.md) | after implementation | fresh |
| [real-repo-loop.md](../prompts/cross-cutting/real-repo-loop.md) | after every gate from Phase 2 | fresh |
| [red-team.md](../prompts/cross-cutting/red-team.md) | Phase 5 gate, then nightly | subagent |
| [fixture-writer.md](../prompts/cross-cutting/fixture-writer.md) | on every NEW_PATTERN | subagent |
| [spec-drift-audit.md](../prompts/cross-cutting/spec-drift-audit.md) | weekly | fresh |

## UNKNOWN disposition — the only four outcomes

Every UNKNOWN in an Exposure Map is exactly one of:

1. `CLOSED_WITH_EVIDENCE`
2. `CLOSED_WITH_RECORDED_HUMAN_DECISION` (`hops decide`)
3. `PRESERVED_UNKNOWN` with a precise closing instruction
4. `NEW_PATTERN` → anonymized fixture (+ rule/observer/falsifier where justified)

The goal is **UNEXPLAINED = 0, not UNKNOWN = 0**. A genuinely undecidable UNKNOWN is a
correct result and must never be forced closed to satisfy a gate.

## Why these prompts work

1. **Outcome contracts, not micromanagement** — define done-ness and evidence; leave internals open.
2. **Evidence clause everywhere** — "paste command + output" kills "should work".
3. **Named traps** — agents repeat known failure modes; naming them removes most in one pass.
4. **Laws enforced by hooks and tests, not prose** — schema freeze, import direction, provider-name
   leak, sentinel independence are all mechanical.
5. **Fresh eyes for review and gate** — the builder never grades itself.
6. **Plan mode with written open questions** — a guessed answer is a design decision you didn't make.
7. **UNEXPLAINED = 0, never UNKNOWN = 0** — stated in the Laws so no gate can be satisfied by
   forcing certainty.
8. **New SHA → new proof** — stated in the Laws and tested by red-team, so incremental optimization
   can never become proof reuse.
