# Phase 7 — Proof Pack, PR, Backslide Guard, `.hubbleops` memory writes

|  |  |
|---|---|
| **Status** | IMPLEMENTED — GATE PENDING |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §16–§19, `dev/proposals.md` P-019/P-020/P-021, `dev/plan.md` Part Two, `dev/context.md` |
| **Ships** | `proof/pr_body.py`, `proof/guard.py`, exact-SHA GitHub Actions, `.hubbleops/` files, `hops decide`, `hops impact`, `hops replay`, `hops prepare-pr` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 8 |

**No proof reuse across SHAs in this phase.** A new commit on the PR branch kills the Receipt and
triggers a full re-verify. The incremental path does not exist until Phase 8 proves it equals the
clean one.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 7. Read CLAUDE.md, docs/ARCHITECTURE.md §16–§19, dev/context.md.

OUTCOME: a verified or explicitly human-required candidate can be prepared locally as complete GitHub PR materials: Proof Pack body, exact-SHA verification Action, cumulative backslide guard, and source-bound `.hubbleops/` memory. This phase performs no remote publication or commit.

DEFINITION OF DONE:
1. proof/pr_body.py renders: discovery, obligations, oracle results, blast-radius table (changed symbol → dependents → covering tests → status), falsifiers, UNKNOWNs with closing instructions, ProofScope hashes.
2. proof/guard.py generates .github/workflows/hubbleops-guard.yml running `hops guard` (ripgrep-only, <2s) over the cumulative retired-surface list in .hubbleops/retired.yml (patterns supplied by the pack at write time); PR fails on reintroduction.
3. Generated read-only GitHub Actions run on `pull_request` and `merge_group`, verify `GITHUB_SHA` exactly, and perform a full `hops verify` on every new SHA. The Action result is the repository-owned status check; no hosted App exists. An incremental path remains forbidden until Phase 8 proves incremental == clean.
4. `.hubbleops/{surface.yml,bindings.json,decisions.yml,retired.yml}` is written locally without overwriting existing memory; retired patterns are cumulative and content-addressed.
5. `hops decide <unknown_id> --value ... --by ...` records a human decision keyed to the blob hash of the source location.

INVARIANTS: PR opened only for VERIFIED_FOR_SCOPE or HUMAN_REQUIRED (labeled, with list); status never green for HUMAN_REQUIRED; no proof reuse across SHAs in this phase; new SHA → old proof dead → new proof required.
OPEN MIDDLE: PR Markdown layout and workflow templates.
APPROVAL BOUNDARIES: remote publication; permissions beyond `contents: read`; a hosted App; incremental proof reuse.
EVIDENCE REQUIRED: deterministic PR body; generated read-only workflows for PR and merge queue; exact-SHA binding; a changed SHA invalidating replay; guard passing then failing on a seeded regression; cumulative memory round trip.
NON-GOALS: UI, multi-repo, incremental verification.
TRAPS: (1) HUMAN_REQUIRED shown green; (2) "radius-only" re-verification before Phase 8 exists; (3) guard needing the full scanner; (4) decisions not tied to blob hash; (5) hidden remote writes or permissions wider than contents:read.
PROCESS: plan mode → dev/plan.md → stop.
```
