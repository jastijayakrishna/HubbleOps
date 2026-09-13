# Phase 6 — Obligation Engine + Deterministic Repair (untrusted)

|  |  |
|---|---|
| **Status** | IMPLEMENTED — GATE PENDING |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §7–§8, `dev/proposals.md` P-005/P-014, `dev/plan.md` Part Two, `dev/context.md` |
| **Ships** | `obligations/engine.py`, `repair/deterministic.py`, pack transforms, `hops migrate` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 7 |

The repair worker is **untrusted**. It produces a candidate SHA, never a verdict; its exit code
never encodes one. Its change manifest is a HINT, never truth.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 6. Read CLAUDE.md, docs/ARCHITECTURE.md §7–§8, dev/proposals.md P-005, dev/context.md.

OUTCOME: `hops migrate <repo> --pack google_ads [--target latest|vNN]` derives source-bound obligations and applies pack-supplied deterministic transforms to the working tree; it produces a repair report, never a commit or verdict. Unsupported and non-deterministic work remains HUMAN or PRESERVE_UNKNOWN. Obligations are generated per (candidate, candidate's own effective version, target), so a repo with candidates on more than one version gets more than one obligation set against the one target.

DEFINITION OF DONE:
1. obligations/engine.py: `build(change_pack, ledger, oracle: ContractOracle, target: str) -> list[Obligation]`; each candidate's target-version catalog is reached by composing the Change Pack's consecutive diffs from the candidate's own effective version (read off the ledger's already-resolved claim, never a repo-wide "current version") to `target`; validates request skeleton literals against that catalog via the injected oracle; every AFFECTED candidate → ≥1 obligation (file:line, current, required_state, repair_class, verification_method) keyed by (candidate, effective_version, target); an SDK-version bump is itself an obligation when a candidate's effective version differs from target. UNKNOWNs → PRESERVE_UNKNOWN obligations. Obligations whose composed diff does not resolve cleanly (P-005, UNKNOWN_PROVIDER_CONTRACT) → PRESERVE_UNKNOWN, never guessed. No provider names in obligations/.
2. repair/deterministic.py: generic transform runner (precondition check → apply → post-check). Transform definitions live in packs/google_ads/repairs/ (version-literal rewrite, SDK pin bump, generated-namespace rename, REST path re-versioning) and are returned by pack.repair_transforms(); each has before/after unit tests.
3. `PROVIDER_TOOL` and `AGENT` repair classes route to HUMAN. No repair agent, model runtime, provider tool, network expansion, or automated retry ships in this compressed tier; the measured trigger is a pilot where HUMAN obligations exceed 20% of mapped hunks.
4. `hops migrate` writes only deterministic results to the working tree and emits the complete repair report. It does not commit; the user owns candidate-SHA creation, and only Phase 5 verification can issue a verdict.
5. tests: every transform has positive, negative and postcondition cases; throwing transforms fail closed; both packs satisfy transform conformance; the `python_pinned_v22` fixture runs end to end through migration and independent verification.

INVARIANTS: repair cannot write verify/, .hubbleops/decisions.yml, receipts; no production credentials; repair/ and obligations/ contain no provider names.
OPEN MIDDLE: deterministic transform internals and report rendering.
APPROVAL BOUNDARIES: adding a repair agent or provider tool; sandbox network expansion; automatic commits.
EVIDENCE REQUIRED: integration log migrate → verify with the verdict from verify only; transform tests; test_no_provider_leak on repair/ and obligations/; a fixture with candidates on two different effective versions producing two obligation sets against one --target.
NON-GOALS: PR, memory.
TRAPS: (1) transforms hard-coded in repair/; (2) repair output passed to the verifier as truth; (3) test mutation to pass; (4) skipping the oracle in obligation building; (5) one repo-wide "current version" instead of each candidate's own effective version; (6) forcing a non-composing diff closed instead of PRESERVE_UNKNOWN; (7) committing on the user's behalf.
PROCESS: plan mode → dev/plan.md → stop.
```
