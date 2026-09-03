from __future__ import annotations

from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.schema import validate

OBSERVERS = ("text", "deps", "structure", "dynamic", "telemetry", "sentinel")
AI_DERIVATION = "DERIVED_AI_EVIDENCE"
DERIVATIONS = ("OBSERVED", "DERIVED_DETERMINISTIC", AI_DERIVATION)
CONFIDENCES = ("RAW", "PROVEN", "DOCUMENTED", "INFERRED")


def make_evidence(
    *,
    run_id: str,
    proof_scope_hash: str,
    claim_type: str,
    observer: str,
    repo_sha: str | None,
    path: str,
    line_start: int | None,
    line_end: int | None,
    source_hash: str,
    value: Any,
    provider_subject: str | None,
    dependency_context_hash: str | None,
    derivation: str,
    confidence: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": run_id,
        "proof_scope_hash": proof_scope_hash,
        "claim_type": claim_type,
        "observer": observer,
        "repo_sha": repo_sha,
        "path": path,
        "line_start": line_start,
        "line_end": line_end,
        "source_hash": source_hash,
        "value": value,
        "provider_subject": provider_subject,
        "dependency_context_hash": dependency_context_hash,
        "derivation": derivation,
        "confidence": confidence,
    }
    return validate("evidence", {**body, "id": content_id(body)})
