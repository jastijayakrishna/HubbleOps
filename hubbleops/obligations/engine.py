from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hubbleops.core.candidate import OPEN_STATUSES
from hubbleops.core.canonical import content_id
from hubbleops.core.evidence import declared_versions, searchable_text
from hubbleops.core.records import as_line, as_mapping, as_sequence, as_text
from hubbleops.core.schema import validate
from hubbleops.core.subjects import in_sources
from hubbleops.core.verification import (
    ABSENT,
    CONSERVED,
    SUPPORTS,
    ChangeSet,
    OracleView,
    SubjectChange,
    method,
)
from hubbleops.observe import CandidateLocation, FileVersionEvidence, Ledger

DETERMINISTIC = "DETERMINISTIC"
HUMAN = "HUMAN"
PRESERVE_UNKNOWN = "PRESERVE_UNKNOWN"

OPEN = "OPEN"

VERSION_CLAIMS = ("call_version",)
ENDPOINT_CLAIMS = ("endpoint_reference",)
DEPENDENCY_CLAIMS = ("sdk_installed", "dependency_state", "package_reference")
SUBJECT_BEARING_CLAIMS = ("surface_reference", "request_text")

CARRIED_STATUSES = OPEN_STATUSES


@dataclass(frozen=True, slots=True)
class ObligationInputs:
    ledger: Ledger
    change_sets: Mapping[str, ChangeSet]
    oracle: OracleView
    target: str
    sources: Mapping[str, str] = field(default_factory=dict[str, str])
    uncomposable: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class BoundVersion:
    version: str
    written_at: tuple[str, ...]


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
    file_versions = ledger.file_versions()
    literal_sites = _literal_sites_by_path(ledger.evidence, file_versions)
    drafts: list[ObligationDraft] = []
    for candidate in ledger.ordered_candidates():
        attached = _attached(candidate, index)
        location = ledger.location_of(candidate)
        status = str(candidate["status"])
        if status in CARRIED_STATUSES:
            drafts.append(_carried(candidate, location, attached, inputs.target))
            drafts.extend(_carried_subjects(candidate, location, attached, inputs))
            continue
        if status != "AFFECTED":
            continue
        bound = bound_version(
            attached, file_versions.get(location.path), literal_sites.get(location.path, ())
        )
        drafts.extend(_affected(candidate, location, attached, inputs, bound))
    return tuple(
        _record(draft, run_id=ledger.run_id, proof_scope_hash=ledger.proof_scope_hash)
        for draft in sorted(drafts, key=_draft_order)
    )


def _affected(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    inputs: ObligationInputs,
    bound: BoundVersion | None = None,
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
                verification_method=method(SUPPORTS, inputs.target),
            )
        ]
    if location.claim_type == "request_text":
        return _query_drafts(candidate, location, attached, inputs)
    effective = effective_version(attached) or (bound.version if bound else None)
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
                verification_method=method(CONSERVED, str(candidate["id"])),
            )
        ]
    uncomposable = inputs.uncomposable.get(effective)
    if uncomposable is not None:
        return [
            ObligationDraft(
                candidate_id=str(candidate["id"]),
                effective_version=effective,
                provider_change_id=f"version:{effective}->{inputs.target}",
                evidence_ids=_evidence_ids(attached),
                current_state=f"{site} calls the provider at {effective}",
                required_state=(
                    f"{site} calls the provider at {inputs.target}; {uncomposable} so no "
                    "deterministic transform applies: migrate by hand or retire the call"
                ),
                repair_class=HUMAN,
                verification_method=method(ABSENT, effective),
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
                verification_method=method(CONSERVED, str(candidate["id"])),
            )
        ]
    drafts = _version_drafts(candidate, location, attached, effective, inputs, bound)
    drafts.extend(
        _subject_drafts(candidate, location, attached, effective, changes, inputs.sources)
    )
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
            verification_method=method(ABSENT, effective),
        )
    ]


def _version_drafts(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    effective: str,
    inputs: ObligationInputs,
    bound: BoundVersion | None = None,
) -> list[ObligationDraft]:
    if effective == inputs.target:
        return []
    claim = location.claim_type
    site = location.display()
    written = resolved_sites(attached, effective)
    if claim in VERSION_CLAIMS:
        current = f"{site} calls the provider at {effective}"
        required = f"{site} calls the provider at {inputs.target}"
    elif claim in ENDPOINT_CLAIMS:
        current = f"{site} addresses a {effective} endpoint path"
        required = f"{site} addresses a {inputs.target} endpoint path"
    elif claim in SUBJECT_BEARING_CLAIMS and bound is not None:
        current = f"{site} is bound to the provider at {effective}"
        required = f"{site} is bound to the provider at {inputs.target}"
        if bound.written_at and not written:
            written = bound.written_at
    else:
        return []
    if written and written != (site,):
        current = f"{current}; the {effective} literal is written at {', '.join(written)}"
    return [
        ObligationDraft(
            candidate_id=str(candidate["id"]),
            effective_version=effective,
            provider_change_id=f"version:{effective}->{inputs.target}",
            evidence_ids=_evidence_ids(attached),
            current_state=current,
            required_state=required,
            repair_class=DETERMINISTIC,
            verification_method=method(ABSENT, effective),
        )
    ]


def _subject_drafts(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    effective: str,
    changes: ChangeSet,
    sources: Mapping[str, str],
) -> list[ObligationDraft]:
    text = _searchable(attached)
    if not text:
        return []
    site = location.display()
    drafts: list[ObligationDraft] = []
    for change in changes.changes:
        if not _obliges(change) or not _mentions(text, change.subject):
            continue
        drafts.append(
            ObligationDraft(
                candidate_id=str(candidate["id"]),
                effective_version=effective,
                provider_change_id=f"subject:{change.subject}",
                evidence_ids=_evidence_ids(attached),
                current_state=_subject_state(change, site, changes.to_version, sources),
                required_state=_required_for(change, site, changes.to_version),
                repair_class=_class_for(change),
                verification_method=_method_for(change, str(candidate["id"])),
            )
        )
    return drafts


def _query_drafts(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    inputs: ObligationInputs,
) -> list[ObligationDraft]:
    return _subjects_across_versions(candidate, location, attached, inputs, None)


def _carried_subjects(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    inputs: ObligationInputs,
) -> list[ObligationDraft]:
    return _subjects_across_versions(candidate, location, attached, inputs, "UNKNOWN")


def _subjects_across_versions(
    candidate: Mapping[str, Any],
    location: CandidateLocation,
    attached: Sequence[Mapping[str, Any]],
    inputs: ObligationInputs,
    effective_label: str | None,
) -> list[ObligationDraft]:
    drafts: list[ObligationDraft] = []
    seen: set[str] = set()
    for effective in sorted(inputs.change_sets):
        for draft in _subject_drafts(
            candidate,
            location,
            attached,
            effective_label or effective,
            inputs.change_sets[effective],
            inputs.sources,
        ):
            if draft.provider_change_id in seen:
                continue
            seen.add(draft.provider_change_id)
            drafts.append(draft)
    return drafts


def resolved_sites(attached: Sequence[Mapping[str, Any]], literal: str) -> tuple[str, ...]:
    found: set[str] = set()
    for record in attached:
        for item in as_sequence(as_mapping(record.get("value")).get("paths")):
            entry = as_mapping(item)
            if as_text(entry.get("terminal")) != "LITERAL":
                continue
            resolved = as_text(entry.get("literal"))
            path = as_text(entry.get("path"))
            line = as_line(as_mapping(entry.get("range")).get("start_line"))
            if resolved is None or path is None or line is None:
                continue
            if literal.lower() in resolved.lower():
                found.add(f"{path}:{line}")
    return tuple(sorted(found))


def edit_sites(subject: str, sources: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(item.site() for item in in_sources(sources, (subject,)))


def _subject_state(
    change: SubjectChange, site: str, target: str, sources: Mapping[str, str]
) -> str:
    opening = f"{site} uses {change.subject}, {_verb(change)} in {target}"
    sites = edit_sites(change.subject, sources)
    if not sites:
        return opening
    return f"{opening}; it is written at {', '.join(sites)}"


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
        verification_method=method(CONSERVED, str(candidate["id"])),
    )


def _obliges(change: SubjectChange) -> bool:
    if change.change == "ADDED":
        return False
    if change.kind == "UNKNOWN_PROVIDER_CONTRACT":
        return True
    return change.change == "REMOVED" or change.replacement is not None


def _method_for(change: SubjectChange, candidate_id: str) -> str:
    if change.kind == "UNKNOWN_PROVIDER_CONTRACT":
        return method(CONSERVED, candidate_id)
    return method(ABSENT, change.subject)


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
        if str(record.get("claim_type")) not in SUBJECT_BEARING_CLAIMS:
            continue
        parts.append(searchable_text(record))
        subject = as_text(record.get("provider_subject"))
        if subject is not None:
            parts.append(subject)
    return "\n".join(part for part in parts if part)


def effective_version(attached: Sequence[Mapping[str, Any]]) -> str | None:
    found = {
        version
        for record in attached
        if str(record.get("claim_type")) in VERSION_CLAIMS
        for version in declared_versions(record)
    }
    if len(found) != 1:
        return None
    return found.pop()


def _literal_sites_by_path(
    records: Sequence[Mapping[str, Any]], file_versions: Mapping[str, FileVersionEvidence]
) -> dict[str, tuple[str, ...]]:
    carriers: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        path = str(record.get("path"))
        if path in file_versions and str(record.get("claim_type")) in VERSION_CLAIMS:
            carriers.setdefault(path, []).append(record)
    return {
        path: resolved_sites(found, file_versions[path].version)
        for path, found in sorted(carriers.items())
    }


def bound_version(
    attached: Sequence[Mapping[str, Any]],
    file_evidence: FileVersionEvidence | None,
    literal_sites: tuple[str, ...] = (),
) -> BoundVersion | None:
    structural = [
        as_mapping(record.get("value"))
        for record in sorted(attached, key=lambda record: str(record.get("id")))
        if str(record.get("claim_type")) in SUBJECT_BEARING_CLAIMS
        and str(record.get("observer")) == "structure"
    ]
    bindings = {
        str(item).lower()
        for value in structural
        for item in as_sequence(value.get("binding_versions"))
    }
    if len(bindings) == 1:
        written_at = next(
            (
                as_text(value.get("binding_site"))
                for value in structural
                if as_sequence(value.get("binding_versions")) and value.get("binding_site")
            ),
            None,
        )
        return BoundVersion(version=bindings.pop(), written_at=(written_at,) if written_at else ())
    if bindings or file_evidence is None:
        return None
    return BoundVersion(
        version=file_evidence.version.lower(),
        written_at=literal_sites or (file_evidence.example_location,),
    )


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
