# Phase 10 — Formal full-pipeline pilot hardening

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `dev/context.md` |
| **Ships** | Exposure Map with UNEXPLAINED = 0 on a real customer repo, Proof Pack, PR with guard, updated Failure Atlas |
| **Gate** | the UNKNOWN disposition table + adversarial suite still 100% rejected after any rule change |
| **Then** | — |

**This is not HubbleOps' first contact with reality.** The
[real-repo loop](../cross-cutting/real-repo-loop.md) has run since the Phase 2 gate.

`HUMAN_REQUIRED` with a named untested-radius list is an **acceptable pilot outcome**. Report it;
do not manufacture coverage.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer running HubbleOps Phase 10 (pilot hardening) on a real customer repository at <path>. Read CLAUDE.md, dev/context.md. This is NOT HubbleOps' first contact with reality — the real-repo loop has run since Phase 2.

OUTCOME: the repo produces an Exposure Map with UNEXPLAINED = 0. Every UNKNOWN is exactly one of:
  1. CLOSED_WITH_EVIDENCE
  2. CLOSED_WITH_RECORDED_HUMAN_DECISION
  3. PRESERVED_UNKNOWN with a precise closing instruction
  4. NEW_PATTERN converted into an anonymized fixture and, where justified, a rule/observer/falsifier
A genuinely undecidable UNKNOWN is a correct result and must never be forced closed to satisfy this gate. The goal is UNEXPLAINED = 0, not UNKNOWN = 0. A Proof Pack is produced for v22→v25 and a PR opened with the guard installed.

PROCESS:
1. `hops scan`, `hops capture` (if tests exist), `hops exposure`. Paste outputs.
2. For each UNKNOWN, assign exactly one of the four outcomes above; for (2) use `hops decide`; for (4) run fixture-writer.
3. `hops migrate --target v25`, `hops verify`; paste receipt.md. HUMAN_REQUIRED with a named untested-radius list is an acceptable pilot outcome; report it, do not manufacture coverage.
4. Update docs/FAILURE_ATLAS.md with every new pattern, fixture id, and which observer caught it.
INVARIANTS: no rule or fixture tuned to a single repo's names; rules pass must-not-match snippets; frozen architecture unchanged.
EVIDENCE REQUIRED: all command outputs; new fixture list; adversarial suite still 100% rejected after rule changes; the UNKNOWN disposition table.
TRAPS: repo-specific hacks in packs; closing UNKNOWNs by assertion; treating UNKNOWN count as the score.
```
