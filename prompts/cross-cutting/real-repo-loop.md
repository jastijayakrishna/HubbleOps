# Real-repo loop

**When:** after every gate from Phase 2 onward. Never skipped.
**Session:** fresh.
**Why:** so Phase 10 is pilot *hardening*, not first contact with reality.

Outputs feed [docs/FAILURE_ATLAS.md](../../docs/FAILURE_ATLAS.md) and the next phase's fixture list.

## Prompt

```text
You are running the HubbleOps real-repo loop before Phase <N+1>. Run current HubbleOps (`scan`, `exposure`, and `capture` once Phase 4 exists) on <2–3 prospect/public repos>. Paste outputs. For each UNKNOWN assign one of: CLOSED_WITH_EVIDENCE, CLOSED_WITH_RECORDED_HUMAN_DECISION, PRESERVED_UNKNOWN (with closing instruction), NEW_PATTERN (→ fixture-writer). Record every NEW_PATTERN in docs/FAILURE_ATLAS.md (pattern, fixture id, which observer should catch it, which phase will). Rerun all existing gates. No repo-specific hacks. No frozen-architecture changes. Fixtures carry no customer identifiers.
```

## The three hard rules

1. **No repo-specific hacks.** A rule that only works on one repo's names is a fixture, not a rule.
2. **No frozen-architecture changes.** If the loop demands one, that's `dev/proposals.md`.
3. **No customer identifiers** in any fixture — anonymize the pattern, keep the shape.
