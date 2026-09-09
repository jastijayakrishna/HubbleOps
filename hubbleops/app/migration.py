from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.app import registry
from hubbleops.app.verification import InjectedOracle
from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.repair import TransformInput
from hubbleops.core.verification import ChangeSet, SubjectChange
from hubbleops.obligations import ObligationInputs
from hubbleops.obligations import build as build_obligations
from hubbleops.observe.ledger import Ledger
from hubbleops.repair import deterministic

DETERMINISTIC = "DETERMINISTIC"
VERSION_PREFIX = "version:"
SUBJECT_PREFIX = "subject:"


class MigrationInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationResult:
    obligations: tuple[dict[str, Any], ...]
    report: deterministic.RepairReport
    target: str
    written: tuple[str, ...]

    def open_for_human(self) -> tuple[dict[str, Any], ...]:
        discharged = set(self.report.discharged())
        return tuple(
            item
            for item in self.obligations
            if item["id"] not in discharged and item["repair_class"] != "PRESERVE_UNKNOWN"
        )

    def preserved(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item for item in self.obligations if item["repair_class"] == "PRESERVE_UNKNOWN"
        )


def change_sets_for(
    pack: registry.LoadedPack, versions: Sequence[str], target: str
) -> dict[str, ChangeSet]:
    built: dict[str, ChangeSet] = {}
    for version in sorted(set(versions)):
        try:
            diff = pack.contract.diff(version, target)
        except Exception:
            continue
        built[version] = ChangeSet(
            from_version=diff.from_version,
            to_version=diff.to_version,
            pair_hash=diff.pair_hash,
            changes=tuple(
                sorted(
                    (
                        SubjectChange(
                            subject=fact.subject,
                            change=(
                                fact.change
                                if fact.change in ("ADDED", "REMOVED", "CHANGED")
                                else "CHANGED"
                            ),
                            replacement=fact.replacement,
                            kind=fact.result,
                            reason=fact.reason,
                        )
                        for fact in diff.facts
                    ),
                    key=lambda item: item.subject,
                )
            ),
        )
    return built


def detected_versions(ledger: Ledger) -> tuple[str, ...]:
    found = {
        str(record["provider_subject"])
        for record in ledger.evidence
        if record["claim_type"] == "call_version" and record["provider_subject"]
    }
    return tuple(sorted(found))


def transform_requests(
    *,
    obligations: Sequence[Mapping[str, Any]],
    ledger: Ledger,
    change_sets: Mapping[str, ChangeSet],
    root: Path,
    target: str,
) -> tuple[TransformInput, ...]:
    index = ledger.evidence_by_id()
    by_candidate = {str(item["id"]): item for item in ledger.candidates}
    requests: list[TransformInput] = []
    for obligation in obligations:
        if obligation["repair_class"] != DETERMINISTIC:
            continue
        located = _locate(obligation, index, by_candidate, ledger)
        if located is None:
            continue
        path, line, claim_type = located
        source = root / path
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        subject, replacement = _subject_of(obligation, change_sets)
        requests.append(
            TransformInput(
                obligation_id=str(obligation["id"]),
                path=path,
                text=text,
                current_state=str(obligation["current_state"]),
                required_state=str(obligation["required_state"]),
                from_version=_from_version(obligation, target),
                to_version=target,
                claim_type=claim_type,
                line=line,
                subject=subject,
                replacement=replacement,
            )
        )
    return tuple(requests)


def _locate(
    obligation: Mapping[str, Any],
    index: Mapping[str, dict[str, Any]],
    by_candidate: Mapping[str, Mapping[str, Any]],
    ledger: Ledger,
) -> tuple[str, int | None, str] | None:
    attached = [index[eid] for eid in obligation["evidence_ids"] if eid in index]
    if not attached:
        return None
    for candidate in by_candidate.values():
        if set(obligation["evidence_ids"]) <= set(candidate["evidence_ids"]):
            location = ledger.location_of(candidate)
            return location.path, location.line, location.claim_type
    chosen = attached[0]
    return str(chosen["path"]), chosen.get("line_start"), str(chosen["claim_type"])


def _subject_of(
    obligation: Mapping[str, Any], change_sets: Mapping[str, ChangeSet]
) -> tuple[str | None, str | None]:
    identifier = str(obligation["provider_change_id"])
    if not identifier.startswith(SUBJECT_PREFIX):
        return None, None
    subject = identifier[len(SUBJECT_PREFIX) :]
    for changes in change_sets.values():
        for change in changes.changes:
            if change.subject == subject:
                return subject, change.replacement
    return subject, None


def _from_version(obligation: Mapping[str, Any], target: str) -> str:
    identifier = str(obligation["provider_change_id"])
    if identifier.startswith(VERSION_PREFIX):
        pair = identifier[len(VERSION_PREFIX) :]
        if "->" in pair:
            return pair.split("->", 1)[0]
    return target


def migrate(
    *,
    pack: registry.LoadedPack,
    ledger: Ledger,
    root: Path,
    target: str,
    write: bool = True,
) -> MigrationResult:
    change_sets = change_sets_for(pack, detected_versions(ledger), target)
    obligations = build_obligations(
        ObligationInputs(
            ledger=ledger,
            change_sets=change_sets,
            oracle=InjectedOracle(pack.verification_contract()),
            target=target,
        )
    )
    requests = transform_requests(
        obligations=obligations,
        ledger=ledger,
        change_sets=change_sets,
        root=root,
        target=target,
    )
    report = deterministic.run(transforms=pack.repair_transforms(), requests=requests)
    written: list[str] = []
    if write:
        for path, text in report.texts.items():
            (root / path).write_text(text, encoding="utf-8", newline="")
            written.append(path)
    return MigrationResult(
        obligations=obligations,
        report=report,
        target=target,
        written=tuple(sorted(written)),
    )


__all__ = [
    "MigrationInvalid",
    "MigrationResult",
    "change_sets_for",
    "detected_versions",
    "migrate",
    "transform_requests",
]
