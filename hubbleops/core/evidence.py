from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.core.schema import validate

OBSERVERS = ("text", "deps", "structure", "dynamic", "telemetry", "sentinel")
AI_DERIVATION = "DERIVED_AI_EVIDENCE"
DERIVATIONS = ("OBSERVED", "DERIVED_DETERMINISTIC", AI_DERIVATION)
CONFIDENCES = ("RAW", "PROVEN", "DOCUMENTED", "INFERRED")

VERSION_SUBJECT_CLAIMS = ("call_version",)
VERSION_VALUE_KEYS = ("version", "detected", "target")
PACKAGE_VERSION_CLAIMS = ("dependency_state", "sdk_installed")

NON_OBSERVATIONAL_FIELDS = frozenset(
    {"id", "run_id", "proof_scope_hash", "repo_sha", "source_hash"}
)


def searchable_text(record: Mapping[str, Any]) -> str:
    raw = record.get("value")
    value = as_mapping(raw)
    parts = [as_text(raw) or "", as_text(value.get("line")) or ""]
    parts.extend(str(item) for item in as_sequence(value.get("matches")))
    skeleton = as_mapping(value.get("skeleton"))
    parts.extend(str(item) for item in as_sequence(skeleton.get("fragments")))
    for item in as_sequence(value.get("paths")):
        resolved = as_mapping(item)
        parts.extend(str(fragment) for fragment in as_sequence(resolved.get("fragments")))
        literal = as_text(resolved.get("literal"))
        if literal:
            parts.append(literal)
    return "\n".join(part for part in parts if part)


def declared_versions(record: Mapping[str, Any]) -> frozenset[str]:
    value = as_mapping(record.get("value"))
    found = {
        text.lower()
        for key in VERSION_VALUE_KEYS
        if (text := as_text(value.get(key))) is not None and text
    }
    found.update(str(item).lower() for item in as_sequence(value.get("versions")) if str(item))
    if str(record.get("claim_type")) in VERSION_SUBJECT_CLAIMS:
        subject = as_text(record.get("provider_subject"))
        if subject:
            found.add(subject.lower())
    return frozenset(found)


def provider_versions(record: Mapping[str, Any]) -> frozenset[str]:
    if str(record.get("claim_type")) in PACKAGE_VERSION_CLAIMS:
        return frozenset()
    return declared_versions(record)


def evidence_identity(record: Mapping[str, Any]) -> str:
    return content_id({key: value for key, value in record.items() if key != "id"})


def observation_identity(record: Mapping[str, Any]) -> str:
    return content_id(
        {key: value for key, value in record.items() if key not in NON_OBSERVATIONAL_FIELDS}
    )


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
