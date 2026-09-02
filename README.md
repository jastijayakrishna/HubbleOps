# HubbleOps — build repo

Truth Engine + independent Verification Authority for third-party API migrations.
Provider #1: Google Ads (v22 → v25).

**Nothing is implemented yet.** This repo currently contains the build system: the
frozen instructions (`CLAUDE.md`), the operating protocol, and the phase-by-phase
prompts that produce the code.

## Where things live

| Path | What it is |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Frozen agent instructions. Auto-loaded every session. Keep under 130 lines forever. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **Frozen build document. Not yet supplied — see the file.** Every prompt references its sections. |
| [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md) | The ten phases, their gates, and what unlocks what. |
| [docs/OPERATING_PROTOCOL.md](docs/OPERATING_PROTOCOL.md) | How a phase is run: plan → plan review → implement → evidence → gate audit → real-repo loop. |
| [docs/HOOKS.md](docs/HOOKS.md) | The mechanical enforcement layer (`.claude/settings.json`) and how to switch it on. |
| [docs/FAILURE_ATLAS.md](docs/FAILURE_ATLAS.md) | Every real-world pattern found by the real-repo loop. Grows from Phase 2 onward. |
| [prompts/phases/](prompts/phases/) | One file per phase. Paste into a plan-mode session. |
| [prompts/cross-cutting/](prompts/cross-cutting/) | Plan review, gate audit, real-repo loop, red-team, fixture-writer, spec-drift audit. |
| [prompts/_TEMPLATE.md](prompts/_TEMPLATE.md) | Phase prompt template, for phases not yet written. |
| [dev/](dev/) | Working state: `context.md`, `tasks.md`, `plan.md`, `proposals.md`. |
| [.claude/agents/](.claude/agents/) | Subagent definitions: spec-auditor, red-team, fixture-writer. |

Everything here is the split-out form of **Prompt Pack v3**. Every fenced `text` block under
`prompts/` is the pack's prompt **verbatim** — edit the surrounding notes freely, edit the prompts
deliberately.

## Running a phase

```
1. git checkout main && git checkout -b phase-NN-<slug>
2. claude --permission-mode plan          # plan mode
3. paste prompts/phases/NN-*.md           # the fenced prompt, verbatim
4. answer every OPEN QUESTION in dev/plan.md before leaving plan mode
5. FRESH SESSION -> prompts/cross-cutting/plan-review.md   # fix findings
6. implement (accept-edits)               # evidence required for every "done"
7. FRESH SESSION -> prompts/cross-cutting/gate-audit.md    # needs GATE: PASS
8. from Phase 2 on -> prompts/cross-cutting/real-repo-loop.md
9. update dev/context.md + dev/tasks.md before the session ends
10. merge to main, then git tag v0.N     # v1.0 after Phase 10
```

The builder never grades itself. Steps 4 and 6 are fresh sessions, always.

## Before Phase 1 can start

- [ ] `docs/ARCHITECTURE.md` filled in (frozen build document, §1–§19)
- [ ] `.claude/settings.json` activated from [docs/HOOKS.md](docs/HOOKS.md)
- [ ] toolchain present: `uv`, `ruff`, `pyright`, `rg`, `ast-grep`, rootless docker/podman
- [ ] this directory is its own git repo (`git init`) — currently it is not
