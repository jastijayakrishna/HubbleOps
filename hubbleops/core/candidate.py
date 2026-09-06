from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import UnknownClaimType
from hubbleops.core.records import as_mapping
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


def claim_key(record: Mapping[str, Any]) -> str:
    claim_type = str(record["claim_type"])
    path = str(record["path"])
    line = record["line_start"]
    subject = record["provider_subject"]
    value = as_mapping(record["value"])
    if claim_type == "call_version":
        return f"{path}:{line}"
    if claim_type in (
        "surface_reference",
        "endpoint_reference",
        "package_reference",
        "config_reference",
        "request_text",
    ):
        return f"{path}:{line}:{subject}"
    if claim_type == "sdk_installed":
        ecosystem = value.get("ecosystem")
        package = value.get("package")
        if value.get("state") == "ABSENT" or package is None:
            return f"{ecosystem}:*"
        return f"{ecosystem}:{str(package).lower().replace('_', '-')}"
    if claim_type == "dependency_state":
        return f"{value.get('state')}:{path}"
    if claim_type == "production_version":
        return f"{value.get('service')}:{value.get('method')}:{value.get('version')}"
    if claim_type in ("dynamic_state", "telemetry_state", "sentinel_state"):
        return (
            f"{value.get('code')}:{value.get('row')}:{value.get('service')}:"
            f"{value.get('method')}:{value.get('version')}:{path}"
        )
    if claim_type in ("file_unscanned", "structure_unsupported"):
        return path
    if claim_type == "external_boundary":
        payload = value.get("payload")
        return f"{path}:{line}:{payload}" if payload is not None else path
    raise UnknownClaimType(claim_type)


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
