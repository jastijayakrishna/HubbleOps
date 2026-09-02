# Red-team

**When:** at the Phase 5 gate, then nightly.
**Agent:** [red-team](../../.claude/agents/red-team.md) — write access limited to `tests/adversarial/`.
**Goal:** get a wrong migration to `VERIFIED_FOR_SCOPE`.

**Any `VERIFIED_FOR_SCOPE` on a corrupted candidate is a P0.** Write it as a permanent regression
test and stop.

## Prompt

```text
You are the red-team subagent. Goal: get a wrong migration to VERIFIED_FOR_SCOPE. From a verified candidate in tests/fixtures/<x>, produce ≥10 corruptions under tests/adversarial/<date>/ (patch + expected_verdict.json + reason): hidden per-call override; weakened/deleted tests; unrelated query change; reintroduced removed resource; field renamed in query but not in response reader; coverage removed from a radius module; UNKNOWN closed without evidence; edits under verify/; ordering/time nondeterminism; the AI-triage path; a stale Receipt presented for a new SHA. Run `hops verify` on each. Any VERIFIED result is a P0: write it as a permanent regression test and stop. Report: corruption → verdict → reason → caught by which stage.
```

## Report shape

| Corruption | Verdict | Reason | Caught by |
|---|---|---|---|

The "caught by" column is the useful one. Two corruptions caught only by the same stage means that
stage is a single point of failure.
