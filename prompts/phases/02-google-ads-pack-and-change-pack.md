# Phase 2 — Google Ads Provider Pack + Change Pack version lattice (offline gate)

|  |  |
|---|---|
| **Status** | COMPLETE — `GATE: PASS`; first real-repo loop complete |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §3, §5, §7, `dev/proposals.md` P-005–P-008, `dev/context.md` |
| **Ships** | `packs/google_ads/` full ProviderPack, offline Change Pack (version lattice, computed diffs), `hops pack verify` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, **then the first [real-repo loop](../cross-cutting/real-repo-loop.md)** |
| **Then** | Phase 3 — whose fixtures include every pattern that loop turns up |

v22→v25 was the first commercial target, not the design (P-005): the Change Pack this phase ships
is a catalog per supported version plus a *computed* diff between any two, not one hand-built pair.

The gate does **not** require live Google credentials. Live oracle execution is a Phase 5
requirement.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 2. Read CLAUDE.md, docs/ARCHITECTURE.md §3, §5, §7, dev/proposals.md P-005 through P-008, dev/context.md.

OUTCOME: packs/google_ads/ implements the full ProviderPack contract; catalog/diff work fully OFFLINE from cached, hashed sources over a version lattice (not one hand-built pair); validate() is implemented against a transport abstraction and proven safe with a fake transport; wire_signature parses real logged request paths with zero language-specific code; packs/_mock passes the same conformance suite.

DEFINITION OF DONE:
1. packs/_protocol.py finalizes: ProviderPack { surface, wire_signature, versions(), rules(language), contract: ContractOracle, changes: ChangeCompiler, telemetry: TelemetryAdapter, capture_hooks(language), repair_transforms(), repair_tools(), falsifiers() } as typed Protocols with docstrings. packs/_mock implements all trivially, including a small fixed version lattice for composition testing; tests/unit/test_pack_conformance.py runs against both packs.
2. packs/google_ads/changes.py builds data/catalog_<version>.jsonl for every supported version from v19 to current from (a) googleapis proto diff against the previous version (services, messages, fields, enums), (b) that version's GoogleAdsFieldService catalog (selectable, filterable, sortable, data_type, selectable_with), (c) that version's upgrade guide + release notes parsed into structured claims, (d) client-library compatibility table. Every fact: source_url, retrieved_at, sha256, confidence ∈ {PROVEN, DOCUMENTED}. Ingesting a newly released version reruns this same pipeline against one new version, never a rewrite.
3. Cross-check: (a)∧(b) agree → PROVEN; only (c) → DOCUMENTED; material disagreement → UNKNOWN_PROVIDER_CONTRACT. No LLM adjudication.
4. packs/google_ads/contract.py: catalog(version) reads a single version's catalog; diff(v_from, v_to) is COMPUTED (set difference over subjects between consecutive catalogs) and cached by pair hash, never hand-authored, and for a non-adjacent pair is composed across every consecutive hop (v22→v23→v24→v25). Property test: diff(a, c) == compose(diff(a, b), diff(b, c)) for every subject that survives all hops; a subject whose mapping does not compose cleanly across every hop → UNKNOWN_PROVIDER_CONTRACT, never guessed. validate(request, version) implemented over a Transport interface. Fake-transport tests prove: validate_only is always true; a non-validate_only mutate can never be issued; missing credentials → ORACLE_UNAVAILABLE, never a pass. Live execution is a Phase 5 requirement.
5. packs/google_ads/wire.py: wire_signature regexes parse a request's gRPC method path and REST path into a typed `(service, method, version)` match or explicit UNKNOWN, tested with accompanying `x-goog-api-client` metadata against a corpus of real logged request paths (no live traffic required) — zero per-language code. `x-goog-api-client` is provenance, never endpoint-version authority (P-008).
6. packs/google_ads/telemetry.py parses a Cloud Console methods/versions export into generic (service, method, version) tuples.
7. Change Pack build is idempotent and reproducible without network from data/sources/ (hashed); `hops pack verify google_ads` passes offline over the full lattice.
8. tests/fixtures/google_ads/: known v25 removals (e.g., CustomerLifecycleGoal, CampaignLifecycleGoal) and known field changes appear with PROVEN confidence; a composed diff across a 3+ version gap matches the hand-checked expected mapping.

INVARIANTS: packs/ imported only by app/ and tests. Change Pack hash (over the full lattice) enters ProofScope as `provider_contract_hash` composed with the SurfaceSpec hash Phase 1 already puts there — it never replaces it (P-007); a proof key that stops moving when the surface is edited is one two different recall surfaces can share. Every network fetch cached with sha256 before use. Phase gate does not require live credentials.
OPEN MIDDLE: proto parsing approach, guide parsing heuristics, data layout, how many versions back v19 practically reaches.
APPROVAL BOUNDARIES: any non-read-only / non-validate_only Google call; new dependency for proto parsing.
EVIDENCE REQUIRED: pack build log with source hashes for every ingested version; `hops pack verify google_ads` offline; conformance output for google_ads and _mock; 10 sample Change Pack entries with provenance; a composed multi-hop diff example; fake-transport safety test output; wire_signature test output against logged request paths.
NON-GOALS: obligations, repair, verification, live oracle.
TRAPS: (1) LLM-summarized "facts"; (2) unpinned catalog data; (3) a generic multi-provider framework beyond what two packs need; (4) any mutate path; (5) gating on live credentials; (6) hand-authoring a diff instead of computing it; (7) silently accepting a non-composing mapping instead of UNKNOWN_PROVIDER_CONTRACT.
PROCESS: plan mode → dev/plan.md → stop.
```
