from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.schema import validate

STATUSES = (
    "AFFECTED",
    "NOT_AFFECTED_WITH_EVIDENCE",
    "UNKNOWN",
    "HUMAN_REQUIRED",
    "EXCLUDED_WITH_EVIDENCE",
)

OPEN_STATUSES = ("UNKNOWN", "HUMAN_REQUIRED")


def candidate_identity(provider: str, claim_type: str, claim_key: str) -> str:
    return content_id({"provider": provider, "claim_type": claim_type, "claim_key": claim_key})


def make_candidate(
    *,
    candidate_id: str,
    run_id: str,
    proof_scope_hash: str,
    provider: str,
    evidence_ids: Iterable[str],
    status: str,
    reason: str,
    close_with: str | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": candidate_id,
        "run_id": run_id,
        "proof_scope_hash": proof_scope_hash,
        "provider": provider,
        "evidence_ids": sorted(set(evidence_ids)),
        "status": status,
        "reason": reason,
        "close_with": close_with,
    }
    return validate("candidate", record)
