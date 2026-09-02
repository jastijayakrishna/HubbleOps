# Phase 3 — Wrapper Engine (generic structural observer + pack rules)

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §6 and the Wrapper section, `dev/context.md` |
| **Ships** | `observe/structure.py`, `graph/imports.py`, per-language ast-grep rules, query skeletons, flagged `ai_triage` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) |
| **Then** | Phase 4 |

Fixtures must include **every pattern collected by the real-repo loop after Phase 2** — read
[docs/FAILURE_ATLAS.md](../../docs/FAILURE_ATLAS.md) before planning.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 3. Read CLAUDE.md, docs/ARCHITECTURE.md §6 and the Wrapper section, dev/context.md.

OUTCOME: observe/structure.py finds provider usage hidden behind wrappers, factories, base classes, and dynamically built queries, using ast-grep rules supplied by the active pack and a language-agnostic k-hop backward walk (k=5), producing a conservative superset with explicit UNKNOWN reasons.

DEFINITION OF DONE:
1. packs/google_ads/rules/{python,php,javascript,typescript,java}.yml: families (a) sinks, (b) version carriers, (c) request-string sinks; each rule has an id and a test file under rules/tests/ with must-match / must-not-match snippets run by `ast-grep test`. packs/_mock/rules/ has a minimal equivalent.
2. graph/imports.py builds an import/symbol graph from ast-grep output (nodes = definitions, edges = calls/imports), serialized deterministically; no provider knowledge.
3. observe/structure.py: `scan(closure, rules: RuleSet, graph) -> list[Evidence]`. For each sink hit → enclosing function = W1 → carried params → callers via permissive name+arity matching → argument resolution: literal → resolved; variable → next hop; config/env read → UNKNOWN_CONFIG(key). Depth ≤ 5. Overrides/subclasses/factory registrations inherit wrapper status.
4. Query skeletons: for concatenation/f-string/format/template chains, emit Evidence(claim_type="request_skeleton") with literal fragments and holes; holes → UNKNOWN_QUERY_HOLE. The observer does NOT validate fields; field validation against the catalog is the Obligation Engine's job (Phase 6) via the injected ContractOracle.
5. Cross-service hops (internal HTTP/queue/RPC carrying a query or version) → EXTERNAL_BOUNDARY candidate naming the payload.
6. observe/ai_triage.py: only for candidates unresolved after 3–5; ≤2 files of context; one question per call; output DERIVED_AI_EVIDENCE; cannot alone change status. Feature-flagged; tests run with it off.
7. Missing/incompatible ast-grep → TOOLING_MISSING; if scan is forced, every INSIDE file becomes FILE_UNSCANNED; never a silently smaller ledger.
8. Metamorphic tests: rename wrapper, move to another file, split query across variables, add an intermediate hop → affected set unchanged.
9. Fixtures: ≥10 wrapper patterns (gateway class, DI container, abstract adapter + 2 subclasses, factory registry, decorator, async wrapper, config-driven version, f-string query, template query, cross-service queue payload) plus every pattern collected by the real-repo loop after Phase 2.

INVARIANTS: no custom parser; no build required to scan; every unresolved path ends in a named UNKNOWN; walker is language-agnostic — only rules are per-language; observe/ has no provider names.
OPEN MIDDLE: matching heuristics, graph representation, caching.
APPROVAL BOUNDARIES: SCIP indexing (only as optional adapter behind a flag); any LLM call outside ai_triage.py.
EVIDENCE REQUIRED: `ast-grep test` output for all rule files; fixture pass; printed wrapper chain for the DI fixture with each hop and carried params; metamorphic test output; test_no_provider_leak output.
NON-GOALS: dynamic capture, verification, repair, field validation.
TRAPS: (1) precision-first pruning; (2) AI filter closing UNKNOWNs; (3) depth-limiting to 2–3 hops; (4) per-language walkers; (5) validating GAQL fields inside observe/.
PROCESS: plan mode → dev/plan.md → stop.
```
