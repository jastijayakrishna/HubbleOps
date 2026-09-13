from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.closure.source_closure import SourceClosure
from hubbleops.core.candidate import STATUSES, candidate_identity, claim_key, make_candidate
from hubbleops.core.errors import UnexplainedCandidates
from hubbleops.core.surface import SurfaceSpec
from hubbleops.observe import resolver


@dataclass(frozen=True, slots=True)
class CandidateLocation:
    path: str
    line: int | None
    claim_type: str
    provider_subject: str | None

    def display(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass(frozen=True, slots=True)
class Ledger:
    provider: str
    run_id: str
    proof_scope_hash: str
    evidence: tuple[dict[str, Any], ...]
    candidates: tuple[dict[str, Any], ...]

    def evidence_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(record["id"]): record for record in self.evidence}

    def file_versions(self) -> dict[str, resolver.FileVersionEvidence]:
        return resolver.file_version_index(self.evidence)

    def location_of(self, candidate: Mapping[str, Any]) -> CandidateLocation:
        index = self.evidence_by_id()
        attached = [index[eid] for eid in candidate["evidence_ids"] if eid in index]
        if not attached:
            return CandidateLocation(path=".", line=None, claim_type="", provider_subject=None)
        chosen = resolver.winner(attached)
        return CandidateLocation(
            path=str(chosen["path"]),
            line=chosen["line_start"],
            claim_type=str(chosen["claim_type"]),
            provider_subject=chosen["provider_subject"],
        )

    def ordered_candidates(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            sorted(
                self.candidates,
                key=lambda candidate: _sort_key(self.location_of(candidate), candidate),
            )
        )

    def by_status(self, status: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            candidate for candidate in self.ordered_candidates() if candidate["status"] == status
        )

    def counts(self) -> dict[str, int]:
        counts = {
            "total": len(self.candidates),
            "affected": 0,
            "not_affected_with_evidence": 0,
            "unknown": 0,
            "human_required": 0,
            "excluded_with_evidence": 0,
            "provider_reference_data": 0,
            "unsupported": 0,
            "unscanned": 0,
            "human_accepted_risk": 0,
            "unexplained": self.unexplained(),
        }
        for candidate in self.candidates:
            counts[str(candidate["status"]).lower()] += 1
        return counts

    def unexplained(self) -> int:
        attached = {eid for candidate in self.candidates for eid in candidate["evidence_ids"]}
        orphaned = sum(1 for record in self.evidence if record["id"] not in attached)
        unstatused = sum(
            1 for candidate in self.candidates if candidate.get("status") not in STATUSES
        )
        return orphaned + unstatused

    def export(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "run_id": self.run_id,
            "proof_scope_hash": self.proof_scope_hash,
            "counts": self.counts(),
            "candidates": [
                {
                    "candidate": candidate,
                    "location": self.location_of(candidate).display(),
                }
                for candidate in self.ordered_candidates()
            ],
            "evidence": list(self.evidence),
        }


def build(
    *,
    provider: str,
    run_id: str,
    proof_scope_hash: str,
    evidence: Sequence[dict[str, Any]],
    closure: SourceClosure,
    surface: SurfaceSpec | None = None,
    validations: Mapping[str, Mapping[str, Any]] | None = None,
) -> Ledger:
    classifications = {entry.path: entry.classification.value for entry in closure.entries}
    roles = {entry.path: entry.role.value for entry in closure.entries}
    path_versions = resolver.file_version_index(evidence)
    context = resolver.resolution_context(evidence, surface, validations)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in evidence:
        key = (str(record["claim_type"]), claim_key(record))
        groups.setdefault(key, []).append(record)

    candidates: list[dict[str, Any]] = []
    attached: set[str] = set()
    for (claim_type, key), records in sorted(groups.items()):
        resolution = resolver.resolve_claim(
            claim_type, records, classifications, roles, path_versions, context
        )
        ids = sorted({str(record["id"]) for record in records})
        candidates.append(
            make_candidate(
                candidate_id=candidate_identity(provider, claim_type, key),
                run_id=run_id,
                proof_scope_hash=proof_scope_hash,
                provider=provider,
                evidence_ids=ids,
                status=resolution.status,
                reason=resolution.reason,
                close_with=resolution.close_with,
            )
        )
        attached.update(ids)

    orphaned = sorted(str(record["id"]) for record in evidence if record["id"] not in attached)
    if orphaned:
        raise UnexplainedCandidates(
            len(orphaned),
            f"evidence not attached to any candidate: {', '.join(orphaned[:5])}",
        )

    ledger = Ledger(
        provider=provider,
        run_id=run_id,
        proof_scope_hash=proof_scope_hash,
        evidence=tuple(
            sorted(
                evidence,
                key=lambda record: (
                    str(record["path"]),
                    record["line_start"] or 0,
                    str(record["claim_type"]),
                    str(record["provider_subject"] or ""),
                    str(record["id"]),
                ),
            )
        ),
        candidates=tuple(sorted(candidates, key=lambda candidate: str(candidate["id"]))),
    )
    remaining = ledger.unexplained()
    if remaining:
        raise UnexplainedCandidates(remaining, "ledger recomputed a non-zero unexplained count")
    return ledger


def _sort_key(
    location: CandidateLocation, candidate: Mapping[str, Any]
) -> tuple[str, int, str, str, str]:
    return (
        location.path,
        location.line or 0,
        location.claim_type,
        str(location.provider_subject or ""),
        str(candidate["id"]),
    )
