# Phase 4 — Dynamic capture + sandbox + sentinel + telemetry reconciliation

|  |  |
|---|---|
| **Status** | NOT STARTED |
| **Reads** | `CLAUDE.md`, `docs/ARCHITECTURE.md` §6 (observers D/E), §8, Memory/Sticky sections, `dev/proposals.md` P-006, `dev/context.md` |
| **Ships** | `sandbox/`, `observe/dynamic/`, `hops capture`, `hops promote`, `packages/hubbleops-sentinel` |
| **Gate** | fresh-session [gate audit](../cross-cutting/gate-audit.md) → `GATE: PASS`, then [real-repo loop](../cross-cutting/real-repo-loop.md) — now including `hops capture` |
| **Then** | Phase 5 |

Test-time capture and production sensing are **different products sharing one versioned event
schema**. They never share a code path.

Procedure: [operating protocol](../../docs/OPERATING_PROTOCOL.md).

## Prompt — paste verbatim in plan mode

```text
ROLE: senior engineer implementing HubbleOps Phase 4. Read CLAUDE.md, docs/ARCHITECTURE.md §6 (observers D/E), §8, Memory/Sticky sections, dev/proposals.md P-006, dev/context.md.

OUTCOME: HubbleOps runs the customer's own test suite in an isolated environment and records every provider call (version, service, method, request text, request type, full stack trace) via either of two modes — pack-supplied per-language hooks, or a language-independent egress proxy parsed with the pack's wire_signature — reconciles those with static candidates and provider telemetry, and ships a standalone production sensor with the same two modes. Proxy mode is the default recommendation because it needs no per-language code; hook mode stays available where a proxy cannot sit in the path. Test-time capture and production sensing are different products sharing one versioned event schema.

DEFINITION OF DONE:
1. sandbox/ package: runner.py, image.py, limits.py, network.py, mounts.py, capture.py — git worktree + rootless docker/podman; read-only mounts where specified; no host env leakage; network default-deny with allowlist; wall-clock timeout; resource limits; full command log to artifacts/. Used by capture (now) and repair (Phase 6). sandbox/verifier_image.py is a SEPARATE image/config for Phase 5; verifier isolation is never a flag on the runner.
2. observe/dynamic/: generic runner + event schema (observe/dynamic/schema.json, versioned), carrying an optional field naming which mode populated it (hook | proxy). Hook mode: per-language injection mechanics (Python sitecustomize, PHP auto_prepend_file, Node --require) are generic loaders that load pack-supplied hooks; the Google-specific interceptor/HTTP patch code lives in packs/google_ads/capture/{python,php,node}/ and is returned by pack.capture_hooks(language). Proxy mode: a generic egress proxy in the sandbox network path parses every outbound request with pack.wire_signature into (service, method, version) — no per-language loader involved. observe/dynamic contains no provider names either way.
3. `hops capture <repo> --pack google_ads --cmd "<test command>"` runs the suite in the sandbox in proxy mode by default (`--capture-mode hook` to opt into per-language hooks); events → Evidence(observer="dynamic"); each stack (hook mode) or each matched request (proxy mode) becomes an observed wrapper chain or a wire-sourced candidate; chains/requests absent statically → candidates with reason OBSERVED_NOT_STATIC. Zero events with call sites present → UNKNOWN_DYNAMIC, never "no usage".
4. observe/telemetry.py reconciles generic (service, method, version) tuples with candidates; unmatched → TELEMETRY_UNEXPLAINED; Exposure Map prints "Production services accounted for N/M".
5. packages/hubbleops-sentinel/: separate pip package, own version/tests, two independent modes — hook mode via provider adapters inside it (sentinel/adapters/google_ads.py: gRPC interceptor or logging-config install), and proxy mode via an egress proxy or the client library's own request logging parsed by pack.wire_signature, needing no per-language adapter; writes local JSONL or exports to a URL in the shared event schema. Proxy mode is the default recommendation in the sentinel's own docs. HARD INVARIANT (tested + hook): never imports hubbleops.*. Output is observer="sentinel" evidence, never a verdict, never closes an UNKNOWN alone.
6. `hops promote` writes confirmed chains into .hubbleops/surface.yml as ast-grep rules with provenance; next scan finds the wrapper in one hop (test).

INVARIANTS: capture never uses production credentials; sandbox denies network by default; event schema versioned; promotion revocable; sentinel independent; capture and sentinel never share a code path; proxy mode and hook mode write the identical event schema, so nothing downstream can tell which mode produced an event.
OPEN MIDDLE: injection mechanics, event transport, proxy implementation (mitmproxy vs. a minimal purpose-built one).
APPROVAL BOUNDARIES: sandbox capability writing outside the worktree; runtime deps in hubbleops-sentinel.
EVIDENCE REQUIRED: capture run on the DI fixture with a recorded stack chain (hook mode); the same fixture captured again in proxy mode producing an equivalent candidate; reconciliation output with a deliberately unmatched telemetry tuple; sentinel `pip install` + smoke test in both modes; promotion round-trip; test_no_provider_leak on observe/dynamic.
NON-GOALS: verification, repair.
TRAPS: (1) two sandboxes; (2) verifier image as a runner flag; (3) Google interceptor code inside observe/; (4) Python-only capture; (5) "no events" = "no usage"; (6) proxy mode treated as a lesser fallback instead of the default; (7) wire_signature parsing logic duplicated instead of shared between capture and sentinel.
PROCESS: plan mode → dev/plan.md → stop.
```
