# Phase 9 — Mock pack conformance + provider-agnostic proof

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §3, `dev/context.md` |
| **Ships** | `tests/integration/test_mock_pack_e2e.py`, extended leak test — and **no generic-layer changes** |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS` |
| **Then** | Phase 10 |

This is the architecture test. The pass condition is a **`git diff --stat` that touches only
`packs/_mock/`, `tests/`, and `docs/`.** If a generic-layer change is required, that is the finding
— stop and write `dev/proposals.md`.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 9. Read CLAUDE.md, docs/ARCHITECTURE.md §3, dev/context.md.

OUTCOME: the entire pipeline scan → capture → obligations → repair(deterministic only) → verify → receipt runs end-to-end against packs/_mock with ZERO changes to core/, closure/, observe/, graph/, obligations/, sandbox/, repair/, verify/, proof/, store/, demonstrating that Google Ads semantics have not leaked into generic layers.

DEFINITION OF DONE:
1. tests/integration/test_mock_pack_e2e.py passes on a fixture repo using the mock surface, including a mock ContractOracle that rejects a known-bad request and a mock falsifier.
2. tests/unit/test_no_provider_leak.py extended to all generic dirs; passes.
3. `git diff --stat <phase8_tag>..HEAD` shows changes only under packs/_mock/, tests/, docs/.
4. If a generic-layer change is required, STOP and write dev/proposals.md; do not make the change.

DELETE if present: packs/shopify/, any GraphQL-specific code, any second real provider. The mock pack is the architecture test; a real second provider is built only when a paying reason exists.
EVIDENCE REQUIRED: e2e output; leak test output; git diff --stat.
TRAPS: "temporary" provider branches in generic code; building a second real pack to "prove" the abstraction.
PROCESS: plan mode → dev/plan.md → stop.
```
