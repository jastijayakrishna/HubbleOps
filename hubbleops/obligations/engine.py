from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.records import as_text
from hubbleops.core.schema import validate
from hubbleops.core.verification import ChangeSet, OracleView, SubjectChange
from hubbleops.observe import CandidateLocation, Ledger

DETERMINISTIC = "DETERMINISTIC"
HUMAN = "HUMAN"
PRESERVE_UNKNOWN = "PRESERVE_UNKNOWN"

OPEN = "OPEN"

VERSION_CLAIMS = ("call_version",)
ENDPOINT_CLAIMS = ("endpoint_reference",)
DEPENDENCY_CLAIMS = ("sdk_installed", "dependency_state", "package_reference")
REQUEST_CLAIMS = ("request_text",)

CARRIED_STATUSES = ("UNKNOWN", "HUMAN_REQUIRED")


@dataclass(frozen=True, slots=True)
class ObligationInputs:
    ledger: Ledger
    change_sets: Mapping[str, ChangeSet]
    oracle: OracleView
    target: str


@dataclass(frozen=True, slots=True)
class ObligationDraft:
    candidate_id: str
    effective_version: str
    provider_change_id: str
    evidence_ids: tuple[str, ...]
    current_state: str
    required_state: str
    repair_class: str
    verification_method: str

    def identity(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate_id,
            "effective_version": self.effective_version,
            "provider_change_id": self.provider_change_id,
        }


def build(inputs: ObligationInputs) -> tuple[dict[str, Any], ...]:
    ledger = inputs.ledger
    index = ledger.evidence_by_id()
    drafts: list[ObligationDraft] = []
    for candidate in ledger.ordered_candidates():
        attached = _attached(candidate, index)
        location = ledger.location_of(candidate)
        status = str(candidate["status"])
        if status in CARRIED_STATUSES:
            drafts.append(_carried(candidate, location, attached, inputs.target))
            continue
        if status != "AFFECTED":
            continue
        drafts.extend(_affected(candidate, location, attached, inputs))
    return tuple(
        _record(draft, run_id=ledger.run_id, proof_scope_hash=ledger.proof_scope_hash)
        for draft in sorted(drafts, key=_draft_order)
    )


def _affected(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    inputs: ObligationInputs,
) -> list[ObligationDraft]:
    site = location.display()
    if location.claim_type in DEPENDENCY_CLAIMS:
        return [
            ObligationDraft(
                candidate_id=str(candidate["id"]),
                effective_version=effective_version(attached) or "DEPENDENCY",
                provider_change_id=f"dependency:{inputs.target}",
                evidence_ids=_evidence_ids(attached),
                current_state=f"{site} pins a client library",
                required_state=f"{site} pins a client library supporting {inputs.target}",
                repair_class=DETERMINISTIC,
                verification_method=(
                    "dependency resolution reports a client library supporting the target"
                ),
            )
        ]
    effective = effective_version(attached)
    if effective is None:
        return [
            ObligationDraft(
                candidate_id=str(candidate["id"]),
                effective_version="UNRESOLVED",
                provider_change_id="EFFECTIVE_VERSION_UNRESOLVED",
                evidence_ids=_evidence_ids(attached),
                current_state=f"{site} is affected but no single effective version resolves",
                required_state=(
                    f"resolve the effective version at {site} before a change to "
                    f"{inputs.target} can be derived"
                ),
                repair_class=PRESERVE_UNKNOWN,
                verification_method="rescan resolves exactly one effective version at this site",
            )
        ]
    changes = inputs.change_sets.get(effective)
    if changes is None:
        return [
            ObligationDraft(
                candidate_id=str(candidate["id"]),
                effective_version=effective,
                provider_change_id="UNKNOWN_PROVIDER_CONTRACT",
                evidence_ids=_evidence_ids(attached),
                current_state=f"{site} runs {effective}",
                required_state=(
                    f"a contract diff composing {effective} to {inputs.target} that resolves "
                    "cleanly at every hop"
                ),
                repair_class=PRESERVE_UNKNOWN,
                verification_method="the composed diff resolves at every hop",
            )
        ]
    drafts = _version_drafts(candidate, location, attached, effective, inputs)
    drafts.extend(_subject_drafts(candidate, location, attached, effective, changes))
    if drafts:
        return drafts
    return [
        ObligationDraft(
            candidate_id=str(candidate["id"]),
            effective_version=effective,
            provider_change_id=f"version:{effective}->{inputs.target}",
            evidence_ids=_evidence_ids(attached),
            current_state=f"{site} runs {effective}",
            required_state=f"{site} runs {inputs.target}",
            repair_class=HUMAN,
            verification_method="migration audit finds no reference to the retired version",
        )
    ]


def _version_drafts(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    effective: str,
    inputs: ObligationInputs,
) -> list[ObligationDraft]:
    if effective == inputs.target:
        return []
    claim = location.claim_type
    site = location.display()
    if claim in VERSION_CLAIMS:
        current = f"{site} calls the provider at {effective}"
        required = f"{site} calls the provider at {inputs.target}"
        method = "migration audit finds no explicit reference to the retired version"
    elif claim in ENDPOINT_CLAIMS:
        current = f"{site} addresses a {effective} endpoint path"
        required = f"{site} addresses a {inputs.target} endpoint path"
        method = "migration audit finds no retired endpoint path"
    else:
        return []
    return [
        ObligationDraft(
            candidate_id=str(candidate["id"]),
            effective_version=effective,
            provider_change_id=f"version:{effective}->{inputs.target}",
            evidence_ids=_evidence_ids(attached),
            current_state=current,
            required_state=required,
            repair_class=DETERMINISTIC,
            verification_method=method,
        )
    ]


def _subject_drafts(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    effective: str,
    changes: ChangeSet,
) -> list[ObligationDraft]:
    text = _searchable(attached)
    if not text:
        return []
    site = location.display()
    drafts: list[ObligationDraft] = []
    for change in changes.changes:
        if change.change == "ADDED" or not _mentions(text, change.subject):
            continue
        drafts.append(
            ObligationDraft(
                candidate_id=str(candidate["id"]),
                effective_version=effective,
                provider_change_id=f"subject:{change.subject}",
                evidence_ids=_evidence_ids(attached),
                current_state=(
                    f"{site} uses {change.subject}, {_verb(change)} in {changes.to_version}"
                ),
                required_state=_required_for(change, site, changes.to_version),
                repair_class=_class_for(change),
                verification_method=(
                    "contract oracle accepts the request shape and the response-consumer "
                    "check finds no read of the retired subject"
                ),
            )
        )
    return drafts


def _carried(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    target: str,
) -> ObligationDraft:
    close_with = as_text(candidate.get("close_with")) or (
        f"attach evidence resolving {location.display()} against {target}"
    )
    return ObligationDraft(
        candidate_id=str(candidate["id"]),
        effective_version="UNKNOWN",
        provider_change_id=f"carried:{candidate['status']}",
        evidence_ids=_evidence_ids(attached),
        current_state=f"{location.display()} is {candidate['status']}: {candidate['reason']}",
        required_state=close_with,
        repair_class=PRESERVE_UNKNOWN,
        verification_method="UNKNOWN conservation finds this candidate still carried or closed",
    )


def _class_for(change: SubjectChange) -> str:
    if change.kind == "UNKNOWN_PROVIDER_CONTRACT":
        return PRESERVE_UNKNOWN
    if change.replacement is not None:
        return DETERMINISTIC
    return HUMAN


def _required_for(change: SubjectChange, site: str, target: str) -> str:
    if change.kind == "UNKNOWN_PROVIDER_CONTRACT":
        return (
            f"resolve what replaces {change.subject} in {target}; the composed diff does not "
            f"map it cleanly, so {site} must not be rewritten by guess"
        )
    if change.replacement is not None:
        return f"{site} uses {change.replacement} instead of {change.subject}"
    return (
        f"{site} stops using {change.subject}, which {target} removes with no announced "
        "replacement; a human decides what the call should read instead"
    )


def _verb(change: SubjectChange) -> str:
    return "removed" if change.change == "REMOVED" else "changed"


def _mentions(text: str, subject: str) -> bool:
    if not subject:
        return False
    return subject in text


def _searchable(attached: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for record in attached:
        if str(record.get("claim_type")) not in REQUEST_CLAIMS:
            continue
        value = as_text(record.get("value"))
        if value is not None:
            parts.append(value)
        subject = as_text(record.get("provider_subject"))
        if subject is not None:
            parts.append(subject)
    return "\n".join(parts)


def effective_version(attached: Sequence[Mapping[str, Any]]) -> str | None:
    found = {
        subject
        for record in attached
        if str(record.get("claim_type")) in VERSION_CLAIMS
        and (subject := as_text(record.get("provider_subject"))) is not None
    }
    if len(found) != 1:
        return None
    return found.pop()


def _attached(
    candidate: Mapping[str, Any], index: Mapping[str, dict[str, Any]]
) -> tuple[Mapping[str, Any], ...]:
    resolved = tuple(index[eid] for eid in candidate["evidence_ids"] if eid in index)
    if resolved:
        return resolved
    return tuple({"id": str(eid)} for eid in candidate["evidence_ids"])


def _evidence_ids(attached: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted({str(record["id"]) for record in attached}))


def _draft_order(draft: ObligationDraft) -> tuple[str, str, str]:
    return (draft.candidate_id, draft.effective_version, draft.provider_change_id)


def _record(draft: ObligationDraft, *, run_id: str, proof_scope_hash: str) -> dict[str, Any]:
    return validate(
        "obligation",
        {
            "id": content_id(draft.identity()),
            "run_id": run_id,
            "proof_scope_hash": proof_scope_hash,
            "provider_change_id": draft.provider_change_id,
            "evidence_ids": list(draft.evidence_ids),
            "current_state": draft.current_state,
            "required_state": draft.required_state,
            "repair_class": draft.repair_class,
            "verification_method": draft.verification_method,
            "status": OPEN,
        },
    )


__all__ = ["ObligationDraft", "ObligationInputs", "build", "effective_version"]
