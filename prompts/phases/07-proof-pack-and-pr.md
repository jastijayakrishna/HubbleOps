# Phase 7 — Proof Pack, PR, Backslide Guard, `.hubbleops` memory writes

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §16–§19, `dev/context.md` |
| **Ships** | `proof/pr_body.py`, `proof/guard.py`, thin GitHub App, `.hubbleops/` files, `hops decide` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 8 |

**No proof reuse across SHAs in this phase.** A new commit on the PR branch kills the Receipt and
triggers a full re-verify. The incremental path does not exist until Phase 8 proves it equals the
clean one.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 7. Read CLAUDE.md, docs/ARCHITECTURE.md §16–§19, dev/context.md.

OUTCOME: a verified candidate becomes a GitHub PR with the Proof Pack as body and artifact, a HubbleOps status check bound to the exact SHA, a backslide-guard workflow, and committed .hubbleops/ memory files.

DEFINITION OF DONE:
1. proof/pr_body.py renders: discovery, obligations, oracle results, blast-radius table (changed symbol → dependents → covering tests → status), falsifiers, UNKNOWNs with closing instructions, ProofScope hashes.
2. proof/guard.py generates .github/workflows/hubbleops-guard.yml running `hops guard` (ripgrep-only, <2s) over the cumulative retired-surface list in .hubbleops/retired.yml (patterns supplied by the pack at write time); PR fails on reintroduction.
3. GitHub App (thin): posts a status check keyed to the exact candidate SHA. Any new commit to the PR branch invalidates the previous Receipt and ProofScope and triggers a full `hops verify <base_sha> <new_candidate_sha>`. After Phase 8 proves incremental == clean verification under causal invalidation, the implementation MAY use the proven incremental path, but the resulting Receipt must be bound to the new ProofScope. merge_group event supported; the exact merge-group SHA is verified.
4. .hubbleops/{surface.yml,bindings.json,decisions.yml,retired.yml} written with provenance and included in the PR.
5. `hops decide <unknown_id> --value ... --by ...` records a human decision keyed to the blob hash of the source location.

INVARIANTS: PR opened only for VERIFIED_FOR_SCOPE or HUMAN_REQUIRED (labeled, with list); status never green for HUMAN_REQUIRED; no proof reuse across SHAs in this phase; new SHA → old proof dead → new proof required.
OPEN MIDDLE: App implementation, templates.
APPROVAL BOUNDARIES: GitHub permissions beyond contents:read, pull_requests:write, checks:write.
EVIDENCE REQUIRED: a real PR on a test repo with body and status; a second commit on the branch showing the old check invalidated and a full re-verify; guard passing then failing on a seeded regression.
NON-GOALS: UI, multi-repo, incremental verification.
TRAPS: (1) HUMAN_REQUIRED shown green; (2) "radius-only" re-verification before Phase 8 exists; (3) guard needing the full scanner; (4) decisions not tied to blob hash.
PROCESS: plan mode → dev/plan.md → stop.
```
