# Phase 2 — Google Ads Provider Pack + Change Pack v22→v25 (offline gate)

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §3, §5, §7, `dev/context.md` |
| **Ships** | `packs/google_ads/` full ProviderPack, offline Change Pack, `hops pack verify` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, **then the first [real-repo loop](../cross-cutting/real-repo-loop.md)** |
| **Then** | Phase 3 — whose fixtures include every pattern that loop turns up |

The gate does **not** require live Google credentials. Live oracle execution is a Phase 5
requirement.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 2. Read CLAUDE.md, docs/ARCHITECTURE.md §3, §5, §7, dev/context.md.

OUTCOME: packs/google_ads/ implements the full ProviderPack contract; catalog/diff work fully OFFLINE from cached, hashed sources; validate() is implemented against a transport abstraction and proven safe with a fake transport; packs/_mock passes the same conformance suite.

DEFINITION OF DONE:
1. packs/_protocol.py finalizes: ProviderPack { surface, rules(language), contract: ContractOracle, changes: ChangeCompiler, telemetry: TelemetryAdapter, capture_hooks(language), repair_transforms(), repair_tools(), falsifiers() } as typed Protocols with docstrings. packs/_mock implements all trivially; tests/unit/test_pack_conformance.py runs against both packs.
2. packs/google_ads/changes.py builds data/changes_v22_v25.jsonl from (a) googleapis proto diff (services, messages, fields, enums), (b) GoogleAdsFieldService catalogs for v22 and v25 (selectable, filterable, sortable, data_type, selectable_with), (c) upgrade guides + release notes parsed into structured claims, (d) client-library compatibility table. Every fact: source_url, retrieved_at, sha256, confidence ∈ {PROVEN, DOCUMENTED}.
3. Cross-check: (a)∧(b) agree → PROVEN; only (c) → DOCUMENTED; material disagreement → UNKNOWN_PROVIDER_CONTRACT. No LLM adjudication.
4. packs/google_ads/contract.py: catalog(version) and diff(v_from, v_to) fully functional offline; validate(request, version) implemented over a Transport interface. Fake-transport tests prove: validate_only is always true; a non-validate_only mutate can never be issued; missing credentials → ORACLE_UNAVAILABLE, never a pass. Live execution is a Phase 5 requirement.
5. packs/google_ads/telemetry.py parses a Cloud Console methods/versions export into generic (service, method, version) tuples.
6. Change Pack build is idempotent and reproducible without network from data/sources/ (hashed); `hops pack verify google_ads` passes offline.
7. tests/fixtures/google_ads/: known v25 removals (e.g., CustomerLifecycleGoal, CampaignLifecycleGoal) and known field changes appear with PROVEN confidence.

INVARIANTS: packs/ imported only by app/ and tests. Change Pack hash enters ProofScope. Every network fetch cached with sha256 before use. Phase gate does not require live credentials.
OPEN MIDDLE: proto parsing approach, guide parsing heuristics, data layout.
APPROVAL BOUNDARIES: any non-read-only / non-validate_only Google call; new dependency for proto parsing.
EVIDENCE REQUIRED: pack build log with source hashes; `hops pack verify google_ads` offline; conformance output for google_ads and _mock; 10 sample Change Pack entries with provenance; fake-transport safety test output.
NON-GOALS: obligations, repair, verification, live oracle.
TRAPS: (1) LLM-summarized "facts"; (2) unpinned catalog data; (3) a generic multi-provider framework beyond what two packs need; (4) any mutate path; (5) gating on live credentials.
PROCESS: plan mode → dev/plan.md → stop.
```
