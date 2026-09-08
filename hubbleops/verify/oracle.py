from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.core.verification import (
    CheckReport,
    OracleCheck,
    OracleOutcome,
    OracleView,
    request_identity,
)
from hubbleops.observe import Ledger

REQUEST_CLAIM_TYPE = "request_text"


@dataclass(frozen=True, slots=True)
class OracleReview:
    checks: tuple[OracleCheck, ...]
    available: bool

    def accepted(self) -> tuple[OracleCheck, ...]:
        return tuple(item for item in self.checks if item.code == "VALID")

    def rejected(self) -> tuple[OracleCheck, ...]:
        return tuple(item for item in self.checks if item.code == "INVALID")

    def undecided(self) -> tuple[OracleCheck, ...]:
        return tuple(item for item in self.checks if item.code == "UNKNOWN_PROVIDER_CONTRACT")

    def report(self) -> CheckReport:
        rejected = self.rejected()
        return CheckReport(
            name="contract_oracle",
            passed=not rejected,
            reasons=tuple(
                f"provider rejected request {item.request_hash[:12]} "
                f"({item.service}.{item.method}): {item.reason}"
                for item in rejected
            ),
            unresolved=tuple(
                sorted(
                    f"oracle undecided for request {item.request_hash[:12]}: {item.reason}"
                    for item in self.undecided()
                )
            ),
            detail={
                "available": self.available,
                "total": len(self.checks),
                "accepted": len(self.accepted()),
                "rejected": len(rejected),
                "undecided": len(self.undecided()),
            },
        )


def requests_from(
    ledger: Ledger, captured: Sequence[Mapping[str, Any]]
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    found: dict[str, Mapping[str, Any]] = {}
    evidence = ledger.evidence_by_id()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    for eid in sorted(attached):
        record = evidence.get(eid)
        if record is None or record["claim_type"] != REQUEST_CLAIM_TYPE:
            continue
        request = _static_request(record)
        if request is not None:
            found.setdefault(request_identity(request), request)
    for event in captured:
        request = _captured_request(event)
        if request is not None:
            found.setdefault(request_identity(request), request)
    return tuple((key, found[key]) for key in sorted(found))


def _static_request(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = as_mapping(record.get("value"))
    skeleton = as_mapping(value.get("skeleton"))
    if as_sequence(skeleton.get("holes")):
        return None
    fragments = [str(item) for item in as_sequence(skeleton.get("fragments"))]
    text = as_text(value.get("text")) or as_text(value.get("query")) or " ".join(fragments)
    if not text.strip():
        return None
    return {
        "service": as_text(value.get("service")) or "",
        "method": as_text(value.get("method")) or "",
        "request": {"query": text},
        "origin": f"static:{record['path']}:{record['line_start'] or 0}",
    }


def _captured_request(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    service = as_text(event.get("service"))
    method = as_text(event.get("method"))
    body = as_mapping(event.get("request"))
    if not service or not method or not body:
        return None
    return {
        "service": service,
        "method": method,
        "request": dict(body),
        "origin": "captured",
    }


def review(
    oracle: OracleView,
    requests: Sequence[tuple[str, Mapping[str, Any]]],
    target_version: str,
    now: datetime | None = None,
) -> OracleReview:
    stamp = (now or datetime.now(UTC)).isoformat()
    checks: list[OracleCheck] = []
    available = True
    for request_hash, request in requests:
        outcome = _validate(oracle, request, target_version)
        if outcome.code == "ORACLE_UNAVAILABLE":
            available = False
        checks.append(
            OracleCheck(
                request_hash=request_hash,
                service=str(request.get("service", "")),
                method=str(request.get("method", "")),
                version=target_version,
                origin=str(request.get("origin", "")),
                code=outcome.code,
                reason=outcome.reason,
                checked_at=stamp,
            )
        )
    return OracleReview(checks=tuple(checks), available=available)


def _validate(oracle: OracleView, request: Mapping[str, Any], target_version: str) -> OracleOutcome:
    try:
        return oracle.validate(request, target_version)
    except (OSError, ValueError) as error:
        return OracleOutcome(code="ORACLE_UNAVAILABLE", reason=str(error))


__all__ = ["OracleReview", "requests_from", "review"]
