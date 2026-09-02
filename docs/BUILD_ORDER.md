# Build order

Ten phases. Each is gated: no phase starts until the previous one has `GATE: PASS` from a fresh-session
[gate audit](../prompts/cross-cutting/gate-audit.md). From the Phase 2 gate onward, every gate is
followed by a [real-repo loop](../prompts/cross-cutting/real-repo-loop.md).

| # | Phase | Ships | Reads | Tag | Status |
|---|---|---|---|---|---|
| 1 | [Source Closure + text/dependency observers + Ledger + Exposure Map](../prompts/phases/01-source-closure-and-ledger.md) | `hops scan`, `hops exposure`; schemas; SQLite store; import/leak tests | §1–§6 | `v0.1` | NOT STARTED |
| 2 | [Google Ads Provider Pack + Change Pack v22→v25](../prompts/phases/02-google-ads-pack-and-change-pack.md) | `packs/google_ads`, offline Change Pack, `hops pack verify` | §3, §5, §7 | `v0.2` | NOT STARTED |
| 3 | [Wrapper Engine](../prompts/phases/03-wrapper-engine.md) | `observe/structure.py`, ast-grep rules, k-hop walk, query skeletons | §6 + Wrapper | `v0.3` | NOT STARTED |
| 4 | [Dynamic capture + sandbox + sentinel + telemetry](../prompts/phases/04-dynamic-capture-and-sentinel.md) | `hops capture`, `sandbox/`, `hubbleops-sentinel`, `hops promote` | §6 D/E, §8, Memory | `v0.4` | NOT STARTED |
| 5 | [Independent Verification Authority](../prompts/phases/05-verification-authority.md) | `hops verify`, verdict function, Receipt, adversarial suite | §9 + Verdict rule | `v0.5` | NOT STARTED |
| 6 | [Obligation Engine + Repair Worker](../prompts/phases/06-obligations-and-repair.md) | `hops migrate`, deterministic transforms, bounded agent, one retry | §7–§8 | `v0.6` | NOT STARTED |
| 7 | [Proof Pack, PR, Backslide Guard, `.hubbleops` memory](../prompts/phases/07-proof-pack-and-pr.md) | PR body, status check, guard workflow, `hops decide` | §16–§19 | `v0.7` | NOT STARTED |
| 8 | [Incremental system](../prompts/phases/08-incremental-system.md) | fact cache, bindings, reverse index, `hops impact` | Memory | `v0.8` | NOT STARTED |
| 9 | [Mock pack conformance](../prompts/phases/09-mock-pack-conformance.md) | full pipeline on `packs/_mock` with zero generic-layer changes | §3 | `v0.9` | NOT STARTED |
| 10 | [Pilot hardening](../prompts/phases/10-pilot-hardening.md) | real customer repo → Exposure Map, Proof Pack, PR, guard | — | `v1.0` | NOT STARTED |

## Dependency notes

- **Verification (5) lands before repair (6).** The authority must exist before anything is allowed
  to produce a candidate for it to judge.
- **Phase 3 fixtures include every pattern collected by the real-repo loop after Phase 2.**
- **Phase 7 re-verifies fully on every new SHA.** Only after Phase 8 proves incremental == clean
  under causal invalidation may the incremental path be used — and the Receipt still binds to the
  new ProofScope.
- **Phase 9 is the architecture test.** If it requires a change under `core/ closure/ observe/
  graph/ obligations/ sandbox/ repair/ verify/ proof/ store/`, stop and write `dev/proposals.md`.
- **Phase 10 is not first contact with reality** — the real-repo loop has run since Phase 2.

## Deliberately not built

- A second real provider pack. `packs/_mock` is the abstraction test. A real second provider is
  built only when a paying reason exists.
- Any GraphQL-specific code, `packs/shopify/`, UI, multi-repo, Postgres, multi-tenant service.
