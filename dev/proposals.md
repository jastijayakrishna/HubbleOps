# Proposals

The **only** place a change to the frozen surface may be requested. Nothing frozen is ever
redesigned in place.

Frozen surface (from [CLAUDE.md](../CLAUDE.md)):

- **Schemas** — `Evidence`, `Candidate`, `Obligation`, `ProofScope`, `Receipt` (`core/schemas/*.json`)
- **Interfaces** — `Observer`, `ProviderPack` (and sub-protocols), `Memory`
  (`docs/ARCHITECTURE.md` §3, §5)
- **The verdict function** — `verify/verdict.py`

Also record here: any new dependency, framework, database, or service outside the `CLAUDE.md` stack
list, and any generic-layer change demanded by [Phase 9](../prompts/phases/09-mock-pack-conformance.md).

Nothing here is approved until a human writes a decision on it.

---

## Template

### P-NNN — <title>

| | |
|---|---|
| **Raised** | YYYY-MM-DD, Phase N |
| **Touches** | which frozen item / which stack rule |
| **Status** | OPEN / ACCEPTED / REJECTED |

**What forced this.** The concrete case that cannot be handled within the current frozen surface.
A hypothetical is not a forcing case.

**Proposed change.** Exact, minimal.

**Blast radius.** What else must change; which gates must be re-run; whether existing Receipts
survive (they usually do not — a contract change moves ProofScope).

**Alternatives rejected, and why.**

**Decision.** Who, when, what.

---

*(no proposals yet)*
