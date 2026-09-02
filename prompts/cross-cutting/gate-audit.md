# Gate Audit

**When:** after implementation, before the phase is considered done.
**Session:** fresh. **Agent:** [spec-auditor](../../.claude/agents/spec-auditor.md).
**Pass condition:** the final line reads `GATE: PASS`. Nothing else counts.

The auditor **does not fix anything.** Findings go back to the builder.

## Prompt

```text
You are the spec-auditor. Verify HubbleOps Phase <N> per its DEFINITION OF DONE. For each item: run the commands yourself, paste output, mark PASS/FAIL. Then: (a) `uv run pytest -q` incl. tests/property; (b) grep for `except Exception: pass`, `except: pass`, TODO, "should work"; list every comment line in every code file and every .md file created this phase, and FAIL any that CLAUDE.md "No bloat" does not permit; (c) run tests/unit/test_imports.py and test_no_provider_leak.py; (d) attempt to violate each Law with a small experiment (persist an unexplained candidate; close an UNKNOWN without evidence; reuse a receipt under a new SHA; import packs from observe/) and report whether the code fails closed. Output one conformance table and a final line GATE: PASS or GATE: FAIL. Do not fix anything.
```

Item (d) is the one that matters most: a Law that isn't mechanically enforced is a Law that will be
broken. "The code fails closed" must be demonstrated, not asserted.
