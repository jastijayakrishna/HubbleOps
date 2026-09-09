# Build order

Ten phases. Each is gated: no phase starts until the previous one has `GATE: PASS` from a fresh-session
[gate audit](../prompts/cross-cutting/gate-audit.md). From the Phase 2 gate onward, every gate is
followed by a [real-repo loop](../prompts/cross-cutting/real-repo-loop.md).

| # | Phase | Ships | Reads | Tag | Status |
|---|---|---|---|---|---|
| 1 | [Source Closure + text/dependency observers + Ledger + Exposure Map](../prompts/phases/01-source-closure-and-ledger.md) | `hops scan`, `hops exposure`; schemas; SQLite store; import/leak tests | §1–§6 | `v0.1` | COMPLETE |
| 2 | [Google Ads Provider Pack + Change Pack version lattice](../prompts/phases/02-google-ads-pack-and-change-pack.md) | `packs/google_ads`, offline Change Pack (per-version catalogs + computed diffs), `hops pack verify` | §3, §5, §7 | `v0.2` | COMPLETE |
| 3 | [Wrapper Engine](../prompts/phases/03-wrapper-engine.md) | `observe/structure.py`, ast-grep rules, k-hop walk, query skeletons | §6 + Wrapper | `v0.3` | COMPLETE |
| 4 | [Dynamic capture + sandbox + sentinel + telemetry](../prompts/phases/04-dynamic-capture-and-sentinel.md) | `hops capture`, `sandbox/`, `hubbleops-sentinel`, `hops promote` | §6 D/E, §8, Memory | `v0.4` | COMPLETE |
| 5 | [Independent Verification Authority](../prompts/phases/05-verification-authority.md) | `hops verify`, verdict function, Receipt, adversarial suite | §9 + Verdict rule | `v0.5` | IN PROGRESS |
| 6 | [Obligation Engine + deterministic repair](../prompts/phases/06-obligations-and-repair.md) | `hops migrate`, deterministic transforms — **no repair agent** (`dev/plan.md` Part Two) | §7–§8 | `v0.6` | IN PROGRESS |
| 7 | [Proof Pack, PR, Backslide Guard, `.hubbleops` memory](../prompts/phases/07-proof-pack-and-pr.md) | PR body, guard workflow, `hops decide`, `hops impact` — **Action, not a hosted App** | §16–§19 | `v0.7` | NOT STARTED |
| 8 | ~~Incremental system~~ | deferred — see *Deliberately not built* | Memory | — | DEFERRED |
| 9 | ~~Mock pack conformance~~ | deferred — see *Deliberately not built* | §3 | — | DEFERRED |
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
- **The repair agent** (`repair/agent.py`, the sandboxed loop, `pack.repair_tools()`, the retry).
  The composed v22→v25 diff is ~1,577 ADDED, 6 CHANGED, 174 REMOVED, so deterministic transforms
  carry the bulk; the residue is `HUMAN`, which the frozen `obligation.json` already permits.
  *Trigger to build:* the first pilot where `HUMAN` obligations exceed 20% of mapped hunks.
- **The hosted GitHub App.** A committed Action running `hops verify` in the customer's own runner
  satisfies every §17 clause and removes an enterprise security review at pilot stage.
  *Trigger to build:* a customer needing org-wide rollout or `merge_group`.
- **Phase 8's incremental machinery** (fact cache, bindings, reverse index, `--incremental`).
  `hops impact` ships as a report over a full rescan. *Trigger to build:* a repository where a clean
  rescan is too slow to run per PR. No cache lands before the incremental-equals-clean proof.
- **Phase 9's conformance run.** `test_no_provider_leak` and `test_imports` defend the boundary on
  every run; Phase 9 proves it. *Trigger to build:* the second real provider pack.
