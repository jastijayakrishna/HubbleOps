# Phase prompt template

Every phase prompt is an **outcome contract**: it defines done-ness and required evidence, and
leaves internals open. Copy this shape for any phase not already written.

```text
ROLE: senior engineer implementing HubbleOps Phase N. Read CLAUDE.md, docs/ARCHITECTURE.md §<refs>, dev/context.md.
OUTCOME: <one sentence, must be true when done>
DEFINITION OF DONE: <numbered, testable>
INVARIANTS (hard edges): <what must never happen>
OPEN MIDDLE: <what you decide freely>
APPROVAL BOUNDARIES: stop and ask before <X>
EVIDENCE REQUIRED: <exact commands/outputs>
NON-GOALS: <not this phase>
TRAPS: <known agent failure modes on this task>
PROCESS: plan mode → dev/plan.md with OPEN QUESTIONS → stop.
```

## Writing the sections

| Section | Rule |
|---|---|
| **OUTCOME** | One sentence, checkable. If you can't state it in one sentence, the phase is two phases. |
| **DEFINITION OF DONE** | Numbered and testable. Each item is something the gate auditor can run. |
| **INVARIANTS** | Only hard edges. Don't restate all of `CLAUDE.md`; cite it. |
| **OPEN MIDDLE** | Say explicitly what the agent decides. Silence here reads as "ask me about everything". |
| **APPROVAL BOUNDARIES** | New dependencies, schema renames, anything with a live/production side effect. |
| **EVIDENCE REQUIRED** | Exact commands. This is the clause that kills "should work". |
| **NON-GOALS** | The next phases, named. Prevents scope bleed forward. |
| **TRAPS** | Known failure modes for *this* task. Naming them removes most in one pass. |
