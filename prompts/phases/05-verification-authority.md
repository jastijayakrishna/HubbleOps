# Phase 5 — Independent Verification Authority (before repair)

|  |  |
|---|---|
| **Status** | IMPLEMENTED — GATE PENDING |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §9 and the Verdict rule, `dev/context.md` |
| **Ships** | `hops verify`, pure verdict function, `sandbox/verifier_image.py`, Receipt, adversarial suite |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, plus [red-team](../cross-cutting/red-team.md) (nightly thereafter), then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 6 |

**The authority is built before anything is allowed to produce a candidate for it to judge.**
No live provider credential is required: P-020 permits explicitly labelled catalog authority, and
P-021 extends that credential-free path to catalog-provable Google Ads mutate shapes.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 5. Read CLAUDE.md, docs/ARCHITECTURE.md §9 and the Verdict rule, dev/context.md.

OUTCOME: `hops verify <base_sha> <candidate_sha> --pack google_ads` runs in its own image with the candidate mounted read-only and no access to repair artifacts, receives the pack by injection from app/, and returns a pure verdict + Receipt; every deliberately wrong migration in tests/adversarial is rejected.

DEFINITION OF DONE:
1. verify/audit.py: full independent rescan (closure, text, deps, structure, dynamic) of the candidate using the injected pack; old versions/namespaces/endpoints/removed fields (from the Change Pack) extinct or explained; obligations reconciled one by one with evidence ids.
2. verify/oracle.py: every captured request text → ContractOracle.validate(request, target_version) (Google: Search fields and Mutate protobuf JSON shapes vs. the target catalog; validate_only when a live transport is configured). Results carry request hash + timestamp; rejection → FAILED with provider error verbatim. Every result names the authority that decided it (P-020/P-021): LIVE for the provider, CATALOG for the pack's own version catalog. An acceptance naming no authority is refused. ORACLE_UNAVAILABLE → verdict at most UNKNOWN. This phase does NOT require live execution against a provider: the product engages a customer repository read-only and asks for no provider credentials.
3. verify/radius.py: Δ from `git diff` mapped to definitions; R = reach(graph, Δ); diff containment (hunk ↦ obligation id or COLLATERAL(reason), else FAIL); FROZEN BASELINE TESTS copied from base SHA run against candidate with coverage → frozen_baseline_tests_pass (proof); candidate tests run separately (evidence only, never in verdict); C: test → files; UNKNOWN_BLAST = R \ ⋃C(passing frozen tests), by module.
4. verify/behavior.py: request_shape_differential_pass (base capture vs candidate capture; every changed shape ↦ obligation) and response_consumer_check_pass (no read of a removed/renamed field's old name in response handling). Explicit booleans.
5. verify/falsify.py: runs pack.falsifiers() matching detected failure classes.
6. verify/conserve.py: UNKNOWN_after ⊆ UNKNOWN_before ∪ evidence-linked ∪ decision-linked, else FAIL.
7. verify/verdict.py: pure, total over a typed VerificationResult.
   VERIFIED_FOR_SCOPE iff audit_pass ∧ oracle_all_accepted ∧ zero_unexplained_hunks ∧ UNKNOWN_BLAST = ∅ ∧ request_shape_differential_pass ∧ response_consumer_check_pass ∧ frozen_baseline_tests_pass ∧ falsifiers_pass ∧ unknown_conservation_pass.
   Only UNKNOWN_BLAST ≠ ∅ → HUMAN_REQUIRED with module list. ORACLE_UNAVAILABLE / unresolvable → UNKNOWN. Any FAIL → FAILED with reasons.
   Property tests: every input maps to exactly one verdict; flipping ANY single pass-flag from a VERIFIED input never yields VERIFIED.
8. sandbox/verifier_image.py: separate image; candidate read-only; no access to repair sandbox paths, agent logs, or change manifests; image hash in ProofScope. tests/unit/test_isolation.py proves it.
9. proof/receipt.py: receipt.json + receipt.md (ARCHITECTURE.md §16 format), bound to ProofScope.
10. tests/adversarial/: ≥10 corrupted candidates (hidden v22 override; deleted test; weakened assertion; unrelated query change; old generated namespace; field renamed in query but old name read in response; coverage removed from a radius module; verifier-owned file edited; removed resource reintroduced; UNKNOWN closed without evidence). All non-VERIFIED with correct reason.

INVARIANTS: verifier consumes only base, candidate, Change Pack, ProofScope, injected pack; never reads agent artifacts as truth; verify/ never imports repair/, packs/, or sandbox/runner; candidate tests never enter the verdict.
OPEN MIDDLE: coverage tooling per language, graph library, receipt rendering.
APPROVAL BOUNDARIES: weakening the verdict rule; any non-validate_only oracle call.
EVIDENCE REQUIRED: adversarial suite output with rejection reasons; receipt.md for one good and one bad candidate; isolation test; verdict property tests; an oracle run log with request hashes and the authority that decided each (P-020 retires the live-provider requirement; a CATALOG-authority log satisfies this line).
NON-GOALS: repair, PR, memory.
TRAPS: (1) diff from the agent's manifest instead of git; (2) name-matching tests to modules; (3) "tests pass" = radius covered; (4) time/order-dependent verdict; (5) a check implemented but not wired into the verdict; (6) candidate tests standing in for frozen tests.
PROCESS: plan mode → dev/plan.md → stop.
```
