# Phase 4 — Dynamic capture + sandbox + sentinel + telemetry reconciliation

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §6 (observers D/E), §8, Memory/Sticky sections, `dev/context.md` |
| **Ships** | `sandbox/`, `observe/dynamic/`, `hops capture`, `hops promote`, `packages/hubbleops-sentinel` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) — now including `hops capture` |
| **Then** | Phase 5 |

Test-time capture and production sensing are **different products sharing one versioned event
schema**. They never share a code path.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 4. Read CLAUDE.md, docs/ARCHITECTURE.md §6 (observers D/E), §8, Memory/Sticky sections, dev/context.md.

OUTCOME: HubbleOps runs the customer's own test suite in an isolated environment with pack-supplied capture hooks that record every provider call (version, service, method, request text, request type, full stack trace), reconciles those with static candidates and provider telemetry, and ships a standalone production sensor. Test-time capture and production sensing are different products sharing one versioned event schema.

DEFINITION OF DONE:
1. sandbox/ package: runner.py, image.py, limits.py, network.py, mounts.py, capture.py — git worktree + rootless docker/podman; read-only mounts where specified; no host env leakage; network default-deny with allowlist; wall-clock timeout; resource limits; full command log to artifacts/. Used by capture (now) and repair (Phase 6). sandbox/verifier_image.py is a SEPARATE image/config for Phase 5; verifier isolation is never a flag on the runner.
2. observe/dynamic/: generic runner + event schema (observe/dynamic/schema.json, versioned). Per-language injection mechanics (Python sitecustomize, PHP auto_prepend_file, Node --require) are generic loaders that load pack-supplied hooks. The Google-specific interceptor/HTTP patch code lives in packs/google_ads/capture/{python,php,node}/ and is returned by pack.capture_hooks(language). observe/dynamic contains no provider names.
3. `hops capture <repo> --pack google_ads --cmd "<test command>"` runs the suite in the sandbox; events → Evidence(observer="dynamic"); each stack becomes an observed wrapper chain; chains absent statically → candidates with reason OBSERVED_NOT_STATIC. Zero events with call sites present → UNKNOWN_DYNAMIC, never "no usage".
4. observe/telemetry.py reconciles generic (service, method, version) tuples with candidates; unmatched → TELEMETRY_UNEXPLAINED; Exposure Map prints "Production services accounted for N/M".
5. packages/hubbleops-sentinel/: separate pip package, own version/tests, provider adapters inside it (sentinel/adapters/google_ads.py: gRPC interceptor or logging-config install); writes local JSONL or exports to a URL in the shared event schema. HARD INVARIANT (tested + hook): never imports hubbleops.*. Output is observer="sentinel" evidence, never a verdict, never closes an UNKNOWN alone.
6. `hops promote` writes confirmed chains into .hubbleops/surface.yml as ast-grep rules with provenance; next scan finds the wrapper in one hop (test).

INVARIANTS: capture never uses production credentials; sandbox denies network by default; event schema versioned; promotion revocable; sentinel independent; capture and sentinel never share a code path.
OPEN MIDDLE: injection mechanics, event transport.
APPROVAL BOUNDARIES: sandbox capability writing outside the worktree; runtime deps in hubbleops-sentinel.
EVIDENCE REQUIRED: capture run on the DI fixture with a recorded stack chain; reconciliation output with a deliberately unmatched telemetry tuple; sentinel `pip install` + smoke test; promotion round-trip; test_no_provider_leak on observe/dynamic.
NON-GOALS: verification, repair.
TRAPS: (1) two sandboxes; (2) verifier image as a runner flag; (3) Google interceptor code inside observe/; (4) Python-only capture; (5) "no events" = "no usage".
PROCESS: plan mode → dev/plan.md → stop.
```
