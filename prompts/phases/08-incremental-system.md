# Phase 8 — Incremental system (fact cache, bindings, reverse index)

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` Memory section, `dev/context.md` |
| **Ships** | `store/facts.py`, `store/bindings.py`, `store/reverse_index.py`, `hops impact`, `--incremental` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 9 |

**Memory reduces work, never proof.** This phase is what earns Phase 7 the right to use the
incremental path — and only under a Receipt bound to the *new* ProofScope.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 8. Read CLAUDE.md, docs/ARCHITECTURE.md Memory section, dev/context.md.

OUTCOME: rescanning after a commit costs ≈ changed blobs + import fanout; bindings survive unchanged files; decisions persist; a new Change Pack triggers only affected files — with clean-scan and clean-verify equivalence proven.

DEFINITION OF DONE:
1. store/facts.py: per-file evidence keyed by (blob_hash, rules_hash, analyzer_version); `hops scan --incremental` selects blobs via git diff + fanout(k) from graph/imports.
2. store/bindings.py: wrapper chains with depends_on={blob hashes}; invalidated iff any hash changes.
3. store/reverse_index.py: provider_subject → bindings → files; `hops impact --pack google_ads --changes <jsonl>` lists affected files without full rescan.
4. verify: `hops verify --incremental` recomputes only the causally affected radius but ALWAYS binds the Receipt to the new ProofScope.
5. Property tests: incremental scan == clean scan across randomized edit sequences; incremental verify verdict == clean verify verdict across randomized edits and corruptions; bindings never reused after a dependency hash changes; decisions never re-asked while blob hash unchanged.
6. verify/ has no import from store/facts or store/bindings (memory never feeds proof directly).

INVARIANTS: memory reduces work, never proof; every memory record has provenance and is revocable; a Receipt is never reused across SHAs.
OPEN MIDDLE: cache layout, eviction.
APPROVAL BOUNDARIES: any change to verify/verdict.py.
EVIDENCE REQUIRED: property test output; timing table clean vs incremental on a 500+ file fixture after a 3-file edit; impact output for a synthetic v26 change; incremental-vs-clean verify equivalence output.
NON-GOALS: multi-tenant service, Postgres.
TRAPS: (1) caching by path; (2) skipping fanout; (3) cached NOT_AFFECTED short-circuiting verification; (4) reusing a Receipt under a new ProofScope.
PROCESS: plan mode → dev/plan.md → stop.
```
