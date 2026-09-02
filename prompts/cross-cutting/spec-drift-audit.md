# Spec-drift audit

**When:** weekly.
**Session:** fresh. **Agent:** [spec-auditor](../../.claude/agents/spec-auditor.md).
**Output:** table only.

## Prompt

```text
Compare the codebase to docs/ARCHITECTURE.md section by section: implemented-as-specified / implemented-differently (justification present or absent in dev/proposals.md) / missing. Flag any provider name, hostname, package name, or packs/ import in core/, closure/, observe/, graph/, obligations/, sandbox/, repair/, verify/, proof/, store/. Flag any dependency, service, or abstraction not in the stack list. Flag any code path that reuses a Receipt across SHAs. Table only.
```

An "implemented-differently" with **no** justification in `dev/proposals.md` is drift. Either write
the proposal or revert to spec — never leave it undocumented.
