# Fixture-writer

**When:** on every `NEW_PATTERN` from the [real-repo loop](real-repo-loop.md) or
[Phase 10](../phases/10-pilot-hardening.md).
**Agent:** [fixture-writer](../../.claude/agents/fixture-writer.md).

## Prompt

```text
Convert the pattern at <path:line> in <repo> into tests/fixtures/<category>/<name>/: minimal synthetic code reproducing the pattern (no customer identifiers, file names, or directory layout), expected_candidates.json, expected_unknowns.json, falsifier description. Write/extend the ast-grep rule under packs/<pack>/rules/ with must-match and must-not-match snippets; run `ast-grep test`. Paste outputs. If the pattern is genuinely undecidable statically, the expected output is a PRESERVED UNKNOWN with a precise closing instruction — do not invent a rule that "resolves" it.
```

## The failure mode this prompt exists to prevent

A pattern that is genuinely undecidable statically gets a rule invented for it that "resolves" it —
turning an honest UNKNOWN into a confident wrong answer. `UNKNOWN ≠ UNEXPLAINED`. A preserved
UNKNOWN with a precise closing instruction is the correct deliverable.
