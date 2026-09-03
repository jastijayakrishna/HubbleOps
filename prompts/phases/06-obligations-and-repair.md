# Phase 6 — Obligation Engine + Repair Worker (untrusted)

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §7–§8, `dev/proposals.md` P-005, `dev/context.md` |
| **Ships** | `obligations/engine.py`, `repair/deterministic.py`, `repair/agent.py`, `hops migrate` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 7 |

The repair worker is **untrusted**. It produces a candidate SHA, never a verdict; its exit code
never encodes one. Its change manifest is a HINT, never truth.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 6. Read CLAUDE.md, docs/ARCHITECTURE.md §7–§8, dev/proposals.md P-005, dev/context.md.

OUTCOME: `hops migrate <repo> --pack google_ads [--target latest|vNN]` produces a candidate commit SHA from obligations via pack-supplied deterministic transforms → pack-supplied repair tools → Claude Code, inside the repair sandbox, with one verifier-fed retry; it never produces a verdict. `--target` defaults to `latest` supported by the resolved SDK line; obligations are generated per (candidate, candidate's own effective version, target), so a repo with candidates on more than one version gets more than one obligation set against the one target.

DEFINITION OF DONE:
1. obligations/engine.py: `build(change_pack, ledger, oracle: ContractOracle, target: str) -> list[Obligation]`; each candidate's target-version catalog is reached by composing the Change Pack's consecutive diffs from the candidate's own effective version (read off the ledger's already-resolved claim, never a repo-wide "current version") to `target`; validates request skeleton literals against that catalog via the injected oracle; every AFFECTED candidate → ≥1 obligation (file:line, current, required_state, repair_class, verification_method) keyed by (candidate, effective_version, target); an SDK-version bump is itself an obligation when a candidate's effective version differs from target. UNKNOWNs → PRESERVE_UNKNOWN obligations. Obligations whose composed diff does not resolve cleanly (P-005, UNKNOWN_PROVIDER_CONTRACT) → PRESERVE_UNKNOWN, never guessed. No provider names in obligations/.
2. repair/deterministic.py: generic transform runner (precondition check → apply → post-check). Transform definitions live in packs/google_ads/repairs/ (version-literal rewrite, SDK pin bump, generated-namespace rename, REST path re-versioning) and are returned by pack.repair_transforms(); each has before/after unit tests.
3. repair/agent.py: bounded prompt from obligations + evidence + Change Pack excerpts; runs Claude Code in the sandbox/ repair image with pack.repair_tools() installed (Google: the Developer Assistant plugin); tool allowlist excludes git push, network beyond allowlist, and paths under verify/; the agent's change manifest is stored as HINT only. Prompt explicitly forbids edits outside obligations and test weakening.
4. Loop: repair → verify (Phase 5) → failure evidence → one retry → stop. Output: candidate SHA + artifacts; exit code never encodes a verdict.
5. tests: transforms on fixtures; fake-agent test proving prompt contains obligations and excludes verifier internals; two fixtures end-to-end through verify.

INVARIANTS: repair cannot write verify/, .hubbleops/decisions.yml, receipts; no production credentials; repair/ and obligations/ contain no provider names.
OPEN MIDDLE: prompt wording, retry heuristics, transform internals.
APPROVAL BOUNDARIES: sandbox network allowlist expansion; agent tools outside the container.
EVIDENCE REQUIRED: integration log repair → verify → retry → verdict from verify only; the exact agent prompt for one fixture; transform tests; test_no_provider_leak on repair/ and obligations/; a fixture with candidates on two different effective versions producing two obligation sets against one --target.
NON-GOALS: PR, memory.
TRAPS: (1) transforms hard-coded in repair/; (2) agent confidence passed to the verifier; (3) test mutation to pass; (4) skipping the oracle in obligation building; (5) one repo-wide "current version" instead of each candidate's own effective version; (6) forcing a non-composing diff closed instead of PRESERVE_UNKNOWN.
PROCESS: plan mode → dev/plan.md → stop.
```
