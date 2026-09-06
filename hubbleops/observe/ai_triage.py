from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from hubbleops.core.canonical import blob_hash
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext

MAX_FILES = 2
QUESTION = (
    "Does this source-bound candidate contain a provider-relevant wrapper path that the "
    "deterministic structure observer left unresolved? Return only bounded supporting residue."
)


class TriageClient(Protocol):
    def ask(self, question: str, files: Mapping[str, str]) -> Mapping[str, Any]: ...


def triage(
    candidate: Mapping[str, Any],
    files: Mapping[str, bytes],
    ctx: ObserverContext,
    client: TriageClient,
    *,
    enabled: bool = False,
) -> tuple[dict[str, Any], ...]:
    if not enabled or candidate.get("status") not in ("UNKNOWN", "HUMAN_REQUIRED"):
        return ()
    selected = tuple(sorted(files.items()))[:MAX_FILES]
    if not selected:
        return ()
    decoded = {path: payload.decode("utf-8", errors="replace") for path, payload in selected}
    answer = dict(client.ask(QUESTION, decoded))
    source_hash = blob_hash(b"".join(payload for _, payload in selected))
    evidence = make_evidence(
        run_id=ctx.run_id,
        proof_scope_hash=ctx.proof_scope_hash,
        claim_type="ai_triage_residue",
        observer="structure",
        repo_sha=ctx.repo_sha,
        path=str(selected[0][0]),
        line_start=None,
        line_end=None,
        source_hash=source_hash,
        value={
            "candidate_id": candidate.get("id"),
            "question": QUESTION,
            "answer": answer,
            "files": [path for path, _ in selected],
        },
        provider_subject=None,
        dependency_context_hash=ctx.dependency_context_hash,
        derivation="DERIVED_AI_EVIDENCE",
        confidence="INFERRED",
    )
    return (evidence,)


__all__ = ["MAX_FILES", "QUESTION", "TriageClient", "triage"]
