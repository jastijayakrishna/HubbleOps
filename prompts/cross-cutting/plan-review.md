# Plan Review

**When:** after `dev/plan.md` exists and every OPEN QUESTION in it has been answered, before any
implementation.
**Session:** fresh. The builder never grades its own plan.

## Prompt

```text
You are a principal engineer reviewing dev/plan.md for HubbleOps Phase <N> against CLAUDE.md and docs/ARCHITECTURE.md. Do not implement. Produce: (1) a table: every Definition-of-Done item → where the plan addresses it → gap; (2) every Law or Frozen-item violation; (3) every dependency-direction violation (anything in generic layers naming a provider or importing packs/); (4) every unnecessary abstraction/dependency/scope creep with "cut it" or "keep it, because…"; (5) the three most likely ways this plan yields a false-VERIFIED, a silent miss, or a proof reused across SHAs; (6) the smallest plan that still meets every DoD item. Terse. Cite line numbers.
```

Fix the findings in `dev/plan.md`, then implement in accept-edits mode.
