from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.candidate import OPEN_STATUSES
from hubbleops.core.evidence import AI_DERIVATION
from hubbleops.core.verification import CheckReport
from hubbleops.observe import Ledger

DECISION_CLAIM_TYPE = "human_decision"


@dataclass(frozen=True, slots=True)
class Closure:
    candidate_id: str
    was: str
    now: str
    justification: str
    new_evidence_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "was": self.was,
            "now": self.now,
            "justification": self.justification,
            "new_evidence_ids": list(self.new_evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class Conservation:
    closures: tuple[Closure, ...]
    violations: tuple[Closure, ...]
    preserved: tuple[Mapping[str, Any], ...]

    def report(self) -> CheckReport:
        return CheckReport(
            name="unknown_conservation",
            passed=not self.violations,
            reasons=tuple(
                f"candidate {item.candidate_id} closed {item.was} to {item.now} "
                f"without new evidence: {item.justification}"
                for item in self.violations
            ),
            detail={
                "closed": len(self.closures),
                "violations": len(self.violations),
                "preserved": len(self.preserved),
                "closures": [item.to_mapping() for item in self.closures],
            },
        )

    def preserved_unknowns(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "candidate_id": str(candidate["id"]),
                "close_with": str(candidate["close_with"] or candidate["reason"]),
            }
            for candidate in self.preserved
        )


def compare(
    base: Ledger,
    candidate: Ledger,
    decisions: Sequence[Mapping[str, Any]] = (),
) -> Conservation:
    before = {str(item["id"]): item for item in base.candidates if item["status"] in OPEN_STATUSES}
    base_evidence_ids = {str(record["id"]) for record in base.evidence}
    candidate_by_id = {str(item["id"]): item for item in candidate.candidates}
    candidate_evidence = candidate.evidence_by_id()
    decided = {str(item.get("candidate_id", "")): item for item in decisions}

    open_sites = {
        (_claim_type(item, candidate_evidence), _site(item, candidate_evidence))
        for item in candidate.candidates
        if item["status"] in OPEN_STATUSES
    }
    base_evidence = base.evidence_by_id()

    closures: list[Closure] = []
    violations: list[Closure] = []
    for candidate_id in sorted(before):
        now = candidate_by_id.get(candidate_id)
        if now is None:
            vanished = _vanished(before[candidate_id], base_evidence, open_sites, candidate)
            if vanished is not None:
                violations.append(vanished)
                closures.append(vanished)
            continue
        if now["status"] in OPEN_STATUSES:
            continue
        fresh = tuple(
            sorted(
                eid
                for eid in now["evidence_ids"]
                if eid not in base_evidence_ids
                and str(candidate_evidence.get(eid, {}).get("derivation")) != AI_DERIVATION
            )
        )
        decision = decided.get(candidate_id)
        closure = Closure(
            candidate_id=candidate_id,
            was=str(before[candidate_id]["status"]),
            now=str(now["status"]),
            justification=_justification(fresh, decision),
            new_evidence_ids=fresh,
        )
        closures.append(closure)
        if not fresh and decision is None:
            violations.append(closure)
    preserved = tuple(
        item for item in candidate.ordered_candidates() if item["status"] in OPEN_STATUSES
    )
    return Conservation(closures=tuple(closures), violations=tuple(violations), preserved=preserved)


def _site(item: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> str:
    paths = sorted(str(evidence[eid]["path"]) for eid in item["evidence_ids"] if eid in evidence)
    return paths[0] if paths else "."


def _vanished(
    item: Mapping[str, Any],
    base_evidence: Mapping[str, Mapping[str, Any]],
    open_sites: set[tuple[str, str]],
    candidate: Ledger,
) -> Closure | None:
    claim_type = _claim_type(item, base_evidence)
    site = _site(item, base_evidence)
    if (claim_type, site) in open_sites:
        return None
    if not any(str(record["path"]) == site for record in candidate.evidence):
        return None
    return Closure(
        candidate_id=str(item["id"]),
        was=str(item["status"]),
        now="ABSENT",
        justification=(
            f"the candidate run still observes {site} but carries no open candidate for its "
            f"{claim_type} claim, so this UNKNOWN was dropped rather than closed"
        ),
        new_evidence_ids=(),
    )


def _claim_type(item: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> str:
    kinds = sorted(
        str(evidence[eid]["claim_type"]) for eid in item["evidence_ids"] if eid in evidence
    )
    return kinds[0] if kinds else ""


def _justification(fresh: Sequence[str], decision: Mapping[str, Any] | None) -> str:
    if fresh:
        return f"{len(fresh)} evidence record(s) this run observed and the base run did not"
    if decision is not None:
        return f"recorded human decision {decision.get('id', 'unnamed')}"
    return "no new evidence and no recorded human decision"


__all__ = ["Closure", "Conservation", "compare"]
