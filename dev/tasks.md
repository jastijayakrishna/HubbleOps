# Tasks

Updated before every session ends. Phase-level status lives in
[docs/BUILD_ORDER.md](../docs/BUILD_ORDER.md); this file is the working queue.

## Now

- [ ] Fresh-session [gate audit](../prompts/cross-cutting/gate-audit.md) on Phase 1
- [ ] On `GATE: PASS`: merge `phase-01-source-closure-and-ledger` to `main`, tag `v0.1`
- [ ] Install `ast-grep` before Phase 3; rootless docker/podman before Phase 4
- [x] Supply `docs/ARCHITECTURE.md` — the frozen build document
- [x] `git init` this directory
- [x] Install toolchain: `uv`, `ruff`, `pyright`, `rg`
- [x] Activate `.claude/settings.json` per [docs/HOOKS.md](../docs/HOOKS.md)

## Phase gates

- [x] Phase 1 — scan + exposure + ledger *(implemented and green; gate audit not yet run)*
- [ ] Phase 2 — Google Ads pack + Change Pack *(first real-repo loop after this gate)*
- [ ] Phase 3 — wrapper engine
- [ ] Phase 4 — dynamic capture + sentinel
- [ ] Phase 5 — verification authority *(+ red-team, nightly from here)*
- [ ] Phase 6 — obligations + repair
- [ ] Phase 7 — proof pack + PR + guard
- [ ] Phase 8 — incremental system
- [ ] Phase 9 — mock pack conformance
- [ ] Phase 10 — pilot hardening

Each gate is: plan → plan review (fresh) → implement → evidence → gate audit (fresh) → real-repo
loop (from Phase 2).

## Recurring

- [ ] Weekly: [spec-drift audit](../prompts/cross-cutting/spec-drift-audit.md)
- [ ] Nightly from Phase 5: [red-team](../prompts/cross-cutting/red-team.md)

## Blocked / parked

- Telemetry and production-services accounting in the Exposure Map print "not in this ProofScope"
  until Phase 4 supplies a `TelemetryAdapter`; the `Target` line waits on the Phase 2 Change Pack.
