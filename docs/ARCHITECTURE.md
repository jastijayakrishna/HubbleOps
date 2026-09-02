# HubbleOps — Architecture

> ## ⚠ NOT YET SUPPLIED
>
> This is the **frozen build document**. It overrides anything the agent believes, and every phase
> prompt references it by section number. **Paste your architecture document over this file before
> starting Phase 1.**
>
> The section list below is not architecture — it is the *index the prompts already depend on*,
> derived from what each phase prompt cites. Keep these numbers stable; if your document numbers
> sections differently, update the `§` references in `prompts/phases/` to match.
>
> Nothing here is invented content. Every "must define" line below is a requirement a phase prompt
> or a Law in `CLAUDE.md` already places on this document.

## Section index — what each section must define, and who reads it

| § | Must define | Read by |
|---|---|---|
| §1 | *(cited by Phase 1)* | Phase 1 |
| §2 | *(cited by Phase 1)* | Phase 1 |
| §3 | **FROZEN.** The `Observer` and `ProviderPack` interfaces (and every sub-protocol: `SurfaceSpec`, `RuleSet`, `ContractOracle`, `ChangeCompiler`, `TelemetryAdapter`, `CaptureHooks`, `RepairTransforms`, `RepairTools`, `Falsifiers`). | Phases 1, 2, 9 |
| §4 | The Exposure Map output format — counts, and each UNKNOWN's "close with" instruction. | Phase 1 |
| §5 | **FROZEN.** The `Memory` interface. | Phases 1, 2 |
| §6 | The observer set A–E (text, dependency, structural, dynamic, telemetry) and the **Wrapper** section: k-hop backward walk, argument resolution, query skeletons. | Phases 1, 3, 4 |
| §7 | Change Pack + Obligation model: obligation fields, repair classes, verification methods. | Phases 2, 6 |
| §8 | Sandbox model: repair runner vs. verifier image, capture, limits, network, mounts. | Phases 4, 6 |
| §9 | Verification Authority + **the Verdict rule** (reproduced as a Law in `CLAUDE.md`). **FROZEN.** | Phase 5 |
| §10–§15 | *(unreferenced by the current prompt pack — renumber freely, or leave for your document's own material)* | — |
| §16 | Receipt / Proof Pack format (`receipt.json`, `receipt.md`). | Phases 5, 7 |
| §17–§19 | PR body, backslide guard, `.hubbleops/` memory files, human decisions. | Phase 7 |
| *Memory / Sticky* | Fact cache, bindings, reverse index, invalidation, sticky knowledge — memory reduces work, never proof. | Phases 4, 8 |

## Frozen surface — changes go to `dev/proposals.md`, never edited in place

- **Schemas:** `Evidence`, `Candidate`, `Obligation`, `ProofScope`, `Receipt` (`core/schemas/*.json`)
- **Interfaces:** `Observer`, `ProviderPack` (and sub-protocols), `Memory` (§3, §5)
- **The verdict function** (§9)

## Reminder — the two proofs

1. We found every observable usage.
2. The repair broke nothing else.

Everything in this document exists to make one of those two statements checkable by a party that
did not perform the migration.
