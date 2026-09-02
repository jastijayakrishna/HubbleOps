# Failure Atlas

Every real-world pattern HubbleOps has met and how it was handled. Fed by the
[real-repo loop](../prompts/cross-cutting/real-repo-loop.md) from the Phase 2 gate onward, and by
[Phase 10](../prompts/phases/10-pilot-hardening.md) pilot hardening.

Rules:

- No customer identifiers. Anonymize the pattern; keep the shape.
- One row per pattern, added the moment it is found — not at the end of a phase.
- A pattern that is genuinely undecidable statically gets `PRESERVED UNKNOWN` in *Disposition* and a
  precise closing instruction. That is a correct outcome, not a gap.
- No rule tuned to a single repo's names. If it can't generalize, it's a fixture, not a rule.

| ID | Pattern | Language | Found by | Should be caught by | Phase | Fixture | Disposition |
|---|---|---|---|---|---|---|---|
| *(empty — first entries arrive at the Phase 2 real-repo loop)* | | | | | | | |
