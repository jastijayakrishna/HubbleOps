from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.core.evidence import declared_versions, searchable_text
from hubbleops.core.records import as_text
from hubbleops.core.subjects import in_text
from hubbleops.core.verification import (
    ABSENT,
    CONSERVED,
    OBLIGATION_METHODS,
    PRESENT,
    SUPPORTS,
    ChangeSet,
    CheckReport,
    ObligationView,
    parse_method,
    watched_subjects,
)
from hubbleops.observe import Ledger

RESIDUE_CLAIM_TYPES = ("call_version", "endpoint_reference", "config_reference")
SURFACE_CLAIM_TYPES = ("surface_reference", "request_text")
SCAN_EXPLAINED_STATUSES = frozenset(
    {"NOT_AFFECTED_WITH_EVIDENCE", "EXCLUDED_WITH_EVIDENCE", "PROVIDER_REFERENCE_DATA"}
)
OPEN_STATUS = "OPEN"
DISCHARGED_STATUS = "DISCHARGED"
UNRECONCILABLE_STATUS = "UNRECONCILABLE"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    obligation_id: str
    status: str
    reason: str
    evidence_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class Audit:
    residue: tuple[str, ...]
    reintroduced: tuple[str, ...]
    reconciliations: tuple[Reconciliation, ...]
    unresolved: tuple[str, ...]

    def open_obligations(self) -> tuple[Reconciliation, ...]:
        return tuple(item for item in self.reconciliations if item.status == OPEN_STATUS)

    def report(self) -> CheckReport:
        reasons = (
            *(f"old-version residue at {site}" for site in self.residue),
            *(f"removed subject still present: {subject}" for subject in self.reintroduced),
            *(
                f"obligation {item.obligation_id} is OPEN: {item.reason}"
                for item in self.open_obligations()
            ),
        )
        return CheckReport(
            name="migration_audit",
            passed=not reasons,
            reasons=reasons,
            unresolved=self.unresolved,
            detail={
                "old_version_residue": list(self.residue),
                "removed_subjects_present": list(self.reintroduced),
                "obligations": [item.to_mapping() for item in self.reconciliations],
            },
        )


def run(
    ledger: Ledger,
    changes: ChangeSet,
    obligations: Sequence[ObligationView],
    candidate_root: Path | None = None,
    candidate_paths: Sequence[str] = (),
    captured: Sequence[Mapping[str, Any]] = (),
    supported_targets: Sequence[str] = (),
) -> Audit:
    evidence = ledger.evidence_by_id()
    residue = tuple(
        sorted(
            {
                *_residue(ledger, evidence, changes.from_version),
                *_captured_residue(captured, changes.from_version),
            }
        )
    )
    reintroduced = tuple(
        sorted(
            {
                *_reintroduced(ledger, evidence, changes),
                *_source_reintroduced(candidate_root, candidate_paths, changes),
                *_captured_reintroduced(captured, changes),
            }
        )
    )
    reconciliations = tuple(
        _reconcile(item, ledger, evidence, changes, supported_targets)
        for item in sorted(obligations, key=_key)
    )
    unresolved = tuple(
        sorted(
            f"obligation {item.obligation_id}: {item.reason}"
            for item in reconciliations
            if item.status == UNRECONCILABLE_STATUS
        )
        + sorted(
            f"contract subject {subject}"
            for subject in _consumed_unresolved(ledger, evidence, changes)
        )
    )
    return Audit(
        residue=residue,
        reintroduced=reintroduced,
        reconciliations=reconciliations,
        unresolved=unresolved,
    )


def _captured_residue(
    captured: Sequence[Mapping[str, Any]], source_version: str
) -> tuple[str, ...]:
    return tuple(
        f"captured event {index} ({event.get('service')}.{event.get('method')})"
        for index, event in enumerate(captured, start=1)
        if as_text(event.get("version")) == source_version
    )


def _captured_reintroduced(
    captured: Sequence[Mapping[str, Any]], changes: ChangeSet
) -> tuple[str, ...]:
    watched = watched_subjects(changes)
    if not watched:
        return ()
    found: set[str] = set()
    for index, event in enumerate(captured, start=1):
        text = as_text(event.get("request_text")) or ""
        for subject, _ in in_text(text, watched):
            found.add(f"{subject} at captured event {index}")
    return tuple(sorted(found))


def _source_reintroduced(
    root: Path | None, paths: Sequence[str], changes: ChangeSet
) -> tuple[str, ...]:
    if root is None:
        return ()
    watched = watched_subjects(changes)
    if not watched:
        return ()
    found: set[str] = set()
    for relative in sorted(set(paths)):
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for subject, offset in in_text(text, watched):
            line = text.count("\n", 0, offset) + 1
            found.add(f"{subject} at {relative}:{line} (independent source extinction)")
    return tuple(sorted(found))


def _consumed_unresolved(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], changes: ChangeSet
) -> tuple[str, ...]:
    undecided = tuple(sorted({item.subject for item in changes.unresolved() if item.subject}))
    if not undecided:
        return ()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    named: set[str] = set()
    for eid in sorted(attached):
        record = evidence.get(eid)
        if record is None:
            continue
        text = searchable_text(record)
        if not text:
            continue
        named.update(subject for subject, _ in in_text(text, undecided))
    return tuple(sorted(named))


def _key(obligation: ObligationView) -> str:
    return obligation.id


def _residue(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], source_version: str
) -> tuple[str, ...]:
    found: set[str] = set()
    for candidate in ledger.candidates:
        if candidate["status"] != "AFFECTED":
            continue
        for eid in candidate["evidence_ids"]:
            record = evidence.get(eid)
            if record is None or record["claim_type"] not in RESIDUE_CLAIM_TYPES:
                continue
            if source_version in _version_values(record):
                found.add(f"{record['path']}:{record['line_start'] or 0}")
    return tuple(sorted(found))


def _version_values(record: Mapping[str, Any]) -> frozenset[str]:
    return declared_versions(record)


def _reintroduced(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], changes: ChangeSet
) -> tuple[str, ...]:
    removed = {item.subject for item in changes.removed()}
    if not removed:
        return ()
    found: set[str] = set()
    for candidate in ledger.candidates:
        if candidate["status"] not in ("AFFECTED", "UNKNOWN", "HUMAN_REQUIRED"):
            continue
        for eid in candidate["evidence_ids"]:
            record = evidence.get(eid)
            if record is None or record["claim_type"] not in SURFACE_CLAIM_TYPES:
                continue
            subject = as_text(record.get("provider_subject"))
            if subject in removed:
                found.add(f"{subject} at {record['path']}:{record['line_start'] or 0}")
    return tuple(sorted(found))


def _reconcile(
    obligation: ObligationView,
    ledger: Ledger,
    evidence: Mapping[str, Mapping[str, Any]],
    changes: ChangeSet,
    supported_targets: Sequence[str],
) -> Reconciliation:
    parsed = parse_method(obligation.verification_method)
    if parsed is None:
        return Reconciliation(
            obligation_id=obligation.id,
            status=UNRECONCILABLE_STATUS,
            reason=(
                f"verification_method {obligation.verification_method!r} names no check this "
                f"build executes; expected one of {', '.join(OBLIGATION_METHODS)}"
            ),
            evidence_ids=(),
        )
    subject = parsed.argument
    if parsed.kind == CONSERVED:
        return _conserved(obligation, ledger, subject)
    if parsed.kind == SUPPORTS:
        return _decide(
            obligation,
            subject in supported_targets,
            (),
            f"the resolved client library supports {', '.join(supported_targets) or 'nothing'}, "
            f"not {subject}",
        )
    matches = _subject_sites(ledger, evidence, subject)
    if parsed.kind == ABSENT:
        live = _live_sites(ledger, matches)
        surviving = ", ".join(sorted({site for _, site in live}))
        return _decide(obligation, not live, live, f"{subject} is still present at {surviving}")
    if parsed.kind == PRESENT:
        return _decide(obligation, bool(matches), matches, f"{subject} is absent")
    known = {item.subject for item in changes.changes} | {changes.to_version}
    if subject not in known:
        return Reconciliation(
            obligation_id=obligation.id,
            status=UNRECONCILABLE_STATUS,
            reason=f"{subject} is not a subject or version this Change Pack knows",
            evidence_ids=(),
        )
    return _decide(obligation, bool(matches), matches, f"{subject} is not observed")


def _conserved(obligation: ObligationView, ledger: Ledger, candidate_id: str) -> Reconciliation:
    carried = next(
        (item for item in ledger.candidates if str(item["id"]) == candidate_id),
        None,
    )
    if carried is None:
        return _decide(
            obligation,
            False,
            (),
            f"candidate {candidate_id[:12]} is in no candidate-run ledger entry, so the UNKNOWN "
            "it carried was dropped rather than preserved or closed",
        )
    return _decide(
        obligation,
        True,
        tuple((str(eid), str(carried["status"])) for eid in carried["evidence_ids"]),
        "",
    )


def _decide(
    obligation: ObligationView,
    satisfied: bool,
    matches: tuple[tuple[str, str], ...],
    failure: str,
) -> Reconciliation:
    return Reconciliation(
        obligation_id=obligation.id,
        status=DISCHARGED_STATUS if satisfied else OPEN_STATUS,
        reason=f"{obligation.verification_method} holds" if satisfied else failure,
        evidence_ids=tuple(sorted(eid for eid, _ in matches)),
    )


def _live_sites(ledger: Ledger, sites: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    live = {
        str(eid)
        for candidate in ledger.candidates
        if str(candidate["status"]) not in SCAN_EXPLAINED_STATUSES
        for eid in candidate["evidence_ids"]
    }
    return tuple(site for site in sites if site[0] in live)


def _subject_sites(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], subject: str
) -> tuple[tuple[str, str], ...]:
    sites: set[tuple[str, str]] = set()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    pattern = re.compile(rf"(?<![\w.]){re.escape(subject)}(?![\w.])")
    for eid in sorted(attached):
        record = evidence.get(eid)
        if record is None:
            continue
        if (
            as_text(record.get("provider_subject")) == subject
            or subject in _version_values(record)
            or pattern.search(searchable_text(record))
        ):
            sites.add((eid, f"{record['path']}:{record['line_start'] or 0}"))
    return tuple(sorted(sites))


__all__ = ["Audit", "Reconciliation", "run"]
