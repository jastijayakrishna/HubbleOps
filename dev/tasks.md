# Tasks

Updated before every session ends. Phase-level status lives in
[docs/BUILD_ORDER.md](../docs/BUILD_ORDER.md); this file is the working queue.

## Now

- [ ] Supply `docs/ARCHITECTURE.md` — the frozen build document (§1–§9, §16–§19, Memory/Wrapper)
- [ ] `git init` this directory (currently tracked, unintentionally, by the home-directory repo)
- [ ] Install toolchain: `uv`, `ruff`, `pyright`, `rg`, `ast-grep`, rootless docker/podman
- [ ] Activate `.claude/settings.json` per [docs/HOOKS.md](../docs/HOOKS.md) — at the start of Phase 1

## Phase gates

- [ ] Phase 1 — scan + exposure + ledger
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

*(with the reason and what would unblock it)*
