from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hubbleops.core.canonical import EMPTY_SHA256
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext
from hubbleops.core.records import as_mapping
from hubbleops.observe.ledger import Ledger


@dataclass(frozen=True, order=True, slots=True)
class ProductionTuple:
    service: str
    method: str
    version: str

    def mapping(self) -> dict[str, str]:
        return {"service": self.service, "method": self.method, "version": self.version}


@dataclass(frozen=True, slots=True)
class AdapterIssue:
    row: int
    value: str
    reason: str


@dataclass(frozen=True, slots=True)
class Reconciliation:
    evidence: tuple[dict[str, Any], ...]
    accounted: int
    total: int


def reconcile(
    observations: tuple[ProductionTuple, ...],
    issues: tuple[AdapterIssue, ...],
    ledger: Ledger,
    ctx: ObserverContext,
) -> Reconciliation:
    records: list[dict[str, Any]] = []
    accounted = 0
    for observation in sorted(set(observations)):
        mappings = candidate_ids(ledger, observation)
        if mappings:
            accounted += 1
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="production_version",
                observer="telemetry",
                repo_sha=ctx.repo_sha,
                path=".",
                line_start=None,
                line_end=None,
                source_hash=EMPTY_SHA256,
                value={**observation.mapping(), "candidate_ids": list(mappings)},
                provider_subject=observation.version,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="PROVEN",
            )
        )
        if len(mappings) > 1:
            records.append(
                make_evidence(
                    run_id=ctx.run_id,
                    proof_scope_hash=ctx.proof_scope_hash,
                    claim_type="telemetry_state",
                    observer="telemetry",
                    repo_sha=ctx.repo_sha,
                    path=".",
                    line_start=None,
                    line_end=None,
                    source_hash=EMPTY_SHA256,
                    value={
                        "candidate_ids": list(mappings),
                        "code": "TELEMETRY_SITE_AMBIGUOUS",
                        "reason": "production tuple maps to multiple source candidates",
                        "row": 0,
                        **observation.mapping(),
                    },
                    provider_subject=observation.version,
                    dependency_context_hash=ctx.dependency_context_hash,
                    derivation="OBSERVED",
                    confidence="PROVEN",
                )
            )
    for issue in issues:
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="telemetry_state",
                observer="telemetry",
                repo_sha=ctx.repo_sha,
                path=".",
                line_start=None,
                line_end=None,
                source_hash=EMPTY_SHA256,
                value={
                    "code": "TELEMETRY_ADAPTER_UNKNOWN",
                    "reason": issue.reason,
                    "row": issue.row,
                    "value": issue.value,
                },
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="PROVEN",
            )
        )
    return Reconciliation(tuple(records), accounted, len(set(observations)))


def production_coverage(ledger: Ledger) -> tuple[int, int] | None:
    production = [
        record
        for record in ledger.evidence
        if record["claim_type"] == "production_version"
        and record["observer"] in ("telemetry", "sentinel")
    ]
    if not production:
        return None
    tuples: dict[tuple[str, str, str], bool] = {}
    for record in production:
        value = as_mapping(record["value"])
        key = (str(value.get("service")), str(value.get("method")), str(value.get("version")))
        explained = bool(value.get("candidate_ids"))
        tuples[key] = tuples.get(key, False) or explained
    return sum(tuples.values()), len(tuples)


def candidate_ids(ledger: Ledger, observation: ProductionTuple) -> tuple[str, ...]:
    index = ledger.evidence_by_id()
    return tuple(
        sorted(
            str(candidate["id"])
            for candidate in ledger.candidates
            if _candidate_matches(candidate, index, observation)
        )
    )


def _candidate_matches(
    candidate: dict[str, Any], index: dict[str, dict[str, Any]], observation: ProductionTuple
) -> bool:
    if candidate["status"] != "AFFECTED":
        return False
    for evidence_id in candidate["evidence_ids"]:
        record = index.get(str(evidence_id))
        if record is None or record["claim_type"] != "call_version":
            continue
        value = as_mapping(record["value"])
        if (
            record["provider_subject"] == observation.version
            and value.get("service") == observation.service
            and value.get("method") == observation.method
        ):
            return True
    return False


__all__ = [
    "AdapterIssue",
    "ProductionTuple",
    "Reconciliation",
    "candidate_ids",
    "production_coverage",
    "reconcile",
]
